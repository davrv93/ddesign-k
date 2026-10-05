"""Observabilidad por versión (spec §21–22): cuántos turnos, con qué latencia y con qué control de calidad, para
comparar V1 contra V2. En memoria de proceso; se reinicia con el contenedor.

El acuerdo con V1 se mide solo en los turnos que NO resolvió un flujo de código (en esos V2 no compite), y compara el plan
de V2 con lo que V1 hizo de verdad (`accion_v1`: prendas con foto, pregunta, pedido…), no con su etiqueta `accion`."""
from __future__ import annotations

import threading
from collections import Counter, defaultdict, deque



def _clase(motivo: str) -> str:
    """Agrupa los motivos para contarlos («desacuerdo con V1 (V1 preguntar, V2 recomendar)» → «desacuerdo con V1»)."""
    return motivo.split(" (")[0]


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
        self._motivos = defaultdict(Counter)       # por qué V2 no habló, por versión

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
                if not sb.get("v1_flujo_fijo"):
                    c["comparables"] += 1
                    if sb.get("v1_accion") == sb["plan"]["accion"]:
                        c["acuerdo_v1"] += 1
            if traza.get("modo") == "activo":
                c["activo"] += 1
                if traza.get("enviado") == "v2":
                    c["habla_v2"] += 1
                elif traza.get("motivo_v1"):
                    self._motivos[version][_clase(traza["motivo_v1"])] += 1
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
                cmp_ = c["comparables"] or 1
                out[v] = {
                    "turnos": c["turnos"],
                    "p50_ms": _pct(self._lat[v], 0.5), "p95_ms": _pct(self._lat[v], 0.95),
                    "fallback_contexto": round(c["fallback_contexto"] / n, 3),
                    "motor_error": round(c["motor_error"] / n, 3),
                    "tope": round(c["tope"] / n, 3),
                    "plan_valido": round(c["plan_valido"] / n, 3),
                    "comparables": c["comparables"],
                    "acuerdo_v1": round(c["acuerdo_v1"] / cmp_, 3),
                    "activo": c["activo"],
                    "habla_v2": round(c["habla_v2"] / (c["activo"] or 1), 3),
                    "por_que_no_habla": dict(self._motivos[v].most_common(6)),
                    "calidad_ok": round(c["calidad_ok"] / b, 3),
                    "regeneracion": round(c["regeneraciones"] / b, 3),
                    "fallback_codigo": round(c["fallback_codigo"] / b, 3),
                }
            return out


REGISTRO = Registro()
