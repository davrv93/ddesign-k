"""Configuración de la V2. Todo viene del entorno; nada va fijo en el código.

AGENT_VERSION      v1 (por defecto) | v2
V2_MODO            sombra (por defecto: V2 solo observa) | activo (V2 puede hablar)
V2_HABLA           acciones en las que V2 habla en modo activo (por defecto: recomendar,preguntar; «responder_y_retomar» = cambios de tema)
V2_TEMAS           0 apaga el seguimiento de cambios de tema (pila de pendientes); por defecto corre con V2
MAX_AGENT_STEPS    pasos del agente por turno (1–10)
MAX_DECISION_CALLS llamadas al motor de decisión por turno (0–10; 4 = el camino stock→RAG→stock→recomendar)
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


MODOS = ("sombra", "activo")


def modo_por_defecto(env=os.environ) -> str:
    """V2_MODO: sombra (por defecto: V2 solo observa) | activo (V2 puede hablar; ver AgentV2)."""
    m = (env.get("V2_MODO") or "sombra").strip().lower()
    return m if m in MODOS else "sombra"


ACCIONES_HABLADAS = ("recomendar", "preguntar", "responder_y_retomar")
# «responder_y_retomar» (cambios de tema: responde lo que ella preguntó y retoma UN pendiente) NO está en el valor por defecto: se
# enciende a propósito con V2_HABLA=recomendar,preguntar,responder_y_retomar. Sin ella, V2 sigue la pila de temas en sombra
# (la traza dice qué retomaría) pero la clienta recibe el texto de siempre.
HABLA_DEFECTO = ("recomendar", "preguntar")


def habla_por_defecto(env=os.environ) -> tuple[str, ...]:
    """V2_HABLA: en qué acciones puede hablar V2 en modo activo. Por defecto `recomendar` y `preguntar`.

    En `preguntar`, V1 reconoce lo que la clienta acaba de decir («¡Sí, tenemos vestidos!», «¡Mucho gusto, Alvaro!»). Con
    las plantillas semánticas V2 también lo reconoce (acuses con datos de V1), así que el A/B arranca con preguntas y
    recomendaciones. Antes de las plantillas, una V2 que solo ponía la pregunta perdía esos acuses y la regresión lo detectó."""
    pedidas = tuple(x.strip().lower() for x in (env.get("V2_HABLA") or ",".join(HABLA_DEFECTO)).split(",") if x.strip())
    ok = tuple(x for x in pedidas if x in ACCIONES_HABLADAS)
    return ok or HABLA_DEFECTO


def temas_activos(env=os.environ) -> bool:
    """V2_TEMAS=0 apaga por completo el seguimiento de cambios de tema (la pila y su traza). Por defecto corre cuando corre V2."""
    return (env.get("V2_TEMAS") or "1").strip().lower() not in ("0", "no", "off", "false")


def modo_pedido(valor: str | None, defecto: str) -> str:
    """El modo que pide la petición; vacío o inválido = el del entorno."""
    m = (valor or "").strip().lower()
    return m if m in MODOS else defecto


def _entero(env, nombre: str, defecto: int, minimo: int, maximo: int) -> int:
    try:
        v = int(env.get(nombre, defecto))
    except (TypeError, ValueError):
        v = defecto
    return max(minimo, min(maximo, v))


@dataclass(frozen=True)
class Limites:
    max_pasos: int = 4
    max_decisiones: int = 4
    max_herramientas: int = 4
    max_regeneraciones: int = 1
    timeout_decision_ms: int = 1500
    timeout_generacion_ms: int = 5000


def limites_desde_entorno(env=os.environ) -> Limites:
    return Limites(
        max_pasos=_entero(env, "MAX_AGENT_STEPS", 4, 1, 10),
        max_decisiones=_entero(env, "MAX_DECISION_CALLS", 4, 0, 10),
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
