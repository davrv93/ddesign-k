"""Conocimiento de la tienda que manda JMD Ventas en cada petición (15-10-2026, plan «Fuentes y promos», §2.3).

JMD Ventas manda `conocimiento` en cada `POST /chat` y `POST /foto` (como `colores_grupos`): las fuentes VIGENTES de la
empresa —promos con estructura, FAQ, políticas, guion y material—. Contrato: `ddesign-k-fuentes/docs/FUENTES_CONTRATO.md`
§3. Sin `conocimiento` (o vacío, o sin `empresa`) el agente se comporta como siempre: seed/tienda.md, seed/speech.md y
la política «no hay descuentos para nadie».

Reglas de esta capa:
- **Un payload roto no tumba nada.** Lo que no tiene la forma del contrato se ignora (una promo mala se salta; un
  payload que no es un mapa o no trae empresa, entero). Nunca un 500 por esto.
- **El precio de promo lo calcula el código** (`precio_promo`, tabla del contrato), nunca el LLM.
- **Dos empresas no se mezclan:** lo derivado (índice de FAQ) se guarda por `(empresa, version)` en un LRU pequeño; la
  `version` es el hash del contenido que manda el bot, así una promo activada se nota en el primer mensaje.
- **La vigencia se mira en cada llamada** con el `hoy` que manda el bot (fecha de Lima), no se hornea en la caché: el
  mismo contenido sirve dos días distintos.

    python3 -m app.prueba_conocimiento
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import re
import threading
from collections import OrderedDict
from decimal import ROUND_HALF_UP, Decimal
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import memoria

log = logging.getLogger("agente.conocimiento")

# Coseno mínimo (e5-small, «query: » en los dos lados, normalizado) para contestar con una FAQ de la tienda. Con e5 dos
# frases cualesquiera del rubro ya rondan 0,80-0,85 (los catálogos semánticos se calibraron en 0,88-0,91 con el mismo
# embedder, data/catalogos/umbrales.json): por debajo de 0,88 se abstiene. Abstenerse es barato; contestar otra cosa, no.
UMBRAL_FAQ = float(os.environ.get("FAQ_UMBRAL", "0.88"))
CACHE_MAX = max(1, int(os.environ.get("CONOCIMIENTO_CACHE", "16")))
MECANICAS = ("porcentaje", "monto", "precio_especial", "dos_por", "envio_gratis", "regalo", "otra")
# Categorías del contrato (alcance.tipo = categoria) y cómo se reconocen en el nombre o la categoría de la prenda.
CATEGORIAS = {"vestido": "vestid", "blusa": "blus", "enterizo": "enteriz", "pantalon": "pantal", "conjunto": "conjunt",
              "blazer": "blazer", "falda": "fald"}
MAX_TEXTO = 4000          # una política o un guion más largo se recorta: no se le mete un libro al prompt

# Embedder por defecto (main.py lo fija al arrancar con E.emb). Las pruebas inyectan uno falso.
EMBED: Optional[Callable] = None


# ---------------------------------------------------------------------------
# Lectura defensiva del payload

@dataclass
class Base:
    """Lo derivado de un payload, cacheado por (empresa, version). No lleva `hoy`: la vigencia se mira en cada llamada."""
    empresa: str
    version: str
    promos: list = field(default_factory=list)
    faqs: list = field(default_factory=list)
    politicas: list = field(default_factory=list)
    guion: Optional[dict] = None
    material: list = field(default_factory=list)
    _indice: object = None        # (embed, matriz normalizada, [(faq, texto)])
    _lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class Conocimiento:
    """El conocimiento de UNA petición: la base cacheada + el `hoy` que mandó el bot."""
    base: Base
    hoy: _dt.date

    @property
    def empresa(self) -> str:
        return self.base.empresa

    @property
    def version(self) -> str:
        return self.base.version


_CACHE: "OrderedDict[tuple[str, str], Base]" = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _txt(v, n: int = MAX_TEXTO) -> str:
    return v.strip()[:n] if isinstance(v, str) else ""


def _id(v):
    """Ids del contrato: enteros. Se acepta «12» (texto) por si acaso; lo demás no es un id."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _fecha(v) -> Optional[_dt.date]:
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        return _dt.date.fromisoformat(v.strip()[:10])
    except ValueError:
        return None


def _num(v) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip().replace(",", "."))
        except ValueError:
            return None
    return None


def _promo(d) -> Optional[dict]:
    """Una promo del contrato, limpia; None si no sirve (sin id, mecánica desconocida o valor imposible)."""
    if not isinstance(d, dict):
        return None
    pid = _id(d.get("id"))
    mec = d.get("mecanica")
    if pid is None or mec not in MECANICAS:
        return None
    valor = _num(d.get("valor"))
    if mec == "porcentaje" and not (valor is not None and 0 < valor < 100):
        return None
    if mec in ("monto", "precio_especial", "dos_por") and not (valor is not None and valor > 0):
        return None
    if d.get("vigente") is False:          # el bot solo manda vigentes; si alguna dice que no, no se usa
        return None
    a = d.get("alcance") if isinstance(d.get("alcance"), dict) else {}
    tipo = a.get("tipo") if a.get("tipo") in ("todo", "categoria", "coleccion", "prendas") else "todo"
    alcance = {"tipo": tipo, "categoria": _norm_cat(_txt(a.get("categoria"), 40)), "coleccion": _txt(a.get("coleccion"), 60).lower(),
               "codigos": [c.strip().upper() for c in (a.get("codigos") or []) if isinstance(c, str) and c.strip()][:200]}
    if tipo == "categoria" and alcance["categoria"] not in CATEGORIAS:
        return None
    if tipo == "coleccion" and not alcance["coleccion"]:
        return None
    if tipo == "prendas" and not alcance["codigos"]:
        return None
    return {"id": pid, "titulo": _txt(d.get("titulo"), 120), "contenido": _txt(d.get("contenido")), "mecanica": mec,
            "valor": round(valor, 2) if valor is not None else None, "alcance": alcance,
            "condiciones": _txt(d.get("condiciones"), 500), "mensaje_apertura": _txt(d.get("mensaje_apertura"), 500),
            "respuesta_promo": _txt(d.get("respuesta_promo"), 800), "respuesta_fuera": _txt(d.get("respuesta_fuera"), 500),
            "desde": _fecha(d.get("vigente_desde")), "hasta": _fecha(d.get("vigente_hasta")),
            "publico": _txt(d.get("publico"), 40) or "todas"}


def _faq(d) -> Optional[dict]:
    if not isinstance(d, dict):
        return None
    fid, preg, resp = _id(d.get("id")), _txt(d.get("pregunta"), 300), _txt(d.get("respuesta"), 1200)
    if fid is None or not preg or not resp:
        return None
    variantes = [_txt(v, 300) for v in (d.get("variantes") or []) if isinstance(v, str) and v.strip()][:20]
    return {"id": fid, "pregunta": preg, "variantes": variantes, "respuesta": resp}


def _doc(d) -> Optional[dict]:
    if not isinstance(d, dict):
        return None
    did, cont = _id(d.get("id")), _txt(d.get("contenido"))
    if did is None or not cont:
        return None
    return {"id": did, "titulo": _txt(d.get("titulo"), 120), "contenido": cont}


def _lista(v, fn) -> list:
    return [x for x in (fn(y) for y in (v if isinstance(v, list) else [])[:200]) if x is not None]


def _version(payload: dict) -> str:
    v = payload.get("version")
    if isinstance(v, (str, int)) and str(v).strip():
        return str(v).strip()[:64]
    # Sin versión: el hash del contenido (sin `hoy`, que cambia cada día y no cambia nada de lo derivado).
    resto = {k: payload.get(k) for k in ("promos", "faqs", "politicas", "guion", "material")}
    return "h" + hashlib.sha1(json.dumps(resto, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def _construir(empresa: str, version: str, payload: dict) -> Base:
    guion = _doc(payload.get("guion")) if isinstance(payload.get("guion"), dict) else None
    return Base(empresa=empresa, version=version, promos=_lista(payload.get("promos"), _promo),
                faqs=_lista(payload.get("faqs"), _faq), politicas=_lista(payload.get("politicas"), _doc),
                guion=guion, material=_lista(payload.get("material"), _doc))


def leer(payload, hoy: Optional[_dt.date] = None) -> Optional[Conocimiento]:
    """El conocimiento de la petición, o None si no hay (o no sirve): entonces todo sigue como siempre (seed)."""
    try:
        if not isinstance(payload, dict):
            return None
        empresa = _txt(payload.get("empresa"), 80).lower()
        if not empresa:                      # sin empresa no se aplica ninguna fuente (plan §6: no mezclar tiendas)
            return None
        version = _version(payload)
        clave = (empresa, version)
        with _CACHE_LOCK:
            base = _CACHE.get(clave)
            if base is not None:
                _CACHE.move_to_end(clave)
        if base is None:
            base = _construir(empresa, version, payload)
            with _CACHE_LOCK:
                _CACHE[clave] = base
                _CACHE.move_to_end(clave)
                while len(_CACHE) > CACHE_MAX:
                    _CACHE.popitem(last=False)
        dia = hoy or _fecha(payload.get("hoy")) or memoria.ahora_lima().date()
        c = Conocimiento(base=base, hoy=dia)
        return c if (base.promos or base.faqs or base.politicas or base.guion or base.material) else None
    except Exception as e:                   # nunca un 500 por un payload raro
        log.warning("conocimiento ignorado (%s): %s", type(e).__name__, e)
        return None


def limpiar_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def en_cache() -> list[tuple[str, str]]:
    with _CACHE_LOCK:
        return list(_CACHE)


# ---------------------------------------------------------------------------
# Promos

def _norm_cat(c: str) -> str:
    """«Vestidos», «pantalón», «Pantalones» → vestido, pantalon."""
    t = memoria._plano(c or "").strip()
    for cat, raiz in CATEGORIAS.items():
        if t.startswith(raiz):
            return cat
    return t


def categoria(ficha) -> str:
    """Categoría del contrato de una prenda: la primera palabra de su nombre («Conjunto Xela» → conjunto) o su categoría."""
    for texto in (getattr(ficha, "nombre", "") or "", getattr(ficha, "categoria", "") or ""):
        for palabra in memoria._plano(texto).split():
            for cat, raiz in CATEGORIAS.items():
                if palabra.startswith(raiz):
                    return cat
    return ""


def _handle(codigo: str) -> str:
    from . import ficha_producto
    w = ficha_producto.web(codigo) or {}
    h = w.get("handle") or ""
    if not h and isinstance(w.get("url"), str):
        h = w["url"].rstrip("/").rsplit("/", 1)[-1]
    return (h or "").lower()


def _en_coleccion(codigo: str, coleccion: str) -> bool:
    from . import colecciones
    productos = (colecciones._datos().get(coleccion) or {}).get("productos") or []
    h = _handle(codigo)
    return bool(h) and h in {str(p).lower() for p in productos}


def vigente(promo: dict, hoy: _dt.date) -> bool:
    return (promo["desde"] is None or promo["desde"] <= hoy) and (promo["hasta"] is None or hoy <= promo["hasta"])


def vigentes(conoc: Optional[Conocimiento]) -> list[dict]:
    if conoc is None:
        return []
    return [p for p in conoc.base.promos if vigente(p, conoc.hoy)]


def cubre(promo: dict, ficha) -> bool:
    a, cod = promo["alcance"], (getattr(ficha, "codigo", "") or "").upper()
    if a["tipo"] == "todo":
        return True
    if a["tipo"] == "categoria":
        return categoria(ficha) == a["categoria"]
    if a["tipo"] == "coleccion":
        return _en_coleccion(cod, a["coleccion"])
    return cod in a["codigos"]


def promos_para(conoc: Optional[Conocimiento], ficha) -> list[dict]:
    """Las promos VIGENTES que cubren esa prenda (todo | categoría | colección de baruka.pe | prendas por código)."""
    if conoc is None or ficha is None:
        return []
    return [p for p in vigentes(conoc) if cubre(p, ficha)]


def _r(x: float) -> float:
    """Dinero a 2 decimales, redondeando la mitad hacia arriba (como se cobra): 289,90 × 0,85 = 246,415 → 246,42. Con
    round() de float salía 246,41 (246,415 no existe en binario)."""
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def precio_promo(promo: dict, precio) -> dict:
    """Precio con la promo, EXACTAMENTE la tabla del contrato. `precio` = precio normal de una unidad (None si no hay).

    porcentaje → P×(1−v/100) · monto → max(P−v, 0) · precio_especial → v · dos_por → P la unidad, v por dos ·
    envio_gratis → P y envío 0 · regalo / otra → P (el beneficio está en las condiciones)."""
    mec, v = promo.get("mecanica"), promo.get("valor")
    p = _r(precio) if isinstance(precio, (int, float)) and not isinstance(precio, bool) else None
    out = {"mecanica": mec, "precio_normal": p, "precio_final": p, "precio_por_dos": None, "envio": None, "ahorro": 0.0,
           "porcentaje": None}
    if mec == "porcentaje" and p is not None:
        out["precio_final"] = _r(Decimal(str(p)) * (Decimal(1) - Decimal(str(v)) / Decimal(100)))
        out["porcentaje"] = v
    elif mec == "monto" and p is not None:
        out["precio_final"] = _r(max(p - v, 0))
    elif mec == "precio_especial":
        out["precio_final"] = _r(v)
    elif mec == "dos_por":
        out["precio_por_dos"] = _r(v)
    elif mec == "envio_gratis":
        out["envio"] = 0.0
    if p is not None and out["precio_final"] is not None:
        out["ahorro"] = _r(max(p - out["precio_final"], 0))
    return out


def _soles(x) -> str:
    return f"S/ {x:.2f}"


def _fecha_txt(d: Optional[_dt.date]) -> str:
    return d.strftime("%d-%m-%Y") if d else ""


def mecanica_txt(promo: dict) -> str:
    """«20% de descuento», «S/ 50 de descuento», «a S/ 250», «2 por S/ 400», «envío gratis», o el título."""
    mec, v = promo["mecanica"], promo["valor"]
    if mec == "porcentaje":
        return f"{v:g}% de descuento"
    if mec == "monto":
        return f"{_soles(v)} de descuento"
    if mec == "precio_especial":
        return f"precio especial de {_soles(v)}"
    if mec == "dos_por":
        return f"2 por {_soles(v)}"
    if mec == "envio_gratis":
        return "envío gratis"
    if mec == "regalo":
        return "regalo" + (f" ({promo['condiciones']})" if promo["condiciones"] else "")
    return promo["titulo"] or "promoción"


def alcance_txt(promo: dict) -> str:
    a = promo["alcance"]
    if a["tipo"] == "todo":
        return "en toda la tienda"
    if a["tipo"] == "categoria":
        return f"en {a['categoria']}s" if not a["categoria"].endswith("n") else f"en {a['categoria']}es"
    if a["tipo"] == "coleccion":
        return f"en la colección {a['coleccion']}"
    return "en prendas seleccionadas"


def linea_promo(promo: dict, ficha=None) -> str:
    """Una línea para el prompt con las cifras YA calculadas: «PROMO «X» (id 12): Vestido Salome, precio normal S/ 330.00,
    con la promo S/ 264.00 (20% de descuento). Condiciones: … Vigente hasta el 31-10-2026.»"""
    partes = [f"PROMO «{promo['titulo'] or mecanica_txt(promo)}»: {mecanica_txt(promo)} {alcance_txt(promo)}"]
    if ficha is not None:
        pp = precio_promo(promo, getattr(ficha, "precio", None))
        nombre = getattr(ficha, "nombre", "") or getattr(ficha, "codigo", "")
        if pp["precio_por_dos"] is not None:
            partes.append(f"{nombre}: {_soles(pp['precio_normal'])} la unidad" if pp["precio_normal"] is not None else nombre)
            partes[-1] += f", y 2 por {_soles(pp['precio_por_dos'])}"
        elif pp["envio"] == 0.0:
            partes.append(f"{nombre}: {_soles(pp['precio_normal'])} y el envío sale gratis" if pp["precio_normal"] is not None
                          else f"{nombre}: el envío sale gratis")
        elif pp["precio_normal"] is not None and pp["precio_final"] != pp["precio_normal"]:
            partes.append(f"{nombre}: precio normal {_soles(pp['precio_normal'])}, con la promo {_soles(pp['precio_final'])}")
        elif pp["precio_final"] is not None:
            partes.append(f"{nombre}: {_soles(pp['precio_final'])}")
    if promo["condiciones"]:
        partes.append(f"Condiciones: {promo['condiciones']}")
    if promo["hasta"]:
        partes.append(f"Vigente hasta el {_fecha_txt(promo['hasta'])}")
    return ". ".join(partes) + "."


def resumen_promo(promo: dict) -> str:
    """Lo que se le dice a «¿tienen promo?»: la respuesta que cargó la tienda o un resumen corto armado por código."""
    if promo["respuesta_promo"]:
        return promo["respuesta_promo"]
    txt = f"{(promo['titulo'] + ': ') if promo['titulo'] else ''}{mecanica_txt(promo)} {alcance_txt(promo)}"
    if promo["condiciones"]:
        txt += f" ({promo['condiciones']})"
    if promo["hasta"]:
        txt += f", hasta el {_fecha_txt(promo['hasta'])}"
    return txt[:1].upper() + txt[1:] + "."


def respaldo(promo: dict, fichas=()) -> dict:
    """Lo que el validador puede dar por bueno de esta promo: precios (con y sin envío) y porcentaje."""
    from .v3.herramientas import ENVIOS
    precios, porcentajes = set(), set()
    if promo["mecanica"] == "porcentaje":
        porcentajes.add(float(promo["valor"]))
    if promo["mecanica"] in ("monto", "precio_especial", "dos_por"):
        precios.add(_r(promo["valor"]))
    for f in fichas:
        pp = precio_promo(promo, getattr(f, "precio", None))
        for x in (pp["precio_final"], pp["precio_normal"], pp["precio_por_dos"]):
            if x is not None:
                precios.add(_r(x))
                precios.update(_r(x + e) for e in ENVIOS)
        if pp["ahorro"]:
            precios.add(_r(pp["ahorro"]))
    return {"precios": precios, "porcentajes": porcentajes}


# ---------------------------------------------------------------------------
# FAQ (catálogo semántico en caliente)

def _preparar(t: str) -> str:
    """La misma preparación que los catálogos semánticos (app/catalogos/catalogos.py: preparar_texto): minúsculas, sin
    ¿¡ y espacios colapsados. El embedder del agente pone «query: » y normaliza (modelo.Embedder.__call__)."""
    t = (t or "").lower().replace("¿", "").replace("¡", "")
    return re.sub(r"\s+", " ", t).strip()


def _normalizar(m):
    """Filas de norma 1. Con numpy si está (la imagen); si no, listas de Python (las pruebas del host no traen numpy)."""
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        m = np.asarray(m, dtype="float32")
        return m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)
    out = []
    for fila in m:
        fila = [float(x) for x in fila]
        n = sum(x * x for x in fila) ** 0.5 + 1e-9
        out.append([x / n for x in fila])
    return out


def _similitudes(M, v) -> list[float]:
    if hasattr(M, "shape"):
        return [float(x) for x in (M @ v)]
    return [sum(a * b for a, b in zip(fila, v)) for fila in M]


def _indice(base: Base, embed: Callable):
    with base._lock:
        if base._indice is not None and base._indice[0] is embed:
            return base._indice
        pares = [(q, t) for q in base.faqs for t in [q["pregunta"]] + q["variantes"] if _preparar(t)]
        M = _normalizar(embed([_preparar(t) for _, t in pares])) if pares else None
        base._indice = (embed, M, pares)
        return base._indice


def buscar_faq(conoc: Optional[Conocimiento], texto: str, embed: Optional[Callable] = None,
               umbral: Optional[float] = None) -> Optional[dict]:
    """La FAQ de la tienda que contesta el mensaje: {"faq", "score", "texto"} o None (sin FAQ, sin embedder, mensaje
    demasiado corto o parecido bajo el umbral: se abstiene)."""
    embed = embed or EMBED
    if conoc is None or not conoc.base.faqs or embed is None:
        return None
    q = _preparar(texto)
    if len(memoria._plano(q).split()) < 2:   # «sí», «ok», «gracias»: con e5 una palabra suelta se parece a todo
        return None
    try:
        _, M, pares = _indice(conoc.base, embed)
        if M is None or not pares:
            return None
        v = _normalizar(embed([q]))[0]
        sims = _similitudes(M, v)
        i = max(range(len(sims)), key=sims.__getitem__)
        score = sims[i]
    except Exception as e:                   # sin embedder o con uno roto, no hay FAQ: el turno sigue
        log.warning("FAQ de la tienda sin índice (%s): %s", type(e).__name__, e)
        return None
    if score < (UMBRAL_FAQ if umbral is None else umbral):
        return None
    # Con e5 dos preguntas cortas se parecen aunque no hablen de lo mismo: «¿cuánto cuesta?» dio 0,880 contra «¿emiten
    # factura?» (15-10-2026, prueba local). Bajo FAQ_SEGURA hace falta al menos una palabra con contenido en común.
    if score < FAQ_SEGURA and not (_raices(q) & _raices(pares[i][1])):
        return None
    return {"faq": pares[i][0], "score": round(score, 4), "texto": pares[i][1]}


FAQ_SEGURA = float(os.environ.get("FAQ_SEGURA", "0.94"))
_VACIAS = set("hola tienen tiene tienes puedo puede pueden quiero quisiera como cuanto cuanta cuantos donde cuando para "
              "esta este estos estas tambien hacen hace bueno buenas buenos dias tardes noches gracias favor porfa "
              "saber seria sobre algun alguna ustedes usted mucho".split())


def _raices(t: str) -> set:
    """Raíces (5 letras) de las palabras con contenido: «factura» y «facturan» comparten «factu»."""
    return {w[:5] for w in re.findall(r"[a-zñ]+", memoria._plano(t or "")) if len(w) >= 4 and w not in _VACIAS}


# ---------------------------------------------------------------------------
# Textos que sustituyen a los seed

def texto_tienda(conoc: Optional[Conocimiento]) -> str:
    """Las políticas de la tienda, juntas («## título» + contenido); "" si no mandó ninguna."""
    if conoc is None or not conoc.base.politicas:
        return ""
    return "\n\n".join((f"## {p['titulo']}\n" if p["titulo"] else "") + p["contenido"] for p in conoc.base.politicas)


# La línea de la política de siempre en seed/tienda.md: con promos vigentes es falsa y se quita.
RE_SIN_PROMOS = re.compile(r"^[^\n]*Promociones y descuentos:[^\n]*(?:\n|$)", re.M | re.I)


def tienda_con(base: str, conoc: Optional[Conocimiento]) -> str:
    """El texto de la tienda de este turno. Las políticas que cargó la tienda se SUMAN a seed/tienda.md (no lo
    reemplazan: una política de cambios sola dejaba al bot sin showroom, envíos ni horario, 15-10-2026) y mandan si se
    contradicen. Con promos vigentes se quita la línea «Promociones y descuentos: por ahora no hay»."""
    t = base or ""
    if vigentes(conoc):
        t = RE_SIN_PROMOS.sub("", t).rstrip()
    pol = texto_tienda(conoc)
    if pol:
        t = (t + "\n\n" if t else "") + "POLÍTICAS DE LA TIENDA (las cargó la tienda; si algo de arriba las contradice, mandan estas):\n" + pol
    return t


def texto_guion(conoc: Optional[Conocimiento]) -> str:
    """El guion de venta de la tienda (encabezados «## Paso N»); "" si no cargó uno."""
    if conoc is None or not conoc.base.guion:
        return ""
    return conoc.base.guion["contenido"]


def ids_prompt(conoc: Optional[Conocimiento]) -> list[int]:
    """Ids de las fuentes que entraron al prompt (políticas y guion): para la traza, no son `fuentes_usadas`."""
    if conoc is None:
        return []
    return [p["id"] for p in conoc.base.politicas] + ([conoc.base.guion["id"]] if conoc.base.guion else [])


# Pregunta por promos o pide descuento («¿tienen promo?», «¿hay ofertas?», «¿me haces descuento?», «¿algún 2x1?»).
RE_PREGUNTA_PROMO = re.compile(r"\b(promo\w*|ofertas?|descuent\w*|rebaj\w*|liquidaci\w*|precio especial|2\s*x\s*1|dos por uno|"
                               r"cyber\w*|black friday|sale)\b")
# Mención de descuento en un texto del bot (lo que el validador vigila).
RE_MENCIONA_DESCUENTO = re.compile(r"\b(descuent\w*|rebaj\w*|promo\w*|oferta\w*|precio especial|gratis)\b|\d\s*%", re.I)


def pregunta_promo(texto: str) -> bool:
    return bool(RE_PREGUNTA_PROMO.search(memoria._plano(texto or "")))
