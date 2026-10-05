"""El modelo como REALIZADOR LINGÜÍSTICO, no como vendedor.

Flujo (cada paso lo hace código, salvo el marcado):

    plan de V2 ──► Selector (plantillas.py) ──► datos del backend ──► tokens §PRODUCTO_8f21§ (delex.py)
                                                                      │
                                         texto base ya correcto ◄─────┘
                                                │
                              ┌─────────────────┼──────────────────────┐
                              ▼                 ▼                      ▼
                   RealizadorBase     RealizadorVariantes     RealizadorReescritura      ← el MODELO entra aquí (Qwen3-1.7B)
                   (sin modelo)       elige entre variantes    reescribe conservando tokens
                              └─────────────────┼──────────────────────┘
                                                ▼
                                 CompuertaFactual (factual.py, determinista)
                                                ▼
                          rellenar los datos reales ──► texto final   (si la compuerta rechaza: el texto base)

El modelo nunca ve un dato real: ve tokens. No puede introducir un precio, una talla, un color ni una prenda, porque la compuerta
rechaza todo lo que no estaba en el texto base. Y si el modelo falla, tarda o desvaría, el mensaje que sale es el base, que
escribió una persona.

Qwen3 piensa antes de contestar. Aquí se apaga (`reasoning_effort: none` en Ollama, `enable_thinking: false` en llama-server) y
se quita cualquier bloque <think> que se cuele: con el razonamiento encendido se gasta el tope de tokens y la respuesta sale vacía."""
from __future__ import annotations

import json
import re
import time
from typing import Callable

from .delex import Protegidos
from .factual import CompuertaFactual, Esperado
from .plantillas import Mensaje, SinPlantilla, Selector

SISTEMA_REESCRITURA = (
    "Eres un realizador lingüístico de mensajes de WhatsApp de una tienda de ropa peruana. Recibes un mensaje que ya es correcto y "
    "lo reescribes con otras palabras: natural, amable y comercial, sin exagerar.\n"
    "Reglas estrictas:\n"
    "- Conserva literalmente cada token §...§, exactamente una vez, sin cambiarlo ni traducirlo.\n"
    "- No agregues nombres, precios, tallas, colores, telas, prendas, descuentos, stock, plazos ni ningún hecho nuevo.\n"
    "- Mantén el mismo número de párrafos (separados por una línea en blanco) y la misma pregunta final.\n"
    "- Máximo un emoji. Español peruano natural.\n"
    "- Responde SOLO con el mensaje reescrito, sin comillas ni explicaciones."
)
SISTEMA_VARIANTES = (
    "Eres un selector de estilo para mensajes de WhatsApp de una tienda de ropa peruana. NO escribes texto: eliges, para cada parte, "
    "el número de la variante que mejor va con lo que acaba de escribir la clienta (su tono, su ánimo).\n"
    "Responde SOLO con un JSON como {\"0.cuerpo\": 1, \"1.pregunta\": 0}, con las claves que te doy y un número válido en cada una."
)


class ClienteLLM:
    """Servidor compatible con OpenAI (Ollama, llama-server). Qwen3 sin razonamiento."""

    def __init__(self, url: str, modelo: str, timeout_s: float = 5.0, llamar: Callable | None = None):
        self.url, self.modelo, self.timeout_s = url, modelo, timeout_s
        self._llamar = llamar

    def chat(self, sistema: str, usuario: str, temperatura: float = 0.2, max_tokens: int = 120) -> str:
        cuerpo = {"model": self.modelo, "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
                  "temperature": temperatura, "max_tokens": max_tokens, "stream": False,
                  "reasoning_effort": "none",                                   # Ollama
                  "chat_template_kwargs": {"enable_thinking": False}}           # llama-server
        if self._llamar is not None:
            crudo = self._llamar(cuerpo)
        else:
            import httpx
            r = httpx.post(self.url, json=cuerpo, timeout=self.timeout_s)
            r.raise_for_status()
            crudo = r.json()["choices"][0]["message"]["content"]
        return re.sub(r"<think>.*?</think>", "", crudo or "", flags=re.S).strip()


def _limpia_salida(t: str) -> str:
    t = re.sub(r"^\s*(mensaje|respuesta|reescritura)\s*:\s*", "", t.strip(), flags=re.I)
    t = t.strip().strip('"“”«»').strip()
    return re.sub(r"[ \t]+\n", "\n", t).strip()


class RealizadorBase:
    """Sin modelo: el texto base, con las variantes elegidas por hash (así la conversación no repite siempre las mismas palabras)."""
    nombre = "base"
    usa_modelo = False

    def realizar(self, msg: Mensaje, protegidos: Protegidos, base: str, ctx: dict, variante: int) -> str:
        return base


class RealizadorVariantes:
    """El modelo ELIGE entre variantes ya escritas, con un JSON de números. No escribe una palabra: lo más seguro con un modelo."""
    nombre = "variantes"
    usa_modelo = True

    def __init__(self, llm: ClienteLLM, semilla: str = ""):
        self.llm = llm
        self.semilla = semilla

    def realizar(self, msg: Mensaje, protegidos: Protegidos, base: str, ctx: dict, variante: int) -> str:
        self.ultima = None
        opciones = msg.opciones()
        if not opciones:
            return base
        lineas = []
        for clave, idx in opciones.items():
            i, parte = clave.split(".", 1)
            b = msg.bloques[int(i)]
            vs = "; ".join(f"[{n}] {b.plantilla.partes[parte][n]}" for n in idx)
            lineas.append(f"{clave}: {vs}")
        # Las variantes llevan {{SLOT}}: el modelo ve el hueco, no el dato.
        usuario = ("Lo que escribió la clienta: «" + ((ctx.get("conversation") or {}).get("last_user_message") or "")[:200] + "»\n\n"
                   "Opciones:\n" + "\n".join(lineas) + "\n\nResponde el JSON.")
        crudo = self.llm.chat(SISTEMA_VARIANTES, usuario, temperatura=0.0 if variante == 0 else 0.2, max_tokens=60)
        try:
            m = re.search(r"\{.*\}", crudo, re.S)
            elegidas = {k: int(v) for k, v in json.loads(m.group(0)).items() if k in opciones and isinstance(v, int) and v in opciones[k]}
        except (AttributeError, ValueError, TypeError):
            elegidas = {}
        self.ultima = {"pedidas": len(opciones), "validas": len(elegidas), "elegidas": elegidas}
        return msg.ensamblar(protegidos, elegidas, self.semilla)


class RealizadorReescritura:
    """El modelo REESCRIBE el texto base conservando los tokens. Lo que escriba lo decide la compuerta factual."""
    nombre = "reescritura"
    usa_modelo = True

    def __init__(self, llm: ClienteLLM):
        self.llm = llm

    def realizar(self, msg: Mensaje, protegidos: Protegidos, base: str, ctx: dict, variante: int) -> str:
        usuario = f"Mensaje a reescribir:\n{base}"
        return _limpia_salida(self.llm.chat(SISTEMA_REESCRITURA, usuario, temperatura=0.5 if variante == 0 else 0.2, max_tokens=140))


class GateRechazo(Exception):
    """La compuerta factual rechazó lo que escribió el modelo. `errores` dice por qué."""

    def __init__(self, errores: list[str], texto: str = ""):
        super().__init__("; ".join(errores))
        self.errores, self.texto = errores, texto


class RedactorSemantico:
    """Une selector, realizador y compuerta. Implementa `redactar(plan, variante, contexto)` como los demás redactores de V2."""
    nombre = "semantico"

    def __init__(self, selector: Selector, realizador, compuerta: CompuertaFactual, semilla: Callable[[dict], str] | None = None):
        self.selector, self.realizador, self.compuerta = selector, realizador, compuerta
        self._semilla = semilla or (lambda ctx: "")
        self.traza: dict | None = None

    @property
    def nombre_realizador(self) -> str:
        return self.realizador.nombre

    @property
    def usa_modelo(self) -> bool:
        return self.realizador.usa_modelo

    def atiende(self, plan: dict) -> bool:
        return plan.get("accion") in self.selector.habla

    def elegir(self, plan: dict, contexto: dict | None) -> Mensaje:
        return self.selector.elegir(plan, contexto or {})

    def redactar(self, plan: dict, variante: int = 0, contexto: dict | None = None) -> str:
        ctx = contexto or {}
        t0 = time.perf_counter()
        self.traza = None
        msg = self.elegir(plan, ctx)
        protegidos = Protegidos()
        semilla = self._semilla(ctx)
        base = msg.ensamblar(protegidos, None, semilla)
        traza = {"plantillas": msg.ids(), "realizador": self.realizador.nombre, "tokens": len(protegidos.mapa)}
        self.traza = traza
        modelo_ms = 0
        if msg.sin_modelo() or not self.realizador.usa_modelo:
            salida, usado = base, False
            traza["realizador"] = "base"
        else:
            t1 = time.perf_counter()
            salida, usado = self.realizador.realizar(msg, protegidos, base, ctx, variante), True
            modelo_ms = int((time.perf_counter() - t1) * 1000)
            if getattr(self.realizador, "ultima", None):
                traza["variantes"] = self.realizador.ultima
        traza["modelo_ms"] = modelo_ms
        if usado and self.realizador.nombre == "variantes":
            # El selector de variantes no escribe nada: el texto lo armó el código con variantes ya escritas, así que solo puede
            # contener datos que esas variantes pidieron. Lo único que se comprueba es que no quede un token sin rellenar (abajo).
            traza["gate"] = {"passed": True, "nota": "armado por código con variantes escritas"}
        elif usado and salida != base:
            esp = Esperado(base=base, max_frases=msg.max_frases(), max_chars=int(self.selector.cat.config.get("max_chars", 360)),
                           max_emojis=int(self.selector.cat.config.get("max_emojis", 1)), clave_pregunta=msg.clave_pregunta)
            r = self.compuerta.evaluar(salida, esp)
            traza["gate"] = {"passed": r["passed"], "errors": r["errors"]}
            if not r["passed"]:
                raise GateRechazo(r["errors"], salida)
        try:
            final = protegidos.rellenar(salida)
        except KeyError as e:
            raise GateRechazo([f"dato protegido desconocido {e}"], salida)
        traza["ms"] = int((time.perf_counter() - t0) * 1000)
        return final


__all__ = ["ClienteLLM", "RealizadorBase", "RealizadorVariantes", "RealizadorReescritura", "RedactorSemantico", "GateRechazo",
           "SinPlantilla"]
