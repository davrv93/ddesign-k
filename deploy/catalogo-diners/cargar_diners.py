"""Paso 3/3 (en el servidor, junto a ~/kddesign/.env). Catálogo real de Baruka (baruka.dinersclubmall.pe, 03-10-2026) en el CRM: precios, tallas y stock de la
tienda. Borra los 54 productos sin precio del zip (V21…V74) y crea V21…V41."""
import json, os, re, time, uuid, urllib.request, urllib.error
API = "http://127.0.0.1:18480"
env = dict(l.split("=", 1) for l in open(os.path.expanduser("~/kddesign/.env")).read().splitlines() if "=" in l)

def pedir(metodo, ruta, cuerpo=None, token=None, foto=None):
    h, datos = {}, None
    if token: h["Authorization"] = f"Bearer {token}"
    if cuerpo is not None:
        h["Content-Type"] = "application/json"; datos = json.dumps(cuerpo).encode()
    if foto is not None:
        b = uuid.uuid4().hex
        h["Content-Type"] = f"multipart/form-data; boundary={b}"
        datos = (f'--{b}\r\nContent-Disposition: form-data; name="image"; filename="f.jpg"\r\nContent-Type: image/jpeg\r\n\r\n').encode() + foto + f"\r\n--{b}--\r\n".encode()
    r = urllib.request.Request(API + ruta, data=datos, headers=h, method=metodo)
    try:
        with urllib.request.urlopen(r, timeout=90) as x:
            t = x.read()
            return json.loads(t) if t else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{metodo} {ruta}: {e.code} {e.read()[:200]!r}")

token = pedir("POST", "/api/auth/login", {"user": env["ADMIN_USER"], "password": env["ADMIN_PASSWORD"]})["token"]
cat = json.load(open("catalogo.json"))
nuevos = {p["code"] for p in cat}
for p in pedir("GET", "/api/products", token=token):
    n = int(p["code"][1:]) if re.fullmatch(r"V\d+", p["code"]) else 0
    if 21 <= n <= 74:
        pedir("DELETE", f"/api/products/{p['id']}", token=token)
print("borrados V21…V74 del zip")

for p in cat:
    foto = open(p["fotos"][0], "rb").read()
    tags = ""
    for intento in range(3):
        try:
            tags = pedir("POST", "/api/products/describe", token=token, foto=foto).get("tags", ""); break
        except RuntimeError as e:
            print(p["code"], "describe reintenta:", e); time.sleep(15)
    cuerpo = {k: p[k] for k in ("code", "name", "category", "color", "price", "description", "variants")}
    cuerpo |= {"ai_tags": tags, "active": True}
    g = pedir("POST", "/api/products", cuerpo, token)
    pedir("POST", f"/api/products/{g['id']}/image", token=token, foto=foto)
    print(p["code"], p["name"], "S/", p["price"], "| tags:", tags[:60])
    time.sleep(4)
act = [p for p in pedir("GET", "/api/products", token=token) if p["active"]]
print("activos:", len(act), sorted(p["code"] for p in act))
