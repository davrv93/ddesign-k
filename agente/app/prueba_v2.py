"""Pruebas de la V2 (fases 1–2): sin red, sin modelos, sin LLM. Se ejecutan al construir la imagen:

    python3 -m app.prueba_v2
"""
from __future__ import annotations

import sys
from types import SimpleNamespace as NS

from .v2 import config as C
from .v2.agente import AgentV2
from .v2.contexto import ContextBuilder
from .v2.estado import separar
from .v2.interfaces import Decision, DecisionEngine, GenerationEngine, QualityGate
from .v2.plan import Plan, validar

fallos: list[str] = []
total = 0


def caso(nombre: str, obtenido, esperado) -> None:
    global total
    total += 1
    if obtenido != esperado:
        fallos.append(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


def pedido(**kw):
    base = dict(mensaje="hola", historial=[], etapa="", memoria=None, producto="", talla="", desde_anuncio=False)
    base.update(kw)
    return NS(**base)


# --- configuración ---------------------------------------------------------------------------------------------
caso("sin AGENT_VERSION → v1", C.version_por_defecto({}), "v1")
caso("AGENT_VERSION=V2 → v2 (sin importar mayúsculas)", C.version_por_defecto({"AGENT_VERSION": " V2 "}), "v2")
caso("AGENT_VERSION inválida → v1", C.version_por_defecto({"AGENT_VERSION": "v9"}), "v1")
caso("petición v2 gana al entorno", C.version_pedida("v2", "v1"), "v2")
caso("petición vacía → entorno", C.version_pedida("", "v2"), "v2")
caso("petición inválida → entorno", C.version_pedida("xx", "v1"), "v1")
L = C.limites_desde_entorno({})
caso("límites por defecto", (L.max_pasos, L.max_decisiones, L.max_herramientas, L.max_regeneraciones), (4, 3, 4, 1))
caso("límite fuera de rango se acota a 10", C.limites_desde_entorno({"MAX_AGENT_STEPS": "99"}).max_pasos, 10)
caso("límite negativo se acota a 1", C.limites_desde_entorno({"MAX_AGENT_STEPS": "-3"}).max_pasos, 1)
caso("límite no numérico → defecto", C.limites_desde_entorno({"MAX_TOOL_CALLS": "x"}).max_herramientas, 4)

# --- presupuesto: nunca bucles infinitos -----------------------------------------------------------------------
p = C.Presupuesto(C.Limites(max_pasos=2))
p.gastar("paso"); p.gastar("paso")
try:
    p.gastar("paso")
    caso("el tercer paso con tope 2 debe fallar", False, True)
except C.LimiteExcedido:
    caso("el tercer paso con tope 2 se corta", True, True)
caso("quedan 0 pasos", p.quedan("paso"), 0)
try:
    p.gastar("inventado")
    caso("tipo de paso desconocido se rechaza", False, True)
except ValueError:
    caso("tipo de paso desconocido se rechaza", True, True)

# --- contexto estructurado -------------------------------------------------------------------------------------
historial = [NS(rol="cliente" if i % 2 == 0 else "bot", texto="x" * 500) for i in range(12)]
ctx = ContextBuilder().construir(pedido(mensaje="busco vestido", historial=historial,
                                        etapa="seguimiento",
                                        memoria={"sabemos": {"ocasion": "matrimonio", "nombre": "Lucía"},
                                                 "temperatura": "tibia", "producto": "V35", "mostrados": ["V35"]}))
caso("contexto: etapa de la petición", ctx["conversation"]["stage"], "seguimiento")
caso("contexto: solo los últimos 4 turnos", len(ctx["conversation"]["recent_turns"]), 4)
caso("contexto: turnos recortados a 200 caracteres", max(len(t["texto"]) for t in ctx["conversation"]["recent_turns"]), 200)
caso("contexto: cuenta el historial completo", ctx["conversation"]["turns_total"], 12)
caso("contexto: sin memoria no falla", ContextBuilder().construir(pedido())["customer"]["name"], None)
caso("contexto: prenda en foco de la memoria", ctx["product"]["focus"], "V35")

# --- hechos, inferencias y desconocidos ------------------------------------------------------------------------
s = separar(ctx)
caso("hecho: ocasión dicha por la clienta", s["hechos"].get("ocasion"), "matrimonio")
caso("inferencia: la temperatura no es hecho", "temperature" in s["hechos"], False)
caso("inferencia: temperatura separada", s["inferencias"].get("temperature"), "tibia")
caso("desconocido: talla", "talla" in s["desconocidos"], True)
caso("desconocido: ocasión conocida no aparece", "ocasion" in s["desconocidos"], False)
caso("hecho: la prenda en foco la decidió el código", s["hechos"].get("prenda_en_foco"), "V35")

# --- plan validado -----------------------------------------------------------------------------------------------
STOCK = {"V35", "V21"}
caso("plan válido sin errores", validar(Plan("recomendar", "V35", ["hombros descubiertos"], "ocasión de noche",
                                              {"tipo": "talla", "texto": "¿Qué talla usas?"}), STOCK), [])
caso("producto sin stock se rechaza", validar(Plan("recomendar", "V99"), STOCK),
     ["producto V99 sin stock o inexistente"])
caso("recomendar sin producto se rechaza", validar(Plan("recomendar"), STOCK), ["«recomendar» sin producto"])
caso("acción inventada se rechaza", validar(Plan("hacer_descuento"), STOCK), ["acción desconocida: hacer_descuento"])
caso("pregunta sin ? se rechaza", validar(Plan("preguntar", pregunta={"texto": "Dime tu talla"}), STOCK),
     ["la pregunta no lleva signo de interrogación"])
caso("dos preguntas en un plan se rechazan",
     validar(Plan("preguntar", pregunta={"texto": "¿Talla? ¿Fecha?"}), STOCK), ["más de una pregunta en el mismo plan"])

# --- interfaces ----------------------------------------------------------------------------------------------------
class Fija:
    nombre = "fija"

    def decide(self, contexto, decisiones):
        return [Decision(d, "x", 1.0) for d in decisiones]

    def redactar(self, plan):
        return "ok"

    def evaluar(self, borrador, plan):
        return {"passed": True, "score": 1.0, "errors": []}


caso("implementación cumple DecisionEngine", isinstance(Fija(), DecisionEngine), True)
caso("implementación cumple GenerationEngine", isinstance(Fija(), GenerationEngine), True)
caso("implementación cumple QualityGate", isinstance(Fija(), QualityGate), True)

# --- AgentV2 envuelve V1 sin cambiarla -----------------------------------------------------------------------------
RES_V1 = {"respuesta": "Hola", "etapa": "prospeccion", "sugerencias": [], "memoria": {"etapa": "prospeccion"}}
llamadas = []


def v1_falso(req):
    llamadas.append(req)
    return RES_V1


agente = AgentV2(v1=v1_falso)
out = agente.conversar(pedido(mensaje="hola"))
caso("V2 devuelve lo mismo que V1", {k: out[k] for k in RES_V1}, RES_V1)
caso("V2 marca la versión", out["version"], "v2")
caso("V2 traza: sin fallback", out["v2"]["fallback"], False)
caso("V2 no muta la respuesta de V1", "version" in RES_V1, False)
caso("V1 se llama una sola vez", len(llamadas), 1)


class RompeContexto:
    def construir(self, req):
        raise KeyError("boom")


agente = AgentV2(v1=v1_falso, contexto=RompeContexto())
out = agente.conversar(pedido(mensaje="hola"))
caso("si el contexto falla, el turno sigue por V1", out["respuesta"], "Hola")
caso("fallback queda en la traza", (out["v2"]["fallback"], out["v2"]["motivo"]), (True, "KeyError"))


def main() -> int:
    for f in fallos:
        print(f)
    print(f"v2 fases 1–2: {total - len(fallos)}/{total} casos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
