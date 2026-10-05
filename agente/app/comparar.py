"""Fase 7: V1 contra V2 sobre las conversaciones de regresión (anonimizadas). Solo lee: no cambia nada.

Requiere un agente con AGENT_VERSION=v2 (cada respuesta trae `v2.sombra`). Por turno mide:
- acuerdo: si el plan de V2 coincide con la acción que hizo V1 (MAPA_V1);
- control de calidad: borradores que pasan el gate, cuántos caen al texto seguro;
- topes, errores del motor y latencia de la capa V2.

Uso:  python3 -m app.comparar --url http://127.0.0.1:18497
Sale con código 1 si el agente no responde en V2 o si el acuerdo queda por debajo de --minimo-acuerdo.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from . import regresion as R
from .v2.metricas import MAPA_V1


def turnos(url: str) -> list[dict]:
    filas = []
    for conv in R.cargar_conversaciones():
        ch = R.Chat(url, conv)
        for t in conv["turnos"]:
            est, j, _ = ch.turno(t["cliente"], t.get("llm", ""), t.get("pausa_horas", 0))
            if est != 200 or not isinstance(j, dict):
                continue
            v2 = j.get("v2") or {}
            sb = v2.get("sombra") or {}
            gen = sb.get("generacion") or {}
            filas.append({
                "conv": conv["id"], "version": j.get("version"), "v1": j.get("accion"),
                "plan": (sb.get("plan") or {}).get("accion"), "errores": sb.get("errores") or [],
                "tope": sb.get("tope"), "error": sb.get("error"), "passed": gen.get("passed"),
                "fallback": gen.get("fallback"), "ms": v2.get("ms"),
            })
    return filas


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:18497")
    ap.add_argument("--minimo-acuerdo", type=float, default=0.0)
    a = ap.parse_args()
    todas = turnos(a.url)
    filas = [f for f in todas if f["version"]]                  # /chat; los turnos de foto (/foto) no traen versión
    print(f"turnos de foto (/foto, fuera de la comparación): {len(todas) - len(filas)}")
    versiones = Counter(f["version"] for f in filas)
    if set(versiones) != {"v2"}:
        print(f"el agente responde en {dict(versiones)}: arranca con AGENT_VERSION=v2 para comparar")
        return 1
    con_plan = [f for f in filas if f["plan"]]
    acuerdo = [f for f in con_plan if MAPA_V1.get(f["v1"]) == f["plan"]]
    con_borr = [f for f in filas if f["passed"] is not None]
    print(f"turnos comparados: {len(filas)} (V1 y V2 sobre las mismas conversaciones)")
    print(f"plan de V2 válido: {len(con_plan)}/{len(filas)}")
    print(f"acuerdo con V1:    {len(acuerdo)}/{len(con_plan) or 1} = {len(acuerdo) / (len(con_plan) or 1):.0%}")
    print(f"borradores: {len(con_borr)}, pasan el gate: {sum(1 for f in con_borr if f['passed'])}, "
          f"caen al texto seguro: {sum(1 for f in con_borr if f['fallback'])}")
    print(f"topes del motor: {sum(1 for f in filas if f['tope'])}, errores del motor: {sum(1 for f in filas if f['error'])}")
    ms = sorted(f["ms"] for f in filas if f["ms"] is not None)
    if ms:
        print(f"latencia de la capa V2: p50 {ms[len(ms) // 2]} ms · p95 {ms[min(len(ms) - 1, int(0.95 * len(ms)))]} ms")
    print("planes de V2:", dict(Counter(f["plan"] for f in con_plan)))
    print("acciones de V1:", dict(Counter(f["v1"] for f in filas)))
    desacuerdos = Counter((f["v1"], f["plan"]) for f in con_plan if MAPA_V1.get(f["v1"]) != f["plan"])
    if desacuerdos:
        print("desacuerdos (V1 → V2), para revisar uno a uno:")
        for (v1, pl), n in desacuerdos.most_common(8):
            print(f"  {v1} → {pl}: {n}")
    tasa = len(acuerdo) / (len(con_plan) or 1)
    return 0 if tasa >= a.minimo_acuerdo else 1


if __name__ == "__main__":
    sys.exit(main())
