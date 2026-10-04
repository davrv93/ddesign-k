"""Pruebas de la memoria de la conversación (app/memoria.py), sin red ni LLM. Se ejecutan al construir la imagen
y a mano:

    python3 -m app.prueba_memoria

Cubren: extracción por reglas, la pregunta pendiente (el hilo), la siguiente pregunta que elige el código, que
no se repitan preguntas y que la memoria se reconstruya del historial cuando quien llama no la manda.
"""
from __future__ import annotations

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
caso("prospección empieza por la ocasión", M.siguiente(M.nueva(), "prospeccion"), "ocasion")
caso("sabida la ocasión, día/noche", M.siguiente(con(ocasion="matrimonio"), "prospeccion"), "horario")
m = M.nueva(); m["preguntado"] = ["ocasion"]
caso("preguntada (aunque sin respuesta) no se repite", M.siguiente(m, "prospeccion"), "horario")
caso("talla del perfil: no se pregunta", M.siguiente(M.con_perfil(con(ocasion="boda", horario="noche"), {"tallas": ["M"]}), "prospeccion"), "estatura")
m = M.nueva(); m["preguntado"] = ["fecha"]
caso("seguimiento: qué le gustó, una sola vez", M.siguiente(m, "seguimiento"), "que_le_gusto")
m["preguntado"] += ["que_le_gusto"]
caso("seguimiento: luego separar", M.siguiente(m, "seguimiento"), "separar")
m["preguntado"] += ["separar"]
caso("seguimiento: no queda nada que preguntar", M.siguiente(m, "seguimiento"), "")
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

# --- 9. Guion b) sin LLM: el bot siempre pregunta lo que dice `siguiente`; ninguna pregunta se repite --------
m, etapa, hechas = M.nueva(), "prospeccion", []
GUION = [("Hola, quisiera saber si todavía tienen este vestido", "prospeccion"), ("es para un matrimonio", "prospeccion"),
         ("de noche", "prospeccion"), ("Sí, me interesa", "seguimiento"), ("¿cuánto cuesta?", "seguimiento"),
         ("¿cómo es el material?", "seguimiento"), ("soy talla M", "seguimiento")]
for msg, etapa in GUION:
    M.leer(m, msg)
    k = M.siguiente(m, etapa)
    if k:
        hechas.append(k)
        M.registrar_respuesta(m, "Respuesta.\n\n" + M.PREGUNTAS[k])
    else:
        M.registrar_respuesta(m, "Respuesta sin pregunta.")
caso("guion b): preguntas en orden y sin repetir", hechas, ["ocasion", "horario", "talla", "fecha", "que_le_gusto", "separar"])
caso("guion b): lo que sabemos al llegar al cierre", {k: v for k, v in m["sabemos"].items() if v},
     {"ocasion": "matrimonio", "horario": "noche", "talla": "M"})


def main() -> int:
    for f in fallos:
        print(f)
    print(f"memoria    extracción, hilo y siguiente pregunta: {total - len(fallos)}/{total} casos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
