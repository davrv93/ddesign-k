"""Enrutador OPCIONAL entre catálogos: ¿a qué catálogo pertenece la frase?

Cada catálogo clasifica por su cuenta y NO sabe qué NO es suyo: «uy q caro» en `senales_compra` sale como `interes_gusto`, y
«muy largo» en `preguntas_producto` como `largo`. Comparar el score entre catálogos para elegir uno funciona mal (cada uno
tiene su propia escala y su umbral: medido, 51–95 % según el catálogo). Lo que sí funciona mejor es un kNN sobre la UNIÓN de
todos los ejemplos con la etiqueta «catálogo»; reutiliza las matrices ya calculadas por cada `Clasificador` (no embebe nada
más al cargar). Se usa solo para ELEGIR a qué catálogo preguntar; la intención sigue saliendo del clasificador del catálogo.

    todos = cargar_todos()                              # {nombre: Clasificador}
    enr = Enrutador(todos)
    enr.enrutar("uy q caro")                            # [("objeciones", 0.93), ("senales_compra", 0.31), ...]
    enr.clasificar("uy q caro")                         # {"catalogo": "objeciones", "resultado": {...}, "ranking": [...]}
"""
from __future__ import annotations

from collections import defaultdict
from typing import Callable, Optional, Sequence

import numpy as np

from .catalogos import listar_catalogos, preparar_texto
from .clasificador import Clasificador, _normalizar_filas, cargar_clasificador, cargar_embedder


def cargar_todos(embed: Optional[Callable] = None, directorio: Optional[str] = None, nombres: Optional[Sequence[str]] = None,
                 cache: Optional[str] = None) -> dict:
    """Carga todos los catálogos (o `nombres`) compartiendo un solo embedder."""
    embed = embed or cargar_embedder()
    return {n: cargar_clasificador(n, embed, directorio=directorio, cache=cache) for n in (nombres or listar_catalogos(directorio))}


class Enrutador:
    def __init__(self, clasificadores: dict, k: int = 5):
        self.clasificadores, self.k = clasificadores, k
        nombres = list(clasificadores)
        self.embed = clasificadores[nombres[0]].embed
        self.E = np.vstack([c.E for c in clasificadores.values()])
        self.etiqueta = np.array([n for n, c in clasificadores.items() for _ in range(len(c.E))])

    def enrutar_lote(self, textos: Sequence[str]) -> list:
        Q = _normalizar_filas(self.embed([preparar_texto(t) for t in textos]))
        S = Q @ self.E.T
        salida = []
        for s in S:
            idx = np.argsort(-s)[: self.k]
            suma = defaultdict(float)
            for j in idx:
                suma[str(self.etiqueta[j])] += float(s[j])
            total = sum(suma.values()) or 1.0
            salida.append(sorted(((n, round(v / total, 4)) for n, v in suma.items()), key=lambda p: -p[1]))
        return salida

    def enrutar(self, texto: str) -> list:
        """[(catálogo, peso)] de mayor a menor; el peso es la fracción de los k vecinos (ponderada por similitud)."""
        return self.enrutar_lote([texto])[0]

    def clasificar(self, texto: str) -> dict:
        ranking = self.enrutar(texto)
        cat = ranking[0][0]
        return {"catalogo": cat, "resultado": self.clasificadores[cat].clasificar(texto), "ranking": ranking}
