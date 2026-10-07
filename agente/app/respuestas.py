"""Respuesta del código (sin LLM) como TABLA DE DECISIÓN, no como cadena de if/elif.

Antes vivía dentro de `main.conversar` (`respaldo_codigo`): ~25 ramas encadenadas donde el orden decidía y cada turno daba
UNA sola respuesta. Ahora:

1. `hechos()` lee el mensaje UNA vez y devuelve hechos con nombre (`pregunta_precio`, `habla_envio`, `pide_rebaja`…).
2. `TABLA` lista las reglas por grupo, en orden de prioridad: `encabezado` (qué se dice primero), `dato_prenda` (lo que
   preguntó de la prenda en foco), `dato_tienda` (envío, showroom, pago, descuento…) y `acuse` («¡Anotado!»).
3. `componer()` aplica la política de cada grupo y arma el texto con la pregunta que toca.

Políticas: `encabezado` y `acuse` son «primera» (gana la primera regla que dispara). Los DATOS (de la prenda y de la
tienda) se «colectan»: si pregunta el precio y la talla, se contestan las dos. Lo decidió la medición del 07-10 sobre las
613 frases de las 100 conversaciones web: ~20 respuestas perdían una pregunta («¿cuánto está y lo tienes en M?» → solo
el precio). Para no amontonar: `EXCLUYE` (una regla cubre a otra), `SOLO` (reglas que solo valen si no hay otro dato) y
`MAX_DATOS`. `RESPUESTAS_COLECTAR=0` vuelve a «primera» en todo (la salida de antes, idéntica). La traza guarda las
reglas elegidas y las que también disparaban (`colisiones`).

    python3 -m app.respuestas        # imprime la tabla (grupo, regla, cuándo dispara, efecto)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable

from . import estructurado, ficha_producto, memoria, venta

# ---------------------------------------------------------------------------
# Patrones con nombre (antes, sueltos dentro de las ramas)

RE_PRECIO = re.compile(r"\b(precio|cuanto (cuesta|sale|esta|vale|es)|que precio|a cuanto)\b")
RE_ENVIO = re.compile(r"\b(envi\w+|delivery|mandan|despach\w+|demora\w*|tarda\w*|llega\w*)\b")
RE_TALLAS = re.compile(r"\btallas?\b|\bstock\b|\bdisponib")
RE_PREGUNTONA = re.compile(r"\b(si|como|cual|cuales|cuanto|cuanta|que|donde|cuando|porque|pregunt\w*|saber|dime|decir|apretad\w*|suelt\w*|largo|corto)\b")
RE_DESCUENTO = re.compile(r"\b(descuent\w*|rebaj\w*|promo(cion(es)?)?|ofertas?|precio especial)\b")
RE_UBICACION = re.compile(r"\b(donde (estan|quedan|queda|es)|direccion|ubicacion|ubicad\w+|horario|a que hora (abren|atienden|cierran))\b")
RE_TIEMPO = re.compile(r"\b(demora\w*|tarda\w*|cuando llega|cuanto tiempo|en cuanto)\b")
RE_PRESUPUESTO = re.compile(r"\bpresupuesto\b|\bmaximo\b|\bhasta \d{2,4}\b|\b\d{3} soles\b")
RE_DUDA = re.compile(r"\bno s[eé]\b|\bno (tengo|estoy) (claro|segura)|\bno se que\b|\baun no\b|\btodavia no\b|\bsolo (estoy )?viendo\b|\bnada\b")
# «Pásamelos al toque, ps, y te mando el comprobante» pide algo con apuro: no es una grosería (07-10).
RE_PEDIDO_APURADO = re.compile(r"\b(pas[ae]\w*|mand[ae]\w*|envi[ae]\w*|dame|damelos|dejame|al toque|rapido|comprobante|yape\w*|pago|datos|cuenta)\b")
RE_CUENTA = re.compile(r"\b\d+\s*(por|mas|menos|entre|dividido|\+|\*)\s*\d+\b")
RE_CALCULA = re.compile(r"\bcuanto (es|da|sale)\b|\bcalcul|\bresuelv")


# ---------------------------------------------------------------------------
# Hechos: lo que dice el mensaje, leído una sola vez

def hechos(c) -> dict:
    """Hechos con nombre sobre el mensaje y el contexto `c` (lo arma `main.respaldo_codigo`)."""
    p, i, preg = c.plano, c.intent, "?" in c.mensaje
    clf = getattr(c, "clf", frozenset())      # lo que detecta app/comprension.py (vacío si no está activo)
    habla_envio = bool(RE_ENVIO.search(p))
    return {
        "pregunta": preg,
        "pregunta_precio": i == "consulta_precio" or bool(RE_PRECIO.search(p)) or "precio" in clf,
        "habla_envio": habla_envio,
        "pide_envio": habla_envio or (i == "consulta_delivery" and preg) or "envio" in clf,
        "pide_tallas": i in ("consulta_talla", "consulta_disponibilidad") or bool(RE_TALLAS.search(p)) or "talla" in clf,
        "pide_color": i == "consulta_color" or c.pregunta_variante or "color" in clf,
        "elogia": i == "interesado" or (c.sol.elogia and not preg),
        "nombra_sin_preguntar": (i == "consulta_producto" and not preg and bool(c.nombrados) and not RE_PREGUNTONA.search(p)),
        "pide_detalle": i == "consulta_producto" and preg,
        "menciona_descuento": (bool(RE_DESCUENTO.search(p)) and not c.datos.get("ocasion")) or c.sol.pide_rebaja,
        "pide_ubicacion": ((i in ("consulta_ubicacion", "consulta_horario") and preg and not c.nombrados) or bool(RE_UBICACION.search(p))
                           or "ubicacion" in clf),
        "pregunta_tiempo": bool(RE_TIEMPO.search(p)),
        "pide_pago": (i == "consulta_pago" and preg or "pago" in clf) and c.etapa != "venta_confirmada",
        "duda": (i in ("objecion", "objecion_precio") and c.nivel == "alta" and not c.sol.pide_rebaja
                 and not any(c.datos.get(k) for k in ("ocasion", "fecha", "horario", "prenda", "nombre", "presupuesto"))
                 and not RE_PRESUPUESTO.search(p)),
        "despide": i == "despedida" or "despedida" in clf,
        "groseria": (c.cl_intencion == "censura" and c.cl_confianza >= c.k.UMBRAL_ACCION
                     and not RE_DUDA.search(p) and not RE_PEDIDO_APURADO.search(p)),
        "fuera_de_giro": (c.cl_intencion == "pregunta_general" and c.cl_confianza >= c.k.UMBRAL_ACCION and not c.lectura.get("respondio")
                          and not getattr(c, "pide_attr", None)
                          and not c.datos and not c.k.RE_ROPA.search(c.mensaje) and not c.k.RE_TEMA_TIENDA.search(p)
                          and len(p.split()) >= 3 and not c.nombrados and not c.afirma and not c.niega),
        "pide_cuenta": bool(RE_CUENTA.search(p) and RE_CALCULA.search(p)),
    }


# ---------------------------------------------------------------------------
# Textos (las plantillas de cada regla)

def _de(c, f) -> str:
    return "del" if c.art(f) == "el" else "de la"


def _t_no_hay(c, h):
    return f"Por ahora no tengo {c.no_hay} 😔" + (f" Viene en color {c.no_hay_viene}." if c.no_hay_viene else "")


def _t_una_opcion(c, h):
    oc = memoria.OCASION_TXT.get(c.sabemos.get("ocasion") or "", "")
    f = c.una_opcion
    return (f"Para {oc} te recomiendo" if oc else "Te recomiendo") + f" {c.art(f)} *{f.codigo}* {f.nombre} 😊 Te paso la foto."


def _t_vitrina(c, h):
    if c.describiendo or c.esperando_cual:
        return "Mira, ¿es alguno de estos? Te paso las fotos."
    f0 = c.sugeridas[0]
    return "¡Claro! 😊 Te paso " + (f"la foto {_de(c, f0)} *{f0.nombre}*." if len(c.sugeridas) == 1 else "las fotos.")


def _t_talla_dicha(c, h):
    t_p, f = c.datos.get("talla"), c.foco
    txt = (f"Sí, {c.art(f)} {c.ref} está disponible en talla *{t_p}* 😊" if t_p in c.libres else
           f"En talla *{t_p}* no hay 😔" + (f" Hay en {c.y(c.libres)}." if c.libres else ""))
    if c.pregunta_variante and f.color:      # «sería en L, ¿pero lo tienes en otros colores?»
        txt += f"\n\nViene en color {f.color}."
    return txt


def _t_envio(c, h):
    envios, k = venta.VENTA.get("envio") or {}, c.k
    zona = c.datos.get("envio") or c.sabemos.get("envio")
    if zona in envios:
        destino = (c.sabemos.get("ciudad") or zona).title() if zona == "provincia" else "Lima"
        txt = f"El envío a {destino} es *{k.MONEDA} {envios[zona]['costo']:.2f}* ({envios[zona]['detalle']})."
    else:
        txt = "Sí hacemos envíos 😊 " + " y ".join(f"a {z.capitalize()} *{k.MONEDA} {e['costo']:.2f}*" for z, e in envios.items()) + "."
    return txt + (" " + k.AVISO_TIEMPO if h["pregunta_tiempo"] else "")


def _t_showroom(c, h):
    sr = venta.SHOWROOM
    return f"Nuestro showroom está en *{sr.get('direccion', '')}* ({sr.get('referencia', '')}). Atendemos solo con cita, {sr.get('horario', '')}."


def _precio(c) -> str:
    return f"{c.art(c.foco).capitalize()} {c.ref} está a *{c.k.MONEDA} {c.foco.precio:.2f}* 😊"


# ---------------------------------------------------------------------------
# La tabla

@dataclass(frozen=True)
class Regla:
    id: str
    grupo: str
    cuando: str                                 # lo que dice la regla, en palabras (sale en `python3 -m app.respuestas`)
    dispara: Callable[[object, dict], bool]
    texto: Callable[[object, dict], str]
    sin_pregunta: bool = False                  # no se le hace la pregunta que tocaba (a quien duda o se despide no se le empuja)
    corta: bool = False                         # la respuesta es solo esta (fuera de giro)


GRUPOS = ("encabezado", "datos", "acuse")       # «datos» = dato_prenda + dato_tienda, en ese orden de prioridad
COLECTAR = os.environ.get("RESPUESTAS_COLECTAR", "1") != "0"
POLITICA = {"encabezado": "primera", "datos": "colectar" if COLECTAR else "primera", "acuse": "primera"}
MAX_DATOS = 3
# Una regla cubre a otras: si dispara, las otras sobran («¿tienes en M?» ya dice la disponibilidad; «¿cuánto el envío?» no
# pregunta el precio de la prenda).
EXCLUYE = {
    "envio_no_es_precio": {"precio", "precio_al_nombrar"},
    "talla_dicha": {"tallas", "detalle"},
    "tallas": {"detalle"},
    "tela": {"detalle"},
    "atributo": {"detalle"},
    "precio": {"precio_al_nombrar", "detalle"},
    "color": {"detalle"},
}
# Un dato EXTRA (el segundo o el tercero) solo entra si el texto lo pide de forma explícita: la intención sola no basta
# (07-10: «¿cuánto cuesta el Kendall? no me digas talla todavía» recibía las tallas porque la intención era «consulta_talla»).
RE_NO_QUIERE = re.compile(r"\bno (me )?(digas|preguntes|hables|mandes|pases)\b[^.?!]{0,20}")
EXPLICITO = {
    "precio": lambda c, h: bool(RE_PRECIO.search(c.plano)),
    "talla_dicha": lambda c, h: bool(c.datos.get("talla")),
    "tallas": lambda c, h: bool(RE_TALLAS.search(c.plano)),
    "color": lambda c, h: c.pregunta_variante or bool(re.search(r"\bcolou?r(es)?\b", c.plano)),
    "tela": lambda c, h: c.pide_tela,
    "atributo": lambda c, h: bool(getattr(c, "pide_attr", None)),
    "descuento": lambda c, h: h["menciona_descuento"],
    "envio": lambda c, h: h["habla_envio"],
    "showroom": lambda c, h: bool(RE_UBICACION.search(c.plano)),
}


# Un dato extra también es explícito si lo detecta el clasificador entrenado (precisión validada ≥ 80 %).
_CLF_DE_REGLA = {"precio": "precio", "talla_dicha": "talla", "tallas": "talla", "color": "color", "tela": "tela",
                 "descuento": "rebaja", "envio": "envio", "showroom": "ubicacion"}


def _explicito(r, c, h) -> bool:
    if _CLF_DE_REGLA.get(r.id) in getattr(c, "clf", frozenset()):
        return True
    ok = EXPLICITO.get(r.id)
    if ok is None or not ok(c, h):
        return False
    # «no me digas talla todavía»: lo que niega no se le dice
    palabra = {"tallas": "talla", "talla_dicha": "talla", "precio": "precio", "color": "color", "envio": "envi"}.get(r.id)
    return not (palabra and any(palabra in m.group(0) for m in RE_NO_QUIERE.finditer(c.plano)))


# Valen solo si no hay ningún otro dato: la descripción general, el ánimo y el cierre de la charla no se mezclan con datos.
SOLO = {"detalle", "elogio", "duda", "despedida", "groseria"}

TABLA: list[Regla] = [
    # --- encabezado: qué se dice primero
    Regla("no_hay", "encabezado", "pidió algo que no tenemos (color, prenda, talla)",
          lambda c, h: bool(c.no_hay), _t_no_hay),
    Regla("es_bot", "encabezado", "pregunta si es un bot",
          lambda c, h: c.es_bot, lambda c, h: f"Soy la asistente virtual de {c.negocio} 😊 Si prefieres que te atienda una asesora, escribe *4*."),
    Regla("fuera_de_giro", "encabezado", "pregunta general ajena a la tienda",
          lambda c, h: h["fuera_de_giro"], lambda c, h: c.k.FUERA_DE_GIRO, corta=True),
    Regla("cuenta", "encabezado", "pide una cuenta («cuánto es 25 x 4»)",
          lambda c, h: h["pide_cuenta"], lambda c, h: c.k.FUERA_DE_GIRO, corta=True),
    Regla("una_opcion", "encabezado", "el método de venta eligió UNA prenda para recomendar",
          lambda c, h: c.una_opcion is not None, _t_una_opcion),
    Regla("vitrina", "encabezado", "hay fotos y pidió ver opciones, el catálogo o está describiendo una prenda",
          lambda c, h: bool(c.sugeridas) and (c.pide or c.es_catalogo or c.describiendo or (c.esperando_cual and bool(c.lectura.get("describe")))),
          _t_vitrina),
    Regla("sin_mas_opciones", "encabezado", "pidió más opciones y no quedan",
          lambda c, h: c.pide, lambda c, h: "Por ahora no tengo otra más económica en esa línea 😊" if c.mas_barato else
          "Por ahora eso es todo lo que tengo en esa línea 😊"),
    Regla("foto", "encabezado", "va una foto sugerida",
          lambda c, h: bool(c.sugeridas),
          lambda c, h: f"¡Claro! 😊 Te paso la foto {_de(c, c.sugeridas[0])} *{c.sugeridas[0].codigo}* {c.sugeridas[0].nombre}."),
    Regla("tenemos_categoria", "encabezado", "pregunta por un tipo de prenda sin una en foco («¿tienen blusas?»)",
          lambda c, h: bool(c.cat_p) and h["pregunta"] and c.foco is None,
          lambda c, h: f"¡Sí, tenemos {c.k.PLURAL.get(c.cat_p, c.cat_p)}! 😊"),
    Regla("tenemos_prenda", "encabezado", "nombra la prenda en foco sin pedir otra cosa (y no regatea)",
          lambda c, h: (c.foco is not None and bool(c.nombrados) and c.intent in ("", "otro", "interesado", "consulta_ubicacion")
                        and not c.sol.pide_rebaja),
          lambda c, h: f"¡Sí, tenemos {c.art(c.foco)} {c.ref}! 😊" if h["pregunta"] or c.intent != "interesado" else "¡Buena elección! 😊"),

    # --- dato de la prenda en foco (solo si se habla de ella)
    Regla("tela", "dato_prenda", "pregunta la tela", lambda c, h: c.pide_tela,
          lambda c, h: venta.respuesta_tela(c.foco.codigo, c.foco.nombre, c.foco.detalle)),
    Regla("atributo", "dato_prenda", "pregunta mangas, largo, escote, espalda, cierre, forro… (ficha técnica)",
          lambda c, h: bool(getattr(c, "pide_attr", None)),
          lambda c, h: ficha_producto.respuesta(c.foco.codigo, c.ref, c.pide_attr)),
    Regla("envio_no_es_precio", "dato_prenda", "habla de envío: «¿cuánto sale el envío?» no pregunta el precio de la prenda",
          lambda c, h: h["habla_envio"], lambda c, h: ""),
    Regla("precio", "dato_prenda", "pregunta el precio", lambda c, h: h["pregunta_precio"] and c.foco.precio is not None,
          lambda c, h: _precio(c)),
    Regla("talla_dicha", "dato_prenda", "dijo o preguntó una talla concreta", lambda c, h: bool(c.datos.get("talla")), _t_talla_dicha),
    Regla("tallas", "dato_prenda", "pregunta tallas o stock", lambda c, h: h["pide_tallas"],
          lambda c, h: (f"{c.art(c.foco).capitalize()} {c.ref} está disponible en talla {c.y(c.libres)}." if c.libres else
                        f"{c.art(c.foco).capitalize()} {c.ref} está agotado por ahora 😔")),
    Regla("color", "dato_prenda", "pregunta el color o por otra variante", lambda c, h: h["pide_color"] and bool(c.foco.color),
          lambda c, h: f"{c.art(c.foco).capitalize()} {c.ref} viene en color {c.foco.color}."),
    Regla("elogio", "dato_prenda", "elogia la prenda y no hay nada más que decir", lambda c, h: h["elogia"] and not c.encabezado and not c.q,
          lambda c, h: f"¡Qué bueno que te guste {c.art(c.foco)} {c.ref}! 😊"),
    Regla("precio_al_nombrar", "dato_prenda", "la nombró sin preguntar nada: el precio abre la charla",
          lambda c, h: h["nombra_sin_preguntar"] and not c.encabezado and not c.q and not c.sugeridas and c.foco.precio is not None,
          lambda c, h: _precio(c)),
    Regla("detalle", "dato_prenda", "pregunta algo de la prenda que no es precio, talla, tela ni color",
          lambda c, h: h["pide_detalle"] and bool(c.foco.detalle) and not c.sugeridas,
          lambda c, h: f"Te cuento {_de(c, c.foco)} {c.ref}: " + (estructurado._frases(c.foco.detalle) or [c.foco.detalle])[0][:220]),

    # --- dato de la tienda (solo si no hubo dato de la prenda)
    Regla("descuento", "dato_tienda", "pide descuento o rebaja (política: no hay para nadie)", lambda c, h: h["menciona_descuento"],
          lambda c, h: c.k.AVISO_DESCUENTO),
    Regla("envio", "dato_tienda", "pregunta por el envío", lambda c, h: h["pide_envio"] and bool(venta.VENTA.get("envio")), _t_envio),
    Regla("showroom", "dato_tienda", "pregunta dónde están o el horario", lambda c, h: h["pide_ubicacion"] and bool(venta.SHOWROOM),
          _t_showroom),
    Regla("pago", "dato_tienda", "pregunta cómo pagar sin pedido confirmado", lambda c, h: h["pide_pago"],
          lambda c, h: "El pago se hace antes del envío y me compartes el comprobante por aquí. Los datos te los paso cuando confirmemos tu pedido 😊"),
    Regla("duda", "dato_tienda", "duda o pospone (sin regatear ni dar datos)", lambda c, h: h["duda"] and not c.encabezado,
          lambda c, h: "Te entiendo, sin apuro 😊 Cuando lo decidas, aquí estoy.", sin_pregunta=True),
    Regla("despedida", "dato_tienda", "se despide", lambda c, h: h["despide"] and not c.encabezado,
          lambda c, h: "¡Gracias a ti! 😊 Cualquier cosa, me escribes por aquí.", sin_pregunta=True),
    Regla("groseria", "dato_tienda", "mensaje grosero (no una duda ni un pedido apurado)", lambda c, h: h["groseria"],
          lambda c, h: "Disculpa si algo te incomodó 🙏 Estoy aquí para ayudarte con lo que necesites de la tienda."),

    # --- acuse: contestó la pregunta del bot y no hay nada más que decir
    Regla("acuse", "acuse", "respondió la pregunta pendiente y no hay otro texto",
          lambda c, h: (not c.cuerpo and bool(c.lectura.get("respondio")) and c.pend in memoria.INDAGAR + ("talla",)
                        and not c.lectura.get("no_sabe")),
          lambda c, h: "¡Qué bonito! 😊" if (c.pend == "ocasion" and c.datos.get("ocasion") not in (None, "diario", "trabajo")) else "¡Anotado! 😊"),
]


def _de_la_prenda(c) -> bool:
    """Se contesta sobre la prenda en foco (no si pregunta por otra categoría o van fotos de otras prendas)."""
    de_otra = bool((c.cat_p and c.cat_p != c.cat_foco) or any(f is not c.foco for f in c.sugeridas))
    return c.foco is not None and not c.no_hay and not c.es_bot and not de_otra


def _datos(c, h) -> list[Regla]:
    """Las reglas de datos que disparan, en orden de prioridad (primero las de la prenda, luego las de la tienda)."""
    prenda = [r for r in TABLA if r.grupo == "dato_prenda" and r.dispara(c, h)] if _de_la_prenda(c) else []
    tienda = [r for r in TABLA if r.grupo == "dato_tienda" and r.dispara(c, h)] if (not c.no_hay and not c.es_bot) else []
    if POLITICA["datos"] == "primera":
        # La de antes: un solo dato; si la prenda «contestó» algo (aunque sea vacío, como el envío), la tienda no habla
        # salvo que el de la prenda quedara vacío.
        if prenda:
            return prenda[:1] if (prenda[0].texto(c, h) or not tienda) else prenda[:1] + tienda[:1]
        return tienda[:1]
    elegidas = prenda + tienda
    quitar = set().union(*(EXCLUYE.get(r.id, set()) for r in elegidas)) if elegidas else set()
    if c.pregunta_variante and any(r.id == "talla_dicha" for r in elegidas):
        quitar.add("color")         # la respuesta de la talla ya dice el color cuando pregunta por otra variante
    elegidas = [r for r in elegidas if r.id not in quitar]
    # el primero entra como siempre; los extra, solo si el texto los pide explícitamente
    reales = [r for r in elegidas if r.id != "envio_no_es_precio"]
    if reales:
        extra_ok = {r.id for r in reales[1:] if _explicito(r, c, h)}
        elegidas = [r for r in elegidas if r.id == "envio_no_es_precio" or r is reales[0] or r.id in extra_ok]
    if any(r.id not in SOLO and r.id != "envio_no_es_precio" for r in elegidas):
        elegidas = [r for r in elegidas if r.id not in SOLO]
    else:
        elegidas = elegidas[:1] + [r for r in elegidas[1:] if r.id == "envio_no_es_precio"]
    return elegidas[:MAX_DATOS + 1]


def componer(c) -> tuple[str, dict]:
    """(texto, traza). `c` trae el contexto del turno (ver `main.respaldo_codigo`)."""
    h = hechos(c)
    traza = {"reglas": [], "colisiones": {}}
    c.encabezado, c.cuerpo = False, []
    q = c.q
    # 1. encabezado: la primera que dispara
    enc = [r for r in TABLA if r.grupo == "encabezado" and r.dispara(c, h)]
    if len(enc) > 1:
        traza["colisiones"]["encabezado"] = [r.id for r in enc]
    if enc:
        r = enc[0]
        traza["reglas"].append(r.id)
        texto = r.texto(c, h)
        if r.corta:
            return texto, traza
        c.encabezado = True
        c.cuerpo.append(texto)
    # 2. datos: de la prenda y de la tienda, según la política
    todas = ([r for r in TABLA if r.grupo == "dato_prenda" and r.dispara(c, h)] if _de_la_prenda(c) else []) + \
            ([r for r in TABLA if r.grupo == "dato_tienda" and r.dispara(c, h)] if (not c.no_hay and not c.es_bot) else [])
    if len(todas) > 1:
        traza["colisiones"]["datos"] = [r.id for r in todas]
    textos = []
    for r in _datos(c, h):
        texto = r.texto(c, h)
        if texto and len(textos) < MAX_DATOS:
            traza["reglas"].append(r.id)
            textos.append(texto)
            if r.sin_pregunta:
                q = ""
        elif not texto:
            traza["reglas"].append(r.id)
    c.cuerpo.extend(textos)
    # 3. acuse
    for r in TABLA:
        if r.grupo == "acuse" and r.dispara(c, h):
            traza["reglas"].append(r.id)
            c.cuerpo.append(r.texto(c, h))
            break
    cab = c.cab
    if c.ofrecer:
        q = ""      # la pregunta será «¿Quieres ver otras opciones?»
    if cab and not c.cuerpo:
        cab = cab.rstrip(".") + " 😊"
    partes = [" ".join(x for x in [cab] + c.cuerpo[:1] if x)] + c.cuerpo[1:] + [q]
    partes = [x for x in partes if x]
    if not partes:
        p = c.plano
        sustancial = (len(p.split()) >= 4 and not c.no_hay and not c.es_bot
                      and (c.intent_crudo == "consulta_producto" or memoria.RE_NECESIDAD.search(p) or memoria.RE_BUSCA_ROPA.search(p)))
        traza["reglas"].append("sin_dato")
        partes = ["¡Claro! 😊" if c.ofrecer else
                  "Ese dato te lo confirma una asesora: escribe *4* 😊" if (h["pregunta"] and re.search(r"\w", c.mensaje))
                  else ("¡Claro! 😊\n\n" + (c.k.OFERTA if c.canal == "web" else c.k.OFERTA + " Responde *SI*")) if sustancial
                  else "¡Dale! 😊 Aquí estoy para lo que necesites."]
    return "\n\n".join(partes), traza


def tabla_md() -> str:
    filas = ["| Grupo | Regla | Dispara cuando | Efecto |", "|---|---|---|---|"]
    for r in TABLA:
        efecto = "responde solo esto" if r.corta else "sin la pregunta siguiente" if r.sin_pregunta else ""
        politica = POLITICA["datos"] if r.grupo.startswith("dato_") else POLITICA[r.grupo]
        filas.append(f"| {r.grupo} ({politica}) | `{r.id}` | {r.cuando} | {efecto} |")
    return "\n".join(filas)


if __name__ == "__main__":
    print(tabla_md())
