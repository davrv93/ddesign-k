"""AgentV2 (spec fase 1): la ruta V2 del turno.

Fase 1: el turno lo resuelve el flujo V1 completo (`v1`, inyectado para no crear importaciones circulares). La V2 le
añade el contexto estructurado, la separación de hechos e inferencias y una traza (`res["v2"]`). Si el contexto falla,
el turno sigue por V1: la caída de la capa nueva nunca tira el bot. Las fases siguientes sustituyen el flujo V1 por
motores de decisión y de generación, detrás de las mismas interfaces.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from . import config
from .contexto import ContextBuilder
from .estado import separar
from .motor import MotorRecursivo

log = logging.getLogger("agente.v2")


class AgentV2:
    def __init__(self, v1: Callable, limites: config.Limites | None = None, contexto: ContextBuilder | None = None,
                 motor: MotorRecursivo | None = None):
        self.v1 = v1
        self.limites = limites or config.limites_desde_entorno()
        self.contexto = contexto or ContextBuilder()
        self.motor = motor   # None = sin motor de decisión (fase 1)

    def conversar(self, req) -> dict:
        t0 = time.perf_counter()
        traza = {"agent_version": "v2", "fase": 2, "fallback": False, "motivo": None}
        try:
            ctx = self.contexto.construir(req)
            traza["separado"] = separar(ctx)
            traza["ctx_turnos"] = len(ctx["conversation"]["recent_turns"])
        except Exception as e:  # la capa nueva nunca tira el turno
            log.warning("v2: contexto falló (%s); el turno sigue por V1", type(e).__name__)
            traza["fallback"] = True
            traza["motivo"] = type(e).__name__
        res = dict(self.v1(req))
        res["version"] = "v2"
        if self.motor is not None and not traza["fallback"]:
            traza["sombra"] = self._en_sombra(ctx, res)
        traza["ms"] = int((time.perf_counter() - t0) * 1000)
        res["v2"] = traza
        return res

    def _en_sombra(self, ctx: dict, res_v1: dict) -> dict:
        """Ejecuta el motor recursivo y compara su plan con lo que hizo V1. No cambia lo que se envía."""
        try:
            r = self.motor.ejecutar(ctx).a_dict()
        except Exception as e:  # el motor nunca tira el turno
            log.warning("v2: motor falló (%s); sin plan en este turno", type(e).__name__)
            return {"error": type(e).__name__}
        r["v1_accion"] = res_v1.get("accion")
        r["v1_etapa"] = res_v1.get("etapa")
        return r
