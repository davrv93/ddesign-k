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
                _LOOKS = {k: [str(c).upper() for c in v] for k, v in json.load(fh).items() if not k.startswith("_")}
                with open(RUTA, encoding="utf-8") as fh2:
                    _LOOKS["_excluidas"] = [str(x) for x in json.load(fh2).get("_excluidas", [])]
        except (OSError, ValueError):
            _LOOKS = {}
    return _LOOKS


def prendas_en(archivo: str) -> list[str]:
    """Los códigos que se ven en esa foto del índice ([] si no es un look)."""
    a = os.path.basename(archivo or "")
    return [] if a.startswith("_") else list(cargar().get(a, []))


def excluida(archivo: str) -> bool:
    """Foto que el usuario revisó y decidió que NO es un look (looks.py EXCLUIR)."""
    return os.path.basename(archivo or "") in cargar().get("_excluidas", [])
