"""Carga y validación de catálogos de intenciones.

Un catálogo es un YAML en `data/catalogos/<nombre>.yaml`:

    catalogo: preguntas_producto
    version: 1
    descripcion: ...
    fuente_txt: faq_vestidos.txt        # opcional: otro archivo en el formato del faq, se fusiona con el YAML
    minimo_ejemplos: 20                  # opcional (por defecto 20)
    intenciones:
      se_estira:
        descripcion: ...
        ejemplos: [...]
        slots: {producto: opcional}
        hechos_requeridos: [elasticidad]
        accion: responder_atributo_producto
        stage: ...                       # opcionales (señales de compra)
        strength: 0.9

Todo lo que hay en los ejemplos es SINTÉTICO (no hay datos de clientas reales). Los marcadores `<PRODUCTO>` y
`<ALTURA>` se sustituyen al cargar por «este vestido» y «1,60».
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

try:
    import yaml
except ImportError:  # pragma: no cover - en el host sin PyYAML solo falla al cargar catálogos
    yaml = None

SUSTITUCIONES = {"<PRODUCTO>": "este vestido", "<ALTURA>": "1,60"}
MINIMO_EJEMPLOS = 20


class CatalogoInvalido(ValueError):
    """El catálogo no pasa la validación (duplicados, ejemplos vacíos, pocos ejemplos...)."""


def directorio_datos() -> str:
    """`data/catalogos`: CATALOGOS_DIR, o AGENTE_DATA_DIR/catalogos, o el del repo."""
    if os.environ.get("CATALOGOS_DIR"):
        return os.environ["CATALOGOS_DIR"]
    base = os.environ.get("AGENTE_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")
    return os.path.normpath(os.path.join(base, "catalogos"))


def sustituir(texto: str) -> str:
    for k, v in SUSTITUCIONES.items():
        texto = texto.replace(k, v)
    return texto


def preparar_texto(texto: str) -> str:
    """Lo que entra al embedder: minúsculas, sin ¿¡ y espacios colapsados. Los mensajes de WhatsApp casi nunca
    traen signos de apertura ni mayúsculas, y los ejemplos del faq sí; así los dos hablan igual."""
    t = (texto or "").lower().replace("¿", "").replace("¡", "")
    return re.sub(r"\s+", " ", t).strip()


def normalizar_clave(texto: str) -> str:
    """Clave para detectar duplicados: sin acentos, sin puntuación, minúsculas, letras repetidas colapsadas."""
    t = unicodedata.normalize("NFD", (texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class Intencion:
    nombre: str
    descripcion: str = ""
    ejemplos: list = field(default_factory=list)       # texto ya sustituido
    origen: list = field(default_factory=list)         # «faq» | «ampliado», paralelo a ejemplos
    slots: dict = field(default_factory=dict)
    hechos_requeridos: list = field(default_factory=list)
    accion: str = ""
    stage: Optional[str] = None
    strength: Optional[float] = None
    extra: dict = field(default_factory=dict)          # cualquier otra clave del YAML


@dataclass
class Catalogo:
    nombre: str
    version: int = 1
    descripcion: str = ""
    intenciones: dict = field(default_factory=dict)    # nombre -> Intencion
    minimo_ejemplos: int = MINIMO_EJEMPLOS
    fuentes: list = field(default_factory=list)

    def ejemplos(self):
        """Lista plana (texto, intención, origen)."""
        for it in self.intenciones.values():
            for t, o in zip(it.ejemplos, it.origen):
                yield t, it.nombre, o

    @property
    def n_ejemplos(self) -> int:
        return sum(len(i.ejemplos) for i in self.intenciones.values())

    def resumen(self) -> str:
        return f"{self.nombre}: {len(self.intenciones)} intenciones, {self.n_ejemplos} ejemplos"


def _leer_yaml(ruta: str) -> dict:
    if yaml is None:
        raise RuntimeError("Falta PyYAML (viene en la imagen del agente; en el host: pip install pyyaml en un venv)")
    with open(ruta, encoding="utf-8") as fh:
        datos = yaml.safe_load(fh)
    if not isinstance(datos, dict):
        raise CatalogoInvalido(f"{ruta}: se esperaba un mapa YAML")
    return datos


RE_GRUPO = re.compile(r"^##\s*([A-Z])\s*\|\s*([^|]+?)\s*\|\s*(.+?)\s*$")
RE_EJEMPLO = re.compile(r"^(\d{3})\s+(\S+)\s+(.+?)\s*$")


def cargar_faq_txt(ruta: str) -> dict:
    """`faq_vestidos.txt` → {intención: {descripcion, ejemplos: [(texto, slug)]}} en el orden del archivo.
    «## A | material | Material y composición» abre un grupo; «001 slug ¿pregunta?» es un ejemplo."""
    grupos: dict = {}
    actual = None
    with open(ruta, encoding="utf-8") as fh:
        for n, linea in enumerate(fh, 1):
            linea = linea.rstrip("\n")
            if not linea.strip() or linea.lstrip().startswith("#") and not linea.startswith("##"):
                continue
            m = RE_GRUPO.match(linea)
            if m:
                actual = m.group(2).strip()
                if actual in grupos:
                    raise CatalogoInvalido(f"{ruta}:{n}: grupo repetido «{actual}»")
                grupos[actual] = {"letra": m.group(1), "descripcion": m.group(3), "ejemplos": []}
                continue
            m = RE_EJEMPLO.match(linea)
            if m:
                if actual is None:
                    raise CatalogoInvalido(f"{ruta}:{n}: ejemplo fuera de un grupo")
                grupos[actual]["ejemplos"].append((m.group(3), m.group(2)))
                continue
            raise CatalogoInvalido(f"{ruta}:{n}: línea que no es grupo ni ejemplo: {linea[:60]!r}")
    return grupos


def _nueva_intencion(nombre: str, d: dict) -> Intencion:
    conocidas = {"descripcion", "ejemplos", "slots", "hechos_requeridos", "accion", "stage", "strength"}
    return Intencion(nombre=nombre, descripcion=str(d.get("descripcion") or ""),
                     slots=dict(d.get("slots") or {}), hechos_requeridos=list(d.get("hechos_requeridos") or []),
                     accion=str(d.get("accion") or ""), stage=d.get("stage"), strength=d.get("strength"),
                     extra={k: v for k, v in d.items() if k not in conocidas})


def cargar_catalogo(nombre_o_ruta: str, directorio: Optional[str] = None, validar_: bool = True) -> Catalogo:
    """Carga `<directorio>/<nombre>.yaml`, fusiona `fuente_txt` si la hay y valida."""
    d = directorio or directorio_datos()
    ruta = nombre_o_ruta if nombre_o_ruta.endswith(".yaml") else os.path.join(d, nombre_o_ruta + ".yaml")
    datos = _leer_yaml(ruta)
    if "intenciones" not in datos:
        raise CatalogoInvalido(f"{ruta}: falta «intenciones»")
    cat = Catalogo(nombre=str(datos.get("catalogo") or os.path.basename(ruta)[:-5]), version=int(datos.get("version", 1)),
                   descripcion=str(datos.get("descripcion") or ""),
                   minimo_ejemplos=int(datos.get("minimo_ejemplos", MINIMO_EJEMPLOS)), fuentes=[os.path.basename(ruta)])
    # 1) el .txt (los originales, en su orden) 2) los ejemplos del YAML (ampliaciones o catálogo propio)
    if datos.get("fuente_txt"):
        ruta_txt = os.path.join(os.path.dirname(ruta), datos["fuente_txt"])
        cat.fuentes.append(datos["fuente_txt"])
        for nom, g in cargar_faq_txt(ruta_txt).items():
            it = cat.intenciones.setdefault(nom, Intencion(nombre=nom, descripcion=g["descripcion"]))
            for texto, _slug in g["ejemplos"]:
                it.ejemplos.append(sustituir(texto))
                it.origen.append("faq")
    for nom, d_int in datos["intenciones"].items():
        d_int = d_int or {}
        base = _nueva_intencion(nom, d_int)
        it = cat.intenciones.get(nom)
        if it is None:
            it = cat.intenciones[nom] = base
        else:  # intención que ya vino del .txt: el YAML aporta metadatos y ejemplos ampliados
            it.descripcion = base.descripcion or it.descripcion
            it.slots, it.hechos_requeridos, it.accion = base.slots, base.hechos_requeridos, base.accion
            it.stage, it.strength, it.extra = base.stage, base.strength, base.extra
        for texto in d_int.get("ejemplos") or []:
            it.ejemplos.append(sustituir(str(texto)) if texto is not None else "")
            it.origen.append("ampliado" if datos.get("fuente_txt") else "propio")
    if validar_:
        validar(cat)
    return cat


def cargar_catalogo_prueba(nombre: str, directorio: Optional[str] = None) -> Optional[dict]:
    """`<nombre>_prueba.yaml` → {intención: [frases]} o None si no existe. NUNCA entra al entrenamiento."""
    d = directorio or directorio_datos()
    ruta = os.path.join(d, nombre + "_prueba.yaml")
    if not os.path.exists(ruta):
        return None
    datos = _leer_yaml(ruta)
    out = {}
    for nom, v in (datos.get("intenciones") or {}).items():
        out[nom] = [sustituir(str(t)) for t in ((v or {}).get("ejemplos") if isinstance(v, dict) else v) or []]
    return out


def validar(cat: Catalogo, minimo: Optional[int] = None) -> None:
    """Sin ejemplos vacíos, sin duplicados (ni dentro de una intención ni entre intenciones), con el mínimo de
    ejemplos por intención. Junta todos los problemas y los lanza juntos."""
    minimo = cat.minimo_ejemplos if minimo is None else minimo
    problemas = []
    vistos: dict = {}
    if not cat.intenciones:
        problemas.append("el catálogo no tiene intenciones")
    for it in cat.intenciones.values():
        if len(it.ejemplos) < minimo:
            problemas.append(f"«{it.nombre}» tiene {len(it.ejemplos)} ejemplos (mínimo {minimo})")
        for t in it.ejemplos:
            if not t or not t.strip():
                problemas.append(f"«{it.nombre}» tiene un ejemplo vacío")
                continue
            k = normalizar_clave(t)
            if not k:
                problemas.append(f"«{it.nombre}»: ejemplo sin letras {t!r}")
                continue
            if k in vistos:
                otro = vistos[k]
                problemas.append(f"duplicado {t!r}: en «{it.nombre}» y en «{otro}»" if otro != it.nombre
                                 else f"duplicado dentro de «{it.nombre}»: {t!r}")
            else:
                vistos[k] = it.nombre
        if it.strength is not None and not 0 <= float(it.strength) <= 1:
            problemas.append(f"«{it.nombre}»: strength fuera de 0–1")
        if "<" in "".join(it.ejemplos) and re.search(r"<[A-Z]+>", " ".join(it.ejemplos)):
            problemas.append(f"«{it.nombre}»: queda un marcador <...> sin sustituir")
    if problemas:
        raise CatalogoInvalido(f"Catálogo «{cat.nombre}» inválido ({len(problemas)} problemas):\n  - " + "\n  - ".join(problemas[:40]))


def listar_catalogos(directorio: Optional[str] = None) -> list:
    """Nombres de los catálogos disponibles (los YAML que no son de prueba ni de ruido ni umbrales)."""
    d = directorio or directorio_datos()
    if not os.path.isdir(d):
        return []
    return sorted(f[:-5] for f in os.listdir(d)
                  if f.endswith(".yaml") and not f.endswith("_prueba.yaml") and not f.startswith("ruido"))
