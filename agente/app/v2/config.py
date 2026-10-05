"""Configuración de la V2. Todo viene del entorno; nada va fijo en el código.

AGENT_VERSION      v1 (por defecto) | v2
MAX_AGENT_STEPS    pasos del agente por turno (1–10)
MAX_DECISION_CALLS llamadas al motor de decisión por turno (0–10)
MAX_TOOL_CALLS     herramientas (stock, RAG, CRM) por turno (0–10)
MAX_REGENERATIONS  regeneraciones tras un fallo de calidad (0–3)
DECISION_TIMEOUT_MS / GENERATION_TIMEOUT_MS  (100–60000)
"""
from __future__ import annotations

import os
from dataclasses import dataclass

VERSIONES = ("v1", "v2")


def version_por_defecto(env=os.environ) -> str:
    v = (env.get("AGENT_VERSION") or "v1").strip().lower()
    return v if v in VERSIONES else "v1"


def version_pedida(valor: str | None, defecto: str) -> str:
    """La versión que pide la petición; si viene vacía o no existe, la del entorno."""
    v = (valor or "").strip().lower()
    return v if v in VERSIONES else defecto


def _entero(env, nombre: str, defecto: int, minimo: int, maximo: int) -> int:
    try:
        v = int(env.get(nombre, defecto))
    except (TypeError, ValueError):
        v = defecto
    return max(minimo, min(maximo, v))


@dataclass(frozen=True)
class Limites:
    max_pasos: int = 4
    max_decisiones: int = 3
    max_herramientas: int = 4
    max_regeneraciones: int = 1
    timeout_decision_ms: int = 1500
    timeout_generacion_ms: int = 5000


def limites_desde_entorno(env=os.environ) -> Limites:
    return Limites(
        max_pasos=_entero(env, "MAX_AGENT_STEPS", 4, 1, 10),
        max_decisiones=_entero(env, "MAX_DECISION_CALLS", 3, 0, 10),
        max_herramientas=_entero(env, "MAX_TOOL_CALLS", 4, 0, 10),
        max_regeneraciones=_entero(env, "MAX_REGENERATIONS", 1, 0, 3),
        timeout_decision_ms=_entero(env, "DECISION_TIMEOUT_MS", 1500, 100, 60000),
        timeout_generacion_ms=_entero(env, "GENERATION_TIMEOUT_MS", 5000, 100, 60000),
    )


class LimiteExcedido(RuntimeError):
    """Se gastó el presupuesto de un tipo de paso en este turno. Nunca hay bucles infinitos."""


class Presupuesto:
    """Cuenta los pasos de un turno y corta en cuanto se agotan."""

    def __init__(self, limites: Limites):
        self.limites = limites
        self._topes = {
            "paso": limites.max_pasos,
            "decision": limites.max_decisiones,
            "herramienta": limites.max_herramientas,
            "regeneracion": limites.max_regeneraciones,
        }
        self.usos = {k: 0 for k in self._topes}

    def gastar(self, tipo: str) -> None:
        if tipo not in self._topes:
            raise ValueError(f"tipo de paso desconocido: {tipo}")
        if self.usos[tipo] >= self._topes[tipo]:
            raise LimiteExcedido(f"{tipo}: tope {self._topes[tipo]} agotado")
        self.usos[tipo] += 1

    def quedan(self, tipo: str) -> int:
        return self._topes[tipo] - self.usos[tipo]
