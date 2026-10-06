"""Pruebas de la híbrida léxico+vector (v2/rag.py): fusión, filtro por
categoría, el reranker elige con «no quita» y ningún fallo tira el turno:

    python3 -m app.prueba_v2_rag
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from .v2 import rag as R

fallos: list[str] = []
total = 0


def caso(nombre: str, obtenido, esperado) -> None:
    global total
    total += 1
    if obtenido != esperado:
        fallos.append(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


FICHAS = [
    NS(codigo="V24", nombre="Vestido Pandora", categoria="vestido", color="vino",
       detalle="seda con encaje", tejido="seda"),
    NS(codigo="V25", nombre="Conjunto Midi", categoria="conjunto", color="negro",
       detalle="algodon fresco", tejido="algodon"),
    NS(codigo="V26", nombre="Mini Falda", categoria="falda", color="azul",
       detalle="denim juvenil", tejido="denim"),
]
VEC_RUIDO = ["V26", "V25", "V24"]  # el vector pone primero lo que no piden


def elige_bien(q, textos, cobs):
    return {"indice": 0, "aceptado": True, "motivo": "ok", "puntaje": 0.9,
            "margen_logit": 1.0, "ms": 5}


cods, det = R.hibrida("vestido pandora de seda", FICHAS, VEC_RUIDO, elegir_fn=elige_bien)
caso("hibrida gana al vector", cods[0], "V24")
caso("fuente hibrida", det["fuente"], "hibrida")
caso("detalle para traza", R.ultimo_detalle()["motivo"], "ok")
caso("lex_top registrado", det["lex_top"][0], "V24")

# Sin reranker: la fusión sola ya ordena por el léxico.
cods, det = R.hibrida("vestido pandora de seda", FICHAS, VEC_RUIDO)
caso("fusión sin reranker", cods[0], "V24")
caso("rerank apagado en traza", det["rerank"]["motivo"], "apagado")

# La categoría pedida filtra como en V1.
cods, _ = R.hibrida("vestido", FICHAS, VEC_RUIDO, permitidos={"V24"})
caso("respeta permitidos", cods, ["V24"])

# El reranker que duda no quita lo que el léxico acertó.
cods, det = R.hibrida("vestido pandora", FICHAS, VEC_RUIDO,
                      elegir_fn=lambda q, t, c: {"indice": 2, "aceptado": False,
                                                 "motivo": "bajo_umbral"})
caso("duda conserva fusión", cods[0], "V24")

# El turno que no usa el RAG no lee el detalle del anterior.
cods, _ = R.hibrida("vestido pandora de seda", FICHAS, VEC_RUIDO)
caso("consume sin reutilizar", (R.tomar_detalle() or {}).get("fuente") == "hibrida"
      and R.tomar_detalle() is None, True)

print(f"rag-hibrida: {total - len(fallos)}/{total} ok")
if fallos:
    print("\n".join(fallos))
    raise SystemExit(1)
