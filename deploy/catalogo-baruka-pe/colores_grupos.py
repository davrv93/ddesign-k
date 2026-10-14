"""Paso 9: grupos de color → agente/seed/colores_grupos.json ({color normalizado: "sobrio" | "vibrante"}).

Es la propuesta de tablero_colores.py (la que el usuario revisó en tablero_colores.pdf, 13-10-2026) como dato: el agente la
usa cuando la tienda no mandó la suya. En jmdventas la tienda la cambia desde Ajustes → Colores (ajuste `colores_grupos`)
y el bot Go la manda en cada turno (`colores_grupos`); lo que mande la tienda manda sobre este archivo.

    python3 colores_grupos.py          # escribe el seed
    python3 colores_grupos.py --go     # imprime el mapa en Go (backend jmdventas, api/colores.go)
"""
import json
import os
import sys

from tablero_colores import ALIAS, POR_DEFINIR, SOBRIOS, VIBRANTES, pl

AQUI = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.normpath(os.path.join(AQUI, "..", "..", "agente", "seed", "colores_grupos.json"))


def grupos() -> dict[str, str]:
    out = {}
    for nombre in SOBRIOS:
        out[pl(nombre)] = "sobrio"
    for nombre in VIBRANTES:
        out[pl(nombre)] = "vibrante"
    for alias, real in ALIAS.items():          # «black» → negro, «camell» → camel…: mismo grupo que el color real
        if pl(real) in out:
            out[pl(alias)] = out[pl(real)]
    for nombre in POR_DEFINIR:                 # «clara», «italia», «color personalizado»: sin grupo
        out.setdefault(pl(nombre), "")
    return dict(sorted(out.items()))


def main():
    g = grupos()
    if "--go" in sys.argv:
        print("var coloresDefecto = map[string]string{")
        for k, v in g.items():
            print(f'\t{json.dumps(k)}: {json.dumps(v)},')
        print("}")
        return
    with open(SEED, "w", encoding="utf-8") as fh:
        json.dump({"_nota": "Propuesta de tablero_colores.py (13-10-2026). vibrante = «llamar la atención»; sobrio = «elegante y "
                            "discreta»; \"\" = sin grupo. La tienda la cambia en jmdventas (Ajustes → Colores).", **g},
                  fh, ensure_ascii=False, indent=1)
    n = {v: sum(1 for x in g.values() if x == v) for v in ("sobrio", "vibrante", "")}
    print(f"{len(g)} colores → {SEED}: {n['sobrio']} sobrios, {n['vibrante']} vibrantes, {n['']} sin grupo")


if __name__ == "__main__":
    main()
