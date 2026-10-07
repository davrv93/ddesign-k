"""Ficha técnica de cada prenda: silueta, largo, escote, mangas, espalda, cierre, tela, forro… (`seed/fichas_producto.json`).

Por qué (07-10-2026): el catálogo solo trae el párrafo de Diners, escrito para vender y no para responder. «¿Tiene mangas?»,
«¿es largo?» o «¿cómo es la espalda?» dependían de que el párrafo lo mencionara; cuando no, el LLM adivinaba y Jev le quitaba
la frase, o se derivaba a una asesora algo que se ve en la foto.

De dónde sale cada dato (campo `fuente`): `diners` = el texto de la tienda en baruka.dinersclubmall.pe (o la lámina de
materiales en V42); `foto` = análisis visual de TODAS las fotos de la prenda (solo lo visible: largo, mangas, escote,
espalda…; la tela NUNCA sale de una foto); `ambas` = el texto lo dice y la foto lo confirma. Lo que no se sabe va a
`pendiente_tienda` (medidas por talla siempre) y el bot dice que lo confirma una asesora. Nada se rellena por adivinar.

Uso:
- `texto(codigo)`  → lo que se suma a la ficha que lee el LLM (y contra lo que Jev verifica).
- `pedidos(plano)` → qué atributos pregunta el mensaje («¿tiene mangas?» → ["mangas"]).
- `respuesta(codigo, ref, attrs)` / `nota(codigo, attrs)` → la frase del código y la nota para el LLM.

`FICHAS_PRODUCTO=0` lo apaga (la ficha vuelve a ser solo el párrafo del catálogo).
"""
from __future__ import annotations

import json
import os
import re

RUTA = os.path.join(os.environ.get("SEED_DIR", os.path.join(os.path.dirname(__file__), "..", "seed")), "fichas_producto.json")
ACTIVO = os.environ.get("FICHAS_PRODUCTO", "1") != "0"

# Orden en que se cuentan; la etiqueta es como se nombra el atributo a la clienta.
ATRIBUTOS = {
    "silueta": "silueta", "largo": "largo", "escote": "escote", "mangas": "mangas", "cintura": "cintura",
    "espalda": "espalda", "cierre": "cierre", "tela": "tela", "forro": "forro", "transparencias": "transparencias",
    "estampado": "estampado",
}

# Qué pregunta el mensaje (sobre el texto plano: minúsculas, sin tildes). La tela tiene su propia regla (venta.RE_MATERIAL).
_PIDE = {
    "mangas": r"\bmangas?\b|\btirant\w*|\bbrazos?\b",
    "largo": r"\blargo\b|\bcorto\b|\bmidi\b|\bmini\b|\bhasta (la|el|los) (rodilla|tobillo|piso|suelo|pie)\w*",
    "escote": r"\bescot\w*|\bcuello\b|\bhalter\b|\bstrapless\b|\bhombros?\b",
    "espalda": r"\bespalda\b",
    "cierre": r"\bcierre\b|\bcremallera\b|\bbotones\b|\bbroches?\b|\bcomo se (pone|abre|cierra)\b",
    "forro": r"\bforr(o|ad[oa]|os)\b",
    "transparencias": r"\btranspar\w*|\btrasluc\w*",
    "cintura": r"\bcintura\b|\bpretina\b|\bcinturon\b|\btalle\b",
    "silueta": r"\bcorte\b|\bsilueta\b|\bajustad\w*|\bpegad\w*|\bholgad\w*|\bsuelt[oa]\b|\bceñid\w*|\bcenid\w*|\bapretad\w*|\bentallad\w*",
    "estampado": r"\bestampad\w*|\bliso\b|\bflores\b|\brayas\b",
    "piezas": r"\bpiezas?\b|\b(viene con|trae|incluye) (la |el )?(falda|pantalon|top|saco|blusa|short)\b|\bes (un )?(conjunto|set)\b",
    "medidas": r"(?<!mis )\bmedidas?\b|\bcuanto mide\b|\bcentimetros?\b|\b\d{2,3} ?cm\b|\bcontorno\b",
    "como_queda": r"\bdisimul\w*|\bfavorec\w*|\bestiliz\w*|\bme hace ver\b|\bme (hara|haria) ver\b|\bpanza\b|\bbarriga\b|\bcaderas?\b",
    "cuidados": r"\blav(a|ar|ado|arlo|arla|o|as)\b|\bplanch\w*|\bcuidados?\b|\bse encoge\b|\bdestiñ\w*|\bdestin\w*",
}
RE_PIDE = {k: re.compile(v) for k, v in _PIDE.items()}

_FICHAS: dict[str, dict] | None = None


def cargar() -> dict[str, dict]:
    global _FICHAS
    if _FICHAS is None:
        try:
            with open(RUTA, encoding="utf-8") as fh:
                _FICHAS = {str(f["codigo"]).upper(): f for f in json.load(fh)}
        except (OSError, ValueError):
            _FICHAS = {}
    return _FICHAS


def ficha(codigo: str) -> dict:
    return cargar().get((codigo or "").upper(), {}) if ACTIVO else {}


def valor(codigo: str, atributo: str) -> str:
    """El valor de un atributo ('' si no se sabe o no aplica)."""
    a = (ficha(codigo).get("atributos") or {}).get(atributo)
    return (a or {}).get("valor") or "" if isinstance(a, dict) else ""


def _lista(codigo: str, campo: str) -> list[str]:
    return [d["valor"] if isinstance(d, dict) else str(d) for d in ficha(codigo).get(campo) or [] if d]


def texto(codigo: str) -> str:
    """Se suma a la ficha que lee el LLM: los atributos, los detalles, cómo queda, cuidados y lo que NO figura."""
    f = ficha(codigo)
    if not f:
        return ""
    partes = []
    if len(f.get("piezas") or []) > 1:
        partes.append("piezas: " + " + ".join(f["piezas"]))
    partes += [f"{ATRIBUTOS[k]}: {valor(codigo, k)}" for k in ATRIBUTOS if valor(codigo, k)]
    if _lista(codigo, "detalles"):
        partes.append("detalles: " + "; ".join(_lista(codigo, "detalles")))
    if _lista(codigo, "como_queda"):
        partes.append("cómo queda (según la tienda): " + "; ".join(_lista(codigo, "como_queda")))
    if f.get("cuidados"):
        partes.append("cuidados: " + f["cuidados"])
    if f.get("pendiente_tienda"):
        partes.append("NO FIGURA (no lo afirmes; lo confirma una asesora): " + "; ".join(f["pendiente_tienda"]))
    # El párrafo de Diners a veces contradice la foto («falda lisa» en un Kabanova floreado): manda la ficha.
    return " | FICHA TÉCNICA (si choca con la descripción, vale esta): " + " | ".join(partes)


def pedidos(plano: str) -> list[str]:
    """Los atributos que pregunta el mensaje, en el orden de `_PIDE`."""
    if not ACTIVO:
        return []
    if RE_BUSCA_OTRA.search(plano or ""):
        return []
    out = [k for k, rx in RE_PIDE.items() if rx.search(plano or "")]
    if re.search(r"\d", plano or ""):     # «tengo 70 de cintura / caderas de 100»: son sus medidas, no un atributo de la prenda
        out = [k for k in out if k not in ("cintura", "como_queda")]
    return out


def _dato(codigo: str, k: str) -> str:
    if k == "piezas":
        p = ficha(codigo).get("piezas") or []
        return " + ".join(p) if len(p) > 1 else (f"es una sola pieza ({p[0]})" if p else "")
    if k == "como_queda":
        return "; ".join(_lista(codigo, "como_queda"))
    if k == "cuidados":
        return ficha(codigo).get("cuidados") or ""
    if k == "medidas":
        return ""
    return valor(codigo, k)


_ETIQUETA = dict(ATRIBUTOS, piezas="piezas", medidas="medidas", como_queda="cómo queda", cuidados="cuidados")
_CON_ARTICULO = {"silueta": "la silueta", "largo": "el largo", "escote": "el escote", "mangas": "las mangas", "cintura": "la cintura",
                 "espalda": "la espalda", "cierre": "el cierre", "tela": "la tela", "forro": "el forro",
                 "transparencias": "las transparencias", "estampado": "el estampado", "piezas": "lo que trae",
                 "medidas": "las medidas exactas por talla", "como_queda": "cómo queda", "cuidados": "los cuidados"}
# Busca OTRA prenda («¿tienes algo más suelto?», «¿qué me recomiendas?»): no se contesta el atributo de la que mira.
RE_BUSCA_OTRA = re.compile(r"\bti[e]?n(e|es|en|s) (algo|otr\w*|algun\w* (vestido|bestido|modelo|conjunto|blusa|pantalon|falda|otro))\b|\brecomi[e]?nd\w*|\balgo (asi|mas)\b|\botr[oa]s? (opcion|modelo|vestido)")
# Pregunta, no búsqueda: «¿es largo?» pregunta el largo; «quiero un vestido largo» busca otra prenda.
RE_PREGUNTA = re.compile(r"\?|^\W*(tiene|es|son|como|cual(es)?|que|cuanto|lleva|viene|trae|se|me|hay|sabes|y)\b|\b(tiene|lleva|viene con)\b")


def respuesta(codigo: str, ref: str, attrs: list[str]) -> str:
    """La frase del código: los datos que hay y, para los que faltan, que los confirma una asesora."""
    if not ficha(codigo):
        return ""
    hay = [(k, _dato(codigo, k)) for k in attrs if _dato(codigo, k)]
    faltan = [k for k in attrs if not _dato(codigo, k)]
    out = []
    if len(hay) == 1:
        k, v = hay[0]
        art = _CON_ARTICULO[k]
        if k == "piezas":
            p = ficha(codigo).get("piezas") or []
            out.append(f"{ref}: trae {' y '.join(p)} 😊" if len(p) > 1 else f"{ref}: es una sola pieza ({p[0]}) 😊")
        else:
            out.append(f"{art[0].upper() + art[1:]} del {ref}: {v} 😊")
    elif hay:
        out.append(f"Te cuento del {ref} 😊\n" + "\n".join(f"• {_ETIQUETA[k].capitalize()}: {v}" for k, v in hay))
    if faltan:
        que = " y ".join(_CON_ARTICULO[k] for k in faltan)
        out.append(f"{'Lo que no tengo a la mano es ' + que if hay else f'Sobre {que} del {ref} no tengo el dato a la mano'}; "
                   "si quieres, una asesora te lo confirma escribiendo *4*.")
    return "\n\n".join(out)


def nota(codigo: str, attrs: list[str]) -> str:
    """Lo que el LLM recibe cuando preguntan atributos concretos."""
    if not ficha(codigo) or not attrs:
        return ""
    hay = [f"{_ETIQUETA[k]}: {_dato(codigo, k)}" for k in attrs if _dato(codigo, k)]
    faltan = [_ETIQUETA[k] for k in attrs if not _dato(codigo, k)]
    txt = f"PREGUNTA POR {', '.join(_ETIQUETA[k].upper() for k in attrs)} DEL {codigo}."
    if hay:
        txt += " Dato (dilo con naturalidad, como vendedora: sin mencionar «la ficha» ni agregar nada que no esté aquí): " + " | ".join(hay) + "."
    if faltan:
        txt += f" Esto NO figura en su ficha: {', '.join(faltan)}. No lo adivines: di que una asesora lo confirma (*4*)."
    return txt


def dicho(codigo: str, attrs: list[str], respuesta_llm: str) -> bool:
    """¿El texto del LLM ya contestó? Cada atributo que la ficha sabe debe aparecer por alguna de sus palabras clave."""
    plano = _plano(respuesta_llm)
    for k in attrs:
        v = _dato(codigo, k)
        if not v:
            if not re.search(r"asesora|\*4\*", respuesta_llm or ""):
                return False
            continue
        claves = [w for w in re.findall(r"[a-zñ]{4,}", _plano(v)) if w not in _VACIAS]
        if claves and not any(w in plano for w in claves):
            return False
    return True


_VACIAS = {"con", "para", "tiene", "lleva", "desde", "hasta", "parte", "tipo", "pieza", "sola", "esta", "este"}


def _plano(t: str) -> str:
    t = (t or "").lower()
    for a, b in zip("áéíóúü", "aeiouu"):
        t = t.replace(a, b)
    return t
