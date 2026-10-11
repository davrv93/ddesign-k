"""Las herramientas de V3: el modelo no lleva datos en el prompt, los pide.

Precios, tallas, telas, direcciones y plazos solo existen en la respuesta de una herramienta. Cada llamada
suma a `hechos` lo que el modelo ya puede afirmar; el validador comprueba el texto contra esos hechos.

Siete herramientas (plan §3): buscar_catalogo, stock, ficha_tecnica, enviar_fotos, crear_reserva,
agendar_cita, derivar_asesora. Reusan datos.py, stock.py, ficha_producto.py y venta.py; no inventan nada.

`Recolector` es la única puerta: `esquema()` da la lista para el LLM (formato OpenAI `tools`) y
`ejecutar(nombre, args)` corre la herramienta y actualiza `hechos`, `fotos` y el pedido en la ficha.
"""
from __future__ import annotations

import re

from .. import datos, ficha_producto, memoria, stock as stk, venta
from .validador import hechos_vacios

MAX_CATALOGO = 6

ESQUEMA = [
    {"type": "function", "function": {
        "name": "buscar_catalogo",
        "description": "Busca prendas del catálogo por texto libre (nombre, color, tipo) y/o categoría. "
                       "Devuelve códigos, nombres, precio y disponibilidad. Úsala antes de nombrar una prenda.",
        "parameters": {"type": "object", "properties": {
            "consulta": {"type": "string", "description": "lo que busca la clienta (p. ej. «vestido rojo de noche»)"},
            "categoria": {"type": "string", "description": "vestido | conjunto | blazer | falda | jeans | pantalón | polo | blusa | enterizo (opcional)"}},
            "required": ["consulta"]}}},
    {"type": "function", "function": {
        "name": "stock",
        "description": "Disponibilidad real por talla de una prenda (tienda virtual y sucursales). Úsala antes de "
                       "afirmar una talla o si está disponible.",
        "parameters": {"type": "object", "properties": {"codigo": {"type": "string"}}, "required": ["codigo"]}}},
    {"type": "function", "function": {
        "name": "ficha_tecnica",
        "description": "Ficha técnica de una prenda (silueta, largo, escote, mangas, espalda, tela). Úsala antes de "
                       "describir cómo es o de qué tela es.",
        "parameters": {"type": "object", "properties": {"codigo": {"type": "string"}}, "required": ["codigo"]}}},
    {"type": "function", "function": {
        "name": "enviar_fotos",
        "description": "Marca las fotos que se le enviarán a la clienta después de tu texto. Solo las prendas aquí "
                       "marcadas se envían; no prometas fotos de prendas que no marques.",
        "parameters": {"type": "object", "properties": {
            "codigos": {"type": "array", "items": {"type": "string"}}}, "required": ["codigos"]}}},
    {"type": "function", "function": {
        "name": "crear_reserva",
        "description": "Reserva una prenda en una talla para la clienta. Llámala SOLO cuando la clienta haya "
                       "confirmado el pedido en el turno anterior. El resumen #N lo arma el sistema.",
        "parameters": {"type": "object", "properties": {
            "codigo": {"type": "string"}, "talla": {"type": "string"}}, "required": ["codigo", "talla"]}}},
    {"type": "function", "function": {
        "name": "agendar_cita",
        "description": "Agenda una cita para probarse una prenda en el showroom. Valida día y hora (L–D 9–19 h, no en "
                       "refrigerio 13–14 h).",
        "parameters": {"type": "object", "properties": {
            "dia": {"type": "string", "description": "YYYY-MM-DD"}, "hora": {"type": "string", "description": "HH:MM"},
            "codigo": {"type": "string"}, "talla": {"type": "string"}}, "required": ["dia", "hora"]}}},
    {"type": "function", "function": {
        "name": "derivar_asesora",
        "description": "Deriva a una asesora humana cuando no puedes resolverlo o la clienta lo pide.",
        "parameters": {"type": "object", "properties": {"motivo": {"type": "string"}}, "required": []}}},
]


class Recolector:
    """Corre las herramientas de un turno y acumula lo que el modelo ya puede afirmar.

    - `fichas`: catálogo vivo (datos.Ficha).
    - `consultar_stock`: callable(codigos) -> {CODIGO: estado} (E.stock.consultar).
    - `pie`: callable(ficha, estado) -> texto del pie de foto (main._pie). Opcional.
    - `permitir_reserva`: False en sombra (la reserva la hace el flujo de V1).
    """

    def __init__(self, fichas, consultar_stock, pie=None, permitir_reserva: bool = False):
        self.fichas = list(fichas)
        self._stock = consultar_stock
        self._pie = pie
        self.permitir_reserva = permitir_reserva
        self.hechos = hechos_vacios()
        self.fotos: list[dict] = []
        self.usadas: list[str] = []
        self.reserva: dict | None = None
        self.cita: dict | None = None
        self.derivar = False

    # -- puerta -----------------------------------------------------------
    def esquema(self) -> list[dict]:
        return ESQUEMA

    def ejecutar(self, nombre: str, args: dict) -> dict:
        fn = getattr(self, "_t_" + nombre, None)
        if fn is None:
            return {"error": f"herramienta desconocida: {nombre}"}
        self.usadas.append(nombre)
        try:
            return fn(args or {})
        except Exception as e:  # una herramienta rota no tumba el turno: el modelo recibe el error
            return {"error": str(e)}

    # -- fichas -----------------------------------------------------------
    def _ficha(self, codigo: str):
        c = (codigo or "").strip().upper()
        return next((f for f in self.fichas if f.codigo.upper() == c), None)

    def _anota_prenda(self, f, st: dict | None = None) -> dict:
        self.hechos["codigos"].add(f.codigo.upper())
        if f.precio is not None:
            self.hechos["precios"].add(round(float(f.precio), 2))
        if st is None:
            st = (self._stock([f.codigo]) or {}).get(f.codigo.upper(), {})
        disp, agot = stk.tallas_online(st)
        for t in list(disp) + list(agot):
            self.hechos["tallas"].add(t.upper())
        return {"codigo": f.codigo, "nombre": f.nombre, "precio": f.precio,
                "tallas": disp, "agotadas": agot, "disponible": stk.estado(st)}

    # -- herramientas -----------------------------------------------------
    def _t_buscar_catalogo(self, args: dict) -> dict:
        consulta = (args.get("consulta") or "").lower()
        cat = (args.get("categoria") or "").lower().strip()
        palabras = [p for p in consulta.replace(",", " ").split() if len(p) >= 3]
        out = []
        for f in self.fichas:
            if cat and f.categoria.lower() != cat:
                continue
            texto = f"{f.codigo} {f.nombre} {f.categoria} {f.color} {f.detalle}".lower()
            if palabras and not any(p in texto for p in palabras):
                continue
            out.append(f)
            if len(out) >= MAX_CATALOGO:
                break
        if not out:  # sin coincidencia por texto, devuelve la categoría entera (si la pidió)
            out = [f for f in self.fichas if cat and f.categoria.lower() == cat][:MAX_CATALOGO]
        codigos = [f.codigo for f in out]
        estados = self._stock(codigos) if codigos else {}
        return {"prendas": [self._anota_prenda(f, estados.get(f.codigo.upper())) for f in out]}

    def _t_stock(self, args: dict) -> dict:
        f = self._ficha(args.get("codigo", ""))
        if f is None:
            return {"error": "no conozco ese código"}
        return self._anota_prenda(f)

    def _t_ficha_tecnica(self, args: dict) -> dict:
        f = self._ficha(args.get("codigo", ""))
        if f is None:
            return {"error": "no conozco ese código"}
        self.hechos["codigos"].add(f.codigo.upper())
        det = ficha_producto.ficha(f.codigo) or {}
        tela = venta.tela(f.codigo, f.detalle)
        if tela:
            self.hechos["telas"].add(re.sub(r"^tela\s+", "", tela.strip().lower()))
        for v in det.values():
            if isinstance(v, str) and v.strip():
                self.hechos["telas"].add(v.strip().lower())
        return {"codigo": f.codigo, "nombre": f.nombre, "ficha": det, "tela": tela}

    def _t_enviar_fotos(self, args: dict) -> dict:
        out = []
        for c in (args.get("codigos") or [])[:MAX_CATALOGO]:
            f = self._ficha(c)
            if f is None or not f.imagen:
                continue
            pie = self._pie(f, self._stock([f.codigo]).get(f.codigo.upper(), {})) if self._pie else f"{f.codigo} {f.nombre}"
            out.append({"codigo": f.codigo, "nombre": f.nombre, "imagen": f.imagen, "pie": pie})
            self.hechos["codigos"].add(f.codigo.upper())
            self.hechos["fotos"].add(f.codigo.upper())
            if f.precio is not None:
                self.hechos["precios"].add(round(float(f.precio), 2))
        self.fotos = out
        return {"enviadas": [x["codigo"] for x in out]}

    def _t_crear_reserva(self, args: dict) -> dict:
        if not self.permitir_reserva:
            return {"error": "la reserva todavía la maneja el flujo base"}
        f = self._ficha(args.get("codigo", ""))
        talla = (args.get("talla") or "").strip().upper()
        if f is None or not talla:
            return {"error": "faltan código o talla"}
        self.reserva = {"codigo": f.codigo, "talla": talla}
        self.hechos["codigos"].add(f.codigo.upper())
        return {"ok": True, "codigo": f.codigo, "talla": talla}

    def _t_agendar_cita(self, args: dict) -> dict:
        dia, hora = (args.get("dia") or "").strip(), (args.get("hora") or "").strip()
        err = memoria.validar_cita(dia, hora, memoria.ahora_lima())
        if err:
            return {"ok": False, "motivo": err}
        self.cita = {"dia": dia, "hora": hora, "codigo": (args.get("codigo") or "").upper(),
                     "talla": (args.get("talla") or "").upper()}
        return {"ok": True, **self.cita}

    def _t_derivar_asesora(self, args: dict) -> dict:
        self.derivar = True
        return {"ok": True, "motivo": (args.get("motivo") or "").strip()}
