"""Arreglos del 14-10-2026 sacados de la prueba de 300 conversaciones. Sin LLM: el modelo se sustituye por una respuesta
fija (que puede pedir herramientas) y se mira lo que decide el código.

  - «los quince de mi sobrina» no es estilo «sobria».
  - la marca interna «(pregunta_hecha: …)» no llega a la clienta.
  - objeción al envío («¿me rebajas el envío?», «¿es seguro?»): sin fotos.
  - «uso talla 42»: el modelo recibe que esa talla no existe en la tienda.
  - «el envío sería a Arequipa»: el modelo recibe el costo (S/ 20).
  - «¿Confirmamos tu pedido?» → «ya, cómo te pago»: el pedido queda confirmado y se pasa al pago.
  - una pregunta hecha dos veces sin respuesta no se hace una tercera (estatura, talla, evento).

    python3 -m app.prueba_pulido_v3
"""
from __future__ import annotations

import json
import sys
import types

from . import memoria
from .prueba_colores import fichas_web
from .v3 import agente, cliente_llm, ficha as ficha_mod, prompt

fallos: list[str] = []
PROMPTS: list[str] = []
RESPUESTA = {"texto": "Perfecto 😊", "herramientas": []}


def ok(cond, msg):
    if not cond:
        fallos.append(msg)
        print("  FALLO:", msg)


def _stock(codigos):
    return {c.upper(): {"product": {"code": c}, "online": {"S": 2, "M": 2, "L": 2}} for c in codigos}


def _llm_fijo(msgs, tools, ejecutar, modelo="", max_vueltas=0):
    PROMPTS.append("\n".join(m.get("content") or "" for m in msgs))
    for nombre, args in RESPUESTA["herramientas"]:
        ejecutar(nombre, args)
    return {"contenido": json.dumps({"respuesta": RESPUESTA["texto"], "pregunta_hecha": ""}), "vueltas": 1, "usadas": [], "uso": {}}


def _turno(fichas, mensaje, hist, mem=None, texto="Perfecto 😊", herramientas=(), permitir=False):
    RESPUESTA["texto"], RESPUESTA["herramientas"] = texto, list(herramientas)
    req = types.SimpleNamespace(mensaje=mensaje, historial=list(hist), memoria=json.loads(json.dumps(mem)) if mem else None,
                                cliente="Ana", etapa="", producto="", talla="", canal="whatsapp", estado="", foto_hecho="",
                                foto_codigo="", foto_oferta=False, colores_grupos=None)
    PROMPTS.clear()
    return agente.conversar(req, fichas, _stock, pie=lambda f, st: f"*{f.codigo}* {f.nombre}", permitir_reserva=permitir)


B = lambda t: {"rol": "bot", "texto": t}
C = lambda t: {"rol": "cliente", "texto": t}


def prueba_reglas():
    ok(memoria._estilo("tengo los quince de mi sobrina", "") is None, "«sobrina» no es estilo")
    ok(memoria._estilo("algo sobrio", "estilo") == "elegante y discreta", "«algo sobrio» sí es discreta")
    ok(memoria._estilo("sobria y elegante", "") == "elegante y discreta", "«sobria y elegante» es discreta")
    for crudo, esperado in (('{"respuesta": "¿Qué te gustó? (pregunta_hecha: que_le_gusto)", "pregunta_hecha": ""}', "¿Qué te gustó?"),
                            ("¡Listo! ¿Lima o provincia? pregunta_hecha: envio", "¡Listo! ¿Lima o provincia?"),
                            ('{"respuesta": "Te mando la foto [pregunta_hecha=que_le_gusto] 😊", "pregunta_hecha": ""}', "Te mando la foto 😊")):
        r = prompt.leer_salida(crudo)["respuesta"]
        ok(r == esperado, f"marca interna fuera: {r!r}")
    r = prompt.leer_salida('Te recomiendo el Bruna ✨ ¿Qué te gustó?\\",\\"pregunta_hecha\\":\\"que_le_gusto"')["respuesta"]
    ok(r == "Te recomiendo el Bruna ✨ ¿Qué te gustó?", f"marca con comillas escapadas fuera: {r!r}")
    # El bot Go manda solo 11 mensajes: lo que ya se preguntó viaja en la ficha (veces) y vale aunque no esté en la ventana.
    f = {"sabemos": {}, "veces": {"ocasion": 2}}
    ventana = [C("provincia, Arequipa"), B("A provincia cuesta S/ 20. ¿Cuál prefieres?")]
    sin = agente._sin_insistir("¡Claro! 😊 Cuéntame, ¿qué evento se te aproxima?", f, ventana)
    ok("evento" not in memoria._plano(sin), f"tercera vez fuera de la ventana: {sin!r}")
    f2 = {"sabemos": {}, "veces": {"talla": 1}}
    agente._anotar_veces(f2, "¿En qué talla te encuentras? ¿El evento es de día o de noche?", [])
    ok(f2["veces"].get("talla") == 2 and f2["veces"].get("horario") == 1, f"la ficha suma lo preguntado: {f2['veces']}")
    hist = [C("hola"), B("¡Hola! ¿Qué evento se te aproxima?"), C("me gusta el Persefone"), B("Está S/ 250 😊 ¿Qué evento se te aproxima?")]
    ok(agente._veces_preguntado(hist).get("ocasion") == 2, f"cuenta de preguntas: {agente._veces_preguntado(hist)}")
    sin = agente._sin_insistir("¡Perfecto, ahí lo ves en rojo! 😊 ¿Qué evento se te aproxima?", {"sabemos": {}}, hist)
    ok("evento" not in memoria._plano(sin) and "rojo" in sin, f"no pregunta el evento por tercera vez: {sin!r}")
    ok("PASO DEL GUION: 1" not in agente._paso_guion({"sabemos": {}}, hist, False), "el paso 1 no se repite tras dos intentos")
    hist2 = [B("¿Cuánto mides de estatura?"), C("azul"), B("Lindo color. ¿Cuánto mides de estatura?"), C("talla M")]
    sin = agente._sin_insistir("¡Sí hay en tu talla! Para el largo, ¿cuánto mides de estatura?", {"sabemos": {"talla": "M"}}, hist2)
    ok("estatura" not in memoria._plano(sin) and "talla" in memoria._plano(sin), f"estatura no por tercera vez: {sin!r}")
    sin = agente._sin_insistir("¿Cuánto mides de estatura?", {"sabemos": {"estatura": "1.60"}}, hist2)
    ok(sin == "¿Cuánto mides de estatura?", "si ya respondió, otra regla se encarga (no se toca aquí)")


def prueba_busqueda(fichas):
    from .v3.herramientas import Recolector, _cat
    ok(_cat("Pantalones") == _cat("pantalón") == _cat("pantalon"), "categoría sin tilde ni plural")
    for f in fichas:
        if f.codigo == "V96":
            f.categoria = "pantalón"          # como viene del catálogo vivo
    for cat in ("pantalón", "pantalon", "pantalones", "Pantalón", "vestido"):
        r = Recolector(fichas, _stock, mensaje="cuánto cuesta el zury").ejecutar("buscar_catalogo", {"consulta": "Zury talla M", "categoria": cat})
        ok([p["codigo"] for p in r["prendas"]][:1] == ["V96"], f"buscar «Zury» con categoría {cat!r}: {[p['codigo'] for p in r['prendas']][:3]}")


def prueba_conversacion(fichas):
    envio = [C("hola"), B("¡Hola! ¿Qué evento se te aproxima?"), C("tengo una boda"),
             B("¡Qué lindo! ¿En qué talla te encuentras?"), C("talla M, mido 1.60, es de noche"),
             B("Excelente. ¿El envío sería para Lima o provincia?"), C("es para provincia"),
             B("Excelente, el envío cuesta S/ 20 a través de Olva Courier o Shalom; el envío llega hasta la agencia. ¿Cuál de ellas prefieres?")]
    for objecion in ("no me puedes rebajar el envío?", "es seguro?", "y si se pierde?"):
        r = _turno(fichas, objecion, envio, texto="Te entiendo 😊 Trabajamos con los mejores servicios. ¿Olva o Shalom?",
                   herramientas=[("enviar_fotos", {"codigos": ["V49"]})])
        ok(not r["sugerencias"], f"«{objecion}»: sin fotos ({[s['codigo'] for s in r['sugerencias']]})")
        ok("OBJECIÓN AL ENVÍO" in (PROMPTS[0] if PROMPTS else ""), f"«{objecion}» se reconoce como objeción")

    base = [C("hola"), B("¡Hola! ¿Qué evento se te aproxima?"), C("tengo un bautizo"), B("¡Qué lindo! ¿En qué talla te encuentras?")]
    _turno(fichas, "uso talla 42", base)
    ok("HECHO YA VERIFICADO: dijo talla 42" in (PROMPTS[0] if PROMPTS else ""), "talla 42: el modelo sabe que no existe")
    _turno(fichas, "mido 1.65", base)
    ok("HECHO YA VERIFICADO: dijo talla" not in (PROMPTS[0] if PROMPTS else ""), "«mido 1.65» no es una talla")

    _turno(fichas, "el envío sería a Arequipa", envio[:6])
    ok("S/ 20" in (PROMPTS[0] if PROMPTS else "") and "Arequipa" in (PROMPTS[0] if PROMPTS else ""), "envío a Arequipa: el modelo recibe el costo")

    mem = ficha_mod.normalizar(None)
    mem["producto"], mem["mostrados"] = "V129", ["V129"]
    mem["sabemos"]["talla"], mem["sabemos"]["envio"] = "S", "lima"
    cierre = [C("me gusta el Sol"), B("*V129* Enterizo Sol"), C("en S, a Lima"),
              B("El Enterizo Sol en talla S cuesta S/ 290 y el envío S/ 15; el total es S/ 305 😊 ¿Confirmamos tu pedido?")]
    for si in ("ya, cómo te pago", "sí", "ahí va el voucher"):
        r = _turno(fichas, si, cierre, mem, texto="¡Listo! Tu pedido quedó confirmado.", permitir=True)
        ok(r["accion"] == "pedido" and r["codigo"] == "V129" and r["talla"] == "S", f"«{si}» confirma el pedido: {r['accion']} {r['codigo']} {r['talla']}")
        ok("CONFIRMÓ EL PEDIDO" in (PROMPTS[0] if PROMPTS else ""), f"«{si}»: el modelo pasa al pago")
    r = _turno(fichas, "no, todavía no", cierre, mem, texto="De acuerdo 😊", permitir=True)
    ok(r["accion"] != "pedido", "«no, todavía no» no confirma")
    r = _turno(fichas, "ya, cómo te pago", cierre, mem, texto="Claro 😊", permitir=False)
    ok(r["accion"] != "pedido", "sin permiso de reserva (sombra) no confirma")

    repetida = [C("hola"), B("¡Hola! ¿Qué evento se te aproxima?"), C("una reunión de trabajo"),
                B("¡Qué bien! ¿Cuánto mides de estatura?"), C("pensaba en azul"), B("Lindo. ¿Cuánto mides de estatura?")]
    r = _turno(fichas, "talla M", repetida, texto="¡Sí hay en tu talla! 😊 Para el largo, ¿cuánto mides de estatura?")
    ok("estatura" not in memoria._plano(r["respuesta"]) and "?" in r["respuesta"], f"no insiste con la estatura: {r['respuesta']!r}")


def main() -> int:
    antes = (cliente_llm.responder, cliente_llm.configurado)
    cliente_llm.responder, cliente_llm.configurado = _llm_fijo, (lambda: True)
    try:
        prueba_reglas()
        prueba_busqueda(fichas_web())
        prueba_conversacion(fichas_web())
    finally:
        cliente_llm.responder, cliente_llm.configurado = antes
    print(f"prueba_pulido_v3: {len(fallos)} fallos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
