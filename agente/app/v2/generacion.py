"""Generación con un modelo local (spec §12, §23, §26): pone en palabras UNA frase del mensaje, nada más.

El código arma el mensaje y el modelo solo escribe lo que no puede salir de una plantilla:

- `recomendar`: el código escribe «Te recomiendo el *Nombre*.» (así la prenda de la foto siempre queda nombrada, sin
  depender del modelo) y el modelo escribe UNA frase con el motivo, desde los datos de la ficha.
- `preguntar`: el modelo escribe un acuse corto de lo que dijo la clienta («¡Qué bonito! 😊»).
- La pregunta que sigue la pone el código (`calidad.componer`), con la redacción de V1.

Todo pasa por el quality gate; si no pasa, se regenera con otra variante y, si tampoco, se cae a la plantilla de código.
Medido con qwen2.5:3b local (05-10-2026): el modelo solo no nombraba la prenda ni respetaba las reglas; con esta
división del trabajo sí. Por eso el modelo escribe una frase y no el mensaje entero.

Habla con cualquier servidor compatible con OpenAI (`/v1/chat/completions`): Ollama, llama-server, mlx_lm.server. No
usa ningún proveedor externo."""
from __future__ import annotations

import re
from typing import Callable

from .calidad import componer

SISTEMA = (
    "Eres Rosmary, asesora de Baruka Design, una tienda peruana de ropa de mujer. Escribes por WhatsApp.\n"
    "Español peruano neutro, cálido y natural, a lo sumo un emoji. Escribes SOLO lo que se te pide.\n"
    "Reglas estrictas:\n"
    "- Usa únicamente los datos que te doy. No inventes telas, colores, tallas, fechas, precios, descuentos ni plazos.\n"
    "- No saludes, no te presentes y no menciones tu nombre.\n"
    "- No hagas ninguna pregunta: la pregunta la agrega el sistema.\n"
    "- Sin comillas, sin explicaciones, sin traducciones."
)
TAREA_RECOMENDAR = (
    "TAREA: escribe UNA sola frase de máximo {palabras} palabras que diga por qué esta prenda le conviene a la clienta, "
    "usando solo los datos de la prenda y lo que ella contó. No repitas el nombre de la prenda.\n"
    "Ejemplo: «Tiene un corte largo con escote en V, ideal para una boda de noche.»"
)
TAREA_ACUSE = (
    "TAREA: escribe un acuse muy corto (máximo {palabras} palabras) de lo que la clienta acaba de decir, con sus mismas palabras.\n"
    "Ejemplos: «¡Qué bonito! 😊» · «¡Anotado!» · «¡Perfecto, el 20 de octubre!» · «¡Mucho gusto!»"
)
PALABRAS = (20, 12)       # variante 0 y 1 (regeneración: más corto)
PALABRAS_ACUSE = (6, 3)


def _chat_openai(url: str, timeout_s: float):
    import httpx

    def llamar(modelo: str, mensajes: list[dict], temperatura: float, max_tokens: int) -> str:
        r = httpx.post(url, timeout=timeout_s, json={
            "model": modelo, "messages": mensajes, "temperature": temperatura, "max_tokens": max_tokens, "stream": False})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    return llamar


def limpiar(texto: str) -> str:
    """Quita lo que un modelo pequeño suele añadir: comillas, etiquetas, bloques de razonamiento y preguntas."""
    t = re.sub(r"<think>.*?</think>", "", texto or "", flags=re.S).strip()
    t = re.sub(r"^(respuesta|mensaje|apertura|frase)\s*:\s*", "", t, flags=re.I).strip().strip('"“”«»').strip()
    frases = re.split(r"(?<=[.!?])\s+|\n+", t)
    return " ".join(f.strip() for f in frases if f.strip() and "?" not in f and "¿" not in f).strip()


def _una_frase(t: str) -> str:
    """Primera frase completa (con su signo final)."""
    m = re.match(r"\s*([^.!?]*[.!?]+(?:\s*[^\w\s.!?¡¿]+)?)", t)       # con el emoji que cierre la frase
    return (m.group(1) if m else t).strip()


class LlmLocalGeneracion:
    """Redacta con un modelo local lo que la plantilla no puede; el nombre de la prenda y la pregunta los pone el código."""
    nombre = "llm-local"

    def __init__(self, url: str, modelo: str, timeout_s: float = 5.0,
                 nombre_de: Callable[[str], str | None] | None = None,
                 ficha_de: Callable[[str], dict] | None = None, llamar: Callable | None = None,
                 acciones: tuple[str, ...] = ("recomendar", "preguntar")):
        self.url = url
        self.modelo = modelo
        self.nombre_de = nombre_de or (lambda c: None)
        self.ficha_de = ficha_de or (lambda c: {})
        self._llamar = llamar or _chat_openai(url, timeout_s)
        self.acciones = acciones                   # las acciones que este modelo redacta; el resto, la plantilla

    def atiende(self, plan: dict) -> bool:
        return plan.get("accion") in self.acciones

    def _datos(self, plan: dict, contexto: dict | None) -> str:
        conv = (contexto or {}).get("conversation") or {}
        sab = ", ".join(h for h in plan.get("hechos") or [] if h) or "nada todavía"
        lineas = [f"LO QUE SABEMOS DE LA CLIENTA: {sab}", f"LO QUE ACABA DE DECIR LA CLIENTA: {conv.get('last_user_message') or ''}"]
        prod = plan.get("producto")
        if prod:
            f = self.ficha_de(prod) or {}
            datos = [f"{k}: {f[k]}" for k in ("categoria", "color", "detalle", "tejido") if f.get(k)]
            lineas.append("DATOS DE LA PRENDA (los únicos que puedes usar): " + " | ".join(datos or ["sin más datos"]))
        return "\n".join(lineas)

    def redactar(self, plan: dict, variante: int = 0, contexto: dict | None = None) -> str:
        v = min(variante, len(PALABRAS) - 1)
        accion, prod = plan.get("accion"), plan.get("producto")
        if accion == "recomendar" and prod:
            tarea = TAREA_RECOMENDAR.format(palabras=PALABRAS[v])
        elif accion == "preguntar":
            tarea = TAREA_ACUSE.format(palabras=PALABRAS_ACUSE[v])
        else:
            raise ValueError(f"este modelo no redacta «{accion}»")
        mensajes = [{"role": "system", "content": SISTEMA},
                    {"role": "user", "content": self._datos(plan, contexto) + "\n\n" + tarea}]
        frase = _una_frase(limpiar(self._llamar(self.modelo, mensajes, 0.3 if v == 0 else 0.1, 60)))
        if accion == "recomendar":
            nombre = self.nombre_de(prod) or prod
            apertura = f"Te recomiendo el *{nombre}*." + (f" {frase}" if frase else "")
        else:
            apertura = frase
        return componer(apertura, plan)


class Encadenada:
    """Prueba los redactores en orden (el modelo local primero, la plantilla de código al final). Cada uno se
    regenera como máximo `intentos` veces; el control de calidad lo hace quien la llama. Un redactor que no atiende la
    acción del plan (`atiende`) se salta."""
    nombre = "encadenada"

    def __init__(self, motores: list, intentos: int = 2):
        self.motores = motores
        self.intentos = intentos

    def intentos_en_orden(self, plan: dict | None = None):
        for m in self.motores:
            if plan is not None and hasattr(m, "atiende") and not m.atiende(plan):
                continue
            n = self.intentos if getattr(m, "nombre", "") != "plantilla" else 1
            for variante in range(n):
                yield m, variante
