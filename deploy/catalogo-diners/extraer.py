"""Paso 1/3. Lee el catálogo de baruka.dinersclubmall.pe y deja productos.json (precio, tallas, stock, fotos).
Uso: python3 extraer.py   (sin dependencias; ~1,5 s de pausa entre fichas)"""
import json, re, time, urllib.request, html as H
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36"}
dec = json.JSONDecoder()

def rsc_de(url):
    h = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read().decode("utf-8")
    partes = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', h, re.S)
    return "".join(json.loads('"' + p + '"') for p in partes)

def ficha(rsc, pid):
    i = rsc.find(f'"id":{pid},"name"')
    for start in sorted([m.start() for m in re.finditer(r'\{"', rsc[:i + 1])], reverse=True)[:600]:
        try:
            obj, end = dec.raw_decode(rsc, start)
        except ValueError:
            continue
        if end > i and "productVariationsTree" in obj:
            return obj
    raise RuntimeError(f"sin ficha {pid}")

def variantes(arbol, ruta=()):
    """Recorre Color → Talla → ProductVariation (en cualquier orden de claves)."""
    for eje, valores in arbol.items():
        for valor, sub in valores.items():
            if "ProductVariation" in sub:
                v = sub["ProductVariation"]
                yield dict(ruta + ((eje, valor),)), v
            else:
                yield from variantes(sub, ruta + ((eje, valor),))

def lista_catalogo():
    """Los productos de /catalogo vienen embebidos en el HTML (Next.js): {"products":[…],"totalCount":N}."""
    rsc = rsc_de("https://baruka.dinersclubmall.pe/catalogo")
    i = rsc.find('"totalCount"')
    for m in reversed(list(re.finditer(r'\{"products":\[', rsc[:i]))):
        obj, end = dec.raw_decode(rsc, m.start())
        if end > i:
            if obj.get("totalPages", 1) > 1:
                print("aviso: el catálogo tiene más de una página; solo se lee la primera")
            return obj["products"]
    raise RuntimeError("no encontré la lista de productos en /catalogo")


lista = lista_catalogo()
json.dump(lista, open("productos_lista.json", "w"), ensure_ascii=False, indent=1)
salida = []
for p in lista:
    url = f"https://baruka.dinersclubmall.pe/p/{p['seoUrl']}"
    rsc = rsc_de(url)
    f = ficha(rsc, p["id"])
    vs = []
    for ejes, v in variantes(f["productVariationsTree"]):
        vs.append({"color": ejes.get("Color", ""), "talla": ejes.get("Talla", ""), "stock": v.get("stock"),
                   "precio": v.get("specialPrice") or v.get("price"), "sku": v.get("sku")})
    fotos = []
    for arr in re.findall(r'"filesProductPage":(\[.*?\])', rsc):
        try:
            for x in json.loads(arr):
                if x["url"] not in fotos:
                    fotos.append(x["url"])
        except ValueError:
            pass
    thumb = p["uriThumbnailMedium"]
    fotos = [thumb] + [u for u in fotos if u != thumb]
    salida.append({"id": p["id"], "nombre": p["name"], "precio": p["specialPrice"] or p["price"],
                   "precio_lista": p["price"], "oferta": p["hasSpecialPrice"], "descripcion": f.get("description", ""),
                   "especificaciones": f.get("specifications", []), "variantes": vs, "fotos": fotos, "url": url})
    print(p["name"], "| S/", salida[-1]["precio"], "|", [(x["color"], x["talla"], x["stock"]) for x in vs], "| fotos", len(fotos))
    time.sleep(1.5)
json.dump(salida, open("productos.json", "w"), ensure_ascii=False, indent=1)
