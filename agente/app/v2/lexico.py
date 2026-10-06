"""Búsqueda léxica BM25F por campos para el catálogo (sin dependencias).

Cubre 2.2 y 2.3 del documento de mejoras: los códigos de prenda («V24»),
colores, tallas y nombres («Pandora») son términos exactos, justo donde el
léxico gana al vector. El vector (e5) sigue aportando, pero a peso bajo en
la fusión RRF: con pesos iguales el vector mete ruido.

Reglas (las que funcionaron en Metrín):

- Pesos por campo: nombre y código 3, categoría/color/ocasión/estilo 2,
  descripción 1. ``k1`` 1,2, ``b`` 0,75.
- Plegado: minúsculas y sin tildes («cómo registro» = «como registro»).
- Raíz ligera propia, NO Snowball: Snowball funde palabras que en moda
  significan cosas distintas («mini»/«midi» no se tocan; solo se quitan
  plural, género, «-mente», «-ación», gerundio, infinitivo y participio).
- Faltas de ortografía: expansión difusa del vocabulario en la consulta
  (distancia 1, términos de 4+ letras), no n-gramas (multiplicaban el
  índice por 5).
- Códigos y atajos («V24», «F7»): un solo término que no se recorta.

Lo que NO se hace (no funcionó): expansión con alias, bono por término
exacto, ni raíz Snowball.
"""
from __future__ import annotations

import math
import re
import threading
import unicodedata
from collections import Counter

# Pesos por campo y constantes BM25 (los medidos; ver docstring).
PESOS = {"nombre": 3.0, "codigo": 3.0, "categoria": 2.0, "color": 2.0,
         "ocasion": 2.0, "estilo": 2.0, "descripcion": 1.0}
K1 = 1.2
B = 0.75
# RRF: peso del vector en el orden final (bajo: el vector mete ruido) y en
# la lista diversa que va al reranker.
PESO_VECTOR_FINAL = 0.1
PESO_VECTOR_DIVERSA = 0.2
RRF_K = 60
# Reranker: margen en logits y umbral de aceptación (regla «no quita»).
MARGEN_LOGITS = 0.405
UMBRAL_ACEPTA = 0.5

CODIGO_RE = re.compile(r"^[a-z]*\d+[a-z]*$|^[a-z]\d\d$", re.I)
TOKEN_RE = re.compile(r"[a-z0-9ñ]+", re.I)


def plegar(t: str) -> str:
    """Minúsculas sin tildes."""
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def raiz(token: str) -> str:
    """Raíz ligera: quita UNA terminación frecuente. Códigos («v24») y
    palabras cortas no se tocan (ahí viven «mini»/«midi»)."""
    if CODIGO_RE.match(token) or len(token) <= 4:
        return token
    for suf in ("mente", "acion", "cion"):
        if token.endswith(suf) and len(token) - len(suf) >= 3:
            return token[: len(token) - len(suf)]
    for suf in ("ando", "iendo", "ado", "ido", "ada", "ida", "ar", "er", "ir"):
        if token.endswith(suf) and len(token) - len(suf) >= 3:
            return token[: len(token) - len(suf)]
    if token.endswith("es") and len(token) - 2 >= 4:
        return token[:-2]
    if token.endswith("s") and len(token) - 1 >= 4:
        return token[:-1]
    if token.endswith(("a", "o")) and len(token) - 1 >= 4:
        return token[:-1]
    return token


def tokenizar(t: str) -> list[str]:
    """Términos plegados con raíz. «Ctrl+F» da [ctrl, f]; «V24» queda intacto."""
    return [raiz(tok) for tok in TOKEN_RE.findall(plegar(t)) if tok]


def _dist1(a: str, b: str) -> bool:
    """Distancia de edición <= 1 (solo para términos de 4+ letras)."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    i = j = e = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
        elif e:
            return False
        else:
            e = 1
            if len(a) == len(b):
                i += 1
                j += 1
            elif len(a) > len(b):
                i += 1
            else:
                j += 1
    return e + (len(a) - i) + (len(b) - j) <= 1


class Indice:
    """BM25F en memoria. Solo biblioteca estándar (32 MiB bastan en Metrín)."""

    def __init__(self, pesos: dict | None = None):
        self.pesos = dict(pesos or PESOS)
        self._docs: dict[str, dict[str, Counter]] = {}
        self._len: dict[str, dict[str, int]] = {}
        self._avg: dict[str, float] = {}
        self._df: Counter = Counter()
        self._n = 0
        self._vocab: set[str] = set()
        self._lock = threading.Lock()

    def agregar(self, doc_id: str, campos: dict[str, str]) -> None:
        toks = {c: tokenizar(t) for c, t in (campos or {}).items() if t}
        with self._lock:
            self._docs[doc_id] = {c: Counter(ts) for c, ts in toks.items()}
            self._len[doc_id] = {c: len(ts) for c, ts in toks.items()}
            for t in {t for ts in toks.values() for t in ts}:
                self._df[t] += 1
                self._vocab.add(t)
            self._n = len(self._docs)
            for c in self.pesos:
                total = sum(l.get(c, 0) for l in self._len.values())
                self._avg[c] = total / self._n if self._n else 0.0

    def _idf(self, t: str) -> float:
        return math.log((self._n - self._df.get(t, 0) + 0.5) / (self._df.get(t, 0) + 0.5) + 1.0)

    def _difusos(self, q: str) -> list[tuple[str, float]]:
        """El término tal cual (1.0) más vecinos a distancia 1 (0.5)."""
        if q in self._vocab or len(q) < 4 or CODIGO_RE.match(q):
            return [(q, 1.0)]
        return [(q, 1.0)] + [(v, 0.5) for v in self._vocab if len(v) >= 4 and _dist1(q, v)][:3]

    def puntuar(self, consulta: str) -> dict[str, float]:
        qts = tokenizar(consulta)
        if not qts or not self._docs:
            return {}
        with self._lock:
            docs = dict(self._docs)
            lens = dict(self._len)
            avg = dict(self._avg)
        out: dict[str, float] = {}
        for q in qts:
            for v, w in self._difusos(q):
                idf = self._idf(v)
                if idf <= 0:
                    continue
                for doc, campos in docs.items():
                    s = 0.0
                    for c, tf in campos.items():
                        f = tf.get(v, 0)
                        if not f:
                            continue
                        den = f + K1 * (1 - B + B * (lens[doc].get(c, 0) / (avg.get(c) or 1.0)))
                        s += self.pesos.get(c, 1.0) * f / den
                    if s:
                        out[doc] = out.get(doc, 0.0) + w * idf * s
        return out

    def buscar(self, consulta: str, k: int = 20) -> list[tuple[str, float]]:
        ps = self.puntuar(consulta)
        return sorted(ps.items(), key=lambda x: -x[1])[:k]

    def cobertura(self, consulta: str, doc_id: str) -> float:
        """Fracción de términos de la consulta presentes en el documento
        (regla «no quita»: el léxico no pierde lo que ya acertó)."""
        qts = [t for t in tokenizar(consulta) if t]
        if not qts or doc_id not in self._docs:
            return 0.0
        vocab = {t for c in self._docs[doc_id].values() for t in c}
        ok = sum(1 for q in qts if q in vocab or any(_dist1(q, v) for v in vocab if len(v) >= 4))
        return ok / len(qts)


def rrf(ordenes: list[tuple[list[str], float]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Fusión RRF con peso por lista. Pesos iguales meten ruido del vector:
    usar PESO_VECTOR_FINAL (0.1) en el orden final."""
    pts: dict[str, float] = {}
    for ids, peso in ordenes:
        for r, doc in enumerate(ids):
            pts[doc] = pts.get(doc, 0.0) + peso / (k + r)
    return sorted(pts.items(), key=lambda x: -x[1])


def campos_de_ficha(f) -> dict[str, str]:
    """Ficha del catálogo → campos con peso (nombre/código 3, categoría 2,
    descripción 1)."""
    return {
        "nombre": f"{getattr(f, 'nombre', '')}",
        "codigo": f"{getattr(f, 'codigo', '')}",
        "categoria": f"{getattr(f, 'categoria', '')} {getattr(f, 'color', '')}",
        "descripcion": f"{getattr(f, 'detalle', '')} {getattr(f, 'tejido', '')}",
    }
