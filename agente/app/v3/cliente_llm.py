"""Cliente del LLM para V3: OpenAI-compatible con `tools` (function calling).

Un solo lugar habla con el proveedor. Cadena: DeepSeek directo (V3_URL/V3_API_KEY/V3_MODELO) y, si no hay
clave, cae a la del motor «actual» (LLM_*). Reintenta una vez por red y corta ante 401/403 (clave muerta).
El bucle de herramientas lo controla el llamador (`ejecutar`), no este módulo.
"""
from __future__ import annotations

import json
import logging
import os

import httpx

log = logging.getLogger("agente.v3")

URL = os.environ.get("V3_URL") or os.environ.get("DEEPSEEK_URL") or "https://api.deepseek.com/v1/chat/completions"
KEY = (os.environ.get("V3_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
       or os.environ.get("LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or "")
MODELO = (os.environ.get("V3_MODELO") or (os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat").split(",")[0]).strip()


def _cfg() -> tuple[str, str, str]:
    """A qué proveedor llama V3. Si hay V3_* propio (DeepSeek directo), ese; si no, el motor «actual»
    (LLM_*), que hoy es el que tiene clave válida. Así V3 no depende de una clave que no exista."""
    if os.environ.get("V3_API_KEY") or os.environ.get("V3_URL"):
        return (os.environ.get("V3_URL") or "https://api.deepseek.com/v1/chat/completions",
                os.environ.get("V3_API_KEY", ""),
                (os.environ.get("V3_MODELO") or "deepseek-chat").split(",")[0].strip())
    return (os.environ.get("LLM_URL") or "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            os.environ.get("LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or "",
            (os.environ.get("LLM_MODEL") or "gemini-3.5-flash-lite").split(",")[0].strip())


URL, KEY, MODELO = _cfg()
TIMEOUT = float(os.environ.get("V3_TIMEOUT_MS", "8000")) / 1000.0
MAX_VUELTAS = max(1, int(os.environ.get("V3_MAX_VUELTAS", "3")))

_http = httpx.Client(timeout=TIMEOUT)


def configurado() -> bool:
    return bool(KEY and URL)


class LLMCaido(RuntimeError):
    """El proveedor no respondió: V3 cae a V1."""


def responder(mensajes: list[dict], tools: list[dict], ejecutar, modelo: str = "", max_vueltas: int = 0) -> dict:
    """Corre el ciclo de herramientas y devuelve {"contenido", "vueltas", "usadas"}.

    `ejecutar(nombre, args)` corre la herramienta y devuelve su resultado (dict). Se corta al llegar a
    `max_vueltas` (o cuando el modelo deja de pedir herramientas) y se devuelve su último texto.
    """
    if not configurado():
        raise LLMCaido("V3 sin clave ni URL")
    modelo = modelo or MODELO
    tope = max_vueltas or MAX_VUELTAS
    msgs = list(mensajes)
    usadas: list[str] = []
    for vuelta in range(tope):
        envio = msgs
        if vuelta == tope - 1:  # última vuelta CON herramientas: que actúe ahora, no que lo prometa y lo deje narrado
            envio = msgs + [{"role": "user", "content": "Es tu última vuelta: si ya tienes lo que necesitas, llama "
                             "ahora a enviar_fotos/crear_reserva/agendar_cita y responde; si no, responde con lo que "
                             "ya sabes. No sigas buscando ni digas que vas a revisar algo."}]
        cuerpo = {"model": modelo, "messages": envio, "temperature": 0.5, "max_tokens": 700,
                  "tools": tools, "tool_choice": "auto"}
        js = _post(cuerpo)
        msg = js["choices"][0]["message"]
        llamadas = msg.get("tool_calls") or []
        if not llamadas:
            return {"contenido": (msg.get("content") or "").strip(), "vueltas": vuelta + 1, "usadas": usadas}
        msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": llamadas})
        for call in llamadas:
            nombre = call.get("function", {}).get("name", "")
            try:
                args = json.loads(call.get("function", {}).get("arguments") or "{}")
            except ValueError:
                args = {}
            resultado = ejecutar(nombre, args)
            usadas.append(nombre)
            msgs.append({"role": "tool", "tool_call_id": call.get("id", ""),
                         "content": json.dumps(resultado, ensure_ascii=False)})
    # Último recurso: incluso con el aviso de «es tu última vuelta» siguió pidiendo herramientas (ya las
    # corrimos arriba). Ahora sí, sin tools: que redacte con lo que ya tiene, en vez de quedar sin responder.
    cuerpo = {"model": modelo, "messages": msgs, "temperature": 0.5, "max_tokens": 700}
    js = _post(cuerpo)
    return {"contenido": (js["choices"][0]["message"].get("content") or "").strip(),
            "vueltas": tope, "usadas": usadas}


def _post(cuerpo: dict) -> dict:
    try:
        r = _http.post(URL, headers={"Authorization": f"Bearer {KEY}"}, json=cuerpo)
    except Exception as e:  # red
        raise LLMCaido(f"red: {e}")
    if r.status_code in (401, 403):
        raise LLMCaido(f"clave inválida: HTTP {r.status_code}")
    if r.status_code >= 400:
        raise LLMCaido(f"HTTP {r.status_code}: {r.text[:200]}")
    return r.json()
