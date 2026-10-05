"""Pruebas del banco de estilo (few-shot) de venta.py, sin dependencias ni llamadas a un LLM.

    python3 -m app.prueba_estilo
"""
from __future__ import annotations

import json
import os
import sys

from . import venta

BANCO = [
    {"etapa": "prospeccion", "situacion": "saluda", "clienta": "hola", "salida": {"responde": "¡Hola! Soy Rosmary.", "por_que": "", "pregunta": ""}},
    {"etapa": "prospeccion", "situacion": "cuenta su evento", "clienta": "es pa una boda", "salida": {"responde": "¡Qué lindo!", "por_que": "", "pregunta": ""}},
    {"etapa": "seguimiento", "situacion": "pregunta la tela", "clienta": "de ke tela es", "salida": {"responde": "Es de gasa.", "por_que": "", "pregunta": ""}},
    {"etapa": "seguimiento", "situacion": "precio", "clienta": "cuanto", "salida": {"responde": "Está a *S/ 220*.", "por_que": "", "pregunta": ""}},
    {"etapa": "seguimiento", "situacion": "objeción", "clienta": "esta caro", "salida": {"responde": "Te entiendo.", "por_que": "", "pregunta": ""}},
    {"etapa": "cierre", "situacion": "quiere probárselo", "clienta": "quiero ir", "salida": {"responde": "¡Me encanta!", "por_que": "", "pregunta": ""}},
]

casos = 0
fallos = []


def ok(cond: bool, nombre: str) -> None:
    global casos
    casos += 1
    if not cond:
        fallos.append(nombre)


# Apagado por defecto: el prompt no cambia.
ok(venta.bloque_estilo("seguimiento", banco=BANCO) == "" or venta.ESTILO_FEWSHOT, "apagado por defecto devuelve vacío")
ok(venta.bloque_estilo("seguimiento", banco=BANCO, activo=False) == "", "activo=False devuelve vacío")
# Encendido: hasta n ejemplos de la etapa, en orden, con su JSON.
b = venta.bloque_estilo("seguimiento", n=3, banco=BANCO, activo=True)
ok(b.startswith("EJEMPLOS DE ESTILO"), "cabecera")
ok(b.count("clienta:") == 3, "tres ejemplos de seguimiento")
ok("[SEGUIMIENTO]" in b and "[PROSPECCIÓN]" not in b, "solo de la etapa cuando hay suficientes")
ok('"responde": "Es de gasa."' in b, "el JSON va tal cual, con tildes")
ok(venta.bloque_estilo("seguimiento", n=2, banco=BANCO, activo=True).count("clienta:") == 2, "respeta n")
# Con menos de 2 de la etapa, se completa con otras.
c = venta.bloque_estilo("cierre", n=3, banco=BANCO, activo=True)
ok(c.count("clienta:") == 3 and "[CIERRE]" in c.split("\n")[1], "cierre: el suyo primero y luego otros")
ok(venta.bloque_estilo("venta_confirmada", banco=[], activo=True) == "", "sin banco no hay bloque")
ok(venta.bloque_estilo("seguimiento", n=0, banco=BANCO, activo=True) == "", "n=0 no hay bloque")
# El banco real (seed/estilo.jsonl), si existe, se lee y cada fila tiene lo que el bloque necesita.
ruta = os.path.join(os.path.dirname(__file__), "..", "seed", "estilo.jsonl")
if os.path.exists(ruta):
    filas = [json.loads(x) for x in open(ruta, encoding="utf-8") if x.strip()]
    ok(15 <= len(filas) <= 20, f"el banco real tiene 15–20 ejemplos ({len(filas)})")
    ok(all({"etapa", "clienta", "salida"} <= set(f) and {"responde", "por_que", "pregunta"} <= set(f["salida"]) for f in filas),
       "cada ejemplo trae etapa, clienta y salida completa")
    ok(all(f["etapa"] in venta.NOMBRE_ETAPA for f in filas), "etapas válidas")
    ok(not any(x in json.dumps(filas, ensure_ascii=False).lower() for x in ("yape", "plin", "bcp", "interbank")),
       "sin datos de pago")
    for etapa in ("prospeccion", "seguimiento", "cierre"):
        ok(venta.bloque_estilo(etapa, banco=filas, activo=True).count("clienta:") >= 2, f"el banco real arma bloque en {etapa}")

print(f"estilo: {casos - len(fallos)}/{casos} casos")
for f in fallos:
    print("  FALLA:", f)
sys.exit(1 if fallos else 0)
