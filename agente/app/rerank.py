"""Reranker del RAG: un cross-encoder que reordena los candidatos por relevancia real con la consulta.

El vector (e5) recupera candidatos; el cross-encoder lee consulta + ficha juntos y los puntúa mejor. Es la
mejora más directa de precisión («reconoce la prenda que pide»). Va **apagado** (RERANK=0): hay que
validarlo en un build antes de encenderlo, y si el modelo no carga se desactiva solo y no rompe nada.

- RERANK=1 lo enciende; RERANK_MODEL el modelo (multilingüe, por el español).
- Se carga perezoso: la primera consulta paga la descarga/carga; si falla, se apaga y sigue el orden del RAG.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("agente.rerank")

ACTIVO = os.environ.get("RERANK", "0") == "1"
MODELO = os.environ.get("RERANK_MODEL", "jinaai/jina-reranker-v2-base-multilingual")
CACHE_DIR = os.environ.get("EMBED_CACHE_DIR", "/models")
LOTE = int(os.environ.get("RERANK_BATCH", "16"))
MARGEN = float(os.environ.get("RERANK_MARGEN", "0.405"))
UMBRAL = float(os.environ.get("RERANK_UMBRAL", "0.5"))
MAX_MS = int(os.environ.get("RERANK_MAX_MS", "3000"))

_modelo = None
_roto = False
_ms_por_candidato: float | None = None


def activo() -> bool:
    return ACTIVO and not _roto


def _cargar():
    global _modelo, _roto
    if _modelo is not None or _roto:
        return _modelo
    try:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        threads = int(os.environ.get("EMBED_THREADS", "2"))
        _modelo = TextCrossEncoder(model_name=MODELO, cache_dir=CACHE_DIR, threads=threads)
        log.info("reranker listo: %s", MODELO)
    except Exception as e:  # noqa: BLE001 — sin reranker el bot sigue con el orden del RAG
        _roto = True
        log.warning("reranker no disponible (%s): sigo con el orden del RAG", e)
    return _modelo


def _sigmoide(x: float) -> float:
    import math
    return 1.0 / (1.0 + math.exp(-x))


def elegir(consulta: str, textos: list[str], coberturas: list[float] | None = None,
           puntuar=None, margen: float = MARGEN, umbral: float = UMBRAL,
           max_ms: int = MAX_MS) -> dict:
    """El reranker ELIGE la unidad (no solo reordena), con la regla «no quita».

    Cada candidata puntúa ``max(sigmoide(logit), cobertura_léxica)``: sin la
    regla, el reranker rechaza lo que el léxico ya acertaba (un «¿dónde
    está…?» frente a «Registrar…» da logit ~0 aunque sea el correcto).

    Gate de tiempo (§4 del doc): en CPU el reranker no llega nunca; si
    ``n × ms_por_candidato`` supera ``max_ms``, ni se intenta y el llamante
    conserva el orden léxico. Nunca tira el turno.
    """
    import time
    global _ms_por_candidato
    n = len(textos or [])
    if not activo():
        return {"indice": None, "aceptado": False, "motivo": "apagado", "ms": 0}
    if not (consulta or "").strip() or not n:
        return {"indice": None, "aceptado": False, "motivo": "sin_candidatos", "ms": 0}
    if coberturas is None:
        coberturas = [0.0] * n
    if _ms_por_candidato and n * _ms_por_candidato > max_ms:
        return {"indice": None, "aceptado": False, "motivo": "fuera_de_tiempo_estimado", "ms": 0,
                "estimado_ms": int(n * _ms_por_candidato)}
    t0 = time.perf_counter()
    try:
        if puntuar is not None:
            crudos = list(puntuar(consulta, list(textos)))
        else:
            m = _cargar()
            if m is None:
                return {"indice": None, "aceptado": False, "motivo": "no_disponible", "ms": 0}
            crudos = list(m.rerank(consulta, list(textos), batch_size=LOTE))
    except Exception as e:  # noqa: BLE001
        log.warning("reranker falló al elegir: %s", e)
        return {"indice": None, "aceptado": False, "motivo": type(e).__name__, "ms": 0}
    ms = int((time.perf_counter() - t0) * 1000)
    _ms_por_candidato = ms / n if _ms_por_candidato is None else 0.7 * _ms_por_candidato + 0.3 * (ms / n)
    en_prob = all(0.0 <= x <= 1.0 for x in crudos)
    probs = [float(x) if en_prob else _sigmoide(float(x)) for x in crudos]
    finales = [max(p, min(1.0, max(0.0, c))) for p, c in zip(probs, coberturas)]
    orden = sorted(range(n), key=lambda i: -finales[i])
    g = orden[0]
    margen_logit = (float(crudos[g]) - float(crudos[orden[1]])) if n > 1 else 0.0
    out = {"indice": g, "puntaje": round(finales[g], 3), "logit": round(float(crudos[g]), 3),
           "cobertura": round(float(coberturas[g]), 3),
           "margen_logit": round(margen_logit, 3), "ms": ms,
           "ms_por_candidato": round(_ms_por_candidato, 1)}
    if finales[g] < umbral:
        return out | {"aceptado": False, "motivo": "bajo_umbral"}
    if n > 1 and margen_logit < margen:
        out["duda"] = True
    return out | {"aceptado": True, "motivo": "ok" if not out.get("duda") else "desempate"}


def ordenar(consulta: str, fichas: list) -> list:
    """Reordena `fichas` por relevancia a la consulta. Con el reranker apagado o roto, las deja igual."""
    if not activo() or not fichas or not consulta.strip():
        return fichas
    m = _cargar()
    if m is None:
        return fichas
    try:
        docs = [f.texto() for f in fichas]
        puntos = list(m.rerank(consulta, docs, batch_size=LOTE))
        return [f for _, f in sorted(zip(puntos, fichas), key=lambda x: -x[0])]
    except Exception as e:  # noqa: BLE001
        log.warning("reranker falló al ordenar: %s", e)
        return fichas
