"""Embeddings locales (ONNX, sin GPU) y clasificadores ligeros sobre ellos.

- Embeddings: multilingual-e5-small cuantizado (Xenova/multilingual-e5-small, MIT), servido con
  fastembed/onnxruntime. Medido el 04-10-2026 contra jina-embeddings-v2-base-es: 500 MB de RAM frente
  a 897, 2 ms por mensaje frente a 13. La versión sin cuantizar ocupa 860 MB: no ahorra casi nada.
  Se cambia con EMBED_MODEL (p. ej. jinaai/jina-embeddings-v2-base-es).
- e5 distingue consultas y pasajes con un prefijo: los mensajes van como «query: » y las fichas del
  catálogo como «passage: ». Sin eso recupera peor.
- Clasificadores: estandarización + regresión logística (scikit-learn). Los vectores de e5 vienen muy
  apretados entre sí; sin estandarizar, la misma regresión acierta 70 % en vez de 94 %.
"""
from __future__ import annotations

import os
import pickle

import numpy as np

EMBED_MODEL = os.environ.get("EMBED_MODEL", "Xenova/multilingual-e5-small")
# Modelos que fastembed no trae de fábrica: se registran con su archivo ONNX.
MODELOS_PROPIOS = {
    "Xenova/multilingual-e5-small": {"dim": 384, "model_file": "onnx/model_quantized.onnx"},
    "intfloat/multilingual-e5-small": {"dim": 384, "model_file": "onnx/model.onnx"},
}
CACHE_DIR = os.environ.get("EMBED_CACHE_DIR", "/models")
INDEX_DIR = os.environ.get("AGENTE_INDEX_DIR", os.path.join(os.path.dirname(__file__), "..", "index"))


class Embedder:
    def __init__(self, model: str = EMBED_MODEL):
        from fastembed import TextEmbedding

        self.model_name = model
        self.e5 = "e5" in model.lower()
        if model in MODELOS_PROPIOS:
            from fastembed.common.model_description import ModelSource, PoolingType
            try:
                TextEmbedding.add_custom_model(model=model, pooling=PoolingType.MEAN, normalization=True,
                                               sources=ModelSource(hf=model), **MODELOS_PROPIOS[model])
            except ValueError:
                pass  # ya registrado en este proceso
        threads = int(os.environ.get("EMBED_THREADS", "2"))
        self._m = TextEmbedding(model_name=model, cache_dir=CACHE_DIR, threads=threads)

    def _vec(self, textos: list[str], prefijo: str, batch: int = 32) -> np.ndarray:
        vecs = np.array(list(self._m.embed([prefijo + t for t in textos], batch_size=batch)), dtype=np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
        return vecs

    def __call__(self, textos: list[str], batch: int = 32) -> np.ndarray:
        """Mensajes de la clienta y cualquier texto que se clasifica o se usa para buscar."""
        return self._vec(textos, "query: " if self.e5 else "", batch)

    def pasajes(self, textos: list[str], batch: int = 32) -> np.ndarray:
        """Fichas del catálogo: lo que se busca."""
        return self._vec(textos, "passage: " if self.e5 else "", batch)


def entrenar_clasificador(X: np.ndarray, y: list[str]):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    clf = make_pipeline(StandardScaler(), LogisticRegression(max_iter=5000, C=8.0, class_weight="balanced"))
    clf.fit(X, y)
    return clf


def guardar(nombre: str, obj) -> None:
    os.makedirs(INDEX_DIR, exist_ok=True)
    with open(os.path.join(INDEX_DIR, nombre), "wb") as f:
        pickle.dump(obj, f)


def cargar(nombre: str):
    with open(os.path.join(INDEX_DIR, nombre), "rb") as f:
        return pickle.load(f)
