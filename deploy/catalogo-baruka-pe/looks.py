"""Paso 8: looks → agente/seed/looks.json ({archivo de foto: [códigos de las prendas que se ven en ella]}).

Un «look» es una foto en la que van DOS prendas del catálogo (la blusa y el pantalón que lleva la modelo); baruka.pe suele
publicar la misma foto bajo los dos productos. Si una clienta manda esa foto, el bot nombra las dos prendas con su precio
en vez de elegir una (13-10-2026, aprobado por el usuario sobre looks.pdf, menos dos casos: EXCLUIR).

Fuentes:
- salida/mapa_fotos.json: fotos idénticas publicadas bajo dos productos (`duplicada_real`, `look_con`).
- salida/recortes_resultado.json: casos de la prueba en que el bot nombró la otra prenda de la misma foto.

    python3 looks.py
"""
import json
import os

AQUI = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.normpath(os.path.join(AQUI, "..", "..", "agente", "seed", "looks.json"))
# (foto, código que NO va en esa foto): el usuario los revisó en looks.pdf y no son looks.
EXCLUIR = {("v78.jpg", "V86"), ("v87.jpg", "V77")}


def cod(archivo: str) -> str:
    return archivo[:-4].split("_")[0].upper()


def main():
    vigentes = {p["code"] for p in json.load(open(os.path.join(AQUI, "salida", "catalogo_oficial.json"), encoding="utf-8"))["productos"]}
    looks: dict[str, set] = {}

    def juntar(archivo, codigos):
        if any((archivo, c) in EXCLUIR for c in codigos):
            return
        s = looks.setdefault(archivo, set())
        s.update(c for c in codigos if c in vigentes)
        s.add(cod(archivo))

    mapa = json.load(open(os.path.join(AQUI, "salida", "mapa_fotos.json"), encoding="utf-8"))
    for f in mapa:
        if f.get("look_con"):
            juntar(f["archivo"], f["look_con"])
            for d in f.get("duplicada_real") or []:          # la misma foto bajo el otro producto
                juntar(d, [f["codigo"]])
    res_path = os.path.join(AQUI, "salida", "recortes_resultado.json")
    if os.path.exists(res_path):
        for r in json.load(open(res_path, encoding="utf-8")):
            if r.get("nivel") in ("exacto", "parecido") and r.get("codigo") and r["codigo"] != r["esperado"] and r["esperado"] in vigentes:
                juntar(r["archivo"], [r["codigo"]])
    out = {a: sorted(s) for a, s in sorted(looks.items()) if len(s) >= 2}
    with open(SEED, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    pares = {tuple(v) for v in out.values()}
    print(f"{len(out)} fotos de look, {len(pares)} pares de prendas → {SEED}")
    for a, v in out.items():
        print(f"  {a:12} {' + '.join(v)}")


if __name__ == "__main__":
    main()
