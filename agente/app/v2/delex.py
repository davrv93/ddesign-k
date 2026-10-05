"""Delexicalización: ningún hecho llega al modelo, y ninguno sale de él.

Cada dato que sale del backend (nombre de la prenda, precio, tallas, colores, nombre de la clienta, ocasión, fecha, enlace…) se
cambia por un token opaco, `§PRODUCTO_8f21§`, antes de que el texto pase por un modelo. El modelo ve y mueve tokens; no ve
«Vestido Pandora» ni «S/ 330.00». Después de la compuerta factual, el código vuelve a poner los valores reales.

El sufijo del token es un hash con sal distinta en cada turno: el modelo no puede adivinar ni memorizar un token. Un mismo
valor da siempre el mismo token dentro de un turno, así que «el *V31* Vestido Pandora» dos veces es un solo token que debe
aparecer las dos veces… o ninguna: por eso la compuerta cuenta apariciones por token."""
from __future__ import annotations

import hashlib
import re
import secrets

TOKEN_RE = re.compile(r"§([A-Z][A-Z0-9_]*)_([0-9a-f]{4})§")
SLOT_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")


class Protegidos:
    """Los datos protegidos de UN turno: valor real ↔ token."""

    def __init__(self, sal: str | None = None):
        self.sal = sal if sal is not None else secrets.token_hex(4)
        self.mapa: dict[str, str] = {}          # token → valor real
        self.tipos: dict[str, str] = {}         # token → tipo (PRODUCTO, PRECIO…)

    def token(self, tipo: str, valor: str) -> str:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", tipo):
            raise ValueError(f"tipo de dato inválido: {tipo!r}")
        h = hashlib.sha1(f"{self.sal}|{tipo}|{valor}".encode("utf-8")).hexdigest()[:4]
        t = f"§{tipo}_{h}§"
        self.mapa[t] = valor
        self.tipos[t] = tipo
        return t

    def proteger(self, texto: str, valores: dict[str, str]) -> str:
        """Cambia cada `{{SLOT}}` del texto por su token. Un slot sin valor es un error: no se deja un hueco."""
        def cambia(m: re.Match) -> str:
            slot = m.group(1)
            if slot not in valores or valores[slot] in (None, ""):
                raise KeyError(slot)
            return self.token(slot, str(valores[slot]))
        return SLOT_RE.sub(cambia, texto)

    def rellenar(self, texto: str) -> str:
        """Vuelve a poner los valores reales. Un token desconocido es un error: nunca sale un `§…§` al cliente."""
        def cambia(m: re.Match) -> str:
            t = m.group(0)
            if t not in self.mapa:
                raise KeyError(t)
            return self.mapa[t]
        return TOKEN_RE.sub(cambia, texto)

    def tokens_en(self, texto: str) -> list[str]:
        return [m.group(0) for m in TOKEN_RE.finditer(texto)]


def sin_tokens(texto: str) -> str:
    """El texto sin los tokens: lo que el modelo escribió de su cosecha."""
    return TOKEN_RE.sub(" ", texto)
