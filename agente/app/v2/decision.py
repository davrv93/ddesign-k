"""Motores de decisión (spec §5–8, «Jev style»).

Un motor de decisión recibe el estado estructurado y devuelve decisiones con opciones de un conjunto cerrado. Nunca
escribe texto para la clienta. Implementaciones intercambiables:

- ReglasDecision: determinista, sin modelo. Es la referencia y el respaldo.
- JevStyleDecision: un juez que responde JSON con enums cerrados (llama-server local con json_schema, o cualquier
  función que reciba el prompt y devuelva texto). Si su salida no valida, lanza ValueError y el motor cae a reglas.

El estado de herramientas que leen las reglas: `herramientas.stock` = {codigo: "online" | "sucursal" | ""} y
`herramientas.rag` = [codigos] (o no existe si aún no se buscó)."""
from __future__ import annotations

import json
import re
from typing import Callable

from .interfaces import Decision

OPCIONES = {
    "next_action": ("consultar_stock", "buscar_alternativa", "recomendar", "preguntar", "responder",
                    "pedir_asesora", "derivar"),
    "intent": ("purchase", "product_information", "support", "greeting", "other"),
}


class ReglasDecision:
    nombre = "reglas"

    def decide(self, estado: dict, decisiones: list[str]) -> list[Decision]:
        foco = (estado.get("product") or {}).get("focus")
        herr = estado.get("herramientas") or {}
        stock = herr.get("stock") or {}
        rag = herr.get("rag")                       # None = no buscado; [] = buscado y vacío
        out: list[Decision] = []
        if "intent" in decisiones:
            req = estado.get("requirements") or {}
            compra = bool(req.get("ocasion") or req.get("prenda") or foco)
            out.append(Decision("intent", "purchase" if compra else "other", 0.7))
        if "next_action" in decisiones:
            out.append(self._accion(foco, stock, rag))
        return out

    @staticmethod
    def _accion(foco, stock: dict, rag) -> Decision:
        if not foco:
            return Decision("next_action", "preguntar", 0.7)                 # no hay prenda: preguntar cuál
        if foco not in stock:
            return Decision("next_action", "consultar_stock", 0.9, arg=foco)  # hecho que falta: consultarlo
        if stock[foco] == "online":
            return Decision("next_action", "recomendar", 0.9, arg=foco)
        if stock[foco] == "sucursal":
            return Decision("next_action", "responder", 0.8)                  # solo en tienda física
        # Sin stock de la prenda en foco: buscar alternativas con stock, sin inventarlas.
        if rag is None:
            return Decision("next_action", "buscar_alternativa", 0.8)
        con_stock = next((c for c in rag if stock.get(c) == "online"), None)
        if con_stock:                                                          # una alternativa ya tiene stock: basta
            return Decision("next_action", "recomendar", 0.8, arg=con_stock)
        por_mirar = next((c for c in rag if c != foco and c not in stock), None)
        if por_mirar:
            return Decision("next_action", "consultar_stock", 0.8, arg=por_mirar)
        return Decision("next_action", "preguntar", 0.8)                      # sin alternativas: preguntar si le interesa otra


def _prompt(estado: dict, decisiones: list[str]) -> str:
    compacto = json.dumps(estado, ensure_ascii=False, default=str)[:3000]
    opciones = {d: list(OPCIONES[d]) for d in decisiones if d in OPCIONES}
    return (
        "Eres el motor de decisión de una tienda. Devuelve SOLO JSON, sin texto alrededor, con la forma "
        '{"decisiones":[{"decision":"<nombre>","choice":"<opción>","confianza":0.0}]}. '
        "Elige únicamente opciones de la lista; no inventes productos, precios ni stock.\n"
        f"OPCIONES: {json.dumps(opciones, ensure_ascii=False)}\n"
        f"ESTADO: {compacto}"
    )


def parsear(crudo: str, pedidas: list[str]) -> list[Decision]:
    """Valida la salida del juez. Cualquier desvío lanza ValueError: nada no validado llega al plan."""
    texto = re.sub(r"^```(?:json)?|```$", "", (crudo or "").strip(), flags=re.M).strip()
    try:
        js = json.loads(texto)
    except ValueError as e:
        raise ValueError(f"JSON inválido: {e}")
    items = js.get("decisiones") if isinstance(js, dict) else None
    if not isinstance(items, list):
        raise ValueError("falta la lista «decisiones»")
    vistas: dict[str, Decision] = {}
    for it in items:
        if not isinstance(it, dict):
            raise ValueError("decisión que no es objeto")
        nombre, choice = it.get("decision"), it.get("choice")
        if nombre not in OPCIONES:
            if nombre in pedidas:
                raise ValueError(f"decisión sin conjunto cerrado: {nombre}")
            continue
        if choice not in OPCIONES[nombre]:
            raise ValueError(f"opción fuera de lista en {nombre}: {choice!r}")
        try:
            conf = float(it.get("confianza"))
        except (TypeError, ValueError):
            raise ValueError("confianza no numérica")
        if not 0.0 <= conf <= 1.0:
            raise ValueError(f"confianza fuera de 0–1: {conf}")
        vistas[nombre] = Decision(nombre, choice, conf, fuente="juez")
    faltan = [d for d in pedidas if d in OPCIONES and d not in vistas]
    if faltan:
        raise ValueError(f"faltan decisiones: {faltan}")
    return [vistas[d] for d in pedidas if d in vistas]


class JevStyleDecision:
    nombre = "jev-style"

    def __init__(self, juzgar: Callable[[str], str]):
        self.juzgar = juzgar

    def decide(self, estado: dict, decisiones: list[str]) -> list[Decision]:
        return parsear(self.juzgar(_prompt(estado, decisiones)), decisiones)


def juez_llama(url: str, timeout_s: float) -> Callable[[str], str]:
    """Juez local: llama-server (OpenAI-compatible) en la misma red. Temperatura 0 y JSON pedido al servidor.
    No apunta a ningún proveedor externo. Sin probar contra un servidor vivo hasta la fase 3 (benchmark)."""
    import httpx

    def juzgar(prompt: str) -> str:
        r = httpx.post(f"{url.rstrip('/')}/v1/chat/completions", timeout=timeout_s, json={
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": 200,
            "response_format": {"type": "json_object"},
        })
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    return juzgar
