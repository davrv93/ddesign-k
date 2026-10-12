"""El prompt de V3: persona, políticas de la tienda y checklist de venta.

Nada de datos de la tienda en el prompt: precios, tallas, telas, direcciones y plazos los da una
herramienta. Aquí solo van la persona (Rosmary), las reglas de la casa y el método de venta como
checklist (no como guion). La salida es JSON: {"respuesta", "pregunta_hecha"}.
"""
from __future__ import annotations

import json
import re
import os

from .. import memoria

_SEED = os.path.join(os.path.dirname(__file__), "..", "..", "seed")


def _seed(nombre: str) -> str:
    try:
        with open(os.path.join(_SEED, nombre), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


TIENDA = _seed("tienda.md")
# Guion de venta de la tienda (spech.docx → seed/speech.md). Manda sobre el «método» genérico.
SPEECH = _seed("speech.md")
# Yape y titular: seed/pago.md vive FUERA de git (el repo es público). Solo entra al prompt con el pedido confirmado.
PAGO = _seed("pago.md")

PREGUNTAS_TXT = "\n".join(f"- {k}: {v}" for k, v in memoria.PREGUNTAS.items())

SISTEMA = """Eres Rosmary, asesora de ventas de Baruka Design (ropa de mujer, Perú), por WhatsApp. Hablas \
natural, cálido y breve (máximo 2 o 3 frases). Tuteas. Uno o dos emojis, como en el guion.

CÓMO DECIDES
- Antes de afirmar cualquier dato de una prenda (precio, talla, tela, cómo es, si está disponible) LLAMA A UNA \
HERRAMIENTA. Nunca inventes: si no lo tienes por una herramienta, dilo o deriva a una asesora.
- Una sola pregunta por mensaje, salvo el paso 3 del guion (talla, día/noche y estatura van juntas). Responde lo \
que ella preguntó ANTES de preguntar lo tuyo.
- No repitas una pregunta que ya está respondida en la ficha.
- Nunca ofrezcas descuentos, rebajas ni plazos de entrega: no existen.
- Si prometes una foto, mándala con enviar_fotos en el mismo turno. No prometas fotos que no marcas.
- crear_reserva SOLO si ella confirmó el pedido en el turno anterior. Si no, no reserves.
- Deriva a una asesora con derivar_asesora SOLO si ella la pide o hay un reclamo. Dirección, horarios, envíos, cambios \
y política de la tienda están en TIENDA: respóndelos tú. Si falta un dato (p. ej. medidas), dilo y sigue; no derives por eso.
- Si ella pide ver prendas, fotos o «cómo es», muéstralas con enviar_fotos en ese mismo turno (1 o 2, máximo 3); \
no le pidas permiso para mandarlas ni la mandes al catálogo web. No reenvíes fotos de «prendas ya mostradas».
- Si pide fotos sin decir cuál, usa la prenda en foco o la que cuadra con lo que contó, no el catálogo entero.
- Si busca un color o prenda y la herramienta dice «sin_color», dile que no lo tienes; no lo disimules.
- Una restricción dicha (talla, mangas, color, presupuesto) manda sobre todo lo demás. Si no hay su talla, dile las que hay.
- Si te da su nombre, úsalo en la respuesta. Si pregunta si eres un bot, di que eres la asistente virtual de Baruka.
- Nunca repitas tu mensaje anterior: si ella insiste o repite, reformula o pregunta otra cosa.
- Datos de pago (Yape, cuenta) SOLO con el pedido ya confirmado; antes, di que primero confirman prenda y talla.
- Al dar el envío, di el total (precio + envío) con una cifra que te dio una herramienta.
- Si ya dijo talla, color o una restricción (mangas, presupuesto), revísala con buscar_catalogo EN ESE MISMO turno \
y dile qué hay (o que no hay) antes de preguntar la ocasión. No lo dejes para después.
- Si no hay su talla exacta, di cuáles sí hay (no te quedes callada ni derives por eso).
- Si responde «ok», «bien» o «gracias» sin dato nuevo, no repitas la pregunta de antes con las mismas palabras: \
sigue con el siguiente paso del guion. Nunca dejes la respuesta vacía.
- «¿Me lo separas?» sin saber de qué prenda: pregúntale cuál antes de hacer nada.
- Si pregunta «qué modelos tienes», nombra 1-2 prendas reales de buscar_catalogo, no una frase vacía.
- Datos de pago (Yape, cuenta, «te paso los datos para el pago») SOLO si el pedido ya está confirmado \
(prenda + talla + un sí explícito). Si solo preguntó el costo de envío u otra cosa, no lo ofrezcas todavía.
- «¿Lo tienes en otros colores?»: en el catálogo cada color es una prenda aparte. Busca su nombre con
buscar_catalogo; si solo hay uno, di con naturalidad «por ahora solo lo tengo en negro» (con su color real) (no «el catálogo no indica»).
- No elijas por ella: si dudas entre dos prendas, pregúntale cuál.

<<GUION>>

TIENDA (lo que sí puedes contar; lo que no esté, no lo inventes):
{tienda}

PREGUNTAS DISPONIBLES (usa la clave exacta en "pregunta_hecha" si preguntas algo de aquí; si no preguntas, ""):
{preguntas}

SALIDA: responde SIEMPRE con un objeto JSON, sin texto fuera de él:
{{"respuesta": "el texto que se le envía a la clienta", "pregunta_hecha": "clave de la pregunta o vacío"}}
""".format(tienda=TIENDA or "(sin datos de la tienda)", preguntas=PREGUNTAS_TXT).replace(
    "<<GUION>>", SPEECH or "MÉTODO DE VENTA: indaga la necesidad (ocasión, fecha, día/noche); ofrece UNA opción; cierra con precio.")


def _resumen_ficha(f: dict) -> str:
    sab = {k: v for k, v in (f.get("sabemos") or {}).items() if v}
    partes = [f"ocasión: {sab.get('ocasion')}", f"fecha: {sab.get('fecha') or sab.get('fecha_iso')}",
              f"día/noche: {sab.get('horario')}", f"talla: {sab.get('talla')}", f"color: {sab.get('color')}"]
    if sab.get("nombre"):
        partes.insert(0, f"nombre: {sab['nombre']}")
    if f.get("mostrados"):
        partes.append("prendas ya mostradas (con foto): " + ", ".join(f["mostrados"]))
    if f.get("producto"):
        partes.append(f"prenda en foco: {f['producto']}")
    if f.get("pedido") and f["pedido"].get("codigo"):
        partes.append(f"pedido en curso: {f['pedido']['codigo']} {f['pedido'].get('talla', '')}".strip())
    if f.get("pendiente"):
        partes.append(f"pregunta pendiente: {f['pendiente']}")
    return " · ".join(p for p in partes if not p.endswith(": None"))


def construir(ficha: dict, historial: list, mensaje: str, canal: str = "", hecho: str = "",
              cliente: str = "", pago: bool = False) -> list[dict]:
    """Los mensajes para el LLM: sistema + historial + el mensaje nuevo. `pago`: el pedido ya está confirmado y
    se le pasan los datos de pago (seed/pago.md) para que los dé tal cual."""
    hist = []
    for t in (historial or [])[-24:]:
        rol = "assistant" if getattr(t, "rol", "") != "cliente" else "user"
        hist.append({"role": rol, "content": getattr(t, "texto", "")})
    nombre = (cliente or "").strip().split(" ")[0] if (cliente or "").strip() else ""
    usuario = (f"FICHA DE LA CONVERSACIÓN: {_resumen_ficha(ficha)}\n\n"
               + (f"NOMBRE DE LA CLIENTA (de WhatsApp): {nombre}\n\n" if nombre else "")
               + f"MENSAJE NUEVO DE LA CLIENTA: {mensaje}")
    msgs = [{"role": "system", "content": SISTEMA}] + hist + [{"role": "user", "content": usuario}]
    if pago and PAGO:
        msgs.append({"role": "system", "content": "EL PEDIDO YA ESTÁ CONFIRMADO. DATOS DE PAGO (dalos tal cual cuando toque "
                     "el paso 9 del guion o ella los pida):\n" + PAGO})
    if hecho:  # system, al final: pesa más que si fuera parte del mensaje de la clienta (11-10: si no, pierde contra el saludo)
        msgs.append({"role": "system", "content": "ANTES DE RESPONDER, ESTO YA SE VERIFICÓ Y VA EN TU RESPUESTA:\n" + hecho})
    return msgs


RE_SALIDA = re.compile(r'"respuesta"\s*:\s*"(?P<r>.*?)"\s*,\s*"pregunta_hecha"\s*:\s*"(?P<p>[^"]*)"', re.S)
# Restos de JSON que nunca deben llegar a la clienta (12-10-2026, WhatsApp real: «…¿No lo consideras? 💛","pregunta_hecha":"fecha"}»).
RE_RESTO_JSON = re.compile(r'^\s*\{?\s*"respuesta"\s*:\s*"|"\s*,\s*"pregunta_hecha"\s*:\s*"[^"]*"\s*\}?\s*$|^\s*\{\s*$|^\s*\}\s*$', re.M)


def leer_salida(texto: str) -> dict:
    """El JSON del modelo, tolerante a ```json``` y a saltos de línea literales dentro del texto (json estricto los
    rechaza y se enviaba el JSON crudo). Devuelve {"respuesta", "pregunta_hecha"}; nunca deja restos de JSON."""
    t = (texto or "").strip()
    t = t.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        try:
            js = json.loads(t[i:j + 1], strict=False)
            if isinstance(js, dict) and "respuesta" in js:
                return {"respuesta": str(js.get("respuesta") or "").strip(),
                        "pregunta_hecha": str(js.get("pregunta_hecha") or "").strip()}
        except ValueError:
            pass
    if m := RE_SALIDA.search(t):
        return {"respuesta": m.group("r").replace('\\"', '"').replace("\\n", "\n").strip(), "pregunta_hecha": m.group("p").strip()}
    return {"respuesta": RE_RESTO_JSON.sub("", t).strip(), "pregunta_hecha": ""}
