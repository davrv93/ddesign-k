"""Pruebas de la métrica venta correcta + tabla de fallo (v2/medicion.py).
Puras: respuestas falsas, sin servidor:

    python3 -m app.prueba_v2_medicion
"""
from __future__ import annotations

from .v2.medicion import tabla_fallo, venta_correcta

fallos: list[str] = []
total = 0


def caso(nombre: str, obtenido, esperado) -> None:
    global total
    total += 1
    if obtenido != esperado:
        fallos.append(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


# Venta correcta: identifica + datos + foto + no inventa + intención + pregunta + no repite.
r = venta_correcta(
    {"codigo": "V24", "precio_txt": "S/ 299", "pregunta": "talla",
     "contiene": ["pandora"], "no_repite": ["chao"]},
    {"plan_producto": "V24", "respuesta": "El Pandora V24 cuesta S/ 299. ¿Qué talla usas?",
     "sugerencias": ["V24"], "conocidos": ["V24"], "siguiente_pregunta": "¿Qué talla usas?"})
caso("venta correcta pasa", r["pasa"], True)

r = venta_correcta({"codigo": "V24"}, {"respuesta": "Te recomiendo el V99, precioso.",
                                      "sugerencias": ["V25"], "conocidos": ["V24", "V25"]})
caso("invento no pasa", (r["pasa"], "no_inventa: inventa ['V99']" in r["fallos"]), (False, True))

r = venta_correcta({"codigo": "V24"}, {"respuesta": "Mira el V24.",
                                      "sugerencias": ["V25"], "conocidos": ["V24", "V25"]})
caso("foto ajena no pasa", (r["pasa"], any(f.startswith("foto") for f in r["fallos"])), (False, True))

# Fuera de alcance deriva; recomendar lo cercano no vale.
r = venta_correcta({"deriva": True}, {"plan_accion": "pedir_asesora",
                                      "respuesta": "Te comunico con una asesora *4*."})
caso("deriva pasa", r["pasa"], True)
r = venta_correcta({"deriva": True}, {"plan_accion": "recomendar",
                                      "respuesta": "Te recomiendo este parecido."})
caso("cercano no es deriva", r["pasa"], False)

# Tabla de fallo: el esperado está en el top-20 pero no primero → ordenar, no recuperar.
t = tabla_fallo([{"esperado": "V24", "diversa": ["V25", "V24"], "elegido": "V24"},
                 {"esperado": "V25", "diversa": ["V25", "V26"], "elegido": "V26"}])
caso("tabla top20/top1/elegido", (t["top20"], t["top1"], t["elegido"]), (1.0, 0.5, 0.5))
caso("tabla vacía", tabla_fallo([]), {"n": 0})

print(f"medicion: {total - len(fallos)}/{total} ok")
if fallos:
    print("\n".join(fallos))
    raise SystemExit(1)
