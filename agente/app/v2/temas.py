"""Cambios de tema: «suspender, no cancelar».

Si el bot preguntó «¿Qué talla usas normalmente?» y la clienta contesta «¿Hacen delivery a Surco?», V1 responde el delivery y se
olvida de la talla (la talla se pregunta una sola vez: `memoria.ya_hecha`), o vuelve a preguntar lo mismo con las mismas palabras.
Aquí el ESTADO resuelve el bucle, no la memoria del modelo: lo que quedó pendiente se guarda en una pila, se responde lo que ella
preguntó y, si importa, se retoma UNA cosa, con otras palabras y con respuestas rápidas.

Todo es código determinista (sin modelo, sin red, sin reloj): el detector, la pila, las prioridades y la decisión de retomar. Un
modelo solo puede cambiar la forma de la frase de retoma (`plantillas.yaml`, `RESUME_*`), como en el resto de V2.

    mensaje ─► ¿contesta lo pendiente? ─ sí ─► se saca de la pila
                     │ no
                     ▼
        ¿cambio total? ──── sí ──► HARD_SWITCH: se cierra la pila, se invalidan los slots que dependían de lo viejo
                     │ no
        ¿pregunta otra cosa? ─ sí ─► SOFT_INTERRUPT (logística) o SIDE_TOPIC (relacionada): el pendiente queda «suspended»
                     ▼
        V1 responde lo que ella preguntó  ──►  ¿hay un pendiente con resume_priority ≥ 50, sin pregunta de V1 en el mismo
        mensaje, sin retomarlo hace poco y sin pasar de 3 intentos?  ── sí ──► se retoma UNO (ANSWER_AND_RESUME)
                                                                        └ no ──► se guarda (`optional_pending` si < 50)

Estado: `memoria.v2.temas` dentro de la ficha que ya viaja entre turnos (el bot Go la guarda como JSON opaco). V1 normaliza la
ficha y tira lo que no conoce, así que V2 lo lee de la petición (antes de V1) y lo vuelve a poner en la respuesta (después de V1).
Está acotado: como mucho `MAX_PENDIENTES` pendientes y `MAX_OFRECIDAS` respuestas rápidas.

Alcance (spec del dueño): cada slot vive en un ámbito. Si cambia de vestido se borra el producto (y quizá el color) pero se conserva la
talla y la zona de entrega; si cambia de ocasión se reemplaza la ocasión y se borra la fecha, el horario y lo que se le recomendó."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from .. import memoria

ACCION = "responder_y_retomar"          # ANSWER_AND_RESUME de la especificación
SOFT, SIDE, HARD = "SOFT_INTERRUPT", "SIDE_TOPIC", "HARD_SWITCH"
NIVELES = (SOFT, SIDE, HARD)
ACTIVO, SUSPENDIDO, OPCIONAL = "active", "suspended", "optional_pending"
ESTADOS = (ACTIVO, SUSPENDIDO, OPCIONAL)

UMBRAL_RETOMA = 50        # solo se retoma solo si resume_priority >= 50: así no se vuelve un interrogatorio
MAX_INTENTOS = 2          # veces que se le pregunta un mismo slot (la original cuenta como la primera): la regla de la regresión del dueño
MAX_INTENTOS_AYUDA = 3    # …más una si ELLA entró en el tema («no sé mi talla», un «sí» ambiguo): aclararle no es perseguirla
ESPERA_RETOMA = 2         # turnos mínimos entre dos retomas, y entre la pregunta y su retoma si no hubo interrupción
VIDA_PENDIENTE = 8        # turnos que vive un pendiente sin que nadie lo toque
MAX_PENDIENTES = 4
MAX_OFRECIDAS = 6
VUELTAS_SIDE = 2          # un tema lateral se contesta como mucho en 2 turnos; luego se retoma

# --- resume_priority por slot (spec: 100 necesario para comprar · 80 para comprobar disponibilidad · 60 importante para recomendar ·
# 40 mejora la recomendación · 20 preferencia opcional · 0 olvidar). La clave es la de V1 (memoria.DETECTOR).
PRIORIDAD = {
    "talla": 85, "ocasion": 80, "fecha": 70, "pago": 70, "separar": 60, "que_busca": 60, "horario": 55, "probar": 55,
    "estatura": 40, "color": 40, "que_le_gusto": 20, "otras_opciones": 20,
}
PILABLES = frozenset(PRIORIDAD)            # preguntas que V1 olvida solas; las persistentes (confirmar, cita…) ya las lleva V1
assert not PILABLES & memoria.PERSISTENTES
TOPIC = {"talla": "size", "ocasion": "occasion", "fecha": "event_date", "horario": "day_night", "pago": "payment",
         "separar": "reserve", "que_busca": "need", "probar": "try_on", "estatura": "height", "color": "color",
         "que_le_gusto": "liked", "otras_opciones": "more_options"}
# Slots de V1 (memoria.sabemos) que solo se pueden retomar con una plantilla (plantillas.yaml, tipo «retoma»).
RETOMABLES = frozenset({"talla", "ocasion", "fecha", "horario"})

# --- ámbito de cada slot (spec): a qué se amarra y, por tanto, cuándo deja de valer.
ALCANCE = {"ocasion": "conversation", "fecha": ("depends_on", "ocasion"), "horario": ("depends_on", "ocasion"),
           "producto": "current_search", "color": "current_search", "prenda": "current_search", "presupuesto": "current_search",
           "talla": "customer", "estatura": "customer", "envio": "conversation", "ciudad": "conversation"}
# Qué se borra de la ficha según lo que cambió. NUNCA la talla, la estatura ni la zona de entrega.
INVALIDA = {"producto": ("producto", "color"), "categoria": ("producto", "color", "prenda"),
            "ocasion": ("fecha", "fecha_iso", "horario", "producto", "color")}
# De esos, lo que SÍ se toca en la ficha de V1, y solo con V2 activo hablando: lo demás es de V1 (en sombra nada se toca).
INVALIDA_EN_FICHA = {"ocasion": ("fecha", "fecha_iso", "horario")}

TEMAS_SOFT = frozenset({"delivery", "pago", "ubicacion", "precio", "promo"})
TEMAS_SIDE = frozenset({"cambios", "material", "objecion", "producto", "talla_info", "otro"})
INTENCION_DEL_TEMA = {"delivery": "ask_delivery", "pago": "ask_payment", "ubicacion": "ask_location", "precio": "ask_price",
                      "promo": "ask_promo", "cambios": "ask_exchange", "material": "ask_material", "objecion": "object",
                      "producto": "ask_product", "talla_info": "ask_size_info", "otro": "ask_other"}

TALLAS_TIENDA = ("S", "M", "L")           # tienda.md: «las tallas son completas y en medidas peruanas (S, M y L)»

_P = memoria._plano


def _re(*alts: str) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(alts) + r")\b")


# Qué tema toca lo que preguntó (por palabras; V1 etiqueta con su clasificador y a veces se equivoca: «¿se lava en lavadora?» →
# consulta_ubicacion). El orden manda: «el costo del envío» es delivery, no precio; «¿aceptan cambios?» es cambios, no pago.
_TEMAS: list[tuple[str, re.Pattern]] = [
    ("cambios", _re(r"cambios?", r"cambiar", r"cambian", r"cambiarlo", r"devol\w+", r"garantia", r"reembols\w+", r"nota de credito",
                    r"si no me queda\w*", r"no me quedaria", r"no me queda")),
    ("delivery", _re(r"delivery", r"deliveri", r"envios?", r"enviar\w*", r"envian", r"despach\w+", r"mandan", r"mandar\w*", r"courier",
                     r"olva", r"shalom", r"a domicilio", r"llegan?", r"llegaria", r"hasta (?:mi casa|provincia)")),
    ("pago", _re(r"yape\w*", r"plin", r"transferenc\w+", r"transferir", r"tarjetas?", r"efectivo", r"contra ?entrega", r"pagar", r"pagos?",
                 r"medios? de pago", r"formas? de pago", r"visa", r"mastercard")),
    ("ubicacion", _re(r"donde (?:quedan|estan|queda|es)", r"direccion", r"ubicacion", r"ubicad\w+", r"showroom", r"horarios?", r"abren",
                      r"atienden", r"tienda fisica", r"a que hora (?:abren|atienden|cierran)")),
    ("promo", _re(r"descuentos?", r"promos?", r"promocion\w*", r"ofertas?", r"rebajas?", r"2x1", r"cupon\w*")),
    ("precio", _re(r"cuanto (?:cuesta\w*|esta\w*|sale\w*|vale\w*|es|cobran)", r"precios?", r"cuestan?", r"costos?", r"vale")),
    ("material", _re(r"telas?", r"materiales?", r"material", r"lava", r"lavar\w*", r"lavadora", r"lavo", r"plancha\w*", r"arruga\w*",
                     r"se estira", r"estira", r"elastic\w*", r"transparent\w*", r"forro", r"forrado", r"abriga", r"calurosa?",
                     r"de que (?:es|esta hecho|tela)")),
    ("talla_info", _re(r"tabla de tallas", r"guia de tallas", r"como (?:calza|queda|talla)", r"es holgad\w+", r"es ajustad\w+",
                       r"holgad\w+", r"ajustad\w+", r"apretad\w+", r"talla (?:real|grande|chica)", r"mide el \w+")),
    ("producto", _re(r"otros? colou?res?", r"colou?res", r"en otro colou?r", r"mas opciones", r"otras opciones", r"otros? modelos?",
                     r"hay en \w+", r"tienen en \w+")),
    ("objecion", _re(r"muy caro", r"esta caro", r"caro", r"lo voy a pensar", r"lo pensare", r"lo pienso", r"no se si (?:comprar|llevar)",
                     r"dudo")),
]
# Intenciones de V1 que, sin palabras reconocibles, apuntan a un tema.
_INTENT_A_TEMA = {"consulta_delivery": "delivery", "consulta_pago": "pago", "consulta_ubicacion": "ubicacion",
                  "consulta_horario": "ubicacion", "consulta_precio": "precio", "consulta_material": "material",
                  "consulta_color": "producto", "consulta_disponibilidad": "producto", "consulta_talla": "talla_info",
                  "objecion_precio": "objecion", "objecion": "objecion"}

# Cambio total: rechaza lo que veníamos viendo o cambia el rumbo.
RE_PIVOTE = re.compile(
    r"\bya no (?:quiero|me interesa|me gusta|busco|lo quiero|la quiero|necesito)\b|"
    r"\b(?:mejor|ahora|mas bien) (?:muestrame|busco|quiero|dame|vamos con|veamos|algo|otro|otra|necesito|enseñame|ensename)\b|"
    r"\bcambie de (?:idea|opinion|parecer)\b|\bcambio de planes\b|\bolvida(?:lo| eso| lo que)\b|"
    r"\balgo (?:diferente|distinto)\b|\ben vez de (?:ese|este|eso|esa|esta)\b|\ben realidad (?:busco|quiero|es para|necesito)\b|"
    r"\bno (?:me convence|me sirve|era ese) (?:ese|esa|este|esta|el|la)\b|\bno,? ese no\b")
RE_DESPEDIDA = re.compile(r"\b(?:chau|chao|adios|hasta luego|bye|nos vemos|buenas noches|hasta manana)\b")   # «gracias» a secas es un acuse
# «sí», «claro», «dale» a «¿Qué talla usas: S, M o L?» no dice cuál. «ok», «ya», «listo» son un acuse, no una respuesta ambigua.
RE_SI_ROTUNDO = re.compile(r"^(?:s+i+p?|claro|dale|por supuesto|de acuerdo|si+ (?:por favor|porfa|claro))\W*$")
RE_ACUSE = re.compile(r"^(?:ok\w*|ya+|yap|dale|listo|ah+ ya|ah+|mmm+|jaja\w*|jeje\w*|aja+|entiendo|claro|perfecto|genial|bueno|vale|bien)\W*$")
# Talla: no sabe, o duda entre dos.
RE_NO_SABE_TALLA = re.compile(r"\bno (?:se|sabria|conozco|tengo claro|tengo idea)\b.{0,24}\b(?:talla|medida|medidas)\b|\bni idea\b|"
                              r"\bno (?:estoy|ando) segur[oa]\b|\bno me acuerdo\b|\bnose\b|\bno se cual\b|\bno se que talla\b|\bno se cual es")
_ALIAS_TALLA = (("grande", "L"), ("large", "L"), ("mediana", "M"), ("medium", "M"), ("chica", "S"), ("pequena", "S"), ("small", "S"))
RE_LETRA_TALLA = re.compile(r"(?<![\w/.])(xxl|xl|xs|s|m|l)(?![\w/])")


def tallas_en(texto: str) -> list[str]:
    """Las tallas que ella nombra, en el orden en que las dice: letras sueltas («creo q m») y alias («de busto soy grande» → L)."""
    p = _P(texto)
    # «1.65 m» es una estatura, no la talla M
    vistas: list[tuple[int, str]] = [(m.start(), m.group(1).upper()) for m in RE_LETRA_TALLA.finditer(p)
                                     if not (m.group(1) in ("m", "l") and re.search(r"\d\s*$", p[:m.start()]))]
    for palabra, talla in _ALIAS_TALLA:
        for m in re.finditer(rf"\b{palabra}\b", p):
            vistas.append((m.start(), talla))
    out: list[str] = []
    for _, t in sorted(vistas):
        if t in memoria.TALLAS and t not in out:
            out.append(t)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Respuestas rápidas (quick replies) y su interpretación determinista

def rapidas_de(slot: str, ayuda: str | None = None) -> list[dict]:
    """[{label, payload:{intent, …}}] del slot que se retoma. Reducen el trabajo del clasificador; ella puede seguir escribiendo libre.
    Cada etiqueta, escrita tal cual, también se entiende con las reglas de V1 (una prueba lo exige): así sirven en WhatsApp, donde
    pulsar un botón llega como texto."""
    if slot == "talla":
        out = [{"label": f"Soy talla {t}", "payload": {"intent": "provide_size", "size": t}} for t in TALLAS_TIENDA]
        out += [{"label": "No sé mi talla", "payload": {"intent": "size_unknown"}},
                {"label": "Te paso mis medidas", "payload": {"intent": "share_measurements"}}]
        return out
    if slot == "horario":
        return [{"label": "De día", "payload": {"intent": "provide_day_night", "when": "dia"}},
                {"label": "De noche", "payload": {"intent": "provide_day_night", "when": "noche"}}]
    if slot == "fecha":
        return [{"label": "Es esta semana", "payload": {"intent": "provide_date", "when": "esta_semana"}},
                {"label": "Es el próximo mes", "payload": {"intent": "provide_date", "when": "proximo_mes"}},
                {"label": "Aún no tengo fecha", "payload": {"intent": "provide_date", "when": "sin_fecha"}}]
    if slot == "ocasion":
        return [{"label": "Es para un matrimonio", "payload": {"intent": "provide_occasion", "occasion": "matrimonio"}},
                {"label": "Es para una graduación", "payload": {"intent": "provide_occasion", "occasion": "graduacion"}},
                {"label": "Es para un cumpleaños", "payload": {"intent": "provide_occasion", "occasion": "cumpleanos"}},
                {"label": "Es para el día a día", "payload": {"intent": "provide_occasion", "occasion": "diario"}}]
    return []


# intención del payload → (slot que resuelve, qué dato de sabemos fija)
_PAYLOAD_SLOT = {"provide_size": "talla", "size_unknown": "talla", "share_measurements": "talla", "provide_day_night": "horario",
                 "provide_date": "fecha", "provide_occasion": "ocasion"}
_PAYLOAD_VALORES = {"provide_size": ("size", set(memoria.TALLAS)), "provide_day_night": ("when", {"dia", "noche"}),
                    "provide_date": ("when", {"esta_semana", "proximo_mes", "sin_fecha"}),
                    "provide_occasion": ("occasion", {k for k, _ in memoria.OCASIONES})}


def validar_payload(p) -> dict | None:
    """El payload ya limpio, o None si no es uno de los que ofrecemos (un payload raro se ignora, nunca revienta)."""
    if not isinstance(p, dict) or not isinstance(p.get("intent"), str) or p["intent"] not in _PAYLOAD_SLOT:
        return None
    out = {"intent": p["intent"]}
    if p["intent"] in _PAYLOAD_VALORES:
        clave, validos = _PAYLOAD_VALORES[p["intent"]]
        v = p.get(clave)
        v = v.upper() if clave == "size" and isinstance(v, str) else v
        if not isinstance(v, str) or v not in validos:
            return None
        out[clave] = v
    return out


def interpretar_rapida(mensaje: str, payload, ofrecidas: list[dict]) -> dict | None:
    """Respuesta rápida → su payload, SIN clasificador y SIN modelo: o llegó el payload (web), o lo que escribió es EXACTAMENTE la
    etiqueta de un botón que le ofrecimos (WhatsApp: el botón llega como texto). Cualquier otra cosa es texto libre → None."""
    p = validar_payload(payload)
    if p:
        return dict(p, fuente="payload")
    t = _P(mensaje).strip(" .!¡¿?")
    for o in ofrecidas or []:
        if isinstance(o, dict) and _P(str(o.get("label", ""))).strip(" .!¡¿?") == t and (v := validar_payload(o.get("payload"))):
            return dict(v, fuente="etiqueta")
    return None


# ---------------------------------------------------------------------------------------------------------------------
# Estado (pila de temas pendientes)

def estado_nuevo() -> dict:
    return {"v": 1, "turno": 0, "pendientes": [], "actual": None, "ultima_retoma": None, "ofrecidas": []}


def prioridad(slot: str, etapa: str = "", hay_prenda: bool = True) -> int:
    """resume_priority del slot en este momento. La talla sube a 100 en el cierre (sin ella no hay compra) y baja a 60 si todavía
    no hay una prenda a la vista (solo sirve para recomendar)."""
    base = PRIORIDAD.get(slot, 0)
    if slot == "talla":
        if etapa == "cierre":
            return 100
        if not hay_prenda:
            return 60
    return base


def _entero(v, lo: int, hi: int, defecto: int) -> int:
    return max(lo, min(hi, v)) if isinstance(v, int) and not isinstance(v, bool) else defecto


def normalizar_estado(x) -> dict:
    """El estado que llegó de fuera, con la forma que el resto del código da por buena. Lo que no cuadra se descarta."""
    base = estado_nuevo()
    if not isinstance(x, dict):
        return base
    base["turno"] = _entero(x.get("turno"), 0, 10 ** 6, 0)
    vistos: set[str] = set()
    for e in x.get("pendientes") if isinstance(x.get("pendientes"), list) else []:
        if not isinstance(e, dict) or e.get("slot") not in PILABLES or e["slot"] in vistos or e.get("status") not in ESTADOS:
            continue
        vistos.add(e["slot"])
        base["pendientes"].append({
            "topic": TOPIC[e["slot"]], "slot": e["slot"],
            "question": str(e.get("question") or "")[:140], "status": e["status"],
            "priority": _entero(e.get("priority"), 0, 100, PRIORIDAD[e["slot"]]),
            "attempts": _entero(e.get("attempts"), 0, 9, 1),
            "desde": _entero(e.get("desde"), 0, 10 ** 6, base["turno"]), "ask": _entero(e.get("ask"), 0, 10 ** 6, base["turno"])})
    base["pendientes"] = base["pendientes"][:MAX_PENDIENTES]
    a = x.get("actual")
    if isinstance(a, dict) and a.get("nivel") in (SOFT, SIDE) and isinstance(a.get("topic"), str):
        base["actual"] = {"topic": a["topic"][:24], "nivel": a["nivel"], "interrupted_topic": str(a.get("interrupted_topic") or "")[:24],
                          "turnos": _entero(a.get("turnos"), 1, 9, 1)}
    r = x.get("ultima_retoma")
    if isinstance(r, dict) and r.get("slot") in PILABLES:
        base["ultima_retoma"] = {"slot": r["slot"], "turno": _entero(r.get("turno"), 0, 10 ** 6, 0)}
    base["ofrecidas"] = [{"label": str(o["label"])[:40], "payload": p} for o in (x.get("ofrecidas") if isinstance(x.get("ofrecidas"), list) else [])
                         if isinstance(o, dict) and isinstance(o.get("label"), str) and (p := validar_payload(o.get("payload")))][:MAX_OFRECIDAS]
    return base


def pendiente_inferida(estado: dict) -> str:
    """La pregunta que V2 hizo en el turno anterior, si V1 no la tiene pendiente. En sombra la retoma no sale (la ficha de V1 no sabe que se
    preguntó la talla), pero la pila simula que sí: así un «sí» a las respuestas rápidas se lee como lo que habría sido."""
    return next((x["slot"] for x in estado.get("pendientes", []) if x["status"] == ACTIVO and x["ask"] == estado.get("turno")), "")


def resumen(estado: dict) -> list[dict]:
    """Los pendientes en la forma corta que se ve en la traza: {slot, status, priority, attempts}."""
    return [{k: e[k] for k in ("slot", "status", "priority", "attempts")} for e in estado.get("pendientes", [])]


# ---------------------------------------------------------------------------------------------------------------------
# Lo que ve el detector de un turno

@dataclass
class Turno:
    mensaje: str = ""
    pend_antes: str = ""                  # pregunta que V1 tenía pendiente ANTES de leer este mensaje
    respondio: bool = False               # V1 lo leyó como respuesta a esa pendiente
    espera: bool = False                  # «un momento, ya te digo»
    datos: dict = field(default_factory=dict)          # lo que V1 sacó de ESTE mensaje
    sabemos_antes: dict = field(default_factory=dict)
    sabemos: dict = field(default_factory=dict)        # la ficha después del turno
    intent: str = ""                      # intención comercial de V1
    etapa: str = ""
    respuesta: str = ""                   # el texto que se va a enviar (para saber si ya trae una pregunta)
    flujo_fijo: bool = False
    estado_go: str = ""                   # el flujo de Go en curso (esperando_talla…): él lleva su propio hilo
    primer_mensaje: bool = False
    hay_prenda: bool = True
    rapida: dict | None = None            # respuesta rápida ya interpretada (interpretar_rapida)


def _tema_de(p: str, intent: str, hay_pregunta: bool) -> str | None:
    for nombre, rx in _TEMAS:
        if rx.search(p):
            return nombre
    tema = _INTENT_A_TEMA.get(intent)
    if tema and hay_pregunta:
        return tema
    if hay_pregunta and intent in ("consulta_producto",):
        return "otro"
    return None


def _temas_en(p: str) -> list[str]:
    return [n for n, rx in _TEMAS if rx.search(p)]


def detectar(t: Turno) -> dict:
    """Qué hizo ella con la pregunta que tenía pendiente. {nivel, tipo, tema, ayuda, cambio, tambien, causa}.

    tipo: «responde» (contestó lo pendiente) · «interrumpe» (pregunta otra cosa) · «cambio» (HARD_SWITCH) · «ayuda» (no sabe, duda o
    contesta ambiguo: sigue con el mismo tema) · «ninguno»."""
    p = _P(t.mensaje)
    pend = t.pend_antes if t.pend_antes in PILABLES else ""
    ev = {"nivel": None, "tipo": "ninguno", "tema": None, "ayuda": None, "cambio": None, "tambien": [], "causa": "", "tallas": []}
    # 0) respuesta rápida: ya sabemos qué es, sin leer el texto
    if t.rapida:
        slot = _PAYLOAD_SLOT[t.rapida["intent"]]
        if t.rapida["intent"] == "size_unknown":
            return dict(ev, tipo="ayuda", ayuda="no_sabe", causa="respuesta rápida: no sé mi talla")
        if t.rapida["intent"] == "share_measurements":
            return dict(ev, tipo="ayuda", ayuda="medidas", causa="respuesta rápida: te paso mis medidas")
        return dict(ev, tipo="responde", causa=f"respuesta rápida: {t.rapida['intent']}", slot=slot)
    # 1) cambio total
    cambio = _cambio_total(t, p)
    if cambio:
        return dict(ev, nivel=HARD, tipo="cambio", cambio=cambio[0], causa=cambio[1])
    # 2) no sabe, duda o contesta ambiguo a la talla
    if pend == "talla":
        a = _ayuda_talla(t, p)
        if a:
            return dict(ev, tipo="ayuda", ayuda=a[0], tallas=a[1], causa=a[2])
    hay_pregunta = "?" in t.mensaje or "¿" in t.mensaje
    temas = _temas_en(p)
    tema = _tema_de(p, t.intent, hay_pregunta)
    # 3) contestó lo pendiente (y a lo mejor pregunta otra cosa además: se contesta, no hay nada que suspender)
    if pend and t.respondio:
        return dict(ev, tipo="responde", tambien=temas or ([tema] if tema else []), causa="contestó la pregunta pendiente")
    if t.espera:
        return dict(ev, tipo="ninguno", causa="pidió un momento")
    # 4) pregunta otra cosa
    if tema and (temas or hay_pregunta):
        nivel = SOFT if all(x in TEMAS_SOFT for x in (temas or [tema])) else SIDE
        return dict(ev, nivel=nivel, tipo="interrumpe", tema=(temas or [tema])[0], tambien=temas[1:],
                    causa="pregunta por " + ", ".join(temas or [tema]))
    if (RE_DESPEDIDA.search(p) or t.intent == "despedida") and not hay_pregunta:
        return dict(ev, causa="se despide")
    return dict(ev, causa="acuse" if RE_ACUSE.match(p) else "sin relación con lo pendiente")


def _cambio_total(t: Turno, p: str) -> tuple[str, str] | None:
    """(qué cambió, por qué) si es un cambio de rumbo; None si no. «Qué cambió» decide qué slots dejan de valer."""
    previa = (t.sabemos_antes or {}).get("ocasion")
    nueva = (t.datos or {}).get("ocasion")
    pivote = RE_PIVOTE.search(p)
    ocasion_cambia = bool(nueva and previa and nueva != previa and nueva not in memoria.GENERICAS and t.pend_antes != "ocasion")
    if not (pivote or ocasion_cambia):
        return None
    if ocasion_cambia:
        return "ocasion", f"cambió la ocasión: {previa} → {nueva}"
    prenda_antes, prenda_nueva = (t.sabemos_antes or {}).get("prenda"), (t.datos or {}).get("prenda")
    if prenda_nueva and prenda_antes and prenda_nueva != prenda_antes:
        return "categoria", f"cambió de prenda: {prenda_antes} → {prenda_nueva}"
    return "producto", "rechaza lo que veníamos viendo"


def _ayuda_talla(t: Turno, p: str) -> tuple[str, list[str], str] | None:
    """(tipo de ayuda, tallas que ella nombra, causa). «No sé mi talla» · «creo q m pero de busto soy grande» · un «sí» a una
    pregunta que no es de sí o no."""
    tallas = tallas_en(t.mensaje)
    if RE_NO_SABE_TALLA.search(p) and "?" not in t.mensaje:
        return "no_sabe", tallas, "no sabe su talla"
    if len(tallas) >= 2:
        return "dudosa", tallas[:2], f"duda entre {' y '.join(tallas[:2])}"
    if RE_SI_ROTUNDO.match(p) and not t.respondio:
        return "ambigua", [], "dijo «sí» a una pregunta que no es de sí o no"
    return None


# ---------------------------------------------------------------------------------------------------------------------
# La pila: aplicar el turno y decidir si se retoma

def _buscar(e: dict, slot: str) -> dict | None:
    return next((x for x in e["pendientes"] if x["slot"] == slot), None)


def _entrada(slot: str, turno: int, t: Turno) -> dict:
    return {"topic": TOPIC[slot], "slot": slot, "question": memoria.PREGUNTAS.get(slot, "")[:140], "status": SUSPENDIDO,
            "priority": prioridad(slot, t.etapa, t.hay_prenda), "attempts": 0, "desde": turno, "ask": turno}


def _resuelta(en: dict, t: Turno) -> bool:
    dato = memoria.DATO_DE.get(en["slot"])
    if dato and (t.sabemos or {}).get(dato):
        return True
    if en["slot"] == "que_busca":                      # «¿qué buscas?» se contesta diciendo la prenda o la ocasión
        return bool((t.sabemos or {}).get("prenda") or (t.sabemos or {}).get("ocasion"))
    return not dato and en["slot"] == t.pend_antes and t.respondio


def _pregunta_de_slot(texto: str, slot: str) -> str:
    return next((q for q in reversed(memoria.preguntas_en(texto)) if memoria.clave_de(q) == slot), "")[:140]


def evaluar(previo: dict, t: Turno) -> dict:
    """Aplica el turno a la pila y decide si hay que retomar algo.

    Devuelve {evento, estado, estado_enviado, retoma, bloqueo, invalida, v1_retomo}:
    - `estado`: la pila si NO se retoma (o la retoma no pudo salir).
    - `estado_enviado`: la pila si la retoma SÍ sale (un intento más, activa, espaciada, con sus respuestas rápidas).
    - `retoma`: {slot, modo, prioridad, intento, ayuda, tallas, tema} o None; `bloqueo` dice por qué no hay retoma."""
    e = copy.deepcopy(previo)
    e["turno"] += 1
    n = e["turno"]
    e["ofrecidas"] = []                                          # las respuestas rápidas del turno anterior ya se leyeron
    ev = detectar(t)
    pend = t.pend_antes if t.pend_antes in PILABLES else ""
    invalida: list[str] = []

    # 1) lo que ya se contestó sale de la pila (salvo la talla de quien duda: el dato que V1 anotó es una suposición suya)
    duda = ev["tipo"] == "ayuda" and ev["ayuda"] == "dudosa" and pend == "talla"
    resueltas = [x for x in e["pendientes"] if _resuelta(x, t) and not (duda and x["slot"] == "talla")]
    e["pendientes"] = [x for x in e["pendientes"] if x not in resueltas]
    # Contestó un pendiente de la pila con este mismo mensaje aunque V1 ya no lo tuviera pendiente («soy talla M» tras haber preguntado
    # otra cosa): cuenta como respuesta, y si además pregunta otra cosa, se contesta y no hay nada que retomar de eso.
    dadas = [x["slot"] for x in resueltas if (dato := memoria.DATO_DE.get(x["slot"])) and (t.datos or {}).get(dato)]
    if dadas and ev["tipo"] == "ninguno":
        ev = dict(ev, tipo="responde", causa="contestó " + ", ".join(dadas) + " (estaba en la pila)")
    elif dadas:
        ev = dict(ev, tambien_responde=dadas)
    # 2) lo que hizo con la pregunta pendiente
    if ev["nivel"] == HARD:
        invalida = list(INVALIDA.get(ev["cambio"], ()))
        e["pendientes"] = []                                     # «no retomar preguntas viejas»
    elif pend and ev["tipo"] in ("interrumpe", "ayuda") and (not t.respondio or duda):
        x = _buscar(e, pend)
        if x is None:
            nueva = _entrada(pend, n, t)
            nueva["attempts"] = 1                                # la preguntó V1 en un turno anterior
            if duda or not _resuelta(nueva, t):                  # (si ya la sabemos porque la contestó antes, no hay nada que suspender)
                e["pendientes"].append(nueva)
                x = nueva
        if x is not None:
            x["status"] = SUSPENDIDO if ev["tipo"] == "interrumpe" else ACTIVO     # con «no sé» sigue el MISMO tema: no se suspende
    # 3) lo que V1 pregunta en este mismo mensaje cuenta como pregunta hecha (y si ya estaba en la pila, V1 la retomó solito)
    slot_v1 = memoria.pregunta_de(t.respuesta) if t.respuesta else ""
    v1_retomo = False
    if slot_v1 in PILABLES:
        x = _buscar(e, slot_v1)
        v1_retomo = x is not None
        if x is None:
            x = _entrada(slot_v1, n, t)
            e["pendientes"].append(x)
        x["attempts"] = min(9, x["attempts"] + 1)
        x["status"], x["ask"] = ACTIVO, n
        x["question"] = _pregunta_de_slot(t.respuesta, slot_v1) or x["question"]
    # 4) lo que V1 no volvió a preguntar queda suspendido (o «optional_pending» si importa poco); lo viejo se olvida
    for x in e["pendientes"]:
        sigue_activa = x["slot"] == slot_v1 or (ev["tipo"] == "ayuda" and x["slot"] == pend)
        if x["status"] == ACTIVO and not sigue_activa:
            x["status"] = SUSPENDIDO
        x["priority"] = prioridad(x["slot"], t.etapa, t.hay_prenda)
        if x["status"] == SUSPENDIDO and x["priority"] < UMBRAL_RETOMA:
            x["status"] = OPCIONAL                                # se guarda, pero no interrumpe a la clienta
        elif x["status"] == OPCIONAL and x["priority"] >= UMBRAL_RETOMA:
            x["status"] = SUSPENDIDO
    e["pendientes"] = [x for x in e["pendientes"] if n - x["ask"] <= VIDA_PENDIENTE and x["priority"] > 0]
    e["pendientes"].sort(key=lambda x: (-x["priority"], x["desde"]))
    e["pendientes"] = e["pendientes"][:MAX_PENDIENTES]
    # 5) tema lateral en curso (un SIDE_TOPIC puede tomar varios turnos); pedir un momento no lo cierra
    previo_actual = e.get("actual")
    if ev["tipo"] == "interrumpe":
        sigue_lateral = bool(previo_actual and previo_actual["nivel"] == SIDE and ev["nivel"] == SIDE)
        e["actual"] = {"topic": ev["tema"] or "otro", "nivel": ev["nivel"], "interrupted_topic": TOPIC.get(pend, ""),
                       "turnos": min(9, previo_actual["turnos"] + 1) if sigue_lateral else 1}
    elif not t.espera:
        e["actual"] = None
    # 6) ¿se retoma algo?
    retoma, bloqueo = _candidata(e, ev, t, n, slot_v1, v1_retomo)
    con = copy.deepcopy(e)
    if retoma:
        x = _buscar(con, retoma["slot"])
        x["attempts"] = min(9, x["attempts"] + 1)
        x["status"], x["ask"] = ACTIVO, n
        x["question"] = memoria.PREGUNTAS.get(retoma["slot"], "")[:140]
        con["ultima_retoma"] = {"slot": retoma["slot"], "turno": n}
        con["actual"] = None
        con["ofrecidas"] = [{"label": r["label"], "payload": r["payload"]} for r in rapidas_de(retoma["slot"])][:MAX_OFRECIDAS]
    if v1_retomo:
        e["ultima_retoma"] = con["ultima_retoma"] = {"slot": slot_v1, "turno": n}
    return {"evento": ev, "estado": e, "estado_enviado": con, "retoma": retoma, "bloqueo": bloqueo, "invalida": invalida,
            "v1_retomo": v1_retomo}


def _candidata(e: dict, ev: dict, t: Turno, n: int, slot_v1: str, v1_retomo: bool) -> tuple[dict | None, str]:
    """(retoma, bloqueo). Una sola retoma por mensaje, la de mayor prioridad. Solo se retoma tras contestar lo que ella preguntó."""
    ayuda = ev["tipo"] == "ayuda" and ev["ayuda"] in ("no_sabe", "dudosa", "ambigua")
    if v1_retomo:
        return None, "V1 ya retomó el pendiente"
    if t.flujo_fijo:
        return None, "flujo fijo de código"
    if t.estado_go:
        return None, "el flujo de Go lleva su propio hilo"
    if t.etapa == "venta_confirmada":
        return None, "pedido confirmado"
    if t.primer_mensaje:
        return None, "primer mensaje"
    if not (t.respuesta or "").strip():
        return None, "V1 no contestó nada"
    if slot_v1 or memoria.preguntas_en(t.respuesta or ""):
        return None, "V1 ya pregunta algo en este mensaje"
    if ev["nivel"] == HARD:
        return None, "cambio total: no se retoman preguntas viejas"
    if ev["tipo"] == "responde":
        return None, "contestó lo pendiente"
    if ev["tipo"] == "ayuda" and not ayuda:
        return None, "se ocupa de sus medidas (V1)"
    if t.espera:
        return None, "pidió un momento"
    if ev["tipo"] == "ninguno" and ev["causa"] == "se despide":
        return None, "se despide: no se persigue"
    if memoria.niega(t.mensaje):
        return None, "dijo que no"
    # SIDE_TOPIC: se contesta primero; la retoma llega en otro mensaje (cuando deja de preguntar o tras VUELTAS_SIDE turnos)
    if ev["tipo"] == "interrumpe" and ev["nivel"] == SIDE and e["actual"] and e["actual"]["turnos"] < VUELTAS_SIDE:
        return None, "tema lateral en curso: se contesta primero y se retoma después"
    cand = [x for x in e["pendientes"] if x["slot"] in RETOMABLES]
    if not cand:
        motivos = []
        if bajos := [x["slot"] for x in e["pendientes"] if x["status"] == OPCIONAL]:
            motivos.append(f"prioridad < {UMBRAL_RETOMA}: se guarda, no se persigue ({', '.join(bajos)})")
        if sin := [x["slot"] for x in e["pendientes"] if x["status"] == SUSPENDIDO]:
            motivos.append(f"sin retoma escrita para: {', '.join(sin)}")
        return None, " · ".join(motivos) or "nada pendiente que se pueda retomar"
    ult = e.get("ultima_retoma")
    if ult and n - ult["turno"] < ESPERA_RETOMA and not ayuda:       # si ella pide ayuda con lo que acabamos de preguntar, se la damos
        return None, "se retomó hace un turno: se espacia"
    pendientes_ayuda = [x for x in cand if x["slot"] == "talla" and x["status"] == ACTIVO] if ayuda else []
    posibles = pendientes_ayuda or [x for x in cand if x["status"] == SUSPENDIDO]
    if not posibles:
        bajos = [x for x in cand if x["status"] == OPCIONAL]
        return None, f"prioridad < {UMBRAL_RETOMA}: se guarda, no se persigue" if bajos else "nada suspendido"
    tope = MAX_INTENTOS_AYUDA if ayuda else MAX_INTENTOS
    posibles = [x for x in posibles if x["attempts"] < tope]
    if not posibles:
        return None, f"ya se le preguntó {tope} veces: no se persigue"
    if ev["tipo"] == "ninguno":
        posibles = [x for x in posibles if n - x["ask"] >= ESPERA_RETOMA]
        if not posibles:
            return None, "sin interrupción: se le da un turno para contestar"
    x = max(posibles, key=lambda y: (y["priority"], -y["desde"]))
    ayuda_tipo = ev["ayuda"] if ayuda else None
    modo = "ayuda" if ayuda_tipo else _modo(x, ev, t)
    return {"slot": x["slot"], "topic": x["topic"], "modo": modo, "prioridad": x["priority"], "intento": x["attempts"] + 1,
            "ayuda": ayuda_tipo, "tallas": list(ev.get("tallas") or []), "tema": ev.get("tema"),
            "answer_intent": INTENCION_DEL_TEMA.get(ev.get("tema") or "", None)}, ""


def _modo(x: dict, ev: dict, t: Turno) -> str:
    """obligatorio: sin eso no se compra (talla antes de separar, o va camino a pagar) · útil: se retoma suave."""
    if x["priority"] >= 90 or (x["slot"] == "talla" and (ev.get("tema") == "pago" or t.intent in ("intencion_compra", "confirmacion_compra", "consulta_pago"))):
        return "obligatorio"
    return "util"


# ---------------------------------------------------------------------------------------------------------------------
# Invalidar slots dependientes (HARD_SWITCH) — solo con V2 activo

def aplicar_invalidacion(mem: dict, cambio: str, datos: dict) -> list[str]:
    """Borra de la ficha de V1 lo que dependía de la ocasión vieja (fecha, día/noche) y lo marca como no preguntado, para que V1 lo
    vuelva a preguntar. NUNCA toca la talla, la estatura ni la zona de entrega. Si en el mismo mensaje trajo el dato nuevo, ese se respeta.
    Devuelve los campos que borró."""
    borrados: list[str] = []
    trajo_fecha = bool(datos.get("fecha") or datos.get("fecha_iso"))      # V1 anota la fecha y su ISO juntas
    for k in INVALIDA_EN_FICHA.get(cambio, ()):
        if datos.get(k) or not (mem.get("sabemos") or {}).get(k) or (trajo_fecha and k in ("fecha", "fecha_iso")):
            continue
        mem["sabemos"][k] = None
        borrados.append(k)
    if borrados:
        mem["preguntado"] = [x for x in (mem.get("preguntado") or []) if x not in ("fecha", "horario")]
    return borrados


def pregunta_canonica(slot: str) -> str:
    """El texto con el que V1 hace esa pregunta por defecto (solo para la traza y el control de calidad: la retoma usa otras palabras)."""
    return memoria.PREGUNTAS.get(slot, "")


def registrar_en_ficha(mem: dict, texto_final: str) -> str:
    """Anota en la ficha de V1 lo que ahora espera el bot, con la MISMA función con la que V1 anota sus preguntas
    (`memoria.registrar_respuesta`): la clave sale de la última pregunta del texto (`memoria.clave_de`). Así, si ella contesta «M», V1 lo
    lee como respuesta a la talla. Solo se llama cuando la retoma de V2 de verdad salió."""
    if not isinstance(mem.get("preguntado"), list) or not isinstance(mem.get("sabemos"), dict):
        return mem.get("pendiente", "")
    return memoria.registrar_respuesta(mem, texto_final)


def olvidar_talla_adivinada(mem: dict) -> bool:
    """Dudó entre dos tallas y V1 anotó una de las dos por su cuenta: hasta que ella elija, no hay talla (así ningún pedido sale con una
    talla que no confirmó). Solo con V2 activo hablando; V1 la vuelve a anotar en cuanto ella conteste."""
    if isinstance(mem.get("sabemos"), dict) and mem["sabemos"].get("talla"):
        mem["sabemos"]["talla"] = None
        return True
    return False


def aplicar_talla(mem: dict, talla: str) -> bool:
    """Pulsó «Soy talla M»: es un dato dicho por ella con todas las letras. Si V1 no lo anotó, se anota."""
    if talla in memoria.TALLAS and isinstance(mem.get("sabemos"), dict) and mem["sabemos"].get("talla") != talla:
        mem["sabemos"]["talla"] = talla
        return True
    return False
