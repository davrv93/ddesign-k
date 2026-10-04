"""Ajuste fino contrastivo de multilingual-e5-small, a la manera de SetFit, y exportación a ONNX cuantizado.

Por qué: el clasificador usaba el embedding CONGELADO de e5 (entrenado para buscar textos, no para vender
vestidos) con una regresión logística encima. Eso supone que las intenciones ya se separan con una línea en
ese espacio genérico. SetFit primero ajusta el propio modelo con aprendizaje contrastivo —acerca frases de
la misma intención y aleja las de intenciones distintas— y después entrena la regresión sobre el espacio ya
ajustado. Funciona con pocas frases por clase (aquí, unas 25).

Diferencias con la librería setfit, a propósito:
- Pérdida contrastiva supervisada por lotes (SupCon: 8 intenciones × 4 frases) en vez de pares sueltos
  con similitud coseno. Mismo objetivo, menos pasos.
- Se congela la tabla de palabras (96 de los 118 millones de parámetros): baja la memoria del build y
  evita que el modelo olvide el vocabulario que no aparece en los datos.
- Un solo modelo para las dos tareas (intención del bot y comercial): cada lote sale de una sola tarea, así
  que las etiquetas de una nunca se comparan con las de la otra.

Se ejecuta en una etapa aparte del Dockerfile, porque necesita PyTorch y la imagen final no lo lleva. La
imagen final solo recibe model.onnx (int8) y tokenizer.json, y los usa con onnxruntime (modelo.EmbedderOnnx).
La búsqueda RAG y las fichas siguen con el e5 sin ajustar: el ajuste es para clasificar, no para buscar.

    python -m app.setfit        # → $SETFIT_OUT/{model.onnx, tokenizer.json, base.txt}
"""
from __future__ import annotations

import os
import random
import time
from collections import defaultdict

import torch
import torch.nn.functional as F

from . import datos

BASE = os.environ.get("SETFIT_BASE", "intfloat/multilingual-e5-small")
OUT = os.environ.get("SETFIT_OUT", "/out/setfit")
PASOS = int(os.environ.get("SETFIT_PASOS", "300"))
P, K = 8, 4          # intenciones por lote × frases por intención
TAU = 0.1            # temperatura de la pérdida contrastiva
LR = 2e-5
MAXLEN = 64


def tareas() -> dict[str, list[tuple[str, str]]]:
    return {"intencion": [(e.texto, e.intencion) for e in datos.ejemplos_intencion()],
            "comercial": datos.ejemplos_comercial()}


def lote(por_etiqueta: dict[str, list[str]], rng: random.Random) -> list[tuple[str, str]]:
    validas = [e for e, ts in por_etiqueta.items() if len(ts) >= 2]
    out = []
    for et in rng.sample(validas, min(P, len(validas))):
        out += [(t, et) for t in rng.sample(por_etiqueta[et], min(K, len(por_etiqueta[et])))]
    return out


def promedio(h: torch.Tensor, m: torch.Tensor) -> torch.Tensor:
    m = m.unsqueeze(-1).to(h.dtype)
    return (h * m).sum(1) / m.sum(1).clamp(min=1e-9)


def supcon(z: torch.Tensor, y: torch.Tensor) -> torch.Tensor:
    """Pérdida contrastiva supervisada (Khosla et al., 2020): cada frase debe parecerse más a las de su
    misma intención que a todas las demás del lote."""
    yo = torch.eye(len(y), dtype=torch.bool)
    pos = (y[:, None] == y[None, :]) & ~yo
    logits = (z @ z.T / TAU).masked_fill(yo, float("-inf"))
    logp = logits - torch.logsumexp(logits, dim=1, keepdim=True)
    return -(logp.masked_fill(~pos, 0).sum(1) / pos.sum(1).clamp(min=1)).mean()


def main():
    if PASOS <= 0:  # SETFIT_PASOS=0: no se entrena; la carpeta vacía deja al agente con el e5 sin ajustar
        os.makedirs(OUT, exist_ok=True)
        print("setfit: desactivado (SETFIT_PASOS=0)")
        return
    from transformers import AutoModel, AutoTokenizer, get_linear_schedule_with_warmup

    t0 = time.time()
    torch.manual_seed(0)
    rng = random.Random(0)
    torch.set_num_threads(max(1, os.cpu_count() or 1))
    tok = AutoTokenizer.from_pretrained(BASE)
    modelo = AutoModel.from_pretrained(BASE)
    modelo.embeddings.word_embeddings.weight.requires_grad_(False)
    entrenables = [p for p in modelo.parameters() if p.requires_grad]
    print(f"setfit: {BASE}, {sum(p.numel() for p in entrenables) / 1e6:.1f} M parámetros entrenables, {PASOS} pasos")

    grupos = {}
    for nombre, filas in tareas().items():
        por = defaultdict(list)
        for t, e in filas:
            por[e].append(t)
        grupos[nombre] = por
        print(f"setfit: tarea {nombre}: {len(filas)} frases, {len(por)} intenciones")

    opt = torch.optim.AdamW(entrenables, lr=LR, weight_decay=0.01)
    sched = get_linear_schedule_with_warmup(opt, int(PASOS * 0.1), PASOS)
    modelo.train()
    nombres = list(grupos)
    media = 0.0
    for paso in range(PASOS):
        lt = lote(grupos[nombres[paso % len(nombres)]], rng)
        ids = {e: k for k, e in enumerate(sorted({e for _, e in lt}))}
        enc = tok(["query: " + t for t, _ in lt], padding=True, truncation=True, max_length=MAXLEN, return_tensors="pt")
        z = F.normalize(promedio(modelo(**enc).last_hidden_state, enc["attention_mask"]), dim=-1)
        perdida = supcon(z, torch.tensor([ids[e] for _, e in lt]))
        perdida.backward()
        torch.nn.utils.clip_grad_norm_(entrenables, 1.0)
        opt.step()
        sched.step()
        opt.zero_grad()
        media = 0.9 * media + 0.1 * perdida.item() if paso else perdida.item()
        if paso % 50 == 0 or paso == PASOS - 1:
            print(f"setfit: paso {paso:4d}  pérdida {media:.3f}  ({time.time() - t0:.0f} s)")

    # Exportación: ONNX con la salida por token (el promedio y la normalización se hacen en numpy al usarlo,
    # igual que aquí) y cuantización int8 dinámica, como el e5 de Xenova que usa el resto del agente.
    from onnxruntime.quantization import QuantType, quantize_dynamic

    modelo.eval()

    class Salida(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask):
            return self.m(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state

    os.makedirs(OUT, exist_ok=True)
    fp32 = os.path.join(OUT, "model_fp32.onnx")
    ej = tok(["query: hola, cuánto cuesta el vestido"], return_tensors="pt")
    with torch.no_grad():
        torch.onnx.export(Salida(modelo), (ej["input_ids"], ej["attention_mask"]), fp32,
                          input_names=["input_ids", "attention_mask"], output_names=["last_hidden_state"],
                          dynamic_axes={"input_ids": {0: "lote", 1: "tokens"}, "attention_mask": {0: "lote", 1: "tokens"},
                                        "last_hidden_state": {0: "lote", 1: "tokens"}},
                          opset_version=17)
    quantize_dynamic(fp32, os.path.join(OUT, "model.onnx"), weight_type=QuantType.QInt8)
    os.remove(fp32)
    tok.save_pretrained(OUT)
    with open(os.path.join(OUT, "base.txt"), "w") as fh:
        fh.write(BASE)
    mb = os.path.getsize(os.path.join(OUT, "model.onnx")) / 2**20
    print(f"setfit: listo en {time.time() - t0:.0f} s, model.onnx {mb:.0f} MB (int8)")


if __name__ == "__main__":
    main()
