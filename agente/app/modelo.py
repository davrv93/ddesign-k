"""Embeddings locales (ONNX, sin GPU) y clasificadores ligeros sobre ellos.

- Embeddings: jinaai/jina-embeddings-v2-base-es (bilingüe español/inglés, Apache-2.0)
  servido con fastembed/onnxruntime. Se puede cambiar con EMBED_MODEL, p. ej. a
  sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 si falta RAM.
- Clasificadores: regresión logística (scikit-learn) sobre el vector normalizado.
  Uno para la intención del mensaje y otro para la categoría de prenda.
"""
from __future__ import annotations

import os
import pickle

import numpy as np

EMBED_MODEL = os.environ.get("EMBED_MODEL", "jinaai/jina-embeddings-v2-base-es")
CACHE_DIR = os.environ.get("EMBED_CACHE_DIR", "/models")
INDEX_DIR = os.environ.get("AGENTE_INDEX_DIR", os.path.join(os.path.dirname(__file__), "..", "index"))


class Embedder:
    def __init__(self, model: str = EMBED_MODEL):
        from fastembed import TextEmbedding

        self.model_name = model
        threads = int(os.environ.get("EMBED_THREADS", "2"))
        self._m = TextEmbedding(model_name=model, cache_dir=CACHE_DIR, threads=threads)

    def __call__(self, textos: list[str], batch: int = 32) -> np.ndarray:
        vecs = np.array(list(self._m.embed(textos, batch_size=batch)), dtype=np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
        return vecs


def entrenar_clasificador(X: np.ndarray, y: list[str]):
    from sklearn.linear_model import LogisticRegression

    clf = LogisticRegression(max_iter=3000, C=8.0, class_weight="balanced")
    clf.fit(X, y)
    return clf


def guardar(nombre: str, obj) -> None:
    os.makedirs(INDEX_DIR, exist_ok=True)
    with open(os.path.join(INDEX_DIR, nombre), "wb") as f:
        pickle.dump(obj, f)


def cargar(nombre: str):
    with open(os.path.join(INDEX_DIR, nombre), "rb") as f:
        return pickle.load(f)
