"""Paso 4: baruka.pe como catálogo OFICIAL (pedido del usuario, 12-10-2026: «quiero que vaya todo lo de baruka.pe como
lo oficial, así que las fichas deben ser según esa web»).

Lee las salidas de extraer.py, mapear.py y fichas.py y deja UN archivo, salida/catalogo_oficial.json, del que salen:
  - el CRM de kddesign y la empresa «baruka» de jmdventas (cargar.py, por la API de cada uno),
  - las fichas técnicas del agente (agente/seed/fichas_producto.json),
  - las fotos para el reconocimiento por imagen (agente/imagenes/tienda/vNN.jpg).

Reglas:
  - Un producto por modelo de la web, con todos sus colores (la web tiene 96 modelos y 336 combinaciones de color).
  - Los 9 que ya estaban en el CRM conservan su código (V22, V26…, V42 = Vestido Alma) y su stock de S/M/L.
  - Los 87 nuevos reciben V45, V46… en el orden de la web. Los 15 que la web ya no vende se desactivan (no se borran:
    hay pedidos que los nombran).
  - Precio = el de la web (si cambia por variante, el menor). Tallas = las de la web; XS/XL/2XL «pre orden» se venden
    BAJO PEDIDO y así lo dice la ficha.
  - Stock: la web no lleva stock real (las 1.463 variantes dicen «disponible»). Los nuevos entran con STOCK_INICIAL por
    talla para que el bot pueda venderlos; la tienda lo corrige en el panel.

    python3 oficial.py            # escribe salida/catalogo_oficial.json
"""
import json
import os
import re

AQUI = os.path.dirname(os.path.abspath(__file__))
SAL = os.path.join(AQUI, "salida")
PRIMER_CODIGO = 45          # V21–V44 ya existen en el CRM (V43 y V44 también)
STOCK_INICIAL = 10
MAX_FOTOS = 3
CATEGORIA = {"vestido": "Vestido", "blusa": "Blusa", "enterizo": "Enterizo", "pantalón": "Pantalón",
             "conjunto": "Conjunto", "blazer": "Blazer"}
# Dos modelos de la web se llaman «Vestido Zoraida». El de 2025 (handle «zoraida», negro y clara) se distingue aquí
# para que «el Zoraida» no apunte a dos prendas a la vez.
NOMBRE_A_MANO = {"zoraida": "Vestido Zoraida Clásico"}
RE_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️‍]+")
RE_LINEA_VACIA = re.compile(r"^[¡¿]?(consulta tu talla.*|beneficios|caracter[ií]sticas|descripci[oó]n|detalles del "
                            r"producto|para qu[eé] sirve|.*:)[:!?¡¿.\s]*$", re.I)


def _cargar(nombre):
    with open(os.path.join(SAL, nombre), encoding="utf-8") as f:
        return json.load(f)


def nombre(p: dict) -> str:
    if p["handle"] in NOMBRE_A_MANO:
        return NOMBRE_A_MANO[p["handle"]]
    n = p["nombre"].strip()
    if n.isupper():                                   # «KERO», «LUZ»: la web no dice qué prenda es; lo dice su colección
        n = f"{CATEGORIA.get(p['categoria'], '')} {n.title()}".strip()
    n = re.sub(r"^Pantalon\b", "Pantalón", n)
    return " ".join(w[:1].upper() + w[1:] for w in n.split())


def descripcion(p: dict, colores: list[str], preorden: list[str]) -> str:
    lineas = []
    for l in (p.get("descripcion") or "").splitlines():
        l = RE_EMOJI.sub("", l).strip(" -•·\t")
        if l and not RE_LINEA_VACIA.match(l) and l not in lineas:
            lineas.append(l)
    txt = ". ".join(x.rstrip(".") for x in lineas[:8])
    extra = [f"Colores: {', '.join(colores)}"] if colores else []
    if preorden:
        extra.append(f"Tallas {', '.join(preorden)} bajo pedido")
    return ". ".join(x for x in [txt] + extra if x) + "."


def main():
    web = _cargar("catalogo_baruka_pe.json")
    web = web if isinstance(web, list) else web["productos"]
    mapeo = _cargar("mapeo.json")
    propuestas = {f["codigo"]: f for f in _cargar("fichas_producto_propuestas.json")}
    nuevas = {f["codigo"]: f for f in _cargar("fichas_nuevos_web.json")}

    codigo_de = {x["web"]["handle"]: x["codigo"] for x in mapeo["crm_a_web"] if x["estado"] == "mapeado"}
    retirados = sorted(x["codigo"] for x in mapeo["crm_a_web"] if x["estado"] != "mapeado")

    sig = PRIMER_CODIGO
    productos = []
    for p in web:
        cod = codigo_de.get(p["handle"])
        if not cod:
            cod, sig = f"V{sig}", sig + 1
        tallas = [t["talla"] for t in p["tallas"]]
        preorden = [t["talla"] for t in p["tallas"] if t.get("preorden")]
        colores = [c.strip().rstrip(".").capitalize() if c.isupper() else c.strip().rstrip(".") for c in p["colores"]]
        ficha = dict(propuestas.get(cod) or nuevas.get(f"WEB:{p['handle']}") or {})
        ficha["codigo"] = cod
        ficha["nombre"] = nombre(p)
        # Lo que la web ya responde deja de estar «pendiente de la tienda»: colores y tallas siempre; medidas si su
        # tabla es válida. (La ficha anterior decía «palo rosa según catálogo» del Holly, que la web vende en otros.)
        ficha["pendiente_tienda"] = [x for x in ficha.get("pendiente_tienda") or []
                                     if not re.search(r"colou?r|talla", x, re.I)
                                     and not (ficha.get("medidas") and re.search(r"medida", x, re.I))]
        ficha.setdefault("web", {})
        ficha["web"] = {**ficha["web"], "url": p["url"], "handle": p["handle"], "colores": colores,
                        "tallas": tallas, "bajo_pedido": preorden, "precio": p["precio"]}
        productos.append({
            "code": cod, "name": nombre(p), "category": p["categoria"],
            "color": ", ".join(colores).lower(), "colores": colores,
            "price": p["precio"], "price_max": p["precio_max"],
            "description": descripcion(p, colores, preorden),
            "sizes": tallas, "bajo_pedido": preorden,
            "variants": [{"size": t, "stock": STOCK_INICIAL} for t in tallas],
            "fotos": p["fotos"][:MAX_FOTOS], "url": p["url"], "handle": p["handle"],
            "nuevo": p["handle"] not in codigo_de, "ficha": ficha,
        })

    out = {"generado_desde": "baruka.pe", "stock_inicial_nuevos": STOCK_INICIAL,
           "productos": productos, "retirados": retirados}
    with open(os.path.join(SAL, "catalogo_oficial.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    nuevos = [x for x in productos if x["nuevo"]]
    print(f"{len(productos)} productos ({len(productos) - len(nuevos)} con código previo, {len(nuevos)} nuevos "
          f"{nuevos[0]['code']}–{nuevos[-1]['code']}); retirados: {', '.join(retirados)}")
    nombres = [x["name"] for x in productos]
    dup = sorted({n for n in nombres if nombres.count(n) > 1})
    if dup:
        print("OJO, nombres repetidos:", dup)


if __name__ == "__main__":
    main()
