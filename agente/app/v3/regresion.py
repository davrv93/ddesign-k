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
import time

from .. import memoria, regresion

BANCO = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "v3_conversaciones.jsonl"))


def _norm(t: str) -> str:
    return " ".join("".join(c if c.isalnum() or c == " " else " " for c in (t or "").lower()).split())


def repeticiones(r: dict) -> list[str]:
    """Lo que ninguna regla del runner mira: el mismo texto (o la misma pregunta) dos veces en una conversación."""
    f, vistos, preguntas = [], {}, {}
    for t in r.get("turnos", []):
        n = _norm(t.get("bot", ""))
        if len(n) > 12:
            if n in vistos:
                f.append(f"t{t['k']}: repite el mensaje del turno {vistos[n]}")
            vistos.setdefault(n, t["k"])
        for q in memoria.preguntas_en(t.get("bot", "")):
            nq = _norm(q)
            if nq in preguntas and preguntas[nq] != t["k"]:
                f.append(f"t{t['k']}: repite la pregunta del turno {preguntas[nq]}: {q.strip()[:60]!r}")
            preguntas.setdefault(nq, t["k"])
    return f


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Banco de conversaciones de V3 (llama al LLM).")
    ap.add_argument("--url", default="http://127.0.0.1:18497")
    ap.add_argument("--banco", default=BANCO)
    ap.add_argument("--caso", default="")
    ap.add_argument("--salida", default="", help="guardar la transcripción completa en este JSONL (para el juez)")
    ap.add_argument("--detalle", action="store_true", help="listar también los aprobados")
    ap.add_argument("--version", default="v3", help="v1 | v3: con qué versión se corre el banco (para comparar)")
    ap.add_argument("--pausa", type=float, default=0.0, help="segundos entre conversaciones (cuota del LLM)")
    ap.add_argument("--paralelo", type=int, default=1, help="conversaciones a la vez (cada una sigue siendo secuencial)")
    a = ap.parse_args(argv)

    regresion.VERSION_OVERRIDE = a.version
    regresion.USAR_LLM_OVERRIDE = True
    regresion.HISTORIAL_FUSIONADO = True
    regresion.PREGUNTAS_MAX = 2 if a.version == "v3" else 1
    url = a.url.rstrip("/")
    cat = regresion.Catalogo(url)
    if not cat.salud.get("ok"):
        print(f"El agente no responde en {url}/health")
        return 2

    convs = [c for c in regresion.cargar_conversaciones(a.banco) if not a.caso or a.caso in c["id"]]

    def una(c):
        r = regresion.correr_conversacion(url, c, cat)
        if not r.get("omitida"):
            r["fallos"] = list(r.get("fallos", [])) + repeticiones(r)
        if a.pausa:
            time.sleep(a.pausa)
        return r

    if a.paralelo > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=a.paralelo) as pool:
            resultados = list(pool.map(una, convs))
    else:
        resultados = [una(c) for c in convs]
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
