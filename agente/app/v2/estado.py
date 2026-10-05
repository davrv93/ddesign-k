"""Memoria estructurada (spec §4): hechos confirmados, inferencias y datos desconocidos, separados.

Regla: una inferencia nunca pasa a hecho. «Temperatura tibia» es una inferencia; «ocasión: matrimonio» es un hecho
porque la clienta lo dijo. Esta función no adivina: solo reordena lo que ya hay en el contexto."""
from __future__ import annotations

from .contexto import REQUERIDOS

INFERIDOS = ("temperature",)


def separar(contexto: dict) -> dict:
    req = contexto.get("requirements") or {}
    cli = contexto.get("customer") or {}
    prod = contexto.get("product") or {}
    biz = contexto.get("business") or {}

    hechos = {k: v for k, v in req.items() if v}
    if cli.get("name"):
        hechos["nombre"] = cli["name"]
    if prod.get("focus"):
        hechos["prenda_en_foco"] = prod["focus"]   # la decidió el código (prenda en foco), no un modelo
    if biz.get("pedido_en_curso"):
        hechos["pedido_en_curso"] = biz["pedido_en_curso"]

    inferencias = {}
    if cli.get("temperature"):
        inferencias["temperature"] = cli["temperature"]

    desconocidos = [k for k in REQUERIDOS if not req.get(k)]
    return {"hechos": hechos, "inferencias": inferencias, "desconocidos": desconocidos}
