"""Respuesta estructurada: el LLM entrega piezas, el código arma el mensaje.

Medido con 50 conversaciones reservadas (04-10-2026): lo que quedaba mal no era la clasificación sino la
redacción. El LLM perdía el hilo (21 de 50), sonaba robótico por largo y elogios repetidos (20), no
contestaba lo que le preguntaron (10) y repetía preguntas (6). Con texto libre no hay forma de obligarlo.

Ahora devuelve JSON con tres campos y el código arma el mensaje en este orden:
1. `responde`: lo que contesta a SU mensaje (máx. 2 frases). Va primero: no se puede saltar.
2. `por_que`: por qué le conviene, conectado con lo que ella contó (máx. 1 frase, opcional).
3. La pregunta: la que eligió el código (`memoria.siguiente`). La del LLM (`pregunta`) solo se usa cuando el
   código no tiene ninguna (p. ej. «¿es alguno de estos?» mientras describe una prenda).

Las preguntas que el LLM meta en `responde` o `por_que` se quitan: una sola pregunta por mensaje, y la decide
el código. El largo se corta por frases. Si el JSON no se puede leer, se usa el texto tal cual (como antes).

El saludo del primer mensaje («¡Hola, Ana! Soy Rosemary, tu asesora de Baruka Design.») NO cuenta para las 2 frases de
`responde` y va en su propio párrafo. Antes se comía las dos y la frase que nombraba la prenda se cortaba: en
producción (05-10-2026) antes de la foto del Irla solo quedó «Es ideal para una boda nocturna…», sin decir cuál.

`presentar` garantiza por código la regla del dueño «si el texto habla de una prenda, la nombra»: con la foto de una
prenda que aún no se nombró, el texto que va ANTES de la foto la nombra (si el LLM no lo hizo, se antepone una frase).

Sin dependencias: se prueba con `python3 -m app.prueba_estructurado`.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata

ACTIVO = os.environ.get("RESPUESTA_ESTRUCTURADA", "1") == "1"
MAX_RESPONDE = (2, 280)   # frases, caracteres
MAX_POR_QUE = (1, 200)
MAX_PREGUNTA = 160
MAX_SALUDO = 2            # «¡Hola, Ana!» + «Soy Rosemary, tu asesora…»: no cuentan para MAX_RESPONDE

RE_FRASE = re.compile(r"[^.!?¡¿]*(?:¿[^?]*\?|¡[^!]*!|[^.!?]+[.!?]+|[^.!?]+$)", re.S)
RE_JSON = re.compile(r"\{.*\}", re.S)
RE_SALUDO = re.compile(r"(hola|holi|buenas|buen[oa]s?\s+(d[ií]as?|tardes|noches)|bienvenid[ao]s?|qu[eé] gusto|"
                       r"mucho gusto|un gusto|encantad[ao])\b", re.I)
RE_PRESENTA = re.compile(r"[Ss]oy (?:tu |la )?[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b")    # «Soy Rosemary, tu asesora…»
# Frases que hablan de la prenda sin decir cuál: antes de la foto no se entienden («¿qué es ideal?»).
RE_SIN_ANTECEDENTE = re.compile(
    r"(es|est[aá]|este|esta|ese|esa|esto|lo|la|le|su|sus|tiene|viene|cae|queda|combina|resalta|favorece|estiliza|"
    r"luce|ser[ií]a|ser[aá]|ideal|perfect[oa]|hermos[oa]|precios[oa]|divin[oa]|lind[oa]|elegante|te (va|van|quedar[ií]?a|"
    r"queda|luce|har[aá]|encantar[aá]|favorece|estiliza))\b", re.I)


def _sin_lead(frase: str) -> str:
    """La frase sin signos ni emojis delante: «😊 Soy Rosemary» → «Soy Rosemary»."""
    return re.sub(r"^[\W_]+", "", frase)


def es_saludo(frase: str) -> bool:
    s = _sin_lead(frase)
    return bool(RE_SALUDO.match(s) or RE_PRESENTA.match(s))


def sin_antecedente(frase: str) -> bool:
    """«Es ideal para…», «Este modelo…», «Te va a quedar…»: habla de una prenda sin nombrarla."""
    return bool(RE_SIN_ANTECEDENTE.match(_sin_lead(frase)))


def _de_entrada(frase: str) -> bool:
    """Exclamación de entrada que no habla de la prenda: «¡Claro!», «¡Perfecto!», «¡Qué bonito! 😊». («¡Es perfecto
    para tu boda!» o «¡Te va a quedar hermoso!» sí hablan de ella.)"""
    s = frase.strip()
    return (re.sub(r"^[^\w¡]+", "", s).startswith("¡") and bool(re.search(r"![^\w!]*$", s))
            and (len(re.findall(r"\w+", s)) <= 2 or not sin_antecedente(s)))


def _separar_saludo(texto: str) -> tuple[str, str]:
    """(saludo, resto): las frases de saludo y presentación del principio, aparte."""
    frases = _frases(texto)
    k = 0
    while k < min(len(frases), MAX_SALUDO) and "?" not in frases[k] and es_saludo(frases[k]):
        k += 1
    return " ".join(frases[:k]), " ".join(frases[k:])


def formato(pregunta_codigo: str, permitir_pregunta: bool) -> str:
    """La instrucción de salida que se añade al final del prompt (manda sobre «escribe solo el texto»)."""
    if pregunta_codigo:
        cierre = (f'El código cierra tu mensaje con esta pregunta: «{pregunta_codigo}». NO la escribas tú ni hagas '
                  'otra: deja "pregunta" vacío.')
    elif permitir_pregunta:
        cierre = ('Si hace falta UNA pregunta (p. ej. «¿es alguno de estos?»), ponla en "pregunta"; si no, déjalo vacío. '
                  'Nunca repitas algo que ya contestó.')
    else:
        cierre = 'No hagas preguntas: deja "pregunta" vacío.'
    return ("FORMATO DE SALIDA (manda sobre cualquier otra indicación de formato): responde SOLO con un objeto JSON:\n"
            '{"responde": "...", "por_que": "...", "pregunta": "..."}\n'
            '- "responde": contesta lo que ella acaba de escribir, directo y con datos reales (PRODUCTO, TIENDA, AHORA). '
            "Máximo 2 frases cortas, sin preguntas (el saludo, si toca, no cuenta). Si no sabes el dato, di que lo "
            "confirma una asesora (*4*). Si hablas de una prenda, NÓMBRALA (p. ej. «te recomiendo el Vestido Irla»): "
            "el texto le llega ANTES que la foto, y «Es ideal…», «Este modelo…» o «Te va a quedar…» sin decir cuál no "
            "se entienden.\n"
            '- "por_que": opcional, 1 frase: por qué le conviene, usando lo que te contó. Sin elogios genéricos '
            "(«es súper elegante», «te va a quedar espectacular») si ya los dijiste antes. Vacío si no aporta.\n"
            f"- {cierre}\n"
            "Tono de vendedora peruana por WhatsApp: cálido y breve, como mucho un emoji en todo el mensaje.")


def _frases(texto: str) -> list[str]:
    # El punto de un decimal o de una hora no termina la frase: «S/ 20.00» salía como «S/ 20. 00».
    t = re.sub(r"(?<=\d)\.(?=\d)", "\u2024", texto or "")
    # «a. m.» y «p. m.» tampoco: partían «a las 11:30 a. m.» en «…a.» y «m.», y el filtro perdía una mitad (WhatsApp/web 06-10).
    t = re.sub(r"\b([ap])\.(\s?)m\.", "\\1\u2024\\2m\u2024", t, flags=re.I)
    return [f.strip().replace("\u2024", ".") for f in RE_FRASE.findall(t) if f.strip()]


def _recortar(texto: str, max_frases: int, max_chars: int, sin_preguntas: bool = True) -> str:
    out = []
    for f in _frases(texto):
        if sin_preguntas and "?" in f:
            continue
        if out and len(" ".join(out + [f])) > max_chars:
            break
        out.append(f)
        if len(out) >= max_frases:
            break
    return " ".join(out).strip()


def leer(texto: str) -> dict | None:
    """El JSON del LLM, tolerante a ```json``` y a texto alrededor. None si no hay objeto legible."""
    t = (texto or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    m = RE_JSON.search(t)
    if not m:
        return None
    try:
        js = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(js, dict):
        return None
    return {k: str(js.get(k) or "").strip() for k in ("responde", "por_que", "pregunta")}


def armar(texto: str, pregunta_codigo: str = "", permitir_pregunta: bool = False) -> str | None:
    """El mensaje final, un párrafo por pieza (cada párrafo sale como un mensaje de WhatsApp). None si el LLM no
    devolvió JSON utilizable: entonces se usa su texto como antes."""
    js = leer(texto)
    if js is None:
        return None
    # El saludo va aparte y no gasta las 2 frases: si no, «¡Hola, Ana! Soy Rosemary…» dejaba fuera lo que contesta.
    saludo, resto = _separar_saludo(js["responde"])
    responde = _recortar(resto, *MAX_RESPONDE)
    por_que = _recortar(js["por_que"], *MAX_POR_QUE)
    if por_que and _parecido(por_que, responde):
        por_que = ""
    pregunta = pregunta_codigo
    if not pregunta and permitir_pregunta:
        q = next((f for f in _frases(js["pregunta"]) if "?" in f), "")
        pregunta = q if len(q) <= MAX_PREGUNTA else ""
    partes = [p for p in (saludo, responde, por_que, pregunta) if p]
    if not (saludo or responde or por_que):
        return None
    return "\n\n".join(partes)


def _plano(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower()) if unicodedata.category(c) != "Mn")


def nombra(texto: str, claves) -> bool:
    """¿El texto nombra la prenda? `claves`: su código y las palabras propias de su nombre («v35», «irla»), sin
    distinguir mayúsculas ni tildes y como palabra entera."""
    p = _plano(texto)
    return any(re.search(rf"(?<![a-z0-9ñ]){re.escape(_plano(c))}(?![a-z0-9ñ])", p) for c in claves if c and c.strip())


def presentar(texto: str, frase: str, claves) -> str:
    """La regla del dueño: si el texto habla de una prenda, la nombra. Va la foto de una prenda que aún no se nombró
    en la conversación; la clienta lee el texto ANTES de la foto (y la pregunta final, después: `sendAgentText` en el
    bot Go y `mostrar()` en ui.html). Si ningún párrafo de antes de la foto la nombra, se pone `frase` («Para tu
    matrimonio de noche te recomiendo el *Vestido Irla*.») después del saludo y de las exclamaciones de entrada
    («¡Claro!»), y antes del porqué: así «Es ideal…» o «Te va a quedar…» tienen de qué hablar. Si el LLM ya la nombró,
    no se toca."""
    partes = [p.strip() for p in (texto or "").split("\n\n") if p.strip()]
    if not partes or not frase:
        return texto
    if len(partes) > 1 and "?" in partes[-1]:
        antes, cierre = partes[:-1], partes[-1:]          # la pregunta final va después de la foto
    elif len(partes) == 1 and all("?" in f for f in _frases(partes[0])):
        antes, cierre = [], partes                        # solo la pregunta: la frase va antes y la separa de la foto
    else:
        antes, cierre = partes, []
    if any(nombra(p, claves) for p in antes):
        return texto
    for i, p in enumerate(antes):
        frases = _frases(p)
        k = 0
        # Lo que va delante: el saludo, la presentación y las exclamaciones que no hablan de la prenda
        # («¡Claro!», «¡Qué bonito!»). «¡Es perfecto para ti!» sí habla de ella: la frase va antes.
        while k < len(frases) and "?" not in frases[k] and (es_saludo(frases[k]) or _de_entrada(frases[k])):
            k += 1
        if k == len(frases):
            continue                                      # el párrafo es solo saludo: la frase va en el siguiente
        # Después de los emojis que cierran la entrada («¡Claro! 😊 | Te paso la foto»).
        pos = p.find(frases[k]) + re.match(r"[^\w¡¿]*", frases[k]).end() if k else 0
        antes[i] = (p[:pos].rstrip() + " " + frase + " " + p[pos:].lstrip()).strip() if k else frase + " " + p
        return "\n\n".join(antes + cierre)
    return "\n\n".join(antes + [frase] + cierre)


def sin_json(texto: str) -> str:
    """Si el LLM mandó JSON roto, que no llegue a la clienta con llaves y comillas."""
    js = leer(texto)
    if js:
        return "\n\n".join(p for p in (js["responde"], js["por_que"], js["pregunta"]) if p) or texto
    if texto.strip().startswith("{"):
        return re.sub(r'[{}"]|\b(responde|por_que|pregunta)\b\s*:', "", texto).strip()
    return texto


def _parecido(a: str, b: str) -> bool:
    pa, pb = set(re.findall(r"\w{4,}", a.lower())), set(re.findall(r"\w{4,}", b.lower()))
    return bool(pa) and len(pa & pb) / len(pa) > 0.7
