"""Stock por sucursal. Hoy sale de seed/sucursales.json (datos de demostración)."""
from __future__ import annotations

import json
import os

RUTA = os.environ.get("SUCURSALES_JSON", os.path.join(os.path.dirname(__file__), "..", "seed", "sucursales.json"))


class Sucursales:
    def __init__(self, ruta: str = RUTA):
        try:
            with open(ruta, encoding="utf-8") as f:
                d = json.load(f)
        except FileNotFoundError:
            d = {"sucursales": [], "stock": {}}
        self.lista = d["sucursales"]
        self.por_id = {s["id"]: s for s in self.lista}
        self.stock = d["stock"]

    def de(self, codigo: str) -> list[tuple[dict, dict]]:
        """[(sucursal, {talla: n})] donde el código tiene unidades."""
        out = []
        for sid, tallas in self.stock.get(codigo, {}).items():
            tallas = {t: n for t, n in tallas.items() if n > 0}
            if tallas and sid in self.por_id:
                out.append((self.por_id[sid], tallas))
        return out

    def texto(self, codigo: str) -> str:
        """Línea para el LLM y para los pies de foto."""
        hay = self.de(codigo)
        if not hay:
            return "sin stock en sucursales"
        return "; ".join(f"{s['nombre']} ({s['direccion']}): " + ", ".join(f"{t}={n}" for t, n in tallas.items())
                         for s, tallas in hay)
