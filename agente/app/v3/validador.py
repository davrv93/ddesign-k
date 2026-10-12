"""Validador determinista de la respuesta de V3.

El código comprueba lo que el modelo afirmó, en vez de recortar el texto a ciegas (los doce filtros de V1
dejaban frases huérfanas). Si el texto nombra un dato verificable que ninguna herramienta respaldó, se
devuelve la lista de motivos: V3 regenera UNA vez con esos motivos y, si vuelve a fallar, contesta V1.

Reglas (lista cerrada de hechos verificables del plan):
- precio, código, talla y tela: respaldados por una herramienta de este turno;
- descuento/promesa de entrega: nunca, salvo que una herramienta lo autorice (no existe);
- una sola pregunta por mensaje y no repetir una pregunta ya respondida;
- no prometer fotos si no hay ninguna que mandar.

`hechos` es lo que devolvieron las herramientas este turno (ver `herramientas.Recolector`):
    {"codigos": set, "precios": set(float), "tallas": set, "telas": set, "plazos": set,
     "descuento": bool, "fotos": set(códigos con foto)}
"""
from __future__ import annotations

import re

from .. import datos, venta

# Talla que se afirma de verdad: precedida de «talla»/«en»/«la»/«probártelo en». Evita la «M» de «Mira».
RE_TALLA_AFIRMADA = re.compile(r"\b(?:talla|en|la|una)\s+(XS|XXL|XL|S|M|L)\b", re.I)
RE_PRECIO = re.compile(r"(?:S/\.?|soles)\s*(\d+(?:[.,]\d{1,2})?)", re.I)
RE_DESCUENTO = re.compile(r"\b(descuent\w*|rebaj\w*|promo\w*|oferta\w*|dto\.?|%\s*(?:de\s+)?(?:descuento|off))\b", re.I)
RE_PLAZO = re.compile(r"\b(en\s+\d+\s+d[ií]as?|de\s+\d+\s+a\s+\d+\s+d[ií]as?|mañana|llegar[aá]\s+en|demora\w*\s+\d+|en\s+\d+\s+horas?)\b", re.I)
RE_PROMESA_FOTO = re.compile(r"\b(te\s+(paso|mando|env[ií]o|muestro|dejo)|aqu[ií]\s+(tienes|van)|ah[ií]\s+van)\b[^.!?\n]*\bfotos?\b", re.I)

RE_PAGO = re.compile(r"(yape|plin)\s*:|a nombre de|\b9\d{8}\b|n[uú]mero de cuenta|\bcci\b", re.I)
MAX_PREGUNTAS = 1


def hechos_vacios() -> dict:
    return {"codigos": set(), "precios": set(), "tallas": set(), "telas": set(), "plazos": set(),
            "descuento": False, "fotos": set()}


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def _codigo_plano(c: str) -> str:
    return re.sub(r"[\s-]", "", c.upper())


def validar(respuesta: str, hechos: dict, pregunta_hecha: str = "", preguntas_respondidas=()) -> list[str]:
    """Devuelve los motivos por los que la respuesta NO se puede enviar. Lista vacía = válida."""
    r = respuesta or ""
    errores: list[str] = []
    h = hechos or hechos_vacios()

    for m in RE_PRECIO.finditer(r):
        if _num(m.group(1)) not in h.get("precios", set()):
            errores.append(f"precio no respaldado: {m.group(0).strip()}")
    for c in datos.codigos_en(r):
        if _codigo_plano(c) not in {_codigo_plano(x) for x in h.get("codigos", set())}:
            errores.append(f"código no respaldado: {c}")
    for m in RE_TALLA_AFIRMADA.finditer(r):
        if m.group(1).upper() in {t.upper() for t in h.get("tallas", set())}:
            continue
        antes = re.split(r"[.!?\n]", r[:m.start()])[-1].lower()
        if re.search(r"\b(no|sin|ni|nunca)\b", antes):   # «no tenemos XL» declina, no afirma
            continue
        errores.append(f"talla no respaldada: {m.group(1)}")
    if not h.get("descuento"):
        for m in RE_DESCUENTO.finditer(r):
            antes = re.split(r"[.!?\n]", r[:m.start()])[-1].lower()
            if not re.search(r"\b(no|sin|ni|nunca|tampoco)\b", antes):   # «no hacemos descuentos» lo dice, no lo promete
                errores.append("promete un descuento")
                break
    for m in RE_PLAZO.finditer(r):
        if m.group(0).strip().lower() not in {x.lower() for x in h.get("plazos", set())}:
            errores.append(f"plazo no respaldado: {m.group(0).strip()}")
    telas = {x.lower() for x in h.get("telas", set())}
    for rx in venta.RE_TELA:
        for m in rx.finditer(r):
            trozo = m.group(0).strip().lower()
            if not any(t and t in trozo for t in telas):
                errores.append(f"tela no respaldada: {trozo}")
    if not r.strip():
        errores.append("respuesta vacía")
    if RE_PAGO.search(r) and not h.get("pago_ok"):
        errores.append("da datos de pago sin pedido confirmado")
    if r.count("?") > MAX_PREGUNTAS:
        errores.append("más de una pregunta")
    if pregunta_hecha and pregunta_hecha in set(preguntas_respondidas):
        errores.append(f"pregunta ya respondida: {pregunta_hecha}")
    if RE_PROMESA_FOTO.search(r) and not h.get("fotos"):
        errores.append("promete fotos y no hay ninguna")
    return errores
