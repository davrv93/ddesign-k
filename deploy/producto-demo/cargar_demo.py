"""Crea o actualiza en el CRM el vestido de la demo comercial (agente/seed/producto_demo.json).

    python3 cargar_demo.py                         # en el servidor: API en 127.0.0.1:18480 y ~/kddesign/.env
    API=http://127.0.0.1:18499 ENV=/ruta/.env python3 cargar_demo.py     # contra un backend local

Idempotente: si el código ya existe, lo actualiza (precio, descripción, stock por talla) y vuelve a subir la foto.
"""
import json, os, sys, uuid, urllib.request, urllib.error

AQUI = os.path.dirname(os.path.abspath(__file__))
API = os.environ.get("API", "http://127.0.0.1:18480")
ENV = os.environ.get("ENV", os.path.expanduser("~/kddesign/.env"))
AGENTE = os.environ.get("AGENTE_DIR", os.path.join(AQUI, "..", "..", "agente"))
env = dict(l.split("=", 1) for l in open(ENV).read().splitlines() if "=" in l and not l.startswith("#"))


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
        with urllib.request.urlopen(r, timeout=60) as x:
            t = x.read()
            return json.loads(t) if t else {}
    except urllib.error.HTTPError as e:
        sys.exit(f"{metodo} {ruta}: {e.code} {e.read()[:200]!r}")


d = json.load(open(os.path.join(AGENTE, "seed", "producto_demo.json"), encoding="utf-8"))
token = pedir("POST", "/api/auth/login", {"user": env.get("ADMIN_USER", "admin"), "password": env["ADMIN_PASSWORD"]})["token"]
existente = next((p for p in pedir("GET", "/api/products", token=token) if p["code"].upper() == d["codigo"].upper()), None)
# El material va también en la descripción: así sale en el catálogo público y en el panel.
cuerpo = {"code": d["codigo"], "name": d["nombre"], "category": d["categoria"], "color": d["color"], "price": d["precio"],
          "description": d["descripcion"] + " Material: " + d["material"], "active": True,
          "ai_tags": "largo, gala, escote en V, mangas capa, pedrería, " + d["color"] + ", " + ", ".join(d.get("ocasion", [])),
          "variants": [{"size": t, "stock": n} for t, n in d["tallas"].items()]}
if existente:
    g = pedir("PUT", f"/api/products/{existente['id']}", cuerpo, token); pid = existente["id"]
else:
    g = pedir("POST", "/api/products", cuerpo, token); pid = g["id"]
foto = open(os.path.join(AGENTE, "imagenes", "tienda", d["imagenes"]["principal"]), "rb").read()
pedir("POST", f"/api/products/{pid}/image", token=token, foto=foto)
p = next(x for x in pedir("GET", "/api/products", token=token) if x["id"] == pid)
print(("actualizado" if existente else "creado"), p["code"], p["name"], "S/", p["price"], {v["size"]: v["stock"] for v in p["variants"]}, p["image"])
