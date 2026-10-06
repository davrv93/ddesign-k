"""Pruebas de la aclaración top-3 y la derivación (2.6): el reranker que duda
aclara con las opciones, sin evidencia deriva a la dueña, y pedir persona o
preguntar fuera del giro va a la asesora. Puras, sin modelos:

    python3 -m app.prueba_v2_aclarar
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from .v2.agente import AgentV2
from .v2.decision import ReglasDecision, fuera_de_giro
from .v2.motor import duda_rag
from .v2.plan import Plan, validar

fallos: list[str] = []
total = 0


def caso(nombre: str, obtenido, esperado) -> None:
    global total
    total += 1
    if obtenido != esperado:
        fallos.append(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


def estado(intent: str = "", mensaje: str = "") -> dict:
    return {"conversation": {"intent": intent, "last_user_message": mensaje},
            "product": {}, "requirements": {}, "herramientas": {}}


R = ReglasDecision()
caso("pide persona deriva", R.decide(estado(mensaje="quiero hablar con una persona"), ["next_action"])[0].choice,
     "pedir_asesora")
caso("asesora deriva", R.decide(estado(intent="asesora"), ["next_action"])[0].choice, "pedir_asesora")
caso("cripto deriva", R.decide(estado(mensaje="cómo pago con criptomonedas?"), ["next_action"])[0].choice,
     "pedir_asesora")
caso("reclamo deriva", R.decide(estado(mensaje="quiero poner un reclamo"), ["next_action"])[0].choice,
     "pedir_asesora")
caso("persona favorita no es pedir persona",
     R.decide(estado(mensaje="es para mi persona favorita, mi mami"), ["next_action"])[0].choice != "pedir_asesora",
     True)
caso("blazer no deriva", R.decide(estado(mensaje="tienen blazer?"), ["next_action"])[0].choice != "pedir_asesora",
     True)

# duda_rag con detalle falso (thread-local de v2/rag).
from .v2 import rag as _rag

plan = Plan(accion="recomendar", producto="V24", hechos=["x: y"])
_rag._local.detalle = {"fuente": "hibrida", "rerank": {"motivo": "desempate"},
                       "opciones": [{"codigo": "V24", "nombre": "Pandora"},
                                     {"codigo": "V25", "nombre": "Midi"},
                                     {"codigo": "V26", "nombre": "Mini"}]}
d = duda_rag(plan, [], {"herramientas": {"stock": {"V24": "online"}}})
caso("desempate aclara", d is not None and d[0].accion, "preguntar")
caso("aclara con top-3", d is not None and all(c in d[0].pregunta["texto"] for c in ("V24", "V25", "V26")), True)
caso("aclara valida", d is not None and validar(d[0], {"V24"}), [])
caso("aclara una pregunta", d is not None and d[0].pregunta["texto"].count("?"), 1)

_rag._local.detalle = {"fuente": "hibrida", "rerank": {"motivo": "bajo_umbral"},
                       "opciones": [{"codigo": "V24", "nombre": "Pandora"}]}
d = duda_rag(plan, [], {"herramientas": {}})
caso("sin evidencia deriva", d is not None and d[0].accion, "pedir_asesora")

_rag._local.detalle = {"fuente": "hibrida", "rerank": {"motivo": "ok"}}
caso("sin duda no toca", duda_rag(plan, [], {"herramientas": {}}), None)
_rag._local.detalle = {"fuente": "vector", "motivo": "lexico_vacio"}
caso("sin híbrida no toca", duda_rag(plan, [], {"herramientas": {}}), None)

# Derivación de punta a punta: lo que está fuera del giro llega a la clienta como «asesora», no como una pregunta de venta.
DERIVA = "Te paso con una asesora"
V1_VENTA = {"accion": "responder", "respuesta": "¿Qué modelo te gustaría pedir?", "sugerencias": [{"codigo": "V24"}],
            "botones": ["x"], "memoria": {}, "etapa": "descubrimiento"}


def turno(mensaje, modo="activo", texto=DERIVA, v1=V1_VENTA):
    ag = AgentV2(v1=lambda r: dict(v1), modo=modo, texto_derivacion=texto)
    return ag.conversar(NS(mensaje=mensaje, historial=[], etapa="", memoria={}, producto="", talla="",
                           desde_anuncio=False, perfil=None, usar_llm=False, modo=modo))


for fuera in ("quiero comprar bitcoin", "¿Buscan personal? quiero trabajar con ustedes", "me dan un préstamo?", "quiero poner una denuncia"):
    caso(f"fuera de giro: {fuera}", fuera_de_giro(fuera), True)
for dentro in ("hola busco un vestido para una boda", "es para mi persona favorita", "tienen blazer?", "eres un bot?", "¿cuánto cuesta el V24?"):
    caso(f"dentro del giro: {dentro}", fuera_de_giro(dentro), False)

r = turno("quiero comprar bitcoin")
caso("activo: acción asesora", r["accion"], "asesora")
caso("activo: el texto es el de la derivación", r["respuesta"], DERIVA)
caso("activo: sin tarjetas ni botones de venta", (r["sugerencias"], r["botones"]), ([], []))
caso("activo: la traza lo dice y guarda lo que habría dicho V1",
     (r["v2"]["fuera_de_giro"]["deriva"], r["v2"]["enviado"], r["v2"]["v1_texto_respaldo"]), (True, "v2", V1_VENTA["respuesta"]))
caso("la traza no sale en la respuesta a la clienta", "traza" in r["respuesta"].lower() or "v2" in r["respuesta"].lower(), False)
r = turno("quiero comprar bitcoin", modo="sombra")
caso("sombra: solo se anota, habla V1", (r["accion"], r["respuesta"], r["v2"]["fuera_de_giro"]), ("responder", V1_VENTA["respuesta"], {"deriva": False, "motivo": "sombra"}))
r = turno("quiero comprar bitcoin", texto="")
caso("sin texto de derivación: V2 no deriva", r["accion"], "responder")
r = turno("quiero poner un reclamo", v1=dict(V1_VENTA, accion="asesora", respuesta=DERIVA))
caso("V1 ya derivó: no se toca", (r["accion"], r["v2"]["fuera_de_giro"]["deriva"]), ("asesora", False))
r = turno("hola busco un vestido")
caso("dentro del giro: nada cambia", (r["accion"], "fuera_de_giro" in r["v2"]), ("responder", False))
import os
os.environ["V2_DERIVA"] = "0"
caso("V2_DERIVA=0 la apaga", turno("quiero comprar bitcoin")["accion"], "responder")
del os.environ["V2_DERIVA"]

print(f"aclarar-derivar: {total - len(fallos)}/{total} ok")
if fallos:
    print("\n".join(fallos))
    raise SystemExit(1)
