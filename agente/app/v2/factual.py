"""Compuerta factual DETERMINISTA: decide si lo que escribió un modelo se puede enviar. No usa ningún modelo.

Trabaja sobre el texto con tokens (`§PRODUCTO_8f21§`) y lo compara con el texto base, que es el que el código ya sabe correcto.
Rechaza —sin interpretar nada— si el texto:

1. pierde, duplica, cambia o inventa un token (cada token tiene que aparecer exactamente las veces que aparece en la base);
2. trae un dato fuera de un token: cifras, precios, tallas, colores, telas, prendas, fechas, plazos, descuentos, escasez, medios de
   pago, garantías… Cada familia de palabras («léxico») solo puede aparecer si ya estaba en la base: el modelo no puede
   introducir ninguna que la base no tenga;
3. nombra a alguien o algo con mayúscula que no esté en la base (nombres propios);
4. cambia la forma: otro número de párrafos, de preguntas, más frases o más emojis de los permitidos, otro idioma, o la última
   pregunta ya no es la que se quería hacer (se reconoce con el mismo detector que usa V1: `memoria.clave_de`).

Si la compuerta rechaza, se usa el texto base: siempre es correcto porque lo escribió una persona y lo rellena el código."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from .delex import TOKEN_RE, sin_tokens

EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️]")
AJENO_RE = re.compile(r"[Ѐ-ӿ֐-ۿ฀-๿぀-ヿ㐀-鿿가-힯]")
INGLES = frozenset("the and you your with for dress size price this that have are not but from will would please thanks "
                   "hello hi dear order shop store".split())
PROPIOS_PERMITIDOS = frozenset({"whatsapp", "yape", "plin"})       # solo si estaban en la base: ver _mayusculas

# Léxicos de hechos: lo que NUNCA puede aparecer si la base no lo tiene. Se comparan sin tildes y en minúscula.
_COLORES = ("negr[oa]s?|blanc[oa]s?|roj[oa]s?|azul(es)?|verdes?|amarill[oa]s?|naranjas?|morad[oa]s?|lilas?|rosad[oa]s?|rosas?|"
            "beige|celestes?|turquesas?|vino|guinda|gris(es)?|plomo|dorad[oa]s?|platead[oa]s?|marron(es)?|cafe|crema|floread[oa]s?|"
            "estampad[oa]s?|multicolor")
_TELAS = ("seda|saten|algodon|lino|gasa|crepe|licra|lycra|poliester|terciopelo|encaje|tul|chiffon|organza|jersey|lana|cuero|"
          "denim|mezclilla|viscosa|spandex|prada|roma|neopreno|tweed|escoces|fibra|tejido|tela")
_PRENDAS = ("vestid\\w*|conjunt\\w*|blus\\w*|pantal\\w*|falda\\w*|blazer\\w*|polo\\w*|jeans?|enteriz\\w*|abrigo\\w*|chaquet\\w*|"
            "sacos?|shorts?|tops?|camis\\w*|palaz+o\\w*|overol\\w*|jumpsuit|corse\\w*|bodys?|casaca\\w*|set")
_MESES = "enero|febrero|marzo|abril|mayo|junio|julio|agosto|setiembre|septiembre|octubre|noviembre|diciembre"
_DIAS = "lunes|martes|miercoles|jueves|viernes|sabado|domingo"

LEXICOS: dict[str, re.Pattern] = {
    "color": re.compile(rf"\b({_COLORES})\b"),
    "tela o material": re.compile(rf"\b({_TELAS})\b"),
    "prenda": re.compile(rf"\b({_PRENDAS})\b"),
    "talla": re.compile(r"\b(xxs|xs|xl|xxl|s|m|l)\b|\btallas?\b|\bmedidas?\b|\bcentimetros?\b|\bcm\b"),
    "fecha o plazo": re.compile(rf"\b({_MESES}|{_DIAS}|hoy|manana|ayer|semanas?|mes(es)?|anos?|dias?|horas?|minutos?|"
                                r"pronto|inmediat\w+|enseguida|ahora mismo|esta (noche|semana)|fin de semana)\b"),
    "precio": re.compile(r"\bs/|\bsoles?\b|\bdolares?\b|\busd\b|\bprecios?\b|\bcost\w+|\bcuest\w+|\bcuotas?\b|\bbarat\w+|"
                         r"\bcaro\b|\bcara\b|\beconomic\w+|\bpagar\b|\binversion\b|\btotal\b"),
    "descuento o promoción": re.compile(r"\bdescuent\w*|\bpromo\w*|\boferta\w*|\brebaj\w*|\bgratis\b|\b2x1\b|\bliquidaci\w+|\bcupon\w*|"
                                        r"\bregalo\w*|\bobsequi\w+|\bbonus\b"),
    "stock o escasez": re.compile(r"\bstock\b|\bagotad\w+|\bquedan?\b|\bquedaron\b|\bultim[oa]s?\b|\bdisponib\w+|\bunidades?\b|"
                                  r"\breserv\w+|\bapart\w+|\breposici\w+|\brestock\b|\bexclusiv\w+|\blimitad\w+|\bpocas?\b"),
    "urgencia": re.compile(r"\burgent\w*|\bcorre\b|\bapurat\w*|\brapido\b|\bsolo hoy\b|\bno te lo pierdas\b|\bantes de que se acabe\b|"
                           r"\baprovecha\w*|\bultima oportunidad\b"),
    "entrega o envío": re.compile(r"\benvi\w+|\bdelivery\b|\bdespach\w+|\bentrega\w*|\bllega\w*|\bcourier\b|\bolva\b|\bshalom\b|"
                                  r"\brecoj\w+|\bagencia\b"),
    "medio de pago": re.compile(r"\byape\b|\bplin\b|\btransferenc\w+|\btarjeta\w*|\befectivo\b|\bcomprobante\b|\bdeposit\w+|"
                                r"\bcuenta\b|\bcci\b|\bvoucher\b|\bpago\b|\bpagos\b"),
    "garantía o calidad": re.compile(r"\bgarant\w+|\bcalidad\b|\b100\s*%|\bhecho a mano\b|\bimportad\w+|\bartesanal\w*|\bunico\b|\bunica\b|"
                                     r"\bexclusiv\w+|\boriginal\w*"),
    "ubicación": re.compile(r"\bshowroom\b|\btienda fisica\b|\bsucursal\w*|\bdireccion\b|\bubicad\w+|\bsanta anita\b|\blima\b|\bprovincia\b"),
    "cita o visita": re.compile(r"\bcita\b|\bprobart\w+|\bvisita\w*|\bagend\w+"),
}


def plano(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower()) if unicodedata.category(c) != "Mn")


@dataclass
class Esperado:
    """Lo que la compuerta sabe del mensaje que se quiere enviar."""
    base: str                                    # texto base CON tokens: lo que el código ya sabe correcto
    max_frases: int = 4
    max_chars: int = 360
    max_emojis: int = 1
    clave_pregunta: str | None = None            # clave (memoria.clave_de) que debe tener la última pregunta
    extra: dict = field(default_factory=dict)


def _parrafos(t: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", (t or "").strip()) if p.strip()]


def _frases(t: str) -> int:
    t = TOKEN_RE.sub("X", t)
    return len([f for f in re.split(r"(?<=[.!?])\s+|\n+", t) if re.search(r"\w", f)])


def _preguntas(t: str) -> list[str]:
    return [m.group(0).strip() for m in re.finditer(r"[^.!?\n¿]*¿[^?]*\?|[^.!?\n]*\?", t)]


def _mayusculas(t: str) -> set[str]:
    """Palabras con mayúscula inicial que NO están al empezar una frase: nombres propios."""
    t = sin_tokens(t)
    out = set()
    for m in re.finditer(r"(?<![.!?¡¿\n])(?<!^)\b([A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,})\b", t):
        previo = t[: m.start()].rstrip()
        if not previo or previo[-1] in ".!?¡¿\n" or EMOJI_RE.match(previo[-1]):
            continue
        out.add(plano(m.group(1)))
    return out


class CompuertaFactual:
    nombre = "factual"

    def __init__(self, clave_de: Callable[[str], str] | None = None):
        self.clave_de = clave_de

    def evaluar(self, salida: str, esp: Esperado) -> dict:
        errores: list[str] = []
        s = (salida or "").replace("\r", "").strip()
        s = re.sub(r"[ \t]+\n", "\n", s)
        if len(s) < 4:
            return {"passed": False, "errors": ["salida vacía"]}

        # 1) tokens: exactamente los de la base, las mismas veces
        en_base = Counter(m.group(0) for m in TOKEN_RE.finditer(esp.base))
        en_salida = Counter(m.group(0) for m in TOKEN_RE.finditer(s))
        for t, n in en_base.items():
            if en_salida.get(t, 0) < n:
                errores.append(f"falta el dato protegido {t}")
            elif en_salida.get(t, 0) > n:
                errores.append(f"duplica el dato protegido {t}")
        for t in en_salida:
            if t not in en_base:
                errores.append(f"inventa el dato protegido {t}")
        if "§" in TOKEN_RE.sub("", s) or "{{" in s or "}}" in s:
            errores.append("restos de marcadores sin resolver")

        # 2) léxicos de hechos: nada que la base no traiga
        b_plano, s_plano = plano(sin_tokens(esp.base)), plano(sin_tokens(s))
        for nombre, rx in LEXICOS.items():
            nuevas = {m.group(0) for m in rx.finditer(s_plano)} - {m.group(0) for m in rx.finditer(b_plano)}
            if nuevas:
                errores.append(f"introduce {nombre}: {', '.join(sorted(nuevas)[:3])}")
        if re.search(r"\d", sin_tokens(s)) and not re.search(r"\d", sin_tokens(esp.base)):
            errores.append("introduce cifras")
        if re.search(r"\b[Vv]\d{2}\b", sin_tokens(s)):
            errores.append("introduce un código de producto")

        # 3) nombres propios que la base no tiene
        propios = _mayusculas(s) - _mayusculas(esp.base) - PROPIOS_PERMITIDOS
        if propios:
            errores.append(f"introduce nombres propios: {', '.join(sorted(propios)[:3])}")

        # 4) forma
        if len(_parrafos(s)) != len(_parrafos(esp.base)):
            errores.append(f"cambia el número de párrafos ({len(_parrafos(s))} ≠ {len(_parrafos(esp.base))})")
        pb, ps = _preguntas(esp.base), _preguntas(s)
        if len(ps) != len(pb):
            errores.append(f"cambia el número de preguntas ({len(ps)} ≠ {len(pb)})")
        elif ps and esp.clave_pregunta and self.clave_de is not None and self.clave_de(ps[-1]) != esp.clave_pregunta:
            errores.append(f"la última pregunta ya no pide «{esp.clave_pregunta}»")
        if _frases(s) > esp.max_frases:
            errores.append(f"demasiadas frases ({_frases(s)} > {esp.max_frases})")
        if len(EMOJI_RE.findall(s)) > max(esp.max_emojis, len(EMOJI_RE.findall(esp.base))):
            errores.append("demasiados emojis")
        if len(s) > esp.max_chars:
            errores.append(f"demasiado largo ({len(s)} > {esp.max_chars})")
        if AJENO_RE.search(s):
            errores.append("caracteres que no son español")
        if set(re.findall(r"[a-z]+", s_plano)) & INGLES:
            errores.append("palabras en inglés")
        if re.search(r"\*\*|^\s*#|^\s*[-•]\s", s, re.M):
            errores.append("formato que no es de WhatsApp")
        return {"passed": not errores, "errors": errores}
