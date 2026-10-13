"""Pruebas de V3 sin dependencias ni red: ficha, validador y herramientas.

    python3 -m app.v3.prueba_v3
"""
from __future__ import annotations

import sys

from .. import datos
from . import ficha as F, herramientas as H, validador as V

FALLOS: list[str] = []


def caso(nombre: str, obtenido, esperado) -> None:
    if obtenido != esperado:
        FALLOS.append(f"{nombre}: esperaba {esperado!r}, obtuve {obtenido!r}")


def ficha(codigo, nombre, categoria="vestido", color="negro", detalle="", tallas="S,M,L", precio=320.0, imagen="/media/products/x.jpg"):
    return datos.Ficha(codigo=codigo, nombre=nombre, categoria=categoria, color=color, detalle=detalle,
                       tallas=tallas, fuente="seed", precio=precio, imagen=imagen)


FICHAS = [ficha("V21", "Vestido Kendall", detalle="tela roma"),
          ficha("V35", "Vestido Irla", detalle="tela roma"),
          ficha("V32", "Conjunto Xela", categoria="conjunto", precio=280.0)]
STOCK = {"V21": {"product": True, "online": {"S": 1, "M": 1, "L": 0}, "branches": []},
         "V35": {"product": True, "online": {"S": 0, "M": 1, "L": 1}, "branches": []}}


def _stock(codigos):
    return {c.upper(): STOCK.get(c.upper(), {"product": False, "online": {}, "branches": []}) for c in codigos}


# --- ficha -------------------------------------------------------------------
def prueba_ficha():
    f = F.nueva()
    caso("ficha nueva trae pedido", f["pedido"], {"codigo": "", "talla": "", "confirmado": False})
    caso("ficha nueva trae herramientas", f["herramientas"], [])
    # Migración desde una memoria V1.
    v1 = {"etapa": "seguimiento", "producto": "V35", "pendiente": "talla",
          "sabemos": {"ocasion": "matrimonio", "talla": "M", "cita": "2026-10-20T17:00"},
          "mostrados": ["V35"], "preguntado": ["ocasion"]}
    g = F.normalizar(v1)
    caso("migra etapa", g["etapa"], "seguimiento")
    caso("migra prenda en foco", g["producto"], "V35")
    caso("migra talla", g["sabemos"]["talla"], "M")
    caso("migra pendiente", g["pendiente"], "talla")
    caso("migra cita", g["sabemos"]["cita"], "2026-10-20T17:00")
    caso("migra mostrados", g["mostrados"], ["V35"])
    F.fijar_pedido(g, "V35", "m")
    caso("fijar pedido talla", g["pedido"], {"codigo": "V35", "talla": "M", "confirmado": False})
    caso("normalizar basura no revienta", F.normalizar({"pedido": "x", "herramientas": 3})["pedido"],
         {"codigo": "", "talla": "", "confirmado": False})


# --- validador ---------------------------------------------------------------
def prueba_validador():
    h = V.hechos_vacios()
    h["precios"] = {320.0}
    h["codigos"] = {"V21"}
    h["tallas"] = {"S", "M", "L"}
    h["telas"] = {"roma"}
    caso("precio respaldado", V.validar("El V21 cuesta S/ 320.", h), [])
    caso("precio no respaldado", V.validar("El V21 cuesta S/ 250.", h) != [], True)
    caso("código no respaldado", V.validar("Te recomiendo el V99.", h) != [], True)
    caso("talla no respaldada", V.validar("Lo tengo en talla XL.", h) != [], True)
    caso("talla respaldada", V.validar("Lo tengo en talla M.", h), [])
    caso("descuento prohibido", V.validar("Te hago un descuento del 10%.", h) != [], True)
    # Guion de la tienda (seed/speech.md, 12-10-2026): hasta dos preguntas; tres solo si son talla/día-noche/estatura.
    caso("tres preguntas sueltas", V.validar("¿Te gusta? ¿Qué color? ¿Para cuándo?", h) != [], True)
    caso("dos preguntas del guion", V.validar("¿Cómo quieres sentirte ese día? ¿Prefieres llamar la atención?", h), [])
    caso("paso 3: talla, día/noche y estatura juntas",
         V.validar("¿En qué talla te encuentras?, ¿el evento es de día o de noche?, ¿cuánto mides de estatura?", h), [])
    caso("una pregunta", V.validar("¿Qué talla usas?", h), [])
    caso("pregunta repetida", V.validar("¿Qué talla usas?", h, "talla", ["talla"]) != [], True)
    caso("tela respaldada", V.validar("Es de tela roma, muy fresca.", h), [])
    caso("tela no respaldada", V.validar("Es de tela seda italiana.", h) != [], True)
    caso("promete fotos sin fotos", V.validar("Te paso las fotos ahora.", h) != [], True)
    h["fotos"] = {"V21"}
    caso("promete fotos con fotos", V.validar("Te paso las fotos ahora.", h), [])
    caso("plazo no respaldado", V.validar("Llega en 3 días.", h) != [], True)


# --- herramientas ------------------------------------------------------------
def prueba_herramientas():
    rec = H.Recolector(FICHAS, _stock, pie=lambda f, st: f"{f.codigo} {f.nombre}")
    r = rec.ejecutar("buscar_catalogo", {"consulta": "vestido negro"})
    caso("buscar_catalogo devuelve prendas", len(r["prendas"]) >= 1, True)
    caso("buscar_catalogo anota códigos", "V21" in rec.hechos["codigos"], True)
    caso("buscar_catalogo anota precio", 320.0 in rec.hechos["precios"], True)
    r = rec.ejecutar("stock", {"codigo": "V21"})
    caso("stock devuelve tallas", set(r["tallas"]) == {"S", "M"}, True)
    caso("stock anota tallas", {"S", "M"} <= rec.hechos["tallas"], True)
    r = rec.ejecutar("ficha_tecnica", {"codigo": "V35"})
    caso("ficha_tecnica anota tela", "roma" in rec.hechos["telas"], True)
    r = rec.ejecutar("enviar_fotos", {"codigos": ["V21", "V35"]})
    caso("enviar_fotos marca fotos", set(rec.hechos["fotos"]) == {"V21", "V35"}, True)
    caso("enviar_fotos arma sugerencias", len(rec.fotos), 2)
    # Reserva bloqueada si no está permitida (sombra).
    caso("reserva bloqueada", rec.ejecutar("crear_reserva", {"codigo": "V21", "talla": "M"}).get("error") is not None, True)
    rec2 = H.Recolector(FICHAS, _stock, permitir_reserva=True)
    caso("reserva permitida", rec2.ejecutar("crear_reserva", {"codigo": "V21", "talla": "M"}).get("ok"), True)
    caso("reserva anota el pedido", rec2.reserva, {"codigo": "V21", "talla": "M"})
    # Cita: fuera de horario y una válida.
    caso("cita fuera de horario", rec.ejecutar("agendar_cita", {"dia": "2030-01-02", "hora": "22:00"}).get("ok"), False)
    caso("cita válida", rec.ejecutar("agendar_cita", {"dia": "2030-01-02", "hora": "17:00"}).get("ok"), True)
    caso("derivar", rec.ejecutar("derivar_asesora", {"motivo": "no sé"}).get("ok"), True)
    caso("derivar marca", rec.derivar, True)
    caso("herramienta desconocida", rec.ejecutar("nope", {}).get("error") is not None, True)


def prueba_encuestas():
    """12-10-2026, pedido del usuario: encuesta solo con UNA pregunta; si se hicieron dos y contestó una, se pregunta la
    que falta."""
    from .agente import _con_la_que_falta, _opciones
    caso("dos preguntas en un «¿…?»: sin encuesta",
         _opciones("¿El bautizo es de día o de noche y cuánto mide de estatura?"), [])
    caso("dos «?»: sin encuesta", _opciones("¿Cómo quiere sentirse ese día? ¿Prefiere llamar un poco más la atención?"), [])
    caso("una pregunta cerrada: encuesta", _opciones("Perfecto 😊 ¿El envío sería para Lima o provincia?"), ["Lima", "Provincia"])
    hist = [{"rol": "cliente", "texto": "es para mi hermana, su bautizo"},
            {"rol": "bot", "texto": "¡Qué lindo detalle! 😊"},
            {"rol": "bot", "texto": "¿El bautizo es de día o de noche y cuánto mide de estatura?"}]
    caso("contestó una de dos: pregunta la otra (en tercera persona)",
         _con_la_que_falta("Perfecto 😊 ¿Cómo quiere sentirse ese día?", {"sabemos": {"horario": "noche"}}, hist),
         "Perfecto 😊 ¿Cómo quiere sentirse ese día? ¿Y cuánto mide de estatura?")
    caso("contestó las dos: no se agrega nada",
         _con_la_que_falta("¿Cómo quiere sentirse?", {"sabemos": {"horario": "noche", "estatura": "1.60"}}, hist),
         "¿Cómo quiere sentirse?")
    from .agente import RE_SENTIR_PARTIDA
    unida = RE_SENTIR_PARTIDA.sub(lambda m: f"{m.group(1)}: {m.group(2).lower()}",
                                  "Perfecto 😊 ¿Cómo quiere sentirse ella ese día? ¿Prefiere llamar un poco más la atención?")
    caso("la pregunta de noche partida en dos se une en una", unida,
         "Perfecto 😊 ¿Cómo quiere sentirse ella ese día: prefiere llamar un poco más la atención?")
    caso("unida, vuelve a tener encuesta", _opciones(unida), ["Llamar la atención", "Elegante y discreta"])
    # Escasez una sola vez y por caso; «Sí, tienes razón» a la encuesta lleva al envío; JSON nunca llega a la clienta.
    from . import plantillas, prompt
    from .agente import _sin_escasez_repetida, _sin_opciones
    hist_esc = [{"rol": "bot", "texto": "¡Uy, estamos muy pronto! 🤭 Sería genial dejarlo listo ya, mientras dispongo de stock."},
                {"rol": "bot", "texto": "📊 ¿No lo consideras? · Sí, tienes razón / Lo voy a pensar"}]
    caso("la encuesta queda solo con su pregunta", _sin_opciones(hist_esc[1])["texto"], "¿No lo consideras?")
    caso("escasez ya dicha: no se repite; pasa al envío",
         _sin_escasez_repetida("Sería genial tenerlo listo mientras dispongo de stock. ¿No lo consideras? 💛", {"sabemos": {}}, hist_esc),
         "Excelente 😊 ¿El envío sería para Lima o provincia?")
    caso("plantilla de fecha no repite la escasez", plantillas.ya_hubo_escasez(hist_esc), True)
    caso("JSON con salto de línea literal se lee",
         prompt.leer_salida('{"respuesta": "Hola\nque tal", "pregunta_hecha": "fecha"}'), {"respuesta": "Hola\nque tal", "pregunta_hecha": "fecha"})
    caso("resto de JSON se limpia", prompt.leer_salida('¿No lo consideras? 💛","pregunta_hecha":"fecha"}')["respuesta"], "¿No lo consideras? 💛")
    caso("JSON cerrado con comilla invertida se limpia (13-10)", prompt.leer_salida('Wooow, muy pronto. ¿No lo consideras?`, "pregunta_hecha":""')["respuesta"],
         "Wooow, muy pronto. ¿No lo consideras?")
    from .agente import _pregunta_sabida
    caso("pregunta la estatura que ya sabe: se rechaza", bool(_pregunta_sabida("¿Cuánto mides? Así te digo el largo.", {"sabemos": {"estatura": "1.78"}})), True)
    caso("pregunta la fecha sin saberla: pasa", _pregunta_sabida("¿Para cuándo lo necesitas?", {"sabemos": {}}), [])
    caso("no contestó ninguna: no se insiste aquí", _con_la_que_falta("¿Me cuentas más?", {"sabemos": {}}, hist),
         "¿Me cuentas más?")


def main() -> int:
    prueba_ficha()
    prueba_validador()
    prueba_herramientas()
    prueba_encuestas()
    if FALLOS:
        print("FALLOS V3:")
        for f in FALLOS:
            print(" -", f)
        return 1
    print("v3  ficha, validador y herramientas: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
