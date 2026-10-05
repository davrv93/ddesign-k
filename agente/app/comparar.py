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


def turnos(url: str, con_temas: bool = False) -> list[dict]:
    filas = []
    for conv in R.cargar_conversaciones():
        if conv.get("tipo") == "interrupcion" and not con_temas:
            continue            # las de cambios de tema se miden aparte: así las cifras de siempre siguen siendo comparables
        ch = R.Chat(url, conv)
        for t in conv["turnos"]:
            est, j, _ = ch.turno(t["cliente"], t.get("llm", ""), t.get("pausa_horas", 0), t.get("payload"))
            if est != 200 or not isinstance(j, dict):
                continue
            v2 = j.get("v2") or {}
            sb = v2.get("sombra") or {}
            gen = sb.get("generacion") or {}
            tm = v2.get("temas") or {}
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
                "razon": (sb.get("plan") or {}).get("razon"), "conv_tipo": conv.get("tipo", ""),
                "tema_nivel": (tm.get("evento") or {}).get("nivel"), "tema_tipo": (tm.get("evento") or {}).get("tipo"),
                "tema_retoma": (tm.get("retoma") or {}).get("slot"), "tema_retoma_pasa": (tm.get("retoma") or {}).get("pasa_la_compuerta"),
                "tema_enviada": tm.get("enviada"), "tema_v1_retomo": tm.get("v1_retomo"), "tema_bloqueo": tm.get("bloqueo"),
                "tema_habla": tm.get("habla"), "tema_error": tm.get("error"),
                "tema_pendientes": [f"{x['slot']}:{x['status']}" for x in tm.get("pendientes") or []],
            })
    return filas


def resumen_temas(filas: list[dict]) -> None:
    """Cambios de tema (app/v2/temas.py): qué interrupciones vio V2, qué retomaría, cuántas veces ya lo hizo V1 solo y cuántas salieron."""
    con = [f for f in filas if f.get("tema_nivel") or f.get("tema_tipo") or f.get("tema_retoma")]
    if not any(f.get("tema_tipo") for f in filas):
        return
    inter = [f for f in filas if f.get("tema_tipo") == "interrumpe"]
    print("cambios de tema:")
    print(f"  interrupciones de la clienta: {len(inter)} · por nivel {dict(Counter(f['tema_nivel'] for f in inter))}")
    print(f"  cambios totales (HARD_SWITCH): {sum(1 for f in filas if f.get('tema_nivel') == 'HARD_SWITCH')} · "
          f"ayuda («no sé», duda, «sí» ambiguo): {sum(1 for f in filas if f.get('tema_tipo') == 'ayuda')}")
    retomas = [f for f in filas if f.get("tema_retoma")]
    print(f"  retomas que V2 planea: {len(retomas)} · pasan la compuerta {sum(1 for f in retomas if f['tema_retoma_pasa'])} · "
          f"salieron al cliente {sum(1 for f in retomas if f['tema_enviada'])}")
    print(f"  V1 ya volvió a preguntar el pendiente solo: {sum(1 for f in filas if f.get('tema_v1_retomo'))}")
    print("  por qué no se retomó en las interrupciones sin retoma:",
          dict(Counter((f['tema_bloqueo'] or '—').split(' (')[0][:48] for f in inter if not f.get('tema_retoma')).most_common(5)))
    print(f"  errores de la capa: {sum(1 for f in filas if f.get('tema_error'))}")


def _clase(motivo: str | None) -> str:
    return (motivo or "—").split(" (")[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:18497")
    ap.add_argument("--minimo-acuerdo", type=float, default=0.0)
    ap.add_argument("--salida", default="", help="JSONL con el texto de V1 y de V2 por turno")
    ap.add_argument("--temas", action="store_true", help="incluir también las conversaciones de cambios de tema (tipo «interrupcion»)")
    a = ap.parse_args()
    todas = turnos(a.url, a.temas)
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
    resumen_temas(filas)
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
