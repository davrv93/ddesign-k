"""Prueba de reglas de ánimo y urgencia. Sin dependencias: `python3 -m app.prueba_animo`."""
from __future__ import annotations

from . import animo


def _casos():
    return [
        ("hola, queria ver vestidos", {}, 0.0, 0.0),
        ("me encanta, es hermoso! 😍", {}, 0.8, 0.0),
        ("esto es una estafa, son unos ladrones 😡", {}, -1.0, 0.0),
        ("no me gusta, muy caro 😒", {}, -0.8, 0.0),
        ("lo necesito urgente para hoy", {}, 0.0, 0.7),
        ("gracias!", {}, 0.4, 0.0),
        ("es para mañana, me urge", {}, 0.0, 0.7),
        ("quiero comprarlo", {"sabemos": {"fecha": "el sabado"}}, 0.0, 0.2),
    ]


def main() -> int:
    fallos = 0
    for texto, mem, sent, urg in _casos():
        r = animo.evaluar(texto, mem)
        ok = abs(r["sentimiento"] - sent) < 0.001 and abs(r["urgencia"] - urg) < 0.001
        if not ok:
            fallos += 1
            print(f"FALLO «{texto}»: {r} (esperaba sent={sent} urg={urg})")
    if fallos:
        print(f"{fallos} fallo(s)")
        return 1
    print(f"animo: {len(_casos())} casos OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
