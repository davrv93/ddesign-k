"""Pruebas de rerank.elegir: la regla «no quita» y el gate de tiempo.
Sin red ni modelos (el puntuador es falso):

    python3 -m app.prueba_v2_rerank
"""
from __future__ import annotations

import app.rerank as R

fallos: list[str] = []
total = 0


def caso(nombre: str, obtenido, esperado) -> None:
    global total
    total += 1
    if obtenido != esperado:
        fallos.append(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


R._roto = False
R.ACTIVO = True  # noqa: el flag se lee en activo(); aquí se fuerza para probar sin entorno
_origen_activo = R.activo
R.activo = lambda: True  # noqa: E731

try:
    # El reranker solo reordenaba: aquí ELIGE al mejor logit.
    r = R.elegir("vestido de seda", ["seda", "algodon", "denim"], [0.0, 0.0, 0.0],
                 puntuar=lambda q, ds: [2.0, 0.5, -1.0])
    caso("elige mejor logit", (r["indice"], r["aceptado"]), (0, True))

    # Regla «no quita»: logit ~0 pero cobertura léxica total → se acepta igual.
    r = R.elegir("donde esta el registro", ["Registrar metrado"], [1.0],
                 puntuar=lambda q, ds: [0.05])
    caso("no quita lo léxico", (r["indice"], r["aceptado"], r["motivo"]), (0, True, "ok"))

    # Sin evidencia en ningún lado: abstención, el llamante conserva el léxico.
    r = R.elegir("criptomonedas", ["seda", "algodon"], [0.0, 0.0],
                 puntuar=lambda q, ds: [-3.0, -3.5])
    caso("bajo umbral no elige", (r["aceptado"], r["motivo"]), (False, "bajo_umbral"))

    # Logits bajos y parejos («¿dónde está?» vs «Registrar…»): duda y gana la cobertura.
    r = R.elegir("donde", ["A", "B"], [0.2, 0.9],
                 puntuar=lambda q, ds: [0.1, 0.12])
    caso("empate lo rompe cobertura", (r["indice"], r.get("duda")), (1, True))

    # Gate de tiempo: si ya se midió lento, ni se intenta.
    R._ms_por_candidato = 900.0
    r = R.elegir("x", ["a"] * 20, [0.0] * 20, max_ms=3000,
                 puntuar=lambda q, ds: (_ for _ in ()).throw(AssertionError("no debe llamarse")))
    caso("gate estima y salta", r["motivo"], "fuera_de_tiempo_estimado")
    R._ms_por_candidato = None
finally:
    R.activo = _origen_activo

print(f"rerank-elegir: {total - len(fallos)}/{total} ok")
if fallos:
    print("\n".join(fallos))
    raise SystemExit(1)
