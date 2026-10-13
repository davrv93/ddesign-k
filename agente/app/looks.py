"""Looks: fotos del catálogo en las que van DOS prendas (la blusa y el pantalón que lleva la modelo). seed/looks.json, lo arma
deploy/catalogo-baruka-pe/looks.py. Si la clienta manda una de esas fotos, el bot nombra las dos prendas en vez de elegir
una (13-10-2026)."""
from __future__ import annotations

import json
import os

RUTA = os.path.join(os.environ.get("SEED_DIR", os.path.join(os.path.dirname(__file__), "..", "seed")), "looks.json")
_LOOKS: dict | None = None


def cargar() -> dict:
    global _LOOKS
    if _LOOKS is None:
        try:
            with open(RUTA, encoding="utf-8") as fh:
                _LOOKS = {k: [str(c).upper() for c in v] for k, v in json.load(fh).items()}
        except (OSError, ValueError):
            _LOOKS = {}
    return _LOOKS


def prendas_en(archivo: str) -> list[str]:
    """Los códigos que se ven en esa foto del índice ([] si no es un look)."""
    return list(cargar().get(os.path.basename(archivo or ""), []))
