"""El ciclo de V3: enrutador barato → LLM con herramientas → validador → respuesta.

Principio: el modelo decide qué hacer y redacta; el código valida y ejecuta. Un solo dueño del estado (la
ficha de `ficha.py`), que V3 escribe y el bot Go guarda opaca. Si algo falla (sin clave, rechazo doble,
excepción), se lanza y quien llama contesta V1.
"""
from __future__ import annotations

import logging
import re
import time

from .. import memoria
from . import cliente_llm, ficha as ficha_mod, prompt, validador
from .herramientas import Recolector

log = logging.getLogger("agente.v3")

# Enrutador barato (antes del LLM): casos que no merecen una llamada.
RE_ASESORA = re.compile(r"\b(asesora|reclamo|queja|persona real|hablar con (alguien|una persona|un humano)|agente humano)\b", re.I)
RE_FUERA_GIRO = re.compile(r"\b(historia|c[oó]mo (se )?calcul|capital de|pol[ií]tica|presidente|matem[aá]tica|clima|"
                           r"software|programaci[oó]n|desarroll\w* (de|un) (software|app|web|sistema)|contrat\w* (de|un) "
                           r"(software|desarroll\w*)|tarea (de|del) colegio|receta de cocina)\b", re.I)
TALLAS_REALES = ("S", "M", "L")


class Rechazado(RuntimeError):
    """El validador rechazó la respuesta dos veces: quien llama contesta V1."""


def _traza(respuesta: str, motivos, usadas, vueltas, ms) -> dict:
    return {"version": "v3", "motivos": motivos, "herramientas": usadas, "vueltas": vueltas, "ms": ms}


def conversar(req, fichas, consultar_stock, pie=None, permitir_reserva: bool = False) -> dict:
    """Un turno de V3. `req` es un ChatIn; `fichas` el catálogo vivo; `consultar_stock` E.stock.consultar."""
    t0 = time.time()
    mensaje = (req.mensaje or "").strip()
    f = ficha_mod.normalizar(getattr(req, "memoria", None))
    if getattr(req, "etapa", ""):
        f["etapa"] = req.etapa

    # 1. Extracción determinista (lo que la clienta ya dijo) y la pregunta pendiente.
    lectura = memoria.leer(f, mensaje, ahora=memoria.ahora_lima())
    f["etapa"] = _etapa(f, lectura)

    # 2. Enrutador barato.
    if RE_ASESORA.search(mensaje):
        return _respuesta(f, "", "asesora", [], None, _traza("", [], ["asesora"], 0, _ms(t0)), req)
    if RE_FUERA_GIRO.search(mensaje):
        return _respuesta(f, "Eso no es mi giro 😅 ¿Te ayudo con algo de la tienda?", "responder", [], None,
                          _traza("", [], [], 0, _ms(t0)), req)

    # 3. LLM con herramientas.
    # Pago habilitado: el pedido lo confirmó V3 (crear_reserva) o el bot Go ya lo confirmó (el *SI* del flujo fijo
    # deja la etapa en venta_confirmada y el estado en esperando_pago). Sin esto V3 nunca daba el Yape (12-10-2026).
    pago_ok = (bool((f.get("pedido") or {}).get("confirmado")) or getattr(req, "etapa", "") == "venta_confirmada"
               or getattr(req, "estado", "") == "esperando_pago")
    rec = Recolector(fichas, consultar_stock, pie=pie, permitir_reserva=permitir_reserva, mensaje=mensaje,
                     mostrados=f.get("mostrados") or [], pago_ok=pago_ok)
    hecho = _precalculo(rec, lectura.get("datos") or {})
    pedido = _pedido_en_curso(rec, req, f)
    if pedido:
        hecho = (hecho + "\n" + pedido).strip()
    msgs = prompt.construir(f, getattr(req, "historial", []) or [], mensaje, getattr(req, "canal", ""), hecho=hecho,
                            cliente=getattr(req, "cliente", ""), pago=pago_ok)
    salida = cliente_llm.responder(msgs, rec.esquema(), rec.ejecutar)
    out = prompt.leer_salida(salida["contenido"])
    _foto_pedida(rec, f, mensaje)
    _recortar_fotos(rec, mensaje)
    _foto_de_la_nombrada(rec, out["respuesta"])
    motivos = validador.validar(out["respuesta"], rec.hechos, out["pregunta_hecha"], f.get("preguntado", []))

    # 4. Un rechazo: regenerar con los motivos. Dos: caer a V1.
    if motivos:
        msgs2 = msgs + [{"role": "user", "content": "Tu respuesta se rechazó por: " + "; ".join(motivos) +
                         ". Corrígela y vuelve a responder SOLO el JSON."}]
        salida2 = cliente_llm.responder(msgs2, rec.esquema(), rec.ejecutar)
        out = prompt.leer_salida(salida2["contenido"])
        motivos = validador.validar(out["respuesta"], rec.hechos, out["pregunta_hecha"], f.get("preguntado", []))
        if motivos and all(m.startswith(("pregunta ya respondida", "más de una pregunta")) for m in motivos):
            out["respuesta"], out["pregunta_hecha"] = _sin_preguntas(out["respuesta"]), ""   # el resto vale: se quita la pregunta
            motivos = validador.validar(out["respuesta"], rec.hechos, "", [])
        if motivos:
            raise Rechazado("; ".join(motivos))

    return _cierra(f, rec, out, salida["usadas"], salida["vueltas"], _ms(t0), req)


def _recortar_fotos(rec: Recolector, mensaje: str) -> None:
    """Lo que se LE MANDA a la clienta: una foto, salvo que pida varias. La herramienta marca todo lo que
    el modelo pidió (eso lo necesita `enviar_fotos` tal cual para que un test con 2 códigos no pierda el 2°);
    el recorte de cuántas fotos salen es cosa de esta capa, que sí conoce el mensaje de la clienta."""
    from .herramientas import RE_PIDE_VARIAS, MAX_FOTOS
    tope = MAX_FOTOS if RE_PIDE_VARIAS.search((mensaje or "").lower()) else 1
    if len(rec.fotos) > tope:
        rec.fotos = rec.fotos[:tope]


def _foto_pedida(rec: Recolector, f: dict, mensaje: str) -> None:
    """El código manda la foto que ella pidió y el modelo olvidó marcar: la prenda que nombra, o la que tiene en foco."""
    from .herramientas import RE_PIDE_FOTO
    m = mensaje.lower()
    if rec.fotos or not RE_PIDE_FOTO.search(m):
        return
    plano = memoria._plano(mensaje)
    for x in rec.fichas:
        nombre = memoria._plano(x.nombre)
        clave = [p for p in nombre.split() if len(p) >= 4 and p not in ("vestido", "conjunto", "blusa", "pantalon", "falda", "enterizo", "blazer")]
        if x.codigo.lower() in plano or (clave and all(p in plano for p in clave[:1])):
            rec.ejecutar("enviar_fotos", {"codigos": [x.codigo]})
            return
    if f.get("producto") and re.search(r"\b(como es|ver(lo|la)?|foto)\b", plano):
        rec.ejecutar("enviar_fotos", {"codigos": [f["producto"]]})


def _precalculo(rec: Recolector, datos: dict) -> str:
    """Lo que el código ya sabe sin preguntarle al modelo si lo consulta: evita que la revise «después» del
    embudo de ocasión/fecha (11-10-2026: con 20 reglas en el prompt, la excepción pierde contra el guion)."""
    partes = []
    talla = (datos.get("talla") or "").upper()
    if talla and talla not in TALLAS_REALES:
        partes.append(f"HECHO YA VERIFICADO: pidió talla {talla}; esa talla no existe, solo hay S, M y L. "
                       "Díselo en tu respuesta (no lo dejes para después ni preguntes la ocasión primero).")
    if datos.get("estatura"):  # guion, paso 4: el modelo la anotaba («mides 1.60 m») y se saltaba la frase
        partes.append("HECHO: dio su estatura. Dile casi textual: «Excelente, sí está diseñado para tu estatura. Si en "
                      "caso te queda un poquito larguito, lo puedes ajustar con tu modista de confianza. ¡Es súper "
                      "sencillo!» y luego pregunta lo que falte del paso 3.")
    color = datos.get("color")
    if color:
        r = rec.ejecutar("buscar_catalogo", {"consulta": color})
        prendas = (r or {}).get("prendas") or []
        if prendas:
            desc = "; ".join(f"{p['codigo']} {p['nombre']} S/{p['precio']}" for p in prendas[:3])
            partes.append(f"HECHO YA VERIFICADO (color {color}): {desc}. Nómbralas en tu respuesta y mándalas "
                          "con enviar_fotos si corresponde; no preguntes la ocasión antes de decir esto.")
        else:
            partes.append(f"HECHO YA VERIFICADO: no tenemos nada color {color}. Dilo tal cual, no lo disimules "
                          "con otra prenda ni preguntes la ocasión antes.")
    return "\n".join(partes)


GENERICAS_NOMBRE = {"vestido", "conjunto", "blusa", "pantalon", "falda", "enterizo", "blazer"}


def _nombradas(texto: str, fichas) -> list[str]:
    """Códigos de las prendas que el texto nombra («Vestido Pandora», «Kabanova rojo», «V31»), en orden de aparición.
    Si un nombre está dentro de otro más largo («Kabanova» en «Kabanova Rojo»), vale el largo."""
    plano = memoria._plano(texto or "")
    hallados = []
    for x in fichas:
        nombre = " ".join(p for p in memoria._plano(x.nombre).split() if p not in GENERICAS_NOMBRE)
        for clave in {nombre, x.codigo.lower()}:
            if len(clave) >= 3:
                for m in re.finditer(r"\b" + re.escape(clave) + r"\b", plano):
                    hallados.append((m.start(), m.end(), x.codigo.upper()))
    hallados.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    out, fin = [], -1
    for ini, f_, cod in hallados:
        if ini < fin:          # dentro de un nombre más largo ya contado
            continue
        fin = f_
        if cod not in out:
            out.append(cod)
    return out


def _foto_de_la_nombrada(rec: Recolector, texto: str) -> None:
    """El texto habla de una prenda y la foto es de otra (12-10-2026: «te recomiendo el Vestido Pandora» con la foto del
    V21 Kendall). Manda lo que se dice: se cambia la foto por la de la prenda nombrada."""
    if not rec.fotos:
        return
    nombradas = _nombradas(texto, rec.fichas)
    enviadas = [x["codigo"].upper() for x in rec.fotos]
    if nombradas and not set(enviadas) & set(nombradas):
        log.info("V3: nombra %s pero la foto era %s; se corrige", nombradas, enviadas)
        rec.fotos = []
        rec.ejecutar("enviar_fotos", {"codigos": nombradas[:max(1, len(enviadas))]})


def _pedido_en_curso(rec: Recolector, req, f: dict) -> str:
    """El pedido que trae el bot Go (código y talla) o el de la ficha: su precio queda respaldado y el modelo puede
    sumar el envío (paso 8 del guion: «S/ 260 + S/ 20 = S/ 280») sin que el validador lo rechace."""
    cod = (getattr(req, "producto", "") or (f.get("pedido") or {}).get("codigo") or "").strip().upper()
    if not cod:
        return ""
    r = rec.ejecutar("stock", {"codigo": cod})
    if not isinstance(r, dict) or r.get("error"):
        return ""
    talla = (getattr(req, "talla", "") or (f.get("pedido") or {}).get("talla") or "").strip().upper()
    precio = r.get("precio")
    p = f"PEDIDO EN CURSO: {cod} {r.get('nombre', '')}" + (f", talla {talla}" if talla else "")
    if precio is not None:
        p += f", precio S/ {float(precio):.0f}. Total = precio + envío (Lima S/ 15, provincia S/ 20, agencia de viaje S/ 15)."
    return p


def _sin_preguntas(texto: str) -> str:
    """Quita las frases con «?» (la pregunta repetida) y deja lo demás; evita caer a V1 por una pregunta de más."""
    partes = re.split(r"(?<=[.!?])\s+|\n+", texto.strip())
    resto = [p for p in partes if "?" not in p]
    return " ".join(resto).strip() or texto.strip()


def _cierra(f: dict, rec: Recolector, out: dict, usadas, vueltas, ms, req) -> dict:
    """Actualiza la ficha con lo que hizo el turno y arma la respuesta para el bot Go."""
    f["herramientas"] = list(rec.usadas)
    accion = "responder"
    if rec.derivar:
        accion = "asesora"
    if rec.fotos:
        f["producto"] = rec.fotos[0]["codigo"]
        for x in rec.fotos:
            if x["codigo"] not in f["mostrados"]:
                f["mostrados"].append(x["codigo"])
    if rec.reserva:
        ficha_mod.fijar_pedido(f, rec.reserva["codigo"], rec.reserva["talla"], confirmado=True)
        f["etapa"] = "cierre"
        accion = "pedido"
    if rec.cita:
        f["sabemos"]["cita"] = f"{rec.cita['dia']}T{rec.cita['hora']}"
        accion = "responder"
    preg = out.get("pregunta_hecha", "")
    if preg:
        f["pendiente"] = preg if preg in memoria.PENDIENTES else ""
        if preg not in f["preguntado"]:
            f["preguntado"].append(preg)
    elif not rec.cita:
        f["pendiente"] = "" if rec.reserva else f["pendiente"]
    return _respuesta(f, out["respuesta"], accion, rec.fotos, rec.reserva,
                      _traza(out["respuesta"], [], usadas, vueltas, ms), req)


def _respuesta(f: dict, texto: str, accion: str, fotos, reserva, traza: dict, req) -> dict:
    sugerencias = [{"codigo": x["codigo"], "fuente": _fuente(x["codigo"]), "imagen": x["imagen"], "pie": x["pie"],
                    "titulo": x.get("titulo", ""), "tallas": x.get("tallas", [])}
                   for x in (fotos or [])]
    return {"respuesta": texto, "accion": accion, "codigo": (reserva or {}).get("codigo", ""),
            "talla": (reserva or {}).get("talla", ""), "sugerencias": sugerencias, "etapa": f.get("etapa", "prospeccion"),
            "memoria": f, "modelo_llm": cliente_llm.MODELO, "motor": "v3", "version": "v3", "v3": traza}


def _fuente(codigo: str) -> str:
    return "seed"  # el catálogo de 100 va apagado en producción (CATALOGO100=0)


def _etapa(f: dict, lectura: dict) -> str:
    if lectura.get("es_cita") or f["pedido"].get("confirmado"):
        return "venta_confirmada" if f["pedido"].get("confirmado") else "cierre"
    if f.get("producto") or f.get("mostrados"):
        return "seguimiento" if f.get("etapa") in ("", "prospeccion") else f["etapa"]
    return f.get("etapa") or "prospeccion"


def _ms(t0: float) -> int:
    return int((time.time() - t0) * 1000)
