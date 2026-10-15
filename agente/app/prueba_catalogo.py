"""Catálogo por secciones y cambios de idea (14-10-2026). Sin LLM: el modelo se sustituye por una respuesta fija.

    python3 -m app.prueba_catalogo
"""
from __future__ import annotations

import sys

from . import colecciones, memoria
from .prueba_colores import fichas_web
from .prueba_pulido_v3 import B, C, PROMPTS, _llm_fijo, _turno, ok, fallos
from .v3 import cliente_llm, ficha as ficha_mod


def prueba_secciones():
    for texto, esperado in (("pásame el catálogo", ["all"]), ("tienes catálogo de enterizos?", ["enterizos"]),
                            ("quiero ver todos los enterizos", ["enterizos"]), ("me mandas el link de vestidos largos", ["vestidos"]),
                            ("catálogo de vestidos", ["vestidos", "vestido-cortos"]), ("tienen página web?", ["all"]),
                            ("link de los conjuntos sastre", ["sastre"]), ("dame el enlace de jumpsuits", ["enterizos"]),
                            ("catalogo de pantalones porfa", ["pantalon"]), ("quiero ver vestidos cortos, tienes catálogo?", ["vestido-cortos"])):
        ok(colecciones.pide_catalogo(texto) and colecciones.elegir(texto, {"sabemos": {}}) == esperado,
           f"«{texto}» → {colecciones.elegir(texto, {'sabemos': {}})} (esperado {esperado})")
    for texto in ("muéstrame de todos", "qué tienes para una boda?", "tienes más?", "me gusta el Holly", "tienes en rojo?"):
        ok(not colecciones.pide_catalogo(texto), f"«{texto}» no es pedir el catálogo")
    ok(colecciones.elegir("quiero ver todo", {"producto": "V83", "sabemos": {}}) == ["enterizos"], "ver todo con un enterizo en foco → enterizos")
    ok(colecciones.elegir("el catálogo", {"sabemos": {"prenda": "blusa"}}) == ["blusas"], "catálogo buscando blusa → blusas")
    t = colecciones.tarjeta("enterizos")
    ok(t["imagen"].startswith("/media/tienda/coleccion_enterizos-") and "https://baruka.pe/collections/enterizos" in t["pie"],
       f"tarjeta de enterizos: {t}")
    ok(colecciones.archivo_valido(t["imagen"].rsplit("/", 1)[-1]) and not colecciones.archivo_valido("coleccion_x.jpg"),
       "solo se sirven las capturas del seed")


def prueba_turnos(fichas):
    base = [C("hola"), B("¡Hola! 😊 Te saluda Rosmary. ¿Qué evento se te aproxima?")]
    r = _turno(fichas, "me pasas el catálogo de enterizos?", base)
    ok([s["imagen"].split("/")[-1].split("-")[0] for s in r["sugerencias"]] == ["coleccion_enterizos"], f"catálogo de enterizos: {r['sugerencias']}")
    ok("evento" in memoria._plano(r["respuesta"]) and "https" not in r["respuesta"], f"pregunta el evento y no repite el enlace: {r['respuesta']!r}")
    ok(not PROMPTS, "el catálogo no llama al LLM")
    r = _turno(fichas, "tienen catálogo de vestidos?", base)
    ok(len(r["sugerencias"]) == 2, f"vestidos a secas: largos y cortos ({len(r['sugerencias'])})")
    mem = ficha_mod.normalizar(None)
    mem["producto"], mem["mostrados"], mem["sabemos"]["ocasion"] = "V83", ["V83"], "boda"
    r = _turno(fichas, "quiero ver todo", base + [B("*V83* Enterizo Elowen")], mem)
    ok([s["imagen"].split("/")[-1].split("-")[0] for s in r["sugerencias"]] == ["coleccion_enterizos"], f"ver todo con el Elowen en foco: {r['sugerencias']}")
    ok("cual te gusto" in memoria._plano(r["respuesta"]), f"con el evento sabido pregunta cuál le gustó: {r['respuesta']!r}")
    r = _turno(fichas, "pásame el link del Holly", base)
    ok(r["sugerencias"] and "baruka.pe/products/" in r["sugerencias"][0]["pie"], f"link de un modelo: {r['sugerencias']}")

    # Cambios de idea
    mem = ficha_mod.normalizar(None)
    mem["producto"], mem["mostrados"] = "V50", ["V50"]
    mem["sabemos"].update({"ocasion": "boda", "prenda": "vestido", "color": "negro"})
    hist = base + [C("boda"), B("*V50* Vestido Salome — S/ 290"), B("¿Qué es lo que más te gustó del modelo?")]
    r = _turno(fichas, "y el Holly?", hist, mem)
    ok("CAMBIO DE MODELO" in (PROMPTS[0] if PROMPTS else "") and r["memoria"]["producto"] != "V50", f"otro modelo: foco {r['memoria']['producto']}")
    r = _turno(fichas, "no me gusta, tienes otro modelo?", hist, mem, herramientas=[("enviar_fotos", {"codigos": ["V53"]})])
    ok("NO LE CONVENCIÓ EL V50" in (PROMPTS[0] if PROMPTS else ""), "«no me gusta» → otro modelo")
    ok([s["codigo"] for s in r["sugerencias"]] == ["V53"], f"y puede mandar otra prenda: {[s['codigo'] for s in r['sugerencias']]}")
    r = _turno(fichas, "mejor quiero un enterizo", hist, mem)
    ok("CAMBIO DE PRENDA" in (PROMPTS[0] if PROMPTS else ""), "vestido → enterizo")
    r = _turno(fichas, "mejor en rojo", hist, mem)
    ok(r["memoria"]["sabemos"].get("color") == "rojo", f"el color nuevo pisa al anterior: {r['memoria']['sabemos'].get('color')}")
    mem2 = ficha_mod.normalizar(None)
    mem2["producto"], mem2["mostrados"], mem2["sabemos"]["ocasion"] = "V83", ["V83"], "boda"
    _turno(fichas, "lo tienes en verde?", base + [B("*V83* Enterizo Elowen")], mem2)
    ok("NO VIENE EN VERDE" in (PROMPTS[0] if PROMPTS else ""), "color que la prenda en foco no tiene: se dice")
    r = _turno(fichas, "me gusta, de qué tela es?", hist, mem)
    ok("CAMBIO" not in (PROMPTS[0] if PROMPTS else ""), "sin cambio de idea no hay aviso")


def main() -> int:
    antes = (cliente_llm.responder, cliente_llm.configurado)
    cliente_llm.responder, cliente_llm.configurado = _llm_fijo, (lambda: True)
    try:
        prueba_secciones()
        prueba_turnos(fichas_web())
    finally:
        cliente_llm.responder, cliente_llm.configurado = antes
    print(f"prueba_catalogo: {len(fallos)} fallos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
