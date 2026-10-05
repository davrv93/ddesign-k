"""Context Builder (spec §3): el estado de la conversación en una forma compacta y estructurada.

No envía el historial completo: solo los últimos turnos, recortados. Lee la petición y la ficha de memoria que ya
guarda V1; no recalcula nada de negocio ni cambia la ficha."""
from __future__ import annotations

REQUERIDOS = ("ocasion", "horario", "fecha", "prenda", "talla")


class ContextBuilder:
    def __init__(self, max_turnos: int = 4, max_chars: int = 200):
        self.max_turnos = max_turnos
        self.max_chars = max_chars

    def construir(self, req) -> dict:
        mem = req.memoria or {}
        sab = mem.get("sabemos") or {}
        ultimos = [
            {"rol": t.rol, "texto": (t.texto or "")[: self.max_chars]}
            for t in (req.historial or [])[-self.max_turnos:]
        ]
        return {
            "conversation": {
                "stage": req.etapa or mem.get("etapa") or "prospeccion",
                "last_user_message": (req.mensaje or "")[: self.max_chars],
                "pending_question": mem.get("pendiente") or None,
                "turns_total": len(req.historial or []),
                "recent_turns": ultimos,
            },
            "customer": {
                "name": sab.get("nombre") or None,
                "temperature": mem.get("temperatura") or None,
            },
            "requirements": {k: sab.get(k) or None for k in ("ocasion", "fecha", "horario", "prenda", "talla", "presupuesto", "color")},
            "product": {
                "focus": mem.get("producto") or req.producto or None,
                "shown": list(mem.get("mostrados") or []),
            },
            "business": {
                "pedido_en_curso": req.producto or None,
                "talla_pedida": req.talla or None,
                "anuncio": bool(req.desde_anuncio),
            },
        }
