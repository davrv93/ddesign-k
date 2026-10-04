"""Máquina de estados comercial: prospección → seguimiento → cierre → venta confirmada.

El clasificador dice qué quiere la clienta (intención y confianza). Este módulo decide, con reglas
explícitas, en qué etapa queda la conversación. El LLM no decide etapas: solo redacta.

Primer contacto: el primer mensaje de una conversación siempre queda en prospección («hola, ¿todavía
tienen este vestido?» es empezar a conocerla, no seguimiento). Solo una intención clara de compra lo saca.

Regla central: mostrar interés NO es comprar. «Sí, me interesa» deja la conversación en seguimiento;
solo una intención clara de compra («quiero comprarlo», «resérvamelo») la lleva a cierre, y solo una
confirmación explícita a una pregunta de confirmación la convierte en venta.

Sin dependencias: se prueba con `python3 -m app.prueba_etapas`.
"""
from __future__ import annotations

import re
import unicodedata

ETAPAS = ("prospeccion", "seguimiento", "cierre", "venta_confirmada")
ORDEN = {e: i for i, e in enumerate(ETAPAS)}

# Preguntar por esto es interés: saca la conversación de prospección, pero no la cierra.
INTERES = {"consulta_precio", "consulta_talla", "consulta_color", "consulta_disponibilidad", "consulta_ubicacion",
           "consulta_horario", "consulta_delivery", "consulta_pago", "consulta_material", "interesado", "comparacion",
           "objecion", "objecion_precio"}

UMBRAL_ALTO = 0.80    # se usa la clasificación tal cual
UMBRAL_MEDIO = 0.60   # entre medio y alto: solo transiciones prudentes; por debajo, ninguna


def _plano(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").strip()


# Respuestas cortas que solo se entienden por lo que el bot acaba de preguntar.
RE_AFIRMA = re.compile(r"^(s+i+p?|sii+|claro( que si)?|ok(ey|is)?|dale|ya|bueno|listo|correcto|asi es|por supuesto|de acuerdo|perfecto|si+ claro|si+ por favor|si+ porfa|esta bien)[\s.!,🙌👍]*$")
RE_NIEGA = re.compile(r"^(no+|nop|nel|no gracias|mejor no|todavia no|aun no)[\s.!,]*$")
# Señales fuertes de compra. Con una de estas no hace falta que el clasificador esté seguro.
RE_COMPRA = re.compile(
    r"\b(quiero (comprar|llevar|reservar|separar|pedir|adquirir|ordenar|apartar)\w*"
    r"|quiero (hacer|confirmar) (el|mi|la) (pedido|compra)|quiero (ese|este|el vestido)\b"
    r"|(me )?lo (llevo|compro)\b|ya lo quiero|me quedo con (ese|este|el)\b"
    r"|como (hago para |puedo )?(comprar|pagar|lo compro|te pago|hago (el|mi) pedido)\w*"
    r"|donde (deposito|te deposito|pago|te yapeo)|a (que numero|donde) te (yapeo|deposito)"
    r"|(reserva|separa|aparta|guarda|envia)melo|pasame (el|tu) yape"
    r"|quisiera (apartar|separar|reservar|comprar)\w*|deseo comprar\w*)")
# El botón de talla de las tarjetas de la web: elegir talla es querer esa prenda.
RE_BOTON_TALLA = re.compile(r"^talla\s+\w+\s+del\s+[a-z]{1,3}-?\d+")
# ¿El último mensaje del bot pedía confirmar la compra?
RE_PIDE_CONFIRMAR = re.compile(r"¿\s*confirm(amos|as)\b|confirmar tu pedido|para confirmar tu pedido|confirmamos la (talla|compra)")


def pide_confirmar(ultimo_bot: str) -> bool:
    return bool(RE_PIDE_CONFIRMAR.search(_plano(ultimo_bot)))


def decidir(etapa: str, intent: str, confianza: float, mensaje: str, ultimo_bot: str = "", primer_mensaje: bool = False) -> dict:
    """Devuelve la etapa nueva y la intención final, con el motivo de cada decisión (para el registro)."""
    etapa = etapa if etapa in ORDEN else "prospeccion"
    texto, motivos = _plano(mensaje), []
    corto = len(texto) <= 24
    confirma = pide_confirmar(ultimo_bot)

    # 1. Reglas de contexto: corrigen la intención antes de mirar umbrales.
    if RE_BOTON_TALLA.search(texto):
        intent, confianza = "intencion_compra", 1.0
        motivos.append("eligió talla en la tarjeta")
    elif corto and RE_AFIRMA.match(texto):
        if confirma:
            intent, confianza = "confirmacion_compra", 0.97
            motivos.append("«sí» a una pregunta de confirmación")
        else:
            intent, confianza = "interesado", 0.95
            motivos.append("«sí» sin pregunta de confirmación: interés, no compra")
    elif corto and RE_NIEGA.match(texto):
        if confirma:
            intent, confianza = "cancelacion", 0.95
            motivos.append("«no» a una pregunta de confirmación")
        else:
            intent, confianza = "otro", 0.5
            motivos.append("«no» sin contexto de compra: no cambia la etapa")
    elif RE_COMPRA.search(texto):
        intent, confianza = "intencion_compra", max(confianza, 0.9)
        motivos.append("señal fuerte de compra")

    # 2. Confirmar solo vale si ya se estaba cerrando y el bot lo preguntó.
    if intent == "confirmacion_compra" and not (etapa == "cierre" and confirma):
        intent = "interesado"
        motivos.append("confirmación fuera de cierre: se trata como interés")

    nivel = "alta" if confianza >= UMBRAL_ALTO else "media" if confianza >= UMBRAL_MEDIO else "baja"
    nueva, cancelada = etapa, False

    # 3. Transiciones.
    if nivel == "baja":
        motivos.append(f"confianza baja ({confianza:.2f}): la etapa no cambia")
    elif etapa == "venta_confirmada":
        if intent == "cancelacion" and nivel == "alta":
            nueva, cancelada = "seguimiento", True
            motivos.append("canceló después de confirmar")
    elif intent == "intencion_compra":
        if nivel == "alta":
            nueva = "cierre"
        elif etapa == "prospeccion":
            nueva = "seguimiento"
            motivos.append("intención de compra con confianza media: avanza solo a seguimiento")
    elif intent == "confirmacion_compra":
        nueva = "venta_confirmada"
    elif intent == "cancelacion":
        if etapa == "cierre":
            nueva, cancelada = "seguimiento", True
            motivos.append("canceló en el cierre: vuelve a seguimiento")
    elif intent in ("objecion", "objecion_precio"):
        nueva = "prospeccion" if (primer_mensaje and etapa == "prospeccion") else "seguimiento"
        if etapa == "cierre":
            motivos.append("objeción en el cierre: vuelve a seguimiento")
    elif intent in INTERES and etapa == "prospeccion":
        if primer_mensaje:
            motivos.append("primer mensaje: se queda en prospección")
        else:
            nueva = "seguimiento"

    return {"etapa_anterior": etapa, "etapa": nueva, "intent": intent, "confianza": round(float(confianza), 3),
            "nivel": nivel, "cancelada": cancelada, "transicion": nueva != etapa, "motivo": "; ".join(motivos)}
