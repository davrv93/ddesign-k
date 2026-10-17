"""Prospectos a futuro, bienvenida por anuncio, «Ver tallas» y presupuesto (octubre de 2026, requerimientos de Luis).

    python3 -m app.prueba_seguimiento
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import types

from . import conocimiento, memoria
from .prueba_colores import fichas_web
from .prueba_pulido_v3 import B, C, PROMPTS, RESPUESTA, _llm_fijo, _stock
from .v3 import agente, cliente_llm, prospectos as P

FALLOS = 0


def caso(nombre, obtenido, esperado):
    global FALLOS
    if obtenido != esperado:
        FALLOS += 1
        print(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


def si(nombre, cond):
    caso(nombre, bool(cond), True)


HOY = memoria.ahora_lima().date()
CFG = {"desde_dias": 45, "aviso_dias": 30}


def req(mensaje, mem, hist, **extra):
    base = dict(mensaje=mensaje, historial=list(hist), memoria=json.loads(json.dumps(mem)), cliente="Ana López", etapa="",
                producto="", talla="", canal="whatsapp", estado="", foto_hecho="", foto_codigo="", foto_oferta=False,
                colores_grupos=None, precios_solo_si_pregunta=False, desde_anuncio=False, anuncio="")
    base.update(extra)
    return types.SimpleNamespace(**base)


def turno(fichas, mensaje, mem, hist, texto="¡Perfecto! 😊", **extra):
    RESPUESTA["texto"], RESPUESTA["herramientas"] = texto, []
    PROMPTS.clear()
    return agente.conversar(req(mensaje, mem, hist, **extra), fichas, _stock, pie=lambda f, st: f"*{f.nombre}* — *S/ {f.precio:.2f}*")


def prueba_lectura():
    for t in ["ya", "ahora mismo", "sí, lo quiero ya", "de una", "quiero separarlo"]:
        caso(f"ahora: {t}", P.leer_respuesta(t), "ahora")
    for t in ["más adelante", "mejor en diciembre", "después te escribo", "todavía no", "no, aún falta", "coordinemos luego"]:
        caso(f"después: {t}", P.leer_respuesta(t), "despues")
    caso("otra cosa", P.leer_respuesta("¿de qué tela es?"), None)
    ev = dt.date(HOY.year + 1, 1, 15)
    caso("mes nombrado antes del evento", P.fecha_de_mes("mejor en diciembre", ev - dt.timedelta(days=30), ev, HOY),
         dt.date(HOY.year if HOY.month < 12 else HOY.year + 1, 12, 1) if HOY.month < 12 else ev - dt.timedelta(days=30))
    caso("sin mes: la fecha del aviso", P.fecha_de_mes("más adelante", dt.date(2030, 1, 1), ev, HOY), dt.date(2030, 1, 1))
    caso("cuándo", P.cuando_txt(dt.date(2026, 12, 3)), "inicios de diciembre")
    caso("config inválida", P.config(types.SimpleNamespace(prospectos={"desde_dias": 0, "aviso_dias": 30})), None)


def prueba_flujo(fichas):
    ev = (HOY + dt.timedelta(days=90)).isoformat()
    hist = [C("hola"), B("¡Hola! Te saluda Rosmary 😊 ¿Qué evento se te aproxima?"), C("una boda"), B("¡Qué lindo! ¿Para cuándo es?")]
    mem = {"sabemos": {"ocasion": "boda"}}
    # Sin la configuración: nada cambia (no pregunta).
    r = turno(fichas, f"es el {ev}", mem, hist)
    si("sin prospectos no pregunta", "coordinemos" not in r["respuesta"])
    # Con la configuración: la fecha lejana dispara la pregunta, una vez.
    mem2 = {"sabemos": {"ocasion": "boda", "fecha_iso": ev, "fecha": "en tres meses"}}
    r = turno(fichas, "ok", mem2, hist, prospectos=CFG)
    si("pregunta ¿ya o lo coordinamos?", "lo coordinemos a" in r["respuesta"] and r["memoria"]["pendiente"] == "coordinar")
    m = r["memoria"]
    hist2 = hist + [C("ok"), B(r["respuesta"])]
    # «mejor más adelante»: cierre amable, fecha del aviso y marca; la fecha del evento no cambia.
    r = turno(fichas, "mejor más adelante", m, hist2, prospectos=CFG)
    sab = r["memoria"]["sabemos"]
    caso("decisión", sab.get("coordinar"), "despues")
    caso("fecha del aviso = evento − 30", sab.get("coordinar_en"), (HOY + dt.timedelta(days=60)).isoformat())
    si("marca", sab.get("coordinar_marca"))
    caso("el evento no cambia", sab.get("fecha_iso"), ev)
    si("cierre sin insistir", "Te escribo a" in r["respuesta"] and "?" not in r["respuesta"])
    # No vuelve a preguntar.
    r2 = turno(fichas, "gracias", r["memoria"], hist2 + [C("mejor más adelante"), B(r["respuesta"])], prospectos=CFG)
    si("no repite la pregunta", "lo coordinemos a" not in r2["respuesta"])
    # «ya»: decisión ahora y sigue normal.
    r = turno(fichas, "ya, lo quiero ahora", m, hist2, prospectos=CFG, texto="¡Genial! Te muestro opciones 😍")
    caso("decisión ahora", r["memoria"]["sabemos"].get("coordinar"), "ahora")
    # Evento cercano: no pregunta.
    cerca = {"sabemos": {"ocasion": "boda", "fecha_iso": (HOY + dt.timedelta(days=20)).isoformat()}}
    r = turno(fichas, "ok", cerca, hist, prospectos=CFG)
    si("evento cercano: sin pregunta", "lo coordinemos a" not in r["respuesta"])
    # Si ella preguntó algo en ese mismo mensaje, primero se le contesta.
    r = turno(fichas, "¿de qué tela es?", mem2, hist, prospectos=CFG, texto="Es de crepé 😊")
    si("con pregunta suya, primero contesta", "lo coordinemos a" not in r["respuesta"])


def prueba_bienvenida(fichas):
    salome = next(x for x in fichas if x.codigo == "V50")
    conoc = {"empresa": "baruka", "version": "g1", "hoy": HOY.isoformat(),
             "politicas": [{"id": 3, "titulo": "Garantía", "contenido": "Garantía de 30 días por fallas de confección. Aplican condiciones."}]}
    conocimiento.limpiar_cache()
    r = turno(fichas, "Hola, quiero más info", {}, [], bienvenida_anuncio=True, desde_anuncio=True, anuncio=salome.nombre,
              conocimiento=conoc)
    si("bienvenida: se presenta y nombra el modelo", "Rosmary" in r["respuesta"] and salome.nombre in r["respuesta"])
    si("bienvenida: garantía", "30 días por fallas" in r["respuesta"])
    caso("bienvenida: una foto del modelo", [s["codigo"] for s in r["sugerencias"]], ["V50"])
    caso("bienvenida: respuestas rápidas", [x["label"] for x in r["respuestas_rapidas"]], ["Ver colores", "Ver tallas"])
    caso("bienvenida: modelo en foco", r["memoria"]["producto"], "V50")
    si("bienvenida: sin LLM", not PROMPTS)
    si("sin precio_al_llegar no marca", not r.get("precio_al_llegar"))
    r = turno(fichas, "Hola", {}, [], bienvenida_anuncio=True, desde_anuncio=True, anuncio=salome.nombre,
              precio_al_llegar_por_anuncio=True)
    si("con precio_al_llegar marca", r.get("precio_al_llegar"))
    si("sin garantía cargada, no la menciona", "garantía" not in r["respuesta"].lower())
    r = turno(fichas, "Hola, quiero más info", {}, [], bienvenida_anuncio=False, desde_anuncio=True, anuncio=salome.nombre)
    si("sin el ajuste: lo de siempre", "Ver tallas" not in json.dumps(r.get("respuestas_rapidas") or []))
    # «Ver tallas» con el modelo en foco: tallas con stock, sin LLM.
    r = turno(fichas, "Ver tallas", {"producto": "V50", "mostrados": ["V50"], "sabemos": {}}, [C("hola"), B("¡Hola! Te comparto el Salome")])
    si("ver tallas", "S, M y L" in r["respuesta"] and not PROMPTS)
    # Stock caído: no dice «agotado».
    RESPUESTA["texto"], RESPUESTA["herramientas"] = "x", []
    r = agente.conversar(req("Ver tallas", {"producto": "V50", "mostrados": ["V50"], "sabemos": {}}, [C("hola"), B("¡Hola!")]),
                         fichas, lambda c: {}, pie=lambda f, st: "")
    si("stock caído: sin «agotado»", "agotado" not in r["respuesta"])


def prueba_presupuesto(fichas):
    hist = [C("hola"), B("¡Hola! Te saluda Rosmary 😊 ¿Qué evento se te aproxima?"), C("una boda"), B("¡Qué lindo!")]
    mem = {"sabemos": {"ocasion": "boda"}, "etapa": "prospeccion"}
    turno(fichas, "me gustaría algo elegante", mem, hist, preguntar_presupuesto=True)
    si("pide el rango de presupuesto", PROMPTS and "PREGUNTA DE PRESUPUESTO" in PROMPTS[0])
    turno(fichas, "me gustaría algo elegante", mem, hist, preguntar_presupuesto=False)
    si("sin el ajuste no", PROMPTS and "PREGUNTA DE PRESUPUESTO" not in PROMPTS[0])
    bajo = {"sabemos": {"ocasion": "boda", "presupuesto": "20"}, "etapa": "seguimiento"}
    turno(fichas, "y qué me recomiendas", bajo, hist, preguntar_presupuesto=True)
    si("presupuesto por debajo: descarte con cariño", PROMPTS and "por debajo de nuestros precios" in PROMPTS[0])


def main() -> int:
    antes = (cliente_llm.responder, cliente_llm.configurado)
    cliente_llm.responder, cliente_llm.configurado = _llm_fijo, (lambda: True)
    try:
        prueba_lectura()
        fw = fichas_web()
        prueba_flujo(fw)
        prueba_bienvenida(fw)
        prueba_presupuesto(fw)
    finally:
        cliente_llm.responder, cliente_llm.configurado = antes
    print(f"prueba_seguimiento: {FALLOS} fallos")
    return 1 if FALLOS else 0


if __name__ == "__main__":
    sys.exit(main())
