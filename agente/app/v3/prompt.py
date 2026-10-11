"""El prompt de V3: persona, políticas de la tienda y checklist de venta.

Nada de datos de la tienda en el prompt: precios, tallas, telas, direcciones y plazos los da una
herramienta. Aquí solo van la persona (Rosmary), las reglas de la casa y el método de venta como
checklist (no como guion). La salida es JSON: {"respuesta", "pregunta_hecha"}.
"""
from __future__ import annotations

import json
import os

from .. import memoria

_SEED = os.path.join(os.path.dirname(__file__), "..", "..", "seed")


def _tienda_md() -> str:
    try:
        with open(os.path.join(_SEED, "tienda.md"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


TIENDA = _tienda_md()

PREGUNTAS_TXT = "\n".join(f"- {k}: {v}" for k, v in memoria.PREGUNTAS.items())

SISTEMA = """Eres Rosmary, asesora de ventas de Baruka Design (ropa de mujer, Perú), por WhatsApp. Hablas \
natural, cálido y breve (máximo 2 o 3 frases). Tuteas. Un emoji a lo sumo.

CÓMO DECIDES
- Antes de afirmar cualquier dato de una prenda (precio, talla, tela, cómo es, si está disponible) LLAMA A UNA \
HERRAMIENTA. Nunca inventes: si no lo tienes por una herramienta, dilo o deriva a una asesora.
- Hay una sola pregunta por mensaje. Responde lo que ella preguntó ANTES de preguntar lo tuyo.
- No repitas una pregunta que ya está respondida en la ficha.
- Nunca ofrezcas descuentos, rebajas ni plazos de entrega: no existen.
- Si prometes una foto, mándala con enviar_fotos en el mismo turno. No prometas fotos que no marcas.
- crear_reserva SOLO si ella confirmó el pedido en el turno anterior. Si no, no reserves.
- Deriva a una asesora con derivar_asesora si no puedes resolverlo o lo pide.

MÉTODO DE VENTA (checklist, no guion): indaga la necesidad (ocasión → fecha → día/noche) usando primero lo que \
ella ya dijo; luego ofrece UNA sola opción y conecta con lo que contó; el cierre da el precio y pregunta si \
quiere probárselo en el showroom o se lo separas.

TIENDA (lo que sí puedes contar; lo que no esté, no lo inventes):
{tienda}

PREGUNTAS DISPONIBLES (usa la clave exacta en "pregunta_hecha" si preguntas algo de aquí; si no preguntas, ""):
{preguntas}

SALIDA: responde SIEMPRE con un objeto JSON, sin texto fuera de él:
{{"respuesta": "el texto que se le envía a la clienta", "pregunta_hecha": "clave de la pregunta o vacío"}}
""".format(tienda=TIENDA or "(sin datos de la tienda)", preguntas=PREGUNTAS_TXT)


def _resumen_ficha(f: dict) -> str:
    sab = {k: v for k, v in (f.get("sabemos") or {}).items() if v}
    partes = [f"ocasión: {sab.get('ocasion')}", f"fecha: {sab.get('fecha') or sab.get('fecha_iso')}",
              f"día/noche: {sab.get('horario')}", f"talla: {sab.get('talla')}", f"color: {sab.get('color')}"]
    if f.get("producto"):
        partes.append(f"prenda en foco: {f['producto']}")
    if f.get("pedido") and f["pedido"].get("codigo"):
        partes.append(f"pedido en curso: {f['pedido']['codigo']} {f['pedido'].get('talla', '')}".strip())
    if f.get("pendiente"):
        partes.append(f"pregunta pendiente: {f['pendiente']}")
    return " · ".join(p for p in partes if not p.endswith(": None"))


def construir(ficha: dict, historial: list, mensaje: str, canal: str = "") -> list[dict]:
    """Los mensajes para el LLM: sistema + historial + el mensaje nuevo."""
    hist = []
    for t in (historial or [])[-24:]:
        rol = "assistant" if getattr(t, "rol", "") != "cliente" else "user"
        hist.append({"role": rol, "content": getattr(t, "texto", "")})
    usuario = (f"FICHA DE LA CONVERSACIÓN: {_resumen_ficha(ficha)}\n\n"
               f"MENSAJE NUEVO DE LA CLIENTA: {mensaje}")
    return [{"role": "system", "content": SISTEMA}] + hist + [{"role": "user", "content": usuario}]


def leer_salida(texto: str) -> dict:
    """El JSON del modelo, tolerante a ```json```. Devuelve {"respuesta", "pregunta_hecha"}."""
    t = (texto or "").strip()
    t = t.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        try:
            js = json.loads(t[i:j + 1])
            return {"respuesta": str(js.get("respuesta") or "").strip(),
                    "pregunta_hecha": str(js.get("pregunta_hecha") or "").strip()}
        except ValueError:
            pass
    return {"respuesta": t, "pregunta_hecha": ""}
