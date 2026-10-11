"""Banco de conversaciones de V3 (llama al LLM: tiene costo).

Reusa el runner de V1/V2 (`app/regresion.py`): las mismas afirmaciones por turno (`espera`) y las mismas
reglas universales, pero forzando `version=v3`. El agente de pruebas debe tener V3 disponible; la versión
viaja en cada petición, así que no importa su AGENT_VERSION.

    python3 -m app.v3.regresion --url http://127.0.0.1:18497
    python3 -m app.v3.juez --resultado pruebas_conv/v3.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .. import regresion

BANCO = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "v3_conversaciones.jsonl"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Banco de conversaciones de V3 (llama al LLM).")
    ap.add_argument("--url", default="http://127.0.0.1:18497")
    ap.add_argument("--banco", default=BANCO)
    ap.add_argument("--caso", default="")
    ap.add_argument("--salida", default="", help="guardar la transcripción completa en este JSONL (para el juez)")
    ap.add_argument("--detalle", action="store_true", help="listar también los aprobados")
    a = ap.parse_args(argv)

    regresion.VERSION_OVERRIDE = "v3"
    regresion.USAR_LLM_OVERRIDE = True
    url = a.url.rstrip("/")
    cat = regresion.Catalogo(url)
    if not cat.salud.get("ok"):
        print(f"El agente no responde en {url}/health")
        return 2

    convs = [c for c in regresion.cargar_conversaciones(a.banco) if not a.caso or a.caso in c["id"]]
    resultados = [regresion.correr_conversacion(url, c, cat) for c in convs]
    if a.salida:
        with open(a.salida, "w", encoding="utf-8") as fh:
            for r in resultados:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    malos = [r for r in resultados if r.get("fallos")]
    for r in resultados:
        if r.get("fallos") or a.detalle:
            marca = "✗" if r.get("fallos") else "✓"
            print(f"  {marca} {r['id']}: {r.get('omitida') or '; '.join(r.get('fallos', []))}")
    print(f"V3 conversaciones: {len(resultados) - len(malos)}/{len(resultados)} ok")
    return 1 if malos else 0


if __name__ == "__main__":
    sys.exit(main())
