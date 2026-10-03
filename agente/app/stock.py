"""Stock como herramienta: se consulta al backend en el momento de responder.

El RAG decide qué prendas podrían interesar; esto decide qué se puede vender AHORA.
Nunca se guarda en el índice ni en las fichas: un embedding de hace cinco minutos no
puede afirmar «sí, tenemos V20 en M».

Respuesta por código (misma forma venga del backend o del respaldo):
    {"product": bool,                 # se vende en la tienda virtual
     "online": {"S": 1, "M": 0},      # disponible = físico - reservas vigentes
     "branches": [{"id","name","address","hours","sizes": {"M": 2}}],
     "fuente": "backend" | "seed"}
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

import httpx

from .sucursales import Sucursales

log = logging.getLogger("agente.stock")

STOCK_URL = os.environ.get("STOCK_URL", "")  # http://backend:8080/api/public/stock
STOCK_TIMEOUT = float(os.environ.get("STOCK_TIMEOUT_SECONDS", "2"))
# Caché cortísima: evita repetir la misma consulta dentro de un turno (vitrina + pies de foto).
STOCK_CACHE_SECONDS = float(os.environ.get("STOCK_CACHE_SECONDS", "3"))


class Stock:
    def __init__(self, url: str = STOCK_URL, seed_productos: list[dict] | None = None):
        self.url = url.rstrip("/")
        self._http = httpx.Client(timeout=STOCK_TIMEOUT)
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()
        self.ultima_fuente = "ninguna"
        # Respaldo cuando el backend no responde (o en la UI de prueba sin backend): el seed del
        # catálogo y las sucursales de demostración. Se marca como "seed" para que se note.
        self._seed_online: dict[str, dict] = {}
        for p in seed_productos or []:
            sizes = p.get("sizes") or {}
            self._seed_online[str(p.get("code", "")).upper()] = {t: (n if isinstance(n, int) else 1) for t, n in sizes.items()}
        self._seed_suc = Sucursales()

    # -- consulta ---------------------------------------------------------
    def consultar(self, codigos: list[str]) -> dict[str, dict]:
        codigos = [c.upper() for c in dict.fromkeys(codigos) if c]
        if not codigos:
            return {}
        ahora = time.time()
        out, faltan = {}, []
        with self._lock:
            for c in codigos:
                hit = self._cache.get(c)
                if hit and ahora - hit[0] < STOCK_CACHE_SECONDS:
                    out[c] = hit[1]
                else:
                    faltan.append(c)
        if faltan:
            nuevos = self._backend(faltan) or self._respaldo(faltan)
            with self._lock:
                for c, v in nuevos.items():
                    self._cache[c] = (ahora, v)
            out.update(nuevos)
        return out

    def _backend(self, codigos: list[str]) -> dict[str, dict]:
        if not self.url:
            return {}
        try:
            r = self._http.get(self.url, params={"codes": ",".join(codigos)})
            r.raise_for_status()
            out = {}
            for e in r.json().get("stock", []):
                out[e["code"].upper()] = {
                    "product": bool(e.get("product")),
                    "online": {t: int(v.get("available", 0)) for t, v in (e.get("online") or {}).items()},
                    "branches": e.get("branches") or [],
                    "fuente": "backend",
                }
            self.ultima_fuente = "backend"
            return out
        except Exception as e:
            log.warning("stock del backend no disponible (%s): uso el seed. %s", self.url, e)
            return {}

    def _respaldo(self, codigos: list[str]) -> dict[str, dict]:
        self.ultima_fuente = "seed"
        out = {}
        for c in codigos:
            branches = [{"id": s["id"], "name": s["nombre"], "address": s["direccion"], "hours": s.get("horario", ""), "sizes": tallas}
                        for s, tallas in self._seed_suc.de(c)]
            out[c] = {"product": c in self._seed_online, "online": dict(self._seed_online.get(c, {})),
                      "branches": branches, "fuente": "seed"}
        return out


# -- cálculo de disponibilidad (código, no LLM) -------------------------------

def tallas_online(st: dict) -> tuple[list[str], list[str]]:
    """(disponibles, agotadas) en la tienda virtual."""
    online = st.get("online") or {}
    return [t for t, n in online.items() if n > 0], [t for t, n in online.items() if n <= 0]


def estado(st: dict) -> str:
    """'online' (se pide ya por el bot), 'sucursal' (sólo en tienda física) o '' (nada)."""
    if tallas_online(st)[0]:
        return "online"
    return "sucursal" if any(b.get("sizes") for b in st.get("branches", [])) else ""


def resumen(st: dict) -> str:
    """Una línea para el LLM, calculada ahora mismo. Es lo único que el LLM puede afirmar sobre stock."""
    partes = []
    disp, agot = tallas_online(st)
    if st.get("product"):
        partes.append("tienda virtual: " + (f"disponible en {', '.join(disp)}" if disp else "AGOTADO"))
        if disp and agot:
            partes.append(f"agotado en {', '.join(agot)}")
    else:
        partes.append("no se vende en la tienda virtual")
    suc = [f"{b['name']} ({b['address']}): {', '.join(f'{t}={n}' for t, n in b['sizes'].items())}" for b in st.get("branches", []) if b.get("sizes")]
    partes.append("sucursales: " + ("; ".join(suc) if suc else "sin stock"))
    return " | ".join(partes)
