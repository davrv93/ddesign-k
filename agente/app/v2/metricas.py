"""Observabilidad por versión (spec §21–22): cuántos turnos, con qué latencia y con qué control de calidad, para
comparar V1 contra V2. En memoria de proceso; se reinicia con el contenedor."""
from __future__ import annotations

import threading
from collections import defaultdict, deque

MAPA_V1 = {"responder": "responder", "codigo": "recomendar", "foto": "recomendar", "pedido": "confirmar_pedido"}


def _pct(xs, q: float) -> int:
    if not xs:
        return 0
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


class Registro:
    def __init__(self, maximo: int = 500):
        self._lock = threading.Lock()
        self._lat = defaultdict(lambda: deque(maxlen=maximo))
        self._c = defaultdict(lambda: defaultdict(int))

    def turno(self, version: str, ms: int, traza: dict | None = None) -> None:
        with self._lock:
            c = self._c[version]
            c["turnos"] += 1
            self._lat[version].append(ms)
            if not traza:
                return
            if traza.get("fallback"):
                c["fallback_contexto"] += 1
            sb = traza.get("sombra") or {}
            if sb.get("error"):
                c["motor_error"] += 1
            if sb.get("tope"):
                c["tope"] += 1
            if sb.get("plan") and not sb.get("errores"):
                c["plan_valido"] += 1
                if MAPA_V1.get(sb.get("v1_accion")) == sb["plan"]["accion"]:
                    c["acuerdo_v1"] += 1
            gen = sb.get("generacion") or {}
            if gen.get("intentos"):
                c["con_borrador"] += 1
                c["regeneraciones"] += gen.get("regeneraciones", 0)
                if gen.get("passed"):
                    c["calidad_ok"] += 1
                if gen.get("fallback"):
                    c["fallback_codigo"] += 1

    def resumen(self) -> dict:
        with self._lock:
            out = {}
            for v, c in self._c.items():
                n = c["turnos"] or 1
                b = c["con_borrador"] or 1
                p = c["plan_valido"] or 1
                out[v] = {
                    "turnos": c["turnos"],
                    "p50_ms": _pct(self._lat[v], 0.5), "p95_ms": _pct(self._lat[v], 0.95),
                    "fallback_contexto": round(c["fallback_contexto"] / n, 3),
                    "motor_error": round(c["motor_error"] / n, 3),
                    "tope": round(c["tope"] / n, 3),
                    "plan_valido": round(c["plan_valido"] / n, 3),
                    "acuerdo_v1": round(c["acuerdo_v1"] / p, 3),
                    "calidad_ok": round(c["calidad_ok"] / b, 3),
                    "regeneracion": round(c["regeneraciones"] / b, 3),
                    "fallback_codigo": round(c["fallback_codigo"] / b, 3),
                }
            return out


REGISTRO = Registro()
