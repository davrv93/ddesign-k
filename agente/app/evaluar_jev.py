"""Mide Jev con las mismas pruebas que el clasificador local, y simula la cascada. Hace llamadas de pago
(unos US$ 0,002 en total) y no carga ningún modelo: se puede correr dentro del contenedor en marcha.

    python -m app.evaluar_jev                                  # Jev solo
    python -m app.evaluar_jev --local http://127.0.0.1:8000    # además: local y cascada (usa /clasificar)

Tres pruebas:
1. data/prueba_comercial.csv (68 mensajes sueltos, sin contexto): exactitud y calibración.
2. Los 23 casos de prueba_etapas.py, con su etapa y lo último que preguntó el bot: la intención la pone Jev
   (no la del caso) y la etapa la decide etapas.py. Mide «Jev + reglas» donde el contexto importa.
3. Verificación: párrafos fieles y párrafos que inventan algo del vestido V42.
"""
from __future__ import annotations

import argparse
import json
import statistics
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import datos, jev
from .etapas import decidir
from .prueba_etapas import CASOS

PRODUCTO_V42 = ("V42 Vestido Gala Capa Azul | vestido largo | azul | S/ 260 | tallas S, M, L | Vestido largo de gala con "
                "escote en V, corpiño bordado con pedrería, cintura marcada, falda con caída y mangas tipo capa. | material: "
                "base de tela podesuá, de estructura rígida; acabado en gasa, ligera y con buena caída | ideal para: "
                "matrimonio, boda, fiesta de noche, graduación, gala")
VERIFICACION = [  # (párrafo, ¿inventa?)
    ("Está confeccionado con una base de tela podesuá que le da estructura, y un acabado en gasa con muy buena caída.", False),
    ("El V42 está a *S/ 260.00* y lo tenemos en tallas S, M y L.", False),
    ("Tiene escote en V y mangas tipo capa, ideal para una boda de noche ✨", False),
    ("El envío a provincia cuesta S/ 20 por Olva o Shalom.", False),
    ("¿Para qué ocasión lo estás buscando?", False),
    ("Es perfecto para tu evento con su capa y detalles brillantes en lentejuelas ✨", True),
    ("Está hecho de seda natural, súper fresca para el verano.", True),
    ("Lo tenemos también en color rojo y en talla XL.", True),
    ("Tiene una abertura lateral en la pierna que estiliza mucho la figura.", True),
]


def _post(url: str, body: dict) -> dict:
    r = urllib.request.Request(url, json.dumps(body).encode(), {"content-type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=30))


def calibracion(conf: list[float], acierto: list[bool], cubos=(0.6, 0.8, 0.9, 1.01)) -> tuple[list[str], float]:
    """Exactitud por tramo de confianza y ECE (error de calibración esperado): 0 = «0,8» acierta 8 de cada 10."""
    filas, ece, lo = [], 0.0, 0.0
    for hi in cubos:
        idx = [k for k, c in enumerate(conf) if lo <= c < hi]
        if idx:
            ex = sum(acierto[k] for k in idx) / len(idx)
            cm = sum(conf[k] for k in idx) / len(idx)
            ece += len(idx) / len(conf) * abs(ex - cm)
            filas.append(f"[{lo:.2f}, {min(hi, 1):.2f}): n={len(idx):2d} confianza media {cm:.2f} → acierta {ex:.2f}")
        lo = hi
    return filas, round(ece, 3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", default="", help="URL del agente para comparar con el clasificador local y simular la cascada")
    a = ap.parse_args()
    if not jev.CLAVE:
        print("falta JEV_API_KEY u OPENROUTER_API_KEY")
        return 1

    # 1. Mensajes sueltos
    pk = datos.prueba_comercial()
    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(lambda t: jev.clasificar(jev.estado(t[0], [], "prospeccion", "")), pk))
    ok = [r is not None and r["intent"] == i for (_, i), r in zip(pk, res)]
    conf = [r["confianza"] if r else 0.0 for r in res]
    ms = [r["ms"] for r in res if r]
    costo = sum((r.get("costo") or 0) for r in res if r)
    print(f"Jev  prueba comercial (sin contexto): {sum(ok) / len(pk):.4f} ({sum(ok)}/{len(pk)}), "
          f"latencia p50 {statistics.median(ms)} ms, p95 {sorted(ms)[int(len(ms) * 0.95) - 1]} ms, costo US$ {costo:.5f}")
    for (t, i), r, b in zip(pk, res, ok):
        if not b:
            print(f"   ✗ {t} → {r['intent'] if r else 'sin respuesta'} {r['confianza'] if r else 0:.2f} (esperado {i})")
    filas, ece = calibracion(conf, ok)
    print(f"Jev  calibración: ECE {ece}")
    for f in filas:
        print("     ", f)

    if a.local:
        loc = [_post(a.local.rstrip("/") + "/clasificar", {"texto": t})["comercial"] for t, _ in pk]
        ok_l = [l["intent"] == i for (_, i), l in zip(pk, loc)]
        filas_l, ece_l = calibracion([l["confianza"] for l in loc], ok_l)
        print(f"local prueba comercial: {sum(ok_l) / len(pk):.4f} ({sum(ok_l)}/{len(pk)}), ECE {ece_l}")
        for f in filas_l:
            print("     ", f)
        # Cascada: el local decide si está seguro; si duda, Jev, cuando está más seguro que el local.
        casc, consultas = [], 0
        for (_, i), l, r in zip(pk, loc, res):
            usa = l["confianza"] < jev.UMBRAL and r is not None and r["confianza"] >= l["confianza"]
            consultas += l["confianza"] < jev.UMBRAL
            casc.append((r["intent"] if usa else l["intent"]) == i)
        print(f"cascada (umbral {jev.UMBRAL}): {sum(casc) / len(pk):.4f} ({sum(casc)}/{len(pk)}), "
              f"consulta a Jev en {consultas}/{len(pk)} mensajes")

    # 2. Con contexto: Jev pone la intención, etapas.py decide
    def caso(c):
        nombre, etapa, _, _, msg, bot, e_esp, i_esp = c
        hist = [{"rol": "bot", "texto": bot}] if bot else []
        return c, jev.clasificar(jev.estado(msg, hist, etapa, bot, "V42 Vestido Gala Capa Azul"))
    with ThreadPoolExecutor(6) as ex:
        res_c = list(ex.map(caso, CASOS))
    bien = 0
    for (nombre, etapa, _, _, msg, bot, e_esp, i_esp), r in res_c:
        if r is None:
            print(f"   ✗ {nombre}: sin respuesta")
            continue
        d = decidir(etapa, r["intent"], r["confianza"], msg, bot, primer_mensaje=nombre.startswith("primer mensaje"))
        b = d["etapa"] == e_esp and d["intent"] == i_esp
        bien += b
        if not b:
            print(f"   ✗ {nombre}: Jev {r['intent']} {r['confianza']:.2f} → {d['etapa']}/{d['intent']} (esperado {e_esp}/{i_esp})")
    print(f"Jev + etapas.py, casos con contexto: {bien}/{len(CASOS)}")

    # 3. Verificación de la respuesta
    pesos = jev.verificar([p for p, _ in VERIFICACION], PRODUCTO_V42) or []
    aciertos = 0
    for (p, inventa), w in zip(VERIFICACION, pesos):
        b = (w >= jev.UMBRAL_INVENTO) == inventa
        aciertos += b
        print(f"   {'✓' if b else '✗'} {w:.2f} {'INVENTA' if inventa else 'fiel   '} {p[:90]}")
    print(f"Jev  verificación: {aciertos}/{len(VERIFICACION)} párrafos bien juzgados (umbral {jev.UMBRAL_INVENTO})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
