"""Integridad de la tabla de respuestas (app/respuestas.py). Pura, sin modelos:

    python3 -m app.prueba_respuestas

Una errata en la tabla (un id repetido, una exclusión que nombra una regla que no existe) no rompe nada a la vista: la
regla simplemente deja de aplicarse. Aquí falla el build.
"""
from . import respuestas as R

fallos: list[str] = []
total = 0


def caso(nombre: str, ok: bool) -> None:
    global total
    total += 1
    if not ok:
        fallos.append("✗ " + nombre)


ids = [r.id for r in R.TABLA]
caso("ids únicos", len(ids) == len(set(ids)))
caso("grupos conocidos", all(r.grupo in ("encabezado", "dato_prenda", "dato_tienda", "acuse") for r in R.TABLA))
caso("cada regla dice cuándo dispara", all(r.cuando.strip() for r in R.TABLA))
for origen, quita in R.EXCLUYE.items():
    caso(f"EXCLUYE: «{origen}» existe", origen in ids)
    for q in quita:
        caso(f"EXCLUYE: «{origen}» → «{q}» existe", q in ids)
for s in R.SOLO:
    caso(f"SOLO: «{s}» existe", s in ids)
for e in R.EXPLICITO:
    caso(f"EXPLICITO: «{e}» existe", e in ids)
caso("ninguna regla se excluye a sí misma", all(o not in q for o, q in R.EXCLUYE.items()))
caso("las de corte son del encabezado", all(r.grupo == "encabezado" for r in R.TABLA if r.corta))
caso("tope de datos razonable", 1 <= R.MAX_DATOS <= 4)
caso("la tabla se imprime", R.tabla_md().count("\n") == len(R.TABLA) + 1)

print(f"respuestas tabla de decisión: {total - len(fallos)}/{total} casos")
if fallos:
    print("\n".join(fallos))
    raise SystemExit(1)
