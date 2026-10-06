"""Pruebas del contrato SolicitudCliente (app/solicitud.py): lo que pide el TEXTO de un mensaje. Puras, sin modelos:

    python3 -m app.prueba_solicitud

Los casos salen de conversaciones reales o simuladas del 06-10-2026 (web): cada uno es una frase que ya falló alguna vez.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from .solicitud import interpretar, pide_otro_color

fallos: list[str] = []
total = 0


def caso(nombre: str, obtenido, esperado) -> None:
    global total
    total += 1
    if obtenido != esperado:
        fallos.append(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


# Color: pide, comenta o descarta
for msg, color, pide in [
    ("algun vestido rojo ?", "rojo", True), ("vestido rojo?", "rojo", True), ("y rojo?", "rojo", True), ("tienen en rojo?", "rojo", True),
    ("busco uno verde", "verde", True), ("¿tienes blusas rojas?", "roja", True),
    ("me gusta el azul que vi", "azul", False),
    ("rojo no, otro color", "", False), ("hola, busco un vestido", "", False),
]:
    s = interpretar(msg)
    caso(f"color «{msg}»", (s.color, s.pide_color), (color, pide))
caso("raíz del color: roja → roj", interpretar("quiero uno rojo").color_raiz, "roj")
caso("raíz del color: palo rosa → ros", interpretar("lo quiero en palo rosa").color_raiz, "ros")

# Pedir otro color que el de la prenda en foco
kendall = NS(color="palo rosa")
caso("«yo quiero uno rojo» mirando un palo rosa", pide_otro_color(kendall, "yo quiero uno rojo"), True)
caso("«lo quiero en rosado» mirando un palo rosa", pide_otro_color(kendall, "lo quiero en rosado"), False)
caso("sin color en el mensaje", pide_otro_color(kendall, "quiero el vestido"), False)
caso("prenda sin ficha de color: no se asume", pide_otro_color(NS(color=""), "quiero uno rojo"), False)

# Cita
for msg, esperado in [
    ("quiero probármelo", True), ("¿puedo ir mañana a las 10?", True), ("¿puedo pasarme a probar?", True),
    ("¿me podrías agendar el domingo a las 7:30?", True), ("¿y entonces el sábado a las 11 te parece bien?", True),
    ("quiero asegurarme que cierro la cita", True),
    ("¿me lo separas para el sábado?", False), ("cuánto cuesta el V21?", False), ("el evento es el sábado, me parece bien el V21", False),
]:
    caso(f"cita «{msg}»", interpretar(msg).pide_cita, esperado)

# Otras opciones
caso("«muéstrame otras opciones»", interpretar("muéstrame otras opciones").mas_opciones, True)
caso("«y otra cosa importante» no es pedir ver más", interpretar("y otra cosa importante: ¿hacen delivery?").mas_opciones, False)
caso("«no quiero otro vestido, quiero el Holly»", interpretar("pero no quiero otro vestido, quiero el holly").no_otro, True)
caso("regateo comparando con Gamarra", interpretar("en gamarra encuentro parecidos más baratos").regatea_comparando, True)
caso("…salvo que pida ver: «muéstrame los de Gamarra»", interpretar("muéstrame algo de gamarra").regatea_comparando, False)
caso("«algo más barato»", interpretar("tienes algo más barato?").mas_barato, True)
caso("«muéstrame tu catálogo» es catálogo, no «otras»", interpretar("muéstrame tu catálogo").catalogo, True)
caso("«no me muestres nada todavía»", interpretar("no me muestres nada todavía").no_mostrar, True)
caso("«busco un vestido» puede ser cambio de prenda", interpretar("busco un vestido").busca_cambio, True)

# Contrato
s = interpretar("algun vestido rojo ?")
caso("a_dict solo trae lo que pide", sorted(s.a_dict()), ["color", "color_raiz", "pide_color"])
caso("mensaje vacío no rompe", interpretar("").a_dict(), {})
caso("None no rompe", interpretar(None).a_dict(), {})   # type: ignore[arg-type]
interpretar.cache_clear()
interpretar("hola"), interpretar("hola"), interpretar("hola")
caso("cacheada por texto", interpretar.cache_info().hits, 2)

print(f"solicitud: {total - len(fallos)}/{total} ok")
if fallos:
    print("\n".join(fallos))
    raise SystemExit(1)
