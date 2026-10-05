"""Quality gate, generación con control y fallback de código (spec §12, §17, §18).

- ReglasCalidad valida un borrador contra el plan y contra los datos reales: precios del catálogo, prenda nombrada,
  sin promesas inventadas, una sola pregunta, y la pregunta cuando el plan la pide.
- PlantillaGeneracion redacta desde el plan con texto fijo de código (sin modelo). Tiene variantes: la 0 es completa y
  la 1 es corta, para la regeneración.
- Si ninguna variante pasa, sale FALLBACK: el texto seguro del spec, nunca un borrador que no pasó la validación."""
from __future__ import annotations

import re
from typing import Callable

PRECIO_RE = re.compile(r"S/\s?(\d+(?:[.,]\d{1,2})?)")
CODIGO_RE = re.compile(r"\bV\d{2}\b")
PROMESA_RE = re.compile(
    r"(entrega (en|para|el) |llega (en|el) |\d+\s*d[ií]as|env[ií]o gratis|gratis|descuento|\d+\s?%|"
    r"[uú]ltimas? unidades?|oferta)", re.I)
FALLBACK = "Déjame confirmarlo con una asesora para darte información exacta 😊"


class ReglasCalidad:
    nombre = "reglas"

    def __init__(self, precios: Callable[[], set[int]], nombres: Callable[[str], str | None] | None = None):
        self.precios = precios
        self.nombres = nombres or (lambda c: None)

    def evaluar(self, borrador: str, plan) -> dict:
        b = (borrador or "").strip()
        errores: list[str] = []
        if not b:
            errores.append("borrador vacío")
        precios = self.precios()
        for m in PRECIO_RE.findall(b):
            if int(round(float(m.replace(",", ".")))) not in precios:
                errores.append(f"precio no verificado: S/ {m}")
        codigos = set(CODIGO_RE.findall(b))
        if plan.producto:
            if codigos - {plan.producto}:
                errores.append("nombra otra prenda")
            nombre = self.nombres(plan.producto) or ""
            if plan.accion == "recomendar" and plan.producto not in codigos and nombre.lower() not in b.lower():
                errores.append("no nombra la prenda que va en la foto")
        if PROMESA_RE.search(b):
            errores.append("promesa o descuento no respaldado")
        if b.count("?") > 2:
            errores.append("más de una pregunta")
        if plan.accion == "preguntar" and "?" not in b:
            errores.append("el plan pide preguntar y no hay pregunta")
        return {"passed": not errores, "score": round(max(0.0, 1.0 - 0.25 * len(errores)), 2), "errors": errores}


class PlantillaGeneracion:
    """Texto desde el plan con plantillas fijas. Variante 0 completa; variante 1 corta (regeneración)."""
    nombre = "plantilla"

    def __init__(self, nombres: Callable[[str], str | None] | None = None):
        self.nombres = nombres or (lambda c: None)

    def redactar(self, plan: dict, variante: int = 0) -> str:
        accion, prod = plan.get("accion"), plan.get("producto")
        pregunta = (plan.get("pregunta") or {}).get("texto") or ""
        if accion == "recomendar" and prod:
            nombre = self.nombres(prod) or prod
            if variante == 0:
                return f"Te recomiendo el *{nombre}*." + (f"\n\n{pregunta}" if pregunta else "")
            return f"Te recomiendo el *{nombre}*."
        if accion == "preguntar":
            return ("Por ahora no tengo esa prenda disponible. ¿Te interesaría ver otra opción?" if variante == 0
                    else "¿Te interesaría ver otra opción?")
        if accion in ("pedir_asesora", "derivar"):
            return "Te paso con una asesora para ayudarte mejor."
        return "Déjame confirmarlo con una asesora para darte la información exacta."
