"""Del oro (datos/oro.jsonl) a pares de chat para `mlx_lm.lora`.

Cada turno de la vendedora que pasó por el LLM es un ejemplo: `system` = `venta.sistema(...)` y `user` = el contexto
que armó el agente (los dos tal cual los mandó, capturados por el puente; el `user`, recortado con `contexto.compactar`
salvo `--completo`) y `assistant` = el JSON de `estructurado.py` que escribió la redactora. Las conversaciones
`reservada` van a `test.jsonl` y no se entrenan; de las de entrenamiento, un 10 % (por conversación, no por turno: los
turnos de una misma charla se parecen) va a `valid.jsonl`.

    python3 finetune/convertir.py            # datos/mlx/{train,valid,test}.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import contexto  # noqa: E402

DATOS = os.path.join(AQUI, "datos")


def pares(conv: dict, compacto: bool = True) -> list[dict]:
    out = []
    for t in conv["turnos"]:
        if not t.get("contexto") or t.get("salida") in (None, ""):
            continue
        sal = t["salida"]
        texto = json.dumps(sal, ensure_ascii=False) if isinstance(sal, dict) else str(sal)
        ctx = contexto.compactar(t["contexto"]) if compacto else t["contexto"]
        out.append({"messages": [*ctx, {"role": "assistant", "content": texto}]})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--oro", default=os.path.join(DATOS, "oro.jsonl"))
    ap.add_argument("--salida", default=os.path.join(DATOS, "mlx"))
    ap.add_argument("--valid", type=float, default=0.10)
    ap.add_argument("--semilla", type=int, default=7)
    ap.add_argument("--sin-reparadas", action="store_true",
                    help="deja fuera las conversaciones que `oro.py reparar` tocó tras la caída del agente")
    ap.add_argument("--completo", action="store_true", help="deja el contexto del agente tal cual (sin contexto.compactar)")
    ap.add_argument("--max-tokens", type=int, default=0,
                    help="deja fuera de train/valid los ejemplos más largos (mlx_lm corta por el final: se llevaría la respuesta). "
                         "Necesita --tokenizer y el paquete `tokenizers` (el venv de finetune lo trae)")
    ap.add_argument("--tokenizer", default=os.path.join(AQUI, "modelos", "qwen15b-4bit", "tokenizer.json"))
    a = ap.parse_args(argv)
    largo = None
    if a.max_tokens:
        from tokenizers import Tokenizer
        tok = Tokenizer.from_file(a.tokenizer)
        # +8 por mensaje: las marcas de turno de la plantilla de chat de Qwen (<|im_start|>rol … <|im_end|>).
        largo = lambda f: sum(len(tok.encode(m["content"]).ids) + 8 for m in f["messages"])   # noqa: E731
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
        filas = [p for c in cs for p in pares(c, not a.completo)]
        largos = 0
        if largo and nombre != "test":
            antes = len(filas)
            filas = [f for f in filas if largo(f) <= a.max_tokens]
            largos = antes - len(filas)
        if largos:
            print(f"  ({largos} ejemplos de {nombre} pasan de {a.max_tokens} tokens y quedan fuera)")
        if nombre == "train":
            rng.shuffle(filas)
        with open(os.path.join(a.salida, f"{nombre}.jsonl"), "w", encoding="utf-8") as fh:
            for f in filas:
                fh.write(json.dumps(f, ensure_ascii=False) + "\n")
        print(f"{nombre}: {len(cs)} conversaciones, {len(filas)} ejemplos")


if __name__ == "__main__":
    main()
