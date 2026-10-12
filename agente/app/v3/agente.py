"""El ciclo de V3: enrutador barato → LLM con herramientas → validador → respuesta.

Principio: el modelo decide qué hacer y redacta; el código valida y ejecuta. Un solo dueño del estado (la
ficha de `ficha.py`), que V3 escribe y el bot Go guarda opaca. Si algo falla (sin clave, rechazo doble,
excepción), se lanza y quien llama contesta V1.
"""
from __future__ import annotations

import logging
import re
import time

from .. import ficha_producto, memoria
from . import cliente_llm, ficha as ficha_mod, plantillas, prompt, validador
from .herramientas import Recolector

log = logging.getLogger("agente.v3")

# Enrutador barato (antes del LLM): casos que no merecen una llamada.
RE_ASESORA = re.compile(r"\b(asesora|reclamo|queja|persona real|hablar con (alguien|una persona|un humano)|agente humano)\b", re.I)
# Se despide o agradece al cerrar: ahí sí se puede terminar sin pregunta.
# Saludo suelto: arranca una conversación nueva (12-10-2026, WhatsApp real: a «hola» le contestó con la boda, la
# talla y la noche de la charla de la mañana, y al no poder repetir esas preguntas se quedó sin preguntar nada).
RE_SALUDO_SOLO = re.compile(r"^\s*(hola+|holi|buenas|buen[oa]s?\s+(d[ií]as?|tardes|noches)|hey|al[oó])\s*[!.?¡¿]*\s*$", re.I)
RE_CATALOGO = re.compile(r"\b(cat[aá]logo|p[aá]gina( web)?|web|link|enlace)\b", re.I)
# Ofrecer modelos sin que los pida (regla de la tienda, 12-10-2026): «¿te muestro un modelo?», «¿quieres ver opciones?».
RE_OFRECE = re.compile(r"(te (muestre|muestro|enseñe|enseño|mande|mando|paso|comparta|comparto)|quieres ver|gustar[ií]a ver)"
                       r"[^.?!]{0,40}\b(modelo|modelos|opci[oó]n|opciones|vestidos?|prendas?|fotos?)\b", re.I)
RE_SI = re.compile(r"^\s*(s[ií]+|claro|ok+|okey|dale|bueno|ya|perfecto|de acuerdo|tienes raz[oó]n|si tienes raz[oó]n|"
                   r"s[ií],? tienes raz[oó]n|me parece( bien)?|exacto|correcto|as[ií] es)\b[^?]*$", re.I)
RE_ESTE_SIN_CUAL = re.compile(r"\b(este|esta|ese|esa|aquel|aquella)\s+(vestido|modelo|conjunto|blusa|prenda|"
                              r"pantal[oó]n|falda|enterizo|blazer)\b", re.I)
RE_PREGUNTO_CUAL = re.compile(r"\b(cual|que modelo|la foto o el nombre|nombre o codigo|codigo del|foto del modelo)\b")
PLANTILLA_PASO7 = ("Excelente 😊 ¿El envío sería para Lima o provincia?",
                   "¡Genial! ¿El envío sería para Lima o para provincia?",
                   "Perfecto 🥰 Cuéntame, ¿el envío sería para Lima o provincia?")
RE_DESPIDE = re.compile(r"\b(chau|chao|adi[oó]s|hasta luego|nos vemos|bye|muchas gracias|gracias)\b", re.I)
RE_FUERA_GIRO = re.compile(r"\b(historia|c[oó]mo (se )?calcul|capital de|pol[ií]tica|presidente|matem[aá]tica|clima|"
                           r"software|programaci[oó]n|desarroll\w* (de|un) (software|app|web|sistema)|contrat\w* (de|un) "
                           r"(software|desarroll\w*)|tarea (de|del) colegio|receta de cocina)\b", re.I)
TALLAS_REALES = ("S", "M", "L")
TALLAS_BAJO_PEDIDO = ("XS", "XL", "2XL", "XXL")   # «pre orden» en baruka.pe: se confeccionan


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

    hist = getattr(req, "historial", []) or []
    ultimo_bot = memoria._plano((_bots(hist) or [""])[-1])
    if RE_SALUDO_SOLO.match(mensaje) and not (f.get("pedido") or {}).get("confirmado") \
            and f.get("etapa", "prospeccion") in ("", "prospeccion", "seguimiento") and "rosmary" not in ultimo_bot:
        _nueva_conversacion(f)

    # Paso 7 por plantilla: tras la escasez («¿no lo consideras?») un «sí» va directo a la pregunta del envío. El modelo
    # a veces ofrecía «¿te gustaría separarlo?» aunque el paso se lo indicaba (12-10-2026).
    if "consideras" in ultimo_bot and RE_SI.match(mensaje) and not (f.get("sabemos") or {}).get("envio"):
        f["etapa"] = f.get("etapa") or "seguimiento"
        f["pendiente"] = "lima_o_provincia"
        return _respuesta(f, _variante(PLANTILLA_PASO7, hist), "responder", [], None,
                          _traza("", [], ["plantilla:paso7"], 0, _ms(t0)), req)

    # 1. Extracción determinista (lo que la clienta ya dijo) y la pregunta pendiente.
    lectura = memoria.leer(f, mensaje, ahora=memoria.ahora_lima())
    f["etapa"] = _etapa(f, lectura)

    # 2. Enrutador barato.
    if RE_ASESORA.search(mensaje):
        return _respuesta(f, "", "asesora", [], None, _traza("", [], ["asesora"], 0, _ms(t0)), req)
    if RE_FUERA_GIRO.search(mensaje):
        return _respuesta(f, "Eso no es mi giro 😅 ¿Te ayudo con algo de la tienda?", "responder", [], None,
                          _traza("", [], [], 0, _ms(t0)), req)

    # 2b. Pasos del guion por plantilla (app/v3/plantillas.py): el dato esperado y nada más, sin pago en curso.
    en_pago = (bool((f.get("pedido") or {}).get("confirmado")) or getattr(req, "etapa", "") == "venta_confirmada"
               or getattr(req, "estado", "") == "esperando_pago")
    if not en_pago:
        txt = ""
        if RE_SALUDO_SOLO.match(mensaje) and not _bots(hist):
            txt = plantillas.saludo(getattr(req, "cliente", ""), hist)
            plantillas._marca(f, "ocasion")
        else:
            txt = (plantillas.toque(mensaje, hist) or plantillas.ok_sin_dato(f, mensaje, hist)
                   or plantillas.responder(f, lectura.get("datos") or {}, mensaje, hist, getattr(req, "cliente", "")) or "")
        if txt:
            log.info("V3 plantilla: %s", txt[:80])
            return _respuesta(f, txt, "responder", [], None, _traza("", [], ["plantilla"], 0, _ms(t0)), req)

    # 3. LLM con herramientas.
    # Pago habilitado: el pedido lo confirmó V3 (crear_reserva) o el bot Go ya lo confirmó (el *SI* del flujo fijo
    # deja la etapa en venta_confirmada y el estado en esperando_pago). Sin esto V3 nunca daba el Yape (12-10-2026).
    pago_ok = (bool((f.get("pedido") or {}).get("confirmado")) or getattr(req, "etapa", "") == "venta_confirmada"
               or getattr(req, "estado", "") == "esperando_pago")
    rec = Recolector(fichas, consultar_stock, pie=pie, permitir_reserva=permitir_reserva, mensaje=mensaje,
                     mostrados=f.get("mostrados") or [], pago_ok=pago_ok,
                     contesta_cual=bool(RE_PREGUNTO_CUAL.search(ultimo_bot)))
    hecho = _precalculo(rec, {**(lectura.get("datos") or {}),
                              "producto": getattr(req, "producto", "") or f.get("producto")
                                          or ((f.get("mostrados") or [""])[-1])})
    pedido = _pedido_en_curso(rec, req, f)
    if pedido:
        hecho = (hecho + "\n" + pedido).strip()
    paso = "" if (lectura.get("datos") or {}).get("horario") else _paso_guion(f, getattr(req, "historial", []) or [], pago_ok)
    if re.search(r"\bsepar", memoria._plano(mensaje)) and not (f.get("producto") or f.get("mostrados")
                                                               or (f.get("pedido") or {}).get("codigo")) \
            and not _nombradas(mensaje, rec.fichas):
        rec.sin_cual = True
        paso = ("QUIERE SEPARAR SIN DECIR QUÉ PRENDA: pregúntale cuál (el nombre, el código o una foto); no inventes "
                "una ni mandes fotos. " + (paso or "")).strip()
    elif RE_ESTE_SIN_CUAL.search(mensaje) and not _nombradas(mensaje, rec.fichas):
        rec.sin_cual = True
        paso = ("PREGUNTA POR «ESTE» MODELO SIN DECIR CUÁL: pídele la foto o el nombre/código del modelo que vio; no "
                "mandes fotos ni adivines. " + (paso or "")).strip()
    elif RE_CATALOGO.search(mensaje):
        paso = ("PIDIÓ EL CATÁLOGO: dale https://baruka.pe (es el único enlace) y ofrécete a ayudarla a elegir; no "
                "mandes fotos de una prenda en lugar del catálogo. " + (paso or "")).strip()
    elif rec._pide_modelos() and not pago_ok:
        paso = ("ELLA PIDIÓ VER MODELOS: muéstrale UNA prenda que cuadre con lo que contó (enviar_fotos), nómbrala con "
                "su precio y pregunta qué le gustó; el resto del guion sigue después. " + (paso or "")).strip()
    if paso:
        hecho = (hecho + "\n" + paso).strip()
    msgs = prompt.construir(f, getattr(req, "historial", []) or [], mensaje, getattr(req, "canal", ""), hecho=hecho,
                            cliente=getattr(req, "cliente", ""), pago=pago_ok)
    salida = cliente_llm.responder(msgs, rec.esquema(), rec.ejecutar)
    out = prompt.leer_salida(salida["contenido"])
    _foto_pedida(rec, f, mensaje)
    _recortar_fotos(rec, mensaje)
    _foto_de_la_nombrada(rec, out["respuesta"])
    motivos = validador.validar(out["respuesta"], rec.hechos, out["pregunta_hecha"], _respondidas(f))
    motivos += _no_avanza(out["respuesta"], mensaje, pago_ok, rec)
    motivos += _repite(out["respuesta"], getattr(req, "historial", []) or [])
    motivos += _ofrece(out["respuesta"], rec)
    motivos += _despedida(out["respuesta"], mensaje)
    motivos_1, salida2 = bool(motivos), None
    if motivos:
        log.info("V3 rechazo 1: %s | %s", "; ".join(motivos), out["respuesta"][:160])

    # 4. Un rechazo: regenerar con los motivos. Dos: caer a V1.
    if motivos:
        msgs2 = msgs + [{"role": "user", "content": "Tu respuesta se rechazó por: " + "; ".join(motivos) +
                         ". Corrígela y vuelve a responder SOLO el JSON."}]
        salida2 = cliente_llm.responder(msgs2, rec.esquema(), rec.ejecutar)
        out = prompt.leer_salida(salida2["contenido"])
        motivos = validador.validar(out["respuesta"], rec.hechos, out["pregunta_hecha"], _respondidas(f))
        motivos += _no_avanza(out["respuesta"], mensaje, pago_ok, rec)
        motivos += _repite(out["respuesta"], getattr(req, "historial", []) or [])
        motivos += _ofrece(out["respuesta"], rec)
        motivos += _despedida(out["respuesta"], mensaje)
        if motivos and all(m.startswith(("pregunta ya respondida", "más de una pregunta", "repite una frase",
                                         "no avanza", "ofrece modelos", "se despidió")) for m in motivos):
            if any(m.startswith("se despidió") for m in motivos):
                out["respuesta"] = _sin_preguntas(out["respuesta"])
            if any(m.startswith("ofrece modelos") for m in motivos):
                out["respuesta"] = " ".join(x for x in re.split(r"(?<=[.!?…])\s+|\n+", out["respuesta"].strip())
                                            if not RE_OFRECE.search(x)).strip()
            if any(m.startswith("repite una frase") for m in motivos):   # se quitan las frases ya dichas
                out["respuesta"] = _sin_repetidas(out["respuesta"], getattr(req, "historial", []) or [])
            if any(m.startswith(("pregunta ya respondida", "más de una pregunta")) for m in motivos):
                out["respuesta"], out["pregunta_hecha"] = _sin_preguntas(out["respuesta"]), ""   # el resto vale: se quita la pregunta
            if not pago_ok:
                out["respuesta"] = _con_siguiente(out["respuesta"], f, hist)  # …pero que no quede sin avanzar
            log.info("V3 rechazo 2 recortado: %s | %s", "; ".join(motivos), out["respuesta"][:160])
            motivos = validador.validar(out["respuesta"], rec.hechos, "", [])
        if motivos:
            raise Rechazado("; ".join(motivos))

    if not _bots(hist) and "rosmary" not in memoria._plano(out["respuesta"]):
        nombre = (getattr(req, "cliente", "") or "").strip().split(" ")[0]
        out["respuesta"] = (f"¡Hola{', ' + nombre if nombre else ''}! 😊 Te saluda Rosmary, desde ahora considérame tu "
                            f"asesora personal. " + re.sub(r"^\s*¡?hola[^!.]*[!.]\s*", "", out["respuesta"], flags=re.I))
    res = _cierra(f, rec, out, salida["usadas"], salida["vueltas"], _ms(t0), req)
    res["v3"]["uso"] = cliente_llm.sumar_uso(salida.get("uso"), (salida2 or {}).get("uso") if motivos_1 else None)
    return res


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
    retirado = _retirado(rec.mensaje, rec.fichas)
    if retirado:
        rec.retirado = retirado
        partes.append(f"HECHO YA VERIFICADO: el {retirado} ya no está en el catálogo (baruka.pe es el catálogo oficial). "
                      "Díselo con amabilidad y pregúntale si quiere ver opciones parecidas; no mandes otra prenda como si "
                      "fuera esa.")
    # Pregunta un atributo (medidas, tela, forro, mangas…) de la prenda en foco: el dato de su ficha (baruka.pe) va ya
    # verificado. Sin esto «¿tienes tabla de medidas?» tras ver el Elvi se contestaba con el enlace de la web, teniendo
    # la tabla (12-10-2026).
    foco = (datos.get("producto") or "").upper()
    attrs = ficha_producto.pedidos(memoria._plano(rec.mensaje))
    if foco and attrs and ficha_producto.ficha(foco):
        partes.append("HECHO YA VERIFICADO: " + ficha_producto.nota(foco, attrs))
    talla = (datos.get("talla") or "").upper()
    if talla in TALLAS_BAJO_PEDIDO:
        # baruka.pe vende XS, XL y 2XL «pre orden» en muchos modelos (12-10-2026): se confeccionan, no hay en stock.
        cod = (datos.get("producto") or "").upper()
        tallas_web = [x.upper() for x in ficha_producto.web(cod).get("tallas", [])] if cod else []
        if tallas_web and talla not in tallas_web:
            partes.append(f"HECHO YA VERIFICADO: pidió talla {talla}; el {cod} no viene en esa talla "
                          f"(tallas: {', '.join(tallas_web)}). Díselo en tu respuesta.")
        else:
            partes.append(f"HECHO YA VERIFICADO: pidió talla {talla}; esa talla va BAJO PEDIDO (se confecciona; no hay en "
                          "stock) en los modelos que la tienen en baruka.pe. Díselo en tu respuesta; el plazo lo confirma una "
                          "asesora (no lo inventes).")
    elif talla and talla not in TALLAS_REALES:
        partes.append(f"HECHO YA VERIFICADO: pidió talla {talla}; esa talla no existe: hay S, M y L, y XS, XL o 2XL bajo "
                       "pedido en algunos modelos. Díselo en tu respuesta (no lo dejes para después ni preguntes la "
                       "ocasión primero).")
    t = (datos.get("talla") or "").upper()
    if t in TALLAS_REALES:  # guion, paso 4: el modelo a veces lo dejaba en «tomo nota de que eres talla L»
        partes.append("HECHO: dio su talla. Dile casi textual: «¡Perfecto, sí lo tenemos aún disponible en tu talla! 😊 "
                      "Te comento que nuestras tallas son completas y son medidas peruanas.» y luego pregunta lo que "
                      "falte del paso 3.")
    horario = datos.get("horario")
    if horario == "noche":
        partes.append("HECHO: el evento es de NOCHE. Dile casi textual: «Perfecto 😊 Tengo colores vibrantes y sobrios. "
                      "¿Cómo quieres sentirte ese día? ¿Prefieres llamar un poco más la atención, o buscas verte elegante, "
                      "sofisticada y más discreta?». En este mensaje NO preguntes la fecha.")
    elif horario == "dia":
        partes.append("HECHO: el evento es de DÍA. Dile casi textual: «Perfecto 😊 Al ser un evento de día, serían ideales "
                      "opciones que se vean frescas, elegantes y que armonicen. Cuéntame, ¿en qué color estás pensando?». "
                      "En este mensaje NO preguntes la fecha.")
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
            rec.sin_color = color   # no se manda la foto de otra prenda como si fuera de ese color
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
        # El nombre completo también cuenta y, por largo, gana: «pantalón Holly» es el pantalón aunque el «Vestido
        # Holly» comparta «holly» (baruka.pe tiene Holly, Camila y Zoraida en dos prendas, 12-10-2026).
        for clave in {nombre, memoria._plano(x.nombre), x.codigo.lower()}:
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


def _retirado(mensaje: str, fichas) -> str:
    """Nombre del modelo retirado que nombra el mensaje («el Irla», «kabanova azul»), si ningún modelo vigente se llama
    igual. Sale de seed/fichas_producto.json (las fichas con `retirado`, con su nombre del CRM)."""
    plano = memoria._plano(mensaje or "")
    vigentes = " ".join(memoria._plano(x.nombre) for x in fichas)
    for cod, fx in ficha_producto.cargar().items():
        if not fx.get("retirado") or not fx.get("nombre"):
            continue
        clave = " ".join(p for p in memoria._plano(fx["nombre"]).split() if p not in GENERICAS_NOMBRE)
        primera = clave.split()[0] if clave else ""
        if len(primera) >= 3 and re.search(r"\b" + re.escape(primera) + r"\b", plano) \
                and not re.search(r"\b" + re.escape(primera) + r"\b", vigentes):
            return fx["nombre"]
    return ""


def _foto_de_la_nombrada(rec: Recolector, texto: str) -> None:
    """El texto habla de una prenda y la foto es de otra (12-10-2026: «te recomiendo el Vestido Pandora» con la foto del
    V21 Kendall). Manda lo que se dice: se cambia la foto por la de la prenda nombrada."""
    nombradas = _nombradas(texto, rec.fichas)
    if not rec.fotos:
        # Pidió ver modelos y el texto presenta una prenda («Te muestro el Vestido Kendall…») sin foto: se manda.
        if nombradas and rec._pide_modelos() and not rec.sin_cual and not rec.sin_color and not rec.retirado:
            rec.ejecutar("enviar_fotos", {"codigos": nombradas[:1]})
        return
    enviadas = [x["codigo"].upper() for x in rec.fotos]
    if nombradas and not set(enviadas) & set(nombradas):
        log.info("V3: nombra %s pero la foto era %s; se corrige", nombradas, enviadas)
        rec.fotos = []
        rec.ejecutar("enviar_fotos", {"codigos": nombradas[:max(1, len(enviadas))]})


def _nueva_conversacion(f: dict) -> None:
    """Olvida lo de la conversación anterior (evento, fecha, talla, color, prenda mostrada…). Conserva el nombre."""
    nombre = (f.get("sabemos") or {}).get("nombre")
    f["sabemos"] = {"nombre": nombre} if nombre else {}
    for k in ("preguntado", "mostrados"):
        f[k] = []
    f["pendiente"], f["producto"], f["etapa"] = "", "", "prospeccion"


# Las preguntas del guion (seed/speech.md), en su orden, para completar un mensaje que quedó sin pregunta.
PREGUNTAS_GUION = (("ocasion", "Cuéntame, ¿qué evento se te aproxima?"), ("talla", "¿En qué talla te encuentras?"),
                   ("horario", "¿El evento es de día o de noche?"), ("estatura", "¿Cuánto mides de estatura?"),
                   ("fecha", "¡Una consulta importante!, ¿para cuándo lo necesitas?"),
                   ("envio", "¿El envío sería para Lima o provincia?"))


def _respondidas(f: dict) -> list[str]:
    """Las preguntas cuya respuesta YA está en la ficha. Antes se pasaba `preguntado` (las que se HICIERON): una
    pregunta hecha y nunca contestada («¿para cuándo?») se rechazaba como «ya respondida» (12-10-2026)."""
    sab = f.get("sabemos") or {}
    out = []
    for k in f.get("preguntado") or []:
        dato = memoria.DATO_DE.get(k)
        if dato and (sab.get(dato) or (dato == "fecha" and sab.get("fecha_iso"))):
            out.append(k)
    return out


def _con_siguiente(texto: str, f: dict, historial=()) -> str:
    """Si al quitar una pregunta (repetida o de más) el mensaje queda sin pregunta, se le suma la siguiente del guion
    que no se haya hecho en los dos últimos mensajes (si no, se repetiría en bucle: «hola?», «??»)."""
    if "?" in texto:
        return texto
    sab = f.get("sabemos") or {}
    ult3 = [memoria._plano(b) for b in _bots(historial)[-3:]]
    clave = lambda q: memoria._plano(q).strip("¡!¿? ")
    q = next((q for k, q in PREGUNTAS_GUION if not (sab.get(k) or (k == "fecha" and sab.get("fecha_iso")))
              and sum(clave(q) in b for b in ult3) < 2), "")   # se omite solo si ya se hizo dos veces
    return f"{texto} {q}".strip() if q else texto


def _sin_repetidas(texto: str, historial) -> str:
    dichas = {fr for b in _bots(historial[-24:]) for fr in _frases(b) if len(fr) >= 18}
    partes = re.split(r"(?<=[.!?…])\s+|\n+", (texto or "").strip())
    quedan = [x for x in partes if _norm(x) not in dichas]
    return " ".join(quedan).strip()


def _bots(historial) -> list[str]:
    return [getattr(t, "texto", "") if not isinstance(t, dict) else t.get("texto", "")
            for t in historial if (getattr(t, "rol", "") if not isinstance(t, dict) else t.get("rol", "")) != "cliente"]


def _norm(x: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9ñ ]", " ", memoria._plano(x))).strip()


def _frases(texto: str) -> list[str]:
    """Las frases del texto normalizadas (sin tildes, emojis ni signos): «😊 Cuéntame, ¿qué evento…?» = «cuentame que evento…»."""
    return [_norm(x) for x in re.split(r"(?<=[.!?…])\s+|\n+", texto or "") if x.strip()]


def _repite(texto: str, historial) -> list[str]:
    """Una frase de 25+ letras que el bot ya dijo en esta conversación: es lo que más suena a robot (12-10-2026: el
    «¡Waooo, qué bonito! Debes estar emocionada…» salía dos veces seguidas)."""
    dichas = {fr for b in _bots(historial[-24:]) for fr in _frases(b) if len(fr) >= 18}
    rep = [fr for fr in _frases(texto) if len(fr) >= 18 and fr in dichas]
    return [f"repite una frase que ya dijiste: «{rep[0][:60]}»; dilo con otras palabras o pasa al siguiente paso"] if rep else []


def _paso_guion(f: dict, historial, pago_ok: bool) -> str:
    """En qué paso del guion (seed/speech.md) va la conversación, por lo que ya se sabe. Lo decide el código para que
    el modelo no se salte pasos ni los repita; el modelo solo lo redacta y contesta antes lo que ella pregunte."""
    sab = f.get("sabemos") or {}
    ultimo = memoria._plano((_bots(historial) or [""])[-1])
    if pago_ok:
        if "yape" in " ".join(memoria._plano(b) for b in _bots(historial)[-6:]):
            return ("PASO DEL GUION: 9, final. Los datos de pago YA se dieron: no los repitas salvo que ella los pida; "
                    "pide el voucher para programar el envío o contesta lo que pregunte.")
        return "PASO DEL GUION: 8-9 (total con el envío elegido si falta, luego datos de pago y pedir el voucher)."
    if not sab.get("ocasion"):
        return "PASO DEL GUION: 1 (preséntate solo si es tu primer mensaje y pregunta qué evento se le aproxima)."
    ult3 = [memoria._plano(b) for b in _bots(historial)[-3:]]
    recien = {k for k, pat in (("talla", "talla"), ("horario", "dia o de noche"), ("estatura", "estatura|mides"))
              if sum(1 for b in ult3 if re.search(pat, b) and "?" in b) >= 2}   # ya se le preguntó dos veces: no insistir
    faltan = [n for k, n in (("talla", "talla"), ("horario", "si el evento es de día o de noche"),
                             ("estatura", "estatura")) if not sab.get(k) and k not in recien]
    if faltan:
        return ("PASO DEL GUION: 3 (si no lo dijiste ya, la emoción del paso 2 va una sola vez). Pregunta SOLO lo que "
                "falta: " + ", ".join(faltan) + ".")
    if "consideras" in ultimo and not sab.get("envio"):   # ya se dijo la escasez y ella contestó (aunque la fecha no se entendiera)
        return "PASO DEL GUION: 7 («Excelente, ¿el envío sería para Lima o provincia?»). No ofrezcas separar ni probar."
    if not (sab.get("fecha") or sab.get("fecha_iso")):
        return "PASO DEL GUION: 6 («¡Una consulta importante!, ¿para cuándo lo necesitas?»)."
    if not sab.get("envio"):
        return ("PASO DEL GUION: 6, segunda parte (la frase de la fecha —pronto o con tiempo— con la escasez y "
                "«¿No lo consideras?»).")
    return "PASO DEL GUION: 7-8 (costo del envío elegido; si acepta, el total y confirmar prenda y talla)."


def _variante(opciones, historial) -> str:
    """Una variante que no se haya dicho en esta conversación (la primera libre)."""
    dichas = {memoria._plano(b) for b in _bots(historial)}
    return next((o for o in opciones if memoria._plano(o) not in dichas), opciones[0])


def _ofrece(texto: str, rec: Recolector) -> list[str]:
    if rec.fotos or rec._pide_modelos() or not RE_OFRECE.search(texto or ""):
        return []
    return ["ofrece modelos sin que ella los pida: no los ofrezcas, sigue con el paso del guion"]


def _despedida(texto: str, mensaje: str) -> list[str]:
    """Solo se despide («chau, gracias»): se despide sin preguntar nada."""
    m = memoria._plano(mensaje)
    solo = RE_DESPIDE.search(m) and len(m.split()) <= 5 and "?" not in mensaje and not re.search(r"\b(pero|y|quiero|cuanto)\b", m)
    return ["se despidió: despídete con calidez y sin preguntar nada"] if solo and "?" in (texto or "") else []


def _no_avanza(texto: str, mensaje: str, pago_ok: bool, rec: Recolector) -> list[str]:
    """Guion de la tienda: toda respuesta termina con la pregunta del siguiente paso. 12-10-2026, WhatsApp real:
    «es para mi novia» → «¡Qué lindo detalle de tu parte!» y nada más; la clienta se quedó esperando."""
    if "?" in (texto or "") or pago_ok or rec.derivar or rec.cita or RE_DESPIDE.search(mensaje or ""):
        return []
    return ["no avanza: termina sin la pregunta del siguiente paso del guion"]


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


# Opciones para la pregunta con que termina el mensaje: por WhatsApp salen como encuesta (el «botón» que sí se ve
# desde una cuenta no oficial) y en el chat web como botones. El texto de cada opción es lo que ya se entiende escrito.
OPCIONES = (
    (re.compile(r"lima o (para )?provincia"), ("Lima", "Provincia")),
    (re.compile(r"olva courier o shalom|cual de ellas prefieres|cual prefieres"), ("Olva Courier", "Shalom")),
    (re.compile(r"no lo consideras"), ("Sí, tienes razón", "Lo voy a pensar")),
    (re.compile(r"llamar un poco mas la atencion|llamar la atencion"), ("Llamar la atención", "Elegante y discreta")),
    (re.compile(r"te parece bien"), ("Sí, me parece bien", "Tengo una duda")),
    (re.compile(r"de dia o de noche"), ("De día", "De noche")),
    (re.compile(r"(en )?que talla (te encuentras|usas)|tu talla"), ("S", "M", "L")),
)


def _opciones(texto: str) -> list[str]:
    """Las opciones de la ÚLTIMA pregunta, si es de respuesta cerrada y va sola (el paso 3 con tres preguntas, no)."""
    t = texto or ""
    if t.count("?") == 0:
        return []
    ini = t.rfind("¿", 0, t.rfind("?"))
    ultima = memoria._plano(t[ini:] if ini >= 0 else t)
    if t.count("?") > 2 or (t.count("?") == 2 and not re.search(r"llamar|atencion", ultima)):
        return []
    for rx, ops in OPCIONES:
        if rx.search(ultima):
            return list(ops)
    return []


def _respuesta(f: dict, texto: str, accion: str, fotos, reserva, traza: dict, req) -> dict:
    sugerencias = [{"codigo": x["codigo"], "fuente": _fuente(x["codigo"]), "imagen": x["imagen"], "pie": x["pie"],
                    "titulo": x.get("titulo", ""), "tallas": x.get("tallas", [])}
                   for x in (fotos or [])]
    return {"respuesta": texto, "accion": accion, "codigo": (reserva or {}).get("codigo", ""),
            "talla": (reserva or {}).get("talla", ""), "sugerencias": sugerencias, "etapa": f.get("etapa", "prospeccion"),
            "memoria": f, "modelo_llm": cliente_llm.MODELO, "motor": "v3", "version": "v3", "v3": traza,
            "respuestas_rapidas": [{"label": o} for o in _opciones(texto)] if accion == "responder" else []}


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
