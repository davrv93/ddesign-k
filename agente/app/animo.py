"""Ánimo y urgencia de un mensaje: cómo se siente la clienta y cuánta prisa tiene.

Reglas, no modelo. Alimentan la Capa de Juicio (backend/internal/juicio) para decidir si
responder, derivar a una persona o callar. No cambian el texto de la respuesta.

- sentimiento: -1 (muy negativo) .. +1 (muy positivo); 0 es neutro.
- urgencia:    0..1; sube con «urgente», «hoy», «ya», plazos cortos y con la fecha del evento.

Sin dependencias: se prueba con `python3 -m app.prueba_animo`.
"""
from __future__ import annotations

import re
import unicodedata

NEGATIVO = re.compile(
    r"\b(estafa|p[eé]sim\w*|horrible|terrible|asqueros\w*|fe[oa]s?\b|no me gusta|reclam\w*|queja|"
    r"indignad\w*|molest\w*|enojad\w*|furios\w*|hart\w*|decepci\w*|desepci\w*|nunca m[aá]s|"
    r"mal\w* servicio|ladrones?|robo|car[ií]sim\w*|pesad[oa]|fatal|inaceptable)\b")
POSITIVO = re.compile(
    r"\b(gracias|muchas gracias|me encanta|me gusta|me gust[oó]|hermos\w*|lind\w*|bell\w*|precios\w*|"
    r"perfecto|excelente|genial|divin\w*|espectacular|feliz|encantad\w*|amo)\b")
URGENTE = re.compile(
    r"\b(urgent\w*|cuanto antes|lo antes posible|de inmediato|ahora mismo|ya mismo|ap[uú]rad\w*|"
    r"r[aá]pid\w*|urge|para hoy|es hoy|hoy mismo|esta noche|manana|pasado manana|ya lo necesito)\b")
EMOJI_NEG = re.compile(
    "[\U0001F620\U0001F621\U0001F624\U0001F92C\U0001F61E\U0001F622\U0001F62D\U0001F629\U0001F62B\U0001F644\U0001F612]")
EMOJI_POS = re.compile(
    "[\U0001F60D\U0001F970\U0001F60A\U0001F929\U0001F618\u2764\U0001F495\U0001F44D]")


def _plano(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def evaluar(texto: str, mem: dict | None = None) -> dict:
    """Devuelve {"sentimiento", "urgencia", "motivo"} para la Capa de Juicio."""
    t = _plano(texto)
    neg = len(NEGATIVO.findall(t)) + len(EMOJI_NEG.findall(texto or ""))
    pos = len(POSITIVO.findall(t)) + len(EMOJI_POS.findall(texto or ""))
    if neg > pos:
        sentimiento = -min(1.0, 0.6 + 0.2 * (neg - 1))
    elif pos > neg:
        sentimiento = min(1.0, 0.4 + 0.2 * (pos - 1))
    else:
        sentimiento = 0.0

    urgencia, marcas = 0.0, []
    n_urg = len(URGENTE.findall(t))
    if n_urg:
        urgencia = min(0.9, 0.5 + 0.2 * (n_urg - 1))
        marcas.append("plazo corto")
    fecha = (mem or {}).get("sabemos", {}).get("fecha") if isinstance(mem, dict) else None
    if fecha:
        urgencia = min(1.0, urgencia + 0.2)
        marcas.append("evento con fecha")

    motivo = "; ".join(marcas) if marcas else "sin señales"
    return {"sentimiento": round(sentimiento, 3), "urgencia": round(urgencia, 3), "motivo": motivo}
