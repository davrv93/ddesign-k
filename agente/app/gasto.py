"""Gasto en dólares de cada mensaje (DeepSeek y Jev por OpenRouter).

Cada petición abre su propia cuenta (`iniciar`) y las llamadas de pago la suman (`sumar`). La respuesta del agente
la devuelve en `costo_usd`: así el arnés de conversaciones (app/conversaciones.py) lleva la cuenta sin depender
del saldo de la clave, que comparten otros servicios. OpenRouter trae el costo en `usage.cost`; si no viene, se
estima por tokens con el precio de DeepSeek (`PRECIO_ENTRADA`/`PRECIO_SALIDA`, US$ por millón).
"""
from __future__ import annotations

import os
from contextvars import ContextVar

PRECIO_ENTRADA = float(os.environ.get("PRECIO_ENTRADA_USD_M", "0.27"))
PRECIO_SALIDA = float(os.environ.get("PRECIO_SALIDA_USD_M", "1.10"))

_cuenta: ContextVar[list | None] = ContextVar("_cuenta_gasto", default=None)


def iniciar() -> None:
    _cuenta.set([])


def sumar(uso: dict | None) -> None:
    """Suma el costo de una respuesta de OpenRouter (`usage`). Fuera de una petición no hace nada."""
    c = _cuenta.get()
    if c is None or not isinstance(uso, dict):
        return
    costo = uso.get("cost")
    if costo is None:
        ent = uso.get("prompt_tokens") or uso.get("input_tokens") or 0
        sal = uso.get("completion_tokens") or uso.get("output_tokens") or 0
        costo = (ent * PRECIO_ENTRADA + sal * PRECIO_SALIDA) / 1e6
    try:
        c.append(float(costo))
    except (TypeError, ValueError):
        pass


def total() -> float:
    return round(sum(_cuenta.get() or []), 6)
