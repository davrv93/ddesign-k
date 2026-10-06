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
        self._cat = defaultdict(Counter)           # lecturas semánticas con intención, por catálogo

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
            tm = traza.get("temas") or {}
            if tm.get("evento"):                      # cambios de tema (v2/temas.py)
                tipo = tm["evento"].get("tipo")
                c["temas_turnos"] += 1
                c["temas_interrupciones"] += tipo == "interrumpe"
                c["temas_cambios_totales"] += tipo == "cambio"
                c["temas_ayuda"] += tipo == "ayuda"
                c["temas_retomas_planeadas"] += bool(tm.get("retoma"))
                c["temas_retomas_enviadas"] += bool(tm.get("enviada"))
                c["temas_v1_retomo"] += bool(tm.get("v1_retomo"))
            elif tm.get("error"):
                c["temas_error"] += 1
            cg = traza.get("catalogos")
            if cg:                                     # catálogos semánticos (v2/semantica.py)
                c["cat_turnos"] += 1
                if cg.get("error"):
                    c["cat_error"] += 1
                elif cg.get("intent"):
                    c["cat_con_lectura"] += 1
                    self._cat[version][cg.get("catalogo")] += 1
                    c["cat_habria_cambiado"] += bool(cg.get("habria_cambiado"))
                    c["cat_tema_aplicado"] += bool(cg.get("modo") == "activo" and cg.get("tema") and (tm.get("evento") or {}).get("causa", "").startswith("catálogo"))
            gen = sb.get("generacion") or {}
            rg = traza.get("rag") or {}
            if rg:                                     # recuperación híbrida (v2/rag.py)
                c["rag_turnos"] += 1
                c["rag_hibrida"] += rg.get("fuente") == "hibrida"
                rr = rg.get("rerank") or {}
                c["rerank_elige"] += rr.get("motivo") in ("ok", "desempate")
                if rr.get("motivo") not in (None, "ok", "apagado", "desempate"):
                    self._motivos[version][f"reranker: {rr.get('motivo')}"] += 1
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
                    # Cambios de tema: cuántas interrupciones vio, cuántas retomas planeó/envió y cuántas veces V1 ya había vuelto a preguntar solo
                    "catalogos": {"turnos": c["cat_turnos"], "con_lectura": c["cat_con_lectura"], "errores": c["cat_error"],
                                  "cobertura": round(c["cat_con_lectura"] / (c["cat_turnos"] or 1), 3),
                                  "por_catalogo": dict(self._cat[v].most_common()), "habria_cambiado_el_tema": c["cat_habria_cambiado"],
                                  "tema_aplicado": c["cat_tema_aplicado"]},
                    "temas": {"interrupciones": c["temas_interrupciones"], "cambios_totales": c["temas_cambios_totales"], "ayuda": c["temas_ayuda"],
                              "retomas_planeadas": c["temas_retomas_planeadas"], "retomas_enviadas": c["temas_retomas_enviadas"],
                              "v1_retomo_solo": c["temas_v1_retomo"], "errores": c["temas_error"]},
                    "rag": {"turnos": c["rag_turnos"],
                            "hibrida": round(c["rag_hibrida"] / (c["rag_turnos"] or 1), 3),
                            "rerank_elige": round(c["rerank_elige"] / (c["rag_turnos"] or 1), 3)},
                }
            return out


REGISTRO = Registro()
