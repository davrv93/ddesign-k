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
from . import temas as T
from .accion import accion_v1, es_flujo_fijo, tarjetas_de_prenda
from .decision import fuera_de_giro
from .calidad import FALLBACK
from .contexto import ContextBuilder
from .estado import separar
from .generacion import Encadenada
from .motor import MotorRecursivo, Resultado, duda_rag
from .plan import Plan, validar
from .plantillas import SinPlantilla
from .rag import tomar_detalle

log = logging.getLogger("agente.v2")


def _etapa(traza: dict, nombre: str, t_ini: float, estado: str = "ok", detalle=None) -> None:
    """Una etapa de la traza con sus ms y los valores que decidió. Con
    V2_TRAZA=0 solo quedan nombre, ms y estado."""
    e: dict = {"etapa": nombre, "ms": int((time.perf_counter() - t_ini) * 1000), "estado": estado}
    if detalle is not None and config.traza_nivel() != "0":
        e["detalle"] = detalle
    traza.setdefault("etapas", []).append(e)


def _degrada(traza: dict, texto: str) -> None:
    """Cada degradación en texto (qué respaldo entró y por qué)."""
    traza.setdefault("degradaciones", []).append(texto)


def _sin_llm(req):
    """La misma petición sin el LLM de pago: V1 entiende y decide con código, y arma su texto de respaldo.
    SIEMPRE una copia: con pydantic 1 (el de la imagen) no hay `model_copy` y `copy.copy` comparte el estado del modelo, así que
    poner `usar_llm=False` en la «copia» lo ponía también en la petición original y V2 activo nunca llamaba al LLM (06-10: 393 de
    613 turnos del 100 salieron del texto de respaldo)."""
    for metodo in ("model_copy", "copy"):                 # pydantic 2 | pydantic 1
        f = getattr(req, metodo, None)
        if f is not None:
            try:
                return f(update={"usar_llm": False})
            except TypeError:
                continue
    r = copy.copy(req)                                    # objetos simples (las pruebas)
    try:
        r.__dict__ = dict(r.__dict__)
    except (AttributeError, TypeError):
        pass
    r.usar_llm = False
    return r


class AgentV2:
    def __init__(self, v1: Callable, limites: config.Limites | None = None, contexto: ContextBuilder | None = None,
                 motor: MotorRecursivo | None = None, calidad=None, redactor=None, redactor_activo=None,
                 modo: str | None = None, habla: tuple[str, ...] | None = None, semantica=None,
                 candidatos_ref: Callable | None = None, texto_derivacion: str = ""):
        self.v1 = v1
        self.texto_derivacion = texto_derivacion         # el de V1 para la acción «asesora»; vacío = V2 no deriva por su cuenta
        self.limites = limites or config.limites_desde_entorno()
        self.contexto = contexto or ContextBuilder()
        self.motor = motor
        self.calidad = calidad
        self.redactor = redactor                         # sombra: de código
        self.redactor_activo = redactor_activo or redactor   # activo: puede llevar un modelo local
        self.modo = modo
        self.habla = habla if habla is not None else config.habla_por_defecto()   # en qué acciones puede hablar V2
        self.semantica = semantica                       # catálogos semánticos (v2/semantica.py); None = sin ellos
        self.candidatos_ref = candidatos_ref             # códigos mostrados → [{codigo, nombre, color}] para «el otro», «el segundo»

    # ------------------------------------------------------------------------------------------------------------
    def conversar(self, req) -> dict:
        t0 = time.perf_counter()
        modo = config.modo_pedido(getattr(req, "modo", ""), self.modo or config.modo_por_defecto())
        traza = {"agent_version": "v2", "fase": 7, "modo": modo, "fallback": False, "motivo": None,
                 "enviado": "v1", "etapas": [], "degradaciones": []}
        activo = modo == "activo" and self.motor is not None and self.contexto is not None
        veces_v1 = 0
        t_v1 = time.perf_counter()
        if activo:
            res = dict(self.v1(_sin_llm(req)) if getattr(req, "usar_llm", True) else self.v1(req))
            veces_v1 = 1
        else:
            res = dict(self.v1(req))
        res["version"] = "v2"
        _etapa(traza, "v1", t_v1, detalle={"etapa": res.get("etapa"), "llamadas": veces_v1 or 1})
        t_cat = time.perf_counter()
        self._leer_catalogos(req, res, traza)
        _etapa(traza, "catalogos", t_cat,
               detalle={k: (traza.get("catalogos") or {}).get(k) for k in ("catalogo", "intent", "score", "margen")})
        t_ana = time.perf_counter()
        sombra = self._analizar(req, res, traza, activo)
        _etapa(traza, "analizar", t_ana, estado="error" if isinstance(sombra, dict) and sombra.get("error") else "ok")
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
        self._derivar_fuera_de_giro(req, res, traza, modo == "activo")
        t_temas = time.perf_counter()
        self._temas(req, res, traza, activo)       # cambios de tema: la pila de pendientes y, si está encendido, la retoma
        _etapa(traza, "temas", t_temas,
               estado="error" if isinstance(traza.get("temas"), dict) and traza["temas"].get("error") else "ok")
        traza["llamadas_v1"] = veces_v1
        traza["sombra"] = sombra
        traza["ms"] = int((time.perf_counter() - t0) * 1000)
        res["v2"] = traza
        return res

    # ------------------------------------------------------------------------------------------------------------
    def _leer_catalogos(self, req, res: dict, traza: dict) -> None:
        """Una lectura semántica del mensaje (v2/semantica.py). Nunca tira el turno."""
        sem = self.semantica
        if sem is None or not sem.activa:
            return
        try:
            cand = None
            if self.candidatos_ref:
                mem_req = req.memoria if isinstance(getattr(req, "memoria", None), dict) else {}
                cand = self.candidatos_ref(list(mem_req.get("mostrados") or []))
            lec = sem.leer(req.mensaje or "", cand)
            if lec is not None:
                lec["modo"] = sem.modo
                traza["catalogos"] = lec
        except Exception as e:
            log.warning("v2: catálogos fallaron (%s); el turno sigue igual", type(e).__name__)

    def _analizar(self, req, res: dict, traza: dict, activo: bool) -> dict:
        """Contexto → motor → borrador. Todo con lo que V1 acaba de entender. Nunca tira el turno."""
        t_ctx = time.perf_counter()
        try:
            ctx = self.contexto.construir(req, res)
            if traza.get("catalogos"):
                ctx["conversation"]["catalog"] = traza["catalogos"]       # solo informa; las reglas de decisión todavía no lo leen
            traza["separado"] = separar(ctx)
            traza["ctx_turnos"] = len(ctx["conversation"]["recent_turns"])
            _etapa(traza, "contexto", t_ctx, detalle={"turnos": traza["ctx_turnos"]})
        except Exception as e:
            log.warning("v2: contexto falló (%s); el turno sigue por V1", type(e).__name__)
            traza["fallback"] = True
            traza["motivo"] = type(e).__name__
            _etapa(traza, "contexto", t_ctx, estado="error")
            _degrada(traza, f"contexto: {type(e).__name__}; el turno sigue por V1")
            return {"error": type(e).__name__, "no_habla": "el contexto falló"}
        if self.motor is None:
            _degrada(traza, "sin motor; el turno sigue por V1")
            return {"no_habla": "sin motor"}
        t_motor = time.perf_counter()
        try:
            r: Resultado = self.motor.ejecutar(ctx)
        except Exception as e:
            log.warning("v2: motor falló (%s); sin plan en este turno", type(e).__name__)
            _etapa(traza, "motor", t_motor, estado="error")
            _degrada(traza, f"motor: {type(e).__name__}; sin plan en este turno")
            return {"error": type(e).__name__, "no_habla": "el motor falló"}
        _etapa(traza, "motor", t_motor, estado="error" if r.tope or r.fallo_herramienta else "ok",
               detalle={"plan": bool(r.plan), "errores": r.errores, "tope": r.tope,
                        "fallo_herramienta": r.fallo_herramienta})
        if r.tope:
            _degrada(traza, f"tope {r.tope}; plan a pedir_asesora")
        if r.fallo_herramienta:
            _degrada(traza, f"herramienta {r.fallo_herramienta} falló; sin el hecho no se afirma nada")
        rag = tomar_detalle()
        if rag and config.traza_nivel() != "0":
            traza["rag"] = rag
        if rag and rag.get("fuente") != "hibrida":
            _degrada(traza, f"rag: {rag.get('motivo')}; orden del vector")
        elif rag and (rag.get("rerank") or {}).get("motivo") not in (None, "ok", "apagado"):
            _degrada(traza, f"reranker: {(rag.get('rerank') or {}).get('motivo')}; orden de la fusión")
        obs = accion_v1(res)
        salida = r.a_dict() | {"v1_accion": obs, "v1_flujo_fijo": es_flujo_fijo(res), "v1_etapa": res.get("etapa")}
        ultima = getattr(getattr(self.motor, "decision", None), "ultima", None)
        if ultima:
            salida["jev"] = ultima
        salida["no_habla"] = self._motivo_no_habla(res, r, ctx)
        duda = duda_rag(r.plan, r.errores, ctx, rag)
        if duda is not None:
            nuevo, nota = duda
            stock = (ctx.get("herramientas") or {}).get("stock") or {}
            errs = validar(nuevo, {c for c, v in stock.items() if v == "online"})
            if not errs:
                r.plan = nuevo
                salida["plan"] = nuevo.a_dict()
                salida["errores"] = []
                salida["no_habla"] = self._motivo_no_habla(res, r, ctx)
                salida["duda_rag"] = {"motivo": (ultimo_detalle().get("rerank") or {}).get("motivo"),
                                      "decision": nota}
        redactor = self.redactor_activo if activo else self.redactor
        # En sombra se redacta siempre (con el redactor de código) para medir el control de calidad; en activo, solo si
        # V2 va a hablar: un modelo local no se llama para un borrador que no se va a enviar.
        puede_hablar = (not activo) or salida["no_habla"] is None
        if r.plan is not None and not r.errores and redactor and self.calidad and puede_hablar:
            t_red = time.perf_counter()
            salida["generacion"] = self._redactar(r, res, ctx, redactor)
            gen = salida["generacion"] or {}
            _etapa(traza, "redaccion", t_red, estado="ok" if gen.get("passed", True) else "error",
                   detalle={"passed": gen.get("passed"), "motor": gen.get("motor")})
            if gen.get("passed") is False:
                _degrada(traza, f"redacción: {gen.get('sin_plantilla') or 'no pasó el control'}; habla V1")
        return salida

    # ------------------------------------------------------------------------------------------------------------
    def _temas(self, req, res: dict, traza: dict, activo: bool) -> None:
        """Cambios de tema («suspender, no cancelar», v2/temas.py). Nunca tira el turno: si algo falla, la respuesta es la de siempre."""
        if not config.temas_activos():
            return
        try:
            traza["temas"] = self._temas_turno(req, res, traza, activo)
        except Exception as e:
            log.warning("v2: los cambios de tema fallaron (%s); el turno sigue igual", type(e).__name__)
            traza["temas"] = {"error": type(e).__name__}

    def _temas_turno(self, req, res: dict, traza: dict, activo: bool) -> dict:
        mem = res.get("memoria")
        if not isinstance(mem, dict) or not isinstance(mem.get("sabemos"), dict):
            return {"omitido": "sin ficha de memoria"}
        mem_req = req.memoria if isinstance(getattr(req, "memoria", None), dict) else {}
        guardado = mem_req.get("v2") if isinstance(mem_req.get("v2"), dict) else {}
        previo = T.normalizar_estado(guardado.get("temas"))
        rapida = T.interpretar_rapida(req.mensaje or "", getattr(req, "payload", None), previo["ofrecidas"])
        lect = res.get("lectura") if isinstance(res.get("lectura"), dict) else {}
        sab_antes = mem_req.get("sabemos") if isinstance(mem_req.get("sabemos"), dict) else {}
        turno = T.Turno(
            mensaje=req.mensaje or "",
            pend_antes=lect.get("pendiente") or mem_req.get("pendiente") or T.pendiente_inferida(previo),
            respondio=bool(lect.get("respondio")), espera=bool(lect.get("espera")), datos=lect.get("datos") or {},
            sabemos_antes=sab_antes, sabemos=mem["sabemos"], intent=(res.get("comercial") or {}).get("intent") or "",
            etapa=res.get("etapa") or "", respuesta=res.get("respuesta") or "", flujo_fijo=_flujo_fijo_de_temas(req, res),
            estado_go=getattr(req, "estado", "") or "", primer_mensaje=not (req.historial or []),
            hay_prenda=bool(mem.get("producto") or mem.get("mostrados")), rapida=rapida)
        cat = traza.get("catalogos") or {}
        if cat.get("tema"):
            if cat.get("modo") == "activo":
                turno.tema_catalogo = cat["tema"]
            else:         # sombra: solo se mide si el catálogo habría cambiado la lectura
                sin = T.detectar(turno)
                turno.tema_catalogo = cat["tema"]
                con = T.detectar(turno)
                turno.tema_catalogo = None
                cat["habria_cambiado"] = (sin["tipo"], sin["tema"]) != (con["tipo"], con["tema"])
        dec = T.evaluar(previo, turno)
        retoma = dec["retoma"]
        # Solo habla V2 activo y con «responder_y_retomar» en V2_HABLA. En cualquier otro caso la pila avanza como si hubiera salido
        # (en sombra se mide lo que V2 haría), pero la clienta recibe el texto de V1 y la ficha de V1 no se toca.
        puede = activo and T.ACCION in self.habla
        out: dict = {"evento": {k: dec["evento"].get(k) for k in ("nivel", "tipo", "tema", "ayuda", "cambio", "causa")},
                     "retoma": None, "bloqueo": dec["bloqueo"], "v1_retomo": dec["v1_retomo"], "invalida": dec["invalida"],
                     "habla": puede, "enviada": False, "pendientes_antes": T.resumen(previo)}
        if rapida:
            out["respuesta_rapida"] = rapida
        texto_retoma = None
        redactor = self.redactor_activo if activo else self.redactor
        if retoma and self.calidad is not None and redactor is not None:
            ctx = self.contexto.construir(req, res)
            foco = mem.get("producto") or None
            plan = Plan(accion=T.ACCION, producto=foco, razon=f"retomar {retoma['slot']} ({retoma['modo']})", retoma=retoma,
                        hechos=[f"{k}: {v}" for k, v in (mem["sabemos"] or {}).items() if v],
                        pregunta={"tipo": retoma["slot"], "texto": T.pregunta_canonica(retoma["slot"])})
            gen = self._redactar(Resultado(plan=plan, errores=[]), res, ctx, redactor)
            ok = bool(gen.get("passed")) and not gen.get("fallback") and bool(gen.get("texto"))
            out["retoma"] = {"slot": retoma["slot"], "topic": retoma["topic"], "modo": retoma["modo"], "prioridad": retoma["prioridad"],
                             "intento": retoma["intento"], "pasa_la_compuerta": ok, "motor": gen.get("motor"),
                             "plantillas": gen.get("plantillas"), "sin_plantilla": gen.get("sin_plantilla"),
                             "errores": sorted({e for i in gen.get("intentos") or [] for e in i.get("errors") or []})}
            if ok:
                texto_retoma = gen["texto"]
                out["retoma"]["texto"] = texto_retoma
                out["plan"] = {"action": "ANSWER_AND_RESUME", "answer_intent": retoma.get("answer_intent"),
                               "resume": {"topic": retoma["topic"], "slot": retoma["slot"]}}
                out["respuestas_rapidas"] = T.rapidas_de(retoma["slot"])
        # Estado: lo que sale (o se simula en sombra) cuenta como pregunta hecha; si la retoma no pasó la compuerta, no cuenta.
        if puede and texto_retoma:
            previo = res.get("respuesta") or ""
            res["respuesta"] = previo.rstrip() + "\n\n" + texto_retoma
            T.registrar_en_ficha(mem, res["respuesta"], previo)         # la memoria de V1 reconoce qué se espera (memoria.clave_de)
            res["respuestas_rapidas"] = out["respuestas_rapidas"]
            out["enviada"] = True
            traza["enviado"] = "v2"
            traza["retoma_enviada"] = True
        if puede:
            if dec["invalida"]:
                out["invalidados_en_ficha"] = T.aplicar_invalidacion(mem, dec["evento"].get("cambio") or "", turno.datos)
            if rapida and rapida["intent"] == "provide_size" and T.aplicar_talla(mem, rapida["size"]):
                out["talla_de_la_respuesta_rapida"] = rapida["size"]
            if dec["evento"].get("ayuda") == "dudosa" and texto_retoma and T.olvidar_talla_adivinada(mem):
                out["talla_adivinada_borrada"] = True
        nuevo = dec["estado_enviado"] if texto_retoma else dec["estado"]
        out["simulada"] = bool(texto_retoma) and not puede
        mem["v2"] = {"temas": nuevo}
        out["pendientes"] = T.resumen(nuevo)
        return out

    def _derivar_fuera_de_giro(self, req, res: dict, traza: dict, activo: bool) -> None:
        """Pregunta ajena al negocio (cripto, empleo, bolsa…): la clienta no recibe «¿qué modelo te gustaría?» sino el paso a la
        dueña, con la misma acción «asesora» de V1 (el bot Go pausa y avisa; sirve igual en la web y en WhatsApp). En sombra solo
        se anota. Lo que V1 ya derivó no se toca."""
        if not fuera_de_giro(getattr(req, "mensaje", "") or ""):
            return
        traza["fuera_de_giro"] = {"deriva": False}
        if res.get("accion") == "asesora":
            traza["fuera_de_giro"]["motivo"] = "V1 ya derivó"
            return
        if not (activo and config.deriva_activa() and self.texto_derivacion):
            traza["fuera_de_giro"]["motivo"] = "sombra" if not activo else "apagada"
            return
        traza["v1_texto_respaldo"] = res.get("respuesta")
        res.update(accion="asesora", respuesta=self.texto_derivacion, codigo=None, confirmar_pedido=False, ofrecer_opciones=False,
                   sugerencias=[], botones=[], tallas=[], modelo_llm="v2:deriva")
        traza["enviado"] = "v2"
        traza["fuera_de_giro"] = {"deriva": True, "motivo": "pregunta fuera del giro"}

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


def _flujo_fijo_de_temas(req, res: dict) -> bool:
    """¿Lo resolvió un flujo de código? Igual que `es_flujo_fijo`, salvo los botones de talla: son solo del chat web (el bot de WhatsApp
    no los pinta), así que en WhatsApp no son una pregunta hecha. En la web sí lo son: ahí la talla ya la pregunta V1."""
    if getattr(req, "canal", "") != "web" and res.get("tallas"):
        return es_flujo_fijo({**res, "tallas": []})
    return es_flujo_fijo(res)


def _sin_texto(intentos: list[dict]) -> list[dict]:
    """La traza guarda cada intento sin su texto (el texto de V1 ya va en la respuesta)."""
    return [{k: v for k, v in i.items() if k != "texto"} for i in intentos]
