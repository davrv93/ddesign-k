"""Fotos de los colores de la prenda en foco (14-10-2026, WhatsApp real del Vestido Salome V50). Sin LLM: el modelo se
sustituye por una respuesta fija y se mira lo que el CÓDIGO decide (qué fotos salen y qué hecho recibe el modelo).

La conversación: la clienta manda la foto del Salome (azul noche), pregunta las medidas de la XL y «¿en qué colores tienes
disponible?»; el bot lista Verde Olivo, Vino, Blanco, Acero, Rojo y Terracota. Entonces:
  - «enviame los colores en foto» → salía otra vez la azul noche y el bot decía «solo puedo mostrarte ese color en foto».
  - «¿cual es el tono verde olivo que tienes?» → ninguna foto y le ofrecía otros modelos verdes.

    python3 -m app.prueba_fotos_colores
"""
from __future__ import annotations

import json
import sys
import types

from . import fotos_color, ficha_producto
from .prueba_colores import fichas_web
from .v3 import agente, cliente_llm
from .v3.herramientas import MAX_FOTOS_COLORES, Recolector

fallos = []
PROMPTS: list[str] = []


def ok(cond, msg):
    if not cond:
        fallos.append(msg)
        print("  FALLO:", msg)


def _stock(codigos):
    return {c.upper(): {"product": {"code": c}, "online": {"S": 1, "M": 1, "L": 1}} for c in codigos}


def _llm_fijo(msgs, tools, ejecutar, modelo="", max_vueltas=0):
    PROMPTS.append("\n".join(m.get("content") or "" for m in msgs))
    return {"contenido": json.dumps({"respuesta": "Mira 😊 ¿Cuál te gusta más?", "pregunta_hecha": ""}),
            "vueltas": 1, "usadas": [], "uso": {}}


HIST = [
    {"rol": "cliente", "texto": "Hola"},
    {"rol": "bot", "texto": "¡Hola, Ana! 🥰 Te saluda Rosmary, desde ahora considérame tu asesora personal. ¿Qué evento se te aproxima?"},
    {"rol": "cliente", "texto": "me interesa este modelo"},
    {"rol": "bot", "texto": "¡Qué lindo que te interese! 😊 Sí tenemos el Vestido Salome V50 en azul noche, disponible en XS, S, M, "
                            "L, XL y 2XL. ¿Para qué ocasión lo estás buscando?"},
    {"rol": "bot", "texto": "*V50* Vestido Salome — *S/ 290.00*\nTallas: S, M, L · XS y XL y 2XL bajo pedido\n👉 Escribe *V50* para pedirlo"},
    {"rol": "cliente", "texto": "¿cuales son sus medidas de la talla xl?"},
    {"rol": "bot", "texto": "La talla XL del modelo V50 mide 110 cm de busto, 100 cm de cintura, 115 cm de cadera y 155 cm de "
                            "largo. Esa talla se confecciona bajo pedido; una asesora puede confirmarte el plazo. ¿Qué evento tienes en mente?"},
    {"rol": "cliente", "texto": "¿en que colores tienes disponible?"},
    {"rol": "bot", "texto": "El Vestido Salome también lo tenemos en Verde Olivo, Vino, Blanco, Acero, Rojo y Terracota; además, "
                            "puedes solicitarlo en color personalizado. ¿Para qué ocasión lo buscas? 😊"},
]


def _turno(fichas, mensaje, hist, memoria):
    req = types.SimpleNamespace(mensaje=mensaje, historial=list(hist), memoria=json.loads(json.dumps(memoria)), cliente="Ana",
                                etapa="", producto="", talla="", canal="whatsapp", estado="", foto_hecho="", foto_codigo="",
                                foto_oferta=False, colores_grupos=None)
    PROMPTS.clear()
    return agente.conversar(req, fichas, _stock, pie=lambda f, st: f"*{f.codigo}* {f.nombre}\nTallas: S, M, L")


def prueba_ayudantes():
    web = ficha_producto.web("V50").get("colores") or []
    ok("Verde Olivo" in web and "Color personalizado" in web, f"colores del V50 en baruka.pe: {web}")
    fotos = fotos_color.fotos_de_colores("V50", web)
    ok(len(fotos) == 7 and fotos[0] == ("v50_5.jpg", "Verde Olivo"), f"siete fotos, una por color, en el orden de la web: {fotos}")
    ok(not any(fotos_color.es_personalizado(c) for _, c in fotos), "«Color personalizado» no es una foto")
    for texto, esperado in (("¿cual es el tono verde olivo que tienes?", ["Verde Olivo"]), ("¿y en rojo?", ["Rojo"]),
                            ("la quiero en azul", ["Azul noche"]), ("terracota y acero porfa", ["Acero", "Terracota"]),
                            ("enviame los colores en foto", [])):
        ok(fotos_color.colores_nombrados(texto, web) == esperado, f"colores nombrados en «{texto}»: "
                                                                    f"{fotos_color.colores_nombrados(texto, web)}")
    rec = Recolector(fichas_web(), _stock, pie=lambda f, st: f"*{f.codigo}* {f.nombre}\nTallas: S, M, L")
    r = rec.enviar_colores("V50", saltar="Azul noche")
    ok(len(rec.fotos) == MAX_FOTOS_COLORES and "Azul noche" not in r["con_foto"], f"todos sin el ya visto: {r}")
    ok(len({x["imagen"] for x in rec.fotos}) == len(rec.fotos), "cada foto es un archivo distinto")
    ok(rec.fotos[0]["pie"].startswith("*V50* Vestido Salome · Verde Olivo"), f"el pie dice su color: {rec.fotos[0]['pie']!r}")
    # La foto ya mostrada en otro color no bloquea la del color pedido (antes: «no pude enviar la foto»).
    rec = Recolector(fichas_web(), _stock, mensaje="¿cual es el tono verde olivo que tienes?", mostrados=["V50"],
                     contesta_cual=True)
    rec.color_pedido = "verde"
    rec.ejecutar("enviar_fotos", {"codigos": ["V50"]})
    ok(rec.fotos and rec.fotos[0]["imagen"].endswith("v50_5.jpg"), f"V50 ya visto, foto en verde olivo: {rec.fotos}")


def prueba_conversacion():
    fichas = fichas_web()
    mem = {"etapa": "seguimiento", "producto": "V50", "mostrados": ["V50"], "pendiente": "ocasion",
           "sabemos": {"talla": "XL"}, "preguntado": ["ocasion", "ocasion"]}
    # 1. «enviame los colores en foto»: el Salome en sus otros colores, no otra vez la azul noche.
    r = _turno(fichas, "enviame los colores en foto", HIST, mem)
    fotos = [s["imagen"] for s in r["sugerencias"]]
    ok({s["codigo"] for s in r["sugerencias"]} == {"V50"}, f"solo fotos del Salome: {[s['codigo'] for s in r['sugerencias']]}")
    ok(len(fotos) == 6 and not any(x.endswith("/v50.jpg") for x in fotos), f"seis colores sin la azul noche ya vista: {fotos}")
    ok(any(x.endswith("v50_5.jpg") for x in fotos), "va el verde olivo")
    hecho = PROMPTS[0] if PROMPTS else ""
    ok("PIDE VER LOS COLORES DEL Vestido Salome V50" in hecho, "el modelo sabe que se enviaron los colores")
    ok("ELLA PIDIÓ VER MODELOS: muéstrale UNA prenda" not in hecho, "sin la orden de mostrar UNA prenda")
    hist = HIST + [{"rol": "cliente", "texto": "enviame los colores en foto"}, {"rol": "bot", "texto": r["respuesta"]}] + \
        [{"rol": "bot", "texto": s["pie"]} for s in r["sugerencias"]]
    # 2. «¿cual es el tono verde olivo que tienes?»: la foto del Salome verde olivo, no otras prendas verdes.
    r2 = _turno(fichas, "¿cual es el tono verde olivo que tienes?", hist, r["memoria"])
    fotos2 = [(s["codigo"], s["imagen"]) for s in r2["sugerencias"]]
    ok(fotos2 == [("V50", "/media/tienda/v50_5.jpg")], f"foto del Salome verde olivo: {fotos2}")
    hecho2 = PROMPTS[0] if PROMPTS else ""
    ok("HECHO YA VERIFICADO (color" not in hecho2, "no se le listan otras prendas de ese color")
    # Lo mismo en la conversación original, sin el turno de las fotos: el Salome ya visto en azul noche.
    r3 = _turno(fichas, "¿cual es el tono verde olivo que tienes?", HIST, mem)
    ok([s["imagen"] for s in r3["sugerencias"]] == ["/media/tienda/v50_5.jpg"], "también directo tras listar los colores")
    # 3. Lo que NO es pedir los colores del Salome sigue como antes.
    r4 = _turno(fichas, "¿en que colores tienes disponible?", HIST[:7], mem)
    ok(not r4["sugerencias"], f"preguntar qué colores hay se contesta con texto: {[s['imagen'] for s in r4['sugerencias']]}")
    r5 = _turno(fichas, "¿tienes algo en verde?", HIST, mem)
    ok("HECHO YA VERIFICADO (color verde)" in (PROMPTS[0] if PROMPTS else ""), "«algo en verde» busca en el catálogo")
    ok(not any(s["imagen"].startswith("/media/tienda/v50_") and s["imagen"] != "/media/tienda/v50.jpg" for s in r5["sugerencias"])
       or len(r5["sugerencias"]) <= 1, "«algo en verde» no manda la tanda de colores del Salome")
    # Elige un color de la lista que acaba de dar el bot, sin pregunta ni verbo (prueba de 300 conversaciones, 14-10-2026).
    for texto, archivo in (("en rojo porfa", "v50_4.jpg"), ("en vino", "v50_2.jpg"), ("terracota", "v50_6.jpg")):
        r6 = _turno(fichas, texto, HIST, mem)
        ok([s["imagen"] for s in r6["sugerencias"]] == [f"/media/tienda/{archivo}"],
           f"«{texto}» tras la lista de colores: foto del Salome en ese color: {[s['imagen'] for s in r6['sugerencias']]}")
    dos = HIST[:-1] + [{"rol": "bot", "texto": "Te muestro el Vestido Salome y el Vestido Blear. ¿Cuál te gusta más?"}]
    _turno(fichas, "enviame los colores en foto", dos, mem)
    ok("PIDE VER LOS COLORES" not in (PROMPTS[0] if PROMPTS else ""), "dos modelos nombrados: no se adivina de cuál")


def main() -> int:
    antes = (cliente_llm.responder, cliente_llm.configurado)
    cliente_llm.responder, cliente_llm.configurado = _llm_fijo, (lambda: True)
    try:
        prueba_ayudantes()
        prueba_conversacion()
    finally:
        cliente_llm.responder, cliente_llm.configurado = antes
    print(f"prueba_fotos_colores: {len(fallos)} fallos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
