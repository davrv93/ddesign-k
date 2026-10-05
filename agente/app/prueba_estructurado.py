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


def main() -> int:
    for f in fallos:
        print(f)
    print(f"estructura respuesta del LLM: {total - len(fallos)}/{total} casos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
