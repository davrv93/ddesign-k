"""Prospectos a futuro y bienvenida por anuncio (octubre de 2026, pedido de Luis, dueño de Baruka).

Prospectos: con el evento lejos (más de `desde_dias`), el bot pregunta UNA vez «¿te gustaría dejarlo listo ya, o
prefieres que lo coordinemos a inicios de <mes>?», como lo hace Luis. Si elige después, la memoria guarda
`sabemos.coordinar = "despues"`, `coordinar_en` (la fecha del aviso: evento − `aviso_dias`, o el mes que ella diga) y
`coordinar_marca` (cuándo lo decidió); el bot Go la pasa a «Más adelante» y le deja una tarea a la asesora.

Bienvenida: quien llega por un anuncio recibe presentación, foto del modelo, garantía (si la tienda la cargó como
política) y la invitación a ver colores o tallas. Todo detrás de los ajustes de la tienda; sin ellos, nada cambia.
"""
from __future__ import annotations

import datetime as dt
import re

from .. import memoria

MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre",
         "diciembre")
RE_MES = re.compile(r"\b(" + "|".join(MESES) + r"|setiembre)\b")
RE_AHORA = re.compile(r"^\s*(s[ií]+|claro|dale|ok|okey|ya|ahora|de una|ahorita|desde ya|vamos)\b|\b(lo quiero ya|ahora mismo|"
                      r"ya mismo|de una vez|separ\w*|asegur\w*|dejarlo listo|tenerlo ya|lo quiero ahora|prefiero ya)\b")
RE_DESPUES = re.compile(r"\b(despu[eé]s|m[aá]s adelante|luego|m[aá]s cerca|todav[ií]a no|a[uú]n no|por ahora no|no por ahora|"
                        r"cuando se acerque|coordin\w*|el otro mes|el pr[oó]ximo mes|mejor (en|para|a)|ma[sñ] adelante|"
                        r"m[aá]s tarde|no todav[ií]a|aun falta|todav[ií]a falta|falta (mucho|bastante))\b")


def config(req) -> dict | None:
    c = getattr(req, "prospectos", None)
    if not isinstance(c, dict):
        return None
    try:
        desde, aviso = int(c.get("desde_dias") or 0), int(c.get("aviso_dias") or 0)
    except (TypeError, ValueError):
        return None
    if desde < 1 or aviso < 1:
        return None
    return {"desde_dias": desde, "aviso_dias": aviso}


def _evento(sab: dict) -> dt.date | None:
    iso = (sab or {}).get("fecha_iso") or ""
    try:
        if len(iso) == 10:
            return dt.date.fromisoformat(iso)
        if len(iso) == 7:
            return dt.date.fromisoformat(iso + "-15")
    except ValueError:
        return None
    return None


def aviso(sab: dict, cfg: dict, hoy: dt.date) -> dt.date | None:
    """La fecha en que se le vuelve a escribir: evento − aviso_dias (nunca antes de mañana)."""
    ev = _evento(sab)
    if ev is None:
        return None
    a = ev - dt.timedelta(days=cfg["aviso_dias"])
    return a if a > hoy else None


def cuando_txt(d: dt.date) -> str:
    parte = "inicios" if d.day <= 10 else "mediados" if d.day <= 20 else "fines"
    return f"{parte} de {MESES[d.month - 1]}"


def toca_preguntar(f: dict, cfg: dict | None, hoy: dt.date, en_pago: bool) -> dt.date | None:
    """Si corresponde la pregunta, devuelve la fecha del aviso; si no, None."""
    if not cfg or en_pago:
        return None
    sab = f.get("sabemos") or {}
    if sab.get("coordinar") or "coordinar" in (f.get("preguntado") or []):
        return None
    ev = _evento(sab)
    if ev is None or (ev - hoy).days <= cfg["desde_dias"]:
        return None
    return aviso(sab, cfg, hoy)


def pregunta(f: dict, fecha_aviso: dt.date, cliente: str = "") -> str:
    f["pendiente"] = "coordinar"
    f.setdefault("preguntado", []).append("coordinar")
    nombre = (cliente or "").strip().split(" ")[0]
    coma = f", {nombre}" if nombre and not nombre.startswith("+") else ""
    return (f"¡Qué bueno que aún tienes tiempo{coma}! 😊 ¿Te gustaría dejarlo listo ya, o prefieres que lo coordinemos a "
            f"{cuando_txt(fecha_aviso)}?")


def leer_respuesta(mensaje: str) -> str | None:
    """«ahora», «despues» o None (no contestó la pregunta)."""
    t = memoria._plano(mensaje or "")
    if not t:
        return None
    despues = RE_DESPUES.search(t) or (RE_MES.search(t) and not RE_AHORA.search(t)) or memoria.niega(mensaje)
    if despues and not re.search(r"\b(lo quiero ya|ahora mismo|de una vez)\b", t):
        return "despues"
    if RE_AHORA.search(t) or memoria.afirma(mensaje):
        return "ahora"
    return None


def fecha_de_mes(mensaje: str, defecto: dt.date, evento: dt.date | None, hoy: dt.date) -> dt.date:
    """«mejor en diciembre» → el 1 de diciembre (antes del evento); sin mes, la fecha por defecto."""
    m = RE_MES.search(memoria._plano(mensaje or ""))
    if not m:
        return defecto
    nombre = "septiembre" if m.group(1) == "setiembre" else m.group(1)
    mes = MESES.index(nombre) + 1
    año = hoy.year if mes > hoy.month else hoy.year + 1
    d = dt.date(año, mes, 1)
    if d <= hoy or (evento and d >= evento):
        return defecto
    return d


def anotar(f: dict, decision: str, fecha_aviso: dt.date | None, ahora: dt.datetime) -> None:
    sab = f.setdefault("sabemos", {})
    sab["coordinar"] = decision
    if decision == "despues" and fecha_aviso:
        sab["coordinar_en"] = fecha_aviso.isoformat()
        sab["coordinar_marca"] = ahora.strftime("%Y-%m-%dT%H:%M")
    f["pendiente"] = ""


def cierre(fecha_aviso: dt.date, cliente: str = "") -> str:
    nombre = (cliente or "").strip().split(" ")[0]
    coma = f", {nombre}" if nombre and not nombre.startswith("+") else ""
    return (f"¡Perfecto{coma}! 😊 Te escribo a {cuando_txt(fecha_aviso)} para que lo separemos con calma y con stock. "
            "Mientras tanto, si quieres ver modelos o tienes alguna duda, aquí estoy 💛")


# ---------------------------------------------------------------------------
# Bienvenida por anuncio

def garantia(conoc) -> str:
    """La primera frase de la política de garantía que cargó la tienda (título o contenido con «garantía»), o ""."""
    if conoc is None:
        return ""
    for p in getattr(conoc.base, "politicas", None) or []:
        titulo, contenido = memoria._plano(p.get("titulo", "")), (p.get("contenido") or "").strip()
        if "garantia" in titulo or "garantia" in memoria._plano(contenido[:200]):
            primera = re.split(r"(?<=[.!])\s+|\n+", contenido)[0].strip(" #*-")
            return primera[:220]
    return ""


def bienvenida(cliente: str, nombre_modelo: str, garantia_txt: str) -> str:
    nombre = (cliente or "").strip().split(" ")[0]
    coma = f", {nombre}" if nombre and not nombre.startswith("+") else ""
    partes = [f"¡Hola{coma}! 😊 Te saluda Rosmary, desde ahora considérame tu asesora personal.",
              f"Te comparto el {nombre_modelo} que viste 😍"]
    if garantia_txt:
        g = garantia_txt.rstrip(".")
        partes.append(("Cuenta con garantía: " + g[0].lower() + g[1:] if not memoria._plano(g).startswith("garantia")
                       else g) + ".")
    partes.append("¿Quieres ver sus colores o las tallas disponibles?")
    return "\n\n".join([" ".join(partes[:2])] + partes[2:])


# «tallas», «ver tallas», «qué tallas hay», «¿cuáles tallas tienen?»: lo escribe la clienta (sin botones, 16-10-2026).
RE_VER_TALLAS = re.compile(r"^\W*((ver|las|que|cuales|y|en)\s+)*tallas(\s+(disponibles|hay|tienes|tienen|tiene|hay disponibles))?"
                           r"\s*[.!?]*\s*$")
