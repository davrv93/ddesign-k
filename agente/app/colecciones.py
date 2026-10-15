"""Catálogo por secciones de baruka.pe (14-10-2026, pedido de la tienda).

Cuando la clienta pide el catálogo («pásame el catálogo», «¿tienen página?», «quiero ver todos los enterizos», «quiero
ver todo»), el bot manda el ENLACE DE LA SECCIÓN que pide, con la captura de esa sección, como lo hace la tienda a mano:
«Aquí te comparto el catálogo de vestidos largos, dale clic 👇👇👇👇 https://baruka.pe/collections/vestidos».

La sección sale, en este orden: de lo que nombra el mensaje (enterizos, blusas, vestidos largos…); si no nombra ninguna,
de la prenda en foco (la sección donde baruka.pe la publica); si no hay, del tipo de prenda que busca (memoria `prenda`);
si tampoco, el catálogo completo. «Vestidos» a secas son dos secciones: largos y cortos.

Datos: seed/colecciones.json e imagenes/colecciones/ (deploy/catalogo-baruka-pe/capturas_colecciones.py).
"""
from __future__ import annotations

import json
import os
import re

from . import ficha_producto, memoria

SEED = os.path.join(os.environ.get("SEED_DIR", os.path.join(os.path.dirname(__file__), "..", "seed")), "colecciones.json")
_DATOS: dict | None = None

# Pide el catálogo o ver «todo» (de un tipo o de la tienda). «De todos» / «los tres» tras nombrar modelos NO es esto: son
# las fotos de esos modelos (agente.py lo resuelve antes).
RE_PIDE_CATALOGO = re.compile(
    r"\b(cat[aá]logos?|p[aá]gina( web)?|web|link|enlace|tienda (online|virtual|en linea)|"
    r"(ver|veo|mostrar|muestrame|ensename|pasame|mandame|enviame) (todo|toda la coleccion|todos los modelos|todas las opciones|todo lo que (tienes|tengas|hay))|"
    r"todo lo que (tienes|tengas|hay)|toda la coleccion|todos (los|tus) (modelos|vestidos|enterizos|pantalones|conjuntos|blazers|disenos)|"
    r"todas (las|tus) (blusas|prendas|opciones)|seccion de|la coleccion de)\b")
# Tipo de prenda de un producto por la primera palabra de su nombre (si baruka.pe no lo publica en ninguna sección).
POR_TIPO = {"vestido": ["vestidos", "vestido-cortos"], "enterizo": ["enterizos"], "blusa": ["blusas"],
            "pantalon": ["pantalon"], "conjunto": ["conjuntos"], "blazer": ["blazer"]}


def _datos() -> dict:
    global _DATOS
    if _DATOS is None:
        try:
            with open(SEED, encoding="utf-8") as fh:
                _DATOS = json.load(fh).get("colecciones") or {}
        except (OSError, ValueError):
            _DATOS = {}
    return _DATOS


def pide_catalogo(texto: str) -> bool:
    return bool(RE_PIDE_CATALOGO.search(memoria._plano(texto or "")))


def en_texto(texto: str) -> list[str]:
    """Las secciones que nombra el texto, por la palabra más larga primero («vestidos largos» antes que «vestidos»)."""
    plano = memoria._plano(texto or "")
    pares = sorted(((a, h) for h, d in _datos().items() for a in d.get("alias") or []), key=lambda x: -len(x[0]))
    out, usado = [], plano
    for alias, h in pares:
        if re.search(r"\b" + re.escape(alias) + r"\b", usado) and h not in out:
            out.append(h)
            usado = re.sub(r"\b" + re.escape(alias) + r"\b", " ", usado)
    if re.search(r"\bvestidos?\b", usado) and not ({"vestidos", "vestido-cortos"} & set(out)):
        out += ["vestidos", "vestido-cortos"]          # «vestidos» a secas: largos y cortos
    return out


def de_producto(codigo: str) -> list[str]:
    """La sección donde baruka.pe publica ese modelo; si no está en ninguna, la de su tipo de prenda."""
    w = ficha_producto.web(codigo) if codigo else {}
    handle = w.get("handle") or ""
    secciones = [h for h, d in _datos().items() if h != "all" and handle and handle in (d.get("productos") or [])]
    if secciones:
        return secciones[:1]
    nombre = memoria._plano((ficha_producto.ficha(codigo) or {}).get("nombre") or "")
    return [h for h in POR_TIPO.get(nombre.split(" ")[0] if nombre else "", []) if h in _datos()][:2]


def elegir(texto: str, f: dict) -> list[str]:
    """Secciones a mandar: las del mensaje, la de la prenda en foco, la del tipo de prenda que busca, o el catálogo entero."""
    secciones = en_texto(texto)
    if not secciones and f.get("producto"):
        secciones = de_producto(f["producto"])
    prenda = memoria._plano((f.get("sabemos") or {}).get("prenda") or "")
    if not secciones and prenda:
        secciones = [h for h in POR_TIPO.get(prenda.rstrip("s"), []) if h in _datos()]
    return [h for h in secciones if h in _datos()][:2] or (["all"] if "all" in _datos() else [])


def tarjeta(h: str) -> dict | None:
    """La «foto» que manda el bot: la captura de la sección con el texto y el enlace en el pie."""
    d = _datos().get(h)
    if not d:
        return None
    pie = (f"Aquí te comparto nuestro catálogo completo, dale clic 👇👇👇👇\n{d['url']}" if h == "all" else
           f"Aquí te comparto el catálogo de {d['titulo']}, dale clic 👇👇👇👇\n{d['url']}")
    return {"codigo": "", "nombre": d["titulo"], "imagen": f"/media/tienda/{d['captura']}", "pie": pie, "titulo": "", "tallas": [],
            "color_foto": "", "sin_color": "", "coleccion": h, "url": d["url"]}


def archivo_valido(archivo: str) -> bool:
    return bool(re.fullmatch(r"coleccion_[a-z0-9-]+\.jpg", archivo or "")) and any(
        d.get("captura") == archivo for d in _datos().values())
