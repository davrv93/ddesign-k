"""Pruebas del conocimiento de la tienda (JMD Ventas → agente, 15-10-2026; contrato docs/FUENTES_CONTRATO.md §3).

Sin modelo de embeddings ni LLM: el embedder es una bolsa de palabras y el LLM una respuesta fija (el arnés de
app/prueba_pulido_v3.py). Cubre la tabla de precios de promo, el alcance, la vigencia, la caché por (empresa, version),
el payload roto, dos empresas sin mezclarse, el validador (descuento con y sin promo, porcentaje equivocado), el hecho de
la promo en V3, la FAQ, `fuentes_usadas`, la regla `promo` de V1 y que sin conocimiento el prompt sea el de siempre.

    python3 -m app.prueba_conocimiento
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import types
from types import SimpleNamespace as N

from . import conocimiento as K, memoria, respuestas
from .prueba_pulido_v3 import B, C, PROMPTS, _llm_fijo, RESPUESTA, _stock
from .prueba_colores import fichas_web
from .v3 import agente, cliente_llm, prompt, validador
from .v3.herramientas import Recolector

FALLOS = 0


def caso(nombre: str, obtenido, esperado) -> None:
    global FALLOS
    if obtenido != esperado:
        FALLOS += 1
        print(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


def si(nombre: str, cond) -> None:
    caso(nombre, bool(cond), True)


# ---------------------------------------------------------------------------
# Datos

def promo(id=12, mecanica="porcentaje", valor=20, alcance=None, **extra) -> dict:
    d = {"id": id, "titulo": "Promo pelotera", "contenido": "", "mecanica": mecanica, "valor": valor,
         "alcance": alcance or {"tipo": "todo"}, "condiciones": "Solo pago por Yape.", "vigente_desde": "", "vigente_hasta": ""}
    d.update(extra)
    return d


def payload(empresa="baruka", version="v1", hoy="2026-10-15", promos=(), faqs=(), politicas=(), guion=None) -> dict:
    return {"empresa": empresa, "version": version, "hoy": hoy, "promos": list(promos), "faqs": list(faqs),
            "politicas": list(politicas), "guion": guion, "material": []}


VOCAB: dict[str, int] = {}


def embed_falso(textos):
    """Bolsa de palabras normalizada (sin tildes): dos frases con las mismas palabras dan coseno 1."""
    out = []
    for t in textos:
        palabras = [w for w in memoria._plano(t).replace("?", " ").split() if len(w) > 2]
        v = [0.0] * 64
        for w in palabras:
            v[VOCAB.setdefault(w, len(VOCAB) % 64)] += 1.0
        out.append(v)
    return out


# ---------------------------------------------------------------------------

def prueba_precios():
    P = 330.0
    pp = K.precio_promo({"mecanica": "porcentaje", "valor": 20}, P)
    caso("porcentaje 20% de 330", pp["precio_final"], 264.0)
    caso("porcentaje: ahorro", pp["ahorro"], 66.0)
    caso("porcentaje con decimales", K.precio_promo({"mecanica": "porcentaje", "valor": 15}, 289.9)["precio_final"], 246.42)
    caso("monto 50", K.precio_promo({"mecanica": "monto", "valor": 50}, P)["precio_final"], 280.0)
    caso("monto mayor que el precio → 0", K.precio_promo({"mecanica": "monto", "valor": 500}, P)["precio_final"], 0.0)
    caso("precio especial", K.precio_promo({"mecanica": "precio_especial", "valor": 250}, P)["precio_final"], 250.0)
    dos = K.precio_promo({"mecanica": "dos_por", "valor": 500}, P)
    caso("dos por: la unidad sigue a P", (dos["precio_final"], dos["precio_por_dos"]), (330.0, 500.0))
    env = K.precio_promo({"mecanica": "envio_gratis", "valor": None}, P)
    caso("envío gratis: P y envío 0", (env["precio_final"], env["envio"]), (330.0, 0.0))
    caso("regalo: P", K.precio_promo({"mecanica": "regalo", "valor": None}, P)["precio_final"], 330.0)
    caso("otra: P", K.precio_promo({"mecanica": "otra", "valor": None}, P)["precio_final"], 330.0)
    caso("sin precio: porcentaje no inventa", K.precio_promo({"mecanica": "porcentaje", "valor": 20}, None)["precio_final"], None)


def prueba_alcance(fichas):
    K.limpiar_cache()
    por = {f.codigo: f for f in fichas}
    salome, holly_p, holly_v = por["V50"], por["V88"], por["V41"]
    c = K.leer(payload(promos=[promo(1, alcance={"tipo": "todo"})]))
    si("todo cubre el Salome", K.promos_para(c, salome))
    c = K.leer(payload(version="cat", promos=[promo(2, alcance={"tipo": "categoria", "categoria": "Pantalones"})]))
    caso("categoría pantalón: el Pantalón Holly sí", [p["id"] for p in K.promos_para(c, holly_p)], [2])
    caso("categoría pantalón: el Vestido Holly no", K.promos_para(c, holly_v), [])
    caso("categoría por el nombre, no por la categoría del catálogo", K.categoria(holly_p), "pantalon")
    c = K.leer(payload(version="col", promos=[promo(3, alcance={"tipo": "coleccion", "coleccion": "vestidos"})]))
    caso("colección vestidos (largos): el Salome sí", [p["id"] for p in K.promos_para(c, salome)], [3])
    caso("colección vestidos (largos): el Holly (corto) no", K.promos_para(c, holly_v), [])
    c = K.leer(payload(version="pre", promos=[promo(4, alcance={"tipo": "prendas", "codigos": ["v50"]})]))
    caso("prendas por código (sin mayúsculas)", [p["id"] for p in K.promos_para(c, salome)], [4])
    caso("prendas: otra no", K.promos_para(c, holly_v), [])


def prueba_vigencia(fichas):
    K.limpiar_cache()
    salome = next(f for f in fichas if f.codigo == "V50")
    p = promo(5, vigente_desde="2026-10-15", vigente_hasta="2026-10-31")
    for hoy, esperado in (("2026-10-14", []), ("2026-10-15", [5]), ("2026-10-31", [5]), ("2026-11-01", [])):
        c = K.leer(payload(version="vig", hoy=hoy, promos=[p]))
        caso(f"vigencia el {hoy}", [x["id"] for x in K.promos_para(c, salome)], esperado)
    caso("la misma versión se cachea una vez aunque cambie el día", K.en_cache().count(("baruka", "vig")), 1)
    c = K.leer(payload(version="vig2", promos=[p]), hoy=dt.date(2026, 12, 1))
    caso("hoy inyectado manda sobre el del payload", K.vigentes(c), [])
    caso("vigente: false explícito no se usa", K.vigentes(K.leer(payload(version="f", promos=[promo(6, vigente=False)]))), [])


def prueba_cache_y_payload():
    K.limpiar_cache()
    a = K.leer(payload(version="1", promos=[promo(7)]))
    b = K.leer(payload(version="1", promos=[promo(8)]))          # misma versión: no se re-deriva
    caso("misma (empresa, version): misma base", a.base is b.base, True)
    c = K.leer(payload(version="2", promos=[promo(8)]))
    caso("versión nueva: base nueva", [p["id"] for p in c.base.promos], [8])
    for i in range(K.CACHE_MAX + 5):
        K.leer(payload(version=f"x{i}", promos=[promo(9)]))
    caso("el LRU no crece sin tope", len(K.en_cache()), K.CACHE_MAX)
    # payloads rotos: se ignoran, nunca revientan
    for nombre, roto in (("no es un mapa", "hola"), ("lista", [1, 2]), ("sin empresa", {"promos": [promo()]}),
                         ("promos no es lista", {"empresa": "b", "promos": "x"}), ("vacío", {"empresa": "b"}), ("None", None)):
        caso(f"payload roto ({nombre}) → None", K.leer(roto), None)
    malas = [promo(10, mecanica="magia"), promo(11, valor=150), promo(None), {"id": 13}, "texto",
             promo(14, mecanica="monto", valor=-5), promo(15, alcance={"tipo": "categoria", "categoria": "zapatos"}),
             promo(16, valor="20")]
    c = K.leer(payload(version="malas", promos=malas))
    caso("solo sobreviven las promos bien formadas", [p["id"] for p in c.base.promos], [16])
    caso("valor como texto se lee", c.base.promos[0]["valor"], 20.0)
    caso("faq sin respuesta se ignora", K.leer(payload(version="fq", faqs=[{"id": 1, "pregunta": "¿x?"}])), None)


def prueba_empresas(fichas):
    K.limpiar_cache()
    salome = next(f for f in fichas if f.codigo == "V50")
    a = K.leer(payload(empresa="baruka", version="same", promos=[promo(20, valor=20)]))
    b = K.leer(payload(empresa="otra", version="same", promos=[promo(30, valor=10)]))
    caso("dos empresas con la misma versión no se mezclan", ([p["id"] for p in K.promos_para(a, salome)],
                                                             [p["id"] for p in K.promos_para(b, salome)]), ([20], [30]))
    caso("claves de caché por empresa", sorted(K.en_cache()), [("baruka", "same"), ("otra", "same")])
    fa = K.leer(payload(empresa="baruka", version="fa", faqs=[{"id": 1, "pregunta": "¿Hacen factura?", "respuesta": "Sí, factura."}]))
    fb = K.leer(payload(empresa="otra", version="fa", faqs=[{"id": 2, "pregunta": "¿Hacen factura?", "respuesta": "No, solo boleta."}]))
    caso("FAQ de cada empresa", (K.buscar_faq(fa, "hacen factura?", embed_falso)["faq"]["id"],
                                 K.buscar_faq(fb, "hacen factura?", embed_falso)["faq"]["id"]), (1, 2))


def prueba_faq():
    K.limpiar_cache()
    c = K.leer(payload(version="faq", faqs=[{"id": 3, "pregunta": "¿Hacen factura?", "variantes": ["dan factura electronica"],
                                             "respuesta": "Sí, emitimos factura electrónica 😊"}]))
    hit = K.buscar_faq(c, "¿dan factura electrónica?", embed_falso)
    caso("FAQ por variante", hit and hit["faq"]["id"], 3)
    caso("FAQ: se abstiene si no se parece", K.buscar_faq(c, "tienen vestidos rojos largos", embed_falso), None)
    caso("FAQ: una palabra suelta no cuenta", K.buscar_faq(c, "factura", embed_falso), None)
    caso("FAQ sin embedder: nada", K.buscar_faq(c, "hacen factura?", None), None)
    caso("umbral configurable", K.buscar_faq(c, "hacen factura?", embed_falso, umbral=1.01), None)
    # Parecido alto sin ninguna palabra en común no basta (e5: «¿cuánto cuesta?» ≈ «¿emiten factura?» = 0,880).
    c = K.leer(payload(version="faq-raices", faqs=[{"id": 3, "pregunta": "¿Hacen factura?", "variantes": ["dan factura electronica"],
                                                    "respuesta": "Sí, emitimos factura electrónica 😊"}]))
    todo_igual = lambda textos: [[1.0, 0.0, 0.0] if ("hacen" in t or "dan " in t) else [0.9, 0.4359, 0.0] for t in textos]
    caso("FAQ: sin palabras en común no cuenta", K.buscar_faq(c, "¿cuánto cuesta?", todo_igual, umbral=0.5), None)
    caso("FAQ: con una palabra en común sí", (K.buscar_faq(c, "¿y facturan?", todo_igual, umbral=0.5) or {}).get("faq", {}).get("id"), 3)


def prueba_textos():
    K.limpiar_cache()
    c = K.leer(payload(version="t", politicas=[{"id": 4, "titulo": "Cambios", "contenido": "Cambios en 7 días."}],
                       guion={"id": 5, "titulo": "Guion", "contenido": "## Paso 1\nSaluda."}))
    caso("texto_tienda", K.texto_tienda(c), "## Cambios\nCambios en 7 días.")
    caso("texto_guion", K.texto_guion(c), "## Paso 1\nSaluda.")
    caso("sin conocimiento: textos vacíos", (K.texto_tienda(None), K.texto_guion(None)), ("", ""))
    # Las políticas se SUMAN a tienda.md (una sola política no puede dejar al bot sin showroom ni envíos, 15-10-2026).
    base = "SHOWROOM: Juan Ayllón 459.\nPromociones y descuentos: por ahora no hay, para nadie; el precio es el publicado.\nENVÍOS: Lima S/ 15."
    t = K.tienda_con(base, c)
    si("políticas suman: showroom sigue", "Juan Ayllón 459" in t and "ENVÍOS" in t and "Cambios en 7 días." in t)
    si("sin promos: la línea de «no hay promociones» queda", "Promociones y descuentos" in t)
    cp = K.leer(payload(version="t2", promos=[promo(12)]))
    si("con promos: sale la línea de «no hay promociones»", "Promociones y descuentos" not in K.tienda_con(base, cp)
       and "Juan Ayllón 459" in K.tienda_con(base, cp))
    caso("sin conocimiento: tienda.md tal cual", K.tienda_con(base, None), base)
    from .v3 import prompt as P
    si("V3: sistema con políticas conserva tienda.md", "Juan Ayllón" in P.sistema_para(c) and "Cambios en 7 días." in P.sistema_para(c))
    si("V3: con promos no dice que no hay promociones", "Promociones y descuentos: por ahora no hay" not in P.sistema_para(cp))


def prueba_validador():
    h = validador.hechos_vacios()
    si("sin promo: «20% de descuento» se rechaza", "promete un descuento" in validador.validar("Te hago 20% de descuento 😊", h))
    caso("sin promo: «no hay descuentos» pasa", validador.validar("Por ahora no hay descuentos 😊 ¿Te ayudo?", h), [])
    h = validador.hechos_vacios()
    h["hay_promos"] = True
    si("promos vigentes pero sin respaldo: pide la herramienta",
       any("promocion_vigente" in m for m in validador.validar("Tiene una promo genial 😊", h)))
    h = validador.hechos_vacios()
    h["promos"], h["porcentajes"], h["precios"] = {12}, {20.0}, {264.0, 330.0}
    caso("promo respaldada: pasa", validador.validar("Está en promo: de S/ 330 a S/ 264 (20% de descuento) 😊", h), [])
    si("porcentaje equivocado: se rechaza", "porcentaje no respaldado: 30%" in validador.validar("¡Tiene 30% de descuento!", h))
    si("precio de promo mal calculado: se rechaza", any(m.startswith("precio no respaldado") for m in
                                                        validador.validar("Con la promo queda en S/ 250 😊", h)))
    caso("composición de tela no es descuento", validador.validar("Es 95% poliéster 😊 ¿Te gusta?", {**h, "telas": {"poliéster"}}), [])


def _turno(fichas, mensaje, hist, mem=None, texto="Perfecto 😊 ¿Para qué ocasión?", herramientas=(), conoc=None, embed=None):
    RESPUESTA["texto"], RESPUESTA["herramientas"] = texto, list(herramientas)
    req = types.SimpleNamespace(mensaje=mensaje, historial=list(hist), memoria=json.loads(json.dumps(mem)) if mem else None,
                                cliente="Ana", etapa="", producto="", talla="", canal="whatsapp", estado="", foto_hecho="",
                                foto_codigo="", foto_oferta=False, colores_grupos=None, conocimiento=conoc)
    PROMPTS.clear()
    return agente.conversar(req, fichas, _stock, pie=lambda f, st: f"*{f.codigo}* {f.nombre}", embed=embed)


def prueba_pie_con_promo(fichas):
    """El pie de la foto de una prenda en promo dice el precio con promo (15-10-2026)."""
    K.limpiar_cache()
    c = K.leer(payload(version="pie", promos=[promo(12, alcance={"tipo": "categoria", "categoria": "vestido"})]))
    rec = Recolector(fichas, _stock, pie=lambda f, st: f"*{f.nombre}* — *S/ {f.precio:.2f}*\nTallas: S, M")
    rec.poner_conocimiento(c)
    salome = next(f for f in fichas if f.codigo == "V50")
    x = rec._foto(salome, salome.imagen, "")
    si("pie con promo", "~S/ 290.00~ *S/ 232.00*" in x["pie"] and "~S/ 290.00~ *S/ 232.00*" in x["titulo"])
    caso("pie con promo: respaldada", 12 in rec.hechos["promos"], True)
    rec2 = Recolector(fichas, _stock, pie=lambda f, st: f"*{f.nombre}* — *S/ {f.precio:.2f}*")
    caso("pie sin conocimiento: igual", rec2._foto(salome, salome.imagen, "")["pie"], "*Vestido Salome* — *S/ 290.00*")


def prueba_precio_sin_promo(fichas):
    """Con una promo respaldada, el texto no puede dar solo el precio normal (15-10-2026)."""
    K.limpiar_cache()
    c = K.leer(payload(version="psp", promos=[promo(12, alcance={"tipo": "categoria", "categoria": "vestido"})]))
    rec = Recolector(fichas, _stock)
    rec.poner_conocimiento(c)
    salome = next(f for f in fichas if f.codigo == "V50")
    rec.respaldar_promo(c.base.promos[0], [salome])
    m = agente._precio_sin_promo("El Vestido Salome cuesta S/ 290 😊", rec)
    si("precio normal solo: motivo", bool(m) and "232" in m[0])
    caso("precio normal y de promo: sin motivo", agente._precio_sin_promo("De S/ 290 a S/ 232 con la promo", rec), [])
    caso("arreglo de la segunda vuelta", agente._con_precio_promo("Cuesta S/ 290. ¿Qué talla?", rec),
         "Cuesta S/ 290 (con la promo, S/ 232.00). ¿Qué talla?")
    caso("sin promo: sin motivo", agente._precio_sin_promo("Cuesta S/ 290", Recolector(fichas, _stock)), [])


def prueba_v3(fichas):
    K.limpiar_cache()
    base = [C("hola"), B("¡Hola! Te saluda Rosmary 😊 ¿Qué evento se te aproxima?"), C("una boda"),
            B("¡Qué lindo! ¿En qué talla te encuentras?")]
    mem = {"producto": "V50", "mostrados": ["V50"], "sabemos": {"ocasion": "boda"}}
    # 1. Sin conocimiento: el prompt es el de siempre y no hay fuentes.
    r = _turno(fichas, "¿cuánto está el Salome?", base, mem)
    si("sin conocimiento: sistema idéntico al de siempre", PROMPTS and PROMPTS[0].startswith(prompt.SISTEMA))
    caso("sin conocimiento: fuentes_usadas vacío", r.get("fuentes_usadas"), [])
    si("sin conocimiento: la veda sigue en el prompt", "Nunca ofrezcas descuentos" in PROMPTS[0])
    caso("sin conocimiento: sin herramienta de promos", any(t["function"]["name"] == "promocion_vigente"
                                                            for t in Recolector(fichas, _stock).esquema()), False)
    # 2. Promo del 20% en vestidos, el Salome (S/ 290) en foco: hecho con el precio calculado por código (232).
    conoc = payload(version="v3", promos=[promo(12, alcance={"tipo": "categoria", "categoria": "vestido"},
                                                vigente_hasta="2026-10-31", mensaje_apertura="¡Esta semana los vestidos tienen 20%!")])
    r = _turno(fichas, "¿cuánto está el Salome?", base, mem, conoc=conoc,
               texto="El Vestido Salome está en promo: de S/ 290 a S/ 232 (20% de descuento) 😊 ¿En qué talla te encuentras?")
    p0 = PROMPTS[0] if PROMPTS else ""
    si("promo: hecho verificado con el precio de código", "HECHO YA VERIFICADO: PROMO «Promo pelotera»" in p0 and "S/ 232.00" in p0)
    si("promo: condiciones y vigencia en el hecho", "Solo pago por Yape" in p0 and "31-10-2026" in p0)
    si("promo: la veda se cambia por el bloque de promos", "PROMOS VIGENTES" in p0 and "Nunca ofrezcas descuentos" not in p0)
    caso("promo: la respuesta respaldada no se rechaza", r["v3"].get("motivos"), [])
    caso("promo: fuentes_usadas arriba y en la traza", (r.get("fuentes_usadas"), r["v3"].get("fuentes_usadas")), ([12], [12]))
    caso("promo: queda dicha en la ficha", r["memoria"].get("promos_dichas"), [12])
    # 3. Segunda vez sin preguntar precio ni promo: no se vuelve a empujar la promo.
    r2 = _turno(fichas, "es de noche", base, r["memoria"], conoc=conoc)
    si("promo ya dicha: no se repite el hecho", "PROMO «Promo pelotera»" not in (PROMPTS[0] if PROMPTS else "x"))
    caso("promo ya dicha y no mencionada: sin fuentes", r2.get("fuentes_usadas"), [])
    # 4. Porcentaje inventado con promo vigente: rechazado (dos veces → cae a V1).
    try:
        _turno(fichas, "¿cuánto está el Salome?", base, mem, conoc=conoc, texto="¡Tiene 30% de descuento! 😊 ¿Qué talla?")
        caso("porcentaje inventado: se rechaza", "pasó", "Rechazado")
    except agente.Rechazado as e:
        si("porcentaje inventado: se rechaza", "porcentaje no respaldado" in str(e))
    # 5. Pide descuento de una prenda que NO entra (Pantalón Holly): la respuesta de la tienda para eso.
    conoc_f = payload(version="v3f", promos=[promo(12, alcance={"tipo": "categoria", "categoria": "vestido"},
                                                   respuesta_fuera="Esa prenda no entra en la promo, pero tiene un precio buenazo 😊")])
    _turno(fichas, "¿me haces descuento en el pantalón holly?", base, {"producto": "V88", "mostrados": ["V88"]}, conoc=conoc_f,
           texto="Esa prenda no entra en la promo, pero tiene un precio buenazo 😊 ¿Qué talla usas?")
    si("prenda fuera de la promo: respuesta_fuera", "NO entra en ninguna promo" in (PROMPTS[0] if PROMPTS else "")
       and "precio buenazo" in PROMPTS[0])
    # 6. «¿tienen promo?» sin prenda: las vigentes.
    conoc_g = payload(version="v3g", promos=[promo(12, respuesta_promo="Sí 😊 Esta semana todo tiene 20% pagando por Yape.")])
    r = _turno(fichas, "hola, ¿tienen alguna promo?", [], None, conoc=conoc_g,
               texto="¡Hola! 😊 Te saluda Rosmary. Sí, esta semana todo tiene 20% pagando por Yape. ¿Qué evento se te aproxima?")
    si("«¿tienen promo?»: respuesta_promo en el hecho", "PREGUNTA POR PROMOS" in (PROMPTS[0] if PROMPTS else "")
       and "todo tiene 20%" in PROMPTS[0])
    caso("«¿tienen promo?»: fuentes", r.get("fuentes_usadas"), [12])
    # 7. FAQ de la tienda: su respuesta va como hecho y su id en fuentes_usadas.
    conoc_q = payload(version="v3q", faqs=[{"id": 3, "pregunta": "¿Hacen factura?", "variantes": ["dan factura"],
                                            "respuesta": "Sí, emitimos factura electrónica con tu RUC 😊"}])
    r = _turno(fichas, "¿hacen factura?", base, mem, conoc=conoc_q, embed=embed_falso,
               texto="Sí, emitimos factura electrónica con tu RUC 😊 ¿En qué talla te encuentras?")
    si("FAQ: hecho con la respuesta de la tienda", "RESPUESTA DE LA TIENDA (pregunta frecuente): Sí, emitimos factura" in
       (PROMPTS[0] if PROMPTS else ""))
    caso("FAQ: fuentes_usadas", r.get("fuentes_usadas"), [3])
    # 8. Políticas y guion de la tienda sustituyen a tienda.md y speech.md.
    conoc_p = payload(version="v3p", politicas=[{"id": 4, "titulo": "Envíos", "contenido": "ENVIO-DE-PRUEBA-XYZ"}],
                      guion={"id": 5, "titulo": "Guion", "contenido": "## Paso 1\nGUION-DE-PRUEBA-XYZ"})
    r = _turno(fichas, "¿hacen envíos?", base, mem, conoc=conoc_p)
    p0 = PROMPTS[0] if PROMPTS else ""
    si("políticas se suman a tienda.md", "ENVIO-DE-PRUEBA-XYZ" in p0 and prompt.TIENDA[:60] in p0)
    si("guion sustituye a speech.md", "GUION-DE-PRUEBA-XYZ" in p0 and (not prompt.SPEECH or prompt.SPEECH[:60] not in p0))
    caso("políticas y guion no son «usadas»", r.get("fuentes_usadas"), [])
    # 9. La herramienta, llamada por el modelo.
    K.limpiar_cache()
    rec = Recolector(fichas, _stock)
    rec.poner_conocimiento(K.leer(conoc))
    si("con promos: el esquema trae promocion_vigente", any(t["function"]["name"] == "promocion_vigente" for t in rec.esquema()))
    out = rec.ejecutar("promocion_vigente", {"codigo": "V50"})
    caso("herramienta: precio de promo del Salome", out["promos"][0]["prendas"][0]["precio_promo"], 232.0)
    si("herramienta: respalda el precio y el porcentaje", 232.0 in rec.hechos["precios"] and 20.0 in rec.hechos["porcentajes"]
       and rec.hechos["promos"] == {12})
    out = rec.ejecutar("promocion_vigente", {"codigo": "V88"})
    caso("herramienta: prenda fuera de la promo", out["promos"], [])
    caso("herramienta sin argumentos: todas las vigentes", [p["id"] for p in rec.ejecutar("promocion_vigente", {})["promos"]], [12])


def prueba_v1(fichas):
    """Regla `promo` de la tabla de V1 (app/respuestas.py): precio calculado por código; `descuento` no dispara si hay promo."""
    salome = next(f for f in fichas if f.codigo == "V50")
    p = K.leer(payload(version="v1", promos=[promo(12)])).base.promos[0]

    def ctx(mensaje, foco=salome, con_promo=True, promos_txt="Promo pelotera: 20% de descuento en toda la tienda.", faq=None):
        return N(mensaje=mensaje, plano=memoria._plano(mensaje), canal="", negocio="Baruka", intent="", intent_crudo="", nivel="alta",
                 cl_intencion="otro", cl_confianza=0.2, etapa="seguimiento", datos={}, sabemos={}, lectura={}, pend="", q="", cab="",
                 no_hay="", no_hay_viene="", es_bot=False, una_opcion=None, sugeridas=[], pide=False, es_catalogo=False,
                 describiendo=False, esperando_cual=False, mas_barato=False, foco=foco, ref=f"*{foco.nombre}*" if foco else "",
                 nombrados=[], sol=N(elogia=False, pide_rebaja="descuento" in mensaje), pide_tela=False, pide_attr=None,
                 clf=frozenset(), cat_p=None, cat_foco="vestido", libres=["S", "M"], pregunta_variante=False, ofrecer=False,
                 afirma=False, niega=False, art=lambda f: "el", y=lambda xs: ", ".join(xs),
                 promo=p if con_promo else None, promo_precio=K.precio_promo(p, foco.precio) if (con_promo and foco) else None,
                 promos_txt=promos_txt, promo_fuera="", faq=faq,
                 k=N(UMBRAL_ACCION=0.55, RE_ROPA=memoria.re.compile("x^"), RE_TEMA_TIENDA=memoria.re.compile("x^"), FUERA_DE_GIRO="",
                     OFERTA="", AVISO_DESCUENTO="Por ahora no tenemos descuentos ni promociones: el precio es el publicado 😊",
                     AVISO_TIEMPO="", MONEDA="S/", PLURAL={}))

    texto, traza = respuestas.componer(ctx("¿cuánto está?"))
    si("V1 precio con promo: regla promo", "promo" in traza["reglas"] and "S/ 232.00" in texto and "S/ 290.00" in texto)
    caso("V1: la regla precio no se suma", "precio" in traza["reglas"], False)
    texto, traza = respuestas.componer(ctx("¿me haces un descuento?"))
    si("V1: descuento con promo en el foco → promo, no la veda", "promo" in traza["reglas"] and "descuento" not in traza["reglas"])
    texto, traza = respuestas.componer(ctx("¿me haces un descuento?", con_promo=False))
    si("V1: prenda fuera de la promo → no entra", "descuento" in traza["reglas"] and "no entra en la promo" in texto)
    texto, traza = respuestas.componer(ctx("¿me haces un descuento?", con_promo=False, promos_txt=""))
    si("V1 sin promos: la política de siempre", "no tenemos descuentos" in texto)
    # FAQ de la tienda en V1 (15-10-2026): «¿dan factura?» caía en la regla del pago.
    faq = {"faq": {"id": 3, "respuesta": "Sí, emitimos factura electrónica con tu RUC."}, "score": 0.93}
    c = ctx("dan factura con ruc? como les pago", foco=None, con_promo=False, faq=faq)
    c.datos, c.intent = {}, "consulta_pago"
    texto, traza = respuestas.componer(c)
    si("V1: la FAQ de la tienda contesta y el pago no se suma", "faq" in traza["reglas"] and "factura electrónica" in texto
       and "pago" not in traza["reglas"])


def main() -> int:
    antes = (cliente_llm.responder, cliente_llm.configurado)
    cliente_llm.responder, cliente_llm.configurado = _llm_fijo, (lambda: True)
    fichas = fichas_web()
    try:
        prueba_precios()
        prueba_alcance(fichas)
        prueba_vigencia(fichas)
        prueba_cache_y_payload()
        prueba_empresas(fichas)
        prueba_faq()
        prueba_textos()
        prueba_validador()
        prueba_pie_con_promo(fichas)
        prueba_precio_sin_promo(fichas)
        prueba_v3(fichas)
        prueba_v1(fichas)
    finally:
        cliente_llm.responder, cliente_llm.configurado = antes
        K.limpiar_cache()
    print(f"prueba_conocimiento: {FALLOS} fallos")
    return 1 if FALLOS else 0


if __name__ == "__main__":
    sys.exit(main())
