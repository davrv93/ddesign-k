"""Recuperación híbrida para la herramienta RAG del motor V2.

Junta el BM25F por campos (v2/lexico.py, peso 1) con el vector (peso bajo:
con pesos iguales el vector mete ruido) y deja que el reranker ELIJA la
unidad final con la regla «no quita» (app/rerank.py).

Para la lista que va al reranker se usa la fusión diversa (vector a 0,2):
más diversa que la final, y el MRR tras el reranker lo agradece.

Nunca tira el turno: sin índice, sin vector o sin reranker, devuelve el
orden del vector con su motivo. El detalle del turno queda en
``ultimo_detalle()`` (thread-local) para la traza por etapas.
"""
from __future__ import annotations

import threading
import time

from .lexico import PESO_VECTOR_DIVERSA, PESO_VECTOR_FINAL, Indice, campos_de_ficha, rrf

K_FINAL = 5
K_DIVERSA = 20
MAX_TEXTO_RERANK = 800

_lock = threading.Lock()
_indice: Indice | None = None
_firma: tuple | None = None
_local = threading.local()


def ultimo_detalle() -> dict | None:
    return getattr(_local, "detalle", None)


def tomar_detalle() -> dict | None:
    """El detalle del turno y lo borra: un turno que no usa el RAG no debe
    leer el del turno anterior del mismo worker."""
    det = getattr(_local, "detalle", None)
    if hasattr(_local, "detalle"):
        del _local.detalle
    return det


def _asegurar(fichas: list) -> Indice:
    """Índice perezoso sobre las fichas vivas; se reconstruye si cambia el catálogo."""
    global _indice, _firma
    firma = (len(fichas), tuple(getattr(f, "codigo", "") for f in fichas))
    with _lock:
        if _indice is None or _firma != firma:
            ix = Indice()
            for f in fichas:
                cod = getattr(f, "codigo", "")
                if cod:
                    ix.agregar(cod, campos_de_ficha(f))
            _indice, _firma = ix, firma
        return _indice


def hibrida(consulta: str, fichas: list, orden_vector: list[str],
            permitidos: set[str] | None = None, k_final: int = K_FINAL,
            k_diversa: int = K_DIVERSA, elegir_fn=None) -> tuple[list[str], dict]:
    """Devuelve (códigos, detalle). ``orden_vector`` es el ranking completo
    del vector; ``permitidos`` restringe a la categoría pedida (como V1)."""
    t0 = time.perf_counter()
    detalle: dict = {"fuente": "vector", "motivo": "sin_indice"}
    try:
        ix = _asegurar(fichas)
        t1 = time.perf_counter()
        lex = [d for d, _ in ix.buscar(consulta, k=k_diversa)]
        ms_lex = int((time.perf_counter() - t1) * 1000)
        if permitidos is not None:
            lex = [d for d in lex if d in permitidos]
            vec = [d for d in orden_vector if d in permitidos]
        else:
            vec = list(orden_vector)
        if not lex:
            detalle = {"fuente": "vector", "motivo": "lexico_vacio", "ms_lex": ms_lex}
            return vec[:k_final], detalle
        final = [d for d, _ in rrf([(lex, 1.0), (vec, PESO_VECTOR_FINAL)])][:k_final]
        diversa = [d for d, _ in rrf([(lex, 1.0), (vec, PESO_VECTOR_DIVERSA)])][:k_diversa]
        por_cod = {getattr(f, "codigo", ""): f for f in fichas}
        textos = [f"{c} · {getattr(por_cod[c], 'nombre', '')}"[:MAX_TEXTO_RERANK]
                  for c in diversa if c in por_cod]
        cods = [c for c in diversa if c in por_cod]
        detalle = {"fuente": "hibrida", "motivo": "ok", "ms_lex": ms_lex,
                   "lex_top": lex[:k_final], "vec_top": vec[:k_final],
                   "fusion": list(final), "diversa": list(diversa),
                   "opciones": [{"codigo": c, "nombre": getattr(por_cod[c], "nombre", "")}
                                for c in diversa[:3] if c in por_cod],
                   "rerank": {"motivo": "apagado"}}
        if elegir_fn is not None and textos:
            cobs = [ix.cobertura(consulta, c) for c in cods]
            r = elegir_fn(consulta, textos, cobs) or {}
            detalle["rerank"] = {k: r.get(k) for k in ("motivo", "puntaje", "margen_logit", "ms") if k in r}
            el = r.get("indice")
            if r.get("aceptado") and el is not None and 0 <= el < len(cods):
                detalle["elegido"] = cods[el]
                final = [cods[el]] + [c for c in final if c != cods[el]]
                detalle["fusion"] = list(final)
        detalle["ms_total"] = int((time.perf_counter() - t0) * 1000)
        out = final[:k_final]
    except Exception as e:  # noqa: BLE001 — sin híbrida el turno sigue con el vector
        try:
            out = list(orden_vector[:k_final])
        except Exception:
            out = []
        detalle = {"fuente": "vector", "motivo": type(e).__name__}
    _local.detalle = detalle
    return out, detalle
