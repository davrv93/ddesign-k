"""Del oro (datos/oro.jsonl) a pares de chat para `mlx_lm.lora`.

Cada turno de la vendedora que pasó por el LLM es un ejemplo: `system` = `venta.sistema(...)` y `user` = el contexto
que armó el agente (los dos tal cual los mandó, capturados por el puente) y `assistant` = el JSON de `estructurado.py`
que escribió la redactora. Las conversaciones `reservada` van a `test.jsonl` y no se entrenan; de las de entrenamiento,
un 10 % (por conversación, no por turno: los turnos de una misma charla se parecen) va a `valid.jsonl`.

    python3 finetune/convertir.py            # datos/mlx/{train,valid,test}.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import random

AQUI = os.path.dirname(os.path.abspath(__file__))
DATOS = os.path.join(AQUI, "datos")


def pares(conv: dict) -> list[dict]:
    out = []
    for t in conv["turnos"]:
        if not t.get("contexto") or t.get("salida") in (None, ""):
            continue
        sal = t["salida"]
        texto = json.dumps(sal, ensure_ascii=False) if isinstance(sal, dict) else str(sal)
        out.append({"messages": [*t["contexto"], {"role": "assistant", "content": texto}]})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--oro", default=os.path.join(DATOS, "oro.jsonl"))
    ap.add_argument("--salida", default=os.path.join(DATOS, "mlx"))
    ap.add_argument("--valid", type=float, default=0.10)
    ap.add_argument("--semilla", type=int, default=7)
    ap.add_argument("--sin-reparadas", action="store_true",
                    help="deja fuera las conversaciones que `oro.py reparar` tocó tras la caída del agente")
    a = ap.parse_args(argv)
    convs = [json.loads(x) for x in open(a.oro, encoding="utf-8") if x.strip()]
    if a.sin_reparadas:
        convs = [c for c in convs if not c.get("reparada")]
    ent = sorted((c for c in convs if c["conjunto"] == "entrenamiento"), key=lambda c: c["id"])
    res = [c for c in convs if c["conjunto"] == "reservada"]
    rng = random.Random(a.semilla)
    rng.shuffle(ent)
    n_val = max(1, round(len(ent) * a.valid))
    grupos = {"valid": ent[:n_val], "train": ent[n_val:], "test": res}
    os.makedirs(a.salida, exist_ok=True)
    for nombre, cs in grupos.items():
        filas = [p for c in cs for p in pares(c)]
        if nombre == "train":
            rng.shuffle(filas)
        with open(os.path.join(a.salida, f"{nombre}.jsonl"), "w", encoding="utf-8") as fh:
            for f in filas:
                fh.write(json.dumps(f, ensure_ascii=False) + "\n")
        print(f"{nombre}: {len(cs)} conversaciones, {len(filas)} ejemplos")


if __name__ == "__main__":
    main()
