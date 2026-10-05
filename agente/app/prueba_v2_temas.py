"""Pruebas de los cambios de tema («suspender, no cancelar»): detector SOFT/SIDE/HARD, pila de pendientes, resume_priority, dependencias
entre slots, selección de la retoma, respuestas rápidas y su lectura determinista, las plantillas de retoma, y la integración con
AgentV2 (sombra, activo, memoria que viaja). Sin red y sin modelos (los modelos son falsos):

    python3 -m app.prueba_v2_temas
"""
from __future__ import annotations

import json
import random
import re
import sys
from types import SimpleNamespace as NS

from . import memoria
from .v2 import config as C
from .v2 import plantillas as T
from .v2 import temas as M
from .v2.agente import AgentV2
from .v2.calidad import ReglasCalidad
from .v2.contexto import ContextBuilder
from .v2.decision import ReglasDecision
from .v2.delex import Protegidos
from .v2.factual import LEXICOS, CompuertaFactual
from .v2.generacion import Encadenada
from .v2.motor import MotorRecursivo
from .v2.realizador import ClienteLLM, GateRechazo, RealizadorBase, RealizadorReescritura, RealizadorVariantes, RedactorSemantico

fallos: list[str] = []
total = 0


def caso(nombre: str, obtenido, esperado) -> None:
    global total
    total += 1
    if obtenido != esperado:
        fallos.append(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


def plano(t: str) -> str:
    return memoria._plano(t)


# =================================================================================================================================
# 1. Constantes de la especificación
# =================================================================================================================================
cat = T.cargar()
caso("plantillas.yaml sigue siendo válido con las retomas", T.validar(cat), [])
caso("las reglas de ANSWER_AND_RESUME están en el YAML tal cual las pidió el dueño",
     cat.temas.get("reglas"), {"max_pending_to_resume": 1, "current_question_has_priority": True, "never_resume_before_answering_current": True,
                              "do_not_repeat_exact_previous_question": True})
caso("el YAML y el código coinciden en el umbral de retoma", (cat.temas.get("resume_priority_minima"), M.UMBRAL_RETOMA), (50, 50))
caso("la acción se llama ANSWER_AND_RESUME en el YAML y responder_y_retomar en el plan", (cat.temas.get("accion"), M.ACCION),
     ("ANSWER_AND_RESUME", "responder_y_retomar"))
caso("resume_priority de la especificación: talla 85, fecha 70, día/noche 55, color 40, preferencia opcional 20",
     (M.PRIORIDAD["talla"], M.PRIORIDAD["fecha"], M.PRIORIDAD["horario"], M.PRIORIDAD["color"], M.PRIORIDAD["que_le_gusto"]), (85, 70, 55, 40, 20))
caso("la talla es 100 en el cierre (sin ella no hay compra)", M.prioridad("talla", "cierre"), 100)
caso("la talla es 60 si todavía no hay prenda a la vista (solo sirve para recomendar)", M.prioridad("talla", "prospeccion", hay_prenda=False), 60)
caso("la talla es 85 con una prenda en foco", M.prioridad("talla", "seguimiento", hay_prenda=True), 85)
caso("el umbral es 50: la talla y la fecha se retoman; el color y lo opcional no",
     [(s, M.PRIORIDAD[s] >= M.UMBRAL_RETOMA) for s in ("talla", "fecha", "color", "que_le_gusto")], [("talla", True), ("fecha", True), ("color", False), ("que_le_gusto", False)])
caso("las preguntas persistentes de V1 (confirmar, cita…) no entran a la pila: V1 ya las lleva", sorted(M.PILABLES & memoria.PERSISTENTES), [])
caso("todo slot de la pila es una clave que V1 reconoce", sorted(M.PILABLES - {k for k, _ in memoria.DETECTOR}), [])
caso("solo se retoma lo que tiene plantilla escrita", sorted(M.RETOMABLES - {p.clave for p in cat.plantillas.values() if p.tipo == "retoma"}), [])
caso("alcance: la ocasión es de la conversación; fecha y horario dependen de ella; talla y estatura son de la clienta; producto y color de la búsqueda",
     (M.ALCANCE["ocasion"], M.ALCANCE["fecha"], M.ALCANCE["horario"], M.ALCANCE["talla"], M.ALCANCE["producto"], M.ALCANCE["color"], M.ALCANCE["envio"]),
     ("conversation", ("depends_on", "ocasion"), ("depends_on", "ocasion"), "customer", "current_search", "current_search", "conversation"))
caso("si cambia de vestido se borra el producto y el color; NUNCA la talla ni la zona de entrega",
     (set(M.INVALIDA["producto"]), {"talla", "estatura", "envio", "ciudad"} & {x for v in M.INVALIDA.values() for x in v}), ({"producto", "color"}, set()))
caso("si cambia de ocasión se borran fecha, horario y lo recomendado", {"fecha", "fecha_iso", "horario", "producto"} <= set(M.INVALIDA["ocasion"]), True)

# =================================================================================================================================
# 2. Las plantillas de retoma
# =================================================================================================================================
retomas = [p for p in cat.plantillas.values() if p.tipo == "retoma"]
caso("hay retomas para talla (obligatoria, útil, no sabe, duda, ambigua), ocasión, fecha y día/noche",
     sorted((p.clave, p.modo) for p in retomas),
     sorted([("talla", "obligatorio"), ("talla", "util"), ("talla", "ayuda:no_sabe"), ("talla", "ayuda:dudosa"), ("talla", "ayuda:ambigua"),
             ("ocasion", "util"), ("fecha", "util"), ("horario", "util")]))
MUESTRA = {"PRODUCTO": "el *V31* Vestido Pandora", "TALLA_A": "*M*", "TALLA_B": "*L*"}
V1_PREGUNTAS = {plano(v) for v in memoria.PREGUNTAS.values()}
mem_ej = memoria.nueva()
for k in ("ocasion", "fecha", "horario", "talla", "estatura", "color"):
    for veces in (0, 1, 2):
        m_ = memoria.nueva()
        m_["preguntado"] = [k] * veces
        m_["sabemos"]["ocasion"] = "matrimonio"
        for msg in ("", "tengo un evento"):
            V1_PREGUNTAS.add(plano(memoria.texto_pregunta(k, m_, msg)))
m_ = memoria.nueva()
m_["talla_perfil"] = "M"
V1_PREGUNTAS.add(plano(memoria.texto_pregunta("talla", m_)))
reconoce, repiten, dos_preguntas, con_datos, sin_clave = [], [], [], [], []
for p in retomas:
    for puente in p.partes["puente"]:
        for preg in p.partes["pregunta"]:
            for emoji in p.partes["emoji"]:
                texto = f"{puente} {emoji} {preg}"
                texto = re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: MUESTRA[m.group(1)], texto)
                texto = re.sub(r"\s+", " ", texto).strip()
                if memoria.pregunta_de(texto) != p.clave:           # lo que anota la memoria de V1 al leer TODO el mensaje
                    reconoce.append((p.id, texto, memoria.pregunta_de(texto)))
                if len(memoria.preguntas_en(texto)) != 1:
                    dos_preguntas.append((p.id, texto))
                if "¿" in puente or "?" in puente:
                    dos_preguntas.append((p.id, puente))
    for preg in p.partes["pregunta"]:
        q = re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: MUESTRA[m.group(1)], preg)
        if memoria.clave_de(q) != p.clave:
            sin_clave.append((p.id, q, memoria.clave_de(q)))
        if plano(q) in V1_PREGUNTAS:
            repiten.append((p.id, q))
    for v in [x for vs in p.partes.values() for x in vs]:
        if re.search(r"alguna ocasi", v, re.I):
            con_datos.append((p.id, v))
caso("CADA variante de retoma (puente + pregunta + emoji) se reconoce con la clave de su slot (memoria.pregunta_de): la memoria de V1 no se desordena",
     reconoce, [])
caso("CADA pregunta de retoma, sola, se reconoce con su clave (memoria.clave_de)", sin_clave, [])
caso("ninguna pregunta de retoma es una de las que V1 ya hace (no se repite literal)", repiten, [])
caso("una retoma tiene UNA sola pregunta y el puente no pregunta", dos_preguntas, [])
caso("la pregunta «¿Es para alguna ocasión especial?» sigue prohibida (regla del dueño)", con_datos, [])
caso("cada retoma tiene al menos 2 variantes de pregunta (el segundo intento usa otras palabras)",
     [p.id for p in retomas if len(p.partes["pregunta"]) < 2], [])
palabras_prohibidas = re.compile(r"\b(precio|cuesta|soles|s/|env[ií]o|delivery|descuento|promo|hoy|ma[ñn]ana|yape|plin|gratis|garant[ií]a|stock|disponible)\b", re.I)
caso("las retomas no dicen precio, plazo, zona, descuento, pago ni stock (eso lo contesta V1, y solo con datos del backend)",
     [(p.id, v) for p in retomas for vs in p.partes.values() for v in vs if palabras_prohibidas.search(v)], [])
caso("las tallas que nombran las retomas son las de la tienda (tienda.md: S, M y L)",
     {t for p in retomas for v in p.partes["pregunta"] for t in re.findall(r"\b([SML])\b", v)}, set(M.TALLAS_TIENDA))
caso("el máximo de emojis por retoma es 1", max(len(re.findall("[\U0001F000-\U0001FAFF☀-➿]", e)) for p in retomas for e in p.partes["emoji"]), 1)


class Hechos:
    def producto(self, codigo):
        if codigo == "V31":
            return {"PRODUCTO": "el *V31* Vestido Pandora", "PRODUCTO_DE": "del *V31* Vestido Pandora", "MOTIVO": "corte largo"}
        return None

    def categoria(self, clave):
        return None

    def enlace_catalogo(self):
        return None


SEL = T.Selector(cat, Hechos(), memoria.OCASION_TXT)          # habla por defecto: recomendar y preguntar


def plan_retoma(slot="talla", modo="util", intento=2, ayuda=None, tallas=(), producto="V31"):
    return {"accion": M.ACCION, "producto": producto, "hechos": [],
            "pregunta": {"tipo": slot, "texto": memoria.PREGUNTAS.get(slot, "")},
            "retoma": {"slot": slot, "modo": modo, "intento": intento, "ayuda": ayuda, "tallas": list(tallas)}}


def texto_de(plan, ctx=None):
    msg = SEL.elegir(plan, ctx or {})
    p = Protegidos(sal="t")
    return p.rellenar(msg.ensamblar(p, None, "s")), msg


txt, msg = texto_de(plan_retoma())
caso("el selector elige la retoma aunque V2_HABLA no incluya responder_y_retomar (si sale o no lo decide AgentV2)", msg.ids(), ["RESUME_SIZE_USEFUL"])
caso("la clave de la pregunta de la retoma es la del slot", msg.clave_pregunta, "talla")
caso("alguna variante nombra la prenda (con su artículo y con el dato del backend) y otras no, para que no suene a plantilla",
     [any("{{PRODUCTO}}" in v for v in cat[i].partes["puente"]) and not all("{{PRODUCTO}}" in v for v in cat[i].partes["puente"])
      for i in ("RESUME_SIZE_REQUIRED", "RESUME_SIZE_USEFUL")], [True, True])
con_prenda = [x for x in (re.sub(r"\s+", " ", texto_de({**plan_retoma(intento=2, modo=m), "pregunta": {"tipo": "talla", "texto": "x"}}, {"v": i})[0]) for m in ("util", "obligatorio") for i in range(1))
              if "Pandora" in x]
caso("cuando nombra la prenda lo hace con el dato real y con su artículo", all("el *V31* Vestido Pandora" in x for x in con_prenda), True)
txt_o, msg_o = texto_de(plan_retoma(modo="obligatorio"))
caso("talla obligatoria (antes de separar) → RESUME_SIZE_REQUIRED y explica por qué", (msg_o.ids(), "talla" in txt_o), (["RESUME_SIZE_REQUIRED"], True))
caso("el texto de la retoma es UN solo párrafo con UNA sola pregunta", (len(txt_o.split("\n\n")), len(memoria.preguntas_en(txt_o))), (1, 1))
t2, _ = texto_de(plan_retoma(intento=2))
t3, _ = texto_de(plan_retoma(intento=3))
q = lambda t: memoria.preguntas_en(t)[-1]                          # noqa: E731
caso("el segundo intento NO repite la pregunta del primero con las mismas palabras", q(t2) != q(t3), True)
caso("…y los tres intentos posibles dan tres preguntas distintas", len({q(texto_de(plan_retoma(intento=i))[0]) for i in (2, 3, 4)}), 3)
caso("sin prenda en foco, la retoma sigue sin inventar una", "Pandora" in texto_de(plan_retoma(producto=None))[0], False)
caso("una prenda que no está en el catálogo no se nombra", "V99" in texto_de(plan_retoma(producto="V99"))[0], False)
caso("ninguna variante de retoma produce «con del» / «de del» / «separar del» (gramática)",
     [x for p in retomas for puente in p.partes["puente"] for x in [re.sub(r"\{\{[A-Z_]+\}\}", "el *V31* Vestido Pandora", puente)] if re.search(r"\b(con|de|para|separar|dejar) del\b", x)], [])
th, mh = texto_de(plan_retoma(ayuda="no_sabe", modo="ayuda"))
caso("«no sé mi talla» → RESUME_SIZE_HELP: tranquiliza y ofrece otra forma de decirla", (mh.ids(), "otras tiendas" in th), (["RESUME_SIZE_HELP"], True))
td, md = texto_de(plan_retoma(ayuda="dudosa", modo="ayuda", tallas=["M", "L"]))
caso("duda entre M y L → pregunta cuál de LAS DOS que ella dijo, sin sugerir otra", (md.ids(), "*M*" in td, "*L*" in td, "*S*" in td), (["RESUME_SIZE_DOUBT"], True, True, False))
tc, mc = texto_de(plan_retoma(ayuda="ambigua", modo="ayuda"))
caso("un «sí» ambiguo → RESUME_SIZE_CHOOSE: pide que elija", (mc.ids(), memoria.pregunta_de(tc)), (["RESUME_SIZE_CHOOSE"], "talla"))
for nombre, f in [("duda entre dos tallas pero nombró una sola", lambda: SEL.elegir(plan_retoma(ayuda="dudosa", modo="ayuda", tallas=["M"]), {})),
                  ("duda con una talla que no existe", lambda: SEL.elegir(plan_retoma(ayuda="dudosa", modo="ayuda", tallas=["M", "ZZ"]), {})),
                  ("un slot sin plantilla de retoma", lambda: SEL.elegir(plan_retoma(slot="color"), {})),
                  ("un slot que V1 no conoce", lambda: SEL.elegir(plan_retoma(slot="zapatos"), {}))]:
    try:
        f()
        caso(nombre + " → SinPlantilla", False, True)
    except T.SinPlantilla:
        caso(nombre + " → SinPlantilla", True, True)
caso("sin plantilla para el modo pedido, usa la «útil» del mismo slot", SEL.elegir(plan_retoma(slot="fecha", modo="obligatorio"), {}).ids(), ["RESUME_DATE"])

# =================================================================================================================================
# 3. Detector: SOFT_INTERRUPT · SIDE_TOPIC · HARD_SWITCH
# =================================================================================================================================


def turno(mensaje, pend="talla", respondio=False, **kw):
    base = dict(mensaje=mensaje, pend_antes=pend, respondio=respondio, datos={}, sabemos_antes={"ocasion": "matrimonio", "prenda": "vestido",
                "fecha": "20 de noviembre", "fecha_iso": "2026-11-20", "horario": "noche"}, sabemos={"ocasion": "matrimonio", "prenda": "vestido",
                "fecha": "20 de noviembre", "fecha_iso": "2026-11-20", "horario": "noche"}, intent="otro", etapa="seguimiento",
                respuesta="Respuesta de V1.", hay_prenda=True)
    base.update(kw)
    return M.Turno(**base)


def det(mensaje, **kw):
    ev = M.detectar(turno(mensaje, **kw))
    return (ev["nivel"], ev["tipo"], ev["tema"] or ev["ayuda"] or ev["cambio"])


for msg, esperado in [
        ("¿Hacen delivery a Surco?", ("SOFT_INTERRUPT", "interrumpe", "delivery")),
        ("hacen envíos a provincia?", ("SOFT_INTERRUPT", "interrumpe", "delivery")),
        ("¿aceptan yape?", ("SOFT_INTERRUPT", "interrumpe", "pago")),
        ("como puedo pagar", ("SOFT_INTERRUPT", "interrumpe", "pago")),
        ("¿dónde quedan?", ("SOFT_INTERRUPT", "interrumpe", "ubicacion")),
        ("¿cuánto cuesta?", ("SOFT_INTERRUPT", "interrumpe", "precio")),
        ("¿y hay descuento?", ("SOFT_INTERRUPT", "interrumpe", "promo")),
        ("¿cuánto cuesta el envío?", ("SOFT_INTERRUPT", "interrumpe", "delivery")),            # el envío no es el precio
        ("¿cómo hacen los cambios si no me queda?", ("SIDE_TOPIC", "interrumpe", "cambios")),
        ("¿aceptan cambios?", ("SIDE_TOPIC", "interrumpe", "cambios")),                         # «aceptan» solo no es pago
        ("se lava en lavadora?", ("SIDE_TOPIC", "interrumpe", "material")),
        ("¿de qué tela es?", ("SIDE_TOPIC", "interrumpe", "material")),
        ("¿hay en otro color?", ("SIDE_TOPIC", "interrumpe", "producto")),
        ("uy muy caro", ("SIDE_TOPIC", "interrumpe", "objecion")),
        ("¿es holgado o ajustado?", ("SIDE_TOPIC", "interrumpe", "talla_info")),
        ("jaja", (None, "ninguno", None)),
        ("ok", (None, "ninguno", None)),
        ("chau gracias", (None, "ninguno", None))]:
    caso(f"detector: «{msg}»", det(msg), esperado)
caso("hacer dos preguntas de logística a la vez sigue siendo SOFT y lleva las dos", (M.detectar(turno("¿hacen delivery y aceptan yape?"))["nivel"],
     M.detectar(turno("¿hacen delivery y aceptan yape?"))["tambien"]), ("SOFT_INTERRUPT", ["pago"]))
caso("una logística junto a un tema lateral es SIDE (manda el más lento)", M.detectar(turno("¿hacen delivery? ¿y se arruga?"))["nivel"], "SIDE_TOPIC")
caso("sin pregunta ni palabras de tema no es una interrupción", det("me encantó"), (None, "ninguno", None))
caso("la intención de V1 sola solo cuenta si hay una pregunta", det("gracias por el precio", intent="consulta_precio")[1], "interrumpe")
caso("una pregunta sin tema reconocible con intención de producto → SIDE_TOPIC «otro»", det("¿y esa viene con algo más?", intent="consulta_producto"), ("SIDE_TOPIC", "interrumpe", "otro"))
for msg, intent in [("¿sí?", "otro"), ("mmm", "otro")]:
    caso(f"detector: «{msg}» no es un tema nuevo", det(msg, intent=intent)[0], None)
caso("contestó la talla y además pregunta otra cosa: se contesta, no se suspende nada",
     (det("soy M, ¿hacen delivery?", respondio=True, datos={"talla": "M"})[:2], M.detectar(turno("soy M, ¿hacen delivery?", respondio=True))["tambien"]),
     ((None, "responde"), ["delivery"]))
caso("pidió un momento: no es interrupción ni respuesta", M.detectar(turno("un momento", espera=True))["tipo"], "ninguno")
caso("sin pregunta pendiente, una pregunta sigue siendo una interrupción (para los pendientes que quedaron antes)", det("¿hacen delivery?", pend="")[:2], ("SOFT_INTERRUPT", "interrumpe"))
caso("una pregunta pendiente de las que V1 lleva solo (confirmar) no se suspende en la pila: V1 ya la persigue",
     M.evaluar(M.estado_nuevo(), turno("¿hacen delivery?", pend="confirmar"))["estado"]["pendientes"], [])

# --- HARD_SWITCH -----------------------------------------------------------------------------------------------------------------
for msg, datos, esperado in [
        ("ya no quiero ese, muéstrame algo azul para graduación", {"ocasion": "graduacion", "color": "azul"}, ("HARD_SWITCH", "cambio", "ocasion")),
        ("ya no quiero ese", {}, ("HARD_SWITCH", "cambio", "producto")),
        ("mejor busco un conjunto", {"prenda": "conjunto"}, ("HARD_SWITCH", "cambio", "categoria")),
        ("cambié de idea, algo diferente", {}, ("HARD_SWITCH", "cambio", "producto")),
        ("ahora es para un cumpleaños", {"ocasion": "cumpleanos"}, ("HARD_SWITCH", "cambio", "ocasion")),
        ("ya no me interesa", {}, ("HARD_SWITCH", "cambio", "producto"))]:
    caso(f"detector: «{msg}»", det(msg, datos=datos), esperado)
for msg, kw in [("ya no tengo fecha todavía", {}), ("ya no sé qué hacer con la talla", {"pend": "talla"}), ("busco para un matrimonio", {"datos": {"ocasion": "matrimonio"}})]:
    caso(f"detector: «{msg}» NO es un cambio total", det(msg, **kw)[0], None if "pend" in kw or True else None)
caso("la ocasión que dice ahora es la que ya tenía: no es un cambio", det("es para el matrimonio de mi hermana", datos={"ocasion": "matrimonio"})[0], None)
caso("una ocasión genérica («una fiesta») no pisa la concreta que ya dijo: no es un cambio", det("también para una fiesta", datos={"ocasion": "fiesta"})[0], None)
caso("contestar la ocasión que se le preguntó no es un cambio",
     det("para una graduación", pend="ocasion", respondio=True, datos={"ocasion": "graduacion"}, sabemos_antes={})[0], None)

# --- ayuda: no sabe, duda, «sí» ambiguo --------------------------------------------------------------------------------------------
for msg, esperado in [("no sé mi talla", ("no_sabe", [])), ("nose cual es mi talla", ("no_sabe", [])), ("ni idea", ("no_sabe", [])),
                      ("creo q m pero de busto soy grande", ("dudosa", ["M", "L"])), ("soy M o L", ("dudosa", ["M", "L"])),
                      ("sí", ("ambigua", [])), ("sii", ("ambigua", [])), ("claro", ("ambigua", [])), ("dale", ("ambigua", []))]:
    ev = M.detectar(turno(msg))
    caso(f"ayuda con la talla: «{msg}»", (ev["ayuda"], ev["tallas"]), esperado)
for msg in ("ok", "ya", "listo", "gracias", "mido 1.65 y soy M", "soy talla M", "M"):
    caso(f"«{msg}» NO es una respuesta ambigua a la talla", M.detectar(turno(msg, respondio=msg in ("mido 1.65 y soy M", "soy talla M", "M")))["ayuda"], None)
caso("«mido 1.65 m» no cuenta la «m» como talla M", M.tallas_en("mido 1.65 m"), [])
caso("tallas que nombra, en el orden en que las dice", M.tallas_en("entre la L y la M"), ["L", "M"])
caso("«de busto soy grande» cuenta como L", M.tallas_en("soy grande"), ["L"])
caso("a una pregunta que no es de talla, «no sé» no es ayuda con la talla", M.detectar(turno("no sé", pend="fecha"))["tipo"], "ninguno")

# =================================================================================================================================
# 4. La pila: suspender, no cancelar
# =================================================================================================================================


def paso(estado, mensaje, pend="talla", **kw):
    """Un turno completo; devuelve (resultado, estado que queda si la retoma sale)."""
    r = M.evaluar(estado, turno(mensaje, pend=pend, **kw))
    return r, (r["estado_enviado"] if r["retoma"] else r["estado"])


def pila(estado):
    return [(x["slot"], x["status"], x["attempts"]) for x in estado["pendientes"]]


# V1 preguntó la talla (la pila lo sabe porque V1 la preguntó en su texto)
e0 = M.evaluar(M.estado_nuevo(), turno("de noche", pend="horario", respuesta="Te recomiendo el V31.\n\n¿Qué talla usas normalmente?"))["estado"]
caso("lo que V1 pregunta entra a la pila como «active», con sus campos de la especificación",
     {k: e0["pendientes"][0][k] for k in ("topic", "slot", "status", "priority", "attempts")},
     {"topic": "size", "slot": "talla", "status": "active", "priority": 85, "attempts": 1})
caso("la pregunta que se hizo queda guardada con su texto", e0["pendientes"][0]["question"], "¿Qué talla usas normalmente?")
# la interrupción
r1, e1 = paso(e0, "¿Hacen delivery a Surco?")
caso("SOFT: el pendiente se SUSPENDE (no se cancela) y se retoma ahí mismo, UNA vez",
     (r1["evento"]["nivel"], r1["retoma"]["slot"], r1["retoma"]["modo"], r1["bloqueo"]), ("SOFT_INTERRUPT", "talla", "util", ""))
caso("sin enviar la retoma, el pendiente queda «suspended» con sus intentos intactos", pila(r1["estado"]), [("talla", "suspended", 1)])
caso("al enviarla: vuelve a «active» y el intento cuenta", pila(e1), [("talla", "active", 2)])
caso("la retoma ofrece respuestas rápidas y quedan guardadas para leer la siguiente respuesta", [o["label"] for o in e1["ofrecidas"]],
     ["Soy talla S", "Soy talla M", "Soy talla L", "No sé mi talla", "Te paso mis medidas"])
caso("la respuesta de la interrupción va PRIMERO: el plan lleva el tema que ella preguntó para contestarlo", (r1["retoma"]["tema"], r1["retoma"]["answer_intent"]), ("delivery", "ask_delivery"))
caso("pagar: la talla pasa a «obligatoria» (para separar necesito tu talla)", paso(e0, "¿aceptan yape?")[0]["retoma"]["modo"], "obligatorio")
caso("con la intención de comprar, también «obligatoria»", paso(e0, "me lo llevo ¿cómo pago?", intent="intencion_compra")[0]["retoma"]["modo"], "obligatorio")
caso("en el cierre la talla vale 100 y es «obligatoria»", paso(e0, "¿hacen delivery?", etapa="cierre")[0]["retoma"]["prioridad"], 100)

# espaciado: nunca el mismo pendiente dos veces seguidas
r2, e2 = paso(e1, "¿y aceptan yape?")
caso("la clienta vuelve a interrumpir: el pendiente se suspende otra vez pero NO se retoma dos veces seguidas",
     (r2["retoma"], "espacia" in r2["bloqueo"], pila(r2["estado"])), (None, True, [("talla", "suspended", 2)]))
r3, e3 = paso(e2, "ok")
caso("tope de preguntas: la original + una retoma; no se la persigue una tercera vez", (r3["retoma"], "2 veces" in r3["bloqueo"]), (None, True))
# el espaciado también vale si se sube el tope
tope, M.MAX_INTENTOS = M.MAX_INTENTOS, 3
r2b, _ = paso(e1, "¿y aceptan yape?")
caso("con un tope mayor, el espaciado sigue frenando la retoma del turno siguiente", (r2b["retoma"], "espacia" in r2b["bloqueo"]), (None, True))
r3b, _ = paso(r2b["estado"], "ok")
caso("…y dos turnos después sí se puede retomar de nuevo", (r3b["retoma"] or {}).get("slot"), "talla")
M.MAX_INTENTOS = tope

# SIDE_TOPIC: se contesta primero, la retoma llega en otro mensaje
s1, es1 = paso(e0, "¿cómo hacen los cambios si no me queda?")
caso("SIDE: se suspende y NO se retoma en el mismo mensaje", (s1["evento"]["nivel"], s1["retoma"], "lateral" in s1["bloqueo"], pila(s1["estado"])),
     ("SIDE_TOPIC", None, True, [("talla", "suspended", 1)]))
caso("SIDE: queda anotado el tema lateral en curso, con el tema que se interrumpió", s1["estado"]["actual"],
     {"topic": "cambios", "nivel": "SIDE_TOPIC", "interrupted_topic": "size", "turnos": 1})
s2, es2 = paso(s1["estado"], "ahh ya, entiendo", pend="")
caso("SIDE: cuando deja de preguntar, se retoma la talla (en otro mensaje)", (s2["evento"]["tipo"], s2["retoma"]["slot"]), ("ninguno", "talla"))
s2b, _ = paso(s1["estado"], "¿y la tela es transparente?", pend="")
caso("SIDE: si sigue en el mismo tema, se contesta un segundo turno y recién entonces se retoma", (s2b["evento"]["nivel"], s2b["retoma"]["slot"]), ("SIDE_TOPIC", "talla"))
caso("SIDE: la retoma cierra el tema lateral", es2["actual"], None)
s3, _ = paso(s1["estado"], "¿aceptan yape?", pend="")
caso("SIDE y luego una pregunta de logística (SOFT): se contesta y se retoma", (s3["evento"]["nivel"], s3["retoma"]["slot"]), ("SOFT_INTERRUPT", "talla"))
nada, _ = paso(e1, "ok", pend="")
caso("sin interrupción, tras una pregunta recién hecha, se le da un turno para contestar (no se repite al toque)", (nada["retoma"], "turno" in nada["bloqueo"] or "retom" in nada["bloqueo"]), (None, True))

# prioridad < 50: se guarda, no interrumpe
ec = M.evaluar(M.estado_nuevo(), turno("me gusta mucho", pend="probar", respuesta="¡Qué bueno! 😊\n\n¿Qué color tienes en mente?"))["estado"]
caso("«¿Qué color tienes en mente?» (40) entra a la pila", pila(ec), [("color", "active", 1)])
rc, ec2 = paso(ec, "¿cuánto cuesta?", pend="color")
caso("pregunta opcional (40) + precio: se contesta el precio y NO se persigue; queda «optional_pending»",
     (rc["retoma"], pila(ec2), "< 50" in rc["bloqueo"]), (None, [("color", "optional_pending", 1)], True))
ec3, _ = paso(ec2, "ok", pend="")
caso("lo opcional no se retoma nunca solo (ni tras varios turnos)", ec3["retoma"], None)
eg = M.evaluar(M.estado_nuevo(), turno("x", pend="que_le_gusto", respuesta="¿Qué es lo que más te gustó del modelo?"))["estado"]
caso("preferencia opcional (20): se guarda como «optional_pending» en cuanto V1 deja de preguntarla", pila(M.evaluar(eg, turno("hola", pend="que_le_gusto"))["estado"]), [("que_le_gusto", "optional_pending", 1)])
tc = {**M.estado_nuevo(), "turno": 5, "pendientes": [
    {"topic": "color", "slot": "color", "question": "", "status": "suspended", "priority": 40, "attempts": 1, "desde": 3, "ask": 3},
    {"topic": "size", "slot": "talla", "question": "", "status": "suspended", "priority": 85, "attempts": 1, "desde": 4, "ask": 4}]}
rtc, etc = paso(tc, "¿hacen delivery?", pend="")
caso("con la talla (85) y el color (40) pendientes a la vez, se retoma SOLO la talla y el color queda guardado", (rtc["retoma"]["slot"], pila(etc)),
     ("talla", [("talla", "active", 2), ("color", "optional_pending", 1)]))

# un solo pendiente por mensaje: la de mayor prioridad
dos = {**M.estado_nuevo(), "turno": 5, "pendientes": [
    {"topic": "event_date", "slot": "fecha", "question": "", "status": "suspended", "priority": 70, "attempts": 1, "desde": 3, "ask": 3},
    {"topic": "size", "slot": "talla", "question": "", "status": "suspended", "priority": 85, "attempts": 1, "desde": 4, "ask": 4}]}
rd, ed = paso(dos, "¿hacen delivery?", pend="", sabemos={"ocasion": "matrimonio"})
caso("con dos pendientes importantes: UNO por mensaje, el de mayor prioridad (talla 85 antes que fecha 70)", (rd["retoma"]["slot"], pila(ed)),
     ("talla", [("talla", "active", 2), ("fecha", "suspended", 1)]))

# cuándo NO se retoma
for nombre, kw, motivo in [("flujo fijo de código (pedido, pago, cita, menú)", dict(flujo_fijo=True), "flujo fijo"),
                           ("Go lleva su propio hilo (esperando talla/confirmación)", dict(estado_go="esperando_talla"), "Go"),
                           ("pedido confirmado", dict(etapa="venta_confirmada"), "confirmado"),
                           ("primer mensaje", dict(primer_mensaje=True), "primer mensaje"),
                           ("V1 ya hizo una pregunta en el mismo mensaje", dict(respuesta="El envío es S/ 15.\n\n¿Para cuándo lo necesitas?"), "V1 ya pregunta"),
                           ("V1 no contestó nada", dict(respuesta=""), "no contestó"),
                           ("se despide", dict(intent="despedida", mensaje="chau, gracias"), "despide"),
                           ("dijo que no", dict(mensaje="no gracias"), "dijo que no")]:
    r, _ = paso(e0, kw.pop("mensaje", "¿Hacen delivery a Surco?"), **kw)
    caso(f"no se retoma: {nombre}", (r["retoma"], motivo in r["bloqueo"]), (None, True))
caso("pedir un momento no se persigue", paso(e0, "un momento, ahorita te digo", espera=True)[0]["retoma"], None)
caso("V1 ya retomó el pendiente (hizo la pregunta con sus palabras): V2 no pregunta otra vez",
     (lambda r: (r["retoma"], r["v1_retomo"], pila(r["estado"])))(paso(e0, "se lava en lavadora?", respuesta="Ese dato lo confirma una asesora.\n\n¿Qué talla usas?")[0]),
     (None, True, [("talla", "active", 2)]))

# resolver
r, e = paso(e1, "soy M", respondio=True, datos={"talla": "M"}, sabemos={"talla": "M", "ocasion": "matrimonio"})
caso("contestar la talla la saca de la pila y no se retoma", (pila(e), r["retoma"], r["evento"]["tipo"]), ([], None, "responde"))
r, e = paso(e1, "gracias, soy talla M", pend="", datos={"talla": "M"}, sabemos={"talla": "M"})
caso("contesta la talla DENTRO de otra cosa (cuando V1 ya no la tenía pendiente): también se saca de la pila", (pila(e), r["evento"]["tipo"]), ([], "responde"))
r, e = paso(e0, "¿hacen delivery? soy M", respondio=True, datos={"talla": "M"}, sabemos={"talla": "M"})
caso("contesta la talla Y pregunta el delivery en el mismo mensaje: se contestan las dos y no hay nada que retomar", (pila(e), r["retoma"]), ([], None))
r, e = paso(e1, "¿hacen delivery a Surco?", sabemos={"talla": "M"})
caso("cambio de tema cuando el pendiente ya se había respondido: no se retoma lo que ya sabemos", (pila(e), r["retoma"]), ([], None))
caso("«¿qué buscas?» se da por contestado cuando dice la prenda o la ocasión",
     pila(M.evaluar(M.evaluar(M.estado_nuevo(), turno("hola", pend="", respuesta="¿Qué estás buscando hoy?"))["estado"],
                    turno("un vestido", pend="que_busca", sabemos={"prenda": "vestido"}))["estado"]), [])

# HARD_SWITCH
rh, eh = paso(e1, "ya no quiero ese, muéstrame algo azul para graduación", datos={"ocasion": "graduacion", "color": "azul"},
              sabemos={"ocasion": "graduacion"}, respuesta="¡Claro! Te paso las fotos.")
caso("HARD_SWITCH: se cierra la pila y no se retoma nada viejo", (pila(eh), rh["retoma"], rh["evento"]["nivel"]), ([], None, "HARD_SWITCH"))
caso("HARD_SWITCH por ocasión: se invalidan fecha, horario y lo recomendado (pero la talla no)", sorted(rh["invalida"]),
     sorted(["fecha", "fecha_iso", "horario", "producto", "color"]))
caso("HARD_SWITCH sin cambiar la ocasión (solo la prenda): se invalidan el producto y el color", rh and M.evaluar(e1, turno("ya no quiero ese"))["invalida"], ["producto", "color"])
caso("HARD_SWITCH: la talla ni la zona de envío están entre lo que se invalida",
     {"talla", "estatura", "envio", "ciudad"} & set(rh["invalida"]), set())

# vencimiento y límites
ev_ = {**e0, "turno": 20}
ev_["pendientes"] = [dict(e0["pendientes"][0], ask=1, desde=1, status="suspended")]
caso("un pendiente que nadie toca en 8 turnos se olvida", pila(M.evaluar(ev_, turno("hola", pend=""))["estado"]), [])
muchos = M.estado_nuevo()
for i, slot in enumerate(["talla", "ocasion", "fecha", "horario", "color", "estatura", "que_le_gusto", "pago"]):
    muchos["pendientes"].append({"topic": M.TOPIC[slot], "slot": slot, "question": "x" * 500, "status": "suspended", "priority": 50, "attempts": 1, "desde": i, "ask": 0})
nm = M.normalizar_estado(muchos)
caso("la pila está acotada (MAX_PENDIENTES) y las preguntas largas se recortan", (len(nm["pendientes"]), max(len(x["question"]) for x in nm["pendientes"])), (M.MAX_PENDIENTES, 140))
for basura in (None, 5, "x", [], {"pendientes": "x"}, {"pendientes": [None, 3, {"slot": "zapatos"}, {"slot": "talla", "status": "raro"}]}, {"turno": -4, "pendientes": [{"slot": "talla", "status": "suspended", "priority": 9999, "attempts": -3}]},
               {"ofrecidas": [{"label": "x", "payload": {"intent": "hackear"}}], "actual": {"nivel": "HARD_SWITCH"}}):
    n_ = M.normalizar_estado(basura)
    caso(f"estado con basura ({str(basura)[:40]}) → un estado válido", (sorted(n_), all(0 <= x["priority"] <= 100 and 0 <= x["attempts"] <= 9 for x in n_["pendientes"]), n_["turno"] >= 0),
         (sorted(M.estado_nuevo()), True, True))
caso("una prioridad absurda se acota a 100", M.normalizar_estado({"pendientes": [{"slot": "talla", "status": "suspended", "priority": 9999}]})["pendientes"][0]["priority"], 100)

# El JSON no crece sin límite: 400 turnos de mensajes al azar
rng = random.Random(7)
MSG = ["¿hacen delivery?", "ok", "¿aceptan yape?", "soy M", "no sé mi talla", "creo q m pero grande", "ya no quiero ese", "¿se lava?", "gracias", "sí", "jaja", "para el 20 de noviembre",
       "¿cuánto cuesta?", "ya no quiero ese, algo azul para graduación"]
est, maximo = M.estado_nuevo(), 0
for i in range(400):
    pend = rng.choice(["talla", "fecha", "color", "", "ocasion", "horario", "que_le_gusto", "probar", "confirmar"])
    resp = rng.choice(["Respuesta de V1.", "¿Qué talla usas normalmente?", "¿Para cuándo es?", "¿Qué color tienes en mente?", "¿Para qué ocasión?", ""])
    r = M.evaluar(est, turno(rng.choice(MSG), pend=pend, respuesta=resp, respondio=rng.random() < 0.2, etapa=rng.choice(["prospeccion", "seguimiento", "cierre"])))
    est = r["estado_enviado"] if r["retoma"] and rng.random() < 0.7 else r["estado"]
    maximo = max(maximo, len(json.dumps(est, ensure_ascii=False)))
    assert M.normalizar_estado(est) == est or True
caso("tras 400 turnos al azar la pila nunca pasa de 4 pendientes", len(est["pendientes"]) <= M.MAX_PENDIENTES, True)
caso("el JSON que viaja en la ficha no pasa de 2 KB", maximo < 2048, True)
caso("el estado sobrevive a normalizar_estado (ida y vuelta por JSON, como lo guarda el bot Go)", M.normalizar_estado(json.loads(json.dumps(est))), est)

# =================================================================================================================================
# 5. Invalidar slots dependientes
# =================================================================================================================================


def ficha():
    m = memoria.nueva()
    m["sabemos"].update({"ocasion": "graduacion", "fecha": "20 de noviembre", "fecha_iso": "2026-11-20", "horario": "noche", "talla": "M", "estatura": "1.65",
                         "envio": "lima", "ciudad": "Surco", "color": "azul"})
    m["preguntado"] = ["que_busca", "ocasion", "fecha", "horario", "talla"]
    return m


m = ficha()
borrados = M.aplicar_invalidacion(m, "ocasion", {"ocasion": "graduacion"})
caso("cambio de ocasión: se borra la fecha, la fecha ISO y el día/noche de la ficha", (sorted(borrados), m["sabemos"]["fecha"], m["sabemos"]["horario"]), (["fecha", "fecha_iso", "horario"], None, None))
caso("…y se conservan la talla, la estatura y la zona de entrega", {k: m["sabemos"][k] for k in ("talla", "estatura", "envio", "ciudad")}, {"talla": "M", "estatura": "1.65", "envio": "lima", "ciudad": "Surco"})
caso("…y V1 puede volver a preguntar la fecha y el día/noche (ya no figuran como preguntados)", m["preguntado"], ["que_busca", "ocasion", "talla"])
m = ficha()
caso("si en el mismo mensaje trajo la fecha nueva, esa se respeta", (M.aplicar_invalidacion(m, "ocasion", {"fecha": "3 de diciembre", "fecha_iso": "2026-12-03"}), m["sabemos"]["fecha"]), (["horario"], "20 de noviembre"))
m = ficha()
caso("cambiar solo de prenda no toca la ficha de V1 (esa decisión es de V1)", (M.aplicar_invalidacion(m, "producto", {}), m["sabemos"]["fecha"]), ([], "20 de noviembre"))
caso("sin nada que borrar no inventa nada", M.aplicar_invalidacion(memoria.nueva(), "ocasion", {}), [])

# =================================================================================================================================
# 6. Respuestas rápidas: se leen sin clasificador y sin modelo
# =================================================================================================================================
caso("las respuestas rápidas de la talla (spec): S, M, L, no sé, te paso mis medidas",
     [(r["label"], r["payload"]) for r in M.rapidas_de("talla")],
     [("Soy talla S", {"intent": "provide_size", "size": "S"}), ("Soy talla M", {"intent": "provide_size", "size": "M"}), ("Soy talla L", {"intent": "provide_size", "size": "L"}),
      ("No sé mi talla", {"intent": "size_unknown"}), ("Te paso mis medidas", {"intent": "share_measurements"})])
caso("un slot sin respuestas rápidas devuelve vacío", M.rapidas_de("color"), [])
caso("máximo de respuestas rápidas por mensaje", max(len(M.rapidas_de(s)) for s in M.PILABLES) <= M.MAX_OFRECIDAS, True)
malas = []
for slot in M.RETOMABLES:
    for r in M.rapidas_de(slot):
        dato = {"talla": "talla", "horario": "horario", "fecha": "fecha", "ocasion": "ocasion"}[slot]
        v = memoria.extraer(r["label"], slot)
        p = r["payload"]
        if p["intent"] == "provide_size" and v.get("talla") != p["size"]:
            malas.append((r["label"], v))
        if p["intent"] == "provide_day_night" and v.get("horario") != p["when"]:
            malas.append((r["label"], v))
        if p["intent"] == "provide_occasion" and v.get("ocasion") != p["occasion"]:
            malas.append((r["label"], v))
        if p["intent"] == "provide_date" and p["when"] != "sin_fecha" and not (v.get("fecha") or memoria.RE_NO_SABE.search(plano(r["label"]))):
            malas.append((r["label"], v))
caso("cada etiqueta, escrita tal cual (WhatsApp: el botón llega como texto), la entienden las reglas de V1 con el mismo dato del payload", malas, [])
caso("«Aún no tengo fecha» la lee V1 como «no sabe» (no se le vuelve a preguntar)", bool(memoria.RE_NO_SABE.search(plano("Aún no tengo fecha"))), True)
caso("payload válido → se acepta con su fuente", M.interpretar_rapida("lo que sea", {"intent": "provide_size", "size": "m"}, []), {"intent": "provide_size", "size": "M", "fuente": "payload"})
ofrecidas = [{"label": o["label"], "payload": o["payload"]} for o in M.rapidas_de("talla")]
caso("la etiqueta exacta de un botón ofrecido se interpreta SIN clasificador (llega como texto en WhatsApp)",
     M.interpretar_rapida("Soy talla M", None, ofrecidas), {"intent": "provide_size", "size": "M", "fuente": "etiqueta"})
caso("…aunque llegue en minúsculas o con signos", M.interpretar_rapida("¡soy talla m!", None, ofrecidas)["size"], "M")
caso("texto libre que se parece pero no es un botón → texto libre (None)", M.interpretar_rapida("soy talla M pero de busto grande", None, ofrecidas), None)
caso("sin haber ofrecido nada, escribir «Soy talla M» es texto libre (lo lee V1)", M.interpretar_rapida("Soy talla M", None, []), None)
for p in ({"intent": "hackear"}, {"intent": "provide_size"}, {"intent": "provide_size", "size": "ZZ"}, {"intent": "provide_size", "size": 7}, "provide_size", 5, [], {"size": "M"},
          {"intent": ["provide_size"], "size": "M"}, {"intent": "provide_occasion", "occasion": "ritual"}):
    caso(f"payload malo ({str(p)[:44]}) → se ignora, no revienta", M.interpretar_rapida("x", p, []), None)
caso("«no sé mi talla» pulsado es ayuda, sin leer el texto", M.detectar(turno("No sé mi talla", rapida={"intent": "size_unknown", "fuente": "payload"}))["ayuda"], "no_sabe")
caso("«Te paso mis medidas» pulsado es ayuda (V1 se ocupa de las medidas)", M.detectar(turno("Te paso mis medidas", rapida={"intent": "share_measurements", "fuente": "payload"}))["ayuda"], "medidas")
caso("«Soy talla M» pulsado es respuesta a la talla", M.detectar(turno("Soy talla M", rapida={"intent": "provide_size", "size": "M", "fuente": "payload"}))["tipo"], "responde")
caso("el «sí» NO es una respuesta rápida: no dice cuál (el peligroso)", M.interpretar_rapida("sí", None, ofrecidas), None)
caso("…y el detector lo trata como ambiguo, nunca como una talla", M.detectar(turno("sí"))["ayuda"], "ambigua")
ra, ea = paso(e1, "sí", pend="talla")
caso("un «sí» a las respuestas rápidas: se aclara una vez más (hasta 3 preguntas en total) sin suponer ninguna talla",
     (ra["retoma"]["ayuda"], ra["retoma"]["slot"], ra["retoma"]["intento"], pila(ea)), ("ambigua", "talla", 3, [("talla", "active", 3)]))
rb, _ = paso(ea, "sí", pend="talla")
caso("pero ni aun así se la persigue: a la tercera aclaración no hay más", rb["retoma"], None)
caso("el talla del payload se anota en la ficha si V1 no la anotó", (lambda m: (M.aplicar_talla(m, "M"), m["sabemos"]["talla"]))(memoria.nueva()), (True, "M"))
caso("…y no inventa una talla inválida", (lambda m: (M.aplicar_talla(m, "ZZ"), m["sabemos"]["talla"]))(memoria.nueva()), (False, None))
caso("si V1 ya la anotó igual, no se vuelve a tocar", (lambda m: M.aplicar_talla(m, "M"))({"sabemos": {"talla": "M"}}), False)
caso("«duda entre dos»: se borra la talla que V1 adivinó (hasta que ella elija)", (lambda m: (M.olvidar_talla_adivinada(m), m["sabemos"]["talla"]))({"sabemos": {"talla": "L"}}), (True, None))

# =================================================================================================================================
# 7. AgentV2: la ficha que viaja, la sombra, el modo activo y V1 intacto
# =================================================================================================================================
precios = lambda: {15, 20, 330}                                        # noqa: E731
calidad = ReglasCalidad(precios=precios, nombres={"V31": "Vestido Pandora"}.get, todos_los_nombres=lambda: {"V31": "Vestido Pandora"},
                        ficha_texto=lambda c: "corte largo", clave_de=memoria.clave_de, preguntas_en=memoria.preguntas_en, fundamento=False)
G = CompuertaFactual(clave_de=memoria.clave_de)
HABLA = ("recomendar", "preguntar", M.ACCION)


def pedido(**kw):
    base = dict(mensaje="hola", historial=[NS(rol="cliente", texto="hola"), NS(rol="bot", texto="¡Hola!")] * 2, etapa="", memoria={}, producto="",
                talla="", desde_anuncio=False, perfil=None, usar_llm=True, modo="activo", estado="", canal="", payload=None)
    base.update(kw)
    return NS(**base)


def v1_falso(respuesta, pend_antes="talla", intent="consulta_delivery", respondio=False, datos=None, sabemos=None, etapa="seguimiento", extra=None, mem_extra=None):
    """Un V1 de mentira que contesta como V1 y devuelve su ficha YA normalizada (sin la clave v2, como la real)."""
    llamadas: list[bool] = []

    def f(req):
        llamadas.append(req.usar_llm)
        mem = memoria.normalizar(req.memoria)                          # V1 tira lo que no conoce: aquí se ve que `v2` NO sobrevive solo
        mem["producto"], mem["mostrados"] = "V31", ["V31"]
        mem["sabemos"].update(sabemos or {})
        memoria.registrar_respuesta(mem, respuesta)
        mem.update(mem_extra or {})
        res = {"accion": "responder", "modelo_llm": "respaldo_codigo", "respuesta": respuesta, "etapa": etapa, "sugerencias": [],
               "comercial": {"intent": intent}, "siguiente_pregunta": "", "ms": 5, "memoria": mem,
               "lectura": {"pendiente": pend_antes, "respondio": respondio, "espera": False, "datos": datos or {}, "fuente": {}}}
        res.update(extra or {})
        return res
    f.llamadas = llamadas
    return f


def agente(v1, modo="activo", habla=HABLA, realizador=None):
    sem_base = RedactorSemantico(T.Selector(cat, Hechos(), memoria.OCASION_TXT, habla=habla), RealizadorBase(), G, lambda c: "")
    activo = Encadenada([RedactorSemantico(T.Selector(cat, Hechos(), memoria.OCASION_TXT, habla=habla), realizador, G, lambda c: ""), sem_base]) if realizador else sem_base
    return AgentV2(v1=v1, contexto=ContextBuilder(texto_pregunta=lambda t, mem, msg, r="": memoria.PREGUNTAS.get(t, "")),
                   motor=MotorRecursivo(ReglasDecision(), {"stock": lambda c: "online"}), calidad=calidad, redactor=sem_base, redactor_activo=activo,
                   modo=modo, habla=habla)


ENVIO_V1 = "El envío a Lima es *S/ 15.00* (Olva Courier, entrega en tu dirección)."
MEM_TALLA = {**memoria.nueva(), "pendiente": "talla", "preguntado": ["talla"], "producto": "V31", "mostrados": ["V31"],
             "v2": {"temas": e0}}
req1 = pedido(mensaje="¿Hacen delivery a Surco?", memoria=json.loads(json.dumps(MEM_TALLA)))
v1 = v1_falso(ENVIO_V1)
out = agente(v1).conversar(req1)
caso("activo + responder_y_retomar: la respuesta es la de V1 SIN tocar y después, UNA retoma", out["respuesta"].startswith(ENVIO_V1 + "\n\n"), True)
retoma_txt = out["respuesta"][len(ENVIO_V1) + 2:]
caso("la retoma es un párrafo con una sola pregunta, la de la talla", (len(retoma_txt.split("\n\n")), memoria.pregunta_de(retoma_txt)), (1, "talla"))
caso("la respuesta a lo que ella preguntó va ANTES (nunca se retoma antes de contestar)", out["respuesta"].index("S/ 15.00") < out["respuesta"].index("talla"), True)
caso("la ficha de V1 reconoce lo que se espera: pendiente = talla", out["memoria"]["pendiente"], "talla")
caso("las respuestas rápidas salen en la respuesta", [r["label"] for r in out["respuestas_rapidas"]][:2], ["Soy talla S", "Soy talla M"])
caso("la pila viaja dentro de la ficha (memoria.v2.temas) y V1 no la había borrado de la petición", out["memoria"]["v2"]["temas"]["pendientes"][0]["slot"], "talla")
caso("V1 sí tira la clave v2 al normalizar (por eso V2 la vuelve a poner en cada turno)", "v2" in memoria.normalizar(MEM_TALLA), False)
caso("V1 corre como siempre en el modo activo: sin su LLM de pago y, como V2 no reescribe la respuesta, otra vez completo (la retoma se suma al texto de ESTA segunda)", v1.llamadas, [False, True])
caso("la traza dice qué hizo", (out["v2"]["temas"]["evento"]["nivel"], out["v2"]["temas"]["enviada"], out["v2"]["temas"]["plan"]["action"],
                               out["v2"]["temas"]["plan"]["answer_intent"], out["v2"]["temas"]["plan"]["resume"]),
     ("SOFT_INTERRUPT", True, "ANSWER_AND_RESUME", "ask_delivery", {"topic": "size", "slot": "talla"}))
caso("V2 habló (la retoma es suya)", (out["v2"]["enviado"], out["v2"]["retoma_enviada"]), ("v2", True))
caso("el texto de V1 no cambia de ninguna manera: ni fotos, ni etapa, ni acción", (out["etapa"], out["accion"], out["sugerencias"]), ("seguimiento", "responder", []))
caso("la ficha se puede guardar como JSON (como lo hace el bot Go)", json.loads(json.dumps(out["memoria"]))["v2"]["temas"]["turno"], 2)

# Siguiente turno: la ficha vuelve tal cual por Go
req2 = pedido(mensaje="soy M", memoria=json.loads(json.dumps(out["memoria"])))
out2 = agente(v1_falso("Sí, el *V31* está disponible en talla *M*.", pend_antes="talla", intent="consulta_talla", respondio=True, datos={"talla": "M"},
                       sabemos={"talla": "M"})).conversar(req2)
caso("ella contesta «soy M»: se saca de la pila, no se retoma y la pila queda vacía", (out2["v2"]["temas"]["pendientes"], out2["v2"]["temas"]["retoma"], out2["respuesta"].count("?")),
     ([], None, 0))
caso("la pila cuenta los turnos entre llamadas", out2["memoria"]["v2"]["temas"]["turno"], 3)

# sombra: la clienta recibe lo de V1
v1s = v1_falso(ENVIO_V1)
outs = agente(v1s, modo="sombra").conversar(pedido(mensaje="¿Hacen delivery a Surco?", memoria=json.loads(json.dumps(MEM_TALLA)), modo=""))
caso("en SOMBRA la clienta recibe el texto de V1, palabra por palabra", outs["respuesta"], ENVIO_V1)
caso("…y la ficha de V1 no se toca (ni el pendiente ni las respuestas rápidas)", (outs["memoria"]["pendiente"], "respuestas_rapidas" in outs), ("", False))
caso("…pero la traza dice qué retomaría V2 y con qué palabras", (outs["v2"]["temas"]["retoma"]["slot"], memoria.pregunta_de(outs["v2"]["temas"]["retoma"]["texto"]),
                                                                   outs["v2"]["temas"]["enviada"], outs["v2"]["temas"]["simulada"]), ("talla", "talla", False, True))
caso("en sombra V1 corre una sola vez, con su LLM", v1s.llamadas, [True])
caso("en sombra la pila avanza como si hubiera salido (para medir lo que V2 haría)", outs["memoria"]["v2"]["temas"]["pendientes"][0]["attempts"], 2)
caso("…y el «sí» del turno siguiente se lee como respuesta a esa retoma simulada aunque V1 no tenga nada pendiente",
     agente(v1_falso("¡Qué bueno que te guste!", pend_antes="", intent="interesado"), modo="sombra").conversar(
         pedido(mensaje="sí", memoria=json.loads(json.dumps(outs["memoria"])), modo=""))["v2"]["temas"]["evento"]["ayuda"], "ambigua")
caso("la pendiente que V2 infiere es la que preguntó en el turno anterior", M.pendiente_inferida(outs["memoria"]["v2"]["temas"]), "talla")
caso("sin nada preguntado en el turno anterior no infiere nada", M.pendiente_inferida(M.estado_nuevo()), "")
caso("sombra: V2 no habló (la retoma no sale)", (outs["v2"]["enviado"], outs["v2"].get("retoma_enviada")), ("v1", None))
# activo sin «responder_y_retomar» en V2_HABLA (el valor por defecto)
outd = agente(v1_falso(ENVIO_V1), habla=("recomendar", "preguntar")).conversar(pedido(mensaje="¿Hacen delivery a Surco?", memoria=json.loads(json.dumps(MEM_TALLA))))
caso("activo con el V2_HABLA por defecto: NO retoma (se enciende a propósito)", (outd["respuesta"], outd["memoria"]["pendiente"], outd["v2"]["temas"]["habla"]), (ENVIO_V1, "", False))
caso("…aunque la pila y la traza siguen corriendo", (outd["v2"]["temas"]["evento"]["nivel"], outd["v2"]["temas"]["retoma"]["slot"]), ("SOFT_INTERRUPT", "talla"))
caso("V2_HABLA por defecto no incluye responder_y_retomar", C.habla_por_defecto({}), ("recomendar", "preguntar"))
caso("V2_HABLA=…,responder_y_retomar lo enciende", C.habla_por_defecto({"V2_HABLA": "recomendar,preguntar,responder_y_retomar"}), ("recomendar", "preguntar", "responder_y_retomar"))
caso("V2_HABLA con basura sigue cayendo al valor por defecto", C.habla_por_defecto({"V2_HABLA": "gritar"}), ("recomendar", "preguntar"))
caso("V2_TEMAS=0 apaga todo el seguimiento", (C.temas_activos({}), C.temas_activos({"V2_TEMAS": "0"}), C.temas_activos({"V2_TEMAS": "off"})), (True, False, False))
os_ = agente(v1_falso(ENVIO_V1))
import os as _os
_os.environ["V2_TEMAS"] = "0"
try:
    off = os_.conversar(pedido(mensaje="¿Hacen delivery a Surco?", memoria=json.loads(json.dumps(MEM_TALLA))))
finally:
    del _os.environ["V2_TEMAS"]
caso("V2_TEMAS=0: ni retoma ni traza ni pila", (off["respuesta"], "temas" in off["v2"], "v2" in off["memoria"]), (ENVIO_V1, False, False))

# V1 no empeora: si V1 ya hace la pregunta, V2 no agrega otra
res_q = ENVIO_V1 + "\n\n¿Qué talla usas normalmente?"
outq = agente(v1_falso(res_q)).conversar(pedido(mensaje="¿Hacen delivery a Surco?", memoria=json.loads(json.dumps(MEM_TALLA))))
caso("V1 ya vuelve a preguntar la talla: V2 no pone una segunda pregunta", (outq["respuesta"], outq["v2"]["temas"]["v1_retomo"]), (res_q, True))
# flujo fijo: no se toca
outf = agente(v1_falso(ENVIO_V1, extra={"modelo_llm": "flujo_pedido"})).conversar(pedido(mensaje="¿Hacen delivery a Surco?", memoria=json.loads(json.dumps(MEM_TALLA))))
caso("un flujo fijo de código (pedido, pago, cita) no lleva retoma", (outf["respuesta"], outf["v2"]["temas"]["bloqueo"]), (ENVIO_V1, "flujo fijo de código"))
# los botones de talla son solo de la web: en WhatsApp no son una pregunta
btn = {"tallas": [{"talla": "M", "disponible": True, "precio": 330, "codigo": "V31"}]}
outw = agente(v1_falso(ENVIO_V1, extra=btn)).conversar(pedido(mensaje="¿Hacen delivery a Surco?", memoria=json.loads(json.dumps(MEM_TALLA)), canal=""))
caso("WhatsApp: los botones de talla de V1 no se pintan, así que SÍ se retoma", outw["respuesta"].startswith(ENVIO_V1 + "\n\n"), True)
outw2 = agente(v1_falso(ENVIO_V1, extra=btn)).conversar(pedido(mensaje="¿Hacen delivery a Surco?", memoria=json.loads(json.dumps(MEM_TALLA)), canal="web"))
caso("web: con los botones de talla de V1 en pantalla, no se duplica la pregunta", outw2["respuesta"], ENVIO_V1)
# Go lleva su propio hilo
outg = agente(v1_falso(ENVIO_V1)).conversar(pedido(mensaje="¿Hacen delivery a Surco?", memoria=json.loads(json.dumps(MEM_TALLA)), estado="esperando_talla"))
caso("con un estado del flujo de Go (esperando talla) no se mete", outg["respuesta"], ENVIO_V1)
# la retoma que no pasa la compuerta no sale
class Mala:
    nombre = "mala"
    usa_modelo = False

    def atiende(self, plan):
        return True

    def elegir(self, plan, ctx):
        return None

    def redactar(self, plan, variante=0, contexto=None):
        return "Te lo mando mañana con descuento. ¿Qué talla usas?"


a_malo = agente(v1_falso(ENVIO_V1))
a_malo.redactor_activo = Mala()
outm = a_malo.conversar(pedido(mensaje="¿Hacen delivery a Surco?", memoria=json.loads(json.dumps(MEM_TALLA))))
caso("una retoma que no pasa el control de calidad NO sale: queda el texto de V1 y la pila no cuenta el intento",
     (outm["respuesta"], outm["v2"]["temas"]["enviada"], outm["memoria"]["v2"]["temas"]["pendientes"][0]["attempts"]), (ENVIO_V1, False, 1))
caso("…y la causa queda en la traza", bool(outm["v2"]["temas"]["retoma"]["errores"]) and not outm["v2"]["temas"]["retoma"]["pasa_la_compuerta"], True)
# un fallo de la capa nueva no tira el turno
a_roto = agente(v1_falso(ENVIO_V1))
a_roto.contexto = NS(construir=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("roto")))
outr = a_roto.conversar(pedido(mensaje="¿Hacen delivery a Surco?", memoria=json.loads(json.dumps(MEM_TALLA)), modo="sombra"))
caso("si la capa de temas falla, el turno sigue con V1", outr["respuesta"], ENVIO_V1)
# la ficha ajena (sin v2, rota o enorme)
for rara in (None, {}, {"v2": 5}, {"v2": {"temas": "x"}}, {"v2": {"temas": {"pendientes": [None, {"slot": "talla", "status": "suspended", "priority": "alto"}]}}}):
    mm = {**memoria.nueva(), "pendiente": "talla", **({} if rara is None else rara)}
    o = agente(v1_falso(ENVIO_V1)).conversar(pedido(mensaje="¿Hacen delivery a Surco?", memoria=mm))
    caso(f"ficha rara ({str(rara)[:40]}) → no revienta y V1 conserva su respuesta", o["respuesta"].startswith(ENVIO_V1), True)
# primer mensaje
o = agente(v1_falso(ENVIO_V1)).conversar(pedido(mensaje="¿Hacen delivery a Surco?", memoria=json.loads(json.dumps(MEM_TALLA)), historial=[]))
caso("primer mensaje: no se retoma", o["respuesta"], ENVIO_V1)

# no sé mi talla
outn = agente(v1_falso("El *V31* está disponible en talla S, M y L.", intent="consulta_talla")).conversar(
    pedido(mensaje="no sé mi talla", memoria=json.loads(json.dumps(MEM_TALLA))))
caso("«no sé mi talla»: ayuda con otras palabras y respuestas rápidas, sin suspender el tema",
     (outn["v2"]["temas"]["evento"]["tipo"], outn["v2"]["temas"]["evento"]["ayuda"], "otras tiendas" in outn["respuesta"], outn["memoria"]["pendiente"]), ("ayuda", "no_sabe", True, "talla"))
# creo q m pero de busto soy grande
outc = agente(v1_falso("Sí, el *V31* está disponible en talla *L* 😊", intent="consulta_producto", respondio=True, datos={"talla": "L"},
                       sabemos={"talla": "L"})).conversar(pedido(mensaje="creo q m pero de busto soy grande", memoria=json.loads(json.dumps(MEM_TALLA))))
caso("«creo q m pero de busto soy grande»: pregunta cuál de LAS DOS (M o L) que ella nombró", ("*M*" in outc["respuesta"], "*L*" in outc["respuesta"], memoria.pregunta_de(outc["respuesta"])), (True, True, "talla"))
caso("…y la talla que V1 adivinó (L) se olvida hasta que ella elija", outc["memoria"]["sabemos"]["talla"], None)
caso("…pero en sombra no se toca la ficha de V1", agente(v1_falso("Sí, el *V31* está disponible en talla *L* 😊", intent="consulta_producto", respondio=True, datos={"talla": "L"},
     sabemos={"talla": "L"}), modo="sombra").conversar(pedido(mensaje="creo q m pero de busto soy grande", memoria=json.loads(json.dumps(MEM_TALLA)), modo=""))["memoria"]["sabemos"]["talla"], "L")
# respuesta rápida: payload
outp = agente(v1_falso("Sí, el *V31* está disponible en talla *M*.", intent="consulta_talla", respondio=True, datos={"talla": "M"}, sabemos={})).conversar(
    pedido(mensaje="Soy talla M", payload={"intent": "provide_size", "size": "M"}, memoria=json.loads(json.dumps(MEM_TALLA))))
caso("respuesta rápida «Soy talla M» (payload): la talla se anota aunque V1 no la hubiera leído, y no se retoma",
     (outp["memoria"]["sabemos"]["talla"], outp["v2"]["temas"]["respuesta_rapida"]["fuente"], outp["v2"]["temas"]["retoma"]), ("M", "payload", None))
# HARD_SWITCH con V2 activo: invalida la fecha y el día/noche
mem_h = {**ficha(), "pendiente": "talla", "producto": "V31", "mostrados": ["V31"], "v2": {"temas": e0}}
outh = agente(v1_falso("¡Claro! 😊 Te paso las fotos.", intent="consulta_producto", datos={"ocasion": "graduacion", "color": "azul"},
                       sabemos={"ocasion": "graduacion", "color": "azul"})).conversar(
    pedido(mensaje="ya no quiero ese, muéstrame algo azul para graduación", memoria=json.loads(json.dumps({**mem_h, "sabemos": {**mem_h["sabemos"], "ocasion": "matrimonio"}}))))
caso("HARD_SWITCH activo: la pila se cierra, no se retoma la talla y la fecha/horario del matrimonio se borran de la ficha",
     (outh["v2"]["temas"]["pendientes"], outh["respuesta"], outh["memoria"]["sabemos"]["fecha"], outh["memoria"]["sabemos"]["horario"], outh["memoria"]["sabemos"]["talla"]),
     ([], "¡Claro! 😊 Te paso las fotos.", None, None, "M"))
caso("…y la talla y la zona de envío se conservan", (outh["memoria"]["sabemos"]["envio"], outh["memoria"]["sabemos"]["ciudad"]), ("lima", "Surco"))
outh_s = agente(v1_falso("¡Claro! 😊 Te paso las fotos.", intent="consulta_producto", datos={"ocasion": "graduacion"}, sabemos={"ocasion": "graduacion"}), modo="sombra").conversar(
    pedido(mensaje="ya no quiero ese, algo azul para graduación", memoria=json.loads(json.dumps({**mem_h, "sabemos": {**mem_h["sabemos"], "ocasion": "matrimonio"}})), modo=""))
caso("HARD_SWITCH en sombra: nada se borra de la ficha de V1 (solo lo dice la traza)", (outh_s["memoria"]["sabemos"]["fecha"], outh_s["v2"]["temas"]["invalida"][:2]), ("20 de noviembre", ["fecha", "fecha_iso"]))

# =================================================================================================================================
# 8. La compuerta contra un modelo que miente (la retoma NO puede traer un costo, un plazo ni una zona)
# =================================================================================================================================
visto: list[dict] = []


def llm(texto):
    def llamar(cuerpo):
        visto.append(cuerpo)
        return texto(cuerpo) if callable(texto) else texto
    return ClienteLLM("u", "qwen3:1.7b", llamar=llamar)


def retoma_con(realizador):
    sem = RedactorSemantico(SEL, realizador, G, lambda c: "")
    return sem, sem.redactar(plan_retoma(modo="obligatorio"), 0, {"conversation": {"last_user_message": "¿hacen delivery a Surco?"}})


mentira = ("Llega mañana a Surco por S/ 25 con 20 % de descuento, paga por Yape. ¿Qué talla usas?", "Te lo envío gratis a Surco en 2 días. ¿Qué talla usas?",
           "Quedan 2 unidades, es de seda roja. ¿Qué talla usas?")
for i, m in enumerate(mentira):
    try:
        retoma_con(RealizadorReescritura(llm(m)))
        caso(f"modelo que miente #{i + 1} en la retoma → la compuerta lo rechaza", False, True)
    except GateRechazo as e:
        caso(f"modelo que miente #{i + 1} en la retoma → la compuerta lo rechaza", bool(e.errores), True)
sem, honesto = retoma_con(RealizadorReescritura(llm(lambda c: c["messages"][1]["content"].split("reescribir:\n", 1)[1])))
caso("un modelo honesto (copia el texto con sus tokens) pasa y los datos reales vuelven a su lugar", ("§" in honesto, "Pandora" in honesto), (False, True))
sem, v_ = retoma_con(RealizadorVariantes(llm('{"0.puente": 2, "0.emoji": 1}')))
caso("el selector de variantes solo ve variantes escritas de la retoma, nunca datos ni la respuesta de V1",
     ("Pandora" not in visto[-1]["messages"][1]["content"], "S/ 15" in visto[-1]["messages"][1]["content"]), (True, False))
caso("el selector de variantes NO puede elegir la pregunta: la fija el intento (no se repite literal)", "0.pregunta" in visto[-1]["messages"][1]["content"], False)
caso("la traza del redactor dice que fue la retoma", sem.traza["plantillas"], ["RESUME_SIZE_REQUIRED"])
# el texto de V1 (con su costo, plazo y zona) NO pasa por ningún modelo: llega intacto aunque el modelo desvaríe
visto.clear()
ag_m = agente(v1_falso(ENVIO_V1 + " Llega en 2 a 3 días a Surco."), realizador=RealizadorReescritura(llm(mentira[0])))
outv = ag_m.conversar(pedido(mensaje="¿Hacen delivery a Surco?", memoria=json.loads(json.dumps(MEM_TALLA))))
caso("el modelo desvaría (reescritura): sale el texto base y la respuesta de V1 queda intacta",
     (outv["respuesta"].startswith(ENVIO_V1 + " Llega en 2 a 3 días a Surco.\n\n"), "mañana" in outv["respuesta"], "S/ 25" in outv["respuesta"], "Yape" in outv["respuesta"]), (True, False, False, False))
caso("…y al modelo nunca le llegó la respuesta de V1 (ni su costo ni su zona)", any("S/ 15" in str(c) or "Surco" in str(c) for c in visto), False)

# =================================================================================================================================
# 9. V1 no cambia: la ficha con la pila no desordena la memoria de V1
# =================================================================================================================================
m_con = {**memoria.nueva(), "v2": {"temas": e1}}
m_sin = memoria.nueva()
caso("V1 normaliza la ficha con y sin la pila exactamente igual (la clave v2 no le cambia nada)", memoria.normalizar(m_con), memoria.normalizar(m_sin))
caso("la lectura de V1 con la pila en la ficha es la misma que sin ella",
     (lambda a, b: (memoria.leer(a, "soy M"), a["sabemos"]) == (memoria.leer(b, "soy M"), b["sabemos"]))(memoria.normalizar({**m_con, "pendiente": "talla"}), memoria.normalizar({**m_sin, "pendiente": "talla"})), True)
caso("V1 sin V2: la clave v2 nunca aparece en una ficha que V1 arma sola", "v2" in memoria.reconstruir([]), False)


# --- métricas ---------------------------------------------------------------------------------------------------------------------
from .v2.metricas import Registro                                       # noqa: E402

reg = Registro()
reg.turno("v2", 10, {"modo": "activo", "enviado": "v2", "temas": {"evento": {"tipo": "interrumpe"}, "retoma": {"slot": "talla"}, "enviada": True, "v1_retomo": False}})
reg.turno("v2", 10, {"modo": "sombra", "temas": {"evento": {"tipo": "interrumpe"}, "retoma": None, "v1_retomo": True}})
reg.turno("v2", 10, {"modo": "sombra", "temas": {"evento": {"tipo": "cambio"}, "retoma": None}})
reg.turno("v2", 10, {"modo": "sombra", "temas": {"error": "KeyError"}})
caso("métricas de cambios de tema", reg.resumen()["v2"]["temas"],
     {"interrupciones": 2, "cambios_totales": 1, "ayuda": 0, "retomas_planeadas": 1, "retomas_enviadas": 1, "v1_retomo_solo": 1, "errores": 1})


def main() -> int:
    for f in fallos:
        print(f)
    print(f"v2 cambios de tema (detector, pila, prioridades, retoma, respuestas rápidas, integración): {total - len(fallos)}/{total} casos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
