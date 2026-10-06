"""Puerta de datos (2.7): los datos visibles también pasan la compuerta.

``factual.py`` protege contra lo que inventa el modelo; aquí se protege lo
que escribieron personas o agentes y que entra como verdad: cada dato
visible (precio, plazo, condición) debe estar en su fuente, o queda marcado
y no se responde como hecho.

Donde vive cada dato:

- fichas del backend (precio, tallas, stock): fuente viva, la manda el código;
- ``seed/tienda.md``, ``seed/venta.json``, ``seed/pago.md``: lo que el LLM
  puede contar de la tienda (fuente declarada, la revisa la dueña);
- ``plantillas.yaml``: textos fijos SIN datos duros (los datos van en slots
  y Hechos, nunca en el texto). ``revisar_plantilla`` lo garantiza en pruebas.
"""
from __future__ import annotations

import re

# nombre → (origen, revisado_por_humano)
FUENTES: dict[str, tuple[str, bool]] = {
    "fichas": ("backend estructurado (precio, tallas, stock en vivo)", True),
    "tienda.md": ("seed/tienda.md, lo que la dueña contó de la tienda", False),
    "venta.json": ("seed/venta.json, método de venta del dueño", True),
    "pago.md": ("seed/pago.md, Yape y titular de la dueña", True),
    "plantillas": ("app/v2/plantillas.yaml, textos fijos sin datos duros", True),
}

DATO_RE = re.compile(r"S/\s*[\d.,]+|\d+\s*%|\b\d+\s*(d[ií]as?|semanas?|meses?|horas?|minutos?)\b", re.I)


def puerta_datos(nombre: str, fuente: str) -> dict:
    """¿Este dato visible tiene fuente? Sin fuente no se responde como hecho."""
    if not fuente or fuente not in FUENTES:
        return {"pasa": False, "motivo": f"sin_fuente: {nombre} no se responde como hecho"}
    origen, revisado = FUENTES[fuente]
    if not revisado:
        return {"pasa": False, "motivo": f"sin_revision: {nombre} ({origen}) no se responde como hecho"}
    return {"pasa": True, "motivo": origen}


def revisar_plantilla(texto: str) -> list[str]:
    """Datos duros en el texto fijo de una plantilla (deben ir en slots)."""
    t = (texto or "").replace("{{", "").replace("}}", "")
    t = re.sub(r"\{[a-z_]+\}", "", t)  # slots: no son datos
    return [f"dato en texto fijo: {m.group(0)}" for m in DATO_RE.finditer(t)][:5]


def auditar(plantillas: list[dict]) -> dict:
    """Informe sobre textos fijos: los que traen datos van sin fuente."""
    mal = {}
    for p in plantillas or []:
        for b in (p.get("bloques") or []) + ([p.get("texto")] if p.get("texto") else []):
            texto = b.get("texto") if isinstance(b, dict) else b
            probs = revisar_plantilla(texto or "")
            if probs and p.get("id"):
                mal[p["id"]] = probs
    return {"total": len(plantillas or []), "con_dato": mal}
