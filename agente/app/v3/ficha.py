"""Ficha de la conversación V3: un solo dueño del estado.

Una ficha JSON por conversación que SOLO escribe V3. Es la memoria de V1 (`app/memoria.py`) más lo propio
de V3: el pedido en curso y las herramientas usadas. Reusa el esquema y la normalización de V1 a propósito,
para que el bot Go siga leyendo `sabemos`, `pendiente`, `producto`, `mostrados` y `cita` igual mientras dure
la transición; cuando V3 esté solo, el bot Go podrá tratar la ficha como JSON opaco.

- `sabemos`, `pendiente`, `producto`, `mostrados`, `preguntado`, `cita`: de V1 (misma forma y reglas).
- `pedido`: {codigo, talla, confirmado} — el pedido que V3 está armando (la reserva la hace el backend).
- `herramientas`: nombres de las herramientas que V3 usó en el último turno (traza).
"""
from __future__ import annotations

from .. import memoria

# Un pedido vacío. `confirmado` pasa a True solo con un turno explícito de confirmación de la clienta.
PEDIDO_VACIO = {"codigo": "", "talla": "", "confirmado": False}
TALLAS = ("XS", "S", "M", "L", "XL", "XXL")


def nueva() -> dict:
    """Ficha vacía: la de V1 con los dos campos de V3."""
    f = memoria.nueva()
    f["pedido"] = dict(PEDIDO_VACIO)
    f["herramientas"] = []
    return f


def normalizar(m: dict | None) -> dict:
    """Acepta una ficha V3 o una memoria V1 (migración) y devuelve una ficha V3 completa y segura."""
    f = memoria.normalizar(m)
    f["pedido"] = _pedido(m.get("pedido") if isinstance(m, dict) else None)
    f["herramientas"] = _nombres(m.get("herramientas") if isinstance(m, dict) else None)
    return f


def _pedido(p) -> dict:
    out = dict(PEDIDO_VACIO)
    if not isinstance(p, dict):
        return out
    codigo = p.get("codigo")
    if isinstance(codigo, str) and codigo.strip():
        out["codigo"] = codigo.strip().upper()[:12]
    talla = p.get("talla")
    if isinstance(talla, str) and talla.strip().upper() in TALLAS:
        out["talla"] = talla.strip().upper()
    out["confirmado"] = bool(p.get("confirmado"))
    return out


def _nombres(lista) -> list[str]:
    if not isinstance(lista, list):
        return []
    return [x for x in lista if isinstance(x, str) and x][-20:]


def fijar_pedido(f: dict, codigo: str = "", talla: str = "", confirmado: bool | None = None) -> dict:
    """Actualiza el pedido en curso sin pisar lo que no cambia."""
    p = f.setdefault("pedido", dict(PEDIDO_VACIO))
    if codigo:
        p["codigo"] = codigo.strip().upper()[:12]
    if talla:
        p["talla"] = talla.strip().upper()[:4]
    if confirmado is not None:
        p["confirmado"] = bool(confirmado)
    return f


def migrar_v1(m: dict | None) -> dict:
    """Alias explícito de `normalizar` cuando lo que llega es una memoria de V1."""
    return normalizar(m)
