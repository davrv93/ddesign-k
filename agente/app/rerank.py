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

_modelo = None
_roto = False


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
