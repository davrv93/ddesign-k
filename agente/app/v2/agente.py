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

log = logging.getLogger("agente.v2")


class AgentV2:
    def __init__(self, v1: Callable, limites: config.Limites | None = None, contexto: ContextBuilder | None = None):
        self.v1 = v1
        self.limites = limites or config.limites_desde_entorno()
        self.contexto = contexto or ContextBuilder()

    def conversar(self, req) -> dict:
        t0 = time.perf_counter()
        traza = {"agent_version": "v2", "fase": 1, "fallback": False, "motivo": None}
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
        traza["ms"] = int((time.perf_counter() - t0) * 1000)
        res["v2"] = traza
        return res
