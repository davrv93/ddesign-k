"""Jev (TypeSafe AI): modelo «System One» que devuelve decisiones tipadas con probabilidad, no texto.

Se le manda un ESTADO (la conversación) y PREGUNTAS con respuestas permitidas; contesta cada una con su
probabilidad en una sola pasada (~0,4 s). Aquí hace dos trabajos que el clasificador local no puede:

1. **Intención con contexto.** El clasificador local ve un mensaje suelto («puedo ir hoy?», «sí»). Jev ve la
   conversación entera, la etapa y lo último que preguntó el bot.
2. **Verificación de la respuesta del LLM.** Pregunta, párrafo por párrafo, si se afirma algo del producto
   que la ficha no dice («detalles brillantes», «pedrería»). Lo que marca se quita antes de enviar.

Jev propone; no decide. La etapa la sigue decidiendo etapas.py con reglas.

Modos (JEV_MODO):
- off      : no se llama (por defecto).
- sombra   : se llama en segundo plano, se registra [JEV] junto a la decisión local y no cambia nada.
             Sirve para medir con conversaciones reales antes de dejarle decidir.
- cascada  : si el clasificador local duda (confianza < JEV_UMBRAL, 0,80), se le pregunta a Jev con todo el
             contexto y se usa su respuesta si está más segura. Los mensajes claros no salen del servidor.
JEV_VERIFICAR=1 activa la verificación de la respuesta (independiente del modo).

Se llama por OpenRouter (POST /api/v1/systemone) con la misma clave que DeepSeek, o directo a TypeSafe con
JEV_URL=https://api.typesafe.ai/v1/systemone y JEV_API_KEY. No se manda el nombre de la clienta.
"""
from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

log = logging.getLogger("agente.jev")

MODO = os.environ.get("JEV_MODO", "off").strip().lower()
VERIFICAR = os.environ.get("JEV_VERIFICAR", "0") == "1"
URL = os.environ.get("JEV_URL", "https://openrouter.ai/api/v1/systemone")
MODELO = os.environ.get("JEV_MODEL", "typesafe/jev-1.13")
CLAVE = (os.environ.get("JEV_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
TIMEOUT = float(os.environ.get("JEV_TIMEOUT_SECONDS", "2.5"))
UMBRAL = float(os.environ.get("JEV_UMBRAL", "0.80"))            # por debajo, la cascada consulta a Jev
UMBRAL_INVENTO = float(os.environ.get("JEV_UMBRAL_INVENTO", "0.80"))  # desde aquí, un párrafo se quita

_http = httpx.Client(timeout=TIMEOUT)
_fondo = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jev")

# Las 20 intenciones del clasificador comercial (data/comercial.csv), descritas para Jev. Las mismas
# etiquetas: la máquina de etapas no distingue de dónde vino la intención.
INTENCIONES = {
    "saludo": "Solo saluda o llama la atención («hola», «buenas», «hay alguien?»), sin pedir nada concreto.",
    "consulta_producto": "Pide información general del producto o de cómo es (detalles, fotos, largo, escote, mangas), "
                         "o cuenta para qué evento busca ropa.",
    "consulta_material": "Pregunta por la tela o el material.",
    "consulta_precio": "Pregunta el precio o el costo de la prenda.",
    "consulta_talla": "Pregunta por tallas o medidas, o dice qué talla usa.",
    "consulta_color": "Pregunta por colores.",
    "consulta_disponibilidad": "Pregunta si todavía hay stock del producto o si sigue disponible.",
    "consulta_ubicacion": "Pregunta dónde está la tienda, o quiere ir en persona a verlo o probárselo.",
    "consulta_horario": "Pregunta el horario o qué días y horas atienden.",
    "consulta_delivery": "Pregunta por envíos, delivery, costo o tiempo de envío, o recojo.",
    "consulta_pago": "Pregunta cómo pagar o qué medios de pago aceptan, sin decir todavía que quiere comprar.",
    "interesado": "Muestra interés o le gusta el producto, o responde que sí a una pregunta que NO es de "
                  "confirmar el pedido. Interés no es compra.",
    "objecion_precio": "Le parece caro o pide descuento.",
    "objecion": "Duda o pospone: lo va a pensar, lo consulta, teme que no le quede, solo estaba viendo.",
    "comparacion": "Compara con otro modelo o pide ver otros modelos u opciones.",
    "intencion_compra": "Dice claramente que quiere comprar, llevar, separar, reservar o pagar el producto.",
    "confirmacion_compra": "Confirma el pedido respondiendo a una pregunta del bot que pedía confirmarlo "
                           "(«¿Confirmamos tu pedido?»).",
    "cancelacion": "Cancela o dice que ya no lo quiere.",
    "despedida": "Agradece o se despide.",
    "otro": "Nada de lo anterior: risas, «ok», preguntas sobre el bot o temas ajenos a la compra.",
}

PREGUNTA_INTENCION = ("¿Qué quiere la clienta con `mensaje_de_la_clienta`? Usa `historial` y `ultimo_mensaje_del_bot` "
                      "solo para entender respuestas cortas o ambiguas («sí», «ya», «puedo ir hoy?»).")
# Señales sueltas (sí/no), que una sola etiqueta no captura: «puedo ir hoy?» es horario Y visita.
SENALES = {
    "quiere_comprar": "¿En `mensaje_de_la_clienta` la clienta dice claramente que quiere comprar, separar o pagar "
                      "el producto (no solo que le gusta)?",
    "quiere_visitar": "¿En `mensaje_de_la_clienta` la clienta quiere ir en persona a la tienda a ver o probarse el producto?",
    "pide_otros_modelos": "¿En `mensaje_de_la_clienta` la clienta pide ver otros modelos u opciones distintas?",
}


def activo() -> bool:
    return MODO in ("sombra", "cascada") and bool(CLAVE)


def estado(mensaje: str, historial: list[dict], etapa: str, ultimo_bot: str, producto: str = "") -> dict:
    """El estado que ve Jev. Sin el nombre de la clienta."""
    return {
        "etapa_de_venta": etapa or "prospeccion",
        "producto_en_conversacion": producto or "ninguno",
        "historial": historial[-8:],
        "ultimo_mensaje_del_bot": ultimo_bot,
        "mensaje_de_la_clienta": mensaje,
    }


def _evaluar(state, preguntas: dict) -> tuple[dict, dict]:
    t0 = time.time()
    r = _http.post(URL, headers={"Authorization": f"Bearer {CLAVE}", "Content-Type": "application/json"},
                   json={"model": MODELO, "state": state, "questions": preguntas})
    r.raise_for_status()
    j = r.json()
    uso = j.get("usage") or {}
    return j.get("answers") or {}, {"ms": int((time.time() - t0) * 1000), "tokens": uso.get("input_tokens"), "costo": uso.get("cost")}


def clasificar(st: dict) -> dict | None:
    """Intención comercial con contexto, más tres señales sí/no. None si Jev no respondió."""
    if not CLAVE:
        return None
    preguntas = {"intencion": {"type": "choice", "instructions": PREGUNTA_INTENCION, "criteria": INTENCIONES}}
    preguntas |= {k: {"type": "noul", "instructions": v} for k, v in SENALES.items()}
    try:
        ans, meta = _evaluar(st, preguntas)
        it = ans["intencion"]
        probs = it.get("probabilities") or {}
        return {"intent": it["choice"], "confianza": round(float(probs.get(it["choice"], it.get("confidence", 0))), 3),
                "probabilidades": {k: round(v, 3) for k, v in sorted(probs.items(), key=lambda x: -x[1])[:3]},
                "senales": {k: round(float(ans[k]["noul"]), 3) for k in SENALES if k in ans}} | meta
    except Exception as e:  # sin Jev el bot sigue con el clasificador local
        log.warning("Jev no respondió: %s", e)
        return None


def sombra(st: dict, local: dict, conversacion: str) -> None:
    """Modo sombra: Jev clasifica en segundo plano y queda en el registro junto a la decisión local."""
    def tarea():
        j = clasificar(st)
        if j:
            log.info("[JEV] %s", json.dumps({
                "conversation_id": conversacion, "mensaje": st["mensaje_de_la_clienta"][:200],
                "local": {"intent": local["intent"], "confianza": round(float(local["confianza"]), 3)},
                "jev": {k: j[k] for k in ("intent", "confianza", "probabilidades", "senales", "ms")},
                "coincide": j["intent"] == local["intent"]}, ensure_ascii=False))
    _fondo.submit(tarea)


PREGUNTA_INVENTO = ("¿El párrafo `parrafo` le atribuye a la prenda alguna característica (material, tela, adornos, "
                    "brillo, pedrería, bordado, mangas, escote, largo, color, tallas o precio) que NO aparece en "
                    "`producto`?")
CRITERIO_INVENTO = {"true": "Describe la prenda con algo que `producto` no dice.",
                    "false": "Solo usa datos de `producto`, no describe la prenda, o habla de envíos, pagos, la "
                             "tienda o el evento de la clienta."}


def verificar(parrafos: list[str], producto: str) -> list[float] | None:
    """Probabilidad de que cada párrafo invente algo de la prenda. None si Jev no respondió."""
    if not CLAVE or not parrafos:
        return None
    preguntas = {f"p{k}": {"type": "noul", "instructions": {"parrafo": p, "producto": producto, "pregunta": PREGUNTA_INVENTO},
                           "criteria": CRITERIO_INVENTO} for k, p in enumerate(parrafos)}
    try:
        ans, meta = _evaluar({"tarea": "Verificar que la respuesta de una vendedora no invente datos del producto."}, preguntas)
        return [round(float(ans[f"p{k}"]["noul"]), 3) for k in range(len(parrafos))]
    except Exception as e:
        log.warning("Jev (verificación) no respondió: %s", e)
        return None


def filtrar(respuesta: str, producto: str, conversacion: str = "") -> str:
    """Quita los párrafos que Jev marca como inventados, salvo que no quede ninguno."""
    parrafos = [p for p in respuesta.split("\n\n") if p.strip()]
    pesos = verificar(parrafos, producto)
    if not pesos:
        return respuesta
    quedan = [p for p, w in zip(parrafos, pesos) if w < UMBRAL_INVENTO]
    if len(quedan) < len(parrafos):
        log.info("[JEV-VERIFICA] %s", json.dumps({"conversation_id": conversacion, "quitados": [
            {"parrafo": p[:160], "p": w} for p, w in zip(parrafos, pesos) if w >= UMBRAL_INVENTO]}, ensure_ascii=False))
    return "\n\n".join(quedan) if quedan else respuesta
