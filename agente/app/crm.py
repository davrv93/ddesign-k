"""Aviso al CRM (Kommo) de cada turno del chat web.

El chat web (`/demo-design`) habla directo con este agente: no pasa por el backend Go, que es quien lleva las
conversaciones a Kommo (`backend/internal/kommo`). Para que los dos canales lleguen al CRM con la misma lógica, al final
de cada turno web el agente avisa al backend con un POST interno:

    POST {CRM_EVENT_URL}            (http://backend:8080/api/internal/crm/evento, red interna de Docker)
    X-CRM-Secret: {CRM_EVENT_SECRET}
    {"canal": "web", "conversacion": "<sesión>", "etapa": …, "memoria": {…}, "intencion": …, "mensaje": …, "respuesta": …}

y el backend lo encola al mismo sincronizador que WhatsApp. Sin las dos variables no se manda nada.

**Nunca retrasa la respuesta a la clienta:** el POST va en un hilo aparte, con 3 s de tope y sin reintentos; si el
backend o Kommo fallan, solo queda un aviso en el registro. El bot de WhatsApp (sin `canal`) no avisa por aquí: su turno
ya lo lleva el backend.

Sin dependencias: se prueba con `python3 -m app.prueba_crm`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.request

log = logging.getLogger("agente")

URL = os.environ.get("CRM_EVENT_URL", "").strip()
SECRETO = os.environ.get("CRM_EVENT_SECRET", "").strip()
TIMEOUT = float(os.environ.get("CRM_EVENT_TIMEOUT", "3"))

# La sesión del navegador (la UI la crea al abrir y en «Nuevo chat»). «web» a secas (UI vieja) no identifica a nadie.
RE_SESION = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def activo() -> bool:
    return bool(URL and SECRETO)


def payload(req, res: dict, envios: dict | None = None) -> dict | None:
    """El aviso de un turno web, o None si no toca (otro canal, sin sesión, sin respuesta)."""
    if getattr(req, "canal", "") != "web" or not isinstance(res, dict):
        return None
    sesion = (getattr(req, "conversacion", "") or "").strip()
    if sesion == "web" or not RE_SESION.match(sesion):
        return None
    mem = res.get("memoria") if isinstance(res.get("memoria"), dict) else None
    envio = ((mem or {}).get("sabemos") or {}).get("envio")
    costo = 0.0
    if envio and isinstance(envios, dict) and isinstance(envios.get(envio), dict):
        try:
            costo = float(envios[envio].get("costo") or 0)
        except (TypeError, ValueError):
            costo = 0.0
    comercial = res.get("comercial") if isinstance(res.get("comercial"), dict) else {}
    foto = res.get("foto") if isinstance(res.get("foto"), dict) else None
    return {
        "canal": "web",
        "conversacion": sesion,
        "etapa": res.get("etapa") or "",
        "memoria": mem,
        "intencion": comercial.get("intent") or "",
        "accion": res.get("accion") or "",
        "codigo": res.get("codigo") or "",
        "talla": res.get("talla") or "",
        "sugerencias": [s["codigo"] for s in (res.get("sugerencias") or []) if isinstance(s, dict) and s.get("codigo")][:6],
        "desde_anuncio": bool(getattr(req, "desde_anuncio", False)),
        "anuncio": getattr(req, "anuncio", "") or "",
        "envio_costo": costo,
        "mensaje": (getattr(req, "mensaje", "") or "")[:2000],
        "respuesta": (res.get("respuesta") or "")[:4000],
        "foto": {k: foto.get(k) for k in ("codigo", "caso", "similitud")} if foto else None,
    }


def _post(cuerpo: dict) -> None:
    try:
        datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
        pet = urllib.request.Request(URL, data=datos, method="POST",
                                     headers={"Content-Type": "application/json", "X-CRM-Secret": SECRETO})
        with urllib.request.urlopen(pet, timeout=TIMEOUT) as r:
            r.read()
    except Exception as e:  # noqa: BLE001 — el CRM nunca rompe la conversación
        log.warning("[CRM] no se pudo avisar el turno web %s: %s", cuerpo.get("conversacion"), e)


def avisar(req, res: dict, envios: dict | None = None) -> bool:
    """Manda el aviso en segundo plano. Devuelve True si salió un aviso (para las pruebas)."""
    if not activo():
        return False
    cuerpo = payload(req, res, envios)
    if cuerpo is None:
        return False
    threading.Thread(target=_post, args=(cuerpo,), daemon=True, name="crm-aviso").start()
    return True
