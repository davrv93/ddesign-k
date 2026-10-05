"""Context Builder (spec §3): el estado de la conversación en una forma compacta y estructurada.

No envía el historial completo: solo los últimos turnos, recortados. Lee la petición y la ficha de memoria que ya
guarda V1; no recalcula nada de negocio ni cambia la ficha.

`construir(req)` ve lo que llegó con la petición (la ficha de antes del turno). `construir(req, res)` ve además lo
que V1 acaba de entender en este turno (ficha actualizada, etapa, intención, prendas mostradas): es el que usa la
V2, porque decide con lo que la clienta acaba de decir. La intención sale del clasificador de V1 (local y Jev): la V2
no la recalcula, la reutiliza (spec §2)."""
from __future__ import annotations

from typing import Callable

REQUERIDOS = ("ocasion", "horario", "fecha", "prenda", "talla")


class ContextBuilder:
    def __init__(self, max_turnos: int = 4, max_chars: int = 200,
                 texto_pregunta: Callable[..., str] | None = None,
                 pide_ver: Callable[[str], bool] | None = None,
                 categoria_pedida: Callable[[str], str | None] | None = None):
        self.max_turnos = max_turnos
        self.max_chars = max_chars
        # mensaje → ¿pide ver prendas u otras opciones? La regla es de V1 (memoria.pide_ver): no se duplica.
        self.pide_ver = pide_ver
        # mensaje → la categoría de prenda que nombra («vestido»), con la regla de V1 (main.categoria_pedida).
        self.categoria_pedida = categoria_pedida
        # (tipo, memoria, mensaje, respuesta_de_v1) → el texto con el que V1 hace esa pregunta. La V2 no escribe sus
        # propias preguntas; con la respuesta de V1 a mano usa la redacción exacta que V1 ya eligió (una pregunta que V1
        # acaba de registrar como hecha cambia de redacción si se vuelve a pedir: «Y cuéntame, ¿ya tienes fecha?»).
        self.texto_pregunta = texto_pregunta

    def _pregunta(self, tipo: str | None, mem: dict, mensaje: str, respuesta: str = "") -> dict | None:
        if not tipo or self.texto_pregunta is None:
            return None
        try:
            texto = self.texto_pregunta(tipo, mem, mensaje, respuesta)
        except Exception:                      # una ficha incompleta no tira el turno
            return None
        return {"tipo": tipo, "texto": texto} if texto else None

    def construir(self, req, res: dict | None = None) -> dict:
        mem = (res or {}).get("memoria") or req.memoria or {}
        sab = mem.get("sabemos") or {}
        ultimos = [
            {"rol": t.rol, "texto": (t.texto or "")[: self.max_chars]}
            for t in (req.historial or [])[-self.max_turnos:]
        ]
        conv = {
            "stage": (res or {}).get("etapa") or req.etapa or mem.get("etapa") or "prospeccion",
            "last_user_message": (req.mensaje or "")[: self.max_chars],
            "pending_question": mem.get("pendiente") or None,
            "turns_total": len(req.historial or []),
            "recent_turns": ultimos,
        }
        if res is not None:
            conv["intent"] = (res.get("comercial") or {}).get("intent") or None
            conv["next_question"] = self._pregunta(res.get("siguiente_pregunta"), mem, req.mensaje or "", res.get("respuesta") or "")
            try:
                conv["wants_to_see"] = bool(self.pide_ver and self.pide_ver(req.mensaje or "")) or conv["intent"] == "comparacion"
            except Exception:
                conv["wants_to_see"] = False
            # Lo que V1 sacó de ESTE mensaje (nombre, ocasión, fecha…) y si respondió a la pregunta pendiente: es lo que se reconoce.
            lect = res.get("lectura") or {}
            conv["captured"] = {k: v for k, v in (lect.get("datos") or {}).items() if v}
            conv["responded"] = bool(lect.get("respondio")) and not lect.get("no_sabe")
            # «¿Tienes vestidos?»: pregunta por una categoría sin tener todavía una prenda en la conversación.
            conv["category_asked"] = None
            if self.categoria_pedida and "?" in (req.mensaje or "") and not (req.memoria or {}).get("producto") and not req.producto:
                try:
                    conv["category_asked"] = self.categoria_pedida(req.mensaje or "")
                except Exception:
                    pass
        perfil = getattr(req, "perfil", None)
        shown_antes = list((req.memoria or {}).get("mostrados") or []) if res is not None else list(mem.get("mostrados") or [])
        talla = self._pregunta("talla", mem, req.mensaje or "", (res or {}).get("respuesta") or "")
        return {
            "conversation": conv,
            "preguntas": {"talla": talla} if talla else {},
            "customer": {
                "name": sab.get("nombre") or None,
                "temperature": mem.get("temperatura") or None,
                "talla_perfil": mem.get("talla_perfil") or None,
                "history": perfil if isinstance(perfil, dict) and (perfil.get("pedidos") or perfil.get("productos")) else None,
            },
            "requirements": {k: sab.get(k) or None for k in ("ocasion", "fecha", "horario", "prenda", "talla", "presupuesto", "color")},
            "product": {
                "focus": mem.get("producto") or req.producto or None,
                # Con V1 ya corrido, «shown» es lo que se había mostrado ANTES de este turno: una prenda recién
                # puesta en foco todavía no la ha visto la clienta.
                "shown": shown_antes,
            },
            "business": {
                "pedido_en_curso": req.producto or None,
                "talla_pedida": req.talla or None,
                "anuncio": bool(req.desde_anuncio),
            },
        }
