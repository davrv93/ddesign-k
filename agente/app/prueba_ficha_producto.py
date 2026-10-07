"""La ficha técnica de cada prenda (app/ficha_producto.py, seed/fichas_producto.json): integridad y detección.

    python3 -m app.prueba_ficha_producto
"""
import json
import os

from . import ficha_producto as F
from . import memoria, venta

fallos, total = [], 0


def caso(ok: bool, msg: str):
    global total
    total += 1
    if not ok:
        fallos.append("✗ " + msg)


# --- integridad: una ficha por prenda real, con el esquema, y la tela nunca sale de una foto
fichas = F.cargar()
caso(set(fichas) == {f"V{n}" for n in range(21, 43)}, f"faltan o sobran fichas: {sorted(fichas)}")
cat = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data", "catalogo_seed.json"), encoding="utf-8"))
reales = {p.get("code") or p.get("codigo") for p in (cat if isinstance(cat, list) else cat.get("products", []))}
caso(not ({c for c in reales if c and "V21" <= c <= "V42"} - set(fichas)), "una prenda real del catálogo no tiene ficha")
for cod, f in fichas.items():
    for k, a in (f.get("atributos") or {}).items():
        caso(a is None or (isinstance(a, dict) and a.get("valor") and a.get("fuente") in ("diners", "foto", "ambas")),
             f"{cod}.{k}: atributo mal formado {a}")
    caso((f["atributos"].get("tela") or {}).get("fuente") != "foto", f"{cod}: la tela no puede salir de una foto")
    caso(f.get("cuidados") is None or isinstance(f["cuidados"], str), f"{cod}: cuidados")
    caso(any("medidas" in p for p in f.get("pendiente_tienda") or []), f"{cod}: las medidas por talla deben figurar como pendientes")
    caso(all(d.get("fuente") == "diners" for d in f.get("como_queda") or []), f"{cod}: «cómo queda» solo puede venir de la tienda")

# --- detección: lo que pregunta (y lo que NO es pregunta de atributo)
DETECTA = [
    ("¿tiene mangas?", ["mangas"]), ("es largo o corto?", ["largo"]), ("¿y la espalda?", ["espalda"]),
    ("¿tiene forro?", ["forro"]), ("cuáles son sus medidas", ["medidas"]), ("¿deja los hombros descubiertos?", ["escote"]),
    ("¿viene con la falda?", ["piezas"]), ("¿se transparenta?", ["transparencias"]), ("¿cómo se lava?", ["cuidados"]),
    ("¿tiene mangas y forro?", ["mangas", "forro"]),
    ("cuándo me llega", []), ("¿se ve bonito?", []), ("¿incluye envío?", []), ("¿lo tienes en lavanda?", []),
    ("tengo 70 de cintura", []), ("con mis medidas la L me queda justa", []),
    ("mi mamá es más de vestidos sueltos, ¿tienes algo así?", []), ("no quiero nada muy apretado, ¿qué me recomiendas?", []),
    ("¿ese vestido es muy largo o apretado?", ["largo", "silueta"]),
    ("me preocupa que me quede largo, ¿tienen algún ajuste?", ["largo"]), ("¿tiens algun bestido asi más suelto?", []),
]
for m, esp in DETECTA:
    caso(F.pedidos(memoria._plano(m)) == esp, f"«{m}»: detecta {F.pedidos(memoria._plano(m))}, esperaba {esp}")
caso(not F.RE_PREGUNTA.search(memoria._plano("quiero un vestido largo para boda")), "«quiero un vestido largo» no es una pregunta de atributo")

# --- respuestas: el dato de la ficha, y lo que no figura va a la asesora (nunca se inventa)
r = F.respuesta("V35", "*V35* Vestido Irla", ["mangas"])
caso(r.startswith("Las mangas del *V35* Vestido Irla:") and "sin mangas" in r, f"mangas del Irla: {r}")
r = F.respuesta("V35", "*V35* Vestido Irla", ["forro"])
caso("asesora" in r and "*4*" in r, f"forro del Irla (no figura) debe ir a la asesora: {r}")
r = F.respuesta("V24", "*V24* Conjunto Kabanova Azul", ["mangas", "medidas"])
caso("jamón" in r and "medidas" in r and "asesora" in r, f"mangas + medidas del Kabanova: {r}")
caso(F.respuesta("V01", "*V01* x", ["mangas"]) == "", "sin ficha no hay respuesta del código")
caso(F.dicho("V35", ["largo"], "Es a la rodilla 😊") and not F.dicho("V35", ["largo"], "¡Es precioso!"), "dicho(): reconoce si contestó el largo")
caso("FICHA TÉCNICA" in venta.extras_texto("V24") and "NO FIGURA" in venta.extras_texto("V24"), "la ficha llega al LLM con lo que no figura")
caso(venta.tela("V24", "") == "", "«no se indica» no es una tela que se diga")
caso(venta.tela("V41", "") == "tela roma", f"tela del Holly desde la ficha: {venta.tela('V41', '')}")

print(f"ficha técnica de producto: {total - len(fallos)}/{total} casos")
for f in fallos:
    print(f)
if fallos:
    raise SystemExit(1)
