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

Sin dependencias: se prueba con `python3 -m app.prueba_estructurado`.
"""
from __future__ import annotations

import json
import os
import re

ACTIVO = os.environ.get("RESPUESTA_ESTRUCTURADA", "1") == "1"
MAX_RESPONDE = (2, 280)   # frases, caracteres
MAX_POR_QUE = (1, 200)
MAX_PREGUNTA = 160

RE_FRASE = re.compile(r"[^.!?¡¿]*(?:¿[^?]*\?|¡[^!]*!|[^.!?]+[.!?]+|[^.!?]+$)", re.S)
RE_JSON = re.compile(r"\{.*\}", re.S)


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
            "Máximo 2 frases cortas, sin preguntas. Si no sabes el dato, di que lo confirma una asesora (*4*).\n"
            '- "por_que": opcional, 1 frase: por qué le conviene, usando lo que te contó. Sin elogios genéricos '
            "(«es súper elegante», «te va a quedar espectacular») si ya los dijiste antes. Vacío si no aporta.\n"
            f"- {cierre}\n"
            "Tono de vendedora peruana por WhatsApp: cálido y breve, como mucho un emoji en todo el mensaje.")


def _frases(texto: str) -> list[str]:
    # El punto de un decimal o de una hora no termina la frase: «S/ 20.00» salía como «S/ 20. 00».
    t = re.sub(r"(?<=\d)\.(?=\d)", "\u2024", texto or "")
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
    responde = _recortar(js["responde"], *MAX_RESPONDE)
    por_que = _recortar(js["por_que"], *MAX_POR_QUE)
    if por_que and _parecido(por_que, responde):
        por_que = ""
    pregunta = pregunta_codigo
    if not pregunta and permitir_pregunta:
        q = next((f for f in _frases(js["pregunta"]) if "?" in f), "")
        pregunta = q if len(q) <= MAX_PREGUNTA else ""
    partes = [p for p in (responde, por_que, pregunta) if p]
    if not (responde or por_que):
        return None
    return "\n\n".join(partes)


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
