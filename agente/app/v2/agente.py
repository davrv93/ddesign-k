"""AgentV2: la ruta V2 del turno.

Flujo por turno (fases 1–5, en sombra mientras se valida):

    contexto estructurado → motor recursivo (decide, usa herramientas, vuelve a decidir) → plan validado
    → redactor (plantillas desde el plan) → quality gate → regeneración acotada → fallback de código

V2 nunca cambia el texto que se envía: lo que devuelve al cliente es el de V1. El borrador de V2 y su control de
calidad van en `res["v2"]`, para comparar con V1 (fase 6–7). Cualquier fallo de la capa nueva deja la respuesta de V1.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from . import config
from .calidad import FALLBACK
from .contexto import ContextBuilder
from .estado import separar
from .motor import MotorRecursivo, Resultado

log = logging.getLogger("agente.v2")


class AgentV2:
    def __init__(self, v1: Callable, limites: config.Limites | None = None, contexto: ContextBuilder | None = None,
                 motor: MotorRecursivo | None = None, calidad=None, redactor=None):
        self.v1 = v1
        self.limites = limites or config.limites_desde_entorno()
        self.contexto = contexto or ContextBuilder()
        self.motor = motor
        self.calidad = calidad
        self.redactor = redactor

    def conversar(self, req) -> dict:
        t0 = time.perf_counter()
        traza = {"agent_version": "v2", "fase": 5, "fallback": False, "motivo": None}
        ctx = None
        try:
            ctx = self.contexto.construir(req)
            traza["separado"] = separar(ctx)
            traza["ctx_turnos"] = len(ctx["conversation"]["recent_turns"])
        except Exception as e:   # la capa nueva nunca tira el turno
            log.warning("v2: contexto falló (%s); el turno sigue por V1", type(e).__name__)
            traza["fallback"] = True
            traza["motivo"] = type(e).__name__
        res = dict(self.v1(req))
        res["version"] = "v2"
        if ctx is not None and self.motor is not None:
            traza["sombra"] = self._en_sombra(ctx, res)
        traza["ms"] = int((time.perf_counter() - t0) * 1000)
        res["v2"] = traza
        return res

    def _en_sombra(self, ctx: dict, res_v1: dict) -> dict:
        try:
            r: Resultado = self.motor.ejecutar(ctx)
        except Exception as e:   # el motor nunca tira el turno
            log.warning("v2: motor falló (%s); sin plan en este turno", type(e).__name__)
            return {"error": type(e).__name__}
        salida = r.a_dict() | {"v1_accion": res_v1.get("accion"), "v1_etapa": res_v1.get("etapa")}
        if r.plan is not None and not r.errores and self.redactor and self.calidad:
            salida["generacion"] = self._redactar(r, res_v1)
        return salida

    def _redactar(self, r: Resultado, res_v1: dict) -> dict:
        """Borrador desde el plan, validado. Regenera con la siguiente variante (acotado) y cae al texto seguro."""
        intentos = []
        plan_d = r.plan.a_dict()
        n_variantes = 1 + self.limites.max_regeneraciones
        for variante in range(n_variantes):
            try:
                borrador = self.redactor.redactar(plan_d, variante)
            except Exception as e:
                intentos.append({"variante": variante, "error": type(e).__name__})
                continue
            q = self.calidad.evaluar(borrador, r.plan)
            intentos.append({"variante": variante, "passed": q["passed"], "score": q["score"], "errors": q["errors"],
                             "texto": borrador})
            if q["passed"]:
                return {"texto": borrador, "passed": True, "fallback": False, "regeneraciones": variante,
                        "intentos": _sin_texto(intentos), "v1_texto": res_v1.get("respuesta")}
        return {"texto": FALLBACK, "passed": False, "fallback": True, "regeneraciones": len(intentos) - 1,
                "intentos": _sin_texto(intentos), "v1_texto": res_v1.get("respuesta")}


def _sin_texto(intentos: list[dict]) -> list[dict]:
    """La traza guarda cada intento sin su texto (el texto de V1 ya va en la respuesta)."""
    return [{k: v for k, v in i.items() if k != "texto"} for i in intentos]
