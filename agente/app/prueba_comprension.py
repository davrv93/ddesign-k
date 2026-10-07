"""El clasificador de comprensión (app/comprension.py) entrenado en el build: casos que ya fallaron o que no deben fallar.

    python3 -m app.prueba_comprension      # necesita index/comprension.npz (lo genera `python -m app.comprension entrenar`)
"""
from . import comprension as C
from .modelo import Embedder

CASOS = [   # (mensaje, debe detectar, NO debe detectar)
    ("Causa, ahi te va el voucher de la transferencia por el vestido Holly", {"ya_pago"}, set()),
    ("ya te yapee recien, te mando la captura", {"ya_pago"}, set()),
    ("ok gracias, eso es todo por hoy", {"despedida"}, set()),
    ("Hola, buenas tardes.", set(), {"despedida"}),
    ("buenas noches, una consulta", set(), {"despedida"}),
    ("¿de qué material es el Kendall?", {"tela"}, set()),
    ("quiero el Pandora en talla M", set(), {"despedida", "ya_pago"}),
    ("mandame otros modelos, no me gusta ese", set(), {"no_mostrar"}),    # no_mostrar no está activa: nunca bloquea fotos
]

fallos, total = [], 0
assert C.cargar(), "falta index/comprension.npz: corre `python -m app.comprension entrenar`"
vecs = Embedder()([m for m, _, _ in CASOS])
for (msg, si, no), v in zip(CASOS, vecs):
    d = C.detecta(v, msg)
    total += 1
    if not si <= d or (no & d):
        fallos.append(f"✗ «{msg}»: detecta {sorted(d)}, debía {sorted(si)} y nunca {sorted(no)}")
total += 1
if not C.ACTIVAS <= set(C.ETIQUETAS):
    fallos.append("✗ ACTIVAS nombra etiquetas que no existen")
print(f"comprensión clasificador entrenado: {total - len(fallos)}/{total} casos")
if fallos:
    print("\n".join(fallos))
    raise SystemExit(1)
