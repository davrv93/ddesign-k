"""Paso 10: capturas de las colecciones de baruka.pe → agente/imagenes/colecciones/ + agente/seed/colecciones.json.

Cuando la clienta pide el catálogo, el bot manda la captura de la colección que corresponde con su enlace, como lo hace la
tienda a mano (14-10-2026: «si te pide vestidos largos enviale el enlace y una captura; si pide enterizo, enterizos»).

Las colecciones salen de https://baruka.pe/collections.json; las palabras con que una clienta pide cada una van en ALIAS.
El nombre de cada captura lleva la fecha (coleccion_vestidos-20261014.jpg): el bot Go guarda las fotos que baja del agente
por nombre, así una captura nueva no choca con la vieja. Regenerar cuando cambie la web:

    python3 capturas_colecciones.py      (necesita playwright y Google Chrome)
"""
import datetime as dt
import glob
import json
import os
import urllib.request

from playwright.sync_api import sync_playwright

AQUI = os.path.dirname(os.path.abspath(__file__))
AGENTE = os.path.normpath(os.path.join(AQUI, "..", "..", "agente"))
DIR = os.path.join(AGENTE, "imagenes", "colecciones")
SEED = os.path.join(AGENTE, "seed", "colecciones.json")
WEB = "https://baruka.pe"
# Cómo la nombra la clienta (sin tildes, en minúsculas; agente/app/colecciones.py las busca como palabras enteras).
ALIAS = {
    "vestidos": ["vestidos largos", "vestido largo", "largos", "vestidos de fiesta", "vestido de fiesta", "vestidos de gala",
                 "vestido de gala", "vestidos de noche", "vestido de noche", "vestidos elegantes", "vestidos para boda"],
    "vestido-cortos": ["vestidos cortos", "vestido corto", "cortos", "vestidos de dia", "vestidos casuales", "minivestidos"],
    "enterizos": ["enterizos", "enterizo", "jumpsuit", "jumpsuits", "monos", "mono"],
    "blusas": ["blusas", "blusa", "tops", "top"],
    "pantalon": ["pantalones", "pantalon", "palazzo", "palazos"],
    "conjuntos": ["conjuntos", "conjunto", "dos piezas"],
    "sastre": ["conjuntos sastre", "conjunto sastre", "sastre", "sastres", "trajes sastre", "traje sastre", "ternos", "terno"],
    "blazer": ["blazer", "blazers", "sacos", "saco"],
}
TITULO = {"vestidos": "vestidos largos", "vestido-cortos": "vestidos cortos", "enterizos": "enterizos", "blusas": "blusas",
          "pantalon": "pantalones", "conjuntos": "conjuntos", "sastre": "conjuntos sastre", "blazer": "blazers", "all": "todos los modelos"}
OCULTAR = "[class*=whatsapp],[id*=whatsapp],[class*=Whatsapp],[class*=wa-],[id*=shopify-chat],iframe{display:none!important}"


def colecciones() -> list[str]:
    with urllib.request.urlopen(f"{WEB}/collections.json?limit=250", timeout=30) as r:
        datos = json.load(r)
    return [c["handle"] for c in datos["collections"] if c.get("products_count")]


def productos(h: str) -> list[str]:
    """Handles de los productos de la colección (los mismos que `web.handle` de agente/seed/fichas_producto.json)."""
    with urllib.request.urlopen(f"{WEB}/collections/{h}/products.json?limit=250", timeout=30) as r:
        return [x["handle"] for x in json.load(r).get("products", [])]


def main():
    os.makedirs(DIR, exist_ok=True)
    hoy = dt.date.today().strftime("%Y%m%d")
    handles = colecciones() + ["all"]
    seed = {"_nota": "Generado por deploy/catalogo-baruka-pe/capturas_colecciones.py. «all» = todo el catálogo.", "colecciones": {}}
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome")
        pg = b.new_page(viewport={"width": 1100, "height": 1500}, device_scale_factor=1)
        for h in handles:
            url = f"{WEB}/collections/{h}"
            pg.goto(url, wait_until="networkidle", timeout=90000)
            pg.add_style_tag(content=OCULTAR)
            pg.wait_for_timeout(800)
            titulo = pg.query_selector("h1")
            grilla = pg.query_selector("#product-grid") or pg.query_selector(".product-grid")
            if not grilla:
                print("sin grilla:", h)
                continue
            g = grilla.bounding_box()
            arriba = (titulo.bounding_box()["y"] - 24) if titulo else g["y"] - 80
            alto = min(g["y"] + min(g["height"], 980) - arriba, 1400)
            archivo = f"coleccion_{h}-{hoy}.jpg"
            pg.screenshot(path=os.path.join(DIR, archivo), clip={"x": 30, "y": arriba, "width": 1040, "height": alto},
                          type="jpeg", quality=82)
            for viejo in glob.glob(os.path.join(DIR, f"coleccion_{h}-*.jpg")):
                if not viejo.endswith(archivo):
                    os.remove(viejo)
            seed["colecciones"][h] = {"url": url, "titulo": TITULO.get(h, h), "captura": archivo, "alias": ALIAS.get(h, []),
                                      "productos": productos(h) if h != "all" else []}
            print(f"{h:15} {archivo}  ({int(alto)} px)")
        b.close()
    with open(SEED, "w", encoding="utf-8") as fh:
        json.dump(seed, fh, ensure_ascii=False, indent=1)
    print(len(seed["colecciones"]), "colecciones →", SEED)


if __name__ == "__main__":
    main()
