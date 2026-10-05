"""Clasificador semántico de un catálogo: embeddings + kNN ponderado / centroides, con abstención.

Usa los embeddings que el agente ya usa (multilingual-e5-small, `app.modelo.Embedder`): los mensajes y los ejemplos
van como «query: » (comparación simétrica entre frases), normalizados, así que el producto punto es el coseno.

    clf = cargar_clasificador("preguntas_producto")          # embedder real, umbrales de umbrales.json
    clf.clasificar("se estira?")  ->
        {"intent": "elasticidad", "score": 0.93, "margen": 0.04, "alternativas": [("elasticidad", .93), ...]}

Se abstiene (`intent=None`) si el score o el margen quedan por debajo de los umbrales calibrados. Aun así devuelve
`mejor` (la mejor intención aunque no llegue) para registro y depuración.

La función de embedding es inyectable: cualquier `f(list[str]) -> np.ndarray (n, d)` normalizado. Las pruebas usan una
falsa (hash de n-gramas) y no necesitan el modelo.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Callable, Optional, Sequence

import numpy as np

from .catalogos import Catalogo, cargar_catalogo, directorio_datos, preparar_texto

# Umbrales por defecto si no hay umbrales.json (conservadores: abstenerse es barato, equivocarse no).
UMBRALES_DEFECTO = {"score": 0.80, "margen": 0.010}
METODOS = ("knn", "max", "centroide", "mixto", "hibrido")
METODO_DEFECTO = "hibrido"
K_DEFECTO = 3


def cargar_embedder(modo: str = "e5"):
    """El embedder real del agente. «e5» = Xenova/multilingual-e5-small (`modelo.Embedder`, fastembed/ONNX).
    «setfit» = el e5 ajustado para las intenciones del bot (`modelo.EmbedderOnnx`); NO es el que se recomienda aquí,
    porque se ajustó para otras intenciones."""
    from .. import modelo
    if modo == "setfit":
        return modelo.EmbedderOnnx()
    return modelo.Embedder()


def _normalizar_filas(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    return m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)


def cargar_negativos(directorio: Optional[str] = None) -> list:
    """Frases de `ruido_entrenamiento.yaml` (todos los grupos) o [] si no existe."""
    ruta = os.path.join(directorio or directorio_datos(), "ruido_entrenamiento.yaml")
    if not os.path.exists(ruta):
        return []
    import yaml
    with open(ruta, encoding="utf-8") as fh:
        datos = yaml.safe_load(fh) or {}
    return [str(x) for v in datos.values() for x in (v or [])]


def cargar_umbrales(catalogo: str, ruta: Optional[str] = None) -> dict:
    ruta = ruta or os.path.join(directorio_datos(), "umbrales.json")
    try:
        with open(ruta, encoding="utf-8") as fh:
            u = json.load(fh).get(catalogo)
    except (OSError, ValueError):
        u = None
    return {**UMBRALES_DEFECTO, **u} if u else dict(UMBRALES_DEFECTO)


class Clasificador:
    def __init__(self, catalogo: Catalogo, embed: Callable[[Sequence[str]], np.ndarray], *, metodo: str = METODO_DEFECTO,
                 k: int = K_DEFECTO, umbrales: Optional[dict] = None, cache: Optional[str] = None, matriz=None,
                 negativos: Optional[Sequence[str]] = None):
        if metodo not in METODOS:
            raise ValueError(f"método desconocido {metodo!r} (usa {METODOS})")
        self.catalogo, self.embed, self.metodo, self.k = catalogo, embed, metodo, k
        self.umbrales = dict(UMBRALES_DEFECTO, **(umbrales or {}))
        filas = list(catalogo.ejemplos())
        self.textos = [t for t, _, _ in filas]
        self.etiquetas = [i for _, i, _ in filas]
        self.origen = [o for _, _, o in filas]
        self.nombres = list(catalogo.intenciones)
        idx = {n: j for j, n in enumerate(self.nombres)}
        self.y = np.array([idx[e] for e in self.etiquetas])
        self.E = _normalizar_filas(matriz) if matriz is not None else self._embeber_ejemplos(cache)
        # Clase negativa «ninguno»: charla fuera de giro (ruido_entrenamiento.yaml). Si gana, el catálogo se abstiene.
        # Va aparte de las intenciones: no cuenta en leave-one-out ni en las alternativas.
        self.negativos = list(negativos or [])
        self.N = (_normalizar_filas(self.embed([preparar_texto(t) for t in self.negativos])) if self.negativos
                  else np.zeros((0, self.E.shape[1]), dtype=np.float32))
        self._centroides = self._calcular_centroides()

    # --- construcción -----------------------------------------------------------------------------------------
    def _embeber_ejemplos(self, cache: Optional[str]) -> np.ndarray:
        textos = [preparar_texto(t) for t in self.textos]
        if cache:
            sello = hashlib.sha1(("\n".join(textos) + "|" + getattr(self.embed, "model_name", "?")).encode()).hexdigest()[:16]
            ruta = os.path.join(cache, f"{self.catalogo.nombre}-{sello}.npy")
            if os.path.exists(ruta):
                return np.load(ruta)
        E = _normalizar_filas(self.embed(textos))
        if cache:
            try:
                os.makedirs(cache, exist_ok=True)
                np.save(ruta, E)
            except OSError:
                pass  # caché de solo lectura: se recalcula en el próximo arranque
        return E

    def _calcular_centroides(self) -> np.ndarray:
        C = np.zeros((len(self.nombres), self.E.shape[1]), dtype=np.float32)
        for j in range(len(self.nombres)):
            C[j] = self.E[self.y == j].sum(0)
        return C  # SIN normalizar: así se les puede restar un ejemplo en la validación leave-one-out

    # --- puntuación -------------------------------------------------------------------------------------------
    def puntuar_sims(self, S: np.ndarray, excluir: Optional[np.ndarray] = None, metodo: Optional[str] = None,
                     k: Optional[int] = None) -> np.ndarray:
        """S: (q, n_ejemplos) cosenos de cada consulta contra todos los ejemplos → (q, n_intenciones).
        `excluir[i]` = índice de ejemplo que NO cuenta para la consulta i (leave-one-out)."""
        metodo, k = metodo or self.metodo, k or self.k
        S = S.copy()
        if excluir is not None:
            S[np.arange(len(S)), excluir] = -np.inf
        out = np.full((S.shape[0], len(self.nombres)), -np.inf, dtype=np.float32)
        for j in range(len(self.nombres)):
            Sj = S[:, self.y == j]
            if metodo in ("knn", "max", "mixto"):
                orden = -np.sort(-Sj, axis=1)
                kk = max(1, min(k, Sj.shape[1]))
                top = orden[:, :kk]
                top = np.where(np.isfinite(top), top, np.nan)
                knn = np.nanmean(top, axis=1)
                mx = orden[:, 0]
            if metodo == "knn":
                out[:, j] = knn
            elif metodo == "max":
                out[:, j] = mx
            elif metodo == "mixto":
                out[:, j] = 0.5 * mx + 0.5 * knn
        if metodo in ("centroide", "hibrido"):
            raise RuntimeError("centroide e hibrido se puntúan con puntuar_matriz (necesitan las consultas, no solo S)")
        return out

    def puntuar_matriz(self, Q: np.ndarray, excluir: Optional[np.ndarray] = None, metodo: Optional[str] = None,
                       k: Optional[int] = None) -> np.ndarray:
        """Q: (q, d) consultas normalizadas → (q, n_intenciones). Con `excluir` (índices de ejemplos), esos ejemplos no
        cuentan: así se hace el leave-one-out sin recalcular nada."""
        metodo = metodo or self.metodo
        if metodo == "hibrido":   # mitad kNN (top-k), mitad centroide: lo local y lo global de cada intención
            return 0.5 * self.puntuar_matriz(Q, excluir, "knn", k) + 0.5 * self.puntuar_matriz(Q, excluir, "centroide", k)
        if metodo != "centroide":
            return self.puntuar_sims(Q @ self.E.T, excluir, metodo, k)
        out = np.zeros((len(Q), len(self.nombres)), dtype=np.float32)
        suma = self._centroides
        for i, q in enumerate(Q):
            C = suma.copy()
            if excluir is not None:
                C[self.y[excluir[i]]] -= self.E[excluir[i]]
            C = C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-9)
            out[i] = C @ q
        return out

    def puntuar_negativo(self, Q: np.ndarray, metodo: Optional[str] = None, k: Optional[int] = None) -> np.ndarray:
        """Puntaje de la clase «ninguno» para cada consulta, con la misma regla que las intenciones (→ (q,))."""
        if not len(self.N):
            return np.full(len(Q), -np.inf, dtype=np.float32)
        metodo, k = metodo or self.metodo, k or self.k
        S = Q @ self.N.T
        kk = max(1, min(k, S.shape[1]))
        orden = -np.sort(-S, axis=1)
        mx, knn = orden[:, 0], orden[:, :kk].mean(1)
        cen = self.N.sum(0)
        cen = cen / (np.linalg.norm(cen) + 1e-9)
        c = Q @ cen
        return {"max": mx, "knn": knn, "mixto": 0.5 * mx + 0.5 * knn, "centroide": c, "hibrido": 0.5 * knn + 0.5 * c}[metodo]

    # --- API --------------------------------------------------------------------------------------------------
    def decidir(self, puntajes: np.ndarray, umbrales: Optional[dict] = None, ninguno: float = -np.inf) -> dict:
        """Un renglón de puntajes → resultado con abstención. `ninguno` = puntaje de la clase negativa (fuera de giro)."""
        u = umbrales or self.umbrales
        orden = np.argsort(-puntajes)
        best = int(orden[0])
        s1 = float(puntajes[best])
        s2 = float(puntajes[orden[1]]) if len(orden) > 1 else -1.0
        margen = s1 - s2 if len(orden) > 1 else s1
        alt = [(self.nombres[int(j)], round(float(puntajes[j]), 4)) for j in orden[:3]]
        ruido = ninguno >= s1
        seguro = s1 >= u["score"] and margen >= u["margen"] and not ruido
        it = self.catalogo.intenciones[self.nombres[best]]
        res = {"intent": self.nombres[best] if seguro else None, "score": round(s1, 4), "margen": round(margen, 4),
               "alternativas": alt, "mejor": self.nombres[best], "catalogo": self.catalogo.nombre}
        if ruido:
            res["motivo_abstencion"] = "fuera_de_giro"
        elif not seguro:
            res["motivo_abstencion"] = "score_bajo" if s1 < u["score"] else "margen_bajo"
        if seguro:
            res["accion"] = it.accion
            if it.stage is not None:
                res["stage"] = it.stage
            if it.strength is not None:
                res["strength"] = it.strength
            res.update(it.extra)
        return res

    def clasificar(self, texto: str) -> dict:
        return self.clasificar_lote([texto])[0]

    def clasificar_lote(self, textos: Sequence[str]) -> list:
        if not textos:
            return []
        Q = _normalizar_filas(self.embed([preparar_texto(t) for t in textos]))
        P = self.puntuar_matriz(Q)
        Nn = self.puntuar_negativo(Q)
        return [self.decidir(P[i], ninguno=float(Nn[i])) for i in range(len(textos))]


def cargar_clasificador(nombre: str, embed: Optional[Callable] = None, *, directorio: Optional[str] = None,
                        metodo: Optional[str] = None, cache: Optional[str] = None) -> Clasificador:
    """Carga el catálogo `nombre`, sus umbrales y el embedder (el real si no se inyecta uno)."""
    cat = cargar_catalogo(nombre, directorio)
    cache = cache or os.environ.get("CATALOGOS_CACHE_DIR") or None
    if embed is None:
        embed = cargar_embedder()
    umb = cargar_umbrales(nombre, os.path.join(directorio, "umbrales.json") if directorio else None)
    metodo = metodo or umb.get("metodo") or METODO_DEFECTO
    neg = cargar_negativos(directorio)
    k = int(umb.get("k", K_DEFECTO)) if isinstance(umb.get("k", None), (int, float)) else K_DEFECTO
    return Clasificador(cat, embed, metodo=metodo, k=k, umbrales=umb, cache=cache, negativos=neg)
