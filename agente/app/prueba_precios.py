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


def prueba_una_vez(fichas):
    """El precio de un modelo se dice una vez: al cambiar de color no se repite (15-10-2026)."""
    salome = next(x for x in fichas if x.codigo == "V50")
    montos = {"V50": P.montos(salome)}
    caso("montos del Salome", montos["V50"], {round(float(salome.precio), 2)}
         )
    precio = f"S/ {salome.precio:g}"

    def res(texto, dichos, pie=None):
        r = {"respuesta": texto, "memoria": {"producto": "V50", "mostrados": ["V50"], "precios_dichos": list(dichos)}}
        if pie:
            r["sugerencias"] = [{"codigo": "V50", "pie": pie, "titulo": pie.split("\n")[0]}]
        return r

    r = P.no_repetir(res(f"¡Te va a encantar! El Vestido Salome cuesta {precio} 😍 ¿Qué talla usas?", []), "me gusta el salome",
                     False, montos)
    si("primera vez: el precio se dice y queda anotado", precio in r["respuesta"] and r["memoria"]["precios_dichos"] == ["V50"])
    r = P.no_repetir(res(f"¡Sí, en rojo también lo tenemos! Cuesta {precio} 😍 ¿Qué talla usas?", ["V50"],
                         pie=f"*Vestido Salome* — *{precio}.00*\nTallas: S, M"), "y en rojo?", False, montos)
    si("cambia de color: el precio no se repite en el texto", precio not in r["respuesta"] and "rojo" in r["respuesta"]
       and "¿Qué talla usas?" in r["respuesta"])
    si("cambia de color: ni en el pie de la foto", "S/" not in r["sugerencias"][0]["pie"] and "S/" not in r["sugerencias"][0]["titulo"])
    # Nombrado en el texto sin estar en foco ni mostrado: igual queda anotado (prueba local del 15-10-2026).
    r = {"respuesta": f"El Vestido Salome cuesta {precio} 😊 ¿Qué talla usas?", "memoria": {"producto": "", "mostrados": []}}
    cods = P.relevantes(r["memoria"], r, fichas)
    si("relevantes: lo que nombra la respuesta", "V50" in cods)
    r = P.no_repetir(r, "¿cuánto cuesta?", False, {c: P.montos(next(x for x in fichas if x.codigo == c)) for c in cods})
    caso("nombrado: precio anotado", r["memoria"]["precios_dichos"], ["V50"])
    r = P.no_repetir(res(f"En rojo cuesta lo mismo, {precio} 😊", ["V50"]), "¿cuánto cuesta en rojo?", False, montos)
    si("si lo pregunta, se repite", precio in r["respuesta"])
    r = P.no_repetir(res(f"Tu pedido: Vestido Salome talla M, {precio} + envío S/ 15", ["V50"]), "si", True, montos)
    si("en el cierre de la compra, va", precio in r["respuesta"] and "S/ 15" in r["respuesta"])
    r = P.no_repetir(res("El envío a Lima cuesta S/ 15. ¿Lima o provincia?", ["V50"]), "envío?", False, montos)
    caso("el envío no es el precio de la prenda", r["respuesta"], "El envío a Lima cuesta S/ 15. ¿Lima o provincia?")

    # V3: la pista va al prompt y, si el modelo lo repite igual, sale sin el precio.
    base = [C("hola"), B("¡Hola! Te saluda Rosmary 😊 ¿Qué evento se te aproxima?"), C("una boda"),
            B(f"¡Te va a encantar el Vestido Salome! Cuesta {precio} 😍 ¿Qué talla usas?")]

    def turno(mensaje, texto, mem):
        RESPUESTA["texto"], RESPUESTA["herramientas"] = texto, []
        req = types.SimpleNamespace(mensaje=mensaje, historial=list(base), memoria=json.loads(json.dumps(mem)), cliente="Ana",
                                    etapa="", producto="", talla="", canal="whatsapp", estado="", foto_hecho="", foto_codigo="",
                                    foto_oferta=False, colores_grupos=None, precios_solo_si_pregunta=False)
        PROMPTS.clear()
        return agente.conversar(req, fichas, _stock, pie=lambda f, st: f"*{f.nombre}* — *S/ {f.precio:.2f}*")

    mem = {"producto": "V50", "mostrados": ["V50"], "sabemos": {"ocasion": "boda"}, "precios_dichos": ["V50"]}
    r = turno("me encanta el salome", f"¡Sí, también es divino! Cuesta {precio} 😍 ¿Qué talla usas?", mem)
    si("V3: la pista «ya le diste el precio» va al prompt", PROMPTS and "YA LE DISTE EL PRECIO" in PROMPTS[0])
    si("V3: el precio repetido no sale", precio not in r["respuesta"] and "divino" in r["respuesta"])
    r = turno("¿cuánto cuesta el salome?", f"El Salome sigue a {precio}, con la tela que viste 😊", mem)
    si("V3: si pregunta, sin pista y con precio", PROMPTS and "YA LE DISTE EL PRECIO" not in PROMPTS[0] and precio in r["respuesta"])


def prueba_promo_y_urgencia_una_vez(fichas):
    """La promo ya dicha y la urgencia no se repiten (15-10-2026, prueba local con WhatsApp falso)."""
    from .v3 import plantillas
    rec = Recolector(fichas, lambda c: {})
    rec.promos_no_repetir = [{"id": 1, "titulo": "Fiestas de Octubre", "mecanica": "porcentaje", "valor": 20}]
    si("promo anunciada otra vez → motivo", agente._promo_repetida("¡Fiestas de Octubre! Esta semana hay 20% en vestidos 🎉", rec))
    si("20 % suelto también", agente._promo_repetida("Recuerda que tienes 20 % de descuento 😊", rec))
    caso("precio con la promo sí vale", agente._promo_repetida("Te queda en S/ 208 con la promo Fiestas de Octubre", rec), [])
    caso("sin promo dicha, nada", agente._promo_repetida("Fiestas de Octubre 🎉", Recolector(fichas, lambda c: {})), [])
    caso("arreglo: se quita la frase de la promo",
         agente._sin_promo_repetida("Te entiendo 😊 ¡Fiestas de Octubre! Esta semana hay 20% de descuento 🎉 ¿Cuánto mides?", rec),
         "Te entiendo 😊 ¡Fiestas de Octubre! ¿Cuánto mides?".replace("¡Fiestas de Octubre! ", ""))
    from . import conocimiento as K
    K.limpiar_cache()
    conoc = K.leer({"empresa": "baruka", "version": "pp", "hoy": "2026-10-15", "promos": [
        {"id": 7, "titulo": "Fiestas de Octubre", "mecanica": "porcentaje", "valor": 20, "alcance": {"tipo": "todo"}}]})
    f = {"promos_dichas": []}
    agente._anotar_promos_dichas(f, conoc, {"fuentes_usadas": [7]})
    caso("promo usada en el texto (herramienta) queda dicha", f["promos_dichas"], [7])
    f = {"promos_dichas": []}
    agente._anotar_promos_dichas(f, conoc, {"fuentes_usadas": [], "sugerencias": [
        {"pie": "*Vestido Merlina* — ~S/ 220.00~ *S/ 176.00* con la promo «Fiestas de Octubre»"}]})
    caso("promo en el pie de la foto queda dicha", f["promos_dichas"], [7])
    for t in ["Sería genial tenerlo listo antes mientras aún hay stock, por si necesitas algún ajuste.",
              "Wooow, estamos muy pronto.", "Mejor separarlo antes de que se agote.",
              "sería genial tener el vestido listo antes, mientras aún haya stock"]:
        si(f"urgencia reconocida: {t[:30]}", plantillas.RE_ESCASEZ.search(t))
    hist = [B("Wooow, estamos muy pronto. Lo ideal es tenerlo antes. ¿No lo consideras?")]
    f = {"temperatura": "caliente", "temperatura_motivo": "evento en 10 días", "sabemos": {}}
    si("tono caliente sin urgencia previa: crea urgencia", "crea urgencia" in agente._tono(f, []))
    si("tono caliente con urgencia ya dicha: no la repite", "YA se le dijo" in agente._tono(f, hist))


def prueba_sin_insistir_sin_colgajo():
    """Quitar una pregunta repetida no deja su introducción colgando (15-10-2026, prueba local)."""
    hist = [B("¿Cuánto mides de estatura?"), C("elegante"), B("¿Cuánto mides de estatura?"), C("¿cuánto cuesta?")]
    t = agente._sin_insistir("Te entiendo 😊 Nuestras tallas son completas; para orientarte mejor con el largo, ¿cuánto mides?",
                             {"sabemos": {}, "veces": {"estatura": 2}}, hist)
    caso("sin colgajo", t, "Te entiendo 😊 Nuestras tallas son completas.")


def prueba_temperatura_v3(fichas):
    """V3 calcula la temperatura en cada turno y ajusta el tono (15-10-2026): ≤ 14 días caliente, > 30 fría."""
    import datetime as dt
    from . import memoria
    hoy = memoria.ahora_lima().date()
    base = [C("hola"), B("¡Hola! Te saluda Rosmary 😊 ¿Qué evento se te aproxima?"), C("una boda"),
            B("¡Qué lindo! ¿Para cuándo es?")]

    def turno(mensaje, fecha, texto="¡Te va a quedar precioso! 😍 ¿Qué talla usas?"):
        RESPUESTA["texto"], RESPUESTA["herramientas"] = texto, []
        mem = {"producto": "V50", "mostrados": ["V50"], "sabemos": {"ocasion": "boda", "fecha_iso": fecha}}
        req = types.SimpleNamespace(mensaje=mensaje, historial=list(base), memoria=mem, cliente="Ana",
                                    etapa="", producto="", talla="", canal="whatsapp", estado="", foto_hecho="", foto_codigo="",
                                    foto_oferta=False, colores_grupos=None, precios_solo_si_pregunta=False)
        PROMPTS.clear()
        return agente.conversar(req, fichas, _stock, pie=lambda f, st: f"*{f.nombre}* — *S/ {f.precio:.2f}*")

    r = turno("me encanta el salome", (hoy + dt.timedelta(days=9)).isoformat())
    caso("V3: evento la otra semana = caliente", r["memoria"]["temperatura"], "caliente")
    si("V3: caliente lleva el tono de urgencia", PROMPTS and "CLIENTA CALIENTE" in PROMPTS[0])
    r = turno("me encanta el salome", (hoy + dt.timedelta(days=90)).isoformat())
    caso("V3: evento en 3 meses = fría", r["memoria"]["temperatura"], "frio")
    si("V3: fría lleva el tono sin presión", PROMPTS and "CLIENTA FRÍA" in PROMPTS[0])
    r = turno("me encanta el salome", (hoy + dt.timedelta(days=20)).isoformat())
    caso("V3: evento en 20 días = tibia", r["memoria"]["temperatura"], "tibio")
    r = turno("lo quiero, sepáramelo", (hoy + dt.timedelta(days=90)).isoformat())
    caso("V3: quiere comprarlo = caliente aunque falte", r["memoria"]["temperatura"], "caliente")
    r = turno("me encanta el salome", None)
    caso("V3: sin fecha ni señales = fría", r["memoria"]["temperatura"], "frio")
    r = turno("me da miedo que no me quede", (hoy + dt.timedelta(days=9)).isoformat())
    si("V3: temor anotado para la asesora", "talla" in r["memoria"]["objeciones"])
    si("V3: el temor se contesta (hecho en el prompt)", PROMPTS and "TEMOR: que no le quede" in PROMPTS[0])
    caso("V3: con miedo a la talla no salen fotos de otra prenda", r["sugerencias"], [])


def main() -> int:
    antes = (cliente_llm.responder, cliente_llm.configurado)
    cliente_llm.responder, cliente_llm.configurado = _llm_fijo, (lambda: True)
    try:
        prueba_pregunta()
        prueba_permitido()
        prueba_texto()
        prueba_pie()
        fw = fichas_web()
        prueba_v3(fw)
        prueba_una_vez(fw)
        prueba_temperatura_v3(fw)
        prueba_promo_y_urgencia_una_vez(fw)
        prueba_sin_insistir_sin_colgajo()
    finally:
        cliente_llm.responder, cliente_llm.configurado = antes
    print(f"prueba_precios: {FALLOS} fallos")
    return 1 if FALLOS else 0


if __name__ == "__main__":
    sys.exit(main())
