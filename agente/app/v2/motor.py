"""Motor recursivo (spec §9–10, «Jev style»).

Ciclo por turno:

    DECIDE → (¿falta un hecho?) → TOOL → ACTUALIZA ESTADO → DECIDE DE NUEVO → … → PLAN → VALIDA

Herramientas: `stock` (estado en vivo de un código), `rag` (códigos del catálogo para una búsqueda) y `crm` (lo que
sabemos de la clienta por sus pedidos anteriores; se lee una vez al empezar, sin que ninguna decisión lo pida). Cada
vuelta gasta pasos del presupuesto (MAX_AGENT_STEPS, MAX_DECISION_CALLS, MAX_TOOL_CALLS). Al agotarse, el ciclo se
corta: nunca hay bucles infinitos. Un dato ya consultado no se vuelve a pedir.

Una acción que necesita una herramienta no registrada se convierte en «preguntar»: nunca se afirma algo sin
herramienta. Si una herramienta falla, no se adivina su resultado: el plan pasa a una asesora. El motor no escribe
texto ni llama a ningún modelo generativo."""
from __future__ import annotations

import copy
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

from . import config
from .estado import separar
from .interfaces import Decision, DecisionEngine
from .plan import ACCIONES_CON_PRODUCTO, Plan, validar

DECISIONES = ["intent", "next_action"]
# acción → herramienta que necesita para tener el hecho que falta
ACCION_TOOL = {"consultar_stock": "stock", "buscar_alternativa": "rag"}
PREGUNTA_TALLA = {"tipo": "talla", "texto": "¿Qué talla usas normalmente?"}


@dataclass
class Resultado:
    plan: Plan | None
    errores: list[str]
    pasos: list[dict] = field(default_factory=list)
    tope: str | None = None
    motor: str = ""
    ms: int = 0
    herramientas: dict = field(default_factory=dict)
    fallo_herramienta: str | None = None

    def a_dict(self) -> dict:
        return {
            "plan": self.plan.a_dict() if self.plan else None,
            "errores": self.errores,
            "pasos": self.pasos,
            "tope": self.tope,
            "motor": self.motor,
            "ms": self.ms,
            "herramientas": self.herramientas,
            "fallo_herramienta": self.fallo_herramienta,
        }


def _consulta_rag(estado: dict) -> str:
    req = estado.get("requirements") or {}
    hechos = " ".join(str(req[k]) for k in ("prenda", "ocasion", "horario", "color") if req.get(k))
    return hechos or (estado.get("conversation") or {}).get("last_user_message") or ""


class MotorRecursivo:
    def __init__(self, decision: DecisionEngine, herramientas: dict[str, Callable] | None = None,
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
        fallo_tool: str | None = None

        # CRM: contexto de la clienta, no un hecho que una decisión pida. Falla en silencio: sin él se sigue igual.
        if "crm" in self.herramientas and (estado.get("customer") or {}).get("history"):
            try:
                pres.gastar("herramienta")
                estado["herramientas"]["crm"] = self.herramientas["crm"](estado["customer"]["history"])
                pasos.append({"n": 0, "herramienta": "crm", "resultado": estado["herramientas"]["crm"]})
            except config.LimiteExcedido as e:
                tope = str(e)
            except Exception as e:
                pasos.append({"n": 0, "herramienta": "crm", "error": type(e).__name__})

        while not tope:
            try:
                pres.gastar("paso")
                pres.gastar("decision")
            except config.LimiteExcedido as e:
                tope = str(e)
                break

            ultima = self.decision.decide(estado, DECISIONES)
            accion = next((d for d in ultima if d.decision == "next_action"), None)
            paso = {"n": len(pasos) + 1, "decisiones": [asdict(d) for d in ultima], "herramienta": None}
            pasos.append(paso)
            nombre_accion = accion.choice if accion else "responder"
            tool = ACCION_TOOL.get(nombre_accion)
            if tool and tool not in self.herramientas:
                # Sin herramienta no hay hecho: se pregunta en vez de afirmar.
                paso["degradada"] = nombre_accion
                ultima = [d for d in ultima if d.decision != "next_action"]
                ultima.append(Decision("next_action", "preguntar", accion.confianza, "regla"))
                break
            if not tool:
                break

            arg = (accion.arg if accion and accion.arg else None) or (
                (estado.get("product") or {}).get("focus") if tool == "stock" else _consulta_rag(estado))
            if self._ya_consultado(estado, tool, arg):
                continue                                              # el dato ya está: que decida otra cosa
            try:
                pres.gastar("herramienta")
            except config.LimiteExcedido as e:
                tope = str(e)
                break
            try:
                resultado = self.herramientas[tool](arg)
            except Exception as e:           # sin el hecho no se afirma nada: una persona decide
                paso.update(herramienta=tool, argumento=arg, error=type(e).__name__)
                fallo_tool = tool
                break
            self._guardar(estado, tool, arg, resultado)
            paso.update(herramienta=tool, argumento=arg, resultado=resultado)
            # RECURSIÓN: la siguiente vuelta decide de nuevo con el hecho nuevo en el estado.

        # Tope (o herramienta rota) con una acción de herramienta pendiente: no se entrega un plan a medias.
        pendiente = next((d for d in ultima if d.decision == "next_action"), None)
        if (tope or fallo_tool) and pendiente and pendiente.choice in ACCION_TOOL:
            ultima = [d for d in ultima if d.decision != "next_action"]
            ultima.append(Decision("next_action", "pedir_asesora", pendiente.confianza, "regla"))
        plan, codigos_ok = self._plan(estado, ultima)
        errores = validar(plan, codigos_ok) if plan else ["sin plan"]
        return Resultado(plan=plan, errores=errores, pasos=pasos, tope=tope, motor=self.decision.nombre,
                         ms=int((time.perf_counter() - t0) * 1000), herramientas=estado["herramientas"],
                         fallo_herramienta=fallo_tool)

    @staticmethod
    def _ya_consultado(estado: dict, tool: str, arg) -> bool:
        herr = estado["herramientas"]
        if tool == "stock":
            return arg in (herr.get("stock") or {})
        return herr.get("rag") is not None

    @staticmethod
    def _guardar(estado: dict, tool: str, arg, resultado) -> None:
        if tool == "stock":
            estado["herramientas"].setdefault("stock", {})[arg] = resultado
        else:
            estado["herramientas"]["rag"] = list(resultado or [])

    @staticmethod
    def _plan(estado: dict, decs: list) -> tuple[Plan | None, set[str]]:
        accion_d = next((d for d in decs if d.decision == "next_action"), None)
        accion = accion_d.choice if accion_d else "responder"
        arg = accion_d.arg if accion_d else None
        foco = (estado.get("product") or {}).get("focus")
        stock = (estado.get("herramientas") or {}).get("stock") or {}
        codigos_ok = {c for c, v in stock.items() if v == "online"}
        producto = (arg or foco) if accion in ACCIONES_CON_PRODUCTO else None
        if accion == "recomendar" and not producto:
            return None, codigos_ok
        hechos = [f"{k}: {v}" for k, v in (estado.get("requirements") or {}).items() if v]
        crm = (estado.get("herramientas") or {}).get("crm") or {}
        if crm.get("pedidos"):
            hechos.append(f"clienta que vuelve: {crm['pedidos']} pedido(s) antes")
        if crm.get("tallas"):
            hechos.append(f"talla de su pedido anterior: {crm['tallas'][0]}")
        conv = estado.get("conversation") or {}
        pregunta = None
        if accion in ("recomendar", "preguntar") and conv.get("next_question"):
            pregunta = conv["next_question"]        # la elige el código (memoria.siguiente): método de venta del dueño
        elif accion == "recomendar" and "talla" in separar(estado)["desconocidos"]:
            pregunta = (estado.get("preguntas") or {}).get("talla") or PREGUNTA_TALLA
        return Plan(accion=accion, producto=producto, hechos=hechos, pregunta=pregunta,
                    razon=f"decisión {accion} · stock {stock.get(producto) if producto else '—'}"), codigos_ok
