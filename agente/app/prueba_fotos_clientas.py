"""Fotos REALES que mandaron clientas por WhatsApp (data/fotos_clientas/, fuera de git: son personas), con lo que el bot
debía reconocer (esperado.csv: código del catálogo o vacío = «no es ninguno», más una nota). 13-10-2026, pedido del
usuario: «banco de fotos reales, probado en cada build».

Dos modos:
- En el build (sin clave de LLM): solo el parecido visual. Para cada foto con código esperado, ese código debe estar
  entre los candidatos que verá la visión (2 × CANDIDATOS_MAX); si no está, la visión nunca lo podrá elegir.
- Con un agente corriendo (`--url http://…`): el resultado completo de /foto (visión incluida), como en producción:
  las de catálogo deben salir «exacto» con su código; las negativas no deben salir «exacto» con ninguno (ni «parecido»
  afirmando un modelo). Mide falsos positivos.

    python3 -m app.prueba_fotos_clientas                       # build
    python3 -m app.prueba_fotos_clientas --url http://127.0.0.1:18497   # completo

Si la carpeta no existe (un clon del repo), la prueba pasa avisando: las fotos solo viven en el servidor y en la Mac.
"""
from __future__ import annotations

import base64
import csv
import json
import os
import sys
import urllib.request

DIR = os.path.join(os.path.dirname(__file__), "..", "data", "fotos_clientas")
CSV = os.path.join(DIR, "esperado.csv")


def casos() -> list[dict]:
    if not os.path.exists(CSV):
        return []
    with open(CSV, encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if os.path.exists(os.path.join(DIR, r["archivo"]))]


def en_build() -> int:
    from . import imagen, vision
    filas = casos()
    if not filas:
        print("fotos de clientas: sin carpeta data/fotos_clientas (solo en el servidor y en la Mac); se omite")
        return 0
    b = imagen.Buscador()
    k = 2 * vision.CANDIDATOS_MAX
    fallos = []
    for r in filas:
        if not r["esperado"]:
            continue
        with open(os.path.join(DIR, r["archivo"]), "rb") as fh:
            top = [c for c, _ in b.buscar(fh.read(), k=k)]
        if r["esperado"].upper() not in top:
            fallos.append(f"{r['archivo']}: {r['esperado']} no está entre los {k} candidatos {top} ({r['nota']})")
    pos = sum(1 for r in filas if r["esperado"])
    print(f"fotos de clientas (parecido visual): {pos - len(fallos)}/{pos} con su modelo entre los candidatos; "
          f"{len(filas) - pos} negativas se prueban con --url")
    for f in fallos:
        print("  ✗", f)
    return 1 if fallos else 0


def contra_agente(url: str) -> int:
    filas = casos()
    fallos, ok = [], 0
    for r in filas:
        with open(os.path.join(DIR, r["archivo"]), "rb") as fh:
            cuerpo = {"imagen_b64": base64.b64encode(fh.read()).decode(), "mensaje": "", "usar_llm": False,
                      "conversacion": "prueba-fotos-clientas"}
        req = urllib.request.Request(url.rstrip("/") + "/foto", json.dumps(cuerpo).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as res:
            d = json.load(res)
        fo = d.get("foto") or {}
        cod, nivel = (fo.get("codigo") or "").upper(), fo.get("nivel")
        esp = r["esperado"].upper()
        if esp:
            bien = nivel == "exacto" and cod == esp
        else:
            bien = nivel not in ("exacto", "parecido")      # no afirma ni sugiere un modelo que no es
        if bien:
            ok += 1
        else:
            vis = fo.get("vision") or {}
            fallos.append(f"{r['archivo']}: esperado {esp or 'ninguno'}, salió {cod} {nivel} "
                          f"(visión {vis.get('codigo')!r} {vis.get('confianza')}) · {r['nota']}")
    print(f"fotos de clientas (/foto completo): {ok}/{len(filas)}")
    for f in fallos:
        print("  ✗", f)
    return 1 if fallos else 0


if __name__ == "__main__":
    if "--url" in sys.argv:
        sys.exit(contra_agente(sys.argv[sys.argv.index("--url") + 1]))
    sys.exit(en_build())
