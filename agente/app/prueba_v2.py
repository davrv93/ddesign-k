"""Pruebas de la V2 (fases 1–2): sin red, sin modelos, sin LLM. Se ejecutan al construir la imagen:

    python3 -m app.prueba_v2
"""
from __future__ import annotations

import sys
from types import SimpleNamespace as NS

from .v2 import config as C
from .v2.agente import AgentV2
from .v2.contexto import ContextBuilder
from .v2.estado import separar
from .v2.interfaces import Decision, DecisionEngine, GenerationEngine, QualityGate
from .v2.plan import Plan, validar

fallos: list[str] = []
total = 0


def caso(nombre: str, obtenido, esperado) -> None:
    global total
    total += 1
    if obtenido != esperado:
        fallos.append(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


def pedido(**kw):
    base = dict(mensaje="hola", historial=[], etapa="", memoria=None, producto="", talla="", desde_anuncio=False)
    base.update(kw)
    return NS(**base)


# --- configuración ---------------------------------------------------------------------------------------------
caso("sin AGENT_VERSION → v1", C.version_por_defecto({}), "v1")
caso("AGENT_VERSION=V2 → v2 (sin importar mayúsculas)", C.version_por_defecto({"AGENT_VERSION": " V2 "}), "v2")
caso("AGENT_VERSION inválida → v1", C.version_por_defecto({"AGENT_VERSION": "v9"}), "v1")
caso("petición v2 gana al entorno", C.version_pedida("v2", "v1"), "v2")
caso("petición vacía → entorno", C.version_pedida("", "v2"), "v2")
caso("petición inválida → entorno", C.version_pedida("xx", "v1"), "v1")
L = C.limites_desde_entorno({})
caso("límites por defecto", (L.max_pasos, L.max_decisiones, L.max_herramientas, L.max_regeneraciones), (4, 4, 4, 1))
caso("límite fuera de rango se acota a 10", C.limites_desde_entorno({"MAX_AGENT_STEPS": "99"}).max_pasos, 10)
caso("límite negativo se acota a 1", C.limites_desde_entorno({"MAX_AGENT_STEPS": "-3"}).max_pasos, 1)
caso("límite no numérico → defecto", C.limites_desde_entorno({"MAX_TOOL_CALLS": "x"}).max_herramientas, 4)

# --- presupuesto: nunca bucles infinitos -----------------------------------------------------------------------
p = C.Presupuesto(C.Limites(max_pasos=2))
p.gastar("paso"); p.gastar("paso")
try:
    p.gastar("paso")
    caso("el tercer paso con tope 2 debe fallar", False, True)
except C.LimiteExcedido:
    caso("el tercer paso con tope 2 se corta", True, True)
caso("quedan 0 pasos", p.quedan("paso"), 0)
try:
    p.gastar("inventado")
    caso("tipo de paso desconocido se rechaza", False, True)
except ValueError:
    caso("tipo de paso desconocido se rechaza", True, True)

# --- contexto estructurado -------------------------------------------------------------------------------------
historial = [NS(rol="cliente" if i % 2 == 0 else "bot", texto="x" * 500) for i in range(12)]
ctx = ContextBuilder().construir(pedido(mensaje="busco vestido", historial=historial,
                                        etapa="seguimiento",
                                        memoria={"sabemos": {"ocasion": "matrimonio", "nombre": "Lucía"},
                                                 "temperatura": "tibia", "producto": "V35", "mostrados": ["V35"]}))
caso("contexto: etapa de la petición", ctx["conversation"]["stage"], "seguimiento")
caso("contexto: solo los últimos 4 turnos", len(ctx["conversation"]["recent_turns"]), 4)
caso("contexto: turnos recortados a 200 caracteres", max(len(t["texto"]) for t in ctx["conversation"]["recent_turns"]), 200)
caso("contexto: cuenta el historial completo", ctx["conversation"]["turns_total"], 12)
caso("contexto: sin memoria no falla", ContextBuilder().construir(pedido())["customer"]["name"], None)
caso("contexto: prenda en foco de la memoria", ctx["product"]["focus"], "V35")

# --- hechos, inferencias y desconocidos ------------------------------------------------------------------------
s = separar(ctx)
caso("hecho: ocasión dicha por la clienta", s["hechos"].get("ocasion"), "matrimonio")
caso("inferencia: la temperatura no es hecho", "temperature" in s["hechos"], False)
caso("inferencia: temperatura separada", s["inferencias"].get("temperature"), "tibia")
caso("desconocido: talla", "talla" in s["desconocidos"], True)
caso("desconocido: ocasión conocida no aparece", "ocasion" in s["desconocidos"], False)
caso("hecho: la prenda en foco la decidió el código", s["hechos"].get("prenda_en_foco"), "V35")

# --- plan validado -----------------------------------------------------------------------------------------------
STOCK = {"V35", "V21"}
caso("plan válido sin errores", validar(Plan("recomendar", "V35", ["hombros descubiertos"], "ocasión de noche",
                                              {"tipo": "talla", "texto": "¿Qué talla usas?"}), STOCK), [])
caso("producto sin stock se rechaza", validar(Plan("recomendar", "V99"), STOCK),
     ["producto V99 sin stock o inexistente"])
caso("recomendar sin producto se rechaza", validar(Plan("recomendar"), STOCK), ["«recomendar» sin producto"])
caso("acción inventada se rechaza", validar(Plan("hacer_descuento"), STOCK), ["acción desconocida: hacer_descuento"])
caso("pregunta sin ? se rechaza", validar(Plan("preguntar", pregunta={"texto": "Dime tu talla"}), STOCK),
     ["la pregunta no lleva signo de interrogación"])
caso("dos preguntas en un plan se rechazan",
     validar(Plan("preguntar", pregunta={"texto": "¿Talla? ¿Fecha?"}), STOCK), ["más de una pregunta en el mismo plan"])

# --- interfaces ----------------------------------------------------------------------------------------------------
class Fija:
    nombre = "fija"

    def decide(self, contexto, decisiones):
        return [Decision(d, "x", 1.0) for d in decisiones]

    def redactar(self, plan, variante=0, contexto=None):
        return "ok"

    def evaluar(self, borrador, plan):
        return {"passed": True, "score": 1.0, "errors": []}


caso("implementación cumple DecisionEngine", isinstance(Fija(), DecisionEngine), True)
caso("implementación cumple GenerationEngine", isinstance(Fija(), GenerationEngine), True)
caso("implementación cumple QualityGate", isinstance(Fija(), QualityGate), True)

# --- AgentV2 envuelve V1 sin cambiarla -----------------------------------------------------------------------------
RES_V1 = {"respuesta": "Hola", "etapa": "prospeccion", "sugerencias": [], "memoria": {"etapa": "prospeccion"}}
llamadas = []


def v1_falso(req):
    llamadas.append(req)
    return RES_V1


agente = AgentV2(v1=v1_falso)
out = agente.conversar(pedido(mensaje="hola"))
caso("V2 devuelve lo mismo que V1", {k: out[k] for k in RES_V1}, RES_V1)
caso("V2 marca la versión", out["version"], "v2")
caso("V2 traza: sin fallback", out["v2"]["fallback"], False)
caso("V2 no muta la respuesta de V1", "version" in RES_V1, False)
caso("V1 se llama una sola vez", len(llamadas), 1)


class RompeContexto:
    def construir(self, req, res=None):
        raise KeyError("boom")


agente = AgentV2(v1=v1_falso, contexto=RompeContexto())
out = agente.conversar(pedido(mensaje="hola"))
caso("si el contexto falla, el turno sigue por V1", out["respuesta"], "Hola")
caso("fallback queda en la traza", (out["v2"]["fallback"], out["v2"]["motivo"]), (True, "KeyError"))

# --- motor recursivo: DECIDE → TOOL → ACTUALIZA → DECIDE DE NUEVO ----------------------------------------------------
from .v2.decision import JevStyleDecision, ReglasDecision, parsear
from .v2.motor import MotorRecursivo


def ctx_con(foco="V35", **kw):
    base = {"conversation": {"stage": "seguimiento", "pending_question": None, "recent_turns": []},
            "customer": {}, "requirements": {"ocasion": "matrimonio"},
            "product": {"focus": foco, "shown": []}, "business": {}}
    base.update(kw)
    return base


llamadas_stock = []


def stock_online(codigo):
    llamadas_stock.append(codigo)
    return "online"


r = MotorRecursivo(ReglasDecision(), {"stock": stock_online}).ejecutar(ctx_con())
caso("recursión: dos pasos (consultar stock y luego decidir)", len(r.pasos), 2)
caso("recursión: el primer paso pide la herramienta", r.pasos[0]["herramienta"], "stock")
caso("recursión: la herramienta se llama una vez", llamadas_stock, ["V35"])
caso("recursión: con stock la acción final es recomendar", r.plan.accion, "recomendar")
caso("recursión: el plan lleva el producto en foco", r.plan.producto, "V35")
caso("recursión: plan válido, sin errores", r.errores, [])

r = MotorRecursivo(ReglasDecision(), {"stock": lambda c: ""}).ejecutar(ctx_con())
caso("sin stock: preguntar, no recomendar", r.plan.accion, "preguntar")
caso("sin stock: el plan no nombra producto", r.plan.producto, None)
caso("sin stock: plan válido (preguntar no necesita producto)", r.errores, [])

r = MotorRecursivo(ReglasDecision(), {"stock": lambda c: "online"}).ejecutar(ctx_con(foco=None))
caso("sin prenda en foco no consulta stock", r.pasos[0]["herramienta"], None)
caso("sin prenda en foco ni pregunta del código: responder", r.plan.accion, "responder")
ctx_p = ctx_con(foco=None)
ctx_p["conversation"]["next_question"] = {"tipo": "fecha", "texto": "¿Para cuándo es el matrimonio?"}
r = MotorRecursivo(ReglasDecision(), {"stock": lambda c: "online"}).ejecutar(ctx_p)
caso("sin prenda en foco y con pregunta del código: preguntar", r.plan.accion, "preguntar")
caso("el plan de preguntar lleva la pregunta del código", r.plan.pregunta["texto"], "¿Para cuándo es el matrimonio?")


class Testarudo:
    """Pide consultar stock en todas las vueltas: el motor no debe girar sin fin."""
    nombre = "testarudo"

    def decide(self, estado, decisiones):
        from .v2.interfaces import Decision
        return [Decision("intent", "purchase", 1.0), Decision("next_action", "consultar_stock", 1.0)]


llamadas_stock.clear()
lim = C.Limites(max_pasos=3, max_decisiones=3, max_herramientas=4)
r = MotorRecursivo(Testarudo(), {"stock": stock_online}, lim).ejecutar(ctx_con())
caso("tope: el bucle se corta por pasos", r.tope is not None and "paso" in r.tope, True)
caso("tope: no pasa de 3 pasos", len(r.pasos), 3)
caso("tope con herramienta pendiente: se pasa a una persona, no a un plan a medias", r.plan.accion, "pedir_asesora")
caso("tope: la herramienta se llama una sola vez aunque se insista", llamadas_stock, ["V35"])

lim0 = C.Limites(max_herramientas=0)
llamadas_stock.clear()
r = MotorRecursivo(ReglasDecision(), {"stock": stock_online}, lim0).ejecutar(ctx_con())
caso("tope de herramientas en 0: no llama a la herramienta", llamadas_stock, [])
caso("tope de herramientas en 0: el tope queda anotado", r.tope is not None, True)

# --- juez tipo Jev: salida JSON validada ---------------------------------------------------------------------------
buena = '{"decisiones":[{"decision":"next_action","choice":"recomendar","confianza":0.91},{"decision":"intent","choice":"purchase","confianza":0.88}]}'
ds = parsear(buena, ["intent", "next_action"])
caso("juez: JSON válido se parsea", [(d.decision, d.choice, d.fuente) for d in ds],
     [("intent", "purchase", "juez"), ("next_action", "recomendar", "juez")])
caso("juez: JSON envuelto en ```json se acepta", len(parsear("```json\n" + buena + "\n```", ["intent", "next_action"])), 2)
for nombre, crudo in [
    ("juez: opción fuera de lista se rechaza", '{"decisiones":[{"decision":"next_action","choice":"hacer_descuento","confianza":0.9}]}'),
    ("juez: confianza fuera de 0–1 se rechaza", '{"decisiones":[{"decision":"next_action","choice":"responder","confianza":7}]}'),
    ("juez: JSON roto se rechaza", '{"decisiones":[{"decision"'),
    ("juez: falta una decisión pedida se rechaza", '{"decisiones":[{"decision":"intent","choice":"purchase","confianza":0.9}]}'),
    ("juez: sin lista se rechaza", '{"respuesta":"hola"}'),
]:
    try:
        parsear(crudo, ["intent", "next_action"])
        caso(nombre, False, True)
    except ValueError:
        caso(nombre, True, True)

caso("juez en el motor: su decisión llega al plan",
     MotorRecursivo(JevStyleDecision(lambda p: '{"decisiones":[{"decision":"next_action","choice":"responder","confianza":0.8},{"decision":"intent","choice":"other","confianza":0.6}]}'))
     .ejecutar(ctx_con(foco=None)).plan.accion, "responder")

# --- sombra en AgentV2: el motor no cambia lo que se envía ---------------------------------------------------------
out = AgentV2(v1=v1_falso, motor=MotorRecursivo(ReglasDecision(), {"stock": stock_online})).conversar(pedido(mensaje="hola"))
caso("sombra: la respuesta sigue siendo la de V1", out["respuesta"], "Hola")
caso("sombra: el plan va en la traza", out["v2"]["sombra"]["plan"]["accion"] in ("recomendar", "preguntar", "responder"), True)
caso("sombra: compara con la acción de V1", "v1_accion" in out["v2"]["sombra"], True)


class MotorRoto:
    nombre = "roto"

    def decide(self, estado, decisiones):
        raise RuntimeError("juez caído")


out = AgentV2(v1=v1_falso, motor=MotorRecursivo(MotorRoto())).conversar(pedido(mensaje="hola"))
caso("motor caído: el turno sigue por V1", out["respuesta"], "Hola")
caso("motor caído: queda anotado en la sombra", out["v2"]["sombra"]["error"], "RuntimeError")
caso("motor caído: V2 no habla", out["v2"]["sombra"]["no_habla"], "el motor falló")

# --- fase 4: RAG como herramienta del ciclo --------------------------------------------------------------------------
from .v2.calidad import FALLBACK, PlantillaGeneracion, ReglasCalidad
from .v2.metricas import Registro
from .v2.plan import Plan as P

nombres = {"V35": "Vestido Irla", "V21": "Vestido Kendall", "V40": "Falda Paola"}
stock_v = {"V35": "", "V21": "online", "V40": ""}
llam = {"stock": [], "rag": []}


def tool_stock(c):
    llam["stock"].append(c)
    return stock_v.get(c, "")


def tool_rag(texto):
    llam["rag"].append(texto)
    return ["V21", "V40"]


r = MotorRecursivo(ReglasDecision(), {"stock": tool_stock, "rag": tool_rag}).ejecutar(ctx_con())
caso("RAG: el ciclo hace 4 pasos (stock, rag, stock alternativa, recomendar)", len(r.pasos), 4)
caso("RAG: cada herramienta se llama una vez", (llam["stock"], len(llam["rag"])), (["V35", "V21"], 1))
caso("RAG: la alternativa con stock se recomienda", (r.plan.accion, r.plan.producto), ("recomendar", "V21"))
caso("RAG: plan válido", r.errores, [])

stock_v = {"V35": ""}
llam = {"stock": [], "rag": []}
r = MotorRecursivo(ReglasDecision(), {"stock": tool_stock, "rag": lambda t: []}).ejecutar(ctx_con())
caso("RAG vacío: preguntar, no inventar alternativa", (r.plan.accion, r.plan.producto), ("preguntar", None))
caso("RAG vacío: la búsqueda sí se hizo", r.pasos[1]["herramienta"], "rag")

r = MotorRecursivo(ReglasDecision(), {"stock": tool_stock}).ejecutar(ctx_con())
caso("sin herramienta RAG: buscar_alternativa se degrada a preguntar", r.plan.accion, "preguntar")
caso("sin herramienta RAG: la degradación queda anotada", r.pasos[1].get("degradada"), "buscar_alternativa")

# --- fase 5: quality gate ------------------------------------------------------------------------------------------
gate = ReglasCalidad(precios=lambda: {320, 330, 15, 20}, nombres=lambda c: nombres.get(c))
plan_rec = P("recomendar", "V35", [], "", {"tipo": "talla", "texto": "¿Qué talla usas?"})
caso("gate: borrador bueno pasa", gate.evaluar("Te recomiendo el *Vestido Irla*.\n\n¿Qué talla usas?", plan_rec)["passed"], True)
caso("gate: precio inventado no pasa",
     "precio no verificado: S/ 999" in gate.evaluar("El *Vestido Irla* cuesta S/ 999.", plan_rec)["errors"], True)
caso("gate: precio real sí pasa", [e for e in gate.evaluar("El *Vestido Irla* cuesta S/ 330.", plan_rec)["errors"] if "precio" in e], [])
caso("gate: nombrar otra prenda no pasa", "nombra otra prenda" in gate.evaluar("Te recomiendo el V21 y el Vestido Irla.", plan_rec)["errors"], True)
caso("gate: no nombrar la prenda de la foto no pasa",
     "no nombra la prenda que va en la foto" in gate.evaluar("Esta te va a encantar.", plan_rec)["errors"], True)
caso("gate: promesa de entrega no pasa", "promesa o descuento no respaldado" in gate.evaluar("Llega en 2 días al *Vestido Irla*.", plan_rec)["errors"], True)
caso("gate: pedir una pregunta sin ? no pasa",
     "el plan pide preguntar y no hay pregunta" in gate.evaluar("Por ahora no hay.", P("preguntar")) ["errors"], True)
caso("gate: borrador vacío no pasa", gate.evaluar("   ", plan_rec)["passed"], False)

# --- fase 5: generación con control, regeneración acotada y fallback ----------------------------------------------
class RedactorMalo:
    def redactar(self, plan, variante=0, contexto=None):
        return "Llega en 2 días."


class RedactorUnaVez:
    def redactar(self, plan, variante=0, contexto=None):
        return "Llega en 2 días." if variante == 0 else "Te recomiendo el *Vestido Irla*.\n\n¿Qué talla usas normalmente?"


def agente_v2(redactor, motor_stock=lambda c: "online"):
    return AgentV2(v1=lambda req: RES_FOCO, motor=MotorRecursivo(ReglasDecision(), {"stock": motor_stock}),
                   calidad=gate, redactor=redactor)


pv = pedido(mensaje="busco vestido", memoria={"producto": "V35", "sabemos": {"ocasion": "matrimonio"}})
RES_FOCO = {"respuesta": "Hola", "etapa": "prospeccion", "sugerencias": [],
            "memoria": {"etapa": "prospeccion", "producto": "V35", "sabemos": {"ocasion": "matrimonio"}}}
g = agente_v2(PlantillaGeneracion(nombres=lambda c: nombres.get(c))).conversar(pv)["v2"]["sombra"]["generacion"]
caso("generación: plantilla sale a la primera y pasa", (g["passed"], g["fallback"], g["regeneraciones"]), (True, False, 0))
caso("generación: el borrador nombra la prenda", "Vestido Irla" in g["texto"], True)
g = agente_v2(RedactorUnaVez()).conversar(pv)["v2"]["sombra"]["generacion"]
caso("regeneración: la segunda variante pasa", (g["passed"], g["regeneraciones"], g["fallback"]), (True, 1, False))
g = agente_v2(RedactorMalo()).conversar(pv)["v2"]["sombra"]["generacion"]
caso("fallback: nada pasa → texto seguro, sin inventar", (g["fallback"], g["texto"]), (True, FALLBACK))
caso("fallback: regeneraciones no pasan de MAX_REGENERATIONS",
     g["regeneraciones"] <= C.limites_desde_entorno({}).max_regeneraciones, True)
out = agente_v2(RedactorMalo()).conversar(pv)
caso("fallback: lo que se envía sigue siendo el texto de V1", out["respuesta"], "Hola")
caso("fallback: el borrador rechazado no sale en la respuesta", "Llega en 2 días" not in str(out.get("respuesta")), True)
class SinPlan:
    nombre = "sin_plan"

    def decide(self, estado, decisiones):
        from .v2.interfaces import Decision
        return [Decision("next_action", "recomendar", 1.0)]   # recomendar sin producto: plan inválido


caso("sin plan válido no hay borrador",
     "generacion" in AgentV2(v1=v1_falso, motor=MotorRecursivo(SinPlan()), calidad=gate,
                             redactor=RedactorMalo()).conversar(pedido(mensaje="x"))["v2"]["sombra"], False)

# --- fase 6: métricas por versión -------------------------------------------------------------------------------
reg = Registro()
reg.turno("v1", 120)
reg.turno("v1", 200)
reg.turno("v2", 150, {"sombra": {"plan": {"accion": "recomendar"}, "v1_accion": "recomendar", "errores": [],
                               "generacion": {"passed": True, "fallback": False, "regeneraciones": 1,
                                              "intentos": [1, 2]}}})
reg.turno("v2", 300, {"fallback": True, "sombra": {"error": "RuntimeError"}})
rs = reg.resumen()
caso("métricas: turnos por versión", (rs["v1"]["turnos"], rs["v2"]["turnos"]), (2, 2))
caso("métricas: acuerdo con V1 (recomendar ↔ recomendar)", rs["v2"]["acuerdo_v1"], 1.0)
caso("métricas: tasa de regeneración", rs["v2"]["regeneracion"], 1.0)
caso("métricas: error del motor cuenta", rs["v2"]["motor_error"], 0.5)
caso("métricas: fallback de contexto cuenta", rs["v2"]["fallback_contexto"], 0.5)
caso("métricas: p95 de v1", rs["v1"]["p95_ms"], 200)


def main() -> int:
    for f in fallos:
        print(f)
    print(f"v2 fases 1–6 (contexto, motor recursivo, RAG, calidad, métricas): {total - len(fallos)}/{total} casos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
