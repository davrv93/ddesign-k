"""Pruebas de la respuesta estructurada (sin red ni LLM). Se ejecutan al construir la imagen:

    python3 -m app.prueba_estructurado
"""
from __future__ import annotations

import sys

from . import estructurado as S

fallos: list[str] = []
total = 0


def caso(nombre: str, obtenido, esperado) -> None:
    global total
    total += 1
    if obtenido != esperado:
        fallos.append(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


J = '{"responde": "Es de tela roma, cae súper bien.", "por_que": "Para un matrimonio de noche te estiliza.", "pregunta": "¿Qué talla usas?"}'
caso("arma responde, por qué y la pregunta del código",
     S.armar(J, "¿Qué talla usas normalmente?"),
     "Es de tela roma, cae súper bien.\n\nPara un matrimonio de noche te estiliza.\n\n¿Qué talla usas normalmente?")
caso("sin pregunta del código y sin permiso: no pregunta", S.armar(J, "", False),
     "Es de tela roma, cae súper bien.\n\nPara un matrimonio de noche te estiliza.")
caso("sin pregunta del código y con permiso: la del LLM", S.armar(J, "", True).split("\n\n")[-1], "¿Qué talla usas?")
caso("las preguntas dentro de responde se quitan",
     S.armar('{"responde": "Sí lo tenemos en M. ¿Para cuándo lo necesitas?", "por_que": "", "pregunta": ""}', "¿Qué talla usas?"),
     "Sí lo tenemos en M.\n\n¿Qué talla usas?")
largo = "Una frase. Otra frase más. Y una tercera que sobra."
caso("responde: máximo dos frases", S.armar('{"responde": "%s"}' % largo).split("\n\n")[0], "Una frase. Otra frase más.")
caso("por qué: máximo una frase", S.armar('{"responde": "Hola.", "por_que": "Uno. Dos."}').split("\n\n")[1], "Uno.")
caso("por qué repetido con responde se va",
     S.armar('{"responde": "El vestido Irla es elegante para tu boda.", "por_que": "El vestido Irla es elegante para tu boda de noche."}'),
     "El vestido Irla es elegante para tu boda.")
caso("con ```json``` alrededor", S.armar('```json\n{"responde": "Listo."}\n```'), "Listo.")
caso("texto libre (no JSON) → None, se usa como antes", S.armar("Hola, ¿cómo estás?"), None)
caso("JSON vacío → None", S.armar('{"responde": "", "por_que": ""}', "¿Qué talla?"), None)
caso("JSON roto no llega con llaves", "{" in S.sin_json('{"responde": "Hola"'), False)
caso("un decimal no parte la frase", S.armar('{"responde": "El envío es S/ 20.00 por Olva. Llega en 2 días."}'),
     "El envío es S/ 20.00 por Olva. Llega en 2 días.")
caso("el formato nombra la pregunta del código", "«¿Qué talla usas?»" in S.formato("¿Qué talla usas?", False), True)

# --- El saludo no gasta las 2 frases, y si se habla de una prenda se la nombra (05-10-2026) ---------------------------
# Producción, primer mensaje «hola, busco un vestido para un matrimonio de noche el 24 de octubre»: salió
# «¡Hola, Ana! Soy Rosemary…» → «Es ideal para una boda nocturna…» → foto del Irla → «¿Qué talla usas?». La clienta lee
# «es ideal» antes de ver la foto y sin saber de qué.
SALUDO = "¡Hola, Ana! Soy Rosemary, tu asesora de Baruka Design."
caso("el saludo no se come la frase que nombra la prenda",
     S.armar('{"responde": "%s Para tu matrimonio de noche te recomiendo el Vestido Irla.", "por_que": "Su tela roma cae muy bien y estiliza."}'
             % SALUDO, "¿Qué talla usas normalmente?"),
     SALUDO + "\n\nPara tu matrimonio de noche te recomiendo el Vestido Irla.\n\nSu tela roma cae muy bien y estiliza."
     "\n\n¿Qué talla usas normalmente?")
caso("con saludo, responde sigue con su tope de 2 frases",
     S.armar('{"responde": "¡Hola! Soy Rosemary. Uno. Dos. Tres."}'), "¡Hola! Soy Rosemary.\n\nUno. Dos.")
caso("solo el saludo también vale", S.armar('{"responde": "%s"}' % SALUDO), SALUDO)
caso("«¿Cómo estás?» no es saludo que se guarde: es pregunta y se va",
     S.armar('{"responde": "¡Hola! ¿Cómo estás? Tenemos vestidos de noche."}'), "¡Hola!\n\nTenemos vestidos de noche.")
caso("el formato pide nombrar la prenda", "NÓMBRALA" in S.formato("", False), True)

IRLA = ["V35", "irla"]
FRASE = "Para tu matrimonio de noche te recomiendo el *Vestido Irla*."
PROD = (SALUDO + "\n\nEs ideal para una boda nocturna por su elegancia y tela roma que favorece la figura."
        "\n\n¿Qué talla usas normalmente?")
caso("producción: la frase que la nombra va tras el saludo y antes del porqué; la pregunta sigue al final",
     S.presentar(PROD, FRASE, IRLA),
     SALUDO + "\n\nPara tu matrimonio de noche te recomiendo el *Vestido Irla*. Es ideal para una boda nocturna por su "
     "elegancia y tela roma que favorece la figura.\n\n¿Qué talla usas normalmente?")
caso("producción, de punta a punta: JSON del LLM → armar → presentar",
     S.presentar(S.armar('{"responde": "%s", "por_que": "Es ideal para una boda nocturna por su elegancia."}' % SALUDO,
                         "¿Qué talla usas normalmente?"), FRASE, IRLA).split("\n\n"),
     [SALUDO, FRASE + " Es ideal para una boda nocturna por su elegancia.", "¿Qué talla usas normalmente?"])
caso("si el LLM ya la nombró, no se duplica",
     S.presentar("Te recomiendo el vestido Irla, de tela roma.\n\n¿Qué talla usas?", FRASE, IRLA),
     "Te recomiendo el vestido Irla, de tela roma.\n\n¿Qué talla usas?")
caso("nombrarla por el código también vale", S.presentar("Mira el *V35* 😊\n\n¿Qué talla usas?", FRASE, IRLA),
     "Mira el *V35* 😊\n\n¿Qué talla usas?")
caso("nombrarla solo en la pregunta (después de la foto) no basta",
     S.presentar("Es ideal para tu boda.\n\n¿Qué talla usas para el Irla?", FRASE, IRLA),
     FRASE + " Es ideal para tu boda.\n\n¿Qué talla usas para el Irla?")
caso("«Te va a quedar…» sin nombre: la frase va antes",
     S.presentar("¡Te va a quedar hermoso para tu boda!\n\n¿Qué talla usas?", FRASE, IRLA),
     FRASE + " ¡Te va a quedar hermoso para tu boda!\n\n¿Qué talla usas?")
caso("«Este modelo…» sin nombre: la frase va antes",
     S.presentar("Este modelo tiene escote corazón.", "Te muestro el *Vestido Irla*.", IRLA),
     "Te muestro el *Vestido Irla*. Este modelo tiene escote corazón.")
caso("tras una exclamación de entrada («¡Claro! 😊»), no antes",
     S.presentar("¡Claro! 😊 Te paso la foto.", "Te muestro el *Vestido Irla*.", IRLA),
     "¡Claro! 😊 Te muestro el *Vestido Irla*. Te paso la foto.")
caso("«¡Perfecto!» suelto es de entrada", S.presentar("¡Perfecto! Te va a quedar lindo.", FRASE, IRLA),
     "¡Perfecto! " + FRASE + " Te va a quedar lindo.")
caso("solo la pregunta: la frase va antes, en su párrafo (la pregunta, tras la foto)",
     S.presentar("¿Qué talla usas?", FRASE, IRLA), FRASE + "\n\n¿Qué talla usas?")
caso("solo el saludo: la frase va en su párrafo", S.presentar(SALUDO, FRASE, IRLA), SALUDO + "\n\n" + FRASE)
caso("«Irlanda» no es el Irla", S.nombra("Viajo a Irlanda", IRLA), False)
caso("sin tildes ni mayúsculas", S.nombra("te recomiendo el IRLA", IRLA), True)
caso("«Es ideal…» no tiene antecedente", S.sin_antecedente("Es ideal para una boda nocturna."), True)
caso("«Para tu boda…» sí se entiende", S.sin_antecedente("Para tu boda te recomiendo el Irla."), False)


def main() -> int:
    for f in fallos:
        print(f)
    print(f"estructura respuesta del LLM: {total - len(fallos)}/{total} casos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
