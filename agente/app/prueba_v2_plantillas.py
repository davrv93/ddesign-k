"""Pruebas de las plantillas semánticas: delexicalización, compuerta factual determinista, selector, realizadores y el redactor.
Sin red y sin modelos (los modelos son falsos):

    python3 -m app.prueba_v2_plantillas
"""
from __future__ import annotations

import re
import sys
from types import SimpleNamespace as NS

from . import memoria
from .v2 import config as C
from .v2 import plantillas as T
from .v2.agente import AgentV2
from .v2.calidad import ReglasCalidad
from .v2.contexto import ContextBuilder
from .v2.decision import ReglasDecision
from .v2.delex import TOKEN_RE, Protegidos, sin_tokens
from .v2.factual import LEXICOS, CompuertaFactual, Esperado
from .v2.generacion import Encadenada
from .v2.motivo import motivo
from .v2.motor import MotorRecursivo
from .v2.realizador import (ClienteLLM, GateRechazo, RealizadorBase, RealizadorReescritura, RealizadorVariantes, RedactorSemantico)

fallos: list[str] = []
total = 0


def caso(nombre: str, obtenido, esperado) -> None:
    global total
    total += 1
    if obtenido != esperado:
        fallos.append(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


# --- el catálogo ------------------------------------------------------------------------------------------------------------
cat = T.cargar()
caso("plantillas.yaml es válido", T.validar(cat), [])
caso("las 26 plantillas de la especificación están (id_spec 1–26)",
     sorted({p.id_spec for p in cat.plantillas.values() if p.id_spec} | {1}) == list(range(1, 27)), True)
caso("toda etiqueta «prohibido» tiene su regla en la compuerta",
     sorted({t for p in cat.plantillas.values() for t in p.prohibido if t not in T.PROHIBIDO_A_REGLA}), [])
reglas_validas = set(LEXICOS) | {"estructura", "nombres propios"}
caso("cada regla a la que apunta «prohibido» existe en la compuerta",
     sorted({r for rs in T.PROHIBIDO_A_REGLA.values() for r in rs} - reglas_validas), [])
activas = sorted(p.id for p in cat.plantillas.values() if p.activa)
caso("activas en esta etapa: acuses, preguntas y recomendación",
     activas, sorted(["ACK_NAME", "CATEGORY_AVAILABLE", "OCCASION_CAPTURED", "ACK_NOTED", "ASK_OCCASION", "ASK_DAY_NIGHT", "ASK_DATE", "ASK_SIZE",
                      "CONFIRM_KNOWN_SIZE", "ASK_HEIGHT", "ASK_COLOR", "ASK_WHAT_LOOKING_FOR", "ASK_WHAT_LIKED", "RECOMMEND_ONE_PRODUCT",
                      "SEND_PRODUCT_PHOTOS"]))
caso("las que hablan de datos de la tienda, cierre y derivación siguen sin activar",
     [p.id for p in cat.plantillas.values() if p.id in ("ANSWER_PRICE", "STOCK_AVAILABLE", "HUMAN_HANDOFF", "PURCHASE_INTENT") and p.activa], [])
caso("las decisiones de código no llevan modelo", sorted(p.id for p in cat.plantillas.values() if p.sin_modelo),
     ["CONFIRM_PREVIOUS_OFFER", "HUMAN_HANDOFF", "ORDER_SUMMARY_CONFIRM", "PURCHASE_INTENT"])

MUESTRA = {"OCASION_ART": "el matrimonio", "OCASION_NOM": "matrimonio", "TALLA_PREVIA": "*M*", "NOMBRE": "Alvaro", "CATEGORIA": "vestidos",
           "PRODUCTO": "el *V31* Vestido Pandora", "MOTIVO": "corte largo", "PRECIO": "S/ 330.00", "TALLAS_DISPONIBLES": "S, M y L",
           "COLORES_DISPONIBLES": "negro", "LINK_CATALOGO": "https://x/catalogo", "TALLA": "M", "TALLA_SOLICITADA": "L",
           "PRESUPUESTO": "S/ 300", "ULTIMO_PRODUCTO": "el *V31* Vestido Pandora", "PRODUCTO_NUEVO": "el *V35* Vestido Irla",
           "PRODUCTO_MENCIONADO": "el V99"}
mal = []
for p in cat.plantillas.values():
    if p.tipo != "pregunta" or not p.activa:
        continue
    for v in p.partes["pregunta"]:
        if "{{PREGUNTA_V1}}" in v:
            continue          # es el texto de V1: ya trae su propia clave
        texto = T.SLOT_RE.sub(lambda m: MUESTRA[m.group(1)], v)
        if memoria.clave_de(texto) != p.clave:
            mal.append((p.id, texto, memoria.clave_de(texto)))
caso("CADA variante de pregunta se reconoce con la clave de V1 (memoria.DETECTOR): si no, la memoria de V1 se desordena", mal, [])
claves_v1 = {k for k, _ in memoria.DETECTOR}
caso("las claves de las preguntas activas existen en V1", sorted({p.clave for p in cat.plantillas.values() if p.tipo == "pregunta" and p.activa} - claves_v1), [])
caso("regla del dueño: ninguna variante pregunta «¿Es para alguna ocasión especial?»",
     [v for p in cat.plantillas.values() if p.activa for vs in p.partes.values() for v in vs if re.search(r"es para alguna ocasi", v, re.I)], [])
caso("los emojis de las plantillas activas no pasan de uno por mensaje armado",
     max(len(re.findall("[\U0001F000-\U0001FAFF☀-➿]", T.SLOT_RE.sub("x", v))) for p in cat.plantillas.values() if p.activa
         for v in p.partes.get("emoji", [""])) <= 1, True)

# --- delexicalización --------------------------------------------------------------------------------------------------------
P1 = Protegidos(sal="a1")
t = P1.proteger("Creo que {{PRODUCTO}} va bien. {{PRODUCTO}}", {"PRODUCTO": "el *V31* Vestido Pandora"})
caso("el texto con tokens no trae el dato real", "Pandora" in t or "V31" in t, False)
caso("el mismo valor da el mismo token en un turno", len(set(TOKEN_RE.findall(t))), 1)
caso("el token vuelve al valor real", P1.rellenar(t), "Creo que el *V31* Vestido Pandora va bien. el *V31* Vestido Pandora")
caso("otra sal da otro token (el modelo no puede memorizarlos)", Protegidos(sal="b2").token("PRODUCTO", "x") != Protegidos(sal="a1").token("PRODUCTO", "x"), True)
caso("el token tiene el formato §TIPO_xxxx§", bool(re.fullmatch(r"§PRODUCTO_[0-9a-f]{4}§", P1.token("PRODUCTO", "otro"))), True)
for nombre, f in [("un slot sin valor es un error, no un hueco", lambda: P1.proteger("{{PRECIO}}", {})),
                  ("un slot vacío es un error", lambda: P1.proteger("{{PRECIO}}", {"PRECIO": ""})),
                  ("un token desconocido no se rellena", lambda: P1.rellenar("§PRECIO_ffff§")),
                  ("un tipo de dato inválido se rechaza", lambda: P1.token("precio", "x"))]:
    try:
        f()
        caso(nombre, False, True)
    except (KeyError, ValueError):
        caso(nombre, True, True)
caso("sin_tokens deja solo lo que escribió el modelo", sin_tokens("Hola §NOMBRE_1a2b§!").split(), ["Hola", "!"])

# --- compuerta factual -------------------------------------------------------------------------------------------------------
G = CompuertaFactual(clave_de=memoria.clave_de)
TK = "§PRODUCTO_8f21§"
MOT = "§MOTIVO_44d9§"
BASE = f"Creo que {TK} puede ir muy bien para §OCASION_ART_a1f2§ 😊\n{MOT}. Te paso la foto.\n\n¿Para cuándo es §OCASION_ART_a1f2§?"
BASE = BASE.replace("§OCASION_ART_a1f2§", "§OCASION_a1f2§")
ESP = Esperado(base=BASE, max_frases=5, clave_pregunta="fecha")


def veredicto(salida: str, esp: Esperado = ESP) -> tuple[bool, list[str]]:
    r = G.evaluar(salida, esp)
    return r["passed"], r["errors"]


caso("la base pasa contra sí misma", veredicto(BASE)[0], True)
OK = f"Pienso que {TK} puede ser una muy buena opción para §OCASION_a1f2§ 😊\n{MOT}. Te muestro la foto.\n\n¿Para cuándo es §OCASION_a1f2§?"
caso("una paráfrasis honesta pasa", veredicto(OK), (True, []))


def rechaza(nombre: str, salida: str, fragmento: str, esp: Esperado = ESP) -> None:
    ok, errs = veredicto(salida, esp)
    caso(nombre, (not ok) and any(fragmento in e for e in errs), True)


rechaza("pierde un token", OK.replace(MOT, "algo"), "falta el dato protegido")
rechaza("duplica un token", OK + f" {TK}", "duplica el dato protegido")
rechaza("inventa un token", OK + " §PRECIO_ffff§", "inventa el dato protegido")
rechaza("cambia un token", OK.replace("8f21", "0000"), "falta el dato protegido")
rechaza("trae un resto de marcador", OK + " {{PRECIO}}", "restos de marcadores")
rechaza("introduce un precio con S/", OK.replace("Te muestro la foto.", "Cuesta S/ 330."), "introduce precio")
rechaza("introduce cifras", OK.replace("Te muestro la foto.", "Es de 2 piezas."), "introduce cifras")
rechaza("introduce una talla", OK.replace("Te muestro la foto.", "Te queda en talla M."), "introduce talla")
rechaza("introduce una talla suelta (L)", OK.replace("Te muestro la foto.", "Está en L."), "introduce talla")
rechaza("introduce un color", OK.replace("Te muestro la foto.", "Viene en negro."), "introduce color")
rechaza("introduce una tela", OK.replace("Te muestro la foto.", "Es de satén."), "introduce tela o material")
rechaza("introduce una prenda", OK.replace("Te muestro la foto.", "Combina con una blusa."), "introduce prenda")
rechaza("introduce un descuento", OK.replace("Te muestro la foto.", "Tiene descuento."), "introduce descuento o promoción")
rechaza("introduce escasez", OK.replace("Te muestro la foto.", "Quedan pocas."), "introduce stock o escasez")
rechaza("introduce urgencia", OK.replace("Te muestro la foto.", "Aprovecha ahora."), "introduce urgencia")
rechaza("introduce un plazo", OK.replace("Te muestro la foto.", "Llega mañana."), "introduce")
rechaza("introduce un medio de pago", OK.replace("Te muestro la foto.", "Aceptamos Yape."), "introduce")
rechaza("introduce una garantía", OK.replace("Te muestro la foto.", "Tiene garantía."), "introduce garantía o calidad")
rechaza("introduce una dirección", OK.replace("Te muestro la foto.", "Pásate por el showroom."), "introduce ubicación")
rechaza("introduce una cita", OK.replace("Te muestro la foto.", "Puedes probártelo."), "introduce cita o visita")
rechaza("introduce un nombre propio", OK.replace("Te muestro la foto.", "Te lo dice Carla."), "nombres propios")
rechaza("introduce un código de producto", OK.replace("Te muestro la foto.", "Mira el V05."), "introduce")
rechaza("cambia el número de párrafos", OK.replace("\n\n", "\n"), "número de párrafos")
rechaza("cambia el número de preguntas", OK.replace("Te muestro la foto.", "¿Te gusta?"), "número de preguntas")
rechaza("la última pregunta ya no pide fecha", OK.replace("¿Para cuándo es §OCASION_a1f2§?", "¿Qué talla usas?"), "ya no pide «fecha»")
rechaza("demasiadas frases", OK.replace("Te muestro la foto.", "Te muestro la foto. Es bonito. Es lindo. Es ideal. Es genial."), "demasiadas frases")
rechaza("demasiados emojis", OK.replace("😊", "😊😍"), "demasiados emojis")
rechaza("demasiado largo", OK.replace("Te muestro la foto.", "Te muestro la foto " + "ahora mismo mismo " * 40), "demasiado largo")
rechaza("otro idioma (chino)", OK.replace("Te muestro la foto.", "很漂亮。"), "caracteres que no son español")
rechaza("palabras en inglés", OK.replace("Te muestro la foto.", "Thank you dear."), "inglés")
rechaza("formato que no es de WhatsApp", OK.replace("Te muestro la foto.", "**Te muestro la foto.**"), "formato")
caso("salida vacía", veredicto("")[1], ["salida vacía"])
caso("un «hoy» que la base ya traía no es una invención",
     veredicto("¿Qué estás buscando hoy?", Esperado(base="¿Qué estás buscando hoy?", clave_pregunta="que_busca"))[0], True)
caso("un «hoy» que la base NO traía sí lo es",
     veredicto("¿Qué estás buscando hoy?", Esperado(base="¿Qué estás buscando?", clave_pregunta="que_busca"))[0], False)
caso("la compuerta es determinista: dos veces, lo mismo", veredicto(OK) == veredicto(OK), True)

# --- motivo de la recomendación: una frase completa de la ficha, o ninguna ---------------------------------------------------------
caso("motivo: la primera frase de la ficha",
     motivo("Vestido Kendall", "Diseño surrealista, con tiras strech que ayuda a sujetar el busto. Tipo corset y rayas negras."),
     "Diseño surrealista, con tiras strech que ayuda a sujetar el busto")
caso("motivo: ficha en mayúsculas → minúsculas, nombre propio con su forma, sin repetir «Vestido Pandora es»",
     motivo("Vestido Pandora", "VESTIDO PANDORA es un CLÁSICO ATEMPORAL, con un estilo de los ochenta, con un escote corazón y, a la vez, asimétrico."),
     "Es un clásico atemporal, con un estilo de los ochenta, con un escote corazón y, a la vez, asimétrico")
caso("motivo: «talle.La falda» (sin espacio) corta en el punto",
     motivo("Conjunto X", "Set con silueta princesa, una pretina ancha que ayuda a formar el talle.La falda es lisa y sencilla"),
     "Set con silueta princesa, una pretina ancha que ayuda a formar el talle")
caso("motivo: una frase larga se corta en una coma, nunca a media palabra ni con un «y,» colgando",
     motivo("V", "Pieza " + "muy bonita y elegante, " * 10 + "fin"), "Pieza muy bonita y elegante, muy bonita y elegante, muy bonita y elegante, muy bonita y elegante, muy bonita y elegante, muy bonita y elegante")
caso("motivo: una frase larga sin comas no se corta a la mitad: no hay motivo", motivo("V", "palabra " * 40), None)
caso("motivo: demasiado corto → ninguno", motivo("Vestido Irla", "Bonito."), None)
caso("motivo: sin descripción → ninguno", motivo("Vestido Irla", ""), None)
caso("motivo: un precio con decimales no corta la frase", motivo("V", "Cuesta 330.00 soles el set completo de dos piezas. Otra frase."), "Cuesta 330.00 soles el set completo de dos piezas")
caso("motivo: no termina en una palabra colgante", motivo("V", "Tiene un corte largo con abertura y una caída con"), "Tiene un corte largo con abertura y una caída")

# --- selector ----------------------------------------------------------------------------------------------------------------
class Hechos:
    def __init__(self, hay=("vestido",)):
        self.hay = hay

    def producto(self, codigo):
        if codigo == "V35":
            return {"PRODUCTO": "el *V35* Vestido Irla", "PRODUCTO_DE": "del *V35* Vestido Irla", "MOTIVO": "tiene corte A con escote en V"}
        if codigo == "V40":
            return {"PRODUCTO": "la *V40* Falda Paola", "PRODUCTO_DE": "de la *V40* Falda Paola", "MOTIVO": None}
        return None

    def categoria(self, clave):
        return {"vestido": "vestidos", "falda": "faldas"}.get(clave) if clave in self.hay else None

    def enlace_catalogo(self):
        return "https://x/catalogo"


SEL = T.Selector(cat, Hechos(), memoria.OCASION_TXT)


def ctx(**kw):
    conv = {"stage": "prospeccion", "last_user_message": "hola", "turns_total": 3, "recent_turns": [], "captured": {}, "responded": False,
            "category_asked": None, "wants_to_see": False, "intent": None}
    conv.update(kw.pop("conv", {}))
    base = {"conversation": conv, "customer": {"talla_perfil": None}, "requirements": {"ocasion": "matrimonio"},
            "product": {"focus": None, "shown": []}}
    base.update(kw)
    return base


def plan(accion="preguntar", producto=None, tipo="fecha", texto=None):
    """El texto de la pregunta es el de V1 (memoria.PREGUNTAS), que la plantilla conserva."""
    return {"accion": accion, "producto": producto, "hechos": [],
            "pregunta": {"tipo": tipo, "texto": texto or memoria.PREGUNTAS.get(tipo, "¿Y?")} if tipo else None}


def textos(msg: T.Mensaje) -> str:
    p = Protegidos(sal="t")
    return p.rellenar(msg.ensamblar(p, None, "s"))


m = SEL.elegir(plan(), ctx())
caso("preguntar sin dato nuevo: solo la pregunta de la clave", (m.ids(), m.clave_pregunta), (["ASK_DATE"], "fecha"))
caso("la pregunta es la redacción de V1, tal cual", textos(m), memoria.PREGUNTAS["fecha"])
m2 = SEL.elegir(plan(tipo="ocasion", texto="¿Qué evento es?"), ctx())
caso("«tengo un evento» → V1 pregunta «¿Qué evento es?» y V2 lo conserva", textos(m2), "¿Qué evento es?")
m2 = SEL.elegir(plan(tipo="fecha", texto="Y cuéntame, ¿ya tienes fecha? Así veo que lo tengas a tiempo 😊"), ctx())
caso("la redacción de «segunda vez» de V1 también se conserva", textos(m2).startswith("Y cuéntame, ¿ya tienes fecha?"), True)
caso("sin el texto de V1, la plantilla usa su propia variante (respaldo)",
     bool(SEL.elegir({**plan(), "pregunta": {"tipo": "fecha", "texto": ""}}, ctx()).ids()), True)
m = SEL.elegir(plan(), ctx(conv={"captured": {"nombre": "alvaro"}}, requirements={}))
caso("me llamo Alvaro → acuse con su nombre + pregunta", m.ids()[0], "ACK_NAME")
caso("el nombre sale con mayúscula inicial y sin inventar nada", "Alvaro" in textos(m), True)
m = SEL.elegir(plan(tipo="ocasion"), ctx(conv={"category_asked": "vestido", "last_user_message": "pero no tienes vestidos?"}, requirements={}))
caso("¿tienes vestidos? (hay stock) → CATEGORY_AVAILABLE", (m.ids(), "vestidos" in textos(m)), (["CATEGORY_AVAILABLE", "ASK_OCCASION"], True))
for nombre, f in [("¿tienes jeans? sin stock → SinPlantilla (lo cuenta V1)",
                   lambda: SEL.elegir(plan(tipo="ocasion"), ctx(conv={"category_asked": "jeans"}, requirements={})))]:
    try:
        f()
        caso(nombre, False, True)
    except T.SinPlantilla:
        caso(nombre, True, True)
m = SEL.elegir(plan(tipo="fecha"), ctx(conv={"captured": {"ocasion": "matrimonio"}}))
caso("dijo la ocasión → OCCASION_CAPTURED + pregunta", m.ids(), ["OCCASION_CAPTURED", "ASK_DATE"])
m = SEL.elegir(plan(tipo="horario"), ctx(conv={"captured": {"fecha": "2026-10-20"}}))
caso("dijo otro dato → «Anotado» + pregunta", m.ids(), ["ACK_NOTED", "ASK_DAY_NIGHT"])
m = SEL.elegir(plan(tipo="talla", texto="¿Usas talla *M*, como en tu pedido anterior, o prefieres otra talla?"), ctx(customer={"talla_perfil": "M"}))
caso("clienta que vuelve → confirma la talla del pedido anterior", (m.ids(), "*M*" in textos(m)), (["CONFIRM_KNOWN_SIZE"], True))
m = SEL.elegir(plan(tipo="talla"), ctx())
caso("sin talla anterior → pregunta la talla", m.ids(), ["ASK_SIZE"])
m = SEL.elegir(plan("recomendar", "V35", "fecha"), ctx())
caso("recomendar: la recomendación y luego la pregunta (después de la foto)", m.ids(), ["RECOMMEND_ONE_PRODUCT", "ASK_DATE"])
t = textos(m)
caso("la recomendación nombra la prenda y trae el motivo de su ficha", ("*V35* Vestido Irla" in t, "corte A con escote en V" in t), (True, True))
caso("la pregunta va en su propio párrafo", len(t.split("\n\n")), 2)
m = SEL.elegir(plan("recomendar", "V35", None), ctx(conv={"wants_to_see": True}))
caso("pidió ver la foto → SEND_PRODUCT_PHOTOS", m.ids(), ["SEND_PRODUCT_PHOTOS"])
caso("la foto «del» Vestido, no «de el» (gramática)", all("de el *" not in textos(SEL.elegir(plan("recomendar", "V35", None), ctx(conv={"wants_to_see": True, "last_user_message": str(i)}))) for i in range(8)), True)
m = SEL.elegir(plan("recomendar", "V40", None), ctx())
caso("sin motivo en la ficha, la recomendación sale sin motivo (no se inventa)", "motivo" in textos(m).lower(), False)
for nombre, f in [("una prenda que no está en el catálogo", lambda: SEL.elegir(plan("recomendar", "V99", None), ctx())),
                  ("una pregunta de V1 sin plantilla (pago)", lambda: SEL.elegir(plan(tipo="pago"), ctx())),
                  ("preguntar sin pregunta del código", lambda: SEL.elegir(plan(tipo=None), ctx())),
                  ("una acción en la que V2 no habla", lambda: SEL.elegir(plan("responder", None, None), ctx())),
                  ("una plantilla que no está activa", lambda: T.Selector(cat, Hechos(), memoria.OCASION_TXT)._p("ANSWER_PRICE"))]:
    try:
        f()
        caso(nombre + " → SinPlantilla", False, True)
    except T.SinPlantilla:
        caso(nombre + " → SinPlantilla", True, True)
caso("V2_HABLA limita al selector", T.Selector(cat, Hechos(), memoria.OCASION_TXT, habla=("recomendar",)).habla, ("recomendar",))
try:
    T.Selector(cat, Hechos(), memoria.OCASION_TXT, habla=("recomendar",)).elegir(plan(), ctx())
    caso("con habla=recomendar, preguntar → SinPlantilla", False, True)
except T.SinPlantilla:
    caso("con habla=recomendar, preguntar → SinPlantilla", True, True)
a, b = Protegidos(sal="s"), Protegidos(sal="s")
caso("el armado es determinista con la misma semilla",
     SEL.elegir(plan(), ctx()).ensamblar(a, None, "x") == SEL.elegir(plan(), ctx()).ensamblar(b, None, "x"), True)
def armado(i):
    pr = Protegidos(sal="s")
    return pr.rellenar(SEL.elegir(plan(), ctx(conv={"captured": {"fecha": "x"}})).ensamblar(pr, None, f"turno{i}"))


variantes = {armado(i) for i in range(12)}
caso("con otra semilla el acuse varía (no repite siempre lo mismo) y la pregunta no cambia",
     (len(variantes) > 1, all(v.endswith(memoria.PREGUNTAS["fecha"]) for v in variantes)), (True, True))

# --- realizadores y redactor ---------------------------------------------------------------------------------------------------
visto: list[dict] = []


def llm_que_dice(texto):
    def llamar(cuerpo):
        visto.append(cuerpo)
        return texto(cuerpo) if callable(texto) else texto
    return ClienteLLM("u", "qwen3:1.7b", llamar=llamar)


def redactor(realizador):
    return RedactorSemantico(SEL, realizador, G, lambda c: "")


CTX_R = ctx(conv={"last_user_message": "pa un matrimonio"})
PLAN_R = plan("recomendar", "V35", "fecha")
base_txt = redactor(RealizadorBase()).redactar(PLAN_R, 0, CTX_R)
caso("realizador base: sin modelo, con datos reales y sin tokens", ("§" not in base_txt, "*V35* Vestido Irla" in base_txt), (True, True))
rb = redactor(RealizadorBase())
rb.redactar(PLAN_R, 0, CTX_R)
caso("la traza dice qué plantillas y qué realizador", (rb.traza["plantillas"], rb.traza["realizador"]), (["RECOMMEND_ONE_PRODUCT", "ASK_DATE"], "base"))

ll = llm_que_dice("{}")
RealizadorVariantes(ll).realizar  # existe
rv = redactor(RealizadorVariantes(llm_que_dice('{"0.cuerpo": 2, "1.pregunta": 1}')))
txt = rv.redactar(PLAN_R, 0, CTX_R)
caso("variantes: el modelo elige números y el código arma el texto", ("§" not in txt, "*V35* Vestido Irla" in txt), (True, True))
caso("variantes: la traza cuenta cuántas opciones eligió bien", rv.traza["variantes"]["validas"] >= 1, True)
caso("variantes: al modelo le llegan variantes con huecos {{SLOT}}, nunca el dato real",
     ("Vestido Irla" not in visto[-1]["messages"][1]["content"], "{{PRODUCTO}}" in visto[-1]["messages"][1]["content"]), (True, True))
caso("variantes: el modelo no recibe ningún dato del backend", "corte A" in visto[-1]["messages"][1]["content"], False)
for nombre, salida in [("JSON roto", "no sé"), ("número fuera de rango", '{"0.cuerpo": 99}'), ("clave inventada", '{"7.zzz": 0}'),
                       ("texto libre en vez de JSON", "Creo que el V99 cuesta S/ 20")]:
    t = redactor(RealizadorVariantes(llm_que_dice(salida))).redactar(PLAN_R, 0, CTX_R)
    caso(f"variantes con {nombre}: sale el texto base, sin datos inventados", ("§" not in t, "V99" in t, "S/ 20" in t), (True, False, False))
caso("el modelo (variantes) se pide con temperatura 0 y sin razonamiento",
     (visto[-1]["temperature"], visto[-1]["reasoning_effort"], visto[-1]["chat_template_kwargs"]), (0.0, "none", {"enable_thinking": False}))

def reescribe(f):
    return redactor(RealizadorReescritura(llm_que_dice(f)))


def con_tokens(cuerpo):
    return cuerpo["messages"][1]["content"].split("reescribir:\n", 1)[1]


t = reescribe(lambda c: con_tokens(c).replace("foto", "imagen")).redactar(PLAN_R, 0, CTX_R)
caso("reescritura honesta: pasa la compuerta y se rellenan los datos", ("imagen" in t, "*V35* Vestido Irla" in t, "§" in t), (True, True, False))
for nombre, f, frag in [
        ("pierde el token del motivo", lambda c: re.sub(r"§MOTIVO_[0-9a-f]{4}§", "es bonito", con_tokens(c)), "falta el dato protegido"),
        ("inventa un precio", lambda c: con_tokens(c).replace("Te paso la foto.", "Cuesta S/ 99."), "introduce precio"),
        ("inventa una talla", lambda c: con_tokens(c).replace("Te paso la foto.", "Viene en talla S."), "introduce talla"),
        ("junta los párrafos", lambda c: con_tokens(c).replace("\n\n", " "), "párrafos")]:
    try:
        reescribe(f).redactar(PLAN_R, 0, CTX_R)
        caso(f"reescritura que {nombre} → GateRechazo", False, True)
    except GateRechazo as e:
        caso(f"reescritura que {nombre} → GateRechazo", any(frag in x for x in e.errores), True)
caso("el modelo se pide sin razonamiento y se le quita cualquier <think> que se cuele",
     reescribe(lambda c: "<think>pienso</think>" + con_tokens(c)).redactar(PLAN_R, 0, CTX_R) == base_txt, True)
caso("comillas y etiqueta de la salida del modelo se limpian",
     reescribe(lambda c: 'Mensaje: "' + con_tokens(c) + '"').redactar(PLAN_R, 0, CTX_R) == base_txt, True)
caso("una decisión de código no pasa por el modelo",
     (lambda r: (r.usa_modelo, r.realizador.nombre))(redactor(RealizadorBase())), (False, "base"))

# --- AgentV2 con plantillas semánticas ---------------------------------------------------------------------------------------
precios = lambda: {320, 330}
nombres = {"V35": "Vestido Irla"}
calidad = ReglasCalidad(precios=precios, nombres=nombres.get, todos_los_nombres=lambda: dict(nombres), ficha_texto=lambda c: "corte A escote en V",
                        clave_de=memoria.clave_de, preguntas_en=memoria.preguntas_en, fundamento=False)


def pedido(**kw):
    base = dict(mensaje="hola", historial=[NS(rol="cliente", texto="hola"), NS(rol="bot", texto="¡Hola!")], etapa="", memoria={}, producto="",
                talla="", desde_anuncio=False, perfil=None, usar_llm=True, modo="activo")
    base.update(kw)
    return NS(**base)


def v1(res):
    llamadas = []

    def f(req):
        llamadas.append(req.usar_llm)
        return dict(res)
    f.llamadas = llamadas
    return f


def agente(res, realizador, **kw):
    sem_base = RedactorSemantico(SEL, RealizadorBase(), G, lambda c: "")
    activo = Encadenada([RedactorSemantico(SEL, realizador, G, lambda c: ""), sem_base]) if realizador.usa_modelo else sem_base
    v = v1(res)
    return AgentV2(v1=v, contexto=ContextBuilder(texto_pregunta=lambda t, mem, msg, r="": memoria.PREGUNTAS.get(t, ""),
                                                 categoria_pedida=lambda m: "vestido" if "vestido" in m else ("jeans" if "jeans" in m else None)),
                   motor=MotorRecursivo(ReglasDecision(), {"stock": lambda c: "online"}), calidad=calidad,
                   redactor=sem_base, redactor_activo=activo, modo="activo", **kw), v


RES_NOMBRE = {"accion": "responder", "modelo_llm": "respaldo_codigo", "respuesta": "¡Mucho gusto, Alvaro! 😊\n\nCuéntame, ¿qué estás buscando?",
              "etapa": "prospeccion", "sugerencias": [], "comercial": {"intent": "saludo"}, "siguiente_pregunta": "que_busca", "ms": 5,
              "lectura": {"datos": {"nombre": "Alvaro"}, "respondio": False}, "memoria": {"producto": "", "mostrados": [], "sabemos": {"nombre": "Alvaro"}}}
ag, v = agente(RES_NOMBRE, RealizadorBase())
out = ag.conversar(pedido(mensaje="me llamo alvaro"))
caso("me llamo Alvaro: V2 habla y el texto trae «Alvaro»", (out["v2"]["enviado"], "Alvaro" in out["respuesta"], "§" in out["respuesta"]), ("v2", True, False))
caso("V2 habla con un solo paso de V1 (sin su LLM de pago)", v.llamadas, [False])
caso("la traza dice qué plantillas se usaron", out["v2"]["sombra"]["generacion"]["plantillas"], ["ACK_NAME", "ASK_WHAT_LOOKING_FOR"])
caso("la pregunta del texto es la que V1 eligió (clave que_busca)", memoria.pregunta_de(out["respuesta"]), "que_busca")

RES_CAT = {**RES_NOMBRE, "respuesta": "¡Sí, tenemos vestidos! 😊\n\n¿Para qué ocasión buscas el vestido?", "siguiente_pregunta": "ocasion",
           "lectura": {"datos": {}, "respondio": False}, "memoria": {"producto": "", "mostrados": [], "sabemos": {}}}
ag, v = agente(RES_CAT, RealizadorBase())
out = ag.conversar(pedido(mensaje="pero no tienes vestidos?"))
caso("¿tienes vestidos?: el texto trae «vestidos» y pregunta la ocasión",
     (out["v2"]["enviado"], "vestidos" in out["respuesta"], memoria.pregunta_de(out["respuesta"])), ("v2", True, "ocasion"))
RES_JEANS = {**RES_CAT, "respuesta": "¿Para qué ocasión buscas?"}
ag, v = agente(RES_JEANS, RealizadorBase())
out = ag.conversar(pedido(mensaje="tienen jeans?"))
caso("¿tienes jeans? (sin stock): V2 no habla, lo cuenta V1", (out["v2"]["enviado"], out["respuesta"].startswith("¿Para qué ocasión")), ("v1", True))
caso("y dice por qué", "sin plantilla segura" in out["v2"]["motivo_v1"], True)

RES_REC = {"accion": "responder", "modelo_llm": "respaldo_codigo", "respuesta": "Para el matrimonio te recomiendo el *V35* Vestido Irla 😊 Te paso la foto.\n\n¿Para cuándo es el matrimonio?",
           "etapa": "seguimiento", "sugerencias": [{"codigo": "V35", "titulo": "*V35* Vestido Irla", "pie": "x"}], "comercial": {"intent": "consulta_producto"},
           "siguiente_pregunta": "fecha", "ms": 5, "lectura": {"datos": {"ocasion": "matrimonio"}, "respondio": False},
           "memoria": {"producto": "V35", "mostrados": ["V35"], "sabemos": {"ocasion": "matrimonio"}}}
ag, v = agente(RES_REC, RealizadorBase())
out = ag.conversar(pedido(mensaje="busco algo para un matrimonio", memoria={"mostrados": []}))
caso("recomendar: habla V2 con los datos reales de la ficha y la pregunta de V1",
     (out["v2"]["enviado"], "*V35* Vestido Irla" in out["respuesta"], memoria.pregunta_de(out["respuesta"]), len(out["respuesta"].split("\n\n"))), ("v2", True, "fecha", 2))
caso("las fotos y la ficha siguen siendo las de V1", (out["sugerencias"][0]["codigo"], out["memoria"]["producto"]), ("V35", "V35"))
RES_2FOTOS = {**RES_REC, "sugerencias": [{"codigo": "V35", "titulo": "a", "pie": "x"}, {"codigo": "V40", "titulo": "b", "pie": "y"}]}
ag, v = agente(RES_2FOTOS, RealizadorBase())
out = ag.conversar(pedido(mensaje="muéstrame más", memoria={"mostrados": []}))
caso("V1 manda dos fotos y V2 nombra una: no habla", out["v2"]["enviado"], "v1")

# el modelo malo no llega nunca al cliente: cae al texto base
ag, v = agente(RES_REC, RealizadorReescritura(llm_que_dice(lambda c: con_tokens(c).replace("Te paso la foto.", "Cuesta S/ 10 y hay descuento."))))
out = ag.conversar(pedido(mensaje="busco algo para un matrimonio", memoria={"mostrados": []}))
caso("el modelo inventa un precio: la compuerta lo rechaza y sale el texto base (habla V2, sin el invento)",
     (out["v2"]["enviado"], "S/ 10" in out["respuesta"], "descuento" in out["respuesta"], "*V35* Vestido Irla" in out["respuesta"]), ("v2", False, False, True))
intentos = out["v2"]["sombra"]["generacion"]["intentos"]
caso("la traza guarda lo que rechazó la compuerta",
     any("introduce" in e for i in intentos for e in (i.get("errors") or [])), True)
caso("y qué realizador salió al final", out["v2"]["sombra"]["generacion"]["motor"], "semantico/base")


def modelo_caido(c):
    raise TimeoutError("ollama no contesta")


ag, v = agente(RES_REC, RealizadorVariantes(llm_que_dice(modelo_caido)))
out = ag.conversar(pedido(mensaje="busco algo para un matrimonio", memoria={"mostrados": []}))
caso("el modelo no responde: sale el texto base", (out["v2"]["enviado"], "*V35* Vestido Irla" in out["respuesta"]), ("v2", True))
caso("sin modelo ni compuerta de por medio, el texto nunca trae un token", "§" in out["respuesta"], False)

# --- sombra: el texto de V1 no cambia ------------------------------------------------------------------------------------------
ag, v = agente(RES_NOMBRE, RealizadorBase())
ag.modo = "sombra"
out = ag.conversar(pedido(mensaje="me llamo alvaro", modo=""))
caso("en sombra la clienta recibe el texto de V1 aunque V2 pudiera hablar", out["respuesta"], RES_NOMBRE["respuesta"])
caso("y el borrador de V2 va en la traza", "Alvaro" in (out["v2"]["sombra"]["generacion"]["texto"] or ""), True)

# --- configuración -----------------------------------------------------------------------------------------------------------
caso("V2_HABLA por defecto: preguntas y recomendaciones", C.habla_por_defecto({}), ("recomendar", "preguntar"))


def main() -> int:
    for f in fallos:
        print(f)
    print(f"v2 plantillas semánticas (delex, compuerta factual, selector, realizadores): {total - len(fallos)}/{total} casos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
