"""Lo que la clienta PIDE en un mensaje: una sola interpretación, para todo el agente.

Antes la misma idea («pide un color», «pide otras opciones», «pide cita») estaba escrita en 7 sitios de `main.py` y `memoria.py`
y cada arreglo había que repetirlo (06-10: dos sesiones arreglaron «algún vestido rojo?» por separado). Aquí viven las expresiones y
las funciones PURAS de texto: no leen el stock, ni la ficha de nadie, ni la conversación; eso lo decide cada llamante con los hechos.

`interpretar(mensaje)` devuelve un `SolicitudCliente` (cacheado por texto). Es un contrato: lo leen V1 (`main.py`) y V2 (la traza lo
lleva en `solicitud`), y mañana lo podrá llenar también un clasificador semántico o un modelo pequeño sin tocar a quien lo lee.

Sin cambio de comportamiento: las reglas son EXACTAMENTE las que estaban en `main.py` (movidas, no reescritas). La regresión y la
reproducción de los 613 mensajes del 06-10 deben salir idénticas.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from functools import lru_cache

from . import memoria


def _sin_tildes(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


# ---------------------------------------------------------------------------
# Color

def color_pedido(texto: str) -> str:
    """La raíz del color que pide («azul», «roj», «ros»…), '' si no pide ninguno. «palo rosa» y «rosado» son «ros»."""
    m = memoria.RE_COLOR.search(memoria._plano(texto or ""))
    if not m:
        return ""
    c = m.group(1)
    return "ros" if "ros" in c else c[:3]


def _raiz_color(color: str) -> str:
    """«negra» y «negro» → «negr»; «roja» y «rojo» → «roj»; «rosado» y «palo rosa» → «ros».
    Con «> 4» «roja» se quedaba entera y no casaba con el «rojo» de la ficha: «¿tienes blusas rojas?» → «no tengo» (06-10)."""
    c = memoria._plano(color or "")
    if "ros" in c:
        return "ros"
    return c[:-1] if len(c) > 3 and c[-1] in "oa" else c


def _color_txt(color: str) -> str:
    """El color para decirlo tras «en»: «blusas en rojo», no «en roja» (se escribe como lo dijo la clienta)."""
    c = memoria._plano(color or "")
    return c[:-1] + "o" if c.endswith("a") and c[:-1] + "o" in memoria.COLORES else c


def _de_color(f, raiz: str) -> bool:
    """La prenda es de ese color según su ficha (el campo color; «blazer» no es «blanco»)."""
    return bool(raiz) and any(w.startswith(raiz) for w in re.findall(r"[a-zñ]+", _sin_tildes(f.color or "")))


def color_dicho(texto: str) -> str:
    """El color que pide, con su nombre («rojo»); '' si no pide ninguno o lo descarta («rojo no»)."""
    t = memoria._plano(texto or "")
    m = memoria.RE_COLOR.search(t)
    if not m:
        return ""
    c = m.group(1)
    if re.search(rf"\b{c}\w*\s+no\b|\bno\s+(quiero\s+|me gusta\s+)?(el\s+|en\s+|nada\s+)?{c}", t):
        return ""
    return c


# Un color cuenta si lo está pidiendo («¿tienen en rojo?», «busco uno verde», «¿y en azul?»), no si lo comenta
# («¿combina con zapatos dorados?»).
RE_PIDE_COLOR_VERBO = re.compile(r"\b(tien\w+|hay|busc\w+|quier\w+|quisiera|necesit\w+|tendr\w+|vend\w+|manej\w+|vienen?)\b|^\W*(y\s+)?en\s")
# Sin verbo también lo pide si justo antes del color va una prenda o «algún/uno/otro»: «¿algún vestido rojo?», «vestido
# rojo?», «uno rojo», «algo en rojo», «¿y rojo?». «¿Combina con una cartera roja?» no: el color va con algo que no vendemos.
_PRENDA_ANTES = r"(?:[vb]estid|conjunt|blus|fald|pantal|blazer|enteriz|polo|jean|modelo|opcion|prenda)\w*\s+"
RE_ANTES_DEL_COLOR = re.compile(rf"(?:\b(?:alg\w*|un[oa]s?|otr[oa]s?)\s+(?:{_PRENDA_ANTES})?|\b{_PRENDA_ANTES}|^\W*(?:y\s+)?)"
                                r"(?:en\s+|de\s+color\s+|color\s+)?$")


def color_que_pide(texto: str) -> str:
    """El color que PIDE («rojo»), con verbo o sin él; '' si no nombra ninguno, lo descarta («rojo no») o solo lo comenta.
    Caso real (web, 06-10): «algun vestido rojo ?» no traía verbo, no contaba como pedido y salían tres vestidos de otros
    colores en vez de «no tengo vestidos en rojo»."""
    color = color_dicho(texto)
    if not color:
        return ""
    t = memoria._plano(texto)
    m = memoria.RE_COLOR.search(t)
    sin_verbo = bool(m and RE_ANTES_DEL_COLOR.search(t[:m.start()]))
    return color if (RE_PIDE_COLOR_VERBO.search(t) or sin_verbo) else ""


def pide_otro_color(foco, texto: str) -> bool:
    """«yo quiero uno rojo» con el Kendall (negro) en foco: pide otro color, no confirma ese vestido. Sin ficha de color no se asume."""
    col = color_pedido(texto)
    return bool(col and getattr(foco, "color", "") and not _de_color(foco, _raiz_color(color_dicho(texto) or col)))


# ---------------------------------------------------------------------------
# «Otras opciones» y catálogo

# Pedir más opciones de forma explícita.
RE_MAS_OPCIONES = re.compile(r"\b(otr[oa]s? (modelos?|opci|vestid|colou?r|prendas?|conjunt|blus|fald|blazer|pantal|cosas|dise|tipos?|estilos?)"
                             r"|^\W*(y\s+)?otr[oa]s?\W*$|(tienes|tienen|hay|ten[eé]s|muestr\w*|pas\w*|ens[eé][nñ]\w*) (algun(os|as)? )?otr[oa]s?\b"
                             r"|m[aá]s (modelos|opciones|colores)|qu[eé] m[aá]s|alternativa|parecid|"
                             r"diferente|distint|mu[eé]str|ens[eé][nñ]|ver (los|m[aá]s|otr))\w*", re.I)
RE_CATALOGO = re.compile(r"\bcat[aá]logo|\b(ver|mu[eé]str[ae]me|ens[eé][nñ][ae]me)\s+(los|tus|sus|todos los)\s+modelos\b"
                         # «¿qué hay de nuevo?», «solo dime qué hay»: quiere ver qué tienen, no «otras opciones» (06-10: contestado con «escribe *4*»)
                         r"|\bqu[eé] hay de nuevo\b|\bnovedades\b|\blo nuevo\b|\bqu[eé] (hay|tienen|tienes) (nuevo|ahora|disponible)\b"
                         r"|\bsolo dime (nom[aá]s )?qu[eé] (hay|tienen|tienes)\b", re.I)
RE_OTRAS = re.compile(r"\b(otr[oa]s?|m[aá]s|parecid|alternativ|diferente|distint)", re.I)
RE_MAS_BARATO = re.compile(r"\bmas (barat|economic|comod|bajo)\w*|\bmenos precio\b|\balgo (barat|economic)\w*|\bde menor precio\b")
RE_NO_OTRO = re.compile(r"\bno (quiero|busco|necesito|me interesa|deseo|quisiera)\b[^.?!]{0,20}\botr[oa]s?\b|\bno (x|por|para)\s+otr[oa]s?\b|\bsolo (quiero|me interesa|busco)\b[^.?!]{0,12}\b(el|la|ese|esa|este|esta)\b")
RE_COMPARA_TIENDA = re.compile(r"\b(gamarra|mesa redonda|otra tienda|otras tiendas|en otro lado|en otros lados|por internet|shein|saga|ripley)\b")
RE_PIDE_EXPLICITO = re.compile(r"\b(muestr|ensen|pasame|mandame|enviame|otras? opcion|otros? modelo|ver (otr|mas))\w*")
RE_BUSCA_CAMBIO = re.compile(r"\b(busco|quiero|necesito|prefiero|mejor|no me sirve|no me gusta)\b")
# Pide rebaja o descuento: «¿me lo dejas en 250?», «¿hay descuento si llevo dos?». El descuento NO lo da el bot: lo confirma una asesora.
# («promoción» y «oferta» NO: «la gala de mi promoción» es la clienta, no el precio.)
RE_REBAJA = re.compile(r"\b(descuent\w*|rebaj\w*|precio especial)\b"
                       r"|\b(me lo|lo|me la|la) (dejas|dejan|das|dan|rebajas|bajas)\b|\b(dejas|dejan|das|dan)\b[^.?!]{0,12}\ben \d{2,4}\b"
                       r"|\b(baja|bajas|bajar|bajen)\w* (el )?precio\b")
# Alaba la prenda: «me encantó el Pandora», «qué lindo». No pide nada: se agradece y se sigue.
RE_ELOGIO = re.compile(r"\bme (encant|gust|fascin|enamor)\w*|\b(que|qué) (lindo|linda|bonito|bonita|hermoso|hermosa|precioso|preciosa)\b"
                       r"|\best[aá] (lindo|linda|bonito|bonita|hermoso|hermosa)\b|\bse ve (lindo|linda|bonito|bonita|hermoso|hermosa|precioso|preciosa)\b", re.I)
# Avisa que va a pagar: «ahorita te paso el comprobante», «voy a yapear». Con el pedido confirmado se le espera, no se le deja sin respuesta.
RE_AVISA_PAGO = re.compile(r"\b(ahorita|ya|en un rato|en un momento) te (paso|mando|envio)\b[^.?!]{0,25}\b(comprobante|captura|voucher|foto)\b"
                           r"|\bvoy a (yapear|pagar|depositar|transferir|hacer (el|mi) (pago|yape|deposito|transferencia))\b"
                           r"|\bya (voy a )?(yapeo|deposito|transfiero)\b|\bdame un (toque|momento|momentito|ratito|segundito)\b[^.?!]{0,25}\b(yape|pag|deposit|transfer)")


# ---------------------------------------------------------------------------
# El contrato

@dataclass(frozen=True)
class SolicitudCliente:
    """Lo que dice el TEXTO del mensaje, sin mirar la conversación. Quien decide qué hacer (stock, etapa, foco) lo combina con sus hechos."""
    mensaje: str
    # Color
    color: str = ""             # el color nombrado («rojo»); '' si ninguno o lo descarta («rojo no»)
    pide_color: bool = False    # lo está PIDIENDO («¿tienen en rojo?», «algún vestido rojo?»), no solo lo comenta
    color_raiz: str = ""        # la raíz para compararlo con la ficha («roj»)
    # Cita
    pide_cita: bool = False     # quiere ir / probarse / agendar («¿puedo ir mañana a las 10?»)
    # Otras opciones y catálogo
    mas_opciones: bool = False  # lo dice explícito: «otras opciones», «muéstrame», «alternativas»
    mas_barato: bool = False    # «algo más barato»
    catalogo: bool = False      # «muéstrame tu catálogo» (no son «otras opciones»)
    no_otro: bool = False       # «no quiero otro vestido, quiero el Holly»
    regatea_comparando: bool = False   # «en Gamarra encuentro parecidos» sin pedir ver nada
    busca_cambio: bool = False  # «busco / quiero / prefiero…»: puede estar cambiando de prenda
    no_mostrar: bool = False    # «no me muestres nada todavía»
    # Dinero y trato
    pide_rebaja: bool = False   # «¿me lo dejas en 250?», «¿hay descuento?»
    elogia: bool = False        # «me encantó el Pandora» (no pregunta nada)
    avisa_pago: bool = False    # «ahorita te paso el comprobante»

    def a_dict(self) -> dict:
        d = asdict(self)
        d.pop("mensaje", None)
        return {k: v for k, v in d.items() if v}      # solo lo que pide: la traza no se llena de falsos


@lru_cache(maxsize=2048)
def interpretar(mensaje: str) -> SolicitudCliente:
    """Una sola lectura del texto. Pura y cacheada: llamarla cien veces por turno cuesta lo mismo que una."""
    t = memoria._plano(mensaje or "")
    color = color_dicho(mensaje or "")
    pide_color = bool(color_que_pide(mensaje or ""))
    return SolicitudCliente(
        mensaje=mensaje or "",
        color=color, pide_color=pide_color, color_raiz=_raiz_color(color) if color else "",
        pide_cita=bool(memoria.RE_CITA.search(t)),
        mas_opciones=bool(RE_MAS_OPCIONES.search(mensaje or "")),
        mas_barato=bool(RE_MAS_BARATO.search(t)),
        catalogo=bool(RE_CATALOGO.search(mensaje or "") and not RE_OTRAS.search(mensaje or "")),
        no_otro=bool(RE_NO_OTRO.search(t)),
        regatea_comparando=bool(RE_COMPARA_TIENDA.search(t) and not RE_PIDE_EXPLICITO.search(t)),
        busca_cambio=bool(RE_BUSCA_CAMBIO.search(t)),
        no_mostrar=bool(memoria.no_mostrar(mensaje or "")),
        pide_rebaja=bool(RE_REBAJA.search(t)),
        elogia=bool(RE_ELOGIO.search(mensaje or "")),
        avisa_pago=bool(RE_AVISA_PAGO.search(t)),
    )
