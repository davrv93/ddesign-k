"""Grupos de color del guion de noche (seed/speech.md, paso 4): «¿Prefieres llamar un poco más la atención (colores
VIBRANTES), o buscas verte elegante, sofisticada y más discreta (colores SOBRIOS)?».

Con la respuesta (memoria `sabemos.estilo`) el agente ofrece primero las prendas que vienen en colores de ese grupo y manda
la foto en uno de esos colores. Qué color va en qué grupo lo decide la tienda: jmdventas lo manda en cada turno
(`colores_grupos`, Ajustes → Colores); si no viene, vale seed/colores_grupos.json (propuesta de
deploy/catalogo-baruka-pe/colores_grupos.py, 13-10-2026).
"""
from __future__ import annotations

import json
import os
import re
import unicodedata

from . import ficha_producto, fotos_color

RUTA = os.path.join(os.environ.get("SEED_DIR", os.path.join(os.path.dirname(__file__), "..", "seed")), "colores_grupos.json")
GRUPOS = ("sobrio", "vibrante")
GRUPO_DE_ESTILO = {"llamar la atención": "vibrante", "elegante y discreta": "sobrio"}
NOMBRE_GRUPO = {"vibrante": "vibrantes", "sobrio": "sobrios"}
_SEED: dict | None = None


def normaliza(color: str) -> str:
    """«Azul Noche» → «azul noche», «Marrón.» → «marron»: la clave con la que se compara."""
    t = unicodedata.normalize("NFKD", (color or "").lower()).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t).strip().strip(".").strip()


def _seed() -> dict[str, str]:
    global _SEED
    if _SEED is None:
        try:
            with open(RUTA, encoding="utf-8") as fh:
                _SEED = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
        except (OSError, ValueError):
            _SEED = {}
    return _SEED


def grupos(config: dict | None = None) -> dict[str, str]:
    """{color normalizado: grupo}. Lo que mandó la tienda si trae algo; si no, el seed."""
    if config:
        return {normaliza(k): (v if v in GRUPOS else "") for k, v in config.items() if isinstance(v, str)}
    return _seed()


def grupo_de(color: str, config: dict | None = None) -> str:
    return grupos(config).get(normaliza(color), "")


def colores_de(f) -> list[str]:
    """Los colores de una prenda como los dice baruka.pe; si la web no los trae, los del catálogo de la tienda."""
    web = ficha_producto.web(getattr(f, "codigo", "") or "")
    if web.get("colores"):
        return list(web["colores"])
    return [c.strip() for c in (getattr(f, "color", "") or "").split(",") if c.strip()]


def del_grupo(f, grupo: str, config: dict | None = None) -> list[str]:
    """Los colores de la prenda que caen en `grupo` (vacío si ninguno)."""
    if grupo not in GRUPOS:
        return []
    g = grupos(config)
    return [c for c in colores_de(f) if g.get(normaliza(c)) == grupo]


def disponibles(fichas, grupo: str, config: dict | None = None) -> list[str]:
    """Colores del grupo que de verdad existen en el catálogo, por frecuencia (para decírselos a la clienta)."""
    cuenta: dict[str, int] = {}
    for f in fichas:
        for c in del_grupo(f, grupo, config):
            k = normaliza(c)
            cuenta[k] = cuenta.get(k, 0) + 1
    return [k for k, _ in sorted(cuenta.items(), key=lambda kv: (-kv[1], kv[0]))]


def foto_del_grupo(codigo: str, grupo: str, config: dict | None = None) -> tuple[str, str]:
    """(archivo, color) de la foto de `codigo` en un color del grupo, o ("", "") si no tiene ninguna. Pesa más el color
    que dice la web; la principal (v45.jpg) gana a las secundarias a igualdad."""
    if grupo not in GRUPOS:
        return "", ""
    g = grupos(config)
    mejor: tuple[int, str, str] | None = None
    for archivo, d in sorted(fotos_color._cargar().items()):
        if d.get("codigo", "").upper() != (codigo or "").upper():
            continue
        if g.get(normaliza(d.get("color_web", ""))) == grupo:
            cand = (2, archivo, d["color_web"])
        elif not d.get("color_web") and g.get(normaliza(d.get("color_ia", ""))) == grupo:
            cand = (1, archivo, d["color_ia"])
        else:
            continue
        if mejor is None or cand[0] > mejor[0]:
            mejor = cand
    return (mejor[1], mejor[2]) if mejor else ("", "")
