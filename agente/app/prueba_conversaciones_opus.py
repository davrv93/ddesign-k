"""Extracción de datos (app/memoria.py) contra 30 conversaciones de clientas redactadas con Opus
(data/conversaciones_eventos_opus.csv, 12-10-2026): ocasión, día/noche, talla, estatura, fecha, color, presupuesto, envío,
ciudad y estilo, tal como escribe la gente por WhatsApp (abreviaturas, votos de encuesta, estaturas raras, distritos…).

Cada conversación se lee turno a turno sobre la misma memoria. El CSV trae solo los mensajes de la clienta, así que la
pregunta que el bot habría hecho antes se simula con el dato que se espera de ese turno (si es un dato del guion): en
WhatsApp ese mensaje llega como respuesta a esa pregunta.

    python3 -m app.prueba_conversaciones_opus
"""
from __future__ import annotations

import collections
import csv
import json
import os
import re
import sys
import unicodedata

from . import memoria as M

RUTA = os.path.join(os.path.dirname(__file__), "..", "data", "conversaciones_eventos_opus.csv")
MINIMO = 0.95            # por debajo, falla. 12-10-2026: 82 % antes de ajustar memoria.py, 100 % (210/210) después
PENDIENTE_DE = {"talla": "talla", "horario": "horario", "estatura": "estatura", "fecha": "fecha",
                "envio": "lima_o_provincia", "estilo": "estilo"}
# Mismo evento, otro nombre: el CSV lo escribe como lo diría una persona; memoria usa su clave.
SINONIMOS = {"boda": "matrimonio", "boda civil": "matrimonio", "boda invitada": "matrimonio", "boda madrina": "matrimonio",
             "matrimonio religioso": "matrimonio", "promocion de colegio": "graduacion", "promocion": "graduacion",
             "quinceanero": "quinceanero", "quince": "quinceanero", "cena de empresa": "cena", "evento de oficina": "trabajo",
             "entrevista de trabajo": "trabajo", "sustentacion de tesis": "trabajo", "congreso": "trabajo",
             "pedida de mano": "compromiso", "despedida de soltera": "fiesta", "almuerzo familiar": "fiesta"}
TEXTO_LIBRE = ("fecha", "presupuesto", "ocasion", "ciudad", "color")


def _pl(s) -> str:
    s = unicodedata.normalize("NFKD", str(s).lower()).encode("ascii", "ignore").decode().strip()
    return SINONIMOS.get(s, s)


def _igual(k: str, esperado, obtenido) -> bool:
    if obtenido is None:
        return False
    e, o = _pl(esperado), _pl(obtenido)
    if k == "fecha" and re.findall(r"\d+", e) and re.findall(r"\d+", e) == re.findall(r"\d+", o):
        return True                      # «el 29» = «29 de este mes»: el mismo día
    return (e in o or o in e) if k in TEXTO_LIBRE else e == o


def evaluar() -> tuple[int, int, list[str]]:
    filas = list(csv.DictReader(open(RUTA, encoding="utf-8")))
    por_conv = collections.defaultdict(list)
    for r in filas:
        por_conv[r["conversacion_id"]].append(r)
    ok = total = 0
    fallos: list[str] = []
    for cid, turnos in sorted(por_conv.items()):
        mem = M.nueva()
        for r in sorted(turnos, key=lambda r: int(r["turno"])):
            esperado = {k: v for k, v in json.loads(r["datos_esperados"] or "{}").items() if k != "para_quien"}
            claves = [k for k in esperado if k in PENDIENTE_DE]
            if claves:
                mem["pendiente"] = PENDIENTE_DE[claves[0]]
            M.leer(mem, r["mensaje_cliente"], ahora=M.ahora_lima())
            sab = mem["sabemos"]
            for k, v in esperado.items():
                total += 1
                obtenido = (sab.get("fecha") or sab.get("fecha_iso")) if k == "fecha" else sab.get(k)
                if _igual(k, v, obtenido):
                    ok += 1
                else:
                    fallos.append(f"{cid}.{r['turno']} [{k}] «{r['mensaje_cliente'][:70]}» esperado={v!r} obtuvo={obtenido!r}")
    return ok, total, fallos


def main() -> int:
    ok, total, fallos = evaluar()
    print(f"conversaciones Opus (extracción): {ok}/{total} datos ({100 * ok / total:.1f} %)")
    for f in fallos:
        print("  ✗", f)
    if ok / total < MINIMO:
        print(f"por debajo del mínimo ({MINIMO:.0%})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
