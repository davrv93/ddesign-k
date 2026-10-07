"""Comprensión entrenada: qué PIDE el mensaje, multi-etiqueta, sobre e5 congelado.

Por qué (medido el 07-10-2026 con `app/medir_comprension.py`, 418 frases que ninguna regla vio): las reglas solas
detectaban el 66 % de lo que pedía la clienta («ya pagué» 13 %, despedida 27 %, rebaja 35 %, catálogo 40 %). Las
reglas se escriben frase a frase; esto generaliza por el sentido.

Cómo: el mismo e5-small del agente («query: …», vector normalizado), estandarizado (los vectores de e5 vienen muy
concentrados) y una regresión logística por pedido; la estandarización se pliega en los pesos, así que en producción es
un producto escalar. Un mensaje puede pedir varias cosas (precio Y talla): cada pedido se decide por separado. El umbral
de cada uno sale de validación cruzada (5 partes): el que maximiza F0,5 con precisión ≥ 80 % (pesa más no inventar
pedidos que no perderse alguno; la regla sigue mirando).

    python3 -m app.comprension entrenar      # en el build: data/comprension_entrenamiento.jsonl → index/comprension.npz

Uso en el bot: `COMPRENSION=activo` (por defecto) suma lo detectado a lo que ven las reglas (O lógico); `sombra` solo lo
deja en la traza; `0` lo apaga. El conjunto de PRUEBA (data/comprension_prueba.jsonl) nunca entra al entrenamiento.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

ETIQUETAS = ("precio", "talla", "color", "envio", "ubicacion", "cita", "pago", "rebaja", "otras_opciones", "mas_barato",
             "catalogo", "elogio", "tela", "despedida", "ya_pago", "no_mostrar")
INDEX_DIR = os.environ.get("AGENTE_INDEX_DIR", os.path.join(os.path.dirname(__file__), "..", "index"))
DATA_DIR = os.environ.get("AGENTE_DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
RUTA = os.path.join(INDEX_DIR, "comprension.npz")
PRECISION_MIN = 0.80
# Las que el bot usa. Decidido con la repetición de las 613 frases de las 100 conversaciones (07-10): fuera las que,
# al equivocarse, bloquean o desvían el flujo (`no_mostrar` quitaba las fotos pedidas; `otras_opciones` y `catalogo`
# mandaban fotos y se perdía el precio; `ubicacion` metía el showroom en «quiero ver conjuntos») y las que las reglas ya
# resuelven (talla 93 %, envío 100 %, pago 87 %, ubicación 85 %). Precio y rebaja: el modelo se abstiene (umbral 0.99).
ACTIVAS = frozenset({"ya_pago", "despedida", "tela", "elogio", "cita", "color", "mas_barato"})
_SALUDO = __import__("re").compile(r"^\W*(hola|buen[oa]s?( (dias|tardes|noches))?|que tal|holi+)\b", __import__("re").I)


def modo() -> str:
    m = (os.environ.get("COMPRENSION") or "activo").strip().lower()
    return m if m in ("0", "sombra", "activo") else "activo"


_W: dict | None = None


def cargar() -> bool:
    global _W
    if _W is None and os.path.exists(RUTA):
        z = np.load(RUTA, allow_pickle=False)
        _W = {"etq": [str(x) for x in z["etiquetas"]], "W": z["W"], "b": z["b"], "thr": z["thr"]}
    return _W is not None


def probabilidades(vec: np.ndarray) -> dict[str, float]:
    """vec = el vector e5 del mensaje («query: …», normalizado). {} si no hay modelo."""
    if not cargar():
        return {}
    z = _W["W"] @ np.asarray(vec, dtype=np.float32) + _W["b"]
    p = 1.0 / (1.0 + np.exp(-z))
    return {e: float(x) for e, x in zip(_W["etq"], p)}


def detecta(vec: np.ndarray, texto: str = "", todas: bool = False) -> set[str]:
    """Lo que pide el mensaje según el modelo. Por defecto solo las `ACTIVAS` (las que usa el bot); `todas=True` para medir."""
    p = probabilidades(vec)
    if not p:
        return set()
    out = {e for i, e in enumerate(_W["etq"]) if p.get(e, 0.0) >= float(_W["thr"][i])}
    if not todas:
        out &= ACTIVAS
    # «Hola, buenas tardes» no es una despedida aunque se le parezca a «gracias, buenas tardes» (07-10).
    if "despedida" in out and _SALUDO.search(texto or "") and not __import__("re").search(r"gracias|chao|adios|hasta (luego|pronto|mañana)", (texto or "").lower()):
        out.discard("despedida")
    return out


def _umbral(prob: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """(umbral, recall, precisión): el que maximiza F0,5 con precisión ≥ PRECISION_MIN; si ninguno llega, 0.99 (no decide)."""
    mejor = (0.99, 0.0, 0.0, -1.0)
    for t in np.arange(0.30, 0.96, 0.05):
        pred = prob >= t
        if pred.sum() < 3:
            continue
        pre, rec = float(y[pred].mean()), float(pred[y == 1].mean())
        if pre < PRECISION_MIN or rec == 0:
            continue
        f = 1.25 * pre * rec / (0.25 * pre + rec)
        if f > mejor[3]:
            mejor = (float(round(t, 2)), rec, pre, f)
    return mejor[:3]


def entrenar(ruta_datos: str | None = None, salida: str = RUTA, semilla: int = 2026) -> dict:
    from sklearn.linear_model import LogisticRegression

    from .modelo import Embedder

    from sklearn.model_selection import StratifiedKFold, cross_val_predict

    filas = [json.loads(l) for l in open(ruta_datos or os.path.join(DATA_DIR, "comprension_entrenamiento.jsonl"), encoding="utf-8") if l.strip()]
    X = Embedder()([f["mensaje"] for f in filas])
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Z = (X - mu) / sd
    W, b, thr, informe = [], [], [], {}
    for e in ETIQUETAS:
        y = np.array([e in f.get("si", []) for f in filas], dtype=int)
        clf = LogisticRegression(C=0.5, class_weight="balanced", max_iter=4000)
        cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=semilla)
        p_oof = cross_val_predict(clf, Z, y, cv=cv, method="predict_proba")[:, 1]
        t, rec, pre = _umbral(p_oof, y)
        clf.fit(Z, y)                     # el modelo final ve todos los datos; el umbral sale de la validación cruzada
        w = clf.coef_[0] / sd             # estandarización plegada: w·x + b sobre el vector crudo
        W.append(w)
        b.append(clf.intercept_[0] - float((clf.coef_[0] * mu / sd).sum()))
        thr.append(t)
        informe[e] = {"positivos": int(y.sum()), "umbral": t, "val_recall": round(rec, 3), "val_precision": round(pre, 3)}
    os.makedirs(os.path.dirname(salida), exist_ok=True)
    np.savez(salida, etiquetas=np.array(ETIQUETAS), W=np.array(W, dtype=np.float32), b=np.array(b, dtype=np.float32),
             thr=np.array(thr, dtype=np.float32))
    return informe


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "entrenar":
        inf = entrenar()
        for e, m in inf.items():
            print(f"comprension {e:15} pos={m['positivos']:3} umbral={m['umbral']:.2f} val recall={m['val_recall']:.2f} precisión={m['val_precision']:.2f}")
        print(f"comprension modelo → {RUTA}")
    else:
        print(__doc__)
