"""AgentV2: la ruta V2 del turno.

Flujo por turno:

    V1 entiende el mensaje (clasifica, actualiza la ficha, elige etapa y prenda)
      → contexto estructurado (con lo que V1 acaba de entender) → motor recursivo (decide, usa herramientas, vuelve a
      decidir) → plan validado → redactor → quality gate → regeneración acotada → fallback de código

Dos modos (V2_MODO, o `modo` en la petición):

- **sombra** (por defecto): lo que se envía es siempre el texto de V1. El borrador de V2 y su control de calidad van en
  `res["v2"]`, para compararlos. El redactor es de código (sin modelo): no suma latencia al turno.
- **activo**: si V2 puede hablar, se envía su texto, con las fotos, la etapa y la ficha de V1. V2 NO cambia lo que se
  hace, solo cómo se dice: habla únicamente cuando su plan coincide con lo que V1 hizo (misma acción, misma prenda), la
  acción es una de V2_HABLA (por defecto solo `recomendar`) y el quality gate aprueba el borrador. En cualquier otro caso
  se envía el texto de V1, y la traza dice por qué.
  El análisis de V1 corre sin llamar al LLM de pago (`usar_llm=false`): si V2 habla, no se paga DeepSeek; si no habla,
  V1 vuelve a correr completo.

V2 nunca habla en los flujos que escribe el código (pedido, pago, cita, menú, aclaración de prenda: spec §19) ni en el
primer mensaje (saludo y presentación son de V1). Cualquier fallo de la capa nueva deja la respuesta de V1."""
from __future__ import annotations

import copy
import logging
import time
from typing import Callable

from . import config
from .accion import accion_v1, es_flujo_fijo, tarjetas_de_prenda
from .calidad import FALLBACK
from .contexto import ContextBuilder
from .estado import separar
from .generacion import Encadenada
from .motor import MotorRecursivo, Resultado
from .plantillas import SinPlantilla

log = logging.getLogger("agente.v2")


def _sin_llm(req):
    """La misma petición sin el LLM de pago: V1 entiende y decide con código, y arma su texto de respaldo."""
    try:
        return req.model_copy(update={"usar_llm": False})
    except AttributeError:
        r = copy.copy(req)
        r.usar_llm = False
        return r


class AgentV2:
    def __init__(self, v1: Callable, limites: config.Limites | None = None, contexto: ContextBuilder | None = None,
                 motor: MotorRecursivo | None = None, calidad=None, redactor=None, redactor_activo=None,
                 modo: str | None = None, habla: tuple[str, ...] | None = None):
        self.v1 = v1
        self.limites = limites or config.limites_desde_entorno()
        self.contexto = contexto or ContextBuilder()
        self.motor = motor
        self.calidad = calidad
        self.redactor = redactor                         # sombra: de código
        self.redactor_activo = redactor_activo or redactor   # activo: puede llevar un modelo local
        self.modo = modo
        self.habla = habla if habla is not None else config.habla_por_defecto()   # en qué acciones puede hablar V2

    # ------------------------------------------------------------------------------------------------------------
    def conversar(self, req) -> dict:
        t0 = time.perf_counter()
        modo = config.modo_pedido(getattr(req, "modo", ""), self.modo or config.modo_por_defecto())
        traza = {"agent_version": "v2", "fase": 7, "modo": modo, "fallback": False, "motivo": None, "enviado": "v1"}
        activo = modo == "activo" and self.motor is not None and self.contexto is not None
        veces_v1 = 0
        if activo:
            res = dict(self.v1(_sin_llm(req)) if getattr(req, "usar_llm", True) else self.v1(req))
            veces_v1 = 1
        else:
            res = dict(self.v1(req))
        res["version"] = "v2"
        sombra = self._analizar(req, res, traza, activo)
        if activo:
            motivo = sombra.get("no_habla") if isinstance(sombra, dict) else "sin análisis"
            gen = (sombra.get("generacion") or {}) if isinstance(sombra, dict) else {}
            if motivo is None and gen.get("passed") and gen.get("texto"):
                traza["v1_texto_respaldo"] = res.get("respuesta")     # lo que V1 habría dicho sin su LLM
                res["respuesta"] = gen["texto"]
                res["modelo_llm"] = f"v2:{gen.get('motor') or 'plantilla'}"
                traza["enviado"] = "v2"
            else:
                traza["motivo_v1"] = motivo or (f"sin plantilla segura ({gen['sin_plantilla']})" if gen.get("sin_plantilla") else
                                                 "el borrador no pasó el control de calidad" if gen else "sin borrador")
                if getattr(req, "usar_llm", True):      # V2 no habló: el turno lo contesta V1 completo, con su LLM
                    previo_ms = res.get("ms")
                    res = dict(self.v1(req))
                    res["version"] = "v2"
                    veces_v1 = 2
                    if previo_ms is not None and "ms" in res:
                        res["ms"] += previo_ms
        traza["llamadas_v1"] = veces_v1
        traza["sombra"] = sombra
        traza["ms"] = int((time.perf_counter() - t0) * 1000)
        res["v2"] = traza
        return res

    # ------------------------------------------------------------------------------------------------------------
    def _analizar(self, req, res: dict, traza: dict, activo: bool) -> dict:
        """Contexto → motor → borrador. Todo con lo que V1 acaba de entender. Nunca tira el turno."""
        try:
            ctx = self.contexto.construir(req, res)
            traza["separado"] = separar(ctx)
            traza["ctx_turnos"] = len(ctx["conversation"]["recent_turns"])
        except Exception as e:
            log.warning("v2: contexto falló (%s); el turno sigue por V1", type(e).__name__)
            traza["fallback"] = True
            traza["motivo"] = type(e).__name__
            return {"error": type(e).__name__, "no_habla": "el contexto falló"}
        if self.motor is None:
            return {"no_habla": "sin motor"}
        try:
            r: Resultado = self.motor.ejecutar(ctx)
        except Exception as e:
            log.warning("v2: motor falló (%s); sin plan en este turno", type(e).__name__)
            return {"error": type(e).__name__, "no_habla": "el motor falló"}
        obs = accion_v1(res)
        salida = r.a_dict() | {"v1_accion": obs, "v1_flujo_fijo": es_flujo_fijo(res), "v1_etapa": res.get("etapa")}
        ultima = getattr(getattr(self.motor, "decision", None), "ultima", None)
        if ultima:
            salida["jev"] = ultima
        salida["no_habla"] = self._motivo_no_habla(res, r, ctx)
        redactor = self.redactor_activo if activo else self.redactor
        # En sombra se redacta siempre (con el redactor de código) para medir el control de calidad; en activo, solo si
        # V2 va a hablar: un modelo local no se llama para un borrador que no se va a enviar.
        puede_hablar = (not activo) or salida["no_habla"] is None
        if r.plan is not None and not r.errores and redactor and self.calidad and puede_hablar:
            salida["generacion"] = self._redactar(r, res, ctx, redactor)
        return salida

    def _motivo_no_habla(self, res_v1: dict, r: Resultado, ctx: dict) -> str | None:
        """None = V2 puede hablar en este turno. Si no, la razón, en una frase."""
        if es_flujo_fijo(res_v1):
            return "flujo fijo de código"
        if r.plan is None or r.errores:
            return "plan no válido"
        obs = accion_v1(res_v1)
        if obs != r.plan.accion:
            return f"desacuerdo con V1 (V1 {obs}, V2 {r.plan.accion})"
        if r.plan.accion not in self.habla:
            if r.plan.accion == "preguntar":
                return "V2 coincide con V1 en preguntar: el texto es el de V1"
            return f"V2 no redacta «{r.plan.accion}»"
        if not ctx["conversation"]["turns_total"]:
            return "primer mensaje (saludo y presentación son de V1)"
        if r.plan.accion == "preguntar" and not r.plan.pregunta:
            return "sin pregunta del código"
        if r.plan.accion == "recomendar":
            sug = tarjetas_de_prenda(res_v1)
            if len(sug) != 1 or sug[0].get("codigo") != r.plan.producto:
                return "la foto que manda V1 no es la del plan"
        # Con plantillas semánticas: si el código no tiene una plantilla segura para este turno (un dato que falta, una pregunta sin
        # plantilla, una categoría sin stock), V2 no inventa: habla V1.
        elegir = getattr(self.redactor_activo, "elegir", None)
        if elegir is not None:
            try:
                elegir(r.plan.a_dict(), ctx)
            except SinPlantilla as e:
                return f"sin plantilla segura ({e})"
        return None

    def _redactar(self, r: Resultado, res_v1: dict, ctx: dict, redactor) -> dict:
        """Borrador desde el plan, validado. Regenera (acotado) y, si no pasa, cae a la plantilla de código."""
        intentos: list[dict] = []
        plan_d = r.plan.a_dict()
        if isinstance(redactor, Encadenada):
            redactor.intentos = 1 + self.limites.max_regeneraciones
            orden = list(redactor.intentos_en_orden(plan_d))
        else:
            orden = [(redactor, v) for v in range(1 + self.limites.max_regeneraciones)]
        for motor, variante in orden:
            t = time.perf_counter()
            nombre = getattr(motor, "nombre", "?")
            if getattr(motor, "nombre_realizador", None):
                nombre = f"{nombre}/{motor.nombre_realizador}"
            try:
                borrador = motor.redactar(plan_d, variante, ctx)
            except SinPlantilla as e:                       # el código no tiene una plantilla segura: no se inventa nada
                return {"texto": None, "passed": None, "fallback": False, "motor": None, "sin_plantilla": str(e),
                        "regeneraciones": 0, "intentos": _sin_texto(intentos), "v1_texto": res_v1.get("respuesta")}
            except Exception as e:
                intento = {"motor": nombre, "variante": variante, "error": type(e).__name__, "ms": int((time.perf_counter() - t) * 1000)}
                if getattr(e, "errores", None):
                    intento["errors"] = e.errores            # lo que rechazó la compuerta factual
                if getattr(motor, "traza", None):
                    intento["traza"] = motor.traza
                intentos.append(intento)
                continue
            q = self.calidad.evaluar(borrador, r.plan, ctx)
            intento = {"motor": nombre, "variante": variante, "passed": q["passed"], "score": q["score"], "errors": q["errors"],
                       "texto": borrador, "ms": int((time.perf_counter() - t) * 1000)}
            if getattr(motor, "traza", None):
                intento["traza"] = motor.traza
            intentos.append(intento)
            if q["passed"]:
                out = {"texto": borrador, "passed": True, "fallback": False, "motor": nombre,
                       "regeneraciones": len(intentos) - 1, "intentos": _sin_texto(intentos), "v1_texto": res_v1.get("respuesta")}
                if intento.get("traza"):
                    out["plantillas"] = intento["traza"].get("plantillas")
                return out
        return {"texto": FALLBACK, "passed": False, "fallback": True, "motor": None,
                "regeneraciones": max(0, len(intentos) - 1), "intentos": _sin_texto(intentos),
                "v1_texto": res_v1.get("respuesta")}


def _sin_texto(intentos: list[dict]) -> list[dict]:
    """La traza guarda cada intento sin su texto (el texto de V1 ya va en la respuesta)."""
    return [{k: v for k, v in i.items() if k != "texto"} for i in intentos]
