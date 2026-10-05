"""Interfaces desacopladas (spec §5 y §23). Cada una tiene implementaciones intercambiables por configuración:
local (modelo en el servidor), remota (API) o de reglas. La V2 nunca depende de una implementación concreta."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Decision:
    """Una decisión estructurada: nunca texto libre (spec §6)."""
    decision: str          # intent | stage | next_action | product_focus | escalation | response_quality | ...
    choice: str            # la opción elegida, de un conjunto cerrado
    confianza: float       # 0–1
    fuente: str = "regla"  # regla | local | remota | respaldo


@runtime_checkable
class DecisionEngine(Protocol):
    nombre: str

    def decide(self, contexto: dict, decisiones: list[str]) -> list[Decision]:
        """Devuelve una Decision por cada nombre pedido en `decisiones`. No escribe texto para la clienta."""
        ...


@runtime_checkable
class GenerationEngine(Protocol):
    nombre: str

    def redactar(self, plan: dict) -> str:
        """Convierte un plan ya validado en lenguaje natural. No decide producto, precio, stock ni etapa."""
        ...


@runtime_checkable
class QualityGate(Protocol):
    def evaluar(self, borrador: str, plan: dict) -> dict:
        """{passed: bool, score: float, errors: [str]}."""
        ...
