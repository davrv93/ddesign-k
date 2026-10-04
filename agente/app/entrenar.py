"""Entrena los clasificadores y arma el índice RAG. Se ejecuta al construir la imagen.

    python -m app.entrenar            # entrena, evalúa y guarda en index/

La evaluación es validación cruzada agrupada: las tres variantes (correcta, informal,
con errores ortográficos) de una misma frase caen siempre en el mismo pliegue, para que
el clasificador no se evalúe con la versión "mal escrita" de algo que ya vio bien escrito.
"""
from __future__ import annotations

import json
import time
from collections import Counter

import numpy as np

from . import datos
from .modelo import Embedder, entrenar_clasificador, guardar


def validacion_cruzada(X, y, grupos, pliegues=5) -> dict:
    from sklearn.metrics import classification_report
    from sklearn.model_selection import GroupKFold

    pred = np.empty(len(y), dtype=object)
    for tr, te in GroupKFold(n_splits=pliegues).split(X, y, grupos):
        clf = entrenar_clasificador(X[tr], [y[i] for i in tr])
        pred[te] = clf.predict(X[te])
    rep = classification_report(y, list(pred), output_dict=True, zero_division=0)
    return {"exactitud": round(rep["accuracy"], 4),
            "f1_macro": round(rep["macro avg"]["f1-score"], 4),
            "por_clase": {k: round(v["f1-score"], 3) for k, v in rep.items() if isinstance(v, dict) and k not in ("macro avg", "weighted avg")},
            "pred": list(pred)}


def prueba_chat(emb, clf, umbral: float = 0.35) -> dict:
    """Mensajes de chat reales (data/prueba_chat.csv) que NO entran al entrenamiento. La validación cruzada
    mide sobre frases de plantilla y daba 97 %; con mensajes como «tengo dudas» o «aceptan yape?» era 38 %."""
    import csv, os
    ruta = os.path.join(datos.DATA_DIR, "prueba_chat.csv")
    if not os.path.exists(ruta):
        return {}
    filas = list(csv.DictReader(open(ruta, encoding="utf-8")))
    P = clf.predict_proba(emb([r["mensaje"] for r in filas]))
    fallos = []
    for r, p in zip(filas, P):
        k = int(np.argmax(p))
        pred = str(clf.classes_[k]) if p[k] >= umbral else "otro"
        if pred != r["intencion"]:
            fallos.append(f"{r['mensaje']} → {pred} (esperado {r['intencion']})")
    res = {"exactitud": round(1 - len(fallos) / len(filas), 4), "n": len(filas), "fallos": fallos}
    print(f"intención  prueba con mensajes reales: {res['exactitud']} ({len(filas) - len(fallos)}/{len(filas)})")
    for x in fallos:
        print("   ✗", x)
    return res


def main():
    t0 = time.time()
    emb = Embedder()
    print(f"modelo de embeddings: {emb.model_name}")

    # --- intención -------------------------------------------------------
    ej = datos.ejemplos_intencion()
    Xi = emb([e.texto for e in ej])
    yi = [e.intencion for e in ej]
    gi = [e.grupo for e in ej]
    print("intenciones:", dict(Counter(yi)))
    cv_i = validacion_cruzada(Xi, yi, gi)
    por_variante = {}
    for v in ("correcta", "informal", "errores_ortograficos"):
        idx = [k for k, e in enumerate(ej) if e.variante == v]
        if idx:
            por_variante[v] = round(sum(cv_i["pred"][k] == yi[k] for k in idx) / len(idx), 4)
    print(f"intención  CV exactitud={cv_i['exactitud']} f1_macro={cv_i['f1_macro']} por variante={por_variante}")
    clf_i = entrenar_clasificador(Xi, yi)
    prueba = prueba_chat(emb, clf_i)

    # --- categoría de prenda ----------------------------------------------
    ec = datos.ejemplos_categoria()
    fichas100 = datos.fichas_catalogo100()
    textos_c = [t for t, _, _ in ec] + [f.texto() for f in fichas100]
    yc = [c for _, c, _ in ec] + [datos.categoria_por_nombre(f) for f in fichas100]
    gc = [f"J{k // 5}" for k in range(len(ec))] + [f.codigo for f in fichas100]
    Xc = emb(textos_c)
    cv_c = validacion_cruzada(Xc, yc, gc)
    por_prueba = {}
    for tp in sorted({tp for _, _, tp in ec}):
        idx = [k for k, (_, _, t) in enumerate(ec) if t == tp]
        por_prueba[tp] = round(sum(cv_c["pred"][k] == yc[k] for k in idx) / len(idx), 4)
    print(f"categoría  CV exactitud={cv_c['exactitud']} por tipo de prueba={por_prueba}")
    clf_c = entrenar_clasificador(Xc, yc)

    # --- índice RAG --------------------------------------------------------
    fichas = fichas100 + datos.fichas_seed()
    Xf = emb([f.texto() for f in fichas])

    guardar("intencion.pkl", clf_i)
    guardar("categoria.pkl", clf_c)
    guardar("ejemplos.pkl", {"ejemplos": ej, "X": Xi})
    guardar("fichas.pkl", {"fichas": fichas, "X": Xf})
    metricas = {
        "modelo_embeddings": emb.model_name,
        "n_ejemplos_intencion": len(ej),
        "intencion": {k: v for k, v in cv_i.items() if k != "pred"} | {"por_variante": por_variante, "prueba_chat": prueba},
        "categoria": {k: v for k, v in cv_c.items() if k != "pred"} | {"por_tipo_prueba": por_prueba},
        "n_fichas": len(fichas),
        "segundos": round(time.time() - t0, 1),
    }
    guardar("metricas.pkl", metricas)
    print(json.dumps(metricas, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
