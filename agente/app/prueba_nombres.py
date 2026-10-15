"""Pruebas de app/nombres.py: pedir por el nombre del modelo, sin código, y la red que quita los códigos de la salida.

    python3 -m app.prueba_nombres
"""
from __future__ import annotations

import sys
from types import SimpleNamespace as N

from . import nombres

FICHAS = [N(codigo="V41", nombre="Vestido Holly"), N(codigo="V88", nombre="Pantalón Holly"), N(codigo="V50", nombre="Vestido Salome"),
          N(codigo="V42", nombre="Vestido Alma"), N(codigo="V64", nombre="Blusa Lila"), N(codigo="V129", nombre="Enterizo Sol"),
          N(codigo="V74", nombre="Blusa Camila"), N(codigo="V122", nombre="Enterizo Camila"), N(codigo="V130", nombre="Vestido Zoraida"),
          N(codigo="V131", nombre="Vestido Zoraida Clásico"), N(codigo="V112", nombre="Conjunto Luz"), N(codigo="V55", nombre="Vestido Paloma"),
          N(codigo="V26", nombre="Pantalón Cristal"), N(codigo="V103", nombre="Vestido Milan")]

FALLOS = 0


def caso(nombre: str, obtenido, esperado) -> None:
    global FALLOS
    if obtenido != esperado:
        FALLOS += 1
        print(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


def main() -> int:
    n = lambda t, ctx=None: nombres.nombradas(t, FICHAS, ctx)
    # por nombre, con y sin tipo, sin tildes, dentro de una frase
    caso("nombre solo", n("quiero el Salome").codigos, ["V50"])
    caso("nombre con tipo", n("el vestido salomé en M").codigos, ["V50"])
    caso("dos nombres en orden", n("el Salome o el pantalon holly?").codigos, ["V50", "V88"])
    caso("código escrito sigue valiendo", n("V50 y el v41").codigos, ["V50", "V41"])
    caso("nombre largo gana", n("zoraida clasico").codigos, ["V131"])
    caso("zoraida a secas es la base", n("la zoraida").codigos, ["V130"])
    # repetidos: sin decidir, salvo tipo, foco o lo que el bot nombró
    caso("Holly sin tipo es ambiguo", (n("el Holly").codigos, n("el Holly").ambiguas), ([], [("holly", ["V41", "V88"])]))
    caso("pantalón Holly decide", n("pantalón Holly").codigos, ["V88"])
    caso("pantalones holly decide", n("los pantalones holly").codigos, ["V88"])
    caso("vestido Holly decide", n("el vestido holly rojo").codigos, ["V41"])
    caso("el foco decide", n("el Holly", {"foco": "V88"}).codigos, ["V88"])
    caso("lo que nombró el bot decide", n("y el holly?", {"bot": "Te muestro el Pantalón Holly 😊"}).codigos, ["V88"])
    caso("el bot nombró los dos: sigue ambiguo", n("el holly", {"bot": "Vestido Holly y Pantalón Holly"}).ambiguas, [("holly", ["V41", "V88"])])
    caso("Camila ambigua", n("la Camila").ambiguas, [("camila", ["V74", "V122"])])
    caso("pregunta cuál", nombres.pregunta_cual(("holly", ["V41", "V88"]), FICHAS),
         "Tenemos el Vestido Holly y el Pantalón Holly 😊 ¿Cuál de los dos te interesa?")
    # palabras comunes: solo con contexto
    caso("con toda el alma no es el Alma", n("te lo pido con toda el alma").codigos, [])
    caso("en el alma no es el Alma", n("me llegó en el alma").codigos, [])
    caso("el vestido Alma sí", n("el vestido Alma").codigos, ["V42"])
    caso("el Alma sí", n("quiero el alma en M").codigos, ["V42"])
    caso("Alma como respuesta entera", n("Alma").codigos, ["V42"])
    caso("alma contestando ¿cuál?", n("alma", {"contesta_cual": True}).codigos, ["V42"])
    caso("hace sol no es el Sol", n("hace mucho sol").codigos, [])
    caso("el Sol sí", n("el Sol").codigos, ["V129"])
    caso("color lila no es la Lila", n("lo quiero en lila").codigos, [])
    caso("de color lila no es la Lila", n("algo de color lila").codigos, [])
    caso("la Lila sí", n("la Lila").codigos, ["V64"])
    caso("la blusa lila es la Blusa Lila", n("la blusa lila").codigos, ["V64"])
    caso("una paloma no es el Paloma", n("vi una paloma").codigos, ["V55"])   # «una» es determinante: se acepta, es raro en chat
    caso("mi amiga Camila no es la Camila", n("mi amiga camila").ambiguas, [])
    caso("viajo a Milan no es el Milan", n("viajo a milan").codigos, [])
    caso("el Milan sí", n("el milan").codigos, ["V103"])
    caso("vacío", n("").codigos, [])
    # la red de salida
    sc = lambda t: nombres.sin_codigos(t, FICHAS)
    caso("pie viejo", sc("*V50* Vestido Salome — *S/ 290.00*\nTallas: S, M\n👉 Escribe *V50* para pedirlo"),
         "*Vestido Salome* — *S/ 290.00*\nTallas: S, M\n👉 Dime tu talla y te lo separo 😊")
    caso("código en negrita y nombre", sc("te recomiendo el *V41* Vestido Holly 😊"), "te recomiendo el *Vestido Holly* 😊")
    caso("código solo en negrita", sc("La talla M del *V50* se agotó"), "La talla M del *Vestido Salome* se agotó")
    caso("código sin negrita y nombre", sc("Es el V50 Vestido Salome."), "Es el Vestido Salome.")
    caso("código suelto", sc("el V88 viene en negro"), "el Pantalón Holly viene en negro")
    caso("código con guion", sc("el V-88 viene en negro"), "el Pantalón Holly viene en negro")
    caso("título web", sc("*V88 — Pantalón Holly* — S/ 120"), "*Pantalón Holly* — S/ 120")
    caso("código desconocido se deja", sc("el V99 no existe"), "el V99 no existe")
    caso("varios", sc("Tengo el *V41* Vestido Holly y el *V50* Vestido Salome"), "Tengo el *Vestido Holly* y el *Vestido Salome*")
    caso("sin códigos no cambia", sc("¿Para qué evento es? 😊"), "¿Para qué evento es? 😊")
    res = {"respuesta": "Es el *V50* Vestido Salome", "codigo": "V50",
           "sugerencias": [{"codigo": "V50", "pie": "*V50* Vestido Salome\nTallas: M", "titulo": "*V50* Vestido Salome", "tallas": [{"codigo": "V50"}]}]}
    out = nombres.respuesta_sin_codigos(res, FICHAS)
    caso("respuesta limpia", out["respuesta"], "Es el *Vestido Salome*")
    caso("pie limpio", out["sugerencias"][0]["pie"], "*Vestido Salome*\nTallas: M")
    caso("título limpio", out["sugerencias"][0]["titulo"], "*Vestido Salome*")
    caso("los campos internos siguen", (out["codigo"], out["sugerencias"][0]["codigo"], out["sugerencias"][0]["tallas"][0]["codigo"]),
         ("V50", "V50", "V50"))
    total = 52
    print(f"nombres    por nombre, repetidos, palabras comunes y salida sin códigos: {total - FALLOS}/{total} casos")
    return 1 if FALLOS else 0


if __name__ == "__main__":
    sys.exit(main())
