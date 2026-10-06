"""Pruebas del léxico BM25F + RRF (v2/lexico.py): plegado, raíz ligera,
códigos intactos, tolerancia a faltas, pesos por campo y fusión con el
vector a peso bajo. Sin red ni modelos:

    python3 -m app.prueba_v2_lexico
"""
from __future__ import annotations

from .v2.lexico import (
    Indice,
    campos_de_ficha,
    plegar,
    raiz,
    rrf,
    tokenizar,
)

fallos: list[str] = []
total = 0


def caso(nombre: str, obtenido, esperado) -> None:
    global total
    total += 1
    if obtenido != esperado:
        fallos.append(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


def mini_indice() -> Indice:
    ix = Indice()
    ix.agregar("V24", {"nombre": "Vestido Pandora", "codigo": "V24",
                       "categoria": "vestido fiesta", "descripcion": "seda con encaje"})
    ix.agregar("V25", {"nombre": "Conjunto Midi", "codigo": "V25",
                       "categoria": "conjunto casual", "descripcion": "algodon fresco"})
    ix.agregar("V26", {"nombre": "Mini Falda", "codigo": "V26",
                       "categoria": "falda casual", "descripcion": "denim juvenil"})
    return ix


# --- plegado y raíz ---
caso("pliega tildes", plegar("Cómo registró"), "como registro")
caso("raiz plural", raiz("vestidos"), "vestido")
caso("raiz gerundio", raiz("registrando"), "registr")
caso("raiz codigo intacto", raiz("v24"), "v24")
caso("mini no se toca", raiz("mini"), "mini")
caso("midi no se toca", raiz("midi"), "midi")
caso("tokens codigo", tokenizar("¿Tienen el V24?"), ["tienen", "el", "v24"])

# --- búsqueda ---
ix = mini_indice()
top = [d for d, _ in ix.buscar("pandora")]
caso("nombre exacto primero", top[0] if top else None, "V24")
top = [d for d, _ in ix.buscar("V25")]
caso("código exacto primero", top[0] if top else None, "V25")
top = [d for d, _ in ix.buscar("vestido pandora")]
caso("campo nombre pesa más", top[0] if top else None, "V24")
top = [d for d, _ in ix.buscar("vestdo pandora")]
caso("tolera falta (vestdo)", top[0] if top else None, "V24")
top = [d for d, _ in ix.buscar("midi")]
caso("midi no trae mini", top and top[0] == "V25" and "V26" not in top, True)
top = [d for d, _ in ix.buscar("mini")]
caso("mini no trae midi", top and top[0] == "V26" and "V25" not in top, True)
caso("cobertura total", ix.cobertura("vestido pandora", "V24"), 1.0)
caso("cobertura parcial", ix.cobertura("vestido pandora seda roja", "V24") < 1.0, True)

# --- RRF: el vector a peso bajo no cuela ruido por encima del léxico ---
lex = ["V24", "A", "B"]
vec = ["VX", "V24", "A"]  # VX solo existe en el vector: ruido puro
f = [d for d, _ in rrf([(lex, 1.0), (vec, 0.1)])]
caso("rrf bajo hunde ruido", f[0] == "V24" and f.index("VX") > f.index("B"), True)
igual = [d for d, _ in rrf([(lex, 1.0), (vec, 1.0)])]
caso("rrf iguales cuela ruido", igual.index("VX") < igual.index("B"), True)


class F:
    codigo = "V24"
    nombre = "Vestido Pandora"
    categoria = "vestido"
    color = "vino"
    detalle = "seda"
    tejido = "seda"


caso("campos ficha codigo", campos_de_ficha(F())["codigo"], "V24")

print(f"lexico: {total - len(fallos)}/{total} ok")
if fallos:
    print("\n".join(fallos))
    raise SystemExit(1)
