"""Memoria de la conversación: la ficha que el agente lleva de cada clienta y el hilo de la última pregunta.

Antes el agente releía los últimos 8–14 mensajes y adivinaba qué pasaba: tras «¿me compartes la foto o el
nombre?», un «oh sí» llegaba al LLM, que inventaba un vestido; volvía a preguntar la ocasión ya contestada;
el «sí» dependía de cómo había redactado su última pregunta. Cada síntoma tenía su parche. Esto los junta:

1. **Pregunta pendiente (el hilo).** Cada vez que el bot pregunta algo se anota qué espera (`pendiente`).
   El mensaje siguiente se lee PRIMERO como respuesta a esa pregunta: si la responde, se guarda el dato y
   se limpia; si no, sigue pendiente y nadie inventa la respuesta.
2. **Extraer, no adivinar.** De cada mensaje se sacan con reglas los datos que trae (talla, fecha, estatura,
   ciudad, Lima/provincia, ocasión, día/noche, color, presupuesto). Jev, si está activo, propone lo cerrado
   (ocasión, talla, día/noche, «¿responde a la pendiente?») en la misma llamada de la cascada; las reglas mandan.
3. **La siguiente pregunta la elige el código** (`siguiente`): la primera de la etapa que no se sepa ni se
   haya preguntado ya. El LLM la recibe hecha y la usa tal cual o no pregunta nada.

El agente sigue sin estado: la memoria llega en la petición y vuelve en la respuesta, como la etapa. Si no
llega (llamadas viejas), se reconstruye repasando el historial con las mismas funciones.

4. **Método de venta** (04-10-2026): primero se indaga la necesidad (ocasión → fecha → día/noche), se calcula la
   **temperatura** de la clienta por la fecha del evento y sus señales (`temperatura`), y el cierre ofrece separarlo
   o pasar a probárselo con **cita** en el showroom (`leer_cita`, `validar_cita`).

Sin dependencias: se prueba con `python3 -m app.prueba_memoria`.
"""
from __future__ import annotations

import copy
import datetime as _dt
import re
import unicodedata

try:   # la imagen slim puede no traer tzdata: Perú no tiene horario de verano, UTC-5 fijo da lo mismo
    from zoneinfo import ZoneInfo
    LIMA = ZoneInfo("America/Lima")
except Exception:  # noqa: BLE001
    LIMA = _dt.timezone(_dt.timedelta(hours=-5), "America/Lima")

CAMPOS = ("ocasion", "horario", "fecha", "fecha_iso", "prenda", "talla", "estatura", "color", "presupuesto", "envio",
          "ciudad", "le_gusto", "cita", "nombre")
TEMPERATURAS = ("frio", "tibio", "caliente")


def ahora_lima() -> _dt.datetime:
    return _dt.datetime.now(LIMA)


def nueva() -> dict:
    return {"etapa": "prospeccion", "producto": "", "mostrados": [], "pendiente": "",
            "sabemos": {k: None for k in CAMPOS}, "objeciones": [], "llego_por": "", "preguntado": [],
            # Temperatura de la clienta (reglas, no LLM) y las señales que la explican, en orden.
            "temperatura": "frio", "temperatura_motivo": SIN_DATOS, "senales": [],
            # Cita para probarse que se está armando: día y hora sueltos hasta que los dos valen.
            "cita_tentativa": {"dia": None, "hora": None},
            # Pidió ver modelos antes de contar su necesidad: se le preguntó la ocasión una vez; con la respuesta, se
            # le muestra ya (sin esperar fecha y día/noche). Si insiste sin contestar, se le muestra igual.
            "pidio_ver": False,
            # Talla de sus pedidos anteriores (perfil): se le SUGIERE al preguntar («¿en M, como tu pedido anterior?»);
            # no es la talla de hoy hasta que ella lo diga. Antes prellenaba `sabemos.talla` y armaba el pedido con una
            # talla que no dijo (WhatsApp real, 04-10-2026: «quiero el v21» → pedido en M → «mi talla es L disculpa»).
            "talla_perfil": "",
            # Cuántas veces se hizo de verdad cada pregunta (V1 y la retoma de V2). `preguntado` solo repite las de indagar:
            # la talla preguntada dos veces figuraba una, y la cita la pedía una tercera (07-10).
            "conteo": {}}


def normalizar(m: dict | None) -> dict:
    """La memoria que llega de fuera, con todos sus campos y sin lo que no conocemos."""
    base = nueva()
    if not isinstance(m, dict):
        return base
    for k in ("etapa", "producto", "pendiente", "llego_por", "temperatura_motivo"):
        if isinstance(m.get(k), str):
            base[k] = m[k]
    base["pidio_ver"] = bool(m.get("pidio_ver"))
    if isinstance(m.get("talla_perfil"), str) and m["talla_perfil"].upper() in TALLAS:
        base["talla_perfil"] = m["talla_perfil"].upper()
    if isinstance(m.get("temperatura"), str) and m["temperatura"] in TEMPERATURAS:
        base["temperatura"] = m["temperatura"]
    for k in ("mostrados", "objeciones", "preguntado", "senales"):
        if isinstance(m.get(k), list):
            base[k] = [x for x in m[k] if isinstance(x, str) and x][-30:]
    sab = m.get("sabemos") if isinstance(m.get("sabemos"), dict) else {}
    for k in CAMPOS:
        v = sab.get(k)
        # Solo texto o números, y con la forma que el resto del código da por buena: una cita «basura» reventaba
        # al partirla por la «T» (prueba de regresión, entradas raras).
        v = str(v)[:80] if isinstance(v, (str, int, float)) and not isinstance(v, bool) and v != "" else None
        if v is not None and k in _FORMA and not _FORMA[k].match(v):
            v = None
        base["sabemos"][k] = v
    tent = m.get("cita_tentativa") if isinstance(m.get("cita_tentativa"), dict) else {}
    for k, forma in (("dia", _FORMA["fecha_dia"]), ("hora", _FORMA["hora"])):
        v = tent.get(k)
        base["cita_tentativa"][k] = v if isinstance(v, str) and forma.match(v) else None
    if isinstance(m.get("conteo"), dict):
        base["conteo"] = {k: v for k, v in m["conteo"].items()
                          if k in PENDIENTES and isinstance(v, int) and not isinstance(v, bool) and 0 < v < 100}
    if base["pendiente"] not in PENDIENTES:
        base["pendiente"] = ""
    return base


# La forma que debe tener lo que llega de fuera en la memoria (lo demás se descarta al normalizar).
_FORMA = {"cita": re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$"), "fecha_iso": re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$"),
          "fecha_dia": re.compile(r"^\d{4}-\d{2}-\d{2}$"), "hora": re.compile(r"^\d{2}:\d{2}$"),
          "talla": re.compile(r"^(XXL|XL|XS|S|M|L)$"), "envio": re.compile(r"^(lima|provincia)$"),
          "horario": re.compile(r"^(dia|noche)$"), "presupuesto": re.compile(r"^\d{2,5}$")}


# ---------------------------------------------------------------------------
# Preguntas que el bot puede dejar pendientes

# Texto con el que el código hace cada pregunta (SIGUIENTE PREGUNTA). El LLM la copia tal cual.
PREGUNTAS = {
    "ocasion": "¿Para qué ocasión lo buscas?",
    "horario": "¿El evento es de día o de noche?",
    "talla": "¿Qué talla usas normalmente?",
    "estatura": "¿Cuánto mides? Así te digo cómo te quedaría el largo.",
    "color": "¿Qué color tienes en mente?",
    "fecha": "¿Para cuándo lo necesitas?",
    "que_le_gusto": "¿Qué es lo que más te gustó del modelo?",
    "separar": "¿Te gustaría separarlo?",
    # El cierre del método de venta: dos caminos, separarlo ya o pasar a probárselo (con cita).
    "probar": "¿Te gustaría pasar a probártelo al showroom o prefieres que te lo separe?",
    "cita": "¿Qué día y a qué hora te acomoda venir a probártelo?",
    "confirmar": "¿Confirmamos tu pedido?",
    "lima_o_provincia": "¿El envío sería para Lima o para provincia?",
    "pago": "¿Te paso los datos para el pago?",
    "voucher": "Cuando hagas el pago, ¿me mandas la foto del comprobante?",
    # Solo saludó o escribe de otra cosa y ya se le preguntó dos veces la ocasión: una pregunta abierta, no «¿para
    # cuándo lo necesitas?» (un «lo» sin prenda, a quien no dijo que necesitara nada).
    "que_busca": "¿Qué estás buscando hoy? Cuéntame y te ayudo 😊",
}
# Qué está esperando el bot, en palabras (para el prompt y el panel).
ESPERA = {
    "cual_prenda": "que te diga cuál es la prenda que vio (foto o nombre)",
    "describir_prenda": "que te describa la prenda que vio (color, largo, detalles)",
    "ocasion": "la ocasión", "horario": "si el evento es de día o de noche", "talla": "su talla",
    "estatura": "su estatura", "color": "el color que busca", "fecha": "para cuándo lo necesita",
    "que_le_gusto": "qué le gustó del modelo", "separar": "si quiere separarlo",
    "probar": "si quiere pasar a probárselo al showroom (con cita) o que se lo separes",
    "cita": "el día y la hora de su cita para probárselo",
    "confirmar": "que confirme el pedido", "lima_o_provincia": "si el envío es a Lima o a provincia",
    "pago": "si le pasas los datos de pago", "voucher": "la foto del comprobante de pago",
    "direccion": "su dirección de envío", "otras_opciones": "si quiere ver otras opciones",
    "foto": "la foto del modelo", "que_busca": "que te cuente qué está buscando",
    # La redacta el código V2 con las opciones del RAG (desempate del reranker): puntual, no va en ORDEN.
    "aclarar": "cuál de las opciones le gusta más",
}
PENDIENTES = set(ESPERA)
# Pendientes que se resuelven con un dato de `sabemos`.
DATO_DE = {"ocasion": "ocasion", "horario": "horario", "talla": "talla", "estatura": "estatura", "color": "color",
           "fecha": "fecha", "lima_o_provincia": "envio", "que_le_gusto": "le_gusto", "cita": "cita"}
# Las que el código no suelta solo porque el LLM no volvió a preguntar: hasta que se respondan.
# «cita»: si entre medio pregunta otra cosa («¿hay estacionamiento?»), lo siguiente que diga de día u hora es la cita.
PERSISTENTES = {"cual_prenda", "describir_prenda", "confirmar", "voucher", "direccion", "foto", "cita"}

# La siguiente pregunta de cada etapa, en orden. Lo sabido o ya preguntado se salta.
# Método de venta: primero la necesidad (ocasión → para cuándo, que da la urgencia → día/noche); la talla, solo
# cuando ya se le mostró una prenda; y el cierre ofrece separarlo o pasar a probárselo (`probar`). La temperatura
# cambia el seguimiento: caliente va al cierre antes que a la talla; fría no lo empuja (ver `siguiente`).
INDAGAR = ("ocasion", "fecha", "horario")
# Las preguntas para indagar la necesidad se repiten UNA vez si no se contestaron («hola» → «¿para qué ocasión?» →
# «busco un vestido» → se vuelve a preguntar, con otras palabras). Antes se daban por hechas al primer intento y el
# bot saltaba a la siguiente sin saber la ocasión.
VECES_INDAGAR = 2


REPETIBLES = INDAGAR + ("que_busca",)


def veces(mem: dict, k: str) -> int:
    return mem["preguntado"].count(k)


def veces_hecha(mem: dict, k: str) -> int:
    """Cuántas veces se le hizo de verdad esa pregunta (el conteo; si la ficha es anterior al conteo, lo de `preguntado`)."""
    return max((mem.get("conteo") or {}).get(k, 0), veces(mem, k))


def ya_hecha(mem: dict, k: str) -> bool:
    """Se hizo y no toca repetirla: las de indagar, tras dos intentos; el resto, tras uno."""
    return veces(mem, k) >= (VECES_INDAGAR if k in REPETIBLES else 1)
ORDEN = {
    "prospeccion": ["ocasion", "fecha", "horario", "talla"],
    "seguimiento": ["fecha", "horario", "talla", "probar"],
    "cierre": ["talla", "confirmar"],
    "venta_confirmada": ["lima_o_provincia", "pago", "voucher"],
}


_ABREV = {"pa": "para", "xa": "para", "pal": "para el", "q": "que", "ke": "que", "k": "que", "x": "por", "xq": "porque",
          "tb": "tambien", "tmb": "tambien", "d": "de", "xfa": "por favor", "porfa": "por favor", "ps": "pues", "pe": "pues"}
_RE_ABREV = re.compile(r"(?<![\w/])(?:" + "|".join(_ABREV) + r")(?![\w/])")


def _plano(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").strip()
    # Abreviaturas de chat: «es pa mañana», «q vestidos tienen?», «x la tarde», «d noche» (prueba de regresión).
    s = _RE_ABREV.sub(lambda m: _ABREV[m.group(0)], s)
    # «es este finde» no se leía como fecha y el bot volvía a preguntar «¿para cuándo es?» (prueba con conversaciones)
    return re.sub(r"\bfinde\b", "fin de semana", s)


# Qué pregunta hizo el bot, por su texto. Se mira solo la parte con «?» (las frases que preguntan).
# El orden importa: «¿Confirmamos tu pedido en talla M?» es confirmar, no talla.
DETECTOR = [
    ("cual_prenda", re.compile(r"la foto o el nombre")),
    ("describir_prenda", re.compile(r"cuentame (como era|el color)|como era\b")),
    ("confirmar", re.compile(r"confirm(amos|as|ar)\b|confirmar tu pedido|responde \*?si\*?")),
    # Antes que «horario» («a qué hora») y «separar» («o prefieres que te lo separe»).
    ("cita", re.compile(r"que dia y (a )?que hora|que dia te (acomoda|queda|viene)|a que hora te (acomoda|queda|viene)|"
                        r"te acomoda (a las|a la|desde|el|ese|venir)|(agendar|agendamos|coordinar|coordinamos|separar|separamos) "
                        r"(tu |la |una )?cita")),
    ("probar", re.compile(r"probartel[oa]|probarl[oa]|probarte\b|pasar a probar|venir a probar|ir a probar")),
    ("otras_opciones", re.compile(r"otras opciones")),
    ("lima_o_provincia", re.compile(r"\blima\b.*\bprovincia\b|\bprovincia\b.*\blima\b")),
    ("voucher", re.compile(r"comprobante|voucher|captura del (pago|yape)")),
    ("pago", re.compile(r"datos (para el|de|del) pago|como (prefieres )?pagar|medio de pago")),
    ("direccion", re.compile(r"direccion|ubicacion")),
    ("separar", re.compile(r"\b(separ|reserv|apart)\w*")),
    ("horario", re.compile(r"de dia o de noche|de noche o de dia|\bde dia\b|\bde noche\b|a que hora")),
    ("que_le_gusto", re.compile(r"(lo que )?mas te gust|que te gusto|que te llamo la atencion|que te enamoro")),
    ("aclarar", re.compile(r"cual de estas|entre estas|cual te gusta mas|cual prefieres|con cual te quedas")),
    ("fecha", re.compile(r"para cuando|que fecha|cuando (es|sera|seria) (el|la|tu)|cuando lo necesitas|para que fecha|tienes fecha")),
    ("ocasion", re.compile(r"ocasion|para que (evento|es|lo (buscas|quieres|necesitas))|que evento|que celebr")),
    ("estatura", re.compile(r"cuanto mides|tu (estatura|altura)|que estatura")),
    ("talla", re.compile(r"\btalla\b|\btallas\b")),
    ("color", re.compile(r"que colou?r|algun colou?r|colou?r tienes en mente|colou?r prefieres")),
    ("foto", re.compile(r"mandame la foto|enviame la foto|la foto del modelo")),
    ("que_busca", re.compile(r"que (estas|andas) buscando|en que te (puedo )?ayud|que te gustaria (ver|encontrar)")),
]
RE_PREGUNTA = re.compile(r"[^.!?\n¿]*¿[^?]*\?|[^.!?\n]*\?")


def preguntas_en(texto: str) -> list[str]:
    """Las frases que preguntan, en orden («¿…?» o terminadas en «?»)."""
    return [m.group(0).strip() for m in RE_PREGUNTA.finditer(texto or "")]


def clave_de(pregunta: str) -> str:
    p = _plano(pregunta)
    return next((k for k, rx in DETECTOR if rx.search(p)), "")


def pregunta_de(texto: str) -> str:
    """La clave de la ÚLTIMA pregunta del texto que reconocemos ('' si no pregunta nada conocido)."""
    claves = [clave_de(p) for p in preguntas_en(texto)]
    claves = [k for k in claves if k]
    return claves[-1] if claves else ""


# ---------------------------------------------------------------------------
# Extraer datos del mensaje de la clienta (reglas)

TALLAS = ("XXL", "XL", "XS", "S", "M", "L")
_ALIAS_TALLA = {"chica": "S", "pequena": "S", "small": "S", "mediana": "M", "medium": "M", "grande": "L", "large": "L"}
RE_TALLA_EXPLICITA = re.compile(r"\b(?:talla|soy|uso|usaria|visto|seria)\s+(?:es\s+)?(?:una\s+|la\s+)?(xxl|xl|xs|s|m|l)\b(?!\s*/)")
# «¿lo tienen en M?», «sería en L»: la talla que pide, dicha con «en». «en S/ 330» no es talla S.
RE_TALLA_EN = re.compile(r"\ben\s+(?:talla\s+|la\s+)?(xxl|xl|xs|s|m|l)\b(?!\s*/)")
# La letra dicha con su nombre («la ele», «eme»): solo si se preguntó la talla, porque «ese» es también «ese vestido».
# «ya pues, la S», «dame la M»: la talla con su artículo.
RE_TALLA_LA = re.compile(r"\bla\s+(xxl|xl|xs|s|m|l)\b(?!\s*/)")
RE_TALLA_LETRA = re.compile(r"^(?:la |talla |una |en )?(ele|eme|ese)$")
_LETRA_TALLA = {"ele": "L", "eme": "M", "ese": "S"}
RE_TALLA_SUELTA = re.compile(r"(?<!\d)(?<!\d )\b(xxl|xl|xs|s|m|l)\b(?!\s*/)")   # «1.60 m» no es talla M
RE_TALLA_ALIAS = re.compile(r"\b(?:talla|soy|uso)\s+(chica|pequena|small|mediana|medium|grande|large)\b")

_MESES = r"ene(?:ro)?|feb(?:rero)?|mar(?:zo)?|abr(?:il)?|may(?:o)?|jun(?:io)?|jul(?:io)?|ago(?:sto)?|sep(?:tiembre)?|set(?:iembre)?|oct(?:ubre)?|nov(?:iembre)?|dic(?:iembre)?"
_DIAS = r"lunes|martes|miercoles|jueves|viernes|sabado|domingo"
_NUMS = r"una|un|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|quince|\d+"
RE_FECHA = re.compile(
    rf"\b(\d{{1,2}}\s*(?:de\s+)?(?:{_MESES})\b|\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?"
    # «el sábado 17», «este viernes», «el próximo domingo 18 de octubre»
    rf"|(?:(?:este|el|el proximo|proximo|el otro|este otro)\s+)?(?:{_DIAS})\s+\d{{1,2}}\b(?:\s*de\s+(?:{_MESES})\b)?"
    rf"|(?:este|el|el proximo|el otro|este otro)\s+(?:{_DIAS})"
    rf"|(?:en|dentro de)\s+(?:{_NUMS})\s+(?:semanas?|dias|meses|mes|anos?)"
    r"|(?:la\s+)?(?:proxima|siguiente|otra)\s+semana|(?:la\s+)?semana que viene|(?:el\s+)?(?:proximo|siguiente)\s+mes"
    r"|(?:el\s+)?mes que viene|fin de mes|a fin de (?:mes|ano)|fin de ano"
    r"|(?:el\s+)?(?:proximo|siguiente|otro)\s+ano|(?:el\s+)?ano que viene"
    rf"|(?:en|para)\s+(?:{_MESES})\b)")
RE_FECHA_DIA = re.compile(r"\b(el\s+\d{1,2})\b(?!\s*(?:soles|anos|cm|%|/|:|de la|y media|hrs?\b|h\b|am\b|pm\b))")   # «el 15», si no dijo el mes
# «hoy», «mañana»: solo son la fecha del evento si el bot preguntó para cuándo («¿puedo ir hoy?» no lo es)…
RE_FECHA_CORTA = re.compile(r"\b(pasado manana|(?<!la )manana|hoy|esta semana|este fin de semana)\b")
# …o si lo dice ella: «es para mañana», «lo necesito para este fin de semana».
RE_FECHA_PARA = re.compile(r"\b(?:para|es|sera|seria)\s+(pasado manana|manana|hoy|esta semana|este fin de semana|el fin de semana)\b")
# Prenda que busca (para elegir la opción que se le ofrece). El orden importa: «conjunto de blusa y falda» es conjunto.
PRENDAS = [("conjunto", r"conjunt|\bset\b|dos piezas"), ("enterizo", r"enteriz|jumpsuit|\bmono\b"),
           ("blazer", r"blazer|\bsaco\b"), ("falda", r"\bfalda"), ("jeans", r"\bjean"), ("pantalon", r"pantal|palazzo"),
           ("polo", r"\bpolos?\b|polera"), ("blusa", r"\bblus|\btop\b"), ("vestido", r"\b[vb]estid")]   # «bestido» también
RE_PRENDA = [(k, re.compile(rx)) for k, rx in PRENDAS]
RE_ESTATURA = re.compile(r"\b(1[.,]\s?[4-9]\d?|1\s[4-9]\d)\b(?:\s*m\b|\s*mts?\b|\s*metros?\b)?|\bmido\s+(1[4-9]\d)\b|\b(1[4-9]\d)\s*cm\b")
PROVINCIAS = ("arequipa", "cusco", "cuzco", "trujillo", "piura", "chiclayo", "iquitos", "huancayo", "tacna", "puno", "ica",
              "chimbote", "cajamarca", "ayacucho", "huanuco", "pucallpa", "tarapoto", "juliaca", "moquegua", "tumbes",
              "huaraz", "chincha", "abancay", "huancavelica", "puerto maldonado", "moyobamba", "chachapoyas",
              "cerro de pasco", "pisco", "sullana", "talara", "jaen", "ilo", "paita", "lambayeque", "andahuaylas")
RE_CIUDAD = re.compile(r"\b(" + "|".join(PROVINCIAS) + r"|lima|callao)\b")
# Distritos de Lima: a «¿Lima o provincia?» mucha gente contesta con su distrito («para Surco», «SJL»). Varios son
# también palabras o nombres («Ate», «Magdalena», «Comas», «La Victoria»): solo valen si se preguntó a dónde va el
# envío o si los dice con «soy de», «vivo en», «envían a»…
DISTRITOS_LIMA = ("surco", "santiago de surco", "miraflores", "san isidro", "la molina", "san borja", "barranco", "chorrillos",
                  "sjl", "san juan de lurigancho", "sjm", "san juan de miraflores", "vmt", "villa maria del triunfo", "ves",
                  "villa el salvador", "los olivos", "comas", "smp", "san martin de porres", "independencia", "ate", "vitarte",
                  "santa anita", "el agustino", "la victoria", "brena", "jesus maria", "lince", "magdalena", "pueblo libre",
                  "san miguel", "surquillo", "rimac", "cercado", "carabayllo", "puente piedra", "ventanilla", "lurin",
                  "chaclacayo", "chosica", "cieneguilla", "bellavista", "la perla", "san luis", "pachacamac", "ancon")
_DISTRITOS = "|".join(sorted(DISTRITOS_LIMA, key=len, reverse=True))
RE_DISTRITO = re.compile(r"\b(" + _DISTRITOS + r")\b")
RE_DISTRITO_DICHO = re.compile(r"\b(?:soy de|vivo en|estoy en|envi\w* a|mand\w* a|llega\w* a|delivery a|despach\w* a)\s+(?:el |la |los )?("
                               + _DISTRITOS + r")\b")
OCASIONES = [
    ("matrimonio", r"matrimonio|boda|casamiento|me caso|se casa|matri\b"),
    # «la promo de mi hija», «pa mi promo» es la fiesta de promoción; «¿hay alguna promo?» no (eso es un descuento).
    ("graduacion", r"graduaci|promocion|\bprom\b|(?<=\bmi )promo\b|(?<=\bla )promo\b(?= de (mi|su|la|el|mis)\b)|fiesta de promo"),
    ("quinceanero", r"quincea|\b15 anos\b|quince anos"),
    ("cumpleanos", r"cumplea|\bcumple\b"),
    ("bautizo", r"bautizo|primera comunion|confirmacion"),
    ("compromiso", r"compromiso|pedida de mano"),
    ("aniversario", r"aniversario"),
    ("baby shower", r"baby shower"),
    ("cena", r"\bcena\b"),
    ("gala", r"\bgala\b"),
    ("trabajo", r"trabajo|oficina|entrevista|conferencia"),
    ("fiesta", r"fiesta|recepcion|\bparty\b"),
    # Sin evento: para el día a día. No tiene fecha ni día/noche que preguntar.
    ("diario", r"diario\b|dia a dia|todos los dias"),
]
# Lo general no pisa lo concreto: «la fiesta es de noche» no borra el «matrimonio» que ya dijo.
GENERICAS = {"fiesta", "gala", "cena", "diario"}
# Ocasiones sin evento: no se pregunta para cuándo ni si es de día o de noche.
SIN_EVENTO = {"diario", "trabajo"}
# «ninguna», «nada en especial», «no es para nada» a «¿para qué ocasión?»: no hay evento (es para el día a día).
RE_SIN_OCASION = re.compile(r"\bningun[ao]?\b|\bnada en especial\b|\bno es para (nada|ningun)|\bsin ocasion\b|\bpara mi nomas\b|"
                            r"\bporque si\b|\bsolo porque\b")
# «todavía no tengo fecha», «no sé», «aún no»: contestó que no sabe. No se le vuelve a preguntar lo mismo.
RE_NO_SABE = re.compile(r"\bno (lo )?se\b|\bni idea\b|\bno tengo (fecha|idea|dia|claro)\b|\b(todavia|aun) no\b|\bno hay fecha\b|"
                        r"\bsin fecha\b|\bcualquiera\b|\bda igual\b|\bno importa\b|\bmas adelante\b|\bno (esta|tengo) definid")
RE_OCASION = [(k, re.compile(rf"\b(?:{rx})")) for k, rx in OCASIONES]
RE_NOCHE = re.compile(r"\b(de|en la|por la|a la) noche\b|\bnocturn|\bnoche\b")
RE_DIA = re.compile(r"\b(de|en el|durante el) dia\b|\b(en|por) la (manana|mananita|tarde|tardecita)\b|\bal mediodia\b|\bdiurn|"
                    r"\bde tarde\b|\bde manana\b")
RE_SALUDO_NOCHE = re.compile(r"buenas noches|buenos dias|buen dia|buenas tardes")
COLORES = ("azul", "rojo", "roja", "rosado", "rosada", "palo rosa", "rosa", "negro", "negra", "blanco", "blanca", "beige",
           "nude", "verde", "celeste", "fucsia", "morado", "morada", "lila", "vino", "guinda", "dorado", "dorada",
           "plateado", "plateada", "amarillo", "amarilla", "naranja", "marron", "chocolate", "crema", "turquesa", "esmeralda")
RE_COLOR = re.compile(r"\b(" + "|".join(COLORES) + r")(?:e?s)?\b")   # «vestidos verdes», «azules»
RE_PRESUPUESTO = re.compile(r"(?:presupuesto|hasta|maximo|no mas de|menos de|gastar|tengo|cuento con)\s*(?:de\s*|unos\s*)?(?:s/\.?\s*)?(\d{2,4})(?!\s*(?:anos|dias|cm|m\b))")

# «oh sí», «a ver un momento», «ahora te digo el nombre»: todavía no responde, pero va a hacerlo.
RE_ESPERA = re.compile(r"\b(un momento|un momentito|un segundo|un ratito|a ver|dejame (ver|buscar|revisar)|ahorita te|ahora te|"
                       r"te (digo|paso|mando|envio|aviso)|ya te (digo|paso|mando)|espera|esperame|lo busco|la busco|voy a buscar)\b")
# «no la tengo», «no sé el nombre», «no me acuerdo»: no puede mandar foto ni nombre.
RE_SIN_DATO = re.compile(r"\bno (la |lo )?(tengo|se|recuerdo|me acuerdo|encuentro|guarde)\b|\bno tengo (foto|captura|el nombre)|"
                         r"\bse me borr|\bno se (como se llama|el nombre)")
# Lo que describe una prenda: con esto sí se buscan parecidos.
RE_DESCRIBE = re.compile(r"\b(azul|roj[oa]|rosad[oa]|rosa|palo rosa|negr[oa]|blanc[oa]|beige|nude|verde|celeste|fucsia|morad[oa]|lila|"
                         r"vino|guinda|dorad[oa]|plateado|amarill[oa]|naranja|marron|chocolate|crema|turquesa|"
                         r"larg[oa]|cort[oa]|midi|manga|mangas|tirantes?|strapless|escote|espalda|brill\w*|pedreria|lentejuel\w*|"
                         r"encaje|saten|satinad[oa]|gasa|tul|plisad[oa]|capa|abertura|vuelo|ajustad[oa]|suelto|flores|floread[oa]|"
                         r"cruzad[oa]|asimetric[oa]|drapead[oa])\b")
RE_AFIRMA = re.compile(r"^(s+i+p?|claro|ok\w*|dale|ya|bueno|listo|perfecto|de acuerdo|por supuesto|si+ (por favor|porfa|claro))[\s.!,]*$")
RE_NIEGA = re.compile(r"^(no+|nop|nones|nel|nah|no gracias|mejor no|todavia no|aun no|no todavia|ahorita no|por ahora no|"
                      r"no por ahora|ahora no|tampoco|para nada|no quiero|no deseo|asi nomas|no nada)"
                      r"(,? (gracias|por ahora|todavia|nomas|pues))*[\s.!,]*$")
# Un «sí» dicho de cualquier manera a una pregunta de sí/no: «si ca ver», «ya pues», «claro que sí pásamelos».
RE_AFIRMA_INICIO = re.compile(r"^(s+i+p?|sip|claro|ya|dale|ok\w*|listo|bueno|perfecto|de acuerdo|por supuesto|porfa|por favor|"
                              r"pasa(me)?l[oa]s?|envia(me)?l[oa]s?|manda(me)?l[oa]s?)\b")
# «me llamo Alvaro», «mi nombre es Ana María»: el nombre, no un pedido de hablar con una persona.
RE_NOMBRE = re.compile(r"\b(?:me llamo|mi nombre es|(?:te|le|les|los) saluda|habla)\s+([a-zñ]{2,20}(?:\s+[a-zñ]{2,20})?)")
# «soy alvaro» también es presentarse; «soy talla M», «soy de Tacna», «soy bajita» no. Solo en mensajes cortos.
RE_SOY = re.compile(r"^(?:hola[\s,]+|buenas[\s,]+)?soy\s+([a-zñ]{3,20})(?:\s+([a-zñ]{3,20}))?[\s.!,]*$")
_NO_NOMBRE = {"y", "de", "del", "pero", "busco", "quiero", "para", "por", "con", "una", "un", "la", "el", "queria", "quisiera",
              "necesito", "tengo", "estoy", "vengo", "soy", "desde", "aqui", "otra", "vez", "nuevamente", "que", "como", "cuanto",
              "tienen", "tienes", "hola", "me", "te", "es", "en", "al", "a", "le", "su", "mi"}
_NO_SOY = _NO_NOMBRE | {"talla", "clienta", "cliente", "nueva", "nuevo", "yo", "tu", "quien", "muy", "bien", "mas", "medio", "alta",
                        "baja", "bajita", "altita", "flaca", "flaquita", "delgada", "delgadita", "gordita", "gorda", "rellenita",
                        "llenita", "gruesa", "gruesita", "robusta", "ancha", "anchita", "caderona", "bustona", "chata", "chatita",
                        "chiquita", "pequena", "grande", "mediana", "chica", "small", "medium", "large", "madre", "mama", "hermana",
                        "novia", "madrina", "invitada", "dama", "estudiante", "profesora", "enfermera", "doctora", "mayor",
                        "menor", "alergica", "fan", "asesora", "vendedora", "peruana", "limena", "xs", "xl", "xxl", "ella", "esa",
                        "ese", "esta", "este", "persona", "mujer", "hombre", "varon", "indecisa", "exigente", "friolenta"}


def _nombre(t: str) -> tuple[str, str]:
    """(nombre, trozo del texto que lo dice). «rosa» en «mi nombre es rosa elvira» es su nombre, no un color."""
    m = RE_NOMBRE.search(t)
    palabras = m.group(1).split() if m else []
    if not m and (m := RE_SOY.match(t)) and m.group(1) not in _NO_SOY:
        palabras = [w for w in (m.group(1), m.group(2)) if w]
    out = []
    for w in palabras:
        if w in _NO_NOMBRE:
            break
        out.append(w)
    if not out:
        return "", ""
    return " ".join(w.capitalize() for w in out)[:40], " ".join(out)


def niega(texto: str) -> bool:
    """¿Es un no a una pregunta de sí/no? «nel», «ahorita no gracias», «no por ahora»."""
    return bool(RE_NIEGA.match(_plano(texto)))


def afirma(texto: str) -> bool:
    """¿Es un sí a una pregunta de sí/no? Corto, sin signo de pregunta y empezando por una afirmación."""
    t = _plano(texto)
    if RE_AFIRMA.match(t):
        return True
    return bool(len(t.split()) <= 6 and "?" not in t and not re.match(r"^(no|ya no|todavia no)\b", t) and RE_AFIRMA_INICIO.match(t))


def _talla(t: str, pendiente: str) -> str | None:
    if m := RE_TALLA_EXPLICITA.search(t):
        return m.group(1).upper()
    if m := RE_TALLA_ALIAS.search(t):
        return _ALIAS_TALLA[m.group(1)]
    if m := RE_TALLA_EN.search(t) or RE_TALLA_LA.search(t):
        return m.group(1).upper()
    if pendiente in ("talla", "confirmar") and (m := RE_TALLA_LETRA.match(t.strip(" .!,"))):
        return _LETRA_TALLA[m.group(1)]
    # Una letra suelta solo es talla si el bot la preguntó o el mensaje es corto: «la M» sí; «a ver» no.
    if pendiente in ("talla", "confirmar") or len(t.split()) <= 3:
        if m := RE_TALLA_SUELTA.search(t):
            return m.group(1).upper()
        if t in _ALIAS_TALLA:
            return _ALIAS_TALLA[t]
    return None


def _estatura(t: str) -> str | None:
    m = RE_ESTATURA.search(t)
    if not m:
        return None
    if m.group(1):
        cm = re.sub(r"[\s,]", ".", m.group(1)).replace("..", ".")
        partes = cm.split(".")
        cm = partes[0] + "." + (partes[1] + "0")[:2] if len(partes) > 1 else cm
        return cm
    n = m.group(2) or m.group(3)
    return f"{n[0]}.{n[1:]}"


def extraer(texto: str, pendiente: str = "") -> dict:
    """Los datos que trae el mensaje, por reglas. Solo lo que aparece; nada se supone."""
    t = _plano(texto)
    out: dict = {}
    nombre, trozo = _nombre(t)
    if nombre:
        out["nombre"] = nombre
        t = t.replace(trozo, " ", 1)     # su nombre no es un color («Rosa») ni una ocasión
    if v := _talla(t, pendiente):
        out["talla"] = v
    if m := RE_FECHA.search(t) or RE_FECHA_DIA.search(t):
        out["fecha"] = m.group(1)
    elif pendiente == "fecha" and (m := RE_FECHA_CORTA.search(t)):
        out["fecha"] = m.group(1)
    elif m := RE_FECHA_PARA.search(t):
        out["fecha"] = m.group(1)
    for k, rx in RE_PRENDA:
        if rx.search(t):
            out["prenda"] = k
            break
    if v := _estatura(t):
        out["estatura"] = v
    if m := RE_CIUDAD.search(t):
        ciudad = m.group(1)
        out["ciudad"] = ciudad.title()
        out["envio"] = "lima" if ciudad in ("lima", "callao") else "provincia"
    elif m := (RE_DISTRITO.search(t) if pendiente == "lima_o_provincia" else RE_DISTRITO_DICHO.search(t)):
        out["ciudad"], out["envio"] = (m.group(1).title() if len(m.group(1)) > 3 else m.group(1).upper()), "lima"
    if re.search(r"\bprovincia\b", t):
        out["envio"] = "provincia"
        if out.get("ciudad") in ("Lima", "Callao"):   # «¿a Lima o a provincia?» no dice cuál
            out.pop("ciudad")
            out.pop("envio") if re.search(r"\blima\b.*\bo\b.*\bprovincia|\bprovincia\b.*\bo\b.*\blima", t) else None
    for k, rx in RE_OCASION:
        if rx.search(t):
            out["ocasion"] = k
            break
    if "ocasion" not in out:
        # Espacio mal puesto al teclear: «par aboda», «matri monio». Solo palabras largas e inconfundibles.
        pegado = re.sub(r"\s+", "", t)
        for k, clave in (("matrimonio", "paraboda"), ("matrimonio", "paraunaboda"), ("matrimonio", "matrimonio"),
                         ("graduacion", "graduacion"), ("quinceanero", "quinceanero"), ("cumpleanos", "cumpleanos"),
                         ("bautizo", "bautizo"), ("aniversario", "aniversario")):
            if clave in pegado:
                out["ocasion"] = k
                break
    sin_saludo = RE_SALUDO_NOCHE.sub("", t)
    if RE_NOCHE.search(sin_saludo):
        out["horario"] = "noche"
    elif RE_DIA.search(sin_saludo):
        out["horario"] = "dia"
    elif pendiente == "horario" and re.search(r"\bdia\b|\btarde(cita)?\b|\bmanana\b|\bmananita\b", sin_saludo):
        out["horario"] = "dia"
    if m := RE_COLOR.search(t):
        out["color"] = m.group(1)
    if m := RE_PRESUPUESTO.search(t):
        out["presupuesto"] = m.group(1)
    return out


# ---------------------------------------------------------------------------
# Fechas y horas a ISO (hora de Lima). `hoy` se inyecta para poder probarlo.

DIA_SEMANA = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}
MES_NUM = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9, "set": 9,
           "oct": 10, "nov": 11, "dic": 12}
NUM_PALABRA = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
               "ocho": 8, "nueve": 9, "diez": 10, "quince": 15}


def _num(s: str) -> int:
    return int(s) if s.isdigit() else NUM_PALABRA.get(s, 0)


def _fecha(anio: int, mes: int, dia: int) -> _dt.date | None:
    try:
        return _dt.date(anio, mes, dia)
    except ValueError:
        return None


def _dia_y_mes(dia: int, mes: int, hoy: _dt.date, anio: int | None = None) -> _dt.date | None:
    """Día y mes sin año: si ya pasó este año, es el del año siguiente."""
    if anio:
        return _fecha(anio, mes, dia)
    f = _fecha(hoy.year, mes, dia)
    if f and f < hoy:
        f = _fecha(hoy.year + 1, mes, dia)
    return f


def _solo_dia(dia: int, hoy: _dt.date) -> _dt.date | None:
    """«el 17» sin mes: de este mes; si ya pasó, del siguiente."""
    f = _fecha(hoy.year, hoy.month, dia)
    if f is None or f < hoy:
        mes, anio = (hoy.month % 12) + 1, hoy.year + (hoy.month == 12)
        f = _fecha(anio, mes, dia)
    return f


def fecha_iso(texto: str, hoy: _dt.date) -> str | None:
    """La fecha que dice el texto, en ISO: «AAAA-MM-DD»; «AAAA-MM» si solo dijo el mes; «AAAA» si solo el año.
    None si no dice ninguna. Si el día ya pasó este año, es el del año siguiente."""
    t = _plano(texto)
    if m := re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t):
        anio = int(m.group(3)) if m.group(3) else None
        anio = anio + 2000 if anio is not None and anio < 100 else anio
        f = _dia_y_mes(int(m.group(1)), int(m.group(2)), hoy, anio)
        return f.isoformat() if f else None
    if m := re.search(rf"\b(\d{{1,2}})\s*(?:de\s+)?({_MESES})\b(?:\s+(?:del?\s+)?(\d{{4}}))?", t):
        f = _dia_y_mes(int(m.group(1)), MES_NUM[m.group(2)[:3]], hoy, int(m.group(3)) if m.group(3) else None)
        return f.isoformat() if f else None
    if re.search(r"\bpasado manana\b", t):
        return (hoy + _dt.timedelta(days=2)).isoformat()
    if re.search(r"(?<!la )\bmanana\b", t):
        return (hoy + _dt.timedelta(days=1)).isoformat()
    if re.search(r"\bhoy\b", t):
        return hoy.isoformat()
    if re.search(r"\bfin de semana\b|\besta semana\b", t):
        return (hoy + _dt.timedelta(days=(5 - hoy.weekday()) % 7 if hoy.weekday() <= 5 else 0)).isoformat()
    if m := re.search(rf"\b(?:(este otro|el otro|este|el proximo|proximo|el)\s+)?({_DIAS})(?:\s+(\d{{1,2}}))?\b", t):
        if m.group(3):   # «el sábado 17»: manda el número
            f = _solo_dia(int(m.group(3)), hoy)
            return f.isoformat() if f else None
        delta = (DIA_SEMANA[m.group(2)] - hoy.weekday()) % 7
        if delta == 0 and (m.group(1) or "") != "este":
            delta = 7            # «el viernes» dicho un viernes es el de la semana que viene
        if (m.group(1) or "").endswith("otro"):
            delta += 7
        return (hoy + _dt.timedelta(days=delta)).isoformat()
    if m := re.search(rf"\b(?:en|dentro de)\s+({_NUMS})\s+(semanas?|dias|meses|mes|anos?)\b", t):
        n, u = _num(m.group(1)), m.group(2)
        dias = n * (7 if u.startswith("semana") else 30 if u.startswith("mes") else 365 if u.startswith("ano") else 1)
        return (hoy + _dt.timedelta(days=dias)).isoformat()
    if re.search(r"\b(proxima|siguiente|otra) semana\b|\bsemana que viene\b", t):
        return (hoy + _dt.timedelta(days=7)).isoformat()
    if re.search(r"\b(proximo|siguiente|otro) ano\b|\bano que viene\b", t):
        return str(hoy.year + 1)
    if re.search(r"\bfin de ano\b", t):
        return _dt.date(hoy.year, 12, 31).isoformat()
    if re.search(r"\bfin de mes\b", t):
        sig = _dt.date(hoy.year + (hoy.month == 12), (hoy.month % 12) + 1, 1)
        return (sig - _dt.timedelta(days=1)).isoformat()
    if re.search(r"\b(proximo|siguiente) mes\b|\bmes que viene\b", t):
        return f"{hoy.year + (hoy.month == 12)}-{(hoy.month % 12) + 1:02d}"
    if m := re.search(rf"\b(?:en|para)\s+({_MESES})\b", t):
        mes = MES_NUM[m.group(1)[:3]]
        return f"{hoy.year + (mes < hoy.month)}-{mes:02d}"
    if m := RE_FECHA_DIA.search(t):
        f = _solo_dia(int(m.group(1).split()[-1]), hoy)
        return f.isoformat() if f else None
    return None


def _desde_iso(iso: str) -> _dt.date | None:
    """La fecha más temprana que cubre el ISO («2026-11» → 1 de noviembre): así la urgencia nunca se subestima."""
    try:
        if len(iso) == 10:
            return _dt.date.fromisoformat(iso)
        if len(iso) == 7:
            return _dt.date(int(iso[:4]), int(iso[5:]), 1)
        if len(iso) == 4:
            return _dt.date(int(iso), 1, 1)
    except (ValueError, TypeError):
        return None
    return None


def dias_hasta(iso: str | None, hoy: _dt.date) -> int | None:
    d = _desde_iso(iso or "")
    return (d - hoy).days if d else None


RE_HORA = re.compile(
    r"\b(?:a\s+)?(?:las?|eso de las?|tipo|como a las?)\s+(\d{1,2})(?:\s*[:.h]\s*(\d{2}))?(?:\s+y\s+(media|cuarto|\d{1,2}))?"
    r"(?:\s*(am|a\.?\s?m\.?|pm|p\.?\s?m\.?|de la manana|de la tarde|de la noche|hrs?|horas)\b)?"
    r"|\b(\d{1,2})\s*[:.]\s*(\d{2})\s*(am|pm|hrs?|h)?\b"
    r"|\b(\d{1,2})\s*(am|pm|a\.\s?m\.|p\.\s?m\.)")


def hora_en(texto: str) -> str | None:
    """La hora que dice el texto, «HH:MM» de 24 h. Sin am/pm, de 1 a 8 es de la tarde («a las 5» → 17:00): el
    showroom abre de 9 a 19 h."""
    t = _plano(texto)
    if re.search(r"\bmediodia\b|\bmedio dia\b", t):
        return "12:00"
    m = RE_HORA.search(t)
    if not m:
        return None
    if m.group(1):
        h, mi, extra, suf = int(m.group(1)), int(m.group(2) or 0), m.group(3), m.group(4) or ""
        if extra:
            mi = 30 if extra == "media" else 15 if extra == "cuarto" else int(extra)
    elif m.group(5):
        h, mi, suf = int(m.group(5)), int(m.group(6)), m.group(7) or ""
    else:
        h, mi, suf = int(m.group(8)), 0, m.group(9) or ""
    suf = suf.replace(".", "").replace(" ", "")
    if suf in ("am", "delamanana"):
        h = 0 if h == 12 else h
    elif suf in ("pm", "delatarde", "delanoche"):
        h = h + 12 if h < 12 else h
    elif 1 <= h <= 8:
        h += 12
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return f"{h:02d}:{mi:02d}"


# Showroom (seed/tienda.md): lunes a domingo de 9:00 a 19:00, refrigerio de 13:00 a 14:00, solo con cita.
ABRE, CIERRA, REFRIGERIO = "09:00", "19:00", ("13:00", "14:00")


def validar_cita(dia: str, hora: str, ahora: _dt.datetime, evento: str | None = None) -> str:
    """'' si la cita vale; si no, por qué: dia_pasado, hora_pasada, fuera_horario, refrigerio o despues_evento."""
    hoy = ahora.date()
    d = _desde_iso(dia)
    if d is None or d < hoy:
        return "dia_pasado"
    if not (ABRE <= hora < CIERRA):
        return "fuera_horario"
    if REFRIGERIO[0] <= hora < REFRIGERIO[1]:
        return "refrigerio"
    if d == hoy and hora <= ahora.strftime("%H:%M"):
        return "hora_pasada"
    ev = _desde_iso(evento or "")
    if ev and len(evento or "") == 10 and d > ev:
        return "despues_evento"
    return ""


# Pedir cita para probarse es querer comprar (etapas.py lo trata como intención de compra).
RE_CITA = re.compile(
    r"\b(quiero|quisiera|me gustaria|puedo|podria|voy a|deseo|vamos a|iria|ire)\s+(ir|pasar|pasarme|venir|acercarme|darme una vuelta)\b"
    r"[^.?!]{0,40}\bprob(ar|arme|armel[oa]|arl[oa]|armelos)\b"
    r"|\b(ir|pasar|venir) a probarme(l[oa])?\b"
    # «quiero probarme el vestido Holly», «lo quiero, pero quiero probármelo antes» (chat real: armó un pedido)
    r"|\b(quiero|quisiera|prefiero|me gustaria|necesito)\s+prob(arme|armel[oa]s?|arl[oa]s?)\b"
    r"|\b(agendar|agendame|separar|sacar|reservar|programar|coordinar|hacer|pedir|quiero|quisiera|dame|cierro|cerrar|confirmar|confirmo|fijar|asegurar\w*)\s+(una |la |mi |esa |nuestra )?cita\b"
    # «¿puedo ir mañana a las 4?», «¿podré pasar hoy en la noche?»: visitar con día u hora es pedir cita, aunque no diga «probar»
    # «¿me podrías agendar el domingo a las 7:30?», «¿el sábado a las 11 te parece bien?»: proponer día u hora de visita
    r"|\b(agend\w+|program\w+)\b[^.?!]{0,30}\b(hoy|manana|pasado manana|lunes|martes|miercoles|jueves|viernes|sabado|domingo)\b"
    r"|\b(hoy|manana|pasado manana|lunes|martes|miercoles|jueves|viernes|sabado|domingo)\b[^.?!]{0,30}\b(te parece|te queda|te acomoda|le parece)\b"
    r"|\b(te parece|te queda|te acomoda|le parece|quedamos|nos vemos)\b[^.?!]{0,30}\b(hoy|manana|pasado manana|lunes|martes|miercoles|jueves|viernes|sabado|domingo)\b"
    # «¿lo puedo probar antes?», «¿a qué hora puedo pasar?», «¿voy un día a probármelo?», «¿puedo pasarlo a probar?»: visitar es pedir cita
    r"|\b(puedo|podria|podre)\s+(probar\w*|ver\w*)\b[^.?!]{0,25}\b(antes|en persona|en tienda|ahi|alla)\b"
    r"|\ba que hora (puedo|podria|podre|debo|tengo que)\s+(pasar|ir|llegar|venir)\b"
    r"|\b(voy|ire|iria|pasare|pasaria)\s+(un dia\s+)?a\s+prob\w+"
    r"|\b(puedo|podria|podre)\s+(pasarl[oa]s?|irl[oa]s?)\s+a\s+prob\w+"
    r"|\b(puedo|podria|podre|podrias|quisiera|quiero|voy a|ire|iria|paso|pasaria|pasare|me acerco|me acercaria)\s+(ir|pasar|pasarme|pasarte|venir|visitar|acercarme|llegar)\b"
    r"[^.?!]{0,30}\b(hoy|manana|pasado manana|lunes|martes|miercoles|jueves|viernes|sabado|domingo|a las \d|\d{1,2}\s?(am|pm|a\. ?m|p\. ?m)|\d{1,2}:\d{2})")
# Cuenta una necesidad sin nombrar prenda: «tengo un evento», «busco algo para una boda».
RE_NECESIDAD = re.compile(r"\bevento\b|\b(busco|necesito|quiero)\s+(un|una|algo)\b|\bpara (un|una|mi) "
                          r"(evento|boda|matrimonio|fiesta|graduacion|cena|reunion|quinceanero|compromiso)\b")
# Pide ver prendas: entonces se le muestra una opción aunque falte saber algo de la necesidad.
RE_PIDE_VER = re.compile(r"\b(muestr\w*|ensen\w*|que (modelos|vestidos|opciones) (tienes|tienen|hay)|quiero ver|"
                         r"ver (los |las |unos |unas |algunos |algunas |tus |sus )?(modelos|opciones|vestidos|fotos|algo|catalogo)|catalogo|pas\w* (fotos|opciones|modelos)|"
                         r"tiene[ns]? (fotos|modelos|opciones|algo)|recomiend\w*|sugie\w*|que me (recomiendas|sugieres))\b")
# «no me muestres nada todavía», «no me mandes fotos»: lo contrario de pedir ver.
RE_NO_MOSTRAR = re.compile(r"\bno (me |nos )?(muestr|ensen|mand|pas|envi)\w*")


def en_necesidad(mem: dict, texto: str) -> bool:
    """¿La conversación es de descubrir qué necesita? (contó un evento, o ya sabemos su ocasión o su fecha)."""
    sab = mem["sabemos"]
    return bool(sab.get("ocasion") or sab.get("fecha") or RE_NECESIDAD.search(_plano(texto))
                or mem.get("pendiente") in INDAGAR)


def necesidad_conocida(mem: dict) -> bool:
    """Ya se puede ofrecer: ocasión, fecha y día/noche se saben o ya se preguntaron (no se insiste si no lo sabe)."""
    sab = mem["sabemos"]
    if mem.get("pidio_ver") and sab.get("ocasion"):
        return True   # pidió ver y ya dijo la ocasión: se le muestra sin esperar fecha y día/noche
    if sab.get("ocasion") in SIN_EVENTO:
        return True   # para la oficina o el día a día no hay fecha ni día/noche que esperar
    return all(sab.get(DATO_DE[k]) or ya_hecha(mem, k) for k in INDAGAR) and bool(sab.get("ocasion") or sab.get("fecha"))


def no_mostrar(texto: str) -> bool:
    return bool(RE_NO_MOSTRAR.search(_plano(texto)))


def pide_ver(texto: str) -> bool:
    t = _plano(texto)
    return bool(RE_PIDE_VER.search(t)) and not RE_NO_MOSTRAR.search(t)


# ---------------------------------------------------------------------------
# Temperatura de la clienta: fría, tibia o caliente. Reglas, no LLM.
#
# - Por la fecha del evento (hoy en Lima): ≤ 7 días caliente; 8–30 tibia; > 30 fría.
# - Por señales, en orden (la última fría reinicia las anteriores): «solo estoy viendo», «más adelante», «para el
#   próximo año» → fría; preguntar precio, talla, disponibilidad, color o material, o decir que le interesa → al
#   menos tibia; querer comprarlo, pedir cita para probárselo o «lo necesito urgente» → caliente. Después de que ELLA
#   dijo que solo está viendo, una pregunta de precio no la calienta (sí una compra, una cita o una urgencia).
# - Manda la más alta de las dos (con evento en 5 días, «solo estoy viendo» sigue siendo caliente).
# - Sin datos: fría («sin datos todavía»). Así el bot acompaña sin presionar hasta saber más.

SIN_DATOS = "sin datos todavía: se la trata como fría hasta saber más"
RE_FRIO = re.compile(r"\bsolo (estoy |ando )?(viendo|mirando|averiguando|preguntando|cotizando)|\bnada mas (viendo|mirando)|"
                     r"\bmas adelante|\btodavia no (tengo fecha|se cuando)|\baun no (tengo fecha|se cuando)|"
                     r"\b(para el|el) (proximo|otro|siguiente) ano\b|\bano que viene\b|\bno (tengo|hay) (apuro|prisa)|"
                     r"\bsin (apuro|prisa)|\bno es urgente|\bno me urge")
RE_URGENTE = re.compile(r"\burgen(te|cia)\b|\bme urge\b|\blo antes posible|\bcuanto antes|\blo mas pronto|\bpara ya\b|"
                        r"\blo necesito (ya|hoy|manana|para (hoy|manana|pasado manana|este fin de semana|esta semana))|"
                        r"\bpara este fin de semana")
SENAL_TIBIA = {"consulta_precio", "consulta_talla", "consulta_disponibilidad", "consulta_color", "consulta_material",
               "interesado"}
_RANGO = {"frio": 0, "tibio": 1, "caliente": 2}
_MES_CORTO = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def anotar_senales(mem: dict, texto: str, intent: str = "", nivel: str = "alta", cita: bool = False) -> list[str]:
    """Las señales de temperatura que trae este mensaje (se acumulan en orden en la memoria)."""
    t, nuevas = _plano(texto), []
    if RE_FRIO.search(t):
        nuevas.append("frio")
    elif RE_URGENTE.search(t):
        nuevas.append("urgente")
    if cita:
        nuevas.append("cita")
    elif intent == "intencion_compra" and nivel == "alta":
        nuevas.append("compra")
    elif intent in SENAL_TIBIA and nivel != "baja":
        nuevas.append("interes")
    mem["senales"] = (mem.get("senales", []) + nuevas)[-20:]
    return nuevas


def _corta(d: _dt.date) -> str:
    return f"{d.day}-{_MES_CORTO[d.month - 1]}"


def temperatura(mem: dict, hoy: _dt.date) -> tuple[str, str]:
    """Calcula y guarda la temperatura (`frio`/`tibio`/`caliente`) con su motivo."""
    sab = mem["sabemos"]
    por_senal, mot_senal, dijo_frio = None, "", False
    for s in mem.get("senales", []):
        if s == "frio":
            por_senal, mot_senal, dijo_frio = "frio", "dijo que solo está viendo o que es para más adelante", True
        elif s in ("compra", "cita", "urgente"):
            por_senal, mot_senal, dijo_frio = "caliente", {"compra": "dijo que quiere comprarlo", "cita": "pidió cita para probárselo",
                                                           "urgente": "dijo que lo necesita con urgencia"}[s], False
        elif s == "interes" and not dijo_frio and _RANGO.get(por_senal, -1) < 1:
            # Preguntar el precio no deshace un «solo estoy viendo» dicho por ella: eso lo cambia una señal fuerte.
            por_senal, mot_senal = "tibio", "mostró interés (preguntó por precio, talla, disponibilidad o detalles)"
    if sab.get("cita"):
        por_senal, mot_senal = "caliente", "tiene cita para probárselo"
    por_fecha, mot_fecha = None, ""
    dias = dias_hasta(sab.get("fecha_iso"), hoy)
    if dias is not None and dias >= 0:
        d = _desde_iso(sab["fecha_iso"])
        cuando = (f"evento el {_corta(d)} (en {dias} día{'s' if dias != 1 else ''})" if len(sab["fecha_iso"]) == 10
                  else f"evento en {sab['fecha_iso']} (faltan al menos {dias} días)")
        por_fecha = "caliente" if dias <= 7 else "tibio" if dias <= 30 else "frio"
        mot_fecha = cuando
    if por_fecha is None and por_senal is None:
        nivel, motivo = "frio", SIN_DATOS
    elif por_senal is None or (por_fecha is not None and _RANGO[por_fecha] >= _RANGO[por_senal]):
        nivel, motivo = por_fecha, mot_fecha + (f"; {mot_senal}" if por_senal == por_fecha and mot_senal else "")
    else:
        nivel, motivo = por_senal, mot_senal + (f"; {mot_fecha}" if mot_fecha else "")
    mem["temperatura"], mem["temperatura_motivo"] = nivel, motivo
    return nivel, motivo


# ---------------------------------------------------------------------------
# Leer el mensaje nuevo contra la pendiente

def es_cita(mem: dict, texto: str) -> bool:
    """El mensaje trae (o pide) la cita: lo que diga de día u hora es de la cita, no la fecha del evento."""
    t = _plano(texto)
    pend = mem.get("pendiente", "")
    return bool(pend == "cita" or RE_CITA.search(t) or (pend == "probar" and RE_AFIRMA.match(t)))


def _ultima(texto: str, leer, *args):
    """Lo que `leer` saca de la ÚLTIMA frase que lo trae: «el 25 es la entrevista, no puedo; ¿podría ser el 24 a las
    7:30?» propone el 24, no el 25 (antes se tomaba la primera fecha del mensaje)."""
    for frase in reversed([f for f in re.split(r"[.?!;\n]+|,\s*(?=(?:o|y|pero|mejor)\b)", texto) if f.strip()]):
        if (v := leer(frase, *args)) is not None:
            return v
    return None


def leer_cita(mem: dict, texto: str, ahora: _dt.datetime, sin_dia: bool = False) -> dict:
    """Día y hora de la cita que dice el mensaje, sumados a los que ya había dado. Si los dos valen, la cita queda en
    `sabemos.cita` («AAAA-MM-DDTHH:MM», hora de Lima). Devuelve {dia, hora, dato, ok, error}.
    `sin_dia`: la fecha del mensaje es la del evento («tengo la entrevista el 25, ¿puedo agendar una cita?»), no la de
    la cita."""
    hoy = ahora.date()
    tent = mem["cita_tentativa"]
    dia, hora = (None if sin_dia else _ultima(texto, fecha_iso, hoy)), _ultima(texto, hora_en)
    if dia and len(dia) != 10:
        dia = None     # «en noviembre» no es un día de cita
    # «el sábado 23» cuando el 23 es viernes: no se elige por ella (antes se confirmaba «viernes 23» sin avisar).
    alterno = None
    if dia and (dm := list(re.finditer(rf"\b({_DIAS})\s+(\d{{1,2}})\b", _plano(texto)))):
        nombrado, num = DIA_SEMANA[dm[-1].group(1)], int(dm[-1].group(2))
        d = _desde_iso(dia)
        if d and d.day == num and d.weekday() != nombrado:
            cerca = [d + _dt.timedelta(days=k) for k in (-3, -2, -1, 1, 2, 3)]
            alterno = next((x for x in sorted(cerca, key=lambda x: abs((x - d).days)) if x.weekday() == nombrado and x >= hoy), None)
    if hora:
        tent["hora"] = hora
    if alterno:
        return {"dia": dia, "hora": tent["hora"], "dato": True, "ok": False, "error": "dia_no_coincide",
                "alterno": alterno.isoformat()}
    if dia:
        tent["dia"] = dia
    res = {"dia": tent["dia"], "hora": tent["hora"], "dato": bool(dia or hora), "ok": False, "error": ""}
    if tent["dia"] and tent["hora"]:
        err = validar_cita(tent["dia"], tent["hora"], ahora, mem["sabemos"].get("fecha_iso"))
        if err:
            res["error"] = err
            if err in ("fuera_horario", "refrigerio", "hora_pasada"):
                tent["hora"] = None   # el día sigue en pie: solo falta otra hora
            else:
                tent["dia"] = None
        else:
            mem["sabemos"]["cita"] = f"{tent['dia']}T{tent['hora']}"
            mem["cita_tentativa"] = {"dia": None, "hora": None}
            res["ok"] = True
    return res


def leer(mem: dict, texto: str, jev: dict | None = None, ahora: _dt.datetime | None = None) -> dict:
    """Actualiza la memoria con el mensaje de la clienta y dice qué pasó con la pregunta pendiente.

    Devuelve {"pendiente": la que había, "respondio": bool, "espera": bool, "sin_dato": bool,
              "describe": bool, "datos": {...extraídos}, "fuente": {campo: "reglas"|"jev"}}.
    `jev` son las respuestas de Jev a las preguntas de memoria (ver jev.PREGUNTAS_MEMORIA); proponen lo que
    las reglas no encontraron, nunca pisan lo que sí.
    """
    pend = mem.get("pendiente", "")
    t = _plano(texto)
    ahora = ahora or ahora_lima()
    datos = extraer(texto, pend)
    cita = es_cita(mem, texto)
    # Pide la cita Y cuenta su evento en el mismo mensaje («busco vestido para una entrevista que tengo el 25 de octubre,
    # ¿puedo agendar una cita para probármelo?»): la fecha es la del evento. Antes se tomaba como el día de la cita.
    evento_y_cita = bool(cita and pend != "cita" and "fecha" in datos
                         and (datos.get("ocasion") or RE_NECESIDAD.search(t) or re.search(r"\b(evento|tengo|sera|es)\b", t))
                         and not re.search(r"\b(ir|pasar|venir|acercarme|cita)\b[^.?!]{0,40}\b(" + _DIAS + r"|manana|hoy|el \d)", t))
    if cita and not evento_y_cita:
        datos.pop("fecha", None)     # el día que diga es el de la cita, no el del evento
    elif "fecha" in datos:
        datos["fecha_iso"] = fecha_iso(datos["fecha"], ahora.date())
    fuente = {k: "reglas" for k in datos}
    if jev:
        for k in ("ocasion", "talla", "horario"):
            v = jev.get(k)
            if v and k not in datos:
                datos[k] = v
                fuente[k] = "jev"
    pregunta = "?" in texto
    # «sí, la misma» a «¿en M, como tu pedido anterior, o prefieres otra talla?»: la talla es la de antes.
    if (pend == "talla" and "talla" not in datos and mem.get("talla_perfil") and not pregunta
            and (afirma(texto) or re.search(r"\b(la misma|igual|esa misma|como (la otra|antes|siempre))\b", t))):
        datos["talla"], fuente["talla"] = mem["talla_perfil"], "perfil"
    # «ninguna, es para diario» a «¿para qué ocasión?»: no hay evento.
    if pend == "ocasion" and "ocasion" not in datos and not pregunta and RE_SIN_OCASION.search(t):
        datos["ocasion"], fuente["ocasion"] = "diario", "reglas"
    if pend == "que_le_gusto" and not pregunta and not RE_AFIRMA.match(t) and not RE_NIEGA.match(t) \
            and not RE_ESPERA.search(t) and len(t.split()) >= 2:
        datos.setdefault("le_gusto", texto.strip()[:80])
        fuente.setdefault("le_gusto", "reglas")
    for k, v in list(datos.items()):
        previo = mem["sabemos"].get(k)
        # Ocasión y color: lo nuevo no pisa lo que ya dijo salvo que se le haya preguntado (o sea más concreto).
        if k in ("ocasion", "color") and previo and pend != k and (k == "color" or v in GENERICAS):
            datos.pop(k)
            fuente.pop(k, None)
            continue
        mem["sabemos"][k] = v
    res = {"pendiente": pend, "respondio": False, "espera": False, "sin_dato": False, "describe": False, "no_sabe": False,
           "datos": datos, "fuente": fuente, "es_cita": cita,
           "cita": leer_cita(mem, texto, ahora, sin_dia=evento_y_cita) if cita else None}
    if not pend:
        return res
    if pend == "cita":
        res["respondio"] = res["cita"]["ok"]
    elif pend == "probar":
        res["respondio"] = bool(cita or RE_AFIRMA.match(t) or RE_NIEGA.match(t) or re.search(r"\b(separ|reserv|apart|compr|llev)\w*", t))
    elif pend in DATO_DE:
        res["respondio"] = DATO_DE[pend] in datos
        if not res["respondio"] and pend in INDAGAR and not pregunta and (RE_NO_SABE.search(t) or RE_FRIO.search(t)):
            # Contestó que no sabe («todavía no tengo fecha»): es una respuesta. No se le repite la pregunta.
            res["respondio"] = res["no_sabe"] = True
            while not ya_hecha(mem, pend):
                mem["preguntado"].append(pend)
    elif pend in ("cual_prenda", "describir_prenda"):
        res["describe"] = bool(RE_DESCRIBE.search(t)) or bool(jev and jev.get("responde") and not RE_ESPERA.search(t)
                                                              and len(t.split()) >= 3)
        res["sin_dato"] = bool(RE_SIN_DATO.search(t)) and not res["describe"]
        res["respondio"] = res["describe"]
    elif pend in ("separar", "pago", "otras_opciones", "voucher"):
        res["respondio"] = bool(afirma(texto) or RE_NIEGA.match(t))
    elif pend == "aclarar":
        res["respondio"] = bool(re.search(r"\bv\d{2}\b|primer[oa]?|segund[oa]?|tercer[oa]?|\b[123]\b", t))
    elif pend == "confirmar":
        res["respondio"] = bool(RE_AFIRMA.match(t) or RE_NIEGA.match(t) or re.search(r"\bconfirm", t))
    if not res["respondio"] and RE_ESPERA.search(t) and not pregunta:
        res["espera"] = True
    if res["respondio"]:
        mem["pendiente"] = ""
    return res


def registrar_respuesta(mem: dict, respuesta: str, forzar: str | None = None, prev: dict | None = None, contar: bool = True) -> str:
    """Anota lo que preguntó el bot. `forzar` lo fija el código cuando la pregunta es suya (flujo del pedido,
    «¿cuál es?»). Si el bot no preguntó nada reconocible, la pendiente sin responder sigue (solo las que no se
    sueltan solas); el resto se limpia. Devuelve la pendiente nueva."""
    clave = forzar if forzar is not None else pregunta_de(respuesta)
    if clave:
        # Cada vez que pregunta una de indagar, cuenta (hasta VECES_INDAGAR). Antes solo contaba si seguía pendiente: si
        # entre medio se soltaba, la tercera y la cuarta «¿para cuándo lo necesitas?» no se contaban y se repetía sin fin.
        repite = clave in REPETIBLES
        mem["pendiente"] = clave
        if contar:
            conteo = mem.setdefault("conteo", {})
            conteo[clave] = conteo.get(clave, 0) + 1
        if clave not in mem["preguntado"] or (repite and veces(mem, clave) < VECES_INDAGAR):
            mem["preguntado"].append(clave)
    elif mem.get("pendiente") not in PERSISTENTES:
        mem["pendiente"] = ""
    return mem["pendiente"]


# «tengo un evento», «es para una ocasión especial»: ya dijo que hay algo que celebrar; toca saber qué.
RE_HAY_EVENTO = re.compile(r"\bevento\b|\bcelebraci\w+|\bocasion\b|\breunion\b|\bceremonia\b")
# Dice que busca algo de vestir sin decir qué prenda ni para qué («quisiera ropa formal para mi pareja»).
RE_BUSCA_ROPA = re.compile(r"\bropa\b|\bprendas?\b|\bformal(es)?\b|\belegantes?\b|\bcasual(es)?\b|\boutfit\b|\blook\b|\balgo (bonito|lindo)\b")


def siguiente(mem: dict, etapa: str, hay_prenda: bool | None = None, mensaje: str = "") -> str:
    """La pregunta que toca: la primera de la etapa que no se sepa ni se haya hecho ya. '' si no queda ninguna.

    - La talla (fuera del cierre) solo cuando ya se le mostró una prenda (`hay_prenda`; por defecto, la memoria).
    - `probar` (¿probártelo o te lo separo?) solo con la talla sabida, o si está caliente; nunca con cita ya hecha.
    - Temperatura en seguimiento: caliente pregunta `probar` antes que la talla; fría no lo pregunta (no se empuja)."""
    sab = mem["sabemos"]
    temp = mem.get("temperatura") or ""
    orden = list(ORDEN.get(etapa, []))
    if etapa == "seguimiento" and temp == "caliente":
        orden = ["fecha", "horario", "probar", "talla"]
    elif etapa == "seguimiento" and temp == "frio":
        orden.remove("probar")
    if hay_prenda is None:
        hay_prenda = bool(mem.get("mostrados") or mem.get("producto"))
    if etapa == "prospeccion" and not (sab.get("ocasion") or sab.get("prenda") or sab.get("fecha") or hay_prenda
                                       or mem.get("pidio_ver")):
        # No contó ninguna necesidad (saludó, mandó un emoji, escribe de otra cosa). A un «hola» no se le pregunta «¿es
        # para alguna ocasión especial?» (¿qué cosa?): primero la pregunta abierta, «¿qué estás buscando?», una vez;
        # luego la ocasión, dos veces; luego, ninguna.
        if RE_HAY_EVENTO.search(_plano(mensaje)) and not ya_hecha(mem, "ocasion"):
            return "ocasion"      # «tengo un evento» → «¿Qué evento es?», no otra vez «¿qué estás buscando?»
        if not ya_hecha(mem, "que_busca"):
            return "que_busca"
        if RE_BUSCA_ROPA.search(_plano(mensaje)) and not ya_hecha(mem, "ocasion"):
            return "ocasion"      # ya dijo que busca ropa: se sigue con la ocasión, no se la deja sin pregunta
        # A quien solo saluda («hola», «holaa», «se») no se le pregunta «¿es para alguna ocasión especial?» (¿qué
        # cosa?): era justo la queja de la tienda. Dos veces la pregunta abierta y, si no cuenta nada, ninguna más.
        return ""
    for k in orden:
        if k in DATO_DE and sab.get(DATO_DE[k]):
            continue
        if ya_hecha(mem, k):
            continue
        if k in ("fecha", "horario") and sab.get("ocasion") in SIN_EVENTO:
            continue
        if k == "talla" and etapa != "cierre" and not hay_prenda:
            continue
        if k == "probar" and (sab.get("cita") or not hay_prenda or (temp != "caliente" and not sab.get("talla"))):
            continue
        return k
    return ""


# Con el artículo, para «¿Para cuándo es el matrimonio?».
OCASION_TXT = {"matrimonio": "el matrimonio", "graduacion": "la graduación", "quinceanero": "el quinceañero",
               "cumpleanos": "el cumpleaños", "bautizo": "el bautizo", "compromiso": "el compromiso",
               "aniversario": "el aniversario", "baby shower": "el baby shower", "cena": "la cena", "gala": "la gala",
               "fiesta": "la fiesta"}


def texto_pregunta(k: str, mem: dict, mensaje: str = "") -> str:
    """El texto de la pregunta `k`, ajustado a lo que ella dijo («tengo un evento» → «¿Qué evento es?»). Cada
    variante se reconoce con DETECTOR igual que la de PREGUNTAS (lo comprueba la prueba)."""
    if k == "ocasion" and re.search(r"\bevento\b", _plano(mensaje)):
        return "¿Qué evento es?"
    if k == "que_busca" and veces(mem, "que_busca") >= 1:
        return "Cuéntame, ¿qué estás buscando: un vestido, un conjunto, otra prenda? 😊"
    if k == "ocasion":
        prenda = mem["sabemos"].get("prenda") or ""
        if veces(mem, "ocasion") >= 1:   # segunda vez: con otras palabras y diciendo para qué
            return "Cuéntame, ¿para qué ocasión sería? Así te muestro lo que mejor te va 😊"
        if mem.get("producto") or mem.get("mostrados"):
            return "¿Para qué ocasión lo buscas?"
        if prenda:
            return f"¿Para qué ocasión buscas {'la' if prenda in ('blusa', 'falda') else 'el'} {prenda}?"
        # Era «¿Es para alguna ocasión especial?»: la tienda no la quiere. Esta sirve con una prenda recién mostrada
        # (anuncio, «pásame la foto del Pandora») y cuando dijo que busca algo sin decir qué.
        return "¿Para qué ocasión sería?"
    if k in ("fecha", "horario") and veces(mem, k) >= 1:
        # Segunda vez, con otras palabras: la misma pregunta dos veces seguidas suena a formulario.
        return ("Y cuéntame, ¿ya tienes fecha? Así veo que lo tengas a tiempo 😊" if k == "fecha"
                else "¿Y sería de día o de noche? Así te digo qué te va mejor 😊")
    if k == "fecha" and (oc := OCASION_TXT.get(mem["sabemos"].get("ocasion") or "")):
        return f"¿Para cuándo es {oc}?"
    if k == "fecha" and not (mem["sabemos"].get("prenda") or mem.get("producto") or mem.get("mostrados")):
        return "¿Para cuándo sería?"   # sin prenda ni ocasión, «¿para cuándo lo necesitas?» deja un «lo» huérfano
    if k == "talla" and mem.get("talla_perfil"):
        return f"¿Usas talla *{mem['talla_perfil']}*, como en tu pedido anterior, o prefieres otra talla?"
    return PREGUNTAS.get(k, "")


def quitar_repetidas(texto: str, mem: dict, permitida: str = "", vaciar: bool = False) -> str:
    """Quita del texto del LLM las preguntas que ya se hicieron o cuya respuesta ya sabemos (salvo `permitida`).
    Si un párrafo se queda vacío, se va entero; si todo se iría, se deja como estaba."""
    sab, hechas = mem["sabemos"], set(mem["preguntado"])

    def sobra(q: str) -> bool:
        k = clave_de(q)
        if not k or k == permitida or k in PERSISTENTES or k in ("otras_opciones", "aclarar"):
            return False
        return k in hechas or bool(k in DATO_DE and sab.get(DATO_DE[k]))

    partes = []
    for p in texto.split("\n\n"):
        nuevo = p
        for q in preguntas_en(p):
            if sobra(q) and (i := nuevo.find(q)) >= 0:
                # Lo que sigue a la pregunta en el mismo párrafo suele ser su porqué («¿Para cuándo es? Así te
                # aseguras…»): sin la pregunta queda colgando, así que se va con ella.
                nuevo = nuevo[:i]
        nuevo = re.sub(r"\s{2,}", " ", nuevo).strip()
        if re.search(r"\w", nuevo):
            partes.append(nuevo)
    return "\n\n".join(partes) if partes else ("" if vaciar else texto)


def lo_que_sabemos(mem: dict) -> str:
    sab = {k: v for k, v in mem["sabemos"].items() if v}
    nombres = {"ocasion": "ocasión", "horario": "día/noche", "fecha": "para cuándo", "fecha_iso": "fecha del evento",
               "prenda": "prenda que busca", "talla": "talla", "estatura": "estatura", "color": "color",
               "presupuesto": "presupuesto", "envio": "envío", "ciudad": "ciudad", "le_gusto": "lo que le gustó",
               "cita": "cita para probárselo", "nombre": "se llama"}
    partes = [f"{nombres[k]}: {v}" for k, v in sab.items()]
    if mem.get("talla_perfil") and not sab.get("talla"):
        partes.append(f"talla de su pedido anterior: {mem['talla_perfil']} (hoy no la ha dicho: se le pregunta, no se asume)")
    if mem.get("objeciones"):
        partes.append("dudas que puso: " + ", ".join(mem["objeciones"]))
    if mem.get("llego_por"):
        partes.append(f"llegó por: {mem['llego_por']}")
    return "; ".join(partes) or "nada todavía"


def anotar_turno(mem: dict, etapa: str, producto: str = "", mostrados: list[str] = (), intent: str = "") -> None:
    mem["etapa"] = etapa or mem["etapa"]
    if producto:
        mem["producto"] = producto
    for c in mostrados:
        if c and c not in mem["mostrados"]:
            mem["mostrados"].append(c)
    mem["mostrados"] = mem["mostrados"][-30:]
    obj = {"objecion_precio": "precio", "objecion": "duda"}.get(intent)
    if obj and obj not in mem["objeciones"]:
        mem["objeciones"].append(obj)


def con_perfil(mem: dict, perfil: dict | None) -> dict:
    """Clienta que vuelve: lo que ya sabemos de sus pedidos anteriores prellena la ficha (sin pisar lo de hoy)."""
    if not isinstance(perfil, dict):
        return mem
    crudas = perfil.get("tallas")
    tallas = [t.upper() for t in (crudas if isinstance(crudas, list) else []) if isinstance(t, str) and t.upper() in TALLAS]
    if tallas:
        mem["talla_perfil"] = tallas[0]     # se sugiere al preguntar la talla; no se da por dicha
    return mem


def reconstruir(historial: list, etapa: str = "", llego_por: str = "") -> dict:
    """Memoria para quien no la manda (llamadas viejas): se repasa el historial con las mismas reglas."""
    mem = nueva()
    mem["etapa"] = etapa or "prospeccion"
    mem["llego_por"] = llego_por
    bot = []
    for t in historial or []:
        rol = t.get("rol") if isinstance(t, dict) else getattr(t, "rol", "")
        txt = t.get("texto") if isinstance(t, dict) else getattr(t, "texto", "")
        if rol == "cliente":
            if bot:
                registrar_respuesta(mem, "\n\n".join(bot))
                bot = []
            leer(mem, txt or "")
        else:
            bot.append(txt or "")
    if bot:
        registrar_respuesta(mem, "\n\n".join(bot))
    return mem


def copia(mem: dict) -> dict:
    return copy.deepcopy(mem)
