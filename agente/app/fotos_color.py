"""La foto del COLOR que mostró la clienta (seed/fotos_color.json, lo arma deploy/catalogo-baruka-pe/agente.py).

12-10-2026: una clienta mandó el Vestido Salome en verde olivo; el bot lo reconoció y respondió con la foto azul noche.
Cada foto del índice lleva su color: el de baruka.pe cuando la web lo dice (`color_web`) y, si no, el que vio la IA en
mapa_fotos.py (`color_ia`). Con el color que la visión ve en la foto de la clienta se elige la foto de ese color.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata

RUTA = os.path.join(os.environ.get("SEED_DIR", os.path.join(os.path.dirname(__file__), "..", "seed")), "fotos_color.json")
# Mismo color, otro nombre (cómo lo dice la gente, cómo lo dice la web, cómo lo dice la IA).
SINONIMOS = {"oliva": "olivo", "salvia": "olivo", "sage": "olivo", "militar": "olivo", "azulino": "azul", "azulina": "azul", "marino": "noche", "navy": "noche", "borgona": "vino", "guinda": "vino", "burdeos": "vino",
             "granate": "vino", "black": "negro", "negra": "negro", "blanca": "blanco", "roja": "rojo", "rosada": "rosa",
             "rosado": "rosa", "dorada": "dorado", "plateada": "plateado", "green": "verde", "beige": "beige", "nude": "beige",
             "camel": "beige", "crema": "beige", "fucsia": "fucsia", "magenta": "fucsia", "celeste": "celeste",
             "turquesa": "turquesa", "turqueza": "turquesa"}
_VACIAS = {"color", "tono", "de", "y", "claro", "oscuro", "intenso"}
_MAPA: dict | None = None


def _cargar() -> dict:
    global _MAPA
    if _MAPA is None:
        try:
            with open(RUTA, encoding="utf-8") as fh:
                _MAPA = json.load(fh)
        except (OSError, ValueError):
            _MAPA = {}
    return _MAPA


def _tokens(color: str) -> list[str]:
    t = unicodedata.normalize("NFKD", (color or "").lower()).encode("ascii", "ignore").decode()
    return [SINONIMOS.get(w, w) for w in re.findall(r"[a-z]+", t) if w not in _VACIAS]


def _puntos(pedido: list[str], color: str) -> int:
    """Cuántas palabras del color pedido están en el de la foto; 0 si no coincide el tono base (la primera palabra)."""
    foto = _tokens(color)
    if not pedido or not foto or pedido[0] not in foto:
        return 0
    return sum(1 for w in pedido if w in foto)


def foto_del_color(codigo: str, color: str) -> tuple[str, str]:
    """(archivo, color como lo dice la web) de la foto de `codigo` en `color`, o ("", "") si no hay una de ese color.
    Gana la que coincide en más palabras; a igualdad, la que trae el color de la web (es el dato de la tienda)."""
    pedido = _tokens(color)
    mejor = (0, 0, "", "")
    for archivo, d in _cargar().items():
        if d.get("codigo", "").upper() != (codigo or "").upper():
            continue
        pw, pi = _puntos(pedido, d.get("color_web", "")), _puntos(pedido, d.get("color_ia", ""))
        clave = (max(pw, pi), 1 if pw else 0, archivo, d.get("color_web") or d.get("color_ia") or "")
        # «verde esmeralda» no es «verde olivo»: con dos palabras deben coincidir las dos.
        if clave[0] >= min(2, len(pedido)) and clave[:2] > mejor[:2]:
            mejor = clave
    if mejor[0]:
        return mejor[2], mejor[3]
    # Sin coincidencia completa («verde salvia» y la web dice «Verde Olivo», 13-10-2026): vale el tono base si el modelo
    # tiene UNA sola familia de ese tono (un solo verde). Con dos verdes no se adivina: va la foto principal.
    if len(pedido) >= 2:
        base = pedido[:1]
        familias = {}
        for archivo, d in _cargar().items():
            if d.get("codigo", "").upper() != (codigo or "").upper():
                continue
            for color in (d.get("color_web"), d.get("color_ia")):
                if color and _puntos(base, color):
                    familias.setdefault(" ".join(_tokens(color)), (archivo, d.get("color_web") or color))
                    break
        if len(familias) == 1:
            return next(iter(familias.values()))
    return "", ""


def color_de(archivo: str) -> str:
    """El color de una foto del índice como lo llama la web (o, si la web no lo dice, como lo vio la IA)."""
    d = _cargar().get(archivo) or {}
    return d.get("color_web") or d.get("color_ia") or ""


def es_personalizado(color: str) -> bool:
    """«Color personalizado» de baruka.pe no es un color: es que se confecciona en el que pida la clienta."""
    return "personaliz" in " ".join(_tokens(color))


def fotos_de_colores(codigo: str, colores_web) -> list[tuple[str, str]]:
    """[(archivo, color)] una foto por cada color de `colores_web` (en ese orden) que tenga foto propia del modelo, sin
    repetir archivo. «Color personalizado» no cuenta. 14-10-2026: «enviame los colores en foto» tras listar los ocho
    colores del Salome salía solo la azul noche que ya había visto, y el bot decía que no tenía fotos de los otros."""
    out, vistos = [], set()
    for color in colores_web or ():
        if not color or es_personalizado(color):
            continue
        archivo, c_web = foto_del_color(codigo, color)
        if archivo and archivo not in vistos:
            vistos.add(archivo)
            out.append((archivo, c_web or color))
    return out


def colores_nombrados(texto: str, colores_web) -> list[str]:
    """Los colores de `colores_web` que nombra el texto, en el orden de la lista: «¿cuál es el tono verde olivo?» →
    [«Verde Olivo»]. Vale el nombre completo («verde olivo», «azul noche») o solo el tono base («verde») si el modelo
    tiene un solo color de ese tono; «terracota» o «acero», que memoria.py no reconoce como color, también cuentan."""
    palabras = set(_tokens(texto))
    if not palabras:
        return []
    reales = [c for c in colores_web or () if c and not es_personalizado(c)]
    completos = [c for c in reales if _tokens(c) and all(w in palabras for w in _tokens(c))]
    base = {}
    for c in reales:
        t = _tokens(c)
        if t and t[0] in palabras and c not in completos:
            base.setdefault(t[0], []).append(c)
    sueltos = [cs[0] for cs in base.values() if len(cs) == 1 and not any(_tokens(x)[0] == _tokens(cs[0])[0] for x in completos)]
    elegidos = set(completos) | set(sueltos)
    return [c for c in reales if c in elegidos]

