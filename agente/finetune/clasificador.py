"""Mensajes de clienta del oro que el clasificador local duda o confunde, para etiquetarlos a mano.

Solo de las conversaciones de ENTRENAMIENTO del oro (nunca de las reservadas, ni de prueba_chat.csv ni de
prueba_comercial.csv). Pasa cada mensaje por `/clasificar` de un agente sin LLM y lista los que salen con confianza
comercial < 0,60 (o intención del bot < 0,60), con su etiqueta y la que el contexto sugiere, para revisarlos.

    python3 finetune/clasificador.py --url http://127.0.0.1:18491 > datos/clasificador_revisar.tsv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
DATOS = os.path.join(AQUI, "datos")
DATA = os.path.join(AQUI, "..", "data")


def _post(url: str, cuerpo: dict) -> dict:
    r = urllib.request.Request(url, json.dumps(cuerpo).encode(), {"content-type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=60))


def ya_estan() -> set[str]:
    """Lo que ya está en los datos de entrenamiento o en las pruebas (no se duplica ni se copia de las pruebas)."""
    vistos = set()
    for nombre, col in (("comercial.csv", "mensaje"), ("intenciones_tienda.csv", "mensaje"), ("prueba_comercial.csv", "mensaje"),
                        ("prueba_chat.csv", None)):
        ruta = os.path.join(DATA, nombre)
        if not os.path.exists(ruta):
            continue
        for fila in csv.reader(open(ruta, encoding="utf-8")):
            for x in fila:
                vistos.add(x.strip().lower())
    return vistos


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:18491")
    ap.add_argument("--oro", default=os.path.join(DATOS, "oro.jsonl"))
    ap.add_argument("--umbral", type=float, default=0.60)
    ap.add_argument("--todos", action="store_true", help="lista todos, para buscar también los que confunde con seguridad")
    a = ap.parse_args(argv)
    vistos = ya_estan()
    msgs = []
    for x in open(a.oro, encoding="utf-8"):
        c = json.loads(x)
        if c["conjunto"] != "entrenamiento":
            continue
        for t in c["turnos"]:
            m = re.sub(r"^\[FOTO\]\s*", "", t["clienta"]).strip()
            if m and not m.startswith("[audio]") and len(m) <= 160 and m.lower() not in vistos:
                msgs.append((c["id"], m, t.get("intent") or ""))
    vistos_aqui = set()
    print("id\tmensaje\tcomercial\tp_com\tintencion_bot\tp_bot")
    n = 0
    for cid, m, _ in msgs:
        if m.lower() in vistos_aqui:
            continue
        vistos_aqui.add(m.lower())
        r = _post(a.url + "/clasificar", {"texto": m})
        com = r["comercial"]
        n += 1
        if a.todos or com["confianza"] < a.umbral or r["confianza"] < a.umbral:
            print(f"{cid}\t{m}\t{com['intent']}\t{com['confianza']:.2f}\t{r['intencion']}\t{r['confianza']:.2f}")
    print(f"# {n} mensajes distintos clasificados", flush=True)


if __name__ == "__main__":
    main()
