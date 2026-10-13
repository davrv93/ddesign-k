"""Paso 5: el catálogo oficial (salida/catalogo_oficial.json) en el agente.

- agente/seed/fichas_producto.json: las 96 fichas de baruka.pe; las de los modelos retirados se conservan (con
  `retirado`) porque hay conversaciones y pedidos que los nombran.
- agente/imagenes/tienda/: fotos de la web para el reconocimiento por imagen (imagen.indexar). Los nuevos llevan
  vNN.jpg, vNN_2.jpg…; los 9 que ya tenían fotos de Diners las conservan y suman vNN_w1.jpg… (otra vista, otros
  colores). Las fotos de los retirados pasan a imagenes/retirados/, que no se indexa: si no, una foto de la clienta
  podría reconocerse como un modelo que ya no se vende.

Las fotos se bajan a 800 px de la CDN de Shopify y se pasan a JPEG con `sips` (macOS), sin dependencias.

    python3 agente.py
"""
import glob
import json
import os
import shutil
import subprocess
import time
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
AGENTE = os.path.normpath(os.path.join(AQUI, "..", "..", "agente"))
TIENDA = os.path.join(AGENTE, "imagenes", "tienda")
RETIRADOS = os.path.join(AGENTE, "imagenes", "retirados")
CACHE = os.environ.get("CACHE_DIR") or os.path.join(os.environ.get("TMPDIR", "/tmp"), "catalogo-baruka-pe-cache", "fotos")
UA = "BarukaCatalogoBot/1.0"
MAX_COLORES = 8


def bajar(url: str, destino: str):
    os.makedirs(CACHE, exist_ok=True)
    crudo = os.path.join(CACHE, str(abs(hash(url.split("?")[0]))) + ".img")
    if not os.path.exists(crudo):
        sep = "&" if "?" in url else "?"
        req = urllib.request.Request(url + sep + "width=800", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r, open(crudo, "wb") as f:
            f.write(r.read())
        time.sleep(0.5)
    subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "80", "-Z", "800", crudo, "--out", destino],
                   check=True, capture_output=True)


def main():
    of = json.load(open(os.path.join(AQUI, "salida", "catalogo_oficial.json"), encoding="utf-8"))

    ruta = os.path.join(AGENTE, "seed", "fichas_producto.json")
    antes = json.load(open(ruta, encoding="utf-8"))
    web = [p["ficha"] for p in of["productos"]]
    cods = {f["codigo"] for f in web}
    # Los retirados conservan su ficha y llevan su nombre del CRM: si una clienta pide «el Irla» (de un anuncio viejo),
    # el agente sabe que ya no se vende y lo dice, en vez de mandar otra prenda parecida.
    nombres = {x["codigo"]: x["nombre_crm"] for x in json.load(open(os.path.join(AQUI, "salida", "mapeo.json")))["crm_a_web"]}
    # Solo código, nombre y la marca: ningún dato de Diners queda en las fichas (12-10-2026, «solo según baruka.pe»).
    retiradas = [{"codigo": c, "nombre": nombres.get(c, ""), "retirado": "no está en baruka.pe (12-10-2026)"}
                 for c in of["retirados"]]
    todas = sorted(web + retiradas, key=lambda f: int(f["codigo"][1:]))
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(todas, f, ensure_ascii=False, indent=1)
    print(f"fichas: {len(web)} de baruka.pe + {len(retiradas)} retiradas")

    os.makedirs(RETIRADOS, exist_ok=True)
    for cod in of["retirados"]:
        for p in glob.glob(os.path.join(TIENDA, f"{cod.lower()}.jpg")) + glob.glob(os.path.join(TIENDA, f"{cod.lower()}_*.jpg")):
            shutil.move(p, os.path.join(RETIRADOS, os.path.basename(p)))

    n = 0
    for p in of["productos"]:
        base = p["code"].lower()
        for k, url in enumerate(p["fotos"], 1):
            if p["nuevo"]:
                nombre = f"{base}.jpg" if k == 1 else f"{base}_{k}.jpg"
            else:
                nombre = f"{base}_w{k}.jpg"
            destino = os.path.join(TIENDA, nombre)
            if not os.path.exists(destino):
                bajar(url, destino)
                n += 1
        # Una foto por color (vNN_c1.jpg…), sin repetir las de arriba y con tope: solo alimentan el índice de imagen.
        ya = {u.split("?")[0] for u in p["fotos"]}
        extra = [u for u in (p.get("fotos_color") or {}).values() if u.split("?")[0] not in ya][:MAX_COLORES]
        for k, url in enumerate(extra, 1):
            destino = os.path.join(TIENDA, f"{base}_c{k}.jpg")
            if not os.path.exists(destino):
                bajar(url, destino)
                n += 1
    print(f"fotos nuevas: {n}; retiradas movidas a imagenes/retirados/")


if __name__ == "__main__":
    main()
