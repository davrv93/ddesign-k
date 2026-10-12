"""Paso 6 (en el servidor): el catálogo oficial de baruka.pe en el CRM, por la API. Sirve para los dos CRM que venden
como Baruka: kddesign (/baruka/, SQLite) y la empresa «baruka» de jmdventas (/jmdventas/baruka/, MariaDB). Los dos
deben tener los MISMOS códigos: el agente es uno solo y recomienda por código.

    python3 cargar.py --destino kddesign  [--aplicar]
    python3 cargar.py --destino jmdventas [--aplicar]

Sin --aplicar solo dice qué haría. Junto al script: catalogo_oficial.json y la carpeta fotos/ (las vNN.jpg y vNN_w1.jpg
de agente/imagenes/tienda).

- Modelos de la web que ya estaban (V22, V26…, V42): nombre, precio, colores, descripción y tallas de la web. El stock
  de las tallas que ya tenían NO se toca (es el contado de la tienda); las tallas nuevas entran con el stock inicial.
  Foto: la de la web, salvo V42, cuya foto es la del anuncio que mandó la tienda.
- Nuevos (V45–V131): se crean con su foto y el stock inicial por talla.
- Los que la web ya no vende: active=false. No se borran (hay pedidos que los nombran).

Credenciales: kddesign entra con ADMIN_USER/ADMIN_PASSWORD de ~/kddesign/.env. jmdventas no tiene un usuario de
servicio: el script firma un token de la empresa con JWT_SECRET de ~/jmdventas/deploy/jmdventas/.env (mismo formato que
internal/auth: base64url(json).base64url(hmac-sha256)), válido 30 minutos. Nada de eso se imprime.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
import uuid

AQUI = os.path.dirname(os.path.abspath(__file__))
DESTINOS = {
    "kddesign": {"api": "http://127.0.0.1:18480", "env": "~/kddesign/.env"},
    # jmdventas no publica puerto: la IP del contenedor en su red (la saca `podman inspect`, ver --api).
    # color pasó de VARCHAR(100) a VARCHAR(255) el 12-10-2026 (la lista de la web llega a 113 caracteres). `max_color`
    # queda como red: con una base vieja recorta por colores enteros en vez de dar 500.
    "jmdventas": {"api": "", "env": "~/jmdventas/deploy/jmdventas/.env", "empresa": "baruka", "tenant": 1,
                  "max_color": 255},
}
FOTO_PROPIA = {"V42"}   # su foto es la del anuncio (la mandó la tienda), no la de la web


def leer_env(ruta):
    out = {}
    for l in open(os.path.expanduser(ruta), encoding="utf-8").read().splitlines():
        if "=" in l and not l.lstrip().startswith("#"):
            k, v = l.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def token_firmado(secreto, usuario, tenant, ttl=1800):
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    p = b64(json.dumps({"u": usuario, "t": tenant, "exp": int(time.time()) + ttl}, separators=(",", ":")).encode())
    return p + "." + b64(hmac.new(secreto.encode(), p.encode(), hashlib.sha256).digest())


class API:
    def __init__(self, base, token=None):
        self.base, self.token = base.rstrip("/"), token

    def __call__(self, metodo, ruta, cuerpo=None, foto=None):
        h, datos = {}, None
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if cuerpo is not None:
            h["Content-Type"] = "application/json"
            datos = json.dumps(cuerpo).encode()
        if foto is not None:
            b = uuid.uuid4().hex
            h["Content-Type"] = f"multipart/form-data; boundary={b}"
            datos = (f'--{b}\r\nContent-Disposition: form-data; name="image"; filename="f.jpg"\r\n'
                     'Content-Type: image/jpeg\r\n\r\n').encode() + foto + f"\r\n--{b}--\r\n".encode()
        req = urllib.request.Request(self.base + ruta, data=datos, headers=h, method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                t = r.read()
                return json.loads(t) if t else {}
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{metodo} {ruta}: {e.code} {e.read()[:200]!r}")


def recortar(colores, tope):
    if not tope or len(colores) <= tope:
        return colores
    out = ""
    for c in colores.split(", "):
        if len(out) + len(c) + 2 > tope:
            break
        out = f"{out}, {c}" if out else c
    return out


def foto_de(cod, nuevo):
    nombre = f"{cod.lower()}.jpg" if nuevo else f"{cod.lower()}_w1.jpg"
    ruta = os.path.join(AQUI, "fotos", nombre)
    return open(ruta, "rb").read() if os.path.exists(ruta) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--destino", choices=DESTINOS, required=True)
    ap.add_argument("--api", default="", help="URL base de la API (jmdventas: http://<ip del contenedor>:8080)")
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()
    d = DESTINOS[a.destino]
    env = leer_env(d["env"])
    base = a.api or d["api"]
    if a.destino == "jmdventas":
        if not base:
            raise SystemExit("jmdventas: pasa --api http://<ip del contenedor>:8080")
        api = API(f"{base.rstrip('/')}/{d['empresa']}", token_firmado(env["JWT_SECRET"], "admin", d["tenant"]))
    else:
        api = API(base)
        api.token = api("POST", "/api/auth/login", {"user": env["ADMIN_USER"], "password": env["ADMIN_PASSWORD"]})["token"]

    of = json.load(open(os.path.join(AQUI, "catalogo_oficial.json"), encoding="utf-8"))
    hay = {p["code"].upper(): p for p in api("GET", "/api/products")}
    retirados = set(of["retirados"])
    plan = {"actualizar": [], "crear": [], "desactivar": []}

    for p in of["productos"]:
        cod = p["code"]
        cuerpo = {"code": cod, "name": p["name"], "category": p["category"], "color": recortar(p["color"], d.get("max_color")),
                  "price": p["price"], "description": p["description"], "active": True}
        viejo = hay.get(cod)
        if viejo:
            stock = {v["size"].upper(): v["stock"] for v in viejo.get("variants") or []}
            cuerpo["variants"] = [{"size": v["size"], "stock": stock.get(v["size"].upper(), v["stock"])} for v in p["variants"]]
            # Tallas que la web no vende pero que tienen stock contado: se quedan (no se borra stock real).
            cuerpo["variants"] += [{"size": t, "stock": n} for t, n in stock.items()
                                   if n > 0 and t not in {v["size"].upper() for v in p["variants"]}]
            cuerpo["ai_tags"] = viejo.get("ai_tags", "")
            cuerpo["image"] = viejo.get("image", "")
            plan["actualizar"].append(f"{cod} {viejo['name']} S/{viejo['price']:g}→{p['price']:g} "
                                      f"{viejo.get('color')}→{p['color'][:40]} tallas {[v['size'] for v in cuerpo['variants']]}")
            if a.aplicar:
                api("PUT", f"/api/products/{viejo['id']}", cuerpo)
                f = None if cod in FOTO_PROPIA else foto_de(cod, False)
                if f:
                    api("POST", f"/api/products/{viejo['id']}/image", foto=f)
        else:
            cuerpo["variants"] = p["variants"]
            cuerpo["ai_tags"] = ""
            plan["crear"].append(f"{cod} {p['name']} S/{p['price']:g} {[v['size'] for v in p['variants']]}")
            if a.aplicar:
                g = api("POST", "/api/products", cuerpo)
                f = foto_de(cod, True)
                if f:
                    api("POST", f"/api/products/{g['id']}/image", foto=f)

    for cod, viejo in sorted(hay.items()):
        if cod in retirados and viejo.get("active"):
            plan["desactivar"].append(f"{cod} {viejo['name']}")
            if a.aplicar:
                cuerpo = {k: viejo.get(k) for k in ("code", "name", "category", "color", "price", "description",
                                                    "image", "ai_tags")}
                cuerpo["active"] = False
                cuerpo["variants"] = [{"size": v["size"], "stock": v["stock"]} for v in viejo.get("variants") or []]
                api("PUT", f"/api/products/{viejo['id']}", cuerpo)

    for k, v in plan.items():
        print(f"{k}: {len(v)}")
        for x in v[:200]:
            print("  ", x)
    if a.aplicar:
        act = [p for p in api("GET", "/api/products") if p.get("active")]
        print("activos ahora:", len(act))
    else:
        print("(simulación: nada escrito; repetir con --aplicar)")


if __name__ == "__main__":
    main()
