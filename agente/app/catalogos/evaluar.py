"""Evaluación de catálogos: leave-one-out, prueba aparte, confusiones, curva cobertura/precisión, ruido y umbrales.

    python -m app.catalogos.evaluar                         # todos los catálogos, modelo real (e5)
    python -m app.catalogos.evaluar preguntas_producto      # uno
    python -m app.catalogos.evaluar --solapes               # verifica que la prueba aparte no toque el entrenamiento
    python -m app.catalogos.evaluar --calibrar --guardar    # calibra umbrales y escribe umbrales.json + informe
    python -m app.catalogos.evaluar --metodos               # compara knn / max / centroide / mixto

Sin `--guardar` no escribe nada en disco. Con el modelo real hace falta HF_HUB_OFFLINE=1 y la caché /models de la imagen.

Cómo se evita engañarse:
  * leave-one-out: cada ejemplo se clasifica SIN contarse a sí mismo. Es optimista (hay paráfrasis casi iguales en el
    entrenamiento): no es la cifra que importa;
  * la cifra que importa es la prueba aparte (`<catalogo>_prueba.yaml`), que no entra al entrenamiento;
  * los umbrales se calibran con la prueba aparte y por eso esa cobertura/precisión es «en muestra»; para estimar lo que pasaría
    con frases nuevas se reporta además una calibración cruzada de 2 mitades (se calibra con una mitad y se mide en la otra).
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
import time
from collections import Counter
from typing import Optional

import numpy as np

from .catalogos import (Catalogo, CatalogoInvalido, cargar_catalogo, cargar_catalogo_prueba, directorio_datos, listar_catalogos,
                        normalizar_clave, preparar_texto)
from .clasificador import (METODO_DEFECTO, METODOS, Clasificador, _normalizar_filas, cargar_embedder, cargar_negativos,
                           cargar_umbrales)

OBJETIVO_PRECISION = 0.95       # precisión mínima entre lo que el catálogo se atreve a responder
TOPE_FALSOS_POSITIVOS = 0.10    # tope de ruido «fuera de giro» que puede pasar el umbral


# --- ruido ---------------------------------------------------------------------------------------------------------
def cargar_ruido(directorio: Optional[str] = None) -> dict:
    import yaml
    ruta = os.path.join(directorio or directorio_datos(), "ruido_prueba.yaml")
    if not os.path.exists(ruta):
        return {}
    with open(ruta, encoding="utf-8") as fh:
        return {k: [str(x) for x in v] for k, v in (yaml.safe_load(fh) or {}).items()}


# --- solapes -------------------------------------------------------------------------------------------------------
def _jaccard(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def solapes(cat: Catalogo, prueba: dict, umbral_parecido: float = 0.85) -> tuple:
    """Frases de la prueba aparte que (a) son iguales al entrenamiento tras normalizar (error) o (b) casi iguales (aviso).
    Devuelve (iguales, parecidas) como listas de (frase_prueba, intención_prueba, frase_entrenamiento, intención, medida)."""
    train = [(t, i, normalizar_clave(t)) for t, i, _ in cat.ejemplos()]
    por_clave = {k: (t, i) for t, i, k in train}
    iguales, parecidas = [], []
    for intent, frases in prueba.items():
        for f in frases:
            k = normalizar_clave(f)
            if k in por_clave:
                iguales.append((f, intent, *por_clave[k], 1.0))
                continue
            mejor = max(train, key=lambda x: max(_jaccard(k, x[2]), difflib.SequenceMatcher(None, k, x[2]).ratio()))
            med = max(_jaccard(k, mejor[2]), difflib.SequenceMatcher(None, k, mejor[2]).ratio())
            if med >= umbral_parecido:
                parecidas.append((f, intent, mejor[0], mejor[1], round(med, 2)))
    return iguales, parecidas


# --- cálculo -------------------------------------------------------------------------------------------------------
def loo(clf: Clasificador, metodo: Optional[str] = None, k: Optional[int] = None):
    """Puntajes leave-one-out de todos los ejemplos de entrenamiento. → (P, y)"""
    P = clf.puntuar_matriz(clf.E, excluir=np.arange(len(clf.E)), metodo=metodo, k=k)
    return P, clf.y


def puntuar_frases(clf: Clasificador, frases: list, metodo: Optional[str] = None, k: Optional[int] = None) -> np.ndarray:
    Q = _normalizar_filas(clf.embed([preparar_texto(f) for f in frases]))
    return clf.puntuar_matriz(Q, metodo=metodo, k=k)


def puntuar_con_ninguno(clf: Clasificador, frases: list) -> tuple:
    """→ (P, s1_efectivo, margen): `s1_efectivo` es -inf donde gana la clase «ninguno» (fuera de giro), de modo que ningún
    umbral de score deja pasar esa frase."""
    Q = _normalizar_filas(clf.embed([preparar_texto(f) for f in frases]))
    P = clf.puntuar_matriz(Q)
    pred, s1, mg = score_margen(P)
    rej = clf.puntuar_negativo(Q) + clf.umbrales.get("ruido_delta", 0.0) >= s1
    return P, np.where(rej, -np.inf, s1), mg, rej


def top1_top3(P: np.ndarray, y: np.ndarray) -> tuple:
    orden = np.argsort(-P, axis=1)
    top1 = float(np.mean(orden[:, 0] == y))
    top3 = float(np.mean([y[i] in orden[i, :3] for i in range(len(y))]))
    return top1, top3


def score_margen(P: np.ndarray) -> tuple:
    orden = np.argsort(-P, axis=1)
    s1 = P[np.arange(len(P)), orden[:, 0]]
    s2 = P[np.arange(len(P)), orden[:, 1]] if P.shape[1] > 1 else np.zeros(len(P))
    return orden[:, 0], s1, s1 - s2


def confusiones(P: np.ndarray, y: np.ndarray, nombres: list, n: int = 10) -> list:
    pred = np.argmax(P, axis=1)
    c = Counter((nombres[a], nombres[b]) for a, b in zip(y, pred) if a != b)
    # par no ordenado: A→B y B→A son la misma confusión
    juntos: Counter = Counter()
    for (a, b), v in c.items():
        juntos[tuple(sorted((a, b)))] += v
    return [(a, b, v, c[(a, b)], c[(b, a)]) for (a, b), v in juntos.most_common(n)]


def curva(pred: np.ndarray, y: np.ndarray, s1: np.ndarray, margen: np.ndarray, umbrales: list, eje: str = "score",
          otro: float = 0.0) -> list:
    """Cobertura (qué fracción responde) y precisión (de lo respondido, cuánto acierta) variando un umbral."""
    filas = []
    for u in umbrales:
        resp = (s1 >= u) & (margen >= otro) if eje == "score" else (s1 >= otro) & (margen >= u)
        cob = float(resp.mean())
        prec = float((pred[resp] == y[resp]).mean()) if resp.any() else float("nan")
        filas.append((float(u), cob, prec))
    return filas


def calibrar(pred, y, s1, margen, s1_ruido, margen_ruido, objetivo=OBJETIVO_PRECISION, tope_fp=TOPE_FALSOS_POSITIVOS) -> dict:
    """Busca (score, margen) que MAXIMIZAN la cobertura en la prueba con precisión >= objetivo y falsos positivos de ruido
    <= tope. Si no hay ninguno, el de mayor precisión que cumpla el tope de ruido (y lo dice en `cumple`)."""
    mejores, alterno = None, None
    cand_s = np.unique(np.round(np.concatenate([s1, s1_ruido]), 4))
    cand_s = cand_s[np.isfinite(cand_s)]
    cand_s = np.concatenate([[cand_s.min() - 0.001], cand_s])
    cand_m = np.unique(np.round(np.concatenate([[0.0], margen, margen_ruido]), 4))
    for us in cand_s:
        for um in cand_m:
            resp = (s1 >= us) & (margen >= um)
            if not resp.any():
                continue
            cob = float(resp.mean())
            prec = float((pred[resp] == y[resp]).mean())
            fp = float(((s1_ruido >= us) & (margen_ruido >= um)).mean()) if len(s1_ruido) else 0.0
            if prec >= objetivo and fp <= tope_fp:
                clave = (cob, -fp, -us - um)
                if mejores is None or clave > mejores[0]:
                    mejores = (clave, us, um, cob, prec, fp)
            clave2 = (prec - fp * 0.5, cob)
            if fp <= tope_fp and (alterno is None or clave2 > alterno[0]):
                alterno = (clave2, us, um, cob, prec, fp)
    elegido, cumple = (mejores, True) if mejores else (alterno, False)
    if elegido is None:
        return {"score": 1.0, "margen": 1.0, "cobertura": 0.0, "precision": float("nan"), "falsos_positivos": 0.0, "cumple": False}
    _, us, um, cob, prec, fp = elegido
    return {"score": round(float(us), 4), "margen": round(float(um), 4), "cobertura": round(cob, 4), "precision": round(prec, 4),
            "falsos_positivos": round(fp, 4), "cumple": cumple}


def medir(pred, y, s1, margen, us, um) -> tuple:
    resp = (s1 >= us) & (margen >= um)
    cob = float(resp.mean())
    return cob, (float((pred[resp] == y[resp]).mean()) if resp.any() else float("nan"))


def calibracion_cruzada(pred, y, s1, margen, s1r, mr, semilla=7) -> dict:
    """2 mitades estratificadas por intención: calibra con una, mide en la otra (y al revés). Estima lo que pasa con frases
    que no se usaron para fijar los umbrales."""
    rng = np.random.default_rng(semilla)
    mitad = np.zeros(len(y), dtype=bool)
    for c in np.unique(y):
        idx = np.where(y == c)[0]
        rng.shuffle(idx)
        mitad[idx[: len(idx) // 2]] = True
    mr_mitad = rng.random(len(s1r)) < 0.5 if len(s1r) else np.zeros(0, dtype=bool)
    res = []
    for a in (True, False):
        A, B = mitad == a, mitad != a
        Ar, Br = (mr_mitad == a, mr_mitad != a) if len(s1r) else (np.zeros(0, bool), np.zeros(0, bool))
        u = calibrar(pred[A], y[A], s1[A], margen[A], s1r[Ar], mr[Ar])
        cob, prec = medir(pred[B], y[B], s1[B], margen[B], u["score"], u["margen"])
        fp = float(((s1r[Br] >= u["score"]) & (mr[Br] >= u["margen"])).mean()) if Br.any() else float("nan")
        res.append((cob, prec, fp))
    return {"cobertura": float(np.mean([r[0] for r in res])), "precision": float(np.nanmean([r[1] for r in res])),
            "falsos_positivos": float(np.nanmean([r[2] for r in res]))}


# --- informe por catálogo ------------------------------------------------------------------------------------------
def evaluar_catalogo(nombre: str, embed, directorio: Optional[str] = None, metodo: Optional[str] = None, calibrar_: bool = False,
                     verbose: bool = True, ruido: Optional[dict] = None, otros_prueba: Optional[dict] = None,
                     con_negativos: bool = True) -> dict:
    cat = cargar_catalogo(nombre, directorio)
    umb = cargar_umbrales(nombre, os.path.join(directorio, "umbrales.json") if directorio else None)
    clf = Clasificador(cat, embed, metodo=metodo or umb.get("metodo") or METODO_DEFECTO, umbrales=umb,
                       negativos=cargar_negativos(directorio) if con_negativos else None)
    inf: dict = {"catalogo": nombre, "intenciones": len(cat.intenciones), "ejemplos": cat.n_ejemplos, "metodo": clf.metodo,
                 "por_intencion": {n: len(i.ejemplos) for n, i in cat.intenciones.items()}}
    # (a) leave-one-out
    P, y = loo(clf)
    t1, t3 = top1_top3(P, y)
    inf["loo"] = {"top1": round(t1, 4), "top3": round(t3, 4), "n": len(y)}
    if "ampliado" in clf.origen:
        orig = np.array([o == "faq" for o in clf.origen])
        inf["loo"]["top1_originales_faq"] = round(float(np.mean(np.argmax(P[orig], 1) == y[orig])), 4)
        inf["loo"]["top1_ampliados"] = round(float(np.mean(np.argmax(P[~orig], 1) == y[~orig])), 4)
    inf["loo"]["confusiones"] = confusiones(P, y, clf.nombres)
    # (b) prueba aparte
    prueba = cargar_catalogo_prueba(nombre, directorio)
    if prueba:
        frases, yt = [], []
        idx = {n: j for j, n in enumerate(clf.nombres)}
        for intent, fs in prueba.items():
            for f in fs:
                frases.append(f)
                yt.append(idx[intent])
        yt = np.array(yt)
        Pt, s1, mg, rej_t = puntuar_con_ninguno(clf, frases)
        t1, t3 = top1_top3(Pt, yt)
        pred = np.argmax(Pt, axis=1)
        inf["prueba"] = {"n": len(frases), "top1": round(t1, 4), "top3": round(t3, 4),
                         "confusiones": confusiones(Pt, yt, clf.nombres),
                         "fallos": [(frases[i], clf.nombres[yt[i]], clf.nombres[pred[i]], round(float(Pt[i].max()), 3))
                                    for i in range(len(frases)) if pred[i] != yt[i]],
                         "tomadas_por_ninguno": int(rej_t.sum()),
                         "por_intencion": {n: round(float(np.mean(pred[yt == j] == j)), 2) for n, j in idx.items() if (yt == j).any()}}
        # (d) ruido
        ruido = ruido or {}
        grupos = {"fuera_de_giro": ruido.get("fuera_de_giro", []), "otros_catalogos_no_construidos": ruido.get("otros_catalogos_no_construidos", [])}
        if otros_prueba:  # frases de pruebas de OTROS catálogos: solapan por diseño, se reportan aparte
            grupos["frases_de_otros_catalogos_construidos"] = [f for c, fs in otros_prueba.items() if c != nombre for f in fs]
        pr = {}
        for g, fs in grupos.items():
            if fs:
                _, sr, mr, _ = puntuar_con_ninguno(clf, fs)
                pr[g] = (sr, mr)
        s1r, mr_ = pr.get("fuera_de_giro", (np.zeros(0), np.zeros(0)))
        # umbrales: curvas y calibración
        inf["curva_score"] = curva(pred, yt, s1, mg, list(np.round(np.arange(0.74, 0.97, 0.02), 2)), "score")
        inf["curva_margen"] = curva(pred, yt, s1, mg, [0.0, 0.005, 0.01, 0.015, 0.02, 0.03, 0.04, 0.05], "margen")
        cal = calibrar(pred, yt, s1, mg, s1r, mr_)
        inf["calibracion"] = cal
        inf["calibracion_cruzada"] = calibracion_cruzada(pred, yt, s1, mg, s1r, mr_)
        u_uso = cal if (calibrar_ or "calibrado_con" not in umb) else clf.umbrales   # sin umbrales guardados, se muestra lo calibrado
        cob, prec = medir(pred, yt, s1, mg, u_uso["score"], u_uso["margen"])
        inf["con_umbrales"] = {"score": u_uso["score"], "margen": u_uso["margen"], "cobertura": round(cob, 4), "precision": round(prec, 4)}
        inf["ruido"] = {}
        for g, (sr, mr) in pr.items():
            fp = float(((sr >= u_uso["score"]) & (mr >= u_uso["margen"])).mean())
            inf["ruido"][g] = {"n": int(len(sr)), "falsos_positivos": round(fp, 4),
                               "se_lleva_ninguno": round(float(np.mean(~np.isfinite(sr))), 4)}
        # abstenciones sobre la prueba (¿el umbral descarta lo que acertaría?)
        inf["prueba"]["abstiene"] = round(1 - cob, 4)
    if verbose:
        imprimir(inf)
    return inf


def imprimir(inf: dict) -> None:
    print(f"\n=== {inf['catalogo']}: {inf['intenciones']} intenciones, {inf['ejemplos']} ejemplos, método {inf['metodo']} ===")
    ep = inf["por_intencion"]
    print(f"  ejemplos por intención: min {min(ep.values())}, máx {max(ep.values())}")
    l = inf["loo"]
    extra = (f"  (faq originales {l['top1_originales_faq']:.1%}, ampliados {l['top1_ampliados']:.1%})" if "top1_originales_faq" in l else "")
    print(f"  (a) leave-one-out [optimista]: top-1 {l['top1']:.1%}  top-3 {l['top3']:.1%}  n={l['n']}{extra}")
    if "prueba" not in inf:
        print("  (b) sin prueba aparte")
        return
    p = inf["prueba"]
    print(f"  (b) PRUEBA APARTE: top-1 {p['top1']:.1%}  top-3 {p['top3']:.1%}  n={p['n']}")
    if p.get("tomadas_por_ninguno"):
        print(f"      frases de la prueba que la clase «ninguno» (fuera de giro) se llevó: {p['tomadas_por_ninguno']}")
    print("  (c) pares más confundidos en la prueba aparte:", "; ".join(f"{a}<->{b} ×{v}" for a, b, v, _, _ in p["confusiones"][:6]) or "ninguno")
    print("      pares más confundidos en leave-one-out:", "; ".join(f"{a}<->{b} ×{v}" for a, b, v, _, _ in l["confusiones"][:6]) or "ninguno")
    print("  curva cobertura/precisión (variando score, margen=0):")
    print("     " + "  ".join(f"{u:.2f}:{c:.0%}/{pr:.0%}" for u, c, pr in inf["curva_score"] if c > 0))
    print("  curva (variando margen, score=0):")
    print("     " + "  ".join(f"{u:.3f}:{c:.0%}/{pr:.0%}" for u, c, pr in inf["curva_margen"] if c > 0))
    c = inf["calibracion"]
    print(f"  calibración (precisión>={OBJETIVO_PRECISION:.0%}, ruido<={TOPE_FALSOS_POSITIVOS:.0%}): score>={c['score']} margen>={c['margen']} → cobertura {c['cobertura']:.1%}, "
          f"precisión {c['precision']:.1%}, FP ruido {c['falsos_positivos']:.1%}{'' if c['cumple'] else '  [NO cumple el objetivo]'}")
    cc = inf["calibracion_cruzada"]
    print(f"  calibración cruzada (2 mitades): cobertura {cc['cobertura']:.1%}, precisión {cc['precision']:.1%}, FP ruido {cc['falsos_positivos']:.1%}")
    u = inf["con_umbrales"]
    print(f"  con los umbrales en uso (score>={u['score']}, margen>={u['margen']}): cobertura {u['cobertura']:.1%}, precisión {u['precision']:.1%}")
    for g, r in inf["ruido"].items():
        print(f"  (d) ruido «{g}» (n={r['n']}): falsos positivos {r['falsos_positivos']:.1%} (la clase «ninguno» atrapa de entrada el {r['se_lleva_ninguno']:.0%} de este ruido)")
    if p["fallos"]:
        print(f"  fallos de la prueba aparte ({len(p['fallos'])}):")
        for f, esp, got, s in p["fallos"][:25]:
            print(f"     «{f}»: esperado {esp}, salió {got} ({s})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("catalogos", nargs="*", help="nombres (por defecto, todos)")
    ap.add_argument("--solapes", action="store_true", help="solo verifica que la prueba aparte no solape el entrenamiento")
    ap.add_argument("--metodos", action="store_true", help="compara los métodos de puntuación")
    ap.add_argument("--calibrar", action="store_true", help="usa los umbrales calibrados con la prueba aparte (no los de umbrales.json)")
    ap.add_argument("--guardar", action="store_true", help="escribe umbrales.json e informe_evaluacion.json en data/catalogos")
    ap.add_argument("--enrutador", action="store_true", help="mide el enrutador entre catálogos con las pruebas aparte")
    ap.add_argument("--metodo", choices=METODOS)
    ap.add_argument("--sin-negativos", action="store_true", help="sin la clase «ninguno» de ruido_entrenamiento.yaml")
    ap.add_argument("--embedder", choices=["e5", "setfit"], default="e5")
    ap.add_argument("--dir", help="directorio de catálogos (por defecto data/catalogos)")
    a = ap.parse_args(argv)
    d = a.dir or directorio_datos()
    nombres = a.catalogos or listar_catalogos(d)

    if a.solapes:
        malo = 0
        for n in nombres:
            cat, prueba = cargar_catalogo(n, d), cargar_catalogo_prueba(n, d)
            if prueba is None:
                print(f"{n}: sin prueba aparte")
                continue
            falta = set(cat.intenciones) - set(prueba)
            sobra = set(prueba) - set(cat.intenciones)
            iguales, parecidas = solapes(cat, prueba)
            minimo = min(len(v) for v in prueba.values())
            print(f"{n}: {sum(len(v) for v in prueba.values())} frases de prueba (mín {minimo} por intención); iguales al entrenamiento: "
                  f"{len(iguales)}; muy parecidas: {len(parecidas)}; intenciones sin prueba: {sorted(falta)}; de más: {sorted(sobra)}")
            for f, i, t, ti, m in iguales:
                print(f"   IGUAL  «{f}» ({i}) = «{t}» ({ti})")
            for f, i, t, ti, m in parecidas:
                print(f"   parecida {m}  «{f}» ({i}) ~ «{t}» ({ti})")
            malo += len(iguales) + len(falta) + len(sobra) + (minimo < 4)
        ruido = cargar_ruido(d)
        todo = [normalizar_clave(f) for fs in ruido.values() for f in fs]
        ent = {normalizar_clave(t) for n in nombres for t, _, _ in cargar_catalogo(n, d).ejemplos()}
        choques = [f for f in todo if f in ent]
        print(f"ruido de prueba: {len(todo)} frases; iguales a un ejemplo de entrenamiento: {len(choques)} {choques}")
        neg = {normalizar_clave(f) for f in cargar_negativos(d)}
        choques2 = [f for f in todo if f in neg]
        choques3 = sorted(neg & ent)
        print(f"ruido de entrenamiento: {len(neg)} frases; iguales al ruido de prueba: {len(choques2)} {choques2}; "
              f"iguales a un ejemplo de algún catálogo: {len(choques3)} {choques3}")
        return 1 if malo or choques or choques2 or choques3 else 0

    t0 = time.time()
    embed = cargar_embedder(a.embedder)
    print(f"embedder: {getattr(embed, 'model_name', '?')} ({time.time() - t0:.1f} s en cargar)")
    ruido = cargar_ruido(d)
    todas = {n: cargar_catalogo_prueba(n, d) for n in nombres}
    informes = {}
    if a.enrutador:
        from .enrutador import Enrutador, cargar_todos
        enr = Enrutador(cargar_todos(embed, d, nombres))
        ok, tot, conf = Counter(), Counter(), Counter()
        for n in nombres:
            fr = [f for fs in todas[n].values() for f in fs]
            for g in [r[0][0] for r in enr.enrutar_lote(fr)]:
                tot[n] += 1
                ok[n] += g == n
                conf[(n, g)] += g != n
        for n in nombres:
            print(f"   {n:26s} {ok[n] / tot[n]:.1%}  (n={tot[n]})")
        print(f"   global {sum(ok.values()) / sum(tot.values()):.1%}   confusiones: {[(a_, b_, v) for (a_, b_), v in conf.most_common(8) if v]}")
        return 0
    if a.metodos:
        for n in nombres:
            cat = cargar_catalogo(n, d)
            clf = Clasificador(cat, embed)
            prueba = todas[n]
            idx = {nm: j for j, nm in enumerate(clf.nombres)}
            fr = [f for i, fs in prueba.items() for f in fs]
            yt = np.array([idx[i] for i, fs in prueba.items() for _ in fs])
            print(f"\n{n}:")
            for m in METODOS:
                P, y = loo(clf, metodo=m)
                Pt = puntuar_frases(clf, fr, metodo=m)
                print(f"   {m:10s} LOO top-1 {top1_top3(P, y)[0]:.1%}   prueba top-1 {top1_top3(Pt, yt)[0]:.1%} top-3 {top1_top3(Pt, yt)[1]:.1%}")
        return 0
    for n in nombres:
        informes[n] = evaluar_catalogo(n, embed, d, a.metodo, a.calibrar, True, ruido, todas, not a.sin_negativos)
    if a.guardar:
        ruta_u = os.path.join(d, "umbrales.json")
        try:
            actuales = json.load(open(ruta_u, encoding="utf-8"))
        except (OSError, ValueError):
            actuales = {}
        for n, inf in informes.items():
            if "calibracion" in inf:
                c = inf["calibracion"]
                actuales[n] = {"score": c["score"], "margen": c["margen"], "metodo": inf["metodo"], "k": 3,
                               "cobertura_prueba": c["cobertura"], "precision_prueba": c["precision"],
                               "falsos_positivos_ruido": c["falsos_positivos"], "cumple_objetivo": c["cumple"],
                               "calibrado_con": f"{n}_prueba.yaml + ruido_prueba.yaml (fuera_de_giro)",
                               "embedder": getattr(embed, "model_name", "?")}
        with open(ruta_u, "w", encoding="utf-8") as fh:
            json.dump(actuales, fh, ensure_ascii=False, indent=2)
        with open(os.path.join(d, "informe_evaluacion.json"), "w", encoding="utf-8") as fh:
            json.dump(informes, fh, ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
        print(f"\nescrito: {ruta_u} e informe_evaluacion.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
