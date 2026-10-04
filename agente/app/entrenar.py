"""Entrena los clasificadores y arma el índice RAG. Se ejecuta al construir la imagen.

    python -m app.entrenar            # entrena, evalúa y guarda en index/

La evaluación es validación cruzada agrupada: las tres variantes (correcta, informal,
con errores ortográficos) de una misma frase caen siempre en el mismo pliegue, para que
el clasificador no se evalúe con la versión "mal escrita" de algo que ya vio bien escrito.
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter

import numpy as np

from . import datos
from .modelo import Embedder, EmbedderOnnx, entrenar_clasificador, guardar, hay_setfit


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


def prueba_chat(emb, clf, umbral: float = 0.35, imprimir: bool = True) -> dict:
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
    if imprimir:
        print(f"intención  prueba con mensajes reales: {res['exactitud']} ({len(filas) - len(fallos)}/{len(filas)})")
        for x in fallos:
            print("   ✗", x)
    return res


def prueba_comercial(emb, clf) -> dict:
    """data/prueba_comercial.csv: 68 mensajes que no entran al entrenamiento."""
    pk = datos.prueba_comercial()
    P = clf.predict_proba(emb([t for t, _ in pk]))
    fallos = [f"{t} → {clf.classes_[int(np.argmax(p))]} {p.max():.2f} (esperado {i})"
              for (t, i), p in zip(pk, P) if str(clf.classes_[int(np.argmax(p))]) != i]
    return {"exactitud": round(1 - len(fallos) / len(pk), 4), "n": len(pk),
            "confianza_media": round(float(np.mean(P.max(axis=1))), 3), "fallos": fallos}


def cabezas(emb, ej, ec_) -> dict:
    """Intención del bot y comercial sobre un embedder: los dos clasificadores y sus pruebas."""
    clf_i = entrenar_clasificador(emb([e.texto for e in ej]), [e.intencion for e in ej])
    clf_k = entrenar_clasificador(emb([t for t, _ in ec_]), [i for _, i in ec_])
    return {"clf_i": clf_i, "clf_k": clf_k, "prueba": prueba_chat(emb, clf_i, imprimir=False),
            "comercial": prueba_comercial(emb, clf_k) | {"n_entrenamiento": len(ec_)}}


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
    # --- intención del bot y comercial: e5 sin ajustar frente a e5 ajustado con SetFit ----------------
    ec_ = datos.ejemplos_comercial()
    candidatos = {"base": cabezas(emb, ej, ec_)}
    if hay_setfit():
        candidatos["setfit"] = cabezas(EmbedderOnnx(), ej, ec_)
    for nombre, c in candidatos.items():
        print(f"comparación {nombre:6s}: intención {c['prueba']['exactitud']} ({c['prueba']['n'] - len(c['prueba']['fallos'])}/{c['prueba']['n']}), "
              f"comercial {c['comercial']['exactitud']} ({c['comercial']['n'] - len(c['comercial']['fallos'])}/{c['comercial']['n']}), "
              f"confianza media comercial {c['comercial']['confianza_media']}")
    # CLASIFICADOR=auto (por defecto): SetFit solo si no empeora ninguna de las dos pruebas. Ojo: elegir
    # mirando las pruebas las hace un poco menos independientes; por eso se publican las dos cifras.
    pedido = os.environ.get("CLASIFICADOR", "auto")
    gana = "setfit" in candidatos and all(candidatos["setfit"][k]["exactitud"] >= candidatos["base"][k]["exactitud"] for k in ("prueba", "comercial"))
    elegido = pedido if pedido in candidatos else ("setfit" if gana else "base")
    c = candidatos[elegido]
    clf_i, clf_k, prueba, comercial = c["clf_i"], c["clf_k"], c["prueba"], c["comercial"]
    print(f"clasificador elegido: {elegido} (CLASIFICADOR={pedido})")
    print(f"intención  prueba con mensajes reales: {prueba['exactitud']} ({prueba['n'] - len(prueba['fallos'])}/{prueba['n']})")
    for x in prueba["fallos"]:
        print("   ✗", x)
    print(f"comercial  prueba independiente: {comercial['exactitud']} ({comercial['n'] - len(comercial['fallos'])}/{comercial['n']}), "
          f"{len(ec_)} ejemplos de entrenamiento, confianza media {comercial['confianza_media']}")
    for x in comercial["fallos"]:
        print("   ✗", x)

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
    Xf = emb.pasajes([f.texto() for f in fichas])

    guardar("intencion.pkl", clf_i)
    guardar("categoria.pkl", clf_c)
    guardar("comercial.pkl", clf_k)
    guardar("ejemplos.pkl", {"ejemplos": ej, "X": Xi})
    guardar("fichas.pkl", {"fichas": fichas, "X": Xf})
    metricas = {
        "modelo_embeddings": emb.model_name,
        "n_ejemplos_intencion": len(ej),
        "intencion": {k: v for k, v in cv_i.items() if k != "pred"} | {"por_variante": por_variante, "prueba_chat": prueba},
        "categoria": {k: v for k, v in cv_c.items() if k != "pred"} | {"por_tipo_prueba": por_prueba},
        "comercial": comercial,
        "clasificador": elegido,
        "comparacion": {k: {"intencion": v["prueba"]["exactitud"], "comercial": v["comercial"]["exactitud"]} for k, v in candidatos.items()},
        "n_fichas": len(fichas),
        "segundos": round(time.time() - t0, 1),
    }
    guardar("metricas.pkl", metricas)
    print(json.dumps(metricas, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
