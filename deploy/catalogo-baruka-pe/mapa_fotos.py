"""Paso 7: mapa de TODAS las fotos del índice de imagen y prueba una por una (pedido del usuario, 12-10-2026: «necesito
todas, crea una especie de mapeo… asegurarnos de que todas las fotos sean comparables y testea una por una»).

Corre DENTRO del contenedor del agente (usa su modelo de imagen, PIL y la clave del LLM):

    docker cp agente/imagenes/tienda kd_agente_v3:/tmp/mapa/tienda
    docker cp deploy/catalogo-baruka-pe/salida/catalogo_oficial.json kd_agente_v3:/tmp/mapa/
    docker cp deploy/catalogo-baruka-pe/mapa_fotos.py kd_agente_v3:/tmp/mapa/
    docker exec kd_agente_v3 python /tmp/mapa/mapa_fotos.py /tmp/mapa      # → /tmp/mapa/mapa_fotos.json

Por cada foto:
- huella de bits: dHash de 64 bits (la foto reducida a 9×8 en grises; cada bit dice si un píxel es más claro que el de su
  derecha). Dos fotos con menos de 7 bits distintos son la misma imagen aunque cambie el tamaño o la compresión: así se
  ven las DUPLICADAS, y si están en modelos distintos, el reconocimiento no puede separarlos.
- análisis con IA (el modelo de visión de V3): si es la prenda (puesta, maniquí, plano o detalle) o un banner con texto o
  una tabla, el tipo de prenda, el color principal y la vista. Lo que no es la prenda no sirve para comparar.
- prueba una por una con el modelo de imagen del agente:
    · captura: la foto deformada como captura de celular (recorte, giro, brillo, borde) contra todo el índice → ¿su modelo
      sale primero? Es la clienta que manda una captura de la web.
    · otra foto: sin dejarla verse a sí misma ni a sus duplicadas → ¿la foto más parecida es de su mismo modelo? Es la
      clienta que manda otra foto (otro color u otro ángulo) del mismo modelo.
"""
from __future__ import annotations

import base64
import concurrent.futures as cf
import io
import json
import os
import random
import re
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, "/app")
from app import imagen, vision           # noqa: E402  (contenedor del agente)
from app.v3 import cliente_llm           # noqa: E402

DIR = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mapa"
TIENDA = os.path.join(DIR, "tienda")
HILOS = 8


def dhash(ruta: str) -> int:
    im = Image.open(ruta).convert("L").resize((9, 8), Image.LANCZOS)
    px = np.asarray(im, dtype=np.int16)
    bits = (px[:, 1:] > px[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def distancia(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


PEDIDO_IA = ("Foto de la tienda de ropa de mujer Baruka Design. Responde SOLO JSON: "
             '{"es_prenda": true|false, "que_muestra": "prenda puesta|maniqui|prenda plana|detalle|texto o banner|tabla|otro", '
             '"prenda": "vestido|blusa|pantalon|enterizo|conjunto|blazer|otro", "color": "color principal de la prenda", '
             '"vista": "frente|espalda|lado|detalle|varias", "texto_encima": true|false}. es_prenda=false si la foto es sobre '
             "todo texto, una tabla o algo que no deja ver la prenda.")


def analizar(ruta: str) -> dict:
    img = base64.b64encode(vision._jpeg(open(ruta, "rb").read())).decode()
    cuerpo = {"model": vision.MODELO, "temperature": 0, "max_tokens": 1500, **cliente_llm.EXTRA,
              "messages": [{"role": "user", "content": [{"type": "text", "text": PEDIDO_IA},
                                                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}}]}]}
    for _ in range(3):
        try:
            r = vision._http.post(vision.URL, headers={"Authorization": f"Bearer {vision.KEY}"}, json=cuerpo, timeout=90)
            r.raise_for_status()
            txt = r.json()["choices"][0]["message"].get("content") or ""
            m = re.search(r"\{.*\}", txt, re.S)
            if m:
                return json.loads(m.group(0))
        except Exception as e:      # red o JSON roto: reintento
            err = str(e)[:120]
    return {"error": locals().get("err", "sin respuesta")}


def main():
    of = json.load(open(os.path.join(DIR, "catalogo_oficial.json"), encoding="utf-8"))
    vigentes = {p["code"]: p for p in of["productos"]}
    # archivo → (url, color) de la web
    web = {}
    for p in of["productos"]:
        base = p["code"].lower()
        for k, url in enumerate(p["fotos"], 1):
            nombre = (f"{base}.jpg" if k == 1 else f"{base}_{k}.jpg") if p["nuevo"] else f"{base}_w{k}.jpg"
            web[nombre] = (url, (p.get("color_de_foto") or {}).get(url.split("?")[0], ""))

    archivos = sorted(f for f in os.listdir(TIENDA) if f.endswith(".jpg"))
    filas = []
    for a in archivos:
        cod = a[:-4].split("_")[0].upper()
        origen = "web" if a in web else ("ejemplo" if cod not in vigentes and re.fullmatch(r"V(0\d|1\d|20)", cod) else
                                          "retirado" if cod not in vigentes else "diners")
        filas.append({"archivo": a, "codigo": cod, "modelo": vigentes.get(cod, {}).get("name", ""), "origen": origen,
                      "url": web.get(a, ("", ""))[0], "color_web": web.get(a, ("", ""))[1],
                      "dhash": f"{dhash(os.path.join(TIENDA, a)):016x}"})
    print(f"fotos: {len(filas)} ({sum(f['origen'] == 'web' for f in filas)} de la web)", flush=True)

    # Análisis con IA, en paralelo y con caché (si algo se cae, no se vuelve a pagar lo ya analizado).
    ruta_cache = os.path.join(DIR, "ia_cache.json")
    cache = json.load(open(ruta_cache)) if os.path.exists(ruta_cache) else {}
    faltan = [f for f in filas if f["dhash"] not in cache or "error" in cache[f["dhash"]]]
    with cf.ThreadPoolExecutor(HILOS) as ex:
        for f, res in zip(faltan, ex.map(lambda f: analizar(os.path.join(TIENDA, f["archivo"])), faltan)):
            cache[f["dhash"]] = res
    json.dump(cache, open(ruta_cache, "w"), ensure_ascii=False)
    for f in filas:
        f["ia"] = cache.get(f["dhash"], {"error": "sin análisis"})
    print(f"análisis IA listo ({len(faltan)} nuevos)", flush=True)

    # Duplicadas por huella de bits.
    hs = [int(f["dhash"], 16) for f in filas]
    for i, f in enumerate(filas):
        f["duplicada_de"] = [filas[j]["archivo"] for j in range(len(filas)) if j != i and distancia(hs[i], hs[j]) <= 6]
        f["duplicada_en_otro_modelo"] = sorted({filas[j]["codigo"] for j in range(len(filas))
                                                if j != i and distancia(hs[i], hs[j]) <= 6 and filas[j]["codigo"] != f["codigo"]})

    # Prueba una por una (solo modelos vigentes: es lo que el agente busca en producción).
    emb = imagen.ImageEmbedder()
    prueba = [i for i, f in enumerate(filas) if f["codigo"] in vigentes]
    rutas = [os.path.join(TIENDA, filas[i]["archivo"]) for i in prueba]
    # Por lotes: las 400 fotos (y sus capturas) de una vez se quedaban sin memoria y tumbaban el contenedor.
    def en_lotes(items, n=24):
        return np.vstack([emb(items[k:k + n]) for k in range(0, len(items), n)])
    X = en_lotes(rutas)
    rnd = random.Random(7)
    Q = np.vstack([emb([imagen._deformar(r, rnd) for r in rutas[k:k + 24]]) for k in range(0, len(rutas), 24)])
    cods = np.array([filas[i]["codigo"] for i in prueba])
    S_cap = Q @ X.T                                  # captura deformada contra todo el índice
    S_otra = X @ X.T                                 # la foto limpia contra las demás
    for n, i in enumerate(prueba):
        f = filas[i]
        orden = np.argsort(-S_cap[n])
        f["captura_top1"] = str(cods[orden[0]])
        f["captura_ok"] = f["captura_top1"] == f["codigo"]
        f["captura_sim"] = round(float(S_cap[n][orden[0]]), 4)
        excluir = {n} | {m for m, j in enumerate(prueba) if filas[j]["archivo"] in f["duplicada_de"]}
        otras = [m for m in np.argsort(-S_otra[n]) if m not in excluir]
        mismas = [m for m in range(len(prueba)) if cods[m] == f["codigo"] and m not in excluir]
        f["otra_top1"] = str(cods[otras[0]]) if otras else ""
        f["otra_ok"] = bool(mismas) and f["otra_top1"] == f["codigo"]
        f["otra_top3_ok"] = bool(mismas) and f["codigo"] in {str(cods[m]) for m in otras[:3]}
        f["sola"] = not mismas                       # única foto de su modelo: no hay «otra» con qué compararla

    # Duplicada REAL = misma huella de bits Y casi la misma imagen para el modelo (coseno ≥ 0,95): con fondo blanco de
    # estudio, dos fotos distintas pueden tener huellas cercanas. Las duplicadas reales entre modelos distintos son LOOKS
    # (la misma foto publicada en la blusa y en el pantalón que lleva puestos): la foto muestra las dos prendas.
    idx = {filas[j]["archivo"]: m for m, j in enumerate(prueba)}
    for n, i in enumerate(prueba):
        f = filas[i]
        reales = [a for a in f["duplicada_de"] if a in idx and float(S_otra[n][idx[a]]) >= 0.95]
        f["duplicada_real"] = reales
        f["look_con"] = sorted({a[:-4].split("_")[0].upper() for a in reales} - {f["codigo"]})

    # Visión sobre los fallos de «otra foto»: como en producción, ve la foto junto a los 5 candidatos del parecido
    # visual (sin dejarle ver la propia ni sus duplicadas) y decide.
    if "--sin-vision" not in sys.argv:
        nombres = {c: p["name"] for c, p in vigentes.items()}
        class _F:
            def __init__(s, c): s.codigo, s.nombre = c, nombres[c]
        fichas = [_F(c) for c in vigentes]
        fallidas = [n for n, i in enumerate(prueba) if not filas[i]["sola"] and not filas[i]["otra_ok"]]
        def probar(n):
            f = filas[prueba[n]]
            excluir = {n} | {idx[a] for a in f["duplicada_de"] if a in idx}
            cands, foto_de = [], {}
            for m in np.argsort(-S_otra[n]):
                if m in excluir or str(cods[m]) in cands:
                    continue
                cands.append(str(cods[m])); foto_de[str(cods[m])] = rutas[m]
                if len(cands) == 5:
                    break
            orig = vision._foto_de
            try:
                vision._foto_de = lambda c: open(foto_de[c], "rb").read() if c in foto_de else None
                res = vision.identificar(open(rutas[n], "rb").read(), fichas, "", cands)
            finally:
                vision._foto_de = orig
            return n, cands, res
        import threading
        candado = threading.Lock()
        with cf.ThreadPoolExecutor(4) as ex:     # _foto_de se parchea por hilo: se serializa la llamada
            def seguro(n):
                with candado:
                    return probar(n)
            for n, cands, res in ex.map(seguro, fallidas):
                f = filas[prueba[n]]
                f["vision_candidatos"] = cands
                f["vision"] = res
                f["vision_ok"] = bool(res and res.get("codigo") == f["codigo"] and res.get("confianza", 0) >= vision.CONFIANZA_MIN)
                f["vision_look_ok"] = bool(res and res.get("codigo") in f["look_con"])

    json.dump(filas, open(os.path.join(DIR, "mapa_fotos.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    vig = [filas[i] for i in prueba]
    no_prenda = [f for f in filas if f.get("ia", {}).get("es_prenda") is False]
    dup_otro = [f for f in vig if f["duplicada_en_otro_modelo"]]
    print(f"\nprueba una por una ({len(vig)} fotos de modelos vigentes):")
    print(f"  captura (foto deformada → su modelo primero): {sum(f['captura_ok'] for f in vig)}/{len(vig)}")
    comp = [f for f in vig if not f["sola"]]
    print(f"  otra foto del mismo modelo es la más parecida: {sum(f['otra_ok'] for f in comp)}/{len(comp)} "
          f"(top3: {sum(f['otra_top3_ok'] for f in comp)}/{len(comp)}); {len(vig) - len(comp)} son la única foto de su modelo")
    print(f"  no muestran la prenda según la IA: {len(no_prenda)}  ·  duplicadas en OTRO modelo: {len(dup_otro)}")
    print(f"  errores de la IA: {sum('error' in f.get('ia', {}) for f in filas)}")
    looks = [f for f in vig if f.get("look_con")]
    print(f"  fotos de LOOK (duplicada real en otro modelo): {len(looks)}")
    vf = [f for f in vig if "vision_ok" in f]
    if vf:
        print(f"  fallos de «otra foto» que la VISIÓN acierta: {sum(f['vision_ok'] for f in vf)}/{len(vf)}"
              f" (+{sum((not f['vision_ok']) and f['vision_look_ok'] for f in vf)} eligen la otra prenda del mismo look)")
        ok_total = sum(f["otra_ok"] for f in comp) + sum(f["vision_ok"] for f in vf)
        print(f"  ⇒ otra foto con parecido + visión: {ok_total}/{len(comp)}")


if __name__ == "__main__":
    main()
