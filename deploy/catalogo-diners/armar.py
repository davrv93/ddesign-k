"""Paso 2/3. productos.json → catalogo.json: un producto por color (V21…) y sus fotos en fotos/.
Uso: mkdir -p fotos && python3 armar.py"""
import html, json, re, time, urllib.parse, urllib.request
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36"}
dec = json.JSONDecoder()

def bajar(url, ruta):
    u = urllib.parse.quote(url, safe=":/")
    data = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60).read()
    open(ruta, "wb").write(data)

def fotos_por_sku(url):
    h = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read().decode()
    rsc = "".join(json.loads('"' + x + '"') for x in re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', h, re.S))
    out = {}
    for m in re.finditer(r'\{"productId":\d+,"productGroup"', rsc):
        try:
            v, _ = dec.raw_decode(rsc, m.start())
        except ValueError:
            continue
        im = v.get("images")
        if isinstance(im, dict):
            fs = [im["uriThumbnailMedium"]] + [x["url"] for x in im.get("filesProductPage", [])]
            out[v["sku"]] = list(dict.fromkeys(fs))
    return out

COLOR = {"TURQUEZA": "turquesa"}
def limpio(nombre):
    n = re.sub(r"\s+", " ", nombre.replace("Baruka para Mujer", "")).strip()
    return n.replace("Pantalon", "Pantalón")

cat, k = [], 21
for p in json.load(open("productos.json")):
    colores = list(dict.fromkeys(v["color"] for v in p["variantes"]))
    fsku = fotos_por_sku(p["url"]) if len(colores) > 1 else {}
    for color in colores:
        vs = [v for v in p["variantes"] if v["color"] == color]
        fotos = fsku.get(vs[0]["sku"]) if fsku else p["fotos"]
        nombre = limpio(p["nombre"]) + (f" {COLOR.get(color, color.lower()).title()}" if len(colores) > 1 else "")
        cod = f"V{k}"; k += 1
        rutas = []
        for j, f in enumerate(fotos[:5]):
            r = f"fotos/{cod.lower()}{'' if j == 0 else f'_{j + 1}'}.jpg"
            bajar(f, r); rutas.append(r); time.sleep(0.4)
        specs = [s for s in dict.fromkeys(p["especificaciones"]) if s.lower() not in nombre.lower()]
        desc = html.unescape(p["descripcion"]).strip()   # Diners manda «coraz&oacute;n»
        if specs:
            desc += (" " if desc.endswith(".") else ". ") + "Detalles: " + ", ".join(specs) + "."
        cat.append({"code": cod, "name": nombre, "category": nombre.split()[0].replace("Pantalón", "Pantalón").lower(),
                    "color": COLOR.get(color, color.lower()), "price": float(p["precio"]), "description": desc,
                    "variants": [{"size": v["talla"], "stock": v["stock"]} for v in vs],
                    "fotos": rutas, "origen": p["url"]})
        print(cod, nombre, "| S/", p["precio"], "|", [(v["talla"], v["stock"]) for v in vs], "|", len(rutas), "fotos")
json.dump(cat, open("catalogo.json", "w"), ensure_ascii=False, indent=1)
