"""Pasos del guion de la tienda (seed/speech.md) que salen de plantilla, sin LLM.

Por qué (análisis de costos del 12-10-2026): los pasos casi textuales del guion (saludo, frases por dato, fecha con
escasez, envío) son la mitad de los turnos. Con plantilla salen exactos al guion, a costo cero, y con variantes
para que no suene a robot (no repetir la misma frase en una conversación, aperturas y emojis variados).

Regla de entrada, conservadora: plantilla SOLO si el mensaje trae el dato que se esperaba y nada más (corto, sin
pregunta, sin pedir modelos ni catálogo, y no compra para otra persona). Ante la duda, None y contesta el LLM.
"""
from __future__ import annotations

import datetime as dt
import random
import re

from .. import memoria

MAX_PALABRAS = 7
RE_OTRA_PERSONA = re.compile(r"\bpara (mi|mí) (novia|esposa|enamorada|mam[aá]|madre|hija|hermana|amiga|prima|t[ií]a|"
                             r"abuela|suegra|pareja|jefa)\b|\bes un regalo\b|\bpara regalar\b", re.I)
RE_EXTRA = re.compile(r"\?|\b(pero|cu[aá]nto|precio|cuesta|tela|material|foto|ver|muestra|modelo|cat[aá]logo|"
                      r"tienes|tienen|hay|env[ií]o|delivery|yape|pago|asesora|direcci[oó]n|d[oó]nde)\b", re.I)

# --- frases del guion con variantes (la primera es la del documento) -----------------------------------------
TALLA = (
    "¡Perfecto, sí lo tenemos aún disponible en tu talla! 😊 Te comento que nuestras tallas son completas y son medidas peruanas.",
    "¡Genial, tu talla sí la tenemos disponible! 🥰 Nuestras tallas son completas y en medidas peruanas.",
    "¡Qué bien, sí hay en tu talla! Te cuento que trabajamos tallas completas, en medidas peruanas ✨",
)
ESTATURA = (
    "Excelente, sí está diseñado para tu estatura. Si en caso te queda un poquito larguito, lo puedes ajustar con tu modista de confianza. ¡Es súper sencillo!",
    "¡Perfecto para tu estatura! Y si te queda un poquito larguito, tu modista de confianza lo ajusta en un ratito, es súper sencillo 😊",
    "Te va a quedar lindo con tu estatura 🥰 Si en caso queda un poquito largo, es un ajuste muy sencillo con tu modista de confianza.",
)
NOCHE = (
    "Perfecto 😊 Tengo colores vibrantes y sobrios. ¿Cómo quieres sentirte ese día? ¿Prefieres llamar un poco más la atención, o buscas verte elegante, sofisticada y más discreta?",
    "¡De noche, qué lindo! ✨ Tengo opciones en colores vibrantes y también sobrios. ¿Cómo te quieres sentir ese día: llamar un poco más la atención o verte elegante, sofisticada y discreta?",
)
DIA = (
    "Perfecto 😊 Al ser un evento de día, serían ideales opciones que se vean frescas, elegantes y que armonicen. Cuéntame, ¿en qué color estás pensando?",
    "¡De día, me encanta! 🌸 Lo ideal son opciones frescas, elegantes y que armonicen. ¿En qué color estás pensando?",
)
# Preguntas que faltan del paso 3 (talla, día/noche, estatura), en el orden del guion.
PREGUNTA = {
    "talla": ("¿En qué talla te encuentras?", "¿Qué talla usas?", "Cuéntame, ¿en qué talla te encuentras?"),
    "horario": ("¿El evento es de día o de noche?", "¿Tu evento es de día o de noche?", "Y el evento, ¿es de día o de noche?"),
    "estatura": ("¿Cuánto mides de estatura?", "¿Cuánto mides, más o menos?", "Para el largo, ¿cuánto mides de estatura?"),
}
FECHA = ("¡Una consulta importante!, ¿para cuándo lo necesitas?", "Una consulta importante: ¿para cuándo lo necesitas?",
         "¿Y para cuándo lo necesitas? Es importante para organizarnos 😊")
PRONTO = (
    "Wooow, estamos muy pronto. Lo ideal es tenerlo antes, para que puedas tomar tus precauciones y todo salga perfecto. Recuerda que mujer precavida vale por 1000 🤭\nSería genial tenerlo listo antes de tiempo mientras dispongo de stock; si en caso necesitas ajustes o cambio, tranquilamente podemos resolverlo. ¿No lo consideras?",
    "¡Uy, estamos muy pronto! 🤭 Lo ideal es tenerlo antes para que tomes tus precauciones y todo salga perfecto; mujer precavida vale por 1000.\nSería genial dejarlo listo ya, mientras dispongo de stock, y si necesitas algún ajuste o cambio lo resolvemos con calma. ¿No lo consideras?",
)
# Escasez 1 (la fecha es la otra semana), textual del guion de la tienda (tabla del 12-10-2026).
ESCASEZ_1 = (
    "Sería genial tenerlo listo antes de tiempo mientras dispongo de stock; si en caso necesitas ajustes o cambio, tranquilamente podemos resolverlo. ¿No lo consideras?",
    "Sería genial tenerlo listo antes de tiempo, mientras dispongo de stock; y si necesitas algún ajuste o cambio, tranquilamente lo resolvemos. ¿No lo consideras?",
)
# El mensaje de escasez va UNA sola vez por conversación (pedido del usuario, 12-10-2026: tras «Sí, tienes razón» se repetía).
# Las frases de urgencia del guion y las que el modelo arma con otras palabras («mientras aún hay stock», «tenerlo listo
# antes»): la urgencia va una vez por conversación (15-10-2026, prueba local: el modelo la repitió dos turnos después).
RE_ESCASEZ = re.compile(r"dispongo de stock|mujer precavida|estamos muy pronto|(mientras|ahora que) (a[uú]n |todav[ií]a )?"
                        r"(hay|haya|tengo|tenga|tenemos|tengamos|queda|quede|dispongo|disponga)\b[^.?!]{0,20}stock|"
                        r"tener(lo|la)? (el vestido |la prenda |el modelo )?listo antes|antes de que se agote", re.I)


def ya_hubo_escasez(historial) -> bool:
    return any(RE_ESCASEZ.search(b) for b in _bots(historial))


CON_TIEMPO = (
    "Sería genial tenerlo listo antes de tiempo mientras dispongo de stock. Recuerda que estos últimos meses del año son pesados; si en caso necesitas ajustes o cambio, tranquilamente podemos resolverlo. ¿No lo consideras?",
    "¡Qué bueno que tienes tiempo! Aun así, sería genial dejarlo listo mientras dispongo de stock: estos últimos meses del año son pesados, y si necesitas un ajuste o cambio lo vemos con calma. ¿No lo consideras?",
)
ENVIO = {
    "lima": ("Excelente, el envío cuesta S/ 15 a través de Olva Courier y la entrega es a tu dirección. ¿Te parece bien?",
             "¡Perfecto! A Lima el envío cuesta S/ 15 por Olva Courier y te llega a tu dirección 🚚 ¿Te parece bien?"),
    "provincia": ("Excelente, el envío cuesta S/ 20 a través de Olva Courier o Shalom; el envío llega hasta la agencia. ¿Cuál de ellas prefieres?",
                  "¡Genial! A provincia el envío cuesta S/ 20 por Olva Courier o Shalom, hasta la agencia de tu ciudad 🚚 ¿Cuál prefieres?"),
}
RE_TOQUE = re.compile(r"^\s*(hola+|holi|hey|al[oó]|buenas)?\s*[?¿!.]+\s*$|^\s*(hola+|holi|hey|al[oó])\s*$", re.I)
RE_OK = re.compile(r"^\s*(ok+|okey|oki|ya|bien|dale|listo|perfecto|est[aá] bien|de acuerdo|ah ya|okk)\s*[!.]*\s*$", re.I)
# Toda respuesta termina en pregunta (seed/speech.md). 12-10-2026, real: «hola» tras unas horas → «¡Hola de nuevo! Aquí
# estoy para ayudarte 😊» y la charla quedaba sin rumbo.
TOQUE = ("¡Aquí estoy! 😊 ¿En qué te ayudo?", "Sí, aquí sigo 🥰 ¿Qué estás buscando hoy?",
         "¡Hola de nuevo! 😊 ¿En qué te puedo ayudar hoy?")
OK_PREFIJO = ("¡Listo! ", "Dale 😊 ", "Genial. ", "¡Bien! ")
SALUDO = ("¡Hola{nombre}! 😊 Te saluda Rosmary, desde ahora considérame tu asesora personal. Cuéntame, ¿qué evento se te aproxima?",
          "¡Hola{nombre}! 🥰 Te saluda Rosmary, desde ahora considérame tu asesora personal. ¿Qué evento se te aproxima?")


def _bots(historial) -> list[str]:
    out = []
    for t in historial or []:
        rol = t.get("rol") if isinstance(t, dict) else getattr(t, "rol", "")
        if rol != "cliente":
            out.append(t.get("texto", "") if isinstance(t, dict) else getattr(t, "texto", ""))
    return out


def _clientes(historial) -> list[str]:
    return [(t.get("texto", "") if isinstance(t, dict) else getattr(t, "texto", "")) for t in historial or []
            if (t.get("rol") if isinstance(t, dict) else getattr(t, "rol", "")) == "cliente"]


def _una(opciones, historial) -> str:
    """Una variante que no se haya dicho en esta conversación; entre las libres, al azar."""
    dichas = " ".join(memoria._plano(b) for b in _bots(historial))
    libres = [o for o in opciones if memoria._plano(o.split("\n")[0])[:40] not in dichas]
    return random.choice(libres or list(opciones))


def responder(f: dict, datos: dict, mensaje: str, historial, cliente: str = "") -> str | None:
    """El texto del paso del guion si este mensaje es «el dato esperado y nada más»; si no, None (contesta el LLM).
    Actualiza `f` (preguntado, pendiente) como lo haría el turno del LLM."""
    m = (mensaje or "").strip()
    if not m or RE_EXTRA.search(m) or len(m.split()) > MAX_PALABRAS:
        return None
    hist_cli = " ".join(_clientes(historial)) + " " + m
    if RE_OTRA_PERSONA.search(hist_cli) or (f.get("sabemos") or {}).get("para_quien"):   # para otra persona: lo adapta el LLM
        return None
    sab = f.setdefault("sabemos", {})
    datos = {k: v for k, v in (datos or {}).items() if v}
    utiles = {k for k in datos if k in ("talla", "estatura", "horario", "fecha", "fecha_iso", "envio", "ciudad", "prenda",
                                        "ocasion")}
    if not utiles:
        return None

    # Envío (paso 7): Lima o provincia.
    if datos.get("envio") in ENVIO and set(utiles) <= {"envio", "ciudad"}:
        _marca(f, "lima_o_provincia")
        return _una(ENVIO[datos["envio"]], historial)

    # Fecha (paso 6): pronto (≤ 14 días) o con tiempo, con la escasez del guion.
    if ("fecha" in datos or "fecha_iso" in datos) and sab.get("ocasion") and set(utiles) <= {"fecha", "fecha_iso"}:
        dias = None
        try:
            dias = (dt.date.fromisoformat(sab.get("fecha_iso") or datos.get("fecha_iso")) - memoria.ahora_lima().date()).days
        except (TypeError, ValueError):
            pass
        _marca(f, "fecha")
        if ya_hubo_escasez(historial):
            return None                            # ya se dijo: no se repite
        # Según cuánto falte (guion): muy pronto (≤ 7 días) · la otra semana (8–14) · con tiempo (más).
        if dias is not None and dias <= 7:
            return _una(PRONTO, historial)
        if dias is not None and dias <= 14:
            return _una(ESCASEZ_1, historial)
        return _una(CON_TIEMPO, historial)

    # Datos del paso 3/4 (talla S/M/L, estatura, día o noche), con el evento ya contado.
    if not sab.get("ocasion") or not (set(utiles) & {"talla", "estatura", "horario"}) \
            or not set(utiles) <= {"talla", "estatura", "horario", "prenda"}:
        return None
    if datos.get("talla") and datos["talla"].upper() not in ("S", "M", "L"):
        return None                               # XL y otras: el LLM explica qué tallas hay
    partes = []
    if datos.get("talla"):
        partes.append(_una(TALLA, historial))
    if datos.get("estatura"):
        partes.append(_una(ESTATURA, historial))
    if datos.get("horario") == "noche":           # trae su propia pregunta: no se suma otra
        partes.append(_una(NOCHE, historial))
        return "\n".join(partes)
    if datos.get("horario") == "dia":
        partes.append(_una(DIA, historial))
        return "\n".join(partes)
    faltan = [k for k in ("talla", "horario", "estatura") if not sab.get(k)]
    if faltan:
        k = faltan[0]
        partes.append(_una(PREGUNTA[k], historial))
        _marca(f, k)
    elif not (sab.get("fecha") or sab.get("fecha_iso")):
        partes.append(_una(FECHA, historial))
        _marca(f, "fecha")
    else:
        return None
    return " ".join(partes) if len(partes) == 1 else "\n".join(partes)


def toque(mensaje: str, historial) -> str | None:
    """«hola?», «??» después de que ya se presentó: no se repite la pregunta, se contesta como una persona."""
    if not RE_TOQUE.match(mensaje or "") or "rosmary" not in " ".join(memoria._plano(b) for b in _bots(historial)):
        return None
    return _una(TOQUE, historial)


def ok_sin_dato(f: dict, mensaje: str, historial) -> str | None:
    """«ok», «ya», «dale» sin dato: la siguiente pregunta del guion que NO se haya hecho ya dos veces (no insistir)."""
    if not RE_OK.match(mensaje or "") or not (f.get("sabemos") or {}).get("ocasion"):
        return None
    sab = f["sabemos"]
    ult3 = [memoria._plano(b) for b in _bots(historial)[-3:]]
    pat = {"talla": "talla", "horario": "dia o de noche", "estatura": "estatura|mides", "fecha": "para cuando"}
    for k in ("talla", "horario", "estatura", "fecha"):
        if sab.get(k) or (k == "fecha" and sab.get("fecha_iso")):
            continue
        if sum(1 for b in ult3 if re.search(pat[k], b)) >= 2:
            continue
        _marca(f, k)
        return random.choice(OK_PREFIJO) + _una(FECHA if k == "fecha" else PREGUNTA[k], historial)
    return None


def saludo(cliente: str, historial) -> str:
    nombre = (cliente or "").strip().split(" ")[0]
    return _una(SALUDO, historial).format(nombre=f", {nombre}" if nombre else "")


def _marca(f: dict, clave: str) -> None:
    f["pendiente"] = clave if clave in memoria.PENDIENTES else f.get("pendiente", "")
    pre = f.setdefault("preguntado", [])
    if clave not in pre:
        pre.append(clave)
