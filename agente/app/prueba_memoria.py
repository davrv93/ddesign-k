"""Pruebas de la memoria de la conversación (app/memoria.py), sin red ni LLM. Se ejecutan al construir la imagen
y a mano:

    python3 -m app.prueba_memoria

Cubren: extracción por reglas, la pregunta pendiente (el hilo), la siguiente pregunta que elige el código, que
no se repitan preguntas y que la memoria se reconstruya del historial cuando quien llama no la manda.
"""
from __future__ import annotations

import json

import sys

from . import memoria as M
from .etapas import decidir

fallos: list[str] = []
total = 0


def caso(nombre: str, obtenido, esperado) -> None:
    global total
    total += 1
    if obtenido != esperado:
        fallos.append(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


def con(**sabemos) -> dict:
    m = M.nueva()
    m["sabemos"].update(sabemos)
    return m


# --- 1. Extracción por reglas ---------------------------------------------------------------------------
EXTRAER = [
    ("soy talla M", "", {"talla": "M"}),
    ("uso la S", "", {"talla": "S"}),
    ("M", "talla", {"talla": "M"}),
    ("mediana", "talla", {"talla": "M"}),
    ("mido 1.60 m", "", {"estatura": "1.60"}),          # la «m» de metros no es la talla M
    ("1,65", "estatura", {"estatura": "1.65"}),
    ("mido 158", "", {"estatura": "1.58"}),
    ("es para un matrimonio", "", {"ocasion": "matrimonio"}),
    ("para mi graduación", "", {"ocasion": "graduacion"}),
    ("de noche", "horario", {"horario": "noche"}),
    ("en la tarde", "horario", {"horario": "dia"}),
    ("buenas noches, una consulta", "", {}),             # el saludo no dice que el evento sea de noche
    ("el 15 de noviembre", "", {"fecha": "15 de noviembre"}),
    ("este sábado", "", {"fecha": "este sabado"}),
    ("puedo ir hoy?", "", {}),                           # «hoy» solo es fecha si se preguntó para cuándo
    ("hoy mismo", "fecha", {"fecha": "hoy"}),
    ("provincia, Arequipa", "", {"envio": "provincia", "ciudad": "Arequipa"}),
    ("para Lima", "lima_o_provincia", {"envio": "lima", "ciudad": "Lima"}),
    ("¿envían a Lima o a provincia?", "", {}),
    ("era fucsia, satinado y largo", "describir_prenda", {"color": "fucsia"}),
    ("tengo 200 soles de presupuesto", "", {"presupuesto": "200"}),
    ("oh si", "cual_prenda", {}),
    ("a ver un momento", "cual_prenda", {}),
    ("ahora te digo el nombre", "cual_prenda", {}),
    ("Sí, me interesa", "talla", {}),
]
for texto, pend, esperado in EXTRAER:
    caso(f"extraer «{texto}»", M.extraer(texto, pend), esperado)

# --- 2. Qué preguntó el bot -----------------------------------------------------------------------------
DETECTAR = [
    ("¡Hola! 😊\n\n¿Me compartes la foto o el nombre del vestido que viste? 📸 Así reviso al toque si lo tenemos.", "cual_prenda"),
    ("No te preocupes 😊 Cuéntame cómo era: el color, si era largo o corto", ""),   # sin «?» no hay pregunta…
    ("🧾 *Resumen de tu pedido #3*\n\n• Total: *S/ 260.00*\n\nTe la apartamos por *10 minutos* ⏳\n¿Confirmas tu pedido? "
     "Responde *SI* para confirmar o *NO* para cancelar.\n(Para cambiar la cantidad, escribe el número.)", "confirmar"),
    ("¡Perfecto! 🙌 *V42* Vestido Gala Capa Azul\nTalla *M*\n\n¿Confirmamos tu pedido?", "confirmar"),
    ("¿Quieres ver otras opciones? 👀 Responde *SI*", "otras_opciones"),
    ("¿El envío sería para *Lima* o para *provincia*? 🚚", "lima_o_provincia"),
    ("¡Qué bonito! 💙\n\n¿El evento es de día o de noche?", "horario"),
    ("El *V42* cuesta *S/ 260.00*.\n\n¿Qué es lo que más te gustó del modelo?", "que_le_gusto"),
    ("¿Para cuándo es el matrimonio? Así te ayudo a planificar", "fecha"),
    ("¿Te gustaría que te lo separemos?", "separar"),
    ("¿Qué talla sueles usar normalmente?", "talla"),
    ("Te paso las fotos 😊", ""),
    ("Hay en S y M. ¿Para qué ocasión lo tienes en mente? Y dime, ¿el evento es de día o de noche?", "horario"),  # la última
]
for texto, k in DETECTAR:
    caso(f"detecta «{texto[:40]}»", M.pregunta_de(texto), k)
# Cada pregunta que hace el código se reconoce a sí misma: si no, no quedaría anotada como pendiente.
for k, q in M.PREGUNTAS.items():
    caso(f"la pregunta de {k} se reconoce", M.clave_de(q), k)

# --- 3. La pendiente: se responde, se espera o se repregunta, sin inventar ---------------------------------
m = M.nueva(); m["pendiente"] = "cual_prenda"
r = M.leer(m, "oh si")
caso("«oh sí» no responde «¿cuál es?»", (r["respondio"], r["describe"], m["pendiente"]), (False, False, "cual_prenda"))
r = M.leer(m, "a ver un momento")
caso("«a ver un momento» espera", (r["respondio"], r["espera"]), (False, True))
r = M.leer(m, "ahora te digo el nombre")
caso("«ahora te digo el nombre» espera", (r["respondio"], r["espera"], r["sin_dato"]), (False, True, False))
r = M.leer(m, "no la tengo pero")
caso("«no la tengo» no puede decir cuál", (r["respondio"], r["sin_dato"]), (False, True))
m["pendiente"] = "describir_prenda"
r = M.leer(m, "era fucsia, satinado y largo")
caso("una descripción responde", (r["respondio"], r["describe"], m["pendiente"], m["sabemos"]["color"]), (True, True, "", "fucsia"))
m = M.nueva(); m["pendiente"] = "cual_prenda"
r = M.leer(m, "algo como de princesa con cola", jev={"responde": True})
caso("Jev propone que sí describe", r["describe"], True)
r = M.leer(m, "ahorita te lo paso", jev={"responde": True})
caso("Jev no gana a «ahorita te lo paso»", r["describe"], False)

m = M.nueva(); m["pendiente"] = "ocasion"
r = M.leer(m, "es para un matrimonio")
caso("ocasión respondida", (r["respondio"], m["pendiente"], m["sabemos"]["ocasion"]), (True, "", "matrimonio"))
m["pendiente"] = "horario"
r = M.leer(m, "de noche")
caso("día/noche respondido", (r["respondio"], m["sabemos"]["horario"]), (True, "noche"))
m["pendiente"] = "talla"
r = M.leer(m, "Sí, me interesa")
caso("«sí, me interesa» no es la talla", (r["respondio"], m["pendiente"], m["sabemos"]["talla"]), (False, "talla", None))
r = M.leer(m, "la fiesta es en la noche")
caso("lo general no pisa lo concreto", (m["sabemos"]["ocasion"], m["sabemos"]["horario"]), ("matrimonio", "noche"))
m["pendiente"] = "ocasion"
M.leer(m, "en realidad es una graduación")
caso("corregir la ocasión cuando se pregunta", m["sabemos"]["ocasion"], "graduacion")
m = M.nueva(); m["pendiente"] = "que_le_gusto"
M.leer(m, "la capa, me encanta")
caso("lo que le gustó se guarda", m["sabemos"]["le_gusto"], "la capa, me encanta")
m = M.nueva(); m["pendiente"] = "talla"
M.leer(m, "x", jev={"talla": "L"})
caso("Jev propone la talla si las reglas no la ven", m["sabemos"]["talla"], "L")
m = M.nueva(); m["pendiente"] = "talla"
M.leer(m, "soy talla M", jev={"talla": "L"})
caso("las reglas mandan sobre Jev", m["sabemos"]["talla"], "M")

# --- 4. Registrar lo que preguntó el bot ------------------------------------------------------------------
m = M.nueva()
M.registrar_respuesta(m, "¡Qué bonito!\n\n¿El evento es de día o de noche?")
caso("anota la pendiente y lo preguntado", (m["pendiente"], m["preguntado"]), ("horario", ["horario"]))
M.registrar_respuesta(m, "Cuesta S/ 260 😊")
caso("sin pregunta, una pendiente de dato se suelta", m["pendiente"], "")
M.registrar_respuesta(m, "¡Dale! Aquí te espero", forzar="cual_prenda")
M.registrar_respuesta(m, "Quedamos en la tienda de 9 a 19 h.")
caso("«¿cuál es?» no se suelta sola", m["pendiente"], "cual_prenda")
M.registrar_respuesta(m, "¡Perfecto!", forzar="confirmar")
caso("el código fuerza la pendiente", m["pendiente"], "confirmar")

# --- 5. La siguiente pregunta la elige el código ----------------------------------------------------------
# Método de venta (04-10-2026): ocasión → para cuándo (urgencia) → día/noche; la talla, después de mostrar una prenda;
# y el cierre ofrece probárselo o separarlo (`probar`), que reemplazó a «¿qué te gustó?» y «¿separarlo?».
# Sin ninguna necesidad contada (solo «hola»): primero «¿qué estás buscando?»; la ocasión cuando diga qué busca.
caso("solo saludó: primero qué busca", M.siguiente(M.nueva(), "prospeccion"), "que_busca")
caso("dijo la prenda: la ocasión", M.siguiente(con(prenda="vestido"), "prospeccion"), "ocasion")
caso("sabida la ocasión, para cuándo (la urgencia)", M.siguiente(con(ocasion="matrimonio"), "prospeccion"), "fecha")
m = con(prenda="vestido"); m["preguntado"] = ["ocasion"]
caso("preguntada una vez sin respuesta: se repite (indagar)", M.siguiente(m, "prospeccion"), "ocasion")
m = M.nueva(); m["preguntado"] = ["que_busca"]
caso("«¿qué buscas?» sin respuesta: se repite una vez, con otras palabras",
     (M.siguiente(m, "prospeccion"), M.clave_de(M.texto_pregunta("que_busca", m))), ("que_busca", "que_busca"))
m = M.nueva(); m["preguntado"] = ["que_busca", "que_busca"]
# Ajustado (05-10-2026, prueba de regresión): este caso pedía pasar a «¿es para alguna ocasión especial?» tras dos
# «¿qué buscas?» sin respuesta. Es la queja literal de la tienda («hola» → «¿Es para alguna ocasión especial?» sin saber
# qué busca): a quien solo saluda tres veces no se le pregunta la ocasión. Sin nada contado, no se insiste más.
caso("tras dos «¿qué buscas?» sin respuesta: no se pregunta la ocasión", M.siguiente(m, "prospeccion"), "")
caso("«tengo un evento» tras el saludo: «¿Qué evento es?»",
     (M.siguiente(con(), "prospeccion", None, "tengo un evento"), M.texto_pregunta("ocasion", con(), "tengo un evento")),
     ("ocasion", "¿Qué evento es?"))
caso("«ya pues la S» trae la talla", M.extraer("ya ps la S").get("talla"), "S")
# Ajustado (04-10-2026, prueba con conversaciones): el caso pedía pasar a la fecha tras dos intentos, pero sin ninguna
# necesidad contada el bot terminaba preguntando «¿para cuándo lo necesitas?» a quien solo saludó. Con la prenda dicha
# se sigue con la fecha como antes; sin nada, una pregunta abierta.
m = con(prenda="vestido"); m["preguntado"] = ["ocasion", "ocasion"]
caso("preguntada dos veces sin respuesta: no se insiste más", M.siguiente(m, "prospeccion"), "fecha")
m = M.nueva(); m["preguntado"] = ["ocasion", "ocasion"]
caso("solo saludó y no contestó la ocasión dos veces: pregunta abierta", M.siguiente(m, "prospeccion"), "que_busca")
m["preguntado"] += ["que_busca", "que_busca"]
caso("agotadas la abierta y la ocasión, no se insiste", M.siguiente(m, "prospeccion"), "")
caso("sin prenda ni ocasión, «¿para cuándo sería?» (sin «lo»)", M.texto_pregunta("fecha", M.nueva()), "¿Para cuándo sería?")
caso("«es este finde» es la fecha", M.extraer("es este finde", "fecha").get("fecha"), "este fin de semana")
caso("sabidas ocasión y fecha: día/noche", M.siguiente(con(ocasion="boda", fecha="el sabado"), "prospeccion"), "horario")
# Regla del dueño (04-10-2026): la talla de un pedido anterior no es la de hoy. Este caso decía «talla del perfil: no se
# pregunta» y el bot armó un pedido en M que la clienta tuvo que corregir («mi talla es L disculpa»). Ahora se pregunta,
# sugiriéndole la de antes.
_mp = M.con_perfil(con(ocasion="boda", horario="noche", fecha="el 17"), {"tallas": ["M"]})
caso("talla del perfil: se pregunta (no se asume)", M.siguiente(_mp, "prospeccion", True), "talla")
caso("talla del perfil: no pasa a «sabemos»", (_mp["sabemos"]["talla"], _mp["talla_perfil"]), (None, "M"))
caso("talla del perfil: la pregunta la sugiere y se reconoce", ("*M*" in M.texto_pregunta("talla", _mp), M.clave_de(M.texto_pregunta("talla", _mp))),
     (True, "talla"))
_mp["pendiente"] = "talla"
caso("«si la misma» toma la talla del perfil", M.leer(_mp, "si la misma")["datos"].get("talla"), "M")
_mp = M.con_perfil(M.nueva(), {"tallas": ["M"]}); _mp["pendiente"] = "talla"
caso("otra talla manda sobre la del perfil", M.leer(_mp, "no, mejor L")["datos"].get("talla"), "L")
caso("perfil con tipos equivocados no rompe", M.con_perfil(M.nueva(), {"tallas": 5, "productos": "x"})["talla_perfil"], "")
caso("sin prenda mostrada, la talla no se pregunta", M.siguiente(con(ocasion="boda", horario="noche", fecha="el 17"), "prospeccion"), "")
caso("con prenda mostrada, la talla sí", M.siguiente(con(ocasion="boda", horario="noche", fecha="el 17"), "prospeccion", True), "talla")
m = con(ocasion="boda", horario="noche", fecha="el 17"); m["producto"] = "V42"; m["temperatura"] = "tibio"
caso("seguimiento tibio: la talla antes del cierre", M.siguiente(m, "seguimiento"), "talla")
m["preguntado"] += ["talla"]
caso("seguimiento tibio: sin talla no se empuja el cierre", M.siguiente(m, "seguimiento"), "")
m["sabemos"]["talla"] = "M"
caso("seguimiento tibio: con talla, ¿probártelo o separarlo?", M.siguiente(m, "seguimiento"), "probar")
m["preguntado"] += ["probar"]
caso("seguimiento: no queda nada que preguntar", M.siguiente(m, "seguimiento"), "")
m = con(ocasion="boda", horario="noche", fecha="mañana"); m["producto"] = "V42"; m["temperatura"] = "caliente"
caso("seguimiento caliente: el cierre antes que la talla", M.siguiente(m, "seguimiento"), "probar")
m = con(ocasion="boda", horario="noche", fecha="el otro año", talla="M"); m["producto"] = "V42"; m["temperatura"] = "frio"
caso("seguimiento frío: no se empuja el cierre", M.siguiente(m, "seguimiento"), "")
m = con(ocasion="boda", horario="noche", fecha="el 17", talla="M", cita="2026-10-09T17:00"); m["producto"] = "V42"; m["temperatura"] = "caliente"
caso("con cita hecha no se vuelve a ofrecer", M.siguiente(m, "seguimiento"), "")
caso("cierre con talla: confirmar", M.siguiente(con(talla="M"), "cierre"), "confirmar")
caso("venta confirmada: Lima o provincia", M.siguiente(M.nueva(), "venta_confirmada"), "lima_o_provincia")
caso("venta confirmada con envío: pago", M.siguiente(con(envio="provincia"), "venta_confirmada"), "pago")

# --- 6. Ninguna pregunta repetida, aunque el LLM lo intente -----------------------------------------------
m = M.nueva(); m["preguntado"] = ["que_le_gusto"]
caso("quita la pregunta ya hecha", M.quitar_repetidas("Cuesta *S/ 260.00*.\n\n¿Qué es lo que más te gustó del modelo?", m), "Cuesta *S/ 260.00*.")
caso("deja la permitida", M.quitar_repetidas("¿Qué es lo que más te gustó del modelo?", m, permitida="que_le_gusto"),
     "¿Qué es lo que más te gustó del modelo?")
caso("quita lo ya sabido", M.quitar_repetidas("¡Qué lindo! ¿Para qué ocasión lo buscas?", con(ocasion="matrimonio")), "¡Qué lindo!")
caso("se lleva el porqué que cuelga de la pregunta",
     M.quitar_repetidas("Cuesta *S/ 260.00*.\n\n¿Para cuándo es el matrimonio? Así te aseguras con tiempo.",
                        dict(M.nueva(), preguntado=["fecha"])), "Cuesta *S/ 260.00*.")
caso("no deja el mensaje vacío", M.quitar_repetidas("¿Para qué ocasión lo buscas?", con(ocasion="boda")), "¿Para qué ocasión lo buscas?")

# --- 7. Etapas: el «sí» se lee contra la pendiente --------------------------------------------------------
d = decidir("cierre", "saludo", 0.4, "sí", "", pendiente="confirmar")
caso("«sí» con pendiente confirmar", (d["etapa"], d["intent"]), ("venta_confirmada", "confirmacion_compra"))
d = decidir("cierre", "saludo", 0.4, "sí", "¿Confirmamos tu pedido?", pendiente="horario")
caso("la pendiente manda sobre el texto", (d["etapa"], d["intent"]), ("cierre", "interesado"))
d = decidir("cierre", "otro", 0.3, "no", "", pendiente="confirmar")
caso("«no» con pendiente confirmar", d["intent"], "cancelacion")

# --- 8. Reconstrucción (quien llama no manda memoria) y normalización ---------------------------------------
hist = [{"rol": "cliente", "texto": "Hola, quisiera saber si todavía tienen este vestido"},
        {"rol": "bot", "texto": "¡Hola! Sí lo tenemos 😊"}, {"rol": "bot", "texto": "¿Para qué ocasión lo buscas?"},
        {"rol": "cliente", "texto": "es para un matrimonio"},
        {"rol": "bot", "texto": "¡Qué bonito!"}, {"rol": "bot", "texto": "¿El evento es de día o de noche?"}]
m = M.reconstruir(hist, "prospeccion")
caso("reconstruye pendiente, sabido y preguntado", (m["pendiente"], m["sabemos"]["ocasion"], m["preguntado"]),
     ("horario", "matrimonio", ["ocasion", "horario"]))
m = M.normalizar({"pendiente": "inventada", "sabemos": {"talla": "M", "otra": 1}, "preguntado": "no es lista"})
caso("normaliza lo que llega", (m["pendiente"], m["sabemos"]["talla"], "otra" in m["sabemos"], m["preguntado"]), ("", "M", False, []))
caso("perfil no pisa lo de hoy", M.con_perfil(con(talla="S"), {"tallas": ["M"]})["sabemos"]["talla"], "S")

# --- 9. Guion b) sin LLM (desde el anuncio del V42): el bot siempre pregunta lo que dice `siguiente`; ninguna
# pregunta se repite; la temperatura se calcula en cada turno con «hoy» fijo ------------------------------------
import datetime as _dt
HOY = _dt.date(2026, 10, 4)                                    # domingo
AHORA = _dt.datetime(2026, 10, 4, 18, 40, tzinfo=M.LIMA)
m, etapa, hechas = M.nueva(), "prospeccion", []
m["producto"] = "V42"
GUION = [("Hola, quisiera saber si todavía tienen este vestido", "prospeccion", ""), ("es para un matrimonio", "prospeccion", ""),
         ("el sábado 17", "prospeccion", ""), ("de noche", "prospeccion", ""), ("Sí, me interesa", "seguimiento", "interesado"),
         ("¿cuánto cuesta?", "seguimiento", "consulta_precio"), ("¿cómo es el material?", "seguimiento", "consulta_material"),
         ("soy talla M", "seguimiento", "consulta_talla")]
for msg, etapa, intent in GUION:
    M.leer(m, msg, ahora=AHORA)
    M.anotar_senales(m, msg, intent)
    M.temperatura(m, HOY)
    k = M.siguiente(m, etapa)
    if k:
        hechas.append(k)
        M.registrar_respuesta(m, "Respuesta.\n\n" + M.texto_pregunta(k, m, msg))
    else:
        M.registrar_respuesta(m, "Respuesta sin pregunta.")
caso("guion b): preguntas en orden y sin repetir", hechas, ["ocasion", "fecha", "horario", "talla", "probar"])
caso("guion b): lo que sabemos al llegar al cierre", {k: v for k, v in m["sabemos"].items() if v},
     {"ocasion": "matrimonio", "fecha": "el sabado 17", "fecha_iso": "2026-10-17", "horario": "noche", "prenda": "vestido", "talla": "M"})
caso("guion b): tibia por la fecha", (m["temperatura"], m["temperatura_motivo"].split(";")[0]), ("tibio", "evento el 17-oct (en 13 días)"))

# --- 10. Fechas a ISO, con «hoy» fijo (domingo 4-oct-2026) ------------------------------------------------------
FECHAS = [
    ("el 18 de octubre", "2026-10-18"), ("el 2 de octubre", "2027-10-02"),     # si ya pasó este año, el siguiente
    ("este sábado", "2026-10-10"), ("el sábado 17", "2026-10-17"), ("el sábado 18", "2026-10-18"),   # manda el número
    ("el próximo viernes", "2026-10-09"), ("el domingo", "2026-10-11"), ("este domingo", "2026-10-04"),
    ("el otro sábado", "2026-10-17"), ("en dos semanas", "2026-10-18"), ("dentro de 3 días", "2026-10-07"),
    ("mañana", "2026-10-05"), ("pasado mañana", "2026-10-06"), ("hoy", "2026-10-04"),
    ("el 15/11", "2026-11-15"), ("15/11/2027", "2027-11-15"), ("el 3/10", "2027-10-03"),
    ("este fin de semana", "2026-10-04"), ("la próxima semana", "2026-10-11"), ("para noviembre", "2026-11"),
    ("en septiembre", "2027-09"), ("para el próximo año", "2027"), ("fin de mes", "2026-10-31"),
    ("el 9", "2026-10-09"), ("el 2", "2026-11-02"), ("el viernes a las 5", "2026-10-09"),
    ("a las 10 de la mañana", None), ("el 31 de febrero", None), ("es un matrimonio", None),
]
for texto, iso in FECHAS:
    caso(f"fecha «{texto}»", M.fecha_iso(texto, HOY), iso)
caso("fin de semana dicho un lunes", M.fecha_iso("este fin de semana", _dt.date(2026, 10, 5)), "2026-10-10")
r = M.leer(M.nueva(), "es el sábado 17 de octubre", ahora=AHORA)
caso("leer guarda la fecha y la normaliza", {k: v for k, v in r["datos"].items() if k.startswith("fecha")},
     {"fecha": "el sabado 17 de octubre", "fecha_iso": "2026-10-17"})
caso("«lo necesito para mañana» es fecha aunque no se preguntó", M.extraer("lo necesito para mañana").get("fecha"), "manana")

# --- 11. Horas y validación de la cita (showroom L–D 9–19 h, refrigerio 13–14 h) -----------------------------------
HORAS = [("a las 5", "17:00"), ("a la 1 y media", "13:30"), ("a las 10", "10:00"), ("a las 10 de la mañana", "10:00"),
         ("17:30", "17:30"), ("a las 4 y cuarto", "16:15"), ("5pm", "17:00"), ("al mediodía", "12:00"),
         ("a las 8 de la noche", "20:00"), ("a las 7", "19:00"), ("el viernes", None)]
for texto, h in HORAS:
    caso(f"hora «{texto}»", M.hora_en(texto), h)
VALIDAR = [(("2026-10-09", "17:00"), ""), (("2026-10-09", "13:30"), "refrigerio"), (("2026-10-09", "19:00"), "fuera_horario"),
           (("2026-10-09", "08:30"), "fuera_horario"), (("2026-10-09", "18:30"), ""), (("2026-10-04", "18:00"), "hora_pasada"),
           (("2026-10-03", "10:00"), "dia_pasado"), (("2026-10-09", "12:30"), ""), (("2026-10-09", "14:00"), "")]
for (dia, hora), err in VALIDAR:
    caso(f"cita {dia} {hora}", M.validar_cita(dia, hora, AHORA), err)
caso("cita después del evento", M.validar_cita("2026-10-20", "10:00", AHORA, "2026-10-17"), "despues_evento")

m = con(fecha="el sabado 17", fecha_iso="2026-10-17"); m["pendiente"] = "cita"
r = M.leer(m, "el viernes a la 1 y media", ahora=AHORA)
caso("cita en refrigerio: no vale, el día se queda", (r["cita"]["error"], m["cita_tentativa"], m["sabemos"]["cita"], m["pendiente"]),
     ("refrigerio", {"dia": "2026-10-09", "hora": None}, None, "cita"))
r = M.leer(m, "mejor a las 5 entonces", ahora=AHORA)
caso("otra hora completa la cita", (r["cita"]["ok"], r["respondio"], m["sabemos"]["cita"], m["pendiente"]),
     (True, True, "2026-10-09T17:00", ""))
caso("el día de la cita no pisa la fecha del evento", (m["sabemos"]["fecha"], m["sabemos"]["fecha_iso"]), ("el sabado 17", "2026-10-17"))
m = M.nueva()
r = M.leer(m, "quiero ir a probármelo el sábado a las 11", ahora=AHORA)
caso("cita pedida con día y hora en un mensaje", (r["es_cita"], m["sabemos"]["cita"], m["sabemos"]["fecha"]), (True, "2026-10-10T11:00", None))
m = M.nueva(); m["pendiente"] = "probar"
r = M.leer(m, "sí", ahora=AHORA)
caso("«sí» a ¿probártelo o separarlo? es pedir cita", (r["es_cita"], r["respondio"], r["cita"]["dato"]), (True, True, False))
m = M.nueva(); m["pendiente"] = "cita"
r = M.leer(m, "¿tienen estacionamiento?", ahora=AHORA)
caso("una pregunta no suelta la cita pendiente", (r["respondio"], m["pendiente"]), (False, "cita"))
caso("la pendiente cita no se suelta sola", (M.registrar_respuesta(m, "Sí, hay estacionamiento cerca 😊"), m["pendiente"]), ("cita", "cita"))
for q in ("¿A qué hora te acomoda el viernes 9 de octubre?", "¿Qué día te acomoda venir a las 5:00 p. m.?",
          "¿Te acomoda a las 12:30 p. m. o desde las 2:00 p. m.?"):
    caso(f"detecta cita «{q[:30]}»", M.pregunta_de(q), "cita")

# --- 12. Temperatura: por fecha (hoy fijo) y por señales -----------------------------------------------------------
def temp(fecha_iso=None, senales=(), **sab):
    mm = con(fecha_iso=fecha_iso, **sab); mm["senales"] = list(senales)
    return M.temperatura(mm, HOY)[0]

caso("sin datos: fría", (temp(), M.temperatura(M.nueva(), HOY)[1]), ("frio", M.SIN_DATOS))
for iso, t in [("2026-10-04", "caliente"), ("2026-10-10", "caliente"), ("2026-10-11", "caliente"), ("2026-10-12", "tibio"),
               ("2026-10-17", "tibio"), ("2026-11-03", "tibio"), ("2026-11-04", "frio"), ("2026-11", "tibio"), ("2027", "frio")]:
    caso(f"temperatura por fecha {iso}", temp(iso), t)
caso("solo viendo: fría", temp(senales=["frio"]), "frio")
caso("preguntó precio: tibia", temp(senales=["interes"]), "tibio")
caso("pidió cita: caliente", temp(senales=["cita"]), "caliente")
caso("urgente: caliente", temp(senales=["urgente"]), "caliente")
caso("dijo «solo viendo» y luego pregunta el precio: sigue fría", temp(senales=["frio", "interes"]), "frio")
caso("dijo «solo viendo» y luego quiere comprarlo: caliente", temp(senales=["frio", "interes", "compra"]), "caliente")
caso("evento lejano pero pregunta el precio: tibia", temp("2026-12-20", ["interes"]), "tibio")
caso("quería comprar y luego «más adelante»: fría", temp(senales=["compra", "frio"]), "frio")
caso("evento en 5 días manda sobre «solo viendo»", temp("2026-10-09", ["frio"]), "caliente")
caso("con cita hecha: caliente", temp(cita="2026-10-09T17:00"), "caliente")
SENALES = [("solo estoy viendo", "", ["frio"]), ("no es urgente", "", ["frio"]), ("lo necesito urgente", "", ["urgente"]),
           ("lo necesito para este fin de semana", "", ["urgente"]), ("¿cuánto cuesta?", "consulta_precio", ["interes"]),
           ("ya, resérvamelo", "intencion_compra", ["compra"]), ("hola", "saludo", [])]
for texto, intent, esperado in SENALES:
    caso(f"señal «{texto}»", M.anotar_senales(M.nueva(), texto, intent), esperado)
m = M.nueva()
M.leer(m, "solo estoy viendo, es para el próximo año", ahora=AHORA)
M.anotar_senales(m, "solo estoy viendo, es para el próximo año", "objecion")
caso("caso frío completo", (M.temperatura(m, HOY)[0], m["sabemos"]["fecha_iso"]), ("frio", "2027"))

# --- 13. Indagar antes de ofrecer --------------------------------------------------------------------------------
caso("«tengo un evento» es una necesidad", M.en_necesidad(M.nueva(), "Hola, tengo un evento"), True)
caso("«¿tienen blazers?» no es contar una necesidad", M.en_necesidad(M.nueva(), "¿tienen blazers?"), False)
caso("solo la ocasión no basta para ofrecer", M.necesidad_conocida(con(ocasion="matrimonio")), False)
caso("ocasión, fecha y día/noche: se ofrece", M.necesidad_conocida(con(ocasion="matrimonio", fecha="el 17", horario="noche")), True)
m = con(ocasion="matrimonio"); m["preguntado"] = ["ocasion", "fecha", "horario"]
caso("fecha y día/noche preguntados una sola vez: todavía se insiste", M.necesidad_conocida(m), False)
m = con(ocasion="matrimonio"); m["preguntado"] = ["ocasion", "fecha", "fecha", "horario", "horario"]
caso("lo preguntado dos veces y no sabido no frena la oferta", M.necesidad_conocida(m), True)
m = M.nueva(); m["preguntado"] = ["ocasion", "fecha", "horario"]
caso("sin ocasión ni fecha no hay qué ofrecer", M.necesidad_conocida(m), False)
caso("pide ver: «muéstrame opciones»", M.pide_ver("muéstrame opciones"), True)
caso("«es un matrimonio» no pide ver", M.pide_ver("es un matrimonio"), False)
caso("«tengo un evento» → ¿Qué evento es?", (M.texto_pregunta("ocasion", M.nueva(), "tengo un evento"),
                                              M.clave_de(M.texto_pregunta("ocasion", M.nueva(), "tengo un evento"))), ("¿Qué evento es?", "ocasion"))
q = M.texto_pregunta("fecha", con(ocasion="matrimonio"))
caso("¿Para cuándo es el matrimonio?", (q, M.clave_de(q)), ("¿Para cuándo es el matrimonio?", "fecha"))
caso("la prenda que busca se guarda", M.extraer("busco un conjunto para la oficina").get("prenda"), "conjunto")

# Saludo, «busco un vestido», «quiero ver los modelos» (04-10-2026, chat real): el bot preguntaba por un «lo» que no
# existía, daba la ocasión por preguntada sin respuesta y mostraba tres vestidos sin saber para qué.
q = M.texto_pregunta("ocasion", M.nueva(), "hoola")
# Ajustado (05-10-2026): el texto era «¿Es para alguna ocasión especial?», la frase de la que se quejó la tienda.
caso("saludo: la ocasión sin «lo» huérfano", (q, M.clave_de(q)), ("¿Para qué ocasión sería?", "ocasion"))
m = M.nueva(); m["preguntado"] = ["que_busca", "que_busca"]
caso("«quisiera ropa formal» con la abierta agotada: la ocasión",
     M.siguiente(m, "prospeccion", None, "Quisiera ropa formal para mi pareja"), "ocasion")
q = M.texto_pregunta("ocasion", con(prenda="vestido"), "busco un vestido")
caso("con prenda: ¿para qué ocasión buscas el vestido?", (q, M.clave_de(q)), ("¿Para qué ocasión buscas el vestido?", "ocasion"))
m = con(prenda="vestido"); M.registrar_respuesta(m, "¿Para qué ocasión buscas el vestido?")
caso("ocasión sin contestar: se vuelve a preguntar una vez", M.siguiente(m, "prospeccion"), "ocasion")
q = M.texto_pregunta("ocasion", m, "busco un vestido")
caso("la segunda vez, con otras palabras", (M.clave_de(q), q.startswith("Cuéntame")), ("ocasion", True))
M.registrar_respuesta(m, q)
caso("se cuentan los dos intentos", M.veces(m, "ocasion"), 2)
m["sabemos"]["prenda"] = "vestido"   # dijo «busco un vestido»: hay necesidad, se sigue con la fecha
caso("tras dos intentos sin respuesta, sigue con la fecha", M.siguiente(m, "prospeccion"), "fecha")
caso("pide ver: «me gustaría ver los modelos»", M.pide_ver("me gustaria ver los modelos"), True)
caso("pide ver: «muéstrame el catálogo»", M.pide_ver("muéstrame el catálogo"), True)
m = con(ocasion="matrimonio"); m["pidio_ver"] = True
caso("pidió ver y ya dijo la ocasión: se le muestra", M.necesidad_conocida(m), True)
caso("pidio_ver sobrevive al viaje", M.normalizar(json.loads(json.dumps(m)))["pidio_ver"], True)

# Prueba con conversaciones (04-10-2026): «¿qué tela es?» del Pandora, cuya ficha no dice la tela, recibía «satín»
# inventado; Jev lo quitaba y la clienta se quedaba sin respuesta. La tela sale de la ficha o se dice que no figura.
from . import venta as V  # noqa: E402
caso("tela: «Tipo de tela Roma»", V.tela("V35", "Hecho de nuestra increíble tela roma. Detalles: Tipo de tela Roma"), "tela Roma")
caso("tela: «hecha de GASA»", V.tela("V39", "La blusa hecha de GASA color verde oscuro."), "gasa")
caso("tela: «tejido de lino prada»", V.tela("V28", "la ligereza que le dota el tejido de lino prada en el que está"), "lino prada")
caso("tela: solo las mangas no son la tela", V.tela("V27", "Su manga en organza francesa la convierte en ideal"), "")
caso("tela: la ficha no la dice", V.tela("V31", "Clásico atemporal con escote corazón y cremallera."), "")
caso("tela que no figura: deriva, no inventa", "asesora" in V.respuesta_tela("V31", "Vestido Pandora", "Clásico con cremallera."), True)
caso("tela que figura: la dice", V.respuesta_tela("V35", "Vestido Irla", "Tipo de tela Roma"), "El *V35* Vestido Irla es de tela Roma 😊")


# Cita (prueba con conversaciones, 04-10-2026): la fecha del evento dicha junto con el pedido de cita no es el día de la
# cita; de dos fechas en un mensaje manda la última («el 25 no puedo, ¿el 24?»); y «el sábado 23» cuando el 23 es
# viernes se pregunta, no se elige.
import datetime as _dt  # noqa: E402
_ah = _dt.datetime(2026, 10, 4, 10, 0)
m = M.nueva()
r = M.leer(m, "busco un vestido para una entrevista que tengo el 25 de octubre. me gustaría ir a probármelo antes, ¿puedo agendar una cita?", None, _ah)
caso("evento y cita en un mensaje: la fecha es del evento", (m["sabemos"]["fecha_iso"], r["cita"]["dia"]), ("2026-10-25", None))
m["pendiente"] = "cita"
r = M.leer(m, "el 25 es la entrevista, ese dia no puedo. ¿podria ser el 24 a las 11?", None, _ah)
caso("dos fechas: manda la última", (r["cita"]["dia"], r["cita"]["ok"]), ("2026-10-24", True))
m = M.nueva(); m["pendiente"] = "cita"
r = M.leer(m, "y el sabado 23 a las 11 am?", None, _ah)
caso("«sábado 23» y el 23 es viernes: se pregunta", (r["cita"]["error"], r["cita"].get("alterno"), m["sabemos"].get("cita")),
     ("dia_no_coincide", "2026-10-24", None))
caso("el texto lo explica", "viernes 23 o el sábado 24" in V.cita_invalida("dia_no_coincide", "2026-10-23", "11:00", _ah.date(), None, "2026-10-24"), True)
m = M.nueva(); r = M.leer(m, "quiero ir a probármelo el sábado a las 4", None, _ah)
caso("cita sin evento: el día es de la cita", (m["sabemos"].get("cita"), m["sabemos"].get("fecha")), ("2026-10-10T16:00", None))

# La segunda vez que se pregunta la fecha o el día/noche va con otras palabras, y se sigue reconociendo.
m = con(ocasion="matrimonio"); M.registrar_respuesta(m, "¿Para cuándo es el matrimonio?")
q = M.texto_pregunta("fecha", m)
caso("fecha, segunda vez: otras palabras", (M.clave_de(q), q != "¿Para cuándo es el matrimonio?"), ("fecha", True))
m = con(ocasion="matrimonio"); M.registrar_respuesta(m, "¿El evento es de día o de noche?")
q = M.texto_pregunta("horario", m)
caso("día/noche, segunda vez: otras palabras", (M.clave_de(q), q != M.PREGUNTAS["horario"]), ("horario", True))


# WhatsApp real (04-10-2026): «si ca ver» a «¿te paso los datos para el pago?» no se leyó como sí; y «me llamo
# alvaro» se tomó por pedir una asesora y el bot se pausó.
for t, esp in [("si ca ver", True), ("si claro", True), ("ya pues", True), ("pasamelos porfa", True), ("no gracias", False),
               ("sí?", False), ("no", False), ("el vestido de qué tela es", False)]:
    caso(f"afirma: «{t}»", M.afirma(t), esp)
m = M.nueva(); m["pendiente"] = "pago"
caso("«si ca ver» responde a la pendiente de pago", M.leer(m, "si ca ver")["respondio"], True)
caso("nombre: me llamo alvaro", M.extraer("me llamo alvaro").get("nombre"), "Alvaro")
caso("nombre: mi nombre es Ana María", M.extraer("hola, mi nombre es Ana María").get("nombre"), "Ana Maria")
caso("«soy talla M» no es un nombre", M.extraer("soy talla M").get("nombre"), None)


caso("ocasión con el espacio mal puesto: «par aboda»", M.extraer("hola busco un vestido par aboda").get("ocasion"), M.extraer("para una boda").get("ocasion"))
caso("«te regalo» no es una gala", M.extraer("te regalo algo").get("ocasion"), None)


# --- Prueba de regresión (05-10-2026): lo que fallaba en las 60 preguntas nuevas --------------------------------
for t, pend, esp in [
        ("soy alvaro", "", {"nombre": "Alvaro"}),
        ("te saluda carmen de chiclayo", "", {"nombre": "Carmen", "ciudad": "Chiclayo", "envio": "provincia"}),
        ("Que tal buenas noches te saluda Julio", "", {"nombre": "Julio"}),
        ("me llamo luz, cn quien tengo el gusto?", "", {"nombre": "Luz"}),
        ("pa el matri de mi prima", "ocasion", {"ocasion": "matrimonio"}),
        ("es la promo de mi hija", "ocasion", {"ocasion": "graduacion"}),
        ("pa mi promo", "ocasion", {"ocasion": "graduacion"}),
        ("es pa mañana", "", {"fecha": "manana"}),
        ("es el sabado 18 x la tarde", "fecha", {"horario": "dia"}),
        ("en la mañanita nomas", "horario", {"horario": "dia"}),
        ("d noche", "horario", {"horario": "noche"}),
        ("tienen vestidos verdes?", "", {"prenda": "vestido", "color": "verde"}),
        ("hay polos?", "", {"prenda": "polo"}),
        ("la ele", "talla", {"talla": "L"}),
        ("buenas, el vestido holly lo tienen en M?", "", {"talla": "M"}),
        ("Ok sería en L, pero lo tienes en otros colores?", "", {"talla": "L"}),
        ("mi talla es L disculpa", "", {"talla": "L"}),
        ("para surco", "lima_o_provincia", {"envio": "lima", "ciudad": "Surco"}),
        ("sjl", "lima_o_provincia", {"envio": "lima", "ciudad": "SJL"}),
        ("soy de tacna", "lima_o_provincia", {"envio": "provincia", "ciudad": "Tacna"}),
        ("vivo en los olivos", "", {"envio": "lima"}),
        ("es pa diario", "ocasion", {"ocasion": "diario"})]:
    d = M.extraer(t, pend)
    caso(f"regresión · extraer «{t}»", {k: d.get(k) for k in esp}, esp)
for t, pend, clave in [("mi nombre es rosa elvira", "", "color"), ("soy talla M", "", "nombre"), ("soy de tacna", "", "nombre"),
                       ("soy bajita", "", "nombre"), ("ese vestido me gusta", "", "talla"), ("cuesta en S/ 330?", "", "talla"),
                       ("hay alguna promo?", "", "ocasion"), ("me ate el cabello", "", "envio"), ("la victoria es mia", "", "envio"),
                       ("soy bien flaquita, que talla me recomiendas?", "", "talla"), ("mido 1.58 y peso 60", "talla", "talla")]:
    caso(f"regresión · «{t}» no trae {clave}", M.extraer(t, pend).get(clave), None)
caso("regresión · «mi nombre es rosa elvira»", M.extraer("mi nombre es rosa elvira").get("nombre"), "Rosa Elvira")
for t, esp in [("nel", True), ("ahorita no gracias", True), ("no por ahora", True), ("no gracias", True), ("no tengo la foto", False),
               ("no se", False), ("ya pues", False)]:
    caso(f"regresión · niega «{t}»", M.niega(t), esp)
for t, esp in [("a ver muéstrame", True), ("tienen fotos d los vestidos?", True), ("q vestidos tienen pa matrimonio?", True),
               ("no me muestres nada todavia, solo pregunto", False), ("kiero ver todo el catalogo", True)]:
    caso(f"regresión · pide ver «{t}»", M.pide_ver(t), esp)
# «todavía no tengo fecha» responde a «¿para cuándo?»: no se vuelve a preguntar.
m = con(ocasion="matrimonio", prenda="vestido"); M.registrar_respuesta(m, "¿Para cuándo es el matrimonio?")
r = M.leer(m, "todavia no tengo fecha")
caso("regresión · «todavía no tengo fecha» responde", (r["respondio"], r["no_sabe"], M.siguiente(m, "prospeccion")), (True, True, "horario"))
# Sin evento (oficina, diario) no hay fecha ni día/noche que preguntar: ya se puede ofrecer.
m = con(prenda="conjunto", ocasion="trabajo")
caso("regresión · para la oficina no se pregunta la fecha", (M.siguiente(m, "prospeccion"), M.necesidad_conocida(m)), ("", True))
m = con(prenda="vestido"); m["pendiente"] = "ocasion"
caso("regresión · «ninguna ocasión» es para diario", M.leer(m, "ninguna, es para mi nomas")["datos"].get("ocasion"), "diario")
# Pedir cita: «quiero probármelo antes» no es un pedido.
caso("regresión · «quiero probarme el vestido holly» pide cita", bool(M.RE_CITA.search(M._plano("quiero probarme el vestido holly"))), True)
caso("regresión · «lo quiero pero quiero probarmelo antes» pide cita",
     decidir("seguimiento", "intencion_compra", 0.9, "Lo quiero en talla L , pero quiero probarmelo antes", "")["cita"], True)
# La memoria que llega de fuera con basura no rompe nada (entradas raras de la regresión).
_b = M.normalizar({"sabemos": {"cita": "basura", "fecha_iso": "pronto", "talla": 12, "ocasion": ["boda"], "envio": "marte", "presupuesto": "mucho"},
                   "mostrados": ["V41", None, 3], "pendiente": 5, "cita_tentativa": {"dia": "nunca", "hora": "tarde"}, "temperatura": ["x"]})
caso("regresión · memoria con basura se limpia",
     (_b["sabemos"]["cita"], _b["sabemos"]["fecha_iso"], _b["sabemos"]["talla"], _b["sabemos"]["ocasion"], _b["sabemos"]["envio"],
      _b["mostrados"], _b["pendiente"], _b["cita_tentativa"], _b["temperatura"]),
     (None, None, None, None, None, ["V41"], "", {"dia": None, "hora": None}, "frio"))
caso("regresión · una cita bien formada se conserva", M.normalizar({"sabemos": {"cita": "2026-10-09T17:00"}})["sabemos"]["cita"], "2026-10-09T17:00")


def main() -> int:
    for f in fallos:
        print(f)
    print(f"memoria    extracción, hilo y siguiente pregunta: {total - len(fallos)}/{total} casos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
