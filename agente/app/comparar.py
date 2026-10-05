"""Fase 7: V1 contra V2 sobre las conversaciones de regresión (anonimizadas). Solo lee: no cambia nada.

Requiere un agente con AGENT_VERSION=v2 (cada respuesta trae `v2`). Corre en el modo con el que arrancó el agente
(V2_MODO=sombra o activo). Por turno mide:

- acuerdo: si el plan de V2 coincide con lo que V1 hizo de verdad (`accion_v1`: mandó prendas, preguntó, pasó a una
  asesora…). Solo cuenta en los turnos de texto libre: en los flujos que escribe el código (pedido, pago, cita, menú) V2 no
  compite, y se cuentan aparte;
- en modo activo, en cuántos turnos habló V2 y por qué no habló en los demás;
- control de calidad: borradores que pasan el gate, cuántos caen al texto seguro, qué motor los escribió;
- topes, errores del motor y latencia de la capa V2.

Uso:  python3 -m app.comparar --url http://127.0.0.1:18497 [--salida informe.jsonl] [--minimo-acuerdo 0.6]

`--salida` guarda un JSONL con, por turno, el mensaje, lo que dijo V1 (su texto de respaldo, sin LLM de pago) y lo que dijo
V2, para leerlos uno al lado del otro. Sale con código 1 si el agente no responde en V2 o si el acuerdo queda por debajo
de --minimo-acuerdo."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from . import regresion as R


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
                "conv": conv["id"], "mensaje": t["cliente"], "version": j.get("version"), "modo": v2.get("modo"),
                "v1": sb.get("v1_accion"), "fijo": bool(sb.get("v1_flujo_fijo")),
                "plan": (sb.get("plan") or {}).get("accion"), "errores": sb.get("errores") or [],
                "tope": sb.get("tope"), "error": sb.get("error"), "passed": gen.get("passed"),
                "fallback": gen.get("fallback"), "motor_gen": gen.get("motor"), "ms": v2.get("ms"),
                "errores_gate": sorted({e for i in gen.get("intentos") or [] for e in i.get("errors") or []}),
                "enviado": v2.get("enviado"), "no_habla": sb.get("no_habla"), "motivo_v1": v2.get("motivo_v1"),
                "jev": sb.get("jev"), "respuesta": j.get("respuesta"),
                "v1_texto": v2.get("v1_texto_respaldo") or gen.get("v1_texto"),
                "v2_texto": gen.get("texto") if gen.get("passed") else None,
                "razon": (sb.get("plan") or {}).get("razon"),
            })
    return filas


def _clase(motivo: str | None) -> str:
    return (motivo or "—").split(" (")[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:18497")
    ap.add_argument("--minimo-acuerdo", type=float, default=0.0)
    ap.add_argument("--salida", default="", help="JSONL con el texto de V1 y de V2 por turno")
    a = ap.parse_args()
    todas = turnos(a.url)
    filas = [f for f in todas if f["version"]]                  # /chat; los turnos de foto (/foto) no traen versión
    print(f"turnos de foto y de flujo de Go (fuera de la comparación): {len(todas) - len(filas)}")
    versiones = Counter(f["version"] for f in filas)
    if set(versiones) != {"v2"}:
        print(f"el agente responde en {dict(versiones)}: arranca con AGENT_VERSION=v2 para comparar")
        return 1
    modo = Counter(f["modo"] for f in filas).most_common(1)[0][0]
    con_plan = [f for f in filas if f["plan"] and not f["errores"]]
    fijos = [f for f in con_plan if f["fijo"]]
    comparables = [f for f in con_plan if not f["fijo"]]
    acuerdo = [f for f in comparables if f["v1"] == f["plan"]]
    con_borr = [f for f in filas if f["passed"] is not None]
    print(f"modo de V2: {modo}")
    print(f"turnos de /chat: {len(filas)} (V1 y V2 sobre las mismas conversaciones)")
    print(f"plan de V2 válido: {len(con_plan)}/{len(filas)}")
    print(f"  de ellos, flujos de código (V2 no compite): {len(fijos)}")
    print(f"  de texto libre (comparables):            {len(comparables)}")
    tasa = len(acuerdo) / (len(comparables) or 1)
    print(f"acuerdo con V1 en texto libre: {len(acuerdo)}/{len(comparables)} = {tasa:.0%}")
    print("lo que hizo V1 (texto libre):", dict(Counter(f["v1"] for f in comparables)))
    print("lo que planeó V2 (texto libre):", dict(Counter(f["plan"] for f in comparables)))
    cruce = Counter((f["v1"], f["plan"]) for f in comparables)
    print("V1 → V2:  " + ", ".join(f"{v1}→{pl}: {n}" for (v1, pl), n in cruce.most_common()))
    if modo == "activo":
        habla = [f for f in filas if f["enviado"] == "v2"]
        print(f"V2 habló en {len(habla)}/{len(filas)} turnos ({len(habla) / (len(filas) or 1):.0%}); motor: "
              f"{dict(Counter(f['motor_gen'] for f in habla))}")
        print("por qué no habló:", dict(Counter(_clase(f["motivo_v1"]) for f in filas if f["enviado"] != "v2").most_common(6)))
    print(f"borradores: {len(con_borr)}, pasan el gate: {sum(1 for f in con_borr if f['passed'])}, "
          f"caen al texto seguro: {sum(1 for f in con_borr if f['fallback'])}")
    rechazos = Counter(e.split(":")[0] for f in con_borr for e in f["errores_gate"])
    if rechazos:
        print("lo que rechaza el gate:", dict(rechazos.most_common(6)))
    print(f"topes del motor: {sum(1 for f in filas if f['tope'])}, errores del motor: {sum(1 for f in filas if f['error'])}")
    ms = sorted(f["ms"] for f in filas if f["ms"] is not None)
    if ms:
        print(f"latencia de la capa V2: p50 {ms[len(ms) // 2]} ms · p95 {ms[min(len(ms) - 1, int(0.95 * len(ms)))]} ms")
    if any(f["jev"] for f in filas):
        jev = [f["jev"] for f in filas if f["jev"]]
        print(f"Jev local: {len(jev)} consultas, p media {sum(j['p'] for j in jev) / len(jev):.2f}; "
              f"propuso {dict(Counter(j['opcion'] for j in jev))}")
    desacuerdos = Counter((f["v1"], f["plan"]) for f in comparables if f["v1"] != f["plan"])
    if desacuerdos:
        print("desacuerdos (V1 → V2), para revisar uno a uno:")
        for (v1, pl), n in desacuerdos.most_common(8):
            print(f"  {v1} → {pl}: {n}")
    if a.salida:
        with open(a.salida, "w", encoding="utf-8") as fh:
            for f in filas:
                fh.write(json.dumps(f, ensure_ascii=False) + "\n")
        print(f"informe por turno: {a.salida}")
    return 0 if tasa >= a.minimo_acuerdo else 1


if __name__ == "__main__":
    sys.exit(main())
