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

Sin dependencias: se prueba con `python3 -m app.prueba_memoria`.
"""
from __future__ import annotations

import copy
import re
import unicodedata

CAMPOS = ("ocasion", "horario", "fecha", "talla", "estatura", "color", "presupuesto", "envio", "ciudad", "le_gusto")


def nueva() -> dict:
    return {"etapa": "prospeccion", "producto": "", "mostrados": [], "pendiente": "",
            "sabemos": {k: None for k in CAMPOS}, "objeciones": [], "llego_por": "", "preguntado": []}


def normalizar(m: dict | None) -> dict:
    """La memoria que llega de fuera, con todos sus campos y sin lo que no conocemos."""
    base = nueva()
    if not isinstance(m, dict):
        return base
    for k in ("etapa", "producto", "pendiente", "llego_por"):
        if isinstance(m.get(k), str):
            base[k] = m[k]
    for k in ("mostrados", "objeciones", "preguntado"):
        if isinstance(m.get(k), list):
            base[k] = [str(x) for x in m[k] if x][-30:]
    sab = m.get("sabemos") if isinstance(m.get("sabemos"), dict) else {}
    for k in CAMPOS:
        v = sab.get(k)
        base["sabemos"][k] = str(v)[:80] if v not in (None, "") else None
    if base["pendiente"] not in PENDIENTES:
        base["pendiente"] = ""
    return base


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
    "confirmar": "¿Confirmamos tu pedido?",
    "lima_o_provincia": "¿El envío sería para Lima o para provincia?",
    "pago": "¿Te paso los datos para el pago?",
    "voucher": "Cuando hagas el pago, ¿me mandas la foto del comprobante?",
}
# Qué está esperando el bot, en palabras (para el prompt y el panel).
ESPERA = {
    "cual_prenda": "que te diga cuál es la prenda que vio (foto o nombre)",
    "describir_prenda": "que te describa la prenda que vio (color, largo, detalles)",
    "ocasion": "la ocasión", "horario": "si el evento es de día o de noche", "talla": "su talla",
    "estatura": "su estatura", "color": "el color que busca", "fecha": "para cuándo lo necesita",
    "que_le_gusto": "qué le gustó del modelo", "separar": "si quiere separarlo",
    "confirmar": "que confirme el pedido", "lima_o_provincia": "si el envío es a Lima o a provincia",
    "pago": "si le pasas los datos de pago", "voucher": "la foto del comprobante de pago",
    "direccion": "su dirección de envío", "otras_opciones": "si quiere ver otras opciones",
    "foto": "la foto del modelo",
}
PENDIENTES = set(ESPERA)
# Pendientes que se resuelven con un dato de `sabemos`.
DATO_DE = {"ocasion": "ocasion", "horario": "horario", "talla": "talla", "estatura": "estatura", "color": "color",
           "fecha": "fecha", "lima_o_provincia": "envio", "que_le_gusto": "le_gusto"}
# Las que el código no suelta solo porque el LLM no volvió a preguntar: hasta que se respondan.
PERSISTENTES = {"cual_prenda", "describir_prenda", "confirmar", "voucher", "direccion", "foto"}

# La siguiente pregunta de cada etapa, en orden. Lo sabido o ya preguntado se salta.
ORDEN = {
    "prospeccion": ["ocasion", "horario", "talla", "estatura", "color"],
    "seguimiento": ["fecha", "que_le_gusto", "separar"],
    "cierre": ["talla", "confirmar"],
    "venta_confirmada": ["lima_o_provincia", "pago", "voucher"],
}


def _plano(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").strip()


# Qué pregunta hizo el bot, por su texto. Se mira solo la parte con «?» (las frases que preguntan).
# El orden importa: «¿Confirmamos tu pedido en talla M?» es confirmar, no talla.
DETECTOR = [
    ("cual_prenda", re.compile(r"la foto o el nombre")),
    ("describir_prenda", re.compile(r"cuentame (como era|el color)|como era\b")),
    ("confirmar", re.compile(r"confirm(amos|as|ar)\b|confirmar tu pedido|responde \*?si\*?")),
    ("otras_opciones", re.compile(r"otras opciones")),
    ("lima_o_provincia", re.compile(r"\blima\b.*\bprovincia\b|\bprovincia\b.*\blima\b")),
    ("voucher", re.compile(r"comprobante|voucher|captura del (pago|yape)")),
    ("pago", re.compile(r"datos (para el|de|del) pago|como (prefieres )?pagar|medio de pago")),
    ("direccion", re.compile(r"direccion|ubicacion")),
    ("separar", re.compile(r"\b(separ|reserv|apart)\w*")),
    ("horario", re.compile(r"de dia o de noche|de noche o de dia|\bde dia\b|\bde noche\b|a que hora")),
    ("que_le_gusto", re.compile(r"(lo que )?mas te gust|que te gusto|que te llamo la atencion|que te enamoro")),
    ("fecha", re.compile(r"para cuando|que fecha|cuando (es|sera|seria) (el|la|tu)|cuando lo necesitas|para que fecha")),
    ("ocasion", re.compile(r"ocasion|para que (evento|es|lo (buscas|quieres|necesitas))|que evento|que celebr")),
    ("estatura", re.compile(r"cuanto mides|tu (estatura|altura)|que estatura")),
    ("talla", re.compile(r"\btalla\b|\btallas\b")),
    ("color", re.compile(r"que colou?r|algun colou?r|colou?r tienes en mente|colou?r prefieres")),
    ("foto", re.compile(r"mandame la foto|enviame la foto|la foto del modelo")),
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
RE_TALLA_EXPLICITA = re.compile(r"\b(?:talla|soy|uso|usaria|visto|seria)\s+(?:una\s+|la\s+)?(xxl|xl|xs|s|m|l)\b(?!\s*/)")
RE_TALLA_SUELTA = re.compile(r"(?<!\d)(?<!\d )\b(xxl|xl|xs|s|m|l)\b(?!\s*/)")   # «1.60 m» no es talla M
RE_TALLA_ALIAS = re.compile(r"\b(?:talla|soy|uso)\s+(chica|pequena|small|mediana|medium|grande|large)\b")

_MESES = r"ene(?:ro)?|feb(?:rero)?|mar(?:zo)?|abr(?:il)?|may(?:o)?|jun(?:io)?|jul(?:io)?|ago(?:sto)?|sep(?:tiembre)?|set(?:iembre)?|oct(?:ubre)?|nov(?:iembre)?|dic(?:iembre)?"
RE_FECHA = re.compile(
    rf"\b(\d{{1,2}}\s*(?:de\s+)?(?:{_MESES})\b|\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?"
    r"|(?:este|el|el proximo|el otro|este otro)\s+(?:lunes|martes|miercoles|jueves|viernes|sabado|domingo)"
    r"|(?:en|dentro de)\s+(?:una|dos|tres|cuatro|\d+)\s+(?:semanas?|dias|meses|mes)"
    r"|(?:la\s+)?(?:proxima|siguiente)\s+semana|(?:el\s+)?(?:proximo|siguiente)\s+mes|fin de mes|a fin de (?:mes|ano)"
    rf"|(?:en|para)\s+(?:{_MESES})\b)")
RE_FECHA_DIA = re.compile(r"\b(el\s+\d{1,2})\b(?!\s*(?:soles|anos|cm|%))")   # «el 15», si no dijo el mes
# «hoy», «mañana»: solo son la fecha del evento si el bot preguntó para cuándo («¿puedo ir hoy?» no lo es).
RE_FECHA_CORTA = re.compile(r"\b(pasado manana|manana|hoy|esta semana|este fin de semana)\b")
RE_ESTATURA = re.compile(r"\b(1[.,]\s?[4-9]\d?|1\s[4-9]\d)\b(?:\s*m\b|\s*mts?\b|\s*metros?\b)?|\bmido\s+(1[4-9]\d)\b|\b(1[4-9]\d)\s*cm\b")
PROVINCIAS = ("arequipa", "cusco", "cuzco", "trujillo", "piura", "chiclayo", "iquitos", "huancayo", "tacna", "puno", "ica",
              "chimbote", "cajamarca", "ayacucho", "huanuco", "pucallpa", "tarapoto", "juliaca", "moquegua", "tumbes",
              "huaraz", "chincha", "abancay", "huancavelica", "puerto maldonado", "moyobamba", "chachapoyas",
              "cerro de pasco", "pisco", "sullana", "talara", "jaen", "ilo", "paita", "lambayeque", "andahuaylas")
RE_CIUDAD = re.compile(r"\b(" + "|".join(PROVINCIAS) + r"|lima|callao)\b")
OCASIONES = [
    ("matrimonio", r"matrimonio|boda|casamiento|me caso|se casa"),
    ("graduacion", r"graduaci|promocion|\bprom\b"),
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
]
# Lo general no pisa lo concreto: «la fiesta es de noche» no borra el «matrimonio» que ya dijo.
GENERICAS = {"fiesta", "gala", "cena"}
RE_OCASION = [(k, re.compile(rf"\b(?:{rx})")) for k, rx in OCASIONES]
RE_NOCHE = re.compile(r"\b(de|en la|por la|a la) noche\b|\bnocturn|\bnoche\b")
RE_DIA = re.compile(r"\b(de|en el|durante el) dia\b|\b(en|por) la (manana|tarde)\b|\bal mediodia\b|\bdiurn|\bde tarde\b|\bde manana\b")
RE_SALUDO_NOCHE = re.compile(r"buenas noches|buenos dias|buen dia|buenas tardes")
COLORES = ("azul", "rojo", "roja", "rosado", "rosada", "palo rosa", "rosa", "negro", "negra", "blanco", "blanca", "beige",
           "nude", "verde", "celeste", "fucsia", "morado", "morada", "lila", "vino", "guinda", "dorado", "dorada",
           "plateado", "plateada", "amarillo", "amarilla", "naranja", "marron", "chocolate", "crema", "turquesa", "esmeralda")
RE_COLOR = re.compile(r"\b(" + "|".join(COLORES) + r")\b")
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
RE_NIEGA = re.compile(r"^(no+|nop|no gracias|mejor no|todavia no|aun no)[\s.!,]*$")


def _talla(t: str, pendiente: str) -> str | None:
    if m := RE_TALLA_EXPLICITA.search(t):
        return m.group(1).upper()
    if m := RE_TALLA_ALIAS.search(t):
        return _ALIAS_TALLA[m.group(1)]
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
    if v := _talla(t, pendiente):
        out["talla"] = v
    if m := RE_FECHA.search(t) or RE_FECHA_DIA.search(t):
        out["fecha"] = m.group(1)
    elif pendiente == "fecha" and (m := RE_FECHA_CORTA.search(t)):
        out["fecha"] = m.group(1)
    if v := _estatura(t):
        out["estatura"] = v
    if m := RE_CIUDAD.search(t):
        ciudad = m.group(1)
        out["ciudad"] = ciudad.title()
        out["envio"] = "lima" if ciudad in ("lima", "callao") else "provincia"
    if re.search(r"\bprovincia\b", t):
        out["envio"] = "provincia"
        if out.get("ciudad") in ("Lima", "Callao"):   # «¿a Lima o a provincia?» no dice cuál
            out.pop("ciudad")
            out.pop("envio") if re.search(r"\blima\b.*\bo\b.*\bprovincia|\bprovincia\b.*\bo\b.*\blima", t) else None
    for k, rx in RE_OCASION:
        if rx.search(t):
            out["ocasion"] = k
            break
    sin_saludo = RE_SALUDO_NOCHE.sub("", t)
    if RE_NOCHE.search(sin_saludo):
        out["horario"] = "noche"
    elif RE_DIA.search(sin_saludo):
        out["horario"] = "dia"
    elif pendiente == "horario" and re.search(r"\bdia\b|\btarde\b|\bmanana\b", sin_saludo):
        out["horario"] = "dia"
    if m := RE_COLOR.search(t):
        out["color"] = m.group(1)
    if m := RE_PRESUPUESTO.search(t):
        out["presupuesto"] = m.group(1)
    return out


# ---------------------------------------------------------------------------
# Leer el mensaje nuevo contra la pendiente

def leer(mem: dict, texto: str, jev: dict | None = None) -> dict:
    """Actualiza la memoria con el mensaje de la clienta y dice qué pasó con la pregunta pendiente.

    Devuelve {"pendiente": la que había, "respondio": bool, "espera": bool, "sin_dato": bool,
              "describe": bool, "datos": {...extraídos}, "fuente": {campo: "reglas"|"jev"}}.
    `jev` son las respuestas de Jev a las preguntas de memoria (ver jev.PREGUNTAS_MEMORIA); proponen lo que
    las reglas no encontraron, nunca pisan lo que sí.
    """
    pend = mem.get("pendiente", "")
    t = _plano(texto)
    datos = extraer(texto, pend)
    fuente = {k: "reglas" for k in datos}
    if jev:
        for k in ("ocasion", "talla", "horario"):
            v = jev.get(k)
            if v and k not in datos:
                datos[k] = v
                fuente[k] = "jev"
    pregunta = "?" in texto
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
    res = {"pendiente": pend, "respondio": False, "espera": False, "sin_dato": False, "describe": False,
           "datos": datos, "fuente": fuente}
    if not pend:
        return res
    if pend in DATO_DE:
        res["respondio"] = DATO_DE[pend] in datos
    elif pend in ("cual_prenda", "describir_prenda"):
        res["describe"] = bool(RE_DESCRIBE.search(t)) or bool(jev and jev.get("responde") and not RE_ESPERA.search(t)
                                                              and len(t.split()) >= 3)
        res["sin_dato"] = bool(RE_SIN_DATO.search(t)) and not res["describe"]
        res["respondio"] = res["describe"]
    elif pend in ("separar", "pago", "otras_opciones"):
        res["respondio"] = bool(RE_AFIRMA.match(t) or RE_NIEGA.match(t))
    elif pend == "confirmar":
        res["respondio"] = bool(RE_AFIRMA.match(t) or RE_NIEGA.match(t) or re.search(r"\bconfirm", t))
    if not res["respondio"] and RE_ESPERA.search(t) and not pregunta:
        res["espera"] = True
    if res["respondio"]:
        mem["pendiente"] = ""
    return res


def registrar_respuesta(mem: dict, respuesta: str, forzar: str | None = None, prev: dict | None = None) -> str:
    """Anota lo que preguntó el bot. `forzar` lo fija el código cuando la pregunta es suya (flujo del pedido,
    «¿cuál es?»). Si el bot no preguntó nada reconocible, la pendiente sin responder sigue (solo las que no se
    sueltan solas); el resto se limpia. Devuelve la pendiente nueva."""
    clave = forzar if forzar is not None else pregunta_de(respuesta)
    if clave:
        mem["pendiente"] = clave
        if clave not in mem["preguntado"]:
            mem["preguntado"].append(clave)
    elif mem.get("pendiente") not in PERSISTENTES:
        mem["pendiente"] = ""
    return mem["pendiente"]


def siguiente(mem: dict, etapa: str) -> str:
    """La pregunta que toca: la primera de la etapa que no se sepa ni se haya hecho ya. '' si no queda ninguna."""
    sab, hechas = mem["sabemos"], set(mem["preguntado"])
    for k in ORDEN.get(etapa, []):
        if k in DATO_DE and sab.get(DATO_DE[k]):
            continue
        if k in hechas:
            continue
        return k
    return ""


def quitar_repetidas(texto: str, mem: dict, permitida: str = "") -> str:
    """Quita del texto del LLM las preguntas que ya se hicieron o cuya respuesta ya sabemos (salvo `permitida`).
    Si un párrafo se queda vacío, se va entero; si todo se iría, se deja como estaba."""
    sab, hechas = mem["sabemos"], set(mem["preguntado"])

    def sobra(q: str) -> bool:
        k = clave_de(q)
        if not k or k == permitida or k in PERSISTENTES or k in ("otras_opciones",):
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
    return "\n\n".join(partes) if partes else texto


def lo_que_sabemos(mem: dict) -> str:
    sab = {k: v for k, v in mem["sabemos"].items() if v}
    nombres = {"ocasion": "ocasión", "horario": "día/noche", "fecha": "para cuándo", "talla": "talla", "estatura": "estatura",
               "color": "color", "presupuesto": "presupuesto", "envio": "envío", "ciudad": "ciudad", "le_gusto": "lo que le gustó"}
    partes = [f"{nombres[k]}: {v}" for k, v in sab.items()]
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
    tallas = [str(t).upper() for t in (perfil.get("tallas") or []) if str(t).upper() in TALLAS]
    if tallas and not mem["sabemos"].get("talla"):
        mem["sabemos"]["talla"] = tallas[0]
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
