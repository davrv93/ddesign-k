"""Precios solo si la clienta los pregunta (15-10-2026, configuración de la tienda en JMD Ventas → Ajustes).

Con `precios_solo_si_pregunta` en la petición, el robot no dice precios de prendas por su cuenta: ni en el texto, ni en el
pie o el título de las fotos. Los dice cuando ella pregunta el precio de forma explícita («¿cuánto cuesta?», «¿precio?»,
«¿a cuánto está?») y en el cierre de la compra (resumen, total, pago), donde el precio es parte del pedido. Una promo se
puede mencionar («20 % en vestidos»), sin cifras en soles. El costo del envío (S/ 15, S/ 20) no es precio de prenda.

Capas (como el resto del agente):
- `pregunta_precio(texto)`: lo que cuenta como pregunta explícita.
- `permitido(req, mensaje, ...)`: si en este turno se pueden decir precios.
- `quitar_de_texto` y `quitar_de_pie`: la red de salida (texto, pie y título de las fotos), que deja la frase legible.
Sin la configuración, nada cambia.
"""
from __future__ import annotations

import re

from . import memoria

# Pregunta explícita por el precio. «¿tienen promo?» o «¿hay descuento?» NO lo son: ahí se dice la promo sin cifras.
RE_PREGUNTA_PRECIO = re.compile(
    r"\b(precios?|cu[aá]nto\s+(cuesta|cuestan|sale|salen|esta|estan|vale|valen|es|son|seria|serian|cobran|queda|quedaria|"
    r"me sale|me saldria|me cuesta|me cobran|el total|seria el total|en total)|a\s+cu[aá]nto|valor|valen|cuesta|cuestan|"
    r"costo|costar[ií]a|tarifa|cu[aá]nto\s+ser[ií]a|cu[aá]nto\s*\??\s*$)", re.I)
# Montos en soles en el texto («S/ 290», «S/290.00», «*S/ 232.00*», «~S/ 290.00~»).
RE_MONTO = re.compile(r"[*~]?S/\.?\s*(\d{1,5}(?:[.,]\d{1,2})?)[*~]?")
ESTADOS_COMPRA = ("esperando_talla", "esperando_confirmacion", "esperando_pago", "esperando_ubicacion")


def pregunta_precio(texto: str) -> bool:
    return bool(RE_PREGUNTA_PRECIO.search(memoria._plano(texto or "")))


def permitido(activo: bool, mensaje: str, estado: str = "", etapa: str = "", pedido: dict | None = None,
              pago_ok: bool = False) -> bool:
    """¿Se pueden decir precios de prendas en este turno? Siempre, si la tienda no activó la configuración."""
    if not activo:
        return True
    if pregunta_precio(mensaje) or pago_ok or etapa == "venta_confirmada" or (estado or "") in ESTADOS_COMPRA:
        return True
    return bool((pedido or {}).get("codigo") and (pedido or {}).get("talla"))


def _num(s: str) -> float:
    return round(float(s.replace(",", ".")), 2)


# Dónde empieza otra frase: tras . ! ? …, antes de una pregunta «¿…» y tras un emoji seguido de mayúscula.
RE_CORTE = re.compile(r"(?<=[.!?…])\s+|\s+(?=¿)|(?<=[\U0001F300-\U0001FAFF\u2600-\u27BF])\s+(?=[A-ZÁÉÍÓÚÑ¡¿])")


def _con_monto(texto: str, env: set) -> bool:
    return any(_num(x) not in env for x in RE_MONTO.findall(texto))


def quitar_de_texto(texto: str, envios=(15.0, 20.0)) -> str:
    """Red de salida: quita las partes de frase que dan un precio de prenda (los montos de envío se quedan). Va por
    tramos («…, …; …: …»): un tramo con precio se va entero, así no quedan frases cojas («El Vestido Salome cuesta.»).
    Una frase que se queda sin nada se va. Es el último recurso: antes, V3 regenera la respuesta con la política."""
    if not texto or "S/" not in texto:
        return texto
    env = {round(float(e), 2) for e in envios}
    # paréntesis con montos: «(antes S/ 290)», «(con la promo, S/ 232.00)»
    t = re.sub(r"\s*\([^()]*S/[^()]*\)", lambda m: "" if _con_monto(m.group(0), env) else m.group(0), texto)
    out = []
    for linea in t.split("\n"):
        frases = []
        for f in RE_CORTE.split(linea):
            if not _con_monto(f, env):
                frases.append(f)
                continue
            tramos = re.split(r"(\s*[;,:—–]\s*)", f)
            quedan = [x for i, x in enumerate(tramos) if i % 2 == 0 and x.strip() and not _con_monto(x, env)]
            if not quedan:
                continue
            g = ", ".join(x.strip(" ,;:") for x in quedan)
            fin = re.search(r"[.!?…]+[\W]*$", f)
            if fin and not re.search(r"[.!?…][\W]*$", g):
                g += fin.group(0).strip()
            if not re.search(r"[.!?…:][\W]*$", g):
                g += "."
            g = g[:1].upper() + g[1:]
            if len(re.findall(r"\w+", g)) >= 2:
                frases.append(g)
        out.append(" ".join(frases))
    r = "\n".join(out).strip()
    r = re.sub(r"\n{3,}", "\n\n", r)
    return r or texto


def quitar_de_pie(pie: str) -> str:
    """La primera línea del pie o del título sin el precio: «*Vestido Salome* — *S/ 290.00*» → «*Vestido Salome*»; con
    promo, «*Vestido Salome* — ~S/ 290.00~ *S/ 232.00* con la promo «X»» → «*Vestido Salome* · en promo «X» 🎉»."""
    if not pie or "S/" not in pie:
        return pie
    primera, sep, resto = pie.partition("\n")
    m = re.search(r"con la promo («[^»]+»)", primera)
    if m:
        primera = re.sub(r"\s*[—–-]\s*.*$", f" · en promo {m.group(1)} 🎉", primera)
    else:
        primera = re.sub(r"\s*[—–-]\s*[*~]?S/[^\n]*$", "", primera)
        primera = re.sub(r"\s*·\s*(?:2 por|envío gratis)[^\n]*$", "", primera) if "S/" in primera else primera
    out = primera + (sep + resto if sep else "")
    # Otras líneas del pie con precio («Agotado…», «Hay en…» no llevan; la del precio va en la primera).
    return "\n".join(l for l in out.split("\n") if not re.match(r"^\s*💰", l))


def aplicar(res: dict, envios=(15.0, 20.0)) -> dict:
    """Saca los precios de la respuesta (texto, pies y títulos). Los campos internos (tallas[].precio) no se tocan."""
    if not isinstance(res, dict):
        return res
    if isinstance(res.get("respuesta"), str):
        res["respuesta"] = quitar_de_texto(res["respuesta"], envios)
    for s in res.get("sugerencias") or []:
        if isinstance(s, dict):
            for k in ("pie", "titulo"):
                if isinstance(s.get(k), str):
                    s[k] = quitar_de_pie(s[k])
    res["precios_ocultos"] = True
    return res


NOTA_SISTEMA = ("POLÍTICA DE PRECIOS DE LA TIENDA: en este mensaje ella NO preguntó el precio. NO escribas ningún precio de "
                "prenda (nada de «S/»), aunque más arriba diga «con su precio» o te dé cifras: los precios se dan solo cuando "
                "ella los pregunta. Sí puedes decir que hay promo y su porcentaje («20 % en vestidos»). El costo del envío sí "
                "lo puedes decir.")
