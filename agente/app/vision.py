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
CONFIANZA_MIN = 0.85     # por debajo, decide el parecido visual. 12-10: aciertos 0,94–0,99; sin el correcto entre candidatos dio 0,72
MAX_TOKENS = int(os.environ.get("VISION_MAX_TOKENS", "1500"))

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


CANDIDATOS_MAX = 5
TIENDA = os.path.join(os.environ.get("AGENTE_IMG_DIR", os.path.join(os.path.dirname(__file__), "..", "imagenes")), "tienda")


def _lista(fichas) -> str:
    """Solo código y nombre: sirve para leer una CAPTURA (el código o el nombre escrito). Para una FOTO deciden las fotos
    de los candidatos: con nombre, colores y rasgos en texto el modelo elegía «por el color» o por una frase de la ficha
    (12-10-2026: el Salome verde olivo de una clienta salió Alma y luego Blear, con 0,91)."""
    return "\n".join(f"- {f.codigo}: {f.nombre}" for f in fichas)


def _data_url(crudo: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(_jpeg(crudo)).decode()


def _foto_de(codigo: str) -> bytes | None:
    ruta = os.path.join(TIENDA, f"{codigo.lower()}.jpg")
    try:
        with open(ruta, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def identificar(crudo: bytes, fichas, mensaje: str = "", candidatos=()) -> dict | None:
    """{"codigo": "V50", "confianza": 0.94, "como": "texto|prenda", "motivo": "..."} o None.

    `candidatos`: los códigos más parecidos según el parecido visual (embeddings). El modelo ve la foto de la clienta
    junto a una foto de cada candidato y decide si es el MISMO modelo aunque el color sea otro (cada modelo viene en
    varios colores). Si la imagen es una captura con el código o el nombre escrito, manda eso. `codigo` vacío = ninguno."""
    if not configurado() or not fichas:
        return None
    codigos = {f.codigo.upper() for f in fichas}
    contenido = [{"type": "text", "text": "IMAGEN QUE ENVIÓ LA CLIENTA" + (f" (con el mensaje «{mensaje}»)" if mensaje else "") + ":"},
                 {"type": "image_url", "image_url": {"url": _data_url(crudo)}}]
    vistos = []
    for c in [str(x).upper() for x in candidatos if str(x).upper() in codigos][:CANDIDATOS_MAX]:
        foto = _foto_de(c)
        if foto:
            contenido += [{"type": "text", "text": f"CANDIDATO {c}:"}, {"type": "image_url", "image_url": {"url": _data_url(foto)}}]
            vistos.append(c)
    contenido.append({"type": "text", "text": (
        "Eres el catálogo de Baruka Design (ropa de mujer). La imagen de la clienta puede ser una CAPTURA de la web o del "
        "catálogo (con el código tipo V28 o el nombre escritos) o una FOTO de la prenda.\n"
        "- Si ves escrito un código o un nombre de esta lista, ese manda:\n" + _lista(fichas) + "\n"
        + (f"- Si es una foto, compárala con los CANDIDATOS ({', '.join(vistos)}): cada uno es un modelo que existe en "
           "VARIOS colores, así que decide por la FORMA (escote, mangas, corte, largo, abertura, tela), no por el color. "
           "Elige uno solo si es el mismo modelo; si dudas o no es ninguno, código vacío.\n" if vistos else
           "- Si es una foto sin texto, devuelve código vacío.\n")
        + "No inventes códigos. Responde SOLO JSON: "
          '{"codigo": "Vxx o vacío", "confianza": 0..1, "como": "texto|prenda", "motivo": "breve"}')})
    # gpt-6-luna razona antes de contestar: con 200 tokens los gastaba razonando y devolvía vacío (finish_reason=length),
    # así que la visión nunca decidía y mandaba el parecido visual (12-10-2026). Mismo ajuste que V3: tope alto, esfuerzo bajo.
    cuerpo = {"model": MODELO, "temperature": 0, "max_tokens": MAX_TOKENS, **_v3.EXTRA,
              "messages": [{"role": "user", "content": contenido}]}
    try:
        r = _http.post(URL, headers={"Authorization": f"Bearer {KEY}"}, json=cuerpo)
        r.raise_for_status()
        ch = r.json()["choices"][0]
        txt = (ch["message"].get("content") or "").strip()
        if not txt:
            log.warning("visión: respuesta vacía (fin=%s)", ch.get("finish_reason"))
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
