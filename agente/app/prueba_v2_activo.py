"""Pruebas de la V2 activa: contexto con lo que V1 entendió, lectura de lo que V1 hizo, Jev local como motor de
decisión, herramienta CRM, generación con modelo local, control de calidad ampliado y modo activo. Sin red, sin modelos:

    python3 -m app.prueba_v2_activo
"""
from __future__ import annotations

import sys
from types import SimpleNamespace as NS

from .v2 import config as C
from .v2.accion import accion_v1, es_flujo_fijo
from .v2.agente import AgentV2
from .v2.calidad import FALLBACK, PlantillaGeneracion, ReglasCalidad, componer
from .v2.contexto import ContextBuilder
from .v2.decision import ReglasDecision, JevSystemOneDecision
from .v2.generacion import Encadenada, LlmLocalGeneracion
from .v2.metricas import Registro
from .v2.motor import MotorRecursivo
from .v2.plan import Plan

fallos: list[str] = []
total = 0


def caso(nombre: str, obtenido, esperado) -> None:
    global total
    total += 1
    if obtenido != esperado:
        fallos.append(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


def pedido(**kw):
    base = dict(mensaje="hola", historial=[], etapa="", memoria=None, producto="", talla="", desde_anuncio=False,
                perfil=None, usar_llm=True, modo="")
    base.update(kw)
    return NS(**base)


def turnos(n):
    return [NS(rol="cliente" if i % 2 == 0 else "bot", texto=f"t{i}") for i in range(n)]


NOMBRES = {"V35": "Vestido Irla", "V42": "Vestido Gala Capa Azul", "V21": "Vestido Kendall"}
FICHAS = {"V35": {"nombre": "Vestido Irla", "categoria": "vestido", "color": "negro", "detalle": "corte A con escote en V",
                  "tejido": "crepé"},
          "V42": {"nombre": "Vestido Gala Capa Azul", "categoria": "vestido", "color": "azul", "detalle": "con capa", "tejido": ""}}
PREG = {"fecha": "¿Para cuándo es el matrimonio?", "talla": "¿Qué talla usas normalmente?"}


def texto_pregunta(tipo, mem, mensaje, respuesta=""):
    if tipo == "fecha" and "¿Y para cuándo" in respuesta:
        return "¿Y para cuándo es?"
    return PREG.get(tipo, "")


# --- modos de la V2 --------------------------------------------------------------------------------------------------
caso("sin V2_MODO → sombra", C.modo_por_defecto({}), "sombra")
caso("V2_MODO=ACTIVO → activo", C.modo_por_defecto({"V2_MODO": " ACTIVO "}), "activo")
caso("V2_MODO inválido → sombra", C.modo_por_defecto({"V2_MODO": "loco"}), "sombra")
caso("la petición manda sobre el entorno", C.modo_pedido("activo", "sombra"), "activo")
caso("petición vacía → entorno", C.modo_pedido("", "activo"), "activo")
caso("petición inválida → entorno", C.modo_pedido("x", "sombra"), "sombra")

# --- contexto con lo que V1 acaba de entender ------------------------------------------------------------------------
req = pedido(mensaje="busco algo para una boda", historial=turnos(3), memoria={"mostrados": ["V21"], "sabemos": {}},
             perfil={"pedidos": 2, "tallas": ["M"], "productos": ["V35"]})
res = {"etapa": "seguimiento", "comercial": {"intent": "consulta_producto"}, "siguiente_pregunta": "fecha",
       "memoria": {"producto": "V35", "mostrados": ["V21", "V35"], "pendiente": "fecha",
                   "sabemos": {"ocasion": "matrimonio", "nombre": "Ana"}, "temperatura": "tibia"}}
cb = ContextBuilder(texto_pregunta=texto_pregunta)
c1 = cb.construir(req)
caso("sin V1: la ficha es la que llegó", c1["requirements"]["ocasion"], None)
c2 = cb.construir(req, res)
caso("con V1: la ficha es la actualizada", c2["requirements"]["ocasion"], "matrimonio")
caso("con V1: la prenda en foco es la que V1 puso", c2["product"]["focus"], "V35")
caso("con V1: «shown» es lo mostrado ANTES del turno", c2["product"]["shown"], ["V21"])
caso("con V1: la etapa es la nueva", c2["conversation"]["stage"], "seguimiento")
caso("con V1: la intención es la del clasificador de V1", c2["conversation"]["intent"], "consulta_producto")
caso("con V1: la pregunta del código lleva su texto", c2["conversation"]["next_question"],
     {"tipo": "fecha", "texto": "¿Para cuándo es el matrimonio?"})
caso("el historial de pedidos viaja", c2["customer"]["history"]["pedidos"], 2)
caso("la pregunta de talla viaja aparte", c2["preguntas"]["talla"]["texto"], "¿Qué talla usas normalmente?")
caso("sin perfil: sin historial", cb.construir(pedido(), res)["customer"]["history"], None)
res_q = {**res, "respuesta": "¡Qué bonito!\n\n¿Y para cuándo es?"}
caso("la pregunta usa la redacción exacta que V1 ya eligió", cb.construir(req, res_q)["conversation"]["next_question"]["texto"], "¿Y para cuándo es?")


def rompe(tipo, mem, mensaje, respuesta=""):
    raise KeyError("ficha incompleta")


caso("una ficha incompleta no tira el contexto", ContextBuilder(texto_pregunta=rompe).construir(req, res)["conversation"]["next_question"], None)

# --- lo que V1 hizo, en el vocabulario del plan -----------------------------------------------------------------------
caso("V1: pedido → confirmar_pedido", accion_v1({"accion": "pedido"}), "confirmar_pedido")
caso("V1: asesora → pedir_asesora", accion_v1({"accion": "asesora"}), "pedir_asesora")
caso("V1: con sugerencias → recomendar", accion_v1({"accion": "responder", "sugerencias": [{"codigo": "V35"}]}), "recomendar")
caso("V1: acción codigo → recomendar", accion_v1({"accion": "codigo"}), "recomendar")
caso("V1: texto con pregunta → preguntar", accion_v1({"accion": "responder", "respuesta": "¿Para cuándo es?"}), "preguntar")
caso("V1: texto sin pregunta → responder", accion_v1({"accion": "responder", "respuesta": "Es de crepé."}), "responder")
caso("flujo fijo: flujo_pedido", es_flujo_fijo({"accion": "responder", "modelo_llm": "flujo_pedido"}), True)
caso("flujo fijo: pide_cual", es_flujo_fijo({"accion": "responder", "modelo_llm": "pide_cual"}), True)
caso("flujo fijo: menú", es_flujo_fijo({"accion": "responder", "modelo_llm": "menu"}), True)
caso("flujo fijo: acción de pedido", es_flujo_fijo({"accion": "pedido", "modelo_llm": ""}), True)
caso("flujo fijo: botones", es_flujo_fijo({"accion": "responder", "modelo_llm": "", "botones": ["Lima"]}), True)
caso("flujo fijo: venta confirmada", es_flujo_fijo({"accion": "responder", "modelo_llm": "", "etapa": "venta_confirmada"}), True)
caso("texto libre del LLM no es flujo fijo", es_flujo_fijo({"accion": "responder", "modelo_llm": "deepseek", "etapa": "seguimiento"}), False)
caso("respaldo de código sí es texto libre", es_flujo_fijo({"accion": "responder", "modelo_llm": "respaldo_codigo"}), False)

# --- reglas de decisión con intención y pregunta del código -----------------------------------------------------------


def estado(foco=None, intent=None, pregunta=None, shown=(), stock=None, rag=None):
    herr = {}
    if stock is not None:
        herr["stock"] = stock
    if rag is not None:
        herr["rag"] = rag
    return {"conversation": {"intent": intent, "next_question": pregunta, "stage": "prospeccion", "recent_turns": [],
                             "last_user_message": "x", "turns_total": 2},
            "product": {"focus": foco, "shown": list(shown)}, "requirements": {"ocasion": "matrimonio"},
            "customer": {}, "herramientas": herr}


R = ReglasDecision()
q = {"tipo": "fecha", "texto": "¿Para cuándo es?"}
caso("reglas: pide el precio → responder", R.decide(estado("V35", "consulta_precio"), ["next_action"])[0].choice, "responder")
caso("reglas: se despide → responder", R.decide(estado("V35", "despedida", q), ["next_action"])[0].choice, "responder")
caso("reglas: quiere comprar → responder (flujo de código)", R.decide(estado("V35", "intencion_compra"), ["next_action"])[0].choice, "responder")
caso("reglas: sin prenda y con pregunta → preguntar", R.decide(estado(None, "saludo", q), ["next_action"])[0].choice, "preguntar")
caso("reglas: sin prenda ni pregunta → responder", R.decide(estado(None, "saludo"), ["next_action"])[0].choice, "responder")
d = R.decide(estado("V35", "consulta_producto"), ["next_action"])[0]
caso("reglas: prenda nueva sin stock conocido → consultar stock", (d.choice, d.arg), ("consultar_stock", "V35"))
caso("reglas: prenda nueva con stock → recomendar", R.decide(estado("V35", "consulta_producto", stock={"V35": "online"}), ["next_action"])[0].choice, "recomendar")
caso("reglas: prenda ya mostrada → no se vuelve a recomendar",
     R.decide(estado("V35", "interesado", q, shown=["V35"], stock={"V35": "online"}), ["next_action"])[0].choice, "preguntar")
caso("reglas: prenda ya mostrada y sin pregunta → responder",
     R.decide(estado("V35", "interesado", None, shown=["V35"], stock={"V35": "online"}), ["next_action"])[0].choice, "responder")
caso("reglas: solo en tienda → responder", R.decide(estado("V35", "otro", stock={"V35": "sucursal"}), ["next_action"])[0].choice, "responder")

# --- Jev local (System One): propone, el código decide ----------------------------------------------------------------
vistos: list[dict] = []


def jev_que_dice(opcion, p=0.9, otros=None):
    def cliente(url, cuerpo, timeout):
        vistos.append(cuerpo)
        probs = {"preguntar": 0.02, "recomendar": 0.02, "responder": 0.02, "pedir_asesora": 0.02, **(otros or {}), opcion: p}
        return {"answers": {"next_action": {"type": "choice", "choice": opcion, "confidence": p, "probabilities": probs}}}
    return cliente


j = JevSystemOneDecision("http://jev:8765", cliente=jev_que_dice("recomendar"))
d = j.decide(estado("V35", "consulta_producto"), ["intent", "next_action"])
caso("jev: propone recomendar pero falta el stock → el código consulta el stock",
     [(x.decision, x.choice, x.arg) for x in d if x.decision == "next_action"], [("next_action", "consultar_stock", "V35")])
caso("jev: se llama a /v1/systemone", vistos[0]["questions"]["next_action"]["type"], "choice")
caso("jev: el estado va en español y sin el nombre de la clienta", "name" not in str(vistos[0]["state"]) and "nombre" not in vistos[0]["state"], True)
caso("jev: las opciones son las cuatro de negocio", sorted(vistos[0]["questions"]["next_action"]["criteria"]),
     ["pedir_asesora", "preguntar", "recomendar", "responder"])
m = MotorRecursivo(JevSystemOneDecision("u", cliente=jev_que_dice("recomendar")), {"stock": lambda c: "online"})
vistos.clear()
r = m.ejecutar(estado("V35", "consulta_producto"))
caso("jev en el motor: recomienda tras verificar stock", (r.plan.accion, r.plan.producto), ("recomendar", "V35"))
caso("jev en el motor: Jev se consulta una sola vez en el ciclo", len(vistos), 1)
caso("jev en el motor: la traza guarda lo que dijo Jev", m.decision.ultima["opcion"], "recomendar")
r = MotorRecursivo(JevSystemOneDecision("u", cliente=jev_que_dice("recomendar")), {"stock": lambda c: "online"}).ejecutar(estado(None, "saludo", q))
caso("jev: propone recomendar sin prenda → el código pregunta", r.plan.accion, "preguntar")
r = MotorRecursivo(JevSystemOneDecision("u", cliente=jev_que_dice("recomendar")), {"stock": lambda c: "online"}).ejecutar(estado("V35", "interesado", q, shown=["V35"]))
caso("jev: propone recomendar una prenda ya vista → el código no la repite", r.plan.accion, "preguntar")
r = MotorRecursivo(JevSystemOneDecision("u", cliente=jev_que_dice("responder")), {}).ejecutar(estado("V35", "consulta_producto"))
caso("jev: propone responder", r.plan.accion, "responder")
r = MotorRecursivo(JevSystemOneDecision("u", cliente=jev_que_dice("pedir_asesora", 0.8)), {}).ejecutar(estado("V35", "otro"))
caso("jev: propone pedir asesora", r.plan.accion, "pedir_asesora")
dd = JevSystemOneDecision("u", cliente=jev_que_dice("recomendar", 0.3)).decide(estado(None, "saludo", q), ["next_action"])[0]
caso("jev: duda (p < umbral) → reglas, marcado como respaldo", (dd.choice, dd.fuente), ("preguntar", "respaldo"))
for nombre, cli in [("jev: opción fuera de lista se rechaza", lambda u, c, t: {"answers": {"next_action": {"choice": "hacer_descuento", "probabilities": {"hacer_descuento": 1}}}}),
                    ("jev: probabilidad fuera de 0–1 se rechaza", lambda u, c, t: {"answers": {"next_action": {"choice": "responder", "probabilities": {"responder": 7}}}}),
                    ("jev: respuesta sin answers se rechaza", lambda u, c, t: {})]:
    try:
        JevSystemOneDecision("u", cliente=cli).decide(estado("V35"), ["next_action"])
        caso(nombre, False, True)
    except ValueError:
        caso(nombre, True, True)


def servidor_caido(url, cuerpo, timeout):
    raise TimeoutError("jev no contesta")


out = AgentV2(v1=lambda r: {"respuesta": "Hola", "memoria": {"producto": "V35", "sabemos": {}}},
              motor=MotorRecursivo(JevSystemOneDecision("u", cliente=servidor_caido))).conversar(pedido())
caso("jev caído: el turno sigue por V1", out["respuesta"], "Hola")
caso("jev caído: queda anotado", out["v2"]["sombra"]["error"], "TimeoutError")

# --- herramienta CRM --------------------------------------------------------------------------------------------------
visto_crm: list = []


def tool_crm(perfil):
    visto_crm.append(perfil)
    return {"pedidos": perfil["pedidos"], "tallas": perfil["tallas"], "productos": perfil["productos"]}


ctx_crm = estado("V35", "consulta_producto", shown=[])
ctx_crm["customer"] = {"history": {"pedidos": 2, "tallas": ["M"], "productos": ["V35"]}}
ctx_crm["preguntas"] = {"talla": {"tipo": "talla", "texto": "¿Usas talla *M*, como en tu pedido anterior, o prefieres otra talla?"}}
r = MotorRecursivo(ReglasDecision(), {"stock": lambda c: "online", "crm": tool_crm}).ejecutar(ctx_crm)
caso("crm: se lee una vez, con el historial", visto_crm, [{"pedidos": 2, "tallas": ["M"], "productos": ["V35"]}])
caso("crm: queda en las herramientas del estado", r.herramientas["crm"]["pedidos"], 2)
caso("crm: los hechos del plan dicen que vuelve", "clienta que vuelve: 2 pedido(s) antes" in r.plan.hechos, True)
caso("crm: y su talla anterior", "talla de su pedido anterior: M" in r.plan.hechos, True)
caso("crm: la pregunta de talla usa la redacción con su talla", "talla *M*" in r.plan.pregunta["texto"], True)
visto_crm.clear()
r = MotorRecursivo(ReglasDecision(), {"stock": lambda c: "online", "crm": tool_crm}).ejecutar(estado("V35", "consulta_producto"))
caso("crm: sin historial no se llama", visto_crm, [])


def crm_roto(perfil):
    raise RuntimeError("crm caído")


r = MotorRecursivo(ReglasDecision(), {"stock": lambda c: "online", "crm": crm_roto}).ejecutar(ctx_crm)
caso("crm roto: el turno sigue sin él", (r.plan.accion, r.errores), ("recomendar", []))
caso("crm roto: queda anotado en los pasos", r.pasos[0].get("error"), "RuntimeError")
r = MotorRecursivo(ReglasDecision(), {"stock": lambda c: "online", "crm": tool_crm}, C.Limites(max_herramientas=1)).ejecutar(ctx_crm)
caso("crm cuenta para el tope de herramientas (tope 1: lo gasta el CRM)", r.tope is not None, True)


def stock_roto(codigo):
    raise ConnectionError("stock caído")


r = MotorRecursivo(ReglasDecision(), {"stock": stock_roto}).ejecutar(estado("V35", "consulta_producto"))
caso("stock roto: no se adivina, pasa a una persona", r.plan.accion, "pedir_asesora")
caso("stock roto: queda anotado", r.fallo_herramienta, "stock")
caso("stock roto: el plan es válido", r.errores, [])

# --- quality gate ampliado --------------------------------------------------------------------------------------------
gate = ReglasCalidad(precios=lambda: {330}, nombres=NOMBRES.get, todos_los_nombres=lambda: dict(NOMBRES),
                     ficha_texto=lambda c: " ".join(str(v) for v in FICHAS.get(c, {}).values()))
ctx_g = {"conversation": {"turns_total": 3}}
plan_r = Plan("recomendar", "V35", [], "", {"tipo": "talla", "texto": "¿Qué talla usas normalmente?"})
bueno = "Te recomiendo el *Vestido Irla*, es de crepé y va muy bien para una boda.\n\n¿Qué talla usas normalmente?"
caso("gate: apertura natural + la pregunta del código pasa", gate.evaluar(bueno, plan_r, ctx_g), {"passed": True, "score": 1.0, "errors": []})
caso("gate: texto en chino no pasa", "caracteres que no son español" in gate.evaluar("Te recomiendo el *Vestido Irla*, 很漂亮。\n\n¿Qué talla usas normalmente?", plan_r, ctx_g)["errors"], True)
caso("gate: cirílico no pasa", "caracteres que no son español" in gate.evaluar("Te recomiendo el *Vestido Irla* красивый.\n\n¿Qué talla usas normalmente?", plan_r, ctx_g)["errors"], True)
caso("gate: nombrar otra prenda por su nombre no pasa",
     any("nombra otra prenda" in e for e in gate.evaluar("Te recomiendo el *Vestido Irla*, mejor que el Vestido Kendall.\n\n¿Qué talla usas normalmente?", plan_r, ctx_g)["errors"]), True)
caso("gate: tela que la ficha no tiene no pasa",
     "tela que la ficha no respalda: satén" in gate.evaluar("Te recomiendo el *Vestido Irla*, de satén.\n\n¿Qué talla usas normalmente?", plan_r, ctx_g)["errors"], True)
caso("gate: la tela de la ficha sí pasa", gate.evaluar(bueno, plan_r, ctx_g)["passed"], True)
caso("gate: un estilo inventado («ochentero») no pasa",
     "afirma algo que no está en la ficha: ochentero" in gate.evaluar("Te recomiendo el *Vestido Irla*. Es un clásico ochentero, ideal para tu boda.\n\n¿Qué talla usas normalmente?", plan_r, ctx_g)["errors"], True)
caso("gate: lo que dice la ficha (corte A, escote en V) pasa",
     gate.evaluar("Te recomiendo el *Vestido Irla*. Tiene corte A con escote en V, ideal para una boda.\n\n¿Qué talla usas normalmente?", plan_r, ctx_g)["passed"], True)
caso("gate: palabras de opinión (elegante, ideal) se permiten",
     gate.evaluar("Te recomiendo el *Vestido Irla*. Es muy elegante y perfecto para ti.\n\n¿Qué talla usas normalmente?", plan_r, ctx_g)["passed"], True)
caso("gate: una palabra repetida («Vestido VESTIDO») no pasa",
     "palabra repetida" in gate.evaluar("Te recomiendo el *Vestido Irla*. Vestido VESTIDO elegante.\n\n¿Qué talla usas normalmente?", plan_r, ctx_g)["errors"], True)
caso("gate: la plantilla no se rechaza por su propia frase",
     gate.evaluar("Te recomiendo el *Vestido Irla*.\n\n¿Qué talla usas normalmente?", plan_r, ctx_g)["passed"], True)
caso("gate: sin la pregunta del código no pasa",
     "no hace la pregunta que eligió el código" in gate.evaluar("Te recomiendo el *Vestido Irla*.", plan_r, ctx_g)["errors"], True)
plan_perfil = Plan("recomendar", "V35", [], "", {"tipo": "talla", "texto": "¿Usas talla *M*, como en tu pedido anterior, o prefieres otra talla?"})
caso("gate: la pregunta con negritas (talla del pedido anterior) se reconoce",
     gate.evaluar("Te recomiendo el *Vestido Irla*.\n\n¿Usas talla *M*, como en tu pedido anterior, o prefieres otra talla?", plan_perfil, ctx_g)["passed"], True)
caso("gate: otra pregunta además de la del código no pasa",
     "más de una pregunta" in gate.evaluar("¿Te gusta? Te recomiendo el *Vestido Irla*.\n\n¿Qué talla usas normalmente?", plan_r, ctx_g)["errors"], True)
caso("gate: nombrar a la asesora no pasa", "nombra a la asesora (o llama así a la clienta)" in gate.evaluar("Entendido, Rosmary.\n\n¿Qué talla usas normalmente?", plan_r, ctx_g)["errors"], True)
ctx_t = {"conversation": {"turns_total": 3, "last_user_message": "pa una boda", "recent_turns": []}}
caso("gate: una talla que nadie dijo no pasa",
     "talla que nadie dijo: L" in gate.evaluar("Te recomiendo el *Vestido Irla*, te recordamos tu talla L.\n\n¿Qué talla usas normalmente?", plan_r, ctx_t)["errors"], True)
caso("gate: la talla que sí dijo pasa",
     "talla que nadie dijo: M" in gate.evaluar("Te recomiendo el *Vestido Irla* en talla M.\n\n¿Qué talla usas normalmente?", plan_r, {"conversation": {"turns_total": 3, "last_user_message": "uso talla m", "recent_turns": []}})["errors"], False)
caso("gate: un número que nadie dijo no pasa",
     "dato que nadie dijo: 15" in gate.evaluar("Te recomiendo el *Vestido Irla*, para tus 15 años.\n\n¿Qué talla usas normalmente?", plan_r, ctx_t)["errors"], True)
caso("gate: un número que ella dijo pasa",
     "dato que nadie dijo: 20" in gate.evaluar("Te recomiendo el *Vestido Irla* para el 20.\n\n¿Qué talla usas normalmente?", plan_r, {"conversation": {"turns_total": 3, "last_user_message": "es el 20 de octubre", "recent_turns": []}})["errors"], False)
caso("gate: apertura de más de 32 palabras no pasa",
     any("apertura demasiado larga" in e for e in gate.evaluar("Te recomiendo el *Vestido Irla*. " + "muy bonito y elegante " * 12 + "\n\n¿Qué talla usas normalmente?", plan_r, ctx_g)["errors"]), True)
caso("gate: volver a saludar no pasa", "vuelve a saludar" in gate.evaluar("¡Hola! Te recomiendo el *Vestido Irla*.\n\n¿Qué talla usas normalmente?", plan_r, ctx_g)["errors"], True)
caso("gate: saludar en el primer turno sí se permite",
     "vuelve a saludar" in gate.evaluar("Hola, te recomiendo el *Vestido Irla*.\n\n¿Qué talla usas normalmente?", plan_r, {"conversation": {"turns_total": 0}})["errors"], False)
caso("gate: demasiado largo no pasa", any("demasiado largo" in e for e in gate.evaluar("Te recomiendo el *Vestido Irla*. " + "Muy bonito. " * 40 + "\n\n¿Qué talla usas normalmente?", plan_r, ctx_g)["errors"]), True)
caso("gate: preguntar algo que el plan no pide no pasa",
     "pregunta algo que el plan no pide" in gate.evaluar("Te recomiendo el *Vestido Irla*. ¿Te gusta?", Plan("recomendar", "V35"), ctx_g)["errors"], True)
caso("gate: una pregunta del código sin otra apertura pasa",
     gate.evaluar("¿Para cuándo es el matrimonio?", Plan("preguntar", None, [], "", {"tipo": "fecha", "texto": "¿Para cuándo es el matrimonio?"}), ctx_g)["passed"], True)

# --- generación: plantilla y modelo local ------------------------------------------------------------------------------
caso("componer: apertura y pregunta en párrafos aparte", componer("Hola.", {"pregunta": {"texto": "¿Sí?"}}), "Hola.\n\n¿Sí?")
caso("componer: sin apertura queda solo la pregunta", componer("", {"pregunta": {"texto": "¿Sí?"}}), "¿Sí?")
caso("componer: sin pregunta queda la apertura", componer("Hola.", {}), "Hola.")
pl = PlantillaGeneracion(nombres=NOMBRES.get)
caso("plantilla: recomendar con la pregunta del código", pl.redactar(plan_r.a_dict()), "Te recomiendo el *Vestido Irla*.\n\n¿Qué talla usas normalmente?")
caso("plantilla: preguntar usa la pregunta del código",
     pl.redactar({"accion": "preguntar", "pregunta": {"texto": "¿Para cuándo es?"}}), "¿Para cuándo es?")
caso("plantilla: preguntar sin pregunta es el aviso de sin stock",
     pl.redactar({"accion": "preguntar"}).startswith("Por ahora no tengo esa prenda"), True)

prompts: list[list[dict]] = []


def modelo(texto):
    def llamar(m, mensajes, temperatura, max_tokens):
        prompts.append(mensajes)
        return texto
    return llamar


ctx_llm = {"conversation": {"last_user_message": "busco algo elegante para una boda de noche", "turns_total": 3}}
llm = LlmLocalGeneracion("u", "qwen", nombre_de=NOMBRES.get, ficha_de=FICHAS.get,
                         llamar=modelo("Tiene un corte A con escote en V, ideal para una boda de noche."))
txt = llm.redactar(plan_r.a_dict(), 0, ctx_llm)
caso("llm: el código pone el nombre; el modelo, el motivo; el código, la pregunta",
     txt, "Te recomiendo el *Vestido Irla*. Tiene un corte A con escote en V, ideal para una boda de noche.\n\n¿Qué talla usas normalmente?")
caso("llm: el prompt lleva los datos de la ficha", "corte A con escote en V" in prompts[0][1]["content"] and "crepé" in prompts[0][1]["content"], True)
caso("llm: el prompt prohíbe preguntar, saludar y nombrarse", all(x in prompts[0][0]["content"] for x in ("No hagas ninguna pregunta", "no te presentes", "no menciones tu nombre")), True)
caso("llm: el último mensaje de la clienta viaja", "boda de noche" in prompts[0][1]["content"], True)
caso("llm: el prompt trae un ejemplo", "Ejemplo" in prompts[0][1]["content"], True)
LlmLocalGeneracion("u", "q", ficha_de=FICHAS.get, llamar=modelo("Va muy bien.")).redactar(plan_r.a_dict(), 1, ctx_llm)
caso("llm: la variante 1 pide menos palabras", "máximo 12 palabras" in prompts[-1][1]["content"], True)
caso("llm: si el modelo no escribe nada, queda el nombre de la prenda",
     LlmLocalGeneracion("u", "q", nombre_de=NOMBRES.get, llamar=modelo("¿Algo?")).redactar(plan_r.a_dict(), 0, ctx_llm),
     "Te recomiendo el *Vestido Irla*.\n\n¿Qué talla usas normalmente?")
caso("llm: solo la primera frase del modelo",
     LlmLocalGeneracion("u", "q", nombre_de=NOMBRES.get, llamar=modelo("Es elegante. Además es barato. Y bonito.")).redactar(plan_r.a_dict(), 0, ctx_llm),
     "Te recomiendo el *Vestido Irla*. Es elegante.\n\n¿Qué talla usas normalmente?")
caso("llm: las preguntas que el modelo mete se quitan",
     LlmLocalGeneracion("u", "q", nombre_de=NOMBRES.get, llamar=modelo("¿Te gusta? Es elegante.")).redactar(plan_r.a_dict(), 0, ctx_llm),
     "Te recomiendo el *Vestido Irla*. Es elegante.\n\n¿Qué talla usas normalmente?")
caso("llm: comillas y etiqueta se limpian",
     LlmLocalGeneracion("u", "q", nombre_de=NOMBRES.get, llamar=modelo('Frase: "Te va a encantar."')).redactar(plan_r.a_dict(), 0, ctx_llm),
     "Te recomiendo el *Vestido Irla*. Te va a encantar.\n\n¿Qué talla usas normalmente?")
caso("llm: el razonamiento <think> se quita",
     LlmLocalGeneracion("u", "q", nombre_de=NOMBRES.get, llamar=modelo("<think>hmm</think>Te va a encantar.")).redactar(plan_r.a_dict(), 0, ctx_llm),
     "Te recomiendo el *Vestido Irla*. Te va a encantar.\n\n¿Qué talla usas normalmente?")
plan_p = {"accion": "preguntar", "pregunta": {"texto": "¿Para cuándo es el matrimonio?"}}
caso("llm: al preguntar escribe un acuse y el código pone la pregunta",
     LlmLocalGeneracion("u", "q", llamar=modelo("¡Qué bonito! 😊")).redactar(plan_p, 0, ctx_llm), "¡Qué bonito! 😊\n\n¿Para cuándo es el matrimonio?")
caso("llm: sin acuse queda solo la pregunta",
     LlmLocalGeneracion("u", "q", llamar=modelo("¿Algo?")).redactar(plan_p, 0, ctx_llm), "¿Para cuándo es el matrimonio?")
caso("llm: el acuse del prompt es corto", "máximo 6 palabras" in (prompts.clear() or LlmLocalGeneracion("u", "q", llamar=modelo("Hola")).redactar(plan_p, 0, ctx_llm) and prompts[-1][1]["content"]), True)
try:
    LlmLocalGeneracion("u", "q", llamar=modelo("x")).redactar({"accion": "pedir_asesora"}, 0, ctx_llm)
    caso("llm: no redacta lo que no es suyo", False, True)
except ValueError:
    caso("llm: no redacta lo que no es suyo", True, True)
caso("llm: atiende recomendar y preguntar", (llm.atiende({"accion": "recomendar"}), llm.atiende({"accion": "preguntar"}), llm.atiende({"accion": "responder"})), (True, True, False))
caso("llm: se puede limitar a recomendar", LlmLocalGeneracion("u", "q", llamar=modelo("x"), acciones=("recomendar",)).atiende({"accion": "preguntar"}), False)
cadena = Encadenada([LlmLocalGeneracion("u", "q", llamar=modelo("x")), pl], intentos=2)
caso("encadenada: el modelo local primero (2 intentos) y la plantilla al final (1)",
     [(m.nombre, v) for m, v in cadena.intentos_en_orden(plan_r.a_dict())], [("llm-local", 0), ("llm-local", 1), ("plantilla", 0)])
caso("encadenada: una acción que el modelo no atiende va directo a la plantilla",
     [(m.nombre, v) for m, v in cadena.intentos_en_orden({"accion": "pedir_asesora"})], [("plantilla", 0)])

# --- modo activo en AgentV2 ----------------------------------------------------------------------------------------------
llamadas_v1: list[bool] = []


def v1(res):
    def f(req):
        llamadas_v1.append(req.usar_llm)
        return dict(res)
    return f


RES_REC = {"accion": "responder", "modelo_llm": "deepseek", "respuesta": "Texto de V1", "etapa": "seguimiento",
           "sugerencias": [{"codigo": "V35"}], "comercial": {"intent": "consulta_producto"}, "ms": 700,
           "memoria": {"producto": "V35", "mostrados": ["V35"], "sabemos": {"ocasion": "matrimonio"}}}
RES_PREG = {"accion": "responder", "modelo_llm": "deepseek", "respuesta": "Qué lindo. ¿Para cuándo es?", "etapa": "prospeccion",
            "sugerencias": [], "comercial": {"intent": "saludo"}, "siguiente_pregunta": "fecha", "ms": 700,
            "memoria": {"producto": "", "mostrados": [], "sabemos": {"ocasion": "matrimonio"}}}


def v2(res, motor_stock=lambda c: "online", redactor=None, modo=None, habla=("recomendar", "preguntar")):
    pl_ = PlantillaGeneracion(nombres=NOMBRES.get)
    return AgentV2(v1=v1(res), contexto=ContextBuilder(texto_pregunta=texto_pregunta),
                   motor=MotorRecursivo(ReglasDecision(), {"stock": motor_stock}), calidad=gate,
                   redactor=pl_, redactor_activo=redactor or pl_, modo=modo, habla=habla)


H = turnos(3)
llamadas_v1.clear()
out = v2(RES_REC).conversar(pedido(historial=H, memoria={"mostrados": []}, modo="activo", mensaje="busco algo para una boda"))
caso("activo: V2 habla cuando coincide con V1", out["v2"]["enviado"], "v2")
caso("activo: el texto es el de V2", out["respuesta"], "Te recomiendo el *Vestido Irla*.\n\n¿Qué talla usas normalmente?")
caso("activo: V1 corre UNA vez y sin su LLM de pago", llamadas_v1, [False])
caso("activo: las fotos y la ficha siguen siendo las de V1", (out["sugerencias"], out["memoria"]["producto"]), ([{"codigo": "V35"}], "V35"))
caso("activo: el modelo queda marcado", out["modelo_llm"], "v2:plantilla")
caso("activo: el texto de respaldo de V1 queda en la traza", out["v2"]["v1_texto_respaldo"], "Texto de V1")
caso("activo: una sola llamada a V1", out["v2"]["llamadas_v1"], 1)

llamadas_v1.clear()
out = v2(RES_PREG).conversar(pedido(historial=H, modo="activo", mensaje="tengo una boda"))
caso("activo: preguntar con la pregunta del código", (out["v2"]["enviado"], out["respuesta"]), ("v2", "¿Para cuándo es el matrimonio?"))

llamadas_v1.clear()
out = v2(RES_REC).conversar(pedido(historial=H, memoria={"mostrados": []}, modo="activo", mensaje="x", usar_llm=False))
caso("activo sin usar_llm: V1 corre una vez", llamadas_v1, [False])

# V2 no habla → V1 completo, con su LLM
llamadas_v1.clear()
out = v2(RES_REC, motor_stock=lambda c: "").conversar(pedido(historial=H, memoria={"mostrados": []}, modo="activo", mensaje="x"))
caso("activo: sin stock V2 no coincide con V1 → habla V1", (out["v2"]["enviado"], out["respuesta"]), ("v1", "Texto de V1"))
caso("activo: V1 corre dos veces (sin LLM y luego completo)", llamadas_v1, [False, True])
caso("activo: el motivo queda en la traza", out["v2"]["motivo_v1"].startswith("desacuerdo con V1"), True)
caso("activo: V1 no se modificó", out["modelo_llm"], "deepseek")

for nombre, res_, kw, motivo in [
    ("flujo fijo", {**RES_REC, "modelo_llm": "flujo_pedido"}, dict(historial=H), "flujo fijo de código"),
    ("primer mensaje", RES_REC, dict(historial=[]), "primer mensaje (saludo y presentación son de V1)"),
    ("foto de V1 distinta a la del plan", {**RES_REC, "sugerencias": [{"codigo": "V42"}]}, dict(historial=H),
     "la foto que manda V1 no es la del plan"),
    ("V1 recomienda y V2 pregunta", RES_REC, dict(historial=H, memoria={"mostrados": ["V35"]}), "desacuerdo con V1 (V1 recomendar, V2 responder)"),
]:
    llamadas_v1.clear()
    o = v2(res_).conversar(pedido(modo="activo", mensaje="x", **kw))
    caso(f"activo: no habla — {nombre}", (o["v2"]["enviado"], o["v2"]["motivo_v1"]), ("v1", motivo))

# En sombra, nunca cambia el texto
llamadas_v1.clear()
out = v2(RES_REC).conversar(pedido(historial=H, memoria={"mostrados": []}, mensaje="x"))
caso("sombra: el texto es el de V1 aunque V2 pudiera hablar", (out["respuesta"], out["v2"]["enviado"]), ("Texto de V1", "v1"))
caso("sombra: V1 corre una vez, con su LLM", llamadas_v1, [True])
caso("sombra: aun así se redacta el borrador (con la plantilla de código)", out["v2"]["sombra"]["generacion"]["passed"], True)
out = v2(RES_REC, modo="activo").conversar(pedido(historial=H, memoria={"mostrados": []}, mensaje="x"))
caso("el modo del entorno lo define el agente (modo=activo)", out["v2"]["enviado"], "v2")
out = v2(RES_REC, modo="activo").conversar(pedido(historial=H, memoria={"mostrados": []}, mensaje="x", modo="sombra"))
caso("la petición manda sobre el modo del agente", out["v2"]["enviado"], "v1")


class LlmChino:
    nombre = "llm-local"

    def redactar(self, plan, variante=0, contexto=None):
        return "Te recomiendo el *Vestido Irla*, 很漂亮。\n\n¿Qué talla usas normalmente?"


pl_ = PlantillaGeneracion(nombres=NOMBRES.get)
out = v2(RES_REC, redactor=Encadenada([LlmChino(), pl_])).conversar(pedido(historial=H, memoria={"mostrados": []}, modo="activo", mensaje="x"))
caso("el modelo se va a chino → el gate lo rechaza y sale la plantilla de código",
     (out["v2"]["enviado"], out["modelo_llm"], out["v2"]["sombra"]["generacion"]["regeneraciones"]), ("v2", "v2:plantilla", 2))
caso("los intentos rechazados quedan en la traza",
     [(i["motor"], i["passed"]) for i in out["v2"]["sombra"]["generacion"]["intentos"]],
     [("llm-local", False), ("llm-local", False), ("plantilla", True)])


class LlmCaido:
    nombre = "llm-local"

    def redactar(self, plan, variante=0, contexto=None):
        raise TimeoutError("ollama no contesta")


out = v2(RES_REC, redactor=Encadenada([LlmCaido(), pl_])).conversar(pedido(historial=H, memoria={"mostrados": []}, modo="activo", mensaje="x"))
caso("el modelo local caído → sale la plantilla de código", (out["v2"]["enviado"], out["modelo_llm"]), ("v2", "v2:plantilla"))


class TodoMalo:
    nombre = "malo"

    def redactar(self, plan, variante=0, contexto=None):
        return "Llega mañana con descuento."


llamadas_v1.clear()
out = v2(RES_REC, redactor=TodoMalo()).conversar(pedido(historial=H, memoria={"mostrados": []}, modo="activo", mensaje="x"))
caso("nada pasa el gate → habla V1 completo", (out["v2"]["enviado"], out["respuesta"], llamadas_v1), ("v1", "Texto de V1", [False, True]))
caso("nada pasa el gate: el motivo es el control de calidad", out["v2"]["motivo_v1"], "el borrador no pasó el control de calidad")

# Por defecto V2 solo habla al recomendar: en preguntar conserva lo que V1 contesta y reconoce.
caso("V2_HABLA por defecto: solo recomendar", C.habla_por_defecto({}), ("recomendar",))
caso("V2_HABLA=recomendar,preguntar", C.habla_por_defecto({"V2_HABLA": "Recomendar, preguntar"}), ("recomendar", "preguntar"))
caso("V2_HABLA con basura → recomendar", C.habla_por_defecto({"V2_HABLA": "gritar"}), ("recomendar",))
llamadas_v1.clear()
out = v2(RES_PREG, habla=None).conversar(pedido(historial=H, modo="activo", mensaje="tengo una boda"))
caso("por defecto, en preguntar habla V1 (conserva su acuse y su respuesta)",
     (out["v2"]["enviado"], out["respuesta"], out["v2"]["motivo_v1"]),
     ("v1", "Qué lindo. ¿Para cuándo es?", "V2 coincide con V1 en preguntar: el texto es el de V1"))
out = v2(RES_REC, habla=None).conversar(pedido(historial=H, memoria={"mostrados": []}, modo="activo", mensaje="x"))
caso("por defecto, en recomendar sí habla V2", out["v2"]["enviado"], "v2")

# --- métricas del modo activo ---------------------------------------------------------------------------------------------
reg = Registro()
reg.turno("v2", 100, {"modo": "activo", "enviado": "v2", "sombra": {"plan": {"accion": "recomendar"}, "v1_accion": "recomendar", "errores": []}})
reg.turno("v2", 100, {"modo": "activo", "enviado": "v1", "motivo_v1": "desacuerdo con V1 (V1 recomendar, V2 preguntar)",
                      "sombra": {"plan": {"accion": "preguntar"}, "v1_accion": "recomendar", "errores": []}})
reg.turno("v2", 100, {"modo": "activo", "enviado": "v1", "motivo_v1": "flujo fijo de código",
                      "sombra": {"plan": {"accion": "preguntar"}, "v1_accion": "responder", "v1_flujo_fijo": True, "errores": []}})
rs = reg.resumen()["v2"]
caso("métricas: turnos activos", rs["activo"], 3)
caso("métricas: V2 habló en 1 de 3", rs["habla_v2"], round(1 / 3, 3))
caso("métricas: los flujos fijos no cuentan para el acuerdo", rs["comparables"], 2)
caso("métricas: acuerdo sobre los comparables", rs["acuerdo_v1"], 0.5)
caso("métricas: por qué no habló, agrupado", rs["por_que_no_habla"], {"desacuerdo con V1": 1, "flujo fijo de código": 1})


def main() -> int:
    for f in fallos:
        print(f)
    print(f"v2 activa (contexto con V1, Jev local, CRM, generación, modo activo): {total - len(fallos)}/{total} casos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
