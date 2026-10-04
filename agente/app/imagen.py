"""Búsqueda por foto: embeddings de imagen locales (ONNX) contra las fotos del catálogo.

Modelo por defecto: Qdrant/Unicom-ViT-B-32 (Apache-2.0), elegido midiendo 120 fotos del
catálogo deformadas (recorte, giro, brillo, color, borde, JPEG al 55 %):

    clip-ViT-B-32   top-1 80,0 %   top-3 90,8 %   140 ms/foto
    jina-clip-v1    top-1 90,0 %   top-3 95,0 %   360 ms/foto
    Unicom-ViT-B-32 top-1 96,7 %   top-3 98,3 %   128 ms/foto

    python -m app.imagen          # indexa las fotos, calibra umbrales y guarda index/imagenes.pkl
"""
from __future__ import annotations

import glob
import io
import os
import random

import numpy as np

from .modelo import CACHE_DIR, cargar, guardar

IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "Qdrant/Unicom-ViT-B-32")
IMG_DIR = os.environ.get("AGENTE_IMG_DIR", os.path.join(os.path.dirname(__file__), "..", "imagenes"))


class ImageEmbedder:
    def __init__(self, model: str = IMAGE_MODEL):
        from fastembed import ImageEmbedding

        self.model_name = model
        self._m = ImageEmbedding(model_name=model, cache_dir=CACHE_DIR, threads=int(os.environ.get("EMBED_THREADS", "2")))

    def __call__(self, imagenes) -> np.ndarray:
        v = np.array(list(self._m.embed(imagenes, batch_size=8)), dtype=np.float32)
        return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)


def abrir(datos: bytes):
    from PIL import Image, ImageOps

    im = Image.open(io.BytesIO(datos))
    im = ImageOps.exif_transpose(im)  # fotos de celular giradas
    return im.convert("RGB")


def fotos_catalogo() -> list[tuple[str, str]]:
    """(código, ruta) de cada foto: las de la tienda (tienda/v01.jpg -> V01; las vistas extra tienda/v21_2.jpg
    -> V21) y las del catálogo de 100."""
    out = []
    if os.environ.get("CATALOGO100", "1") != "0":  # sin él, solo se reconocen las prendas de la tienda
        out += [(os.path.basename(p)[:-4].upper(), p) for p in sorted(glob.glob(os.path.join(IMG_DIR, "*.jpg")))]
    out += [(os.path.basename(p)[:-4].split("_")[0].upper(), p) for p in sorted(glob.glob(os.path.join(IMG_DIR, "tienda", "*.jpg")))]
    return out


def _deformar(ruta: str, rnd: random.Random):
    """Simula una captura de pantalla o foto de celular de la prenda."""
    from PIL import Image, ImageEnhance, ImageOps

    im = Image.open(ruta).convert("RGB")
    w, h = im.size
    c = rnd.uniform(0.70, 0.9)
    x, y = rnd.uniform(0, 1 - c) * w, rnd.uniform(0, 1 - c) * h
    im = im.crop((x, y, x + c * w, y + c * h)).rotate(rnd.uniform(-12, 12), expand=True, fillcolor=(rnd.randint(150, 255),) * 3)
    im = ImageEnhance.Brightness(im).enhance(rnd.uniform(.75, 1.25))
    im = ImageEnhance.Color(im).enhance(rnd.uniform(.8, 1.2))
    im = ImageOps.expand(im.resize((360, int(360 * im.height / im.width))), border=rnd.randint(0, 60), fill=(rnd.randint(0, 80),) * 3)
    b = io.BytesIO()
    im.save(b, "JPEG", quality=55)
    b.seek(0)
    return Image.open(b).convert("RGB")


def indexar():
    emb = ImageEmbedder()
    fotos = fotos_catalogo()
    codigos = [c for c, _ in fotos]
    X = emb([p for _, p in fotos])

    # Calibración: cada foto deformada contra el catálogo. «mejor otra» es la similitud con la
    # prenda más parecida que NO es la suya: lo que veríamos con una foto de algo que no vendemos.
    rnd = random.Random(3)
    Q = emb([_deformar(p, rnd) for _, p in fotos])
    S = Q @ X.T
    idx = np.arange(len(fotos))
    cod = np.array(codigos)
    # Con varias vistas por prenda, acierta si la más parecida es CUALQUIER foto de la misma prenda,
    # y «mejor otra» solo cuenta prendas distintas.
    correcta = np.array([S[k][cod == cod[k]].max() for k in idx])
    otra = np.array([S[k][cod != cod[k]].max() for k in idx])
    top1 = float(np.mean(cod[S.argmax(1)] == cod))
    # «Es este»: por encima del 95 % de las «mejor otra» (≤5 % de falsos positivos en la calibración).
    t_alto = float(np.percentile(otra, 95))
    # «Se parece»: por encima de la mediana de «mejor otra».
    t_bajo = float(np.median(otra))
    metricas = {
        "modelo": emb.model_name, "fotos": len(fotos), "top1": round(top1, 4),
        "top3": round(float(np.mean([cod[k] in cod[np.argsort(-S[k])[:3]] for k in idx])), 4),
        "umbral_exacto": round(t_alto, 4), "umbral_parecido": round(t_bajo, 4),
        "recall_exacto": round(float(np.mean((cod[S.argmax(1)] == cod) & (correcta >= t_alto))), 4),
    }
    guardar("imagenes.pkl", {"codigos": codigos, "X": X, "metricas": metricas})
    print(metricas)


class Buscador:
    def __init__(self):
        d = cargar("imagenes.pkl")
        self.codigos, self.X, self.metricas = d["codigos"], d["X"], d["metricas"]
        self.emb = ImageEmbedder()
        self.emb([abrir(open(fotos_catalogo()[0][1], "rb").read())])  # primera inferencia, en el arranque

    def buscar(self, datos: bytes, k: int = 6) -> list[tuple[str, float]]:
        v = self.emb([abrir(datos)])[0]
        s = self.X @ v
        out, vistos = [], set()
        for i in np.argsort(-s):  # una entrada por prenda: la vista más parecida
            if self.codigos[i] not in vistos:
                vistos.add(self.codigos[i])
                out.append((self.codigos[i], float(s[i])))
                if len(out) >= k:
                    break
        return out

    def nivel(self, sim: float) -> str:
        if sim >= self.metricas["umbral_exacto"]:
            return "exacto"
        if sim >= self.metricas["umbral_parecido"]:
            return "parecido"
        return "ninguno"


if __name__ == "__main__":
    indexar()
