"""Action Plan (spec §11): qué hace el turno y con qué hechos. El generador de lenguaje recibe este plan; no lo
inventa. `validar` rechaza planes que dirían algo que el código no puede respaldar."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

ACCIONES = frozenset({
    "responder", "recomendar", "preguntar", "consultar_stock",
    "confirmar_pedido", "pedir_asesora", "derivar",
})
ACCIONES_CON_PRODUCTO = frozenset({"recomendar", "consultar_stock", "confirmar_pedido"})


@dataclass
class Plan:
    accion: str
    producto: str | None = None
    hechos: list[str] = field(default_factory=list)
    razon: str = ""
    pregunta: dict | None = None   # {"tipo": "talla", "texto": "¿Qué talla usas?"}

    def a_dict(self) -> dict:
        return asdict(self)


def validar(plan: Plan, codigos_con_stock: set[str]) -> list[str]:
    """Lista de errores. Vacía = el plan se puede redactar."""
    errores: list[str] = []
    if plan.accion not in ACCIONES:
        errores.append(f"acción desconocida: {plan.accion}")
    if plan.accion in ACCIONES_CON_PRODUCTO and not plan.producto:
        errores.append(f"«{plan.accion}» sin producto")
    if plan.producto and plan.producto not in codigos_con_stock:
        errores.append(f"producto {plan.producto} sin stock o inexistente")
    if plan.pregunta:
        texto = (plan.pregunta.get("texto") or "").strip()
        if "?" not in texto:
            errores.append("la pregunta no lleva signo de interrogación")
        if texto.count("?") > 1:
            errores.append("más de una pregunta en el mismo plan")
    return errores
