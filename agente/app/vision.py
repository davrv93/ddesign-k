"""Reconocer la prenda de una foto con un modelo de visión, antes del parecido visual (embeddings).

Por qué (12-10-2026): la clienta mandó una CAPTURA del catálogo de la tienda («V28 · Vestido Azra Turquesa», con
marco, texto y recorte) y el parecido visual contestó «ese modelito exacto no lo tenemos». Una captura trae el código
y el nombre escritos; una foto de la prenda trae color, estampado y corte. El modelo de visión ve las dos cosas y
elige UNA prenda de la lista del catálogo vivo, o ninguna.

Usa el mismo proveedor que V3 (app/v3/cliente_llm: V3_* o LLM_*; hoy luna por OpenRouter). Sin clave, con
VISION=0 o ante cualquier error devuelve None y la búsqueda sigue como antes: nunca tumba /foto.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re

import httpx

log = logging.getLogger("agente.vision")

from .v3 import cliente_llm as _v3   # mismo proveedor que V3, ya resuelto (V3_* o, si no hay, LLM_*)

ACTIVO = os.environ.get("VISION", "1") != "0"
URL, KEY = _v3.URL, _v3.KEY
MODELO = (os.environ.get("VISION_MODELO") or _v3.MODELO or "").split(",")[0].strip()
TIMEOUT = float(os.environ.get("VISION_TIMEOUT_S", "20"))
LADO_MAX = 1024          # px: basta para leer el texto de una captura y abarata la imagen
CONFIANZA_MIN = 0.7      # por debajo, decide el parecido visual de siempre

_http = httpx.Client(timeout=TIMEOUT)


def configurado() -> bool:
    return ACTIVO and bool(URL and KEY and MODELO)


def _jpeg(crudo: bytes) -> bytes:
    """La imagen reducida a LADO_MAX y en JPEG (las capturas suelen venir en PNG grandes)."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(crudo))
        im = im.convert("RGB")
        im.thumbnail((LADO_MAX, LADO_MAX))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=85)
        return out.getvalue()
    except Exception:
        return crudo


def _lista(fichas) -> str:
    return "\n".join(f"- {f.codigo}: {f.nombre} · {getattr(f, 'categoria', '')} · color {getattr(f, 'color', '') or '—'}"
                     for f in fichas)


def identificar(crudo: bytes, fichas, mensaje: str = "") -> dict | None:
    """{"codigo": "V28", "confianza": 0.95, "como": "texto|prenda", "motivo": "..."} o None.

    `codigo` vacío = no es ninguna prenda del catálogo (o no se puede saber)."""
    if not configurado() or not fichas:
        return None
    codigos = {f.codigo.upper() for f in fichas}
    img = base64.b64encode(_jpeg(crudo)).decode()
    pedido = (
        "Eres el catálogo de Baruka Design (ropa de mujer). La clienta envió esta imagen"
        + (f" con el mensaje «{mensaje}»" if mensaje else "") + ".\n"
        "Puede ser una CAPTURA de la web o del catálogo (con el código tipo V28 o el nombre escritos) o una FOTO de la "
        "prenda. Decide cuál de estas prendas es, o ninguna:\n" + _lista(fichas) + "\n\n"
        "Reglas: si ves el código o el nombre escrito, ese manda. Si es una foto, compara color, estampado, largo y "
        "corte; si dudas entre dos o no está en la lista, devuelve código vacío. No inventes códigos.\n"
        'Responde SOLO JSON: {"codigo": "Vxx o vacío", "confianza": 0..1, "como": "texto|prenda", "motivo": "breve"}'
    )
    cuerpo = {"model": MODELO, "temperature": 0, "max_tokens": 200,
              "messages": [{"role": "user", "content": [
                  {"type": "text", "text": pedido},
                  {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}}]}]}
    try:
        r = _http.post(URL, headers={"Authorization": f"Bearer {KEY}"}, json=cuerpo)
        r.raise_for_status()
        txt = (r.json()["choices"][0]["message"].get("content") or "").strip()
        m = re.search(r"\{.*\}", txt, re.S)
        js = json.loads(m.group(0)) if m else {}
    except Exception as e:
        log.warning("visión: no respondió (%s); sigue el parecido visual", str(e)[:160])
        return None
    cod = str(js.get("codigo") or "").strip().upper()
    if cod and cod not in codigos:          # código inventado o retirado del catálogo vivo
        log.info("visión: devolvió %s, que no está en el catálogo", cod)
        cod = ""
    try:
        conf = max(0.0, min(1.0, float(js.get("confianza") or 0)))
    except (TypeError, ValueError):
        conf = 0.0
    res = {"codigo": cod, "confianza": conf, "como": str(js.get("como") or ""), "motivo": str(js.get("motivo") or "")[:200]}
    log.info("visión: %s", res)
    return res
