"""Precios solo si la clienta los pregunta (app/precios.py y su uso en V3), 15-10-2026.

    python3 -m app.prueba_precios
"""
from __future__ import annotations

import sys

from . import precios as P
from .prueba_colores import fichas_web
import json
import types

from .prueba_pulido_v3 import B, C, PROMPTS, RESPUESTA, _llm_fijo, _stock
from .v3 import agente, cliente_llm
from .v3.herramientas import Recolector

FALLOS = 0


def caso(nombre, obtenido, esperado):
    global FALLOS
    if obtenido != esperado:
        FALLOS += 1
        print(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


def si(nombre, cond):
    caso(nombre, bool(cond), True)


def prueba_pregunta():
    for t in ["¿Cuánto cuesta el vestido Salome?", "precio?", "¿a cuánto está?", "¿cuánto?", "y en total cuánto sería",
              "cuánto sale el envío y el vestido", "¿qué valor tiene?"]:
        caso(f"pregunta precio: {t}", P.pregunta_precio(t), True)
    for t in ["¿tienen promo?", "¿hay descuento?", "me gusta el Salome", "quiero ver vestidos", "¿cuánto mide de largo?",
              "hola", "¿tienen en talla M?"]:
        caso(f"no pregunta precio: {t}", P.pregunta_precio(t), False)


def prueba_permitido():
    caso("sin configuración: siempre", P.permitido(False, "hola"), True)
    caso("con configuración y sin preguntar: no", P.permitido(True, "me gusta el Salome"), False)
    caso("con configuración y pregunta: sí", P.permitido(True, "¿cuánto cuesta?"), True)
    caso("en el cierre (esperando confirmación): sí", P.permitido(True, "ok", estado="esperando_confirmacion"), True)
    caso("venta confirmada: sí", P.permitido(True, "ok", etapa="venta_confirmada"), True)
    caso("pedido con prenda y talla: sí", P.permitido(True, "Lima", pedido={"codigo": "V50", "talla": "M"}), True)
    caso("pago habilitado: sí", P.permitido(True, "ok", pago_ok=True), True)


def prueba_texto():
    caso("envío se queda", P.quitar_de_texto("El envío a Lima cuesta S/ 15 por Olva Courier. ¿Lima o provincia?"),
         "El envío a Lima cuesta S/ 15 por Olva Courier. ¿Lima o provincia?")
    caso("tramo con precio se va", P.quitar_de_texto("Esa prenda no entra en la promo 😊 El Pantalón Holly cuesta S/ 150; "
                                                    "te comparto una foto. ¿Qué talla usas?"),
         "Esa prenda no entra en la promo 😊 Te comparto una foto. ¿Qué talla usas?")
    caso("paréntesis con precio se va", P.quitar_de_texto("El Vestido Salome está en promo (antes S/ 290). ¿Qué talla?"),
         "El Vestido Salome está en promo. ¿Qué talla?")
    caso("frase con precio y pregunta aparte", P.quitar_de_texto("Te muestro el Vestido Daleska, a S/ 200 con la promo 🎉 "
                                                                "¿Qué te gustó?"), "Te muestro el Vestido Daleska. ¿Qué te gustó?")
    si("sin montos: igual", P.quitar_de_texto("¡Hola! ¿Qué evento se te aproxima?") == "¡Hola! ¿Qué evento se te aproxima?")
    si("nunca queda un monto de prenda", "S/ 2" not in P.quitar_de_texto(
        "¡Waooo! 😍 El Vestido Salome cuesta S/ 290.00 y con la promo queda en S/ 232.00. ¿Qué te gustó?"))


def prueba_pie():
    caso("pie sin precio", P.quitar_de_pie("*Vestido Salome* — *S/ 290.00*\nTallas: S, M\n👉 Dime tu talla y te lo separo 😊"),
         "*Vestido Salome*\nTallas: S, M\n👉 Dime tu talla y te lo separo 😊")
    caso("pie con promo: dice la promo sin cifras",
         P.quitar_de_pie("*Vestido Salome* — ~S/ 290.00~ *S/ 232.00* con la promo «Fiestas»\nTallas: S"),
         "*Vestido Salome* · en promo «Fiestas» 🎉\nTallas: S")
    caso("pie sin precio: igual", P.quitar_de_pie("*Pantalón Holly*\nAgotado por ahora"), "*Pantalón Holly*\nAgotado por ahora")
    r = P.aplicar({"respuesta": "Es el Vestido Salome a S/ 290. ¿Te gusta?", "codigo": "V50",
                   "sugerencias": [{"pie": "*Vestido Salome* — *S/ 290.00*\nTallas: M", "titulo": "*Vestido Salome* — *S/ 290.00*",
                                    "tallas": [{"talla": "M", "precio": 290}]}]})
    si("aplicar: texto, pie y título sin precio", "S/" not in r["respuesta"] and "S/" not in r["sugerencias"][0]["pie"]
       and "S/" not in r["sugerencias"][0]["titulo"])
    caso("aplicar: lo interno se queda", r["sugerencias"][0]["tallas"][0]["precio"], 290)


def prueba_v3(fichas):
    base = [C("hola"), B("¡Hola! Te saluda Rosmary 😊 ¿Qué evento se te aproxima?"), C("una boda"),
            B("¡Qué lindo! ¿En qué talla te encuentras?")]
    mem = {"producto": "V50", "mostrados": ["V50"], "sabemos": {"ocasion": "boda"}}

    def turno(mensaje, flag, texto):
        RESPUESTA["texto"], RESPUESTA["herramientas"] = texto, []
        req = types.SimpleNamespace(mensaje=mensaje, historial=list(base), memoria=json.loads(json.dumps(mem)), cliente="Ana",
                                    etapa="", producto="", talla="", canal="whatsapp", estado="", foto_hecho="", foto_codigo="",
                                    foto_oferta=False, colores_grupos=None, precios_solo_si_pregunta=flag)
        PROMPTS.clear()
        return agente.conversar(req, fichas, _stock, pie=lambda f, st: f"*{f.nombre}* — *S/ {f.precio:.2f}*")

    r = turno("me encanta el Salome", True, "¡Te va a quedar precioso! 😍 ¿Para cuándo es la boda?")
    si("V3: con la configuración y sin preguntar, va la política", PROMPTS and P.NOTA_SISTEMA in PROMPTS[0])
    r = turno("me encanta el Salome", True, "¡Te va a quedar precioso! Cuesta S/ 290 😍 ¿Para cuándo es la boda?")
    si("V3: el precio que se le escapa al modelo no sale", "S/ 290" not in r["respuesta"] and "precioso" in r["respuesta"])
    r = turno("¿cuánto cuesta el Salome?", True, "Cuesta S/ 290 😊 ¿Para cuándo es la boda?")
    si("V3: si pregunta, no va la política", PROMPTS and P.NOTA_SISTEMA not in PROMPTS[0])
    si("V3: si pregunta, el precio se queda", "S/ 290" in r["respuesta"])
    r = turno("me encanta el Salome", False, "¡Te va a quedar precioso! 😍 ¿Para cuándo es la boda?")
    si("V3: sin la configuración, nada cambia", PROMPTS and P.NOTA_SISTEMA not in PROMPTS[0])
    rec = Recolector(fichas, lambda c: {})
    rec.sin_precios = True
    si("validador V3: precio sin preguntar → motivo", agente._precio_no_pedido("El Salome cuesta S/ 290 😊", rec))
    caso("validador V3: envío no es precio", agente._precio_no_pedido("El envío cuesta S/ 15", rec), [])
    rec.sin_precios = False
    caso("validador V3: sin la configuración no hay motivo", agente._precio_no_pedido("Cuesta S/ 290", rec), [])


def main() -> int:
    antes = (cliente_llm.responder, cliente_llm.configurado)
    cliente_llm.responder, cliente_llm.configurado = _llm_fijo, (lambda: True)
    try:
        prueba_pregunta()
        prueba_permitido()
        prueba_texto()
        prueba_pie()
        prueba_v3(fichas_web())
    finally:
        cliente_llm.responder, cliente_llm.configurado = antes
    print(f"prueba_precios: {FALLOS} fallos")
    return 1 if FALLOS else 0


if __name__ == "__main__":
    sys.exit(main())
