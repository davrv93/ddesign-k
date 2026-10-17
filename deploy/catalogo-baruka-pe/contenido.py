"""Contenido de seguimiento por modelo desde baruka.pe (octubre de 2026, requerimientos de Luis).

    python3 contenido.py                 # beneficios, desde salida/catalogo_baruka_pe.json y catalogo_oficial.json
    python3 contenido.py --testimonios   # además, las reseñas de cada producto (Trustoo, 1,2 s entre peticiones)

Deja `salida/contenido_seguimiento.json`, una lista para `POST /api/contenido/importar` de JMD Ventas:
[{"codigo": "V50", "clave": "beneficio", "texto": "…", "media_url": ""}, …]

- **beneficio**: la sección «Beneficios» de la descripción del producto, copiada tal cual (sin reescribir), en viñetas.
- **testimonio**: la reseña de una clienta real que la web muestra dentro del producto (app Trustoo; el usuario lo
  aclaró el 16-10-2026). Se lee de la misma API pública que usa la página (api.trustoo.io, get_product_reviews) y se
  elige UNA por modelo: 5 estrellas, con foto si hay, la más completa. Texto tal cual, con el nombre como lo publica la
  web («Gisella R.»), y la foto que subió la clienta.
- **tip**: no está en la web; lo carga Luis en el panel.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(AQUI, "salida")
RE_TITULO_BENEF = re.compile(r"(?i)^\W*beneficios\W*$")
# Lo que corta la sección: otro título con emoji o de los habituales de la tienda.
RE_FIN = re.compile(r"(?i)^\W*(¿para qu[eé] sirve\?|para qu[eé] sirve|ocasiones|ideal para|cuidados|caracter[ií]sticas|"
                    r"detalles|descripci[oó]n|medidas|consulta tu talla|composici[oó]n|tela)\b")
RE_VINETA = re.compile(r"^\s*(?:[-•▪●✔✓✅❤️♥️💖💕✨⭐🔸🔹➡️👉]|❤️?|\W)\s*", re.U)


def beneficios(descripcion: str) -> str:
    lineas = [l.strip() for l in (descripcion or "").splitlines()]
    out, dentro = [], False
    for l in lineas:
        if not l:
            continue
        if RE_TITULO_BENEF.match(l) or re.match(r"(?i)^\W*beneficios\s*:", l):
            dentro = True
            resto = re.sub(r"(?i)^\W*beneficios\s*:?\s*", "", l)
            if resto:
                out.append(resto)
            continue
        if dentro:
            limpio = RE_VINETA.sub("", l).strip().lstrip("\ufe0f\u200d\u2764 ").strip()
            if RE_FIN.match(limpio) or (len(limpio) < 40 and limpio.endswith("?")):
                break
            if limpio:
                out.append(limpio)
    out = [x for x in out if len(x) >= 8][:8]
    return "\n".join("• " + x.rstrip(".") for x in out)


TRUSTOO = "https://api.trustoo.io/api/v1/reviews/get_product_reviews"
SHOP_ID = "82065916194"   # el de baruka.pe (aparece en la página del producto)
UA = "BarukaCatalogoBot/1.0 (+contenido de seguimiento)"


def reseñas(product_id) -> list[dict]:
    q = urllib.parse.urlencode({"shop_id": SHOP_ID, "product_id": product_id, "page": 1, "limit": 20,
                                "sort_by": "commented-at-descending"})
    with urllib.request.urlopen(urllib.request.Request(f"{TRUSTOO}?{q}", headers={"User-Agent": UA}), timeout=30) as r:
        d = json.loads(r.read().decode("utf-8"))
    return ((d.get("data") or {}).get("list")) or []


TIPOS = ("vestido", "enterizo", "pantalon", "conjunto", "blazer", "falda", "blusa", "top", "palazzo", "short")


def _plano(t: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower()) if unicodedata.category(c) != "Mn")


def habla_de_otra_prenda(texto: str, nombre_producto: str) -> bool:
    """La reseña nombra otro tipo de prenda que el modelo (16-10-2026: la del Vestido Maura habla de «el enterizo que
    elegí»; la tienda la dejó en ese producto). Esas no se usan como testimonio de ESE modelo."""
    tipo = next((t for t in TIPOS if t in _plano(nombre_producto)), "")
    if not tipo:
        return False
    nombrados = {t for t in TIPOS if re.search(rf"\b{t}e?s?\b", _plano(texto))}
    return bool(nombrados) and tipo not in nombrados


def mejor_testimonio(lista: list[dict], nombre_producto: str = "") -> dict | None:
    buenas = [x for x in lista if (x.get("star") or 0) >= 5 and len((x.get("content") or "").strip()) >= 20
              and not habla_de_otra_prenda(x.get("content") or "", nombre_producto)]
    if not buenas:
        return None
    def foto(x):
        return next((r.get("src") for r in (x.get("resources") or []) if r.get("resource_type") == 1 and r.get("src")), "")
    buenas.sort(key=lambda x: (bool(foto(x)), min(len(x.get("content") or ""), 400)), reverse=True)
    x = buenas[0]
    texto = re.sub(r"\s+", " ", x["content"]).strip().strip('“”"«» ')
    if len(texto) > 450:
        texto = texto[:447].rsplit(" ", 1)[0] + "…"
    autor = (x.get("author") or "").strip()
    return {"texto": f"«{texto}»" + (f" — {autor} ⭐⭐⭐⭐⭐" if autor else ""), "media_url": foto(x)}


def main() -> int:
    cat = json.load(open(os.path.join(SALIDA, "catalogo_baruka_pe.json"), encoding="utf-8"))["productos"]
    ofi = json.load(open(os.path.join(SALIDA, "catalogo_oficial.json"), encoding="utf-8"))["productos"]
    codigo_de = {p["handle"]: p["code"] for p in ofi if p.get("handle") and p.get("code")}
    filas, sin = [], []
    for p in cat:
        cod = codigo_de.get(p.get("handle"))
        if not cod:
            continue
        b = beneficios(p.get("descripcion", ""))
        if b:
            filas.append({"codigo": cod, "clave": "beneficio", "texto": b, "media_url": ""})
        else:
            sin.append(cod)
    with open(os.path.join(SALIDA, "contenido_seguimiento.json"), "w", encoding="utf-8") as f:
        json.dump(filas, f, ensure_ascii=False, indent=1)
    print(f"beneficios: {len(filas)} modelos; sin sección «Beneficios» en la web: {len(sin)}")
    if "--testimonios" in sys.argv:
        n = 0
        for p in cat:
            cod = codigo_de.get(p.get("handle"))
            if not cod or not p.get("id"):
                continue
            time.sleep(1.2)
            try:
                t = mejor_testimonio(reseñas(p["id"]), p.get("nombre") or "")
            except Exception as e:  # noqa: BLE001
                print(f"  {cod}: {e}")
                continue
            if t:
                filas.append({"codigo": cod, "clave": "testimonio", **t})
                n += 1
        with open(os.path.join(SALIDA, "contenido_seguimiento.json"), "w", encoding="utf-8") as f:
            json.dump(filas, f, ensure_ascii=False, indent=1)
        print(f"testimonios: {n} modelos con una reseña de 5 estrellas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
