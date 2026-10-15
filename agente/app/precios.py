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


def _con_monto(texto: str, env: set, solo: set | None = None) -> bool:
    return any(_num(x) not in env and (solo is None or _num(x) in solo) for x in RE_MONTO.findall(texto))


def quitar_de_texto(texto: str, envios=(15.0, 20.0), solo: set | None = None) -> str:
    """Red de salida: quita las partes de frase que dan un precio de prenda (los montos de envío se quedan). Va por
    tramos («…, …; …: …»): un tramo con precio se va entero, así no quedan frases cojas («El Vestido Salome cuesta.»).
    Una frase que se queda sin nada se va. Es el último recurso: antes, V3 regenera la respuesta con la política.
    `solo`: quita únicamente esos montos (el precio de un modelo que ya se le dijo); None = todos los de prenda."""
    if not texto or "S/" not in texto:
        return texto
    env = {round(float(e), 2) for e in envios}
    # paréntesis con montos: «(antes S/ 290)», «(con la promo, S/ 232.00)»
    t = re.sub(r"\s*\([^()]*S/[^()]*\)", lambda m: "" if _con_monto(m.group(0), env, solo) else m.group(0), texto)
    out = []
    for linea in t.split("\n"):
        frases = []
        for f in RE_CORTE.split(linea):
            if not _con_monto(f, env, solo):
                frases.append(f)
                continue
            tramos = re.split(r"(\s*[;,:—–]\s*)", f)
            quedan = [x for i, x in enumerate(tramos) if i % 2 == 0 and x.strip() and not _con_monto(x, env, solo)]
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


# ---------------------------------------------------------------------------
# El precio de un modelo se dice UNA vez (15-10-2026, pedido de la tienda: «si ya le informamos el precio y la clienta
# cambia de color del mismo modelo, no hace falta decir el precio a cada rato»). Va siempre, con o sin la configuración
# de arriba. La memoria guarda `precios_dichos` (códigos cuyo precio ya se le dio); el precio se repite solo si ella lo
# pregunta o en el cierre de la compra (resumen, total, pago).

def montos(ficha, conoc=None) -> set:
    """Los montos que son «el precio» de una prenda: el normal y, si una promo vigente la cubre, el de la promo."""
    from . import conocimiento
    p = getattr(ficha, "precio", None)
    if not isinstance(p, (int, float)) or isinstance(p, bool) or p <= 0:
        return set()
    out = {round(float(p), 2)}
    for promo in conocimiento.promos_para(conoc, ficha) if conoc is not None else []:
        fin = conocimiento.precio_promo(promo, p)
        for k in ("precio_final", "precio_por_dos"):
            if isinstance(fin.get(k), (int, float)):
                out.add(round(float(fin[k]), 2))
    return out


def relevantes(mem: dict, res: dict | None = None) -> list[str]:
    """Códigos de los que se puede estar hablando: en foco, en el pedido, los últimos mostrados y los de las fotos."""
    cods = [mem.get("producto") or "", ((mem.get("pedido") or {}).get("codigo") or "")]
    cods += list(mem.get("mostrados") or [])[-6:]
    cods += [s.get("codigo") or "" for s in ((res or {}).get("sugerencias") or []) if isinstance(s, dict)]
    out = []
    for c in cods:
        c = (c or "").strip().upper()
        if c and c not in out:
            out.append(c)
    return out


def ya_dichos(mem: dict, montos_por_codigo: dict) -> set:
    """Los montos de los modelos cuyo precio ya se le dio."""
    out = set()
    for c in mem.get("precios_dichos") or []:
        out |= montos_por_codigo.get(c, set())
    return out


def no_repetir(res: dict, mensaje: str, en_compra: bool, montos_por_codigo: dict, envios=(15.0, 20.0)) -> dict:
    """Red de salida: quita el precio de un modelo que ya se le dio (texto, pie y título), salvo que ella lo pregunte o
    esté cerrando la compra; luego anota qué precios salieron en este turno."""
    mem = res.get("memoria") if isinstance(res, dict) else None
    if not isinstance(mem, dict):
        return res
    dichos = [c for c in (mem.get("precios_dichos") or []) if isinstance(c, str)]
    sugs = [s for s in (res.get("sugerencias") or []) if isinstance(s, dict)]
    if dichos and not en_compra and not pregunta_precio(mensaje):
        quitar = ya_dichos({"precios_dichos": dichos}, montos_por_codigo)
        if quitar and isinstance(res.get("respuesta"), str):
            nuevo = quitar_de_texto(res["respuesta"], envios, solo=quitar)
            if nuevo != res["respuesta"]:
                res["respuesta"], res["precio_no_repetido"] = nuevo, True
        for s in sugs:
            if (s.get("codigo") or "").upper() in dichos:
                for k in ("pie", "titulo"):
                    if isinstance(s.get(k), str):
                        s[k] = quitar_de_pie(s[k])
    en_texto = {_num(x) for x in RE_MONTO.findall(res.get("respuesta") or "")}
    for c, ms in montos_por_codigo.items():
        if ms & en_texto and c not in dichos:
            dichos.append(c)
    for s in sugs:
        c = (s.get("codigo") or "").upper()
        if c and "S/" in (s.get("pie") or "") + (s.get("titulo") or "") and c not in dichos:
            dichos.append(c)
    mem["precios_dichos"] = dichos[-30:]
    return res

