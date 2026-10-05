"""Motor recursivo (spec §9–10, el planteamiento «Jev style»).

Ciclo por turno:

    DECIDE → (¿necesita un hecho?) → TOOL → ACTUALIZA ESTADO → DECIDE DE NUEVO → … → PLAN → VALIDA

Cada vuelta gasta pasos del presupuesto (MAX_AGENT_STEPS, MAX_DECISION_CALLS, MAX_TOOL_CALLS). Cuando se agotan, el
ciclo se corta: nunca hay bucles infinitos. Una herramienta ya consultada no se vuelve a llamar: el hecho queda en el
estado y el motor decide con él.

El motor no escribe texto ni llama a DeepSeek. Su salida es un plan validado que, en sombra, se compara con lo que
hizo V1."""
from __future__ import annotations

import copy
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

from . import config
from .interfaces import DecisionEngine
from .plan import ACCIONES_CON_PRODUCTO, Plan, validar

DECISIONES = ["intent", "next_action"]
ACCION_HERRAMIENTA = {"consultar_stock": "stock"}


@dataclass
class Resultado:
    plan: Plan | None
    errores: list[str]
    pasos: list[dict] = field(default_factory=list)
    tope: str | None = None
    motor: str = ""
    ms: int = 0

    def a_dict(self) -> dict:
        return {
            "plan": self.plan.a_dict() if self.plan else None,
            "errores": self.errores,
            "pasos": self.pasos,
            "tope": self.tope,
            "motor": self.motor,
            "ms": self.ms,
        }


class MotorRecursivo:
    def __init__(self, decision: DecisionEngine, herramientas: dict[str, Callable[[str], object]] | None = None,
                 limites: config.Limites | None = None):
        self.decision = decision
        self.herramientas = herramientas or {}
        self.limites = limites or config.limites_desde_entorno()

    def ejecutar(self, contexto: dict) -> Resultado:
        t0 = time.perf_counter()
        estado = copy.deepcopy(contexto)
        estado.setdefault("herramientas", {})
        pres = config.Presupuesto(self.limites)
        pasos: list[dict] = []
        tope: str | None = None
        ultima: list = []

        while True:
            try:
                pres.gastar("paso")
                pres.gastar("decision")
            except config.LimiteExcedido as e:
                tope = str(e)
                break

            ultima = self.decision.decide(estado, DECISIONES)
            paso = {"n": len(pasos) + 1, "decisiones": [asdict(d) for d in ultima], "herramienta": None}
            accion = next((d.choice for d in ultima if d.decision == "next_action"), "responder")
            pasos.append(paso)

            nombre_tool = ACCION_HERRAMIENTA.get(accion)
            foco = (estado.get("product") or {}).get("focus")
            if nombre_tool and nombre_tool in self.herramientas and foco:
                if estado["herramientas"].get(nombre_tool) is None:      # no se repite una consulta ya hecha
                    try:
                        pres.gastar("herramienta")
                    except config.LimiteExcedido as e:
                        tope = str(e)
                        break
                    estado["herramientas"][nombre_tool] = self.herramientas[nombre_tool](foco)
                    paso["herramienta"] = nombre_tool
                    paso["resultado"] = estado["herramientas"][nombre_tool]
                    continue                                              # RECURSIÓN: decide de nuevo con el hecho nuevo
                continue                                                  # ya lo sabemos: el siguiente paso decide otra cosa
            break

        plan, codigos_ok = self._plan(estado, ultima)
        errores = validar(plan, codigos_ok) if plan else ["sin plan"]
        return Resultado(plan=plan, errores=errores, pasos=pasos, tope=tope, motor=self.decision.nombre,
                         ms=int((time.perf_counter() - t0) * 1000))

    @staticmethod
    def _plan(estado: dict, decs: list) -> tuple[Plan | None, set[str]]:
        accion = next((d.choice for d in decs if d.decision == "next_action"), "responder")
        foco = (estado.get("product") or {}).get("focus")
        stock = (estado.get("herramientas") or {}).get("stock")
        codigos_ok = {foco} if (foco and stock == "online") else set()
        producto = foco if accion in ACCIONES_CON_PRODUCTO and foco else None
        if accion == "recomendar" and not producto:
            return None, codigos_ok
        hechos = [f"{k}: {v}" for k, v in (estado.get("requirements") or {}).items() if v]
        return Plan(accion=accion, producto=producto, hechos=hechos,
                    razon=f"decisión {accion} con stock {stock!r}"), codigos_ok
