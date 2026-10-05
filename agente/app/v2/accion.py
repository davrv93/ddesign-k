"""Qué hizo V1 en un turno, en el vocabulario del plan de V2 (spec §11, §28.14).

V1 etiqueta casi todo como `accion: "responder"`: esa etiqueta no distingue «recomendó una prenda» de «hizo una
pregunta» ni de «contestó un dato». Para comparar V1 con V2 hay que mirar lo que V1 hizo de verdad: si mandó prendas con
foto, si solo preguntó, si contestó algo, si pasó a una asesora, si armó un pedido. Aquí está esa lectura, en un solo lugar."""
from __future__ import annotations

import re

# Flujos que escribe el código y que ningún modelo toca (spec §19): pedido, pago, cita, menú, aclaración de prenda.
FLUJOS_FIJOS = ("flujo_", "pide_cual", "espera_cual", "menu", "indaga_antes_de_ver", "catalogo_categorias",
                "fuera_de_giro", "referencia_dataset")
# Saludo, presentación y la invitación o el «Así te digo…» que V1 pone junto a su pregunta («Cuéntame y te ayudo»).
SALUDO = re.compile(r"^\W*(hola|holi|buen[oa]s?|bienvenid|soy\s+\w+|qu[eé] gusto|mucho gusto|as[ií]\s|cu[eé]ntame)", re.I)
CORTE = re.compile(r"(?<=[.!?])\s+|\n+")
MIN_PALABRAS = 4        # menos que eso es un acuse («¡Qué lindo!»), no una respuesta


def es_lamina(sugerencia: dict) -> bool:
    """La lámina de materiales de una prenda (sin título ni tallas) no es una recomendación."""
    return not sugerencia.get("titulo") and (sugerencia.get("pie") or "").lstrip().startswith("✨")


def tarjetas_de_prenda(res: dict) -> list[dict]:
    return [s for s in (res.get("sugerencias") or []) if not es_lamina(s)]


def es_flujo_fijo(res: dict) -> bool:
    """El turno lo resolvió un flujo de código: V2 no compite ahí."""
    modelo = (res.get("modelo_llm") or "").strip()
    if modelo.startswith(FLUJOS_FIJOS):
        return True
    if (res.get("accion") or "") not in ("responder", "codigo", "foto"):
        return True                      # pedido, asesora, catálogo, menú…: acciones del código
    if res.get("botones") or res.get("tallas") or res.get("confirmar_pedido") or res.get("categorias"):
        return True
    return (res.get("etapa") or "") == "venta_confirmada"


def contenido(texto: str) -> int:
    """Cuántas palabras dice el texto SIN contar saludos, presentaciones ni preguntas: lo que contesta o informa."""
    n = 0
    plano = re.sub(r"(\d)[.,](\d)", r"\1\2", texto or "")        # «330.00» no es un fin de frase
    for frase in CORTE.split(plano):
        if "?" in frase or "¿" in frase or SALUDO.match(frase):
            continue
        if frase.strip().endswith("!") or frase.lstrip().startswith("¡"):
            continue                                              # «¡Perfecto, el 20 de octubre!»: un acuse, no un dato
        n += len(re.findall(r"\w+", frase))
    return n


def accion_v1(res: dict) -> str:
    """recomendar | preguntar | responder | confirmar_pedido | pedir_asesora, según lo que V1 hizo."""
    accion = res.get("accion") or ""
    if accion == "pedido":
        return "confirmar_pedido"
    if accion == "asesora":
        return "pedir_asesora"
    if tarjetas_de_prenda(res) or accion in ("codigo", "foto"):
        return "recomendar"
    texto = res.get("respuesta") or ""
    if "?" in texto and contenido(texto) < MIN_PALABRAS:
        return "preguntar"
    return "responder"
