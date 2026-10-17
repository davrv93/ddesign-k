"""El ciclo de V3: enrutador barato → LLM con herramientas → validador → respuesta.

Principio: el modelo decide qué hacer y redacta; el código valida y ejecuta. Un solo dueño del estado (la
ficha de `ficha.py`), que V3 escribe y el bot Go guarda opaca. Si algo falla (sin clave, rechazo doble,
excepción), se lanza y quien llama contesta V1.
"""
from __future__ import annotations

import logging
import re
import time

from .. import colecciones, colores, conocimiento, ficha_producto, fotos_color, memoria, nombres, precios
from . import cliente_llm, ficha as ficha_mod, plantillas, prompt, prospectos, validador
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
# «rebajar», «es seguro?», «y si se pierde?» no entraban (14-10-2026, prueba de 300 conversaciones: se mandaba la foto de
# un vestido junto a la respuesta de la objeción).
RE_OBJECION_ENVIO = re.compile(r"\b(car[oa]|car[ií]simo|muy caro|tan caro|rebaj\w*|descuento|m[aá]s barato|gratis|"
                               r"no (me )?llega|se (me )?pierde|perder\w*|segur[oa]|seguro que llega|confiable|confianza|"
                               r"garant\w*|llega bien|llegar[aá])\b", re.I)
# Talla en número («uso talla 42», «soy 38»): la tienda trabaja S, M y L; no se da por buena (14-10-2026).
RE_TALLA_NUMERO = re.compile(r"\b(?:talla|uso|soy|la)\s+(?:la\s+|talla\s+)?(\d{1,2})\b(?!\s*(?:cm|kg|kilos|años|anos|dias|metros|m\b))")
# Pregunta cómo pagar o manda el pago justo tras «¿Confirmamos tu pedido?»: es un sí (14-10-2026, «ya, cómo te pago»).
RE_PAGAR = re.compile(r"\b(como (te )?pag\w*|yape\w*|plin|datos (de|para el) pago|a que numero|numero de cuenta|cuenta|"
                      r"transfer\w*|deposit\w*|voucher|comprobante|ya (te )?pague|te yape\w*)\b")
RE_PIDE_CONFIRMAR = re.compile(r"\bconfirma(mos|s)?\b.{0,25}\bpedido\b|\bte lo separo\b|\blo separamos\b")
# Preguntas que no están en RE_PREGUNTA_DE y que también cansan si se repiten.
RE_PREGUNTA_EXTRA = {"ocasion": re.compile(r"\b(que evento|que ocasion|para que ocasion|que celebracion|evento se te aproxima|"
                                           r"ocasion (tienes|se te aproxima))\b"),
                     "confirmar": RE_PIDE_CONFIRMAR}
RE_NO = re.compile(r"^\s*(no+|nop|nel|no,? gracias|mejor no|as[ií] no m[aá]s|todav[ií]a no|a[uú]n no)\b[^?]*$", re.I)
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
# Habla de OTRA prenda, no de la que está en foco: ahí un color es buscar en el catálogo, no la foto de esa prenda.
RE_OTRA_PRENDA = re.compile(r"\b(algo|algun\w*|otr[oa]s?|parecid\w*|similar\w*|modelos|opciones|vestidos|conjuntos|"
                            r"blusas|prendas|catalogo)\b")
# Pregunta por un color de la prenda en foco («¿cuál es el tono verde olivo?», «¿y en rojo?», «mándamela en vino»).
RE_SOBRE_COLOR = re.compile(r"\b(tonos?|colou?r(es)?|tonalidad|fot\w*|ver(lo|la)?|veo|muestra\w*|envia\w*|manda\w*|"
                            r"pasa\w*|tienes|tienen|hay|como es|como se ve)\b")
# No le convence la prenda en foco («no me gusta», «otro modelo», «algo diferente»): se muestra otra, no se vuelve a esa.
RE_RECHAZA = re.compile(r"\b(no me (gusta|gusto|convence|convencio|agrada)|no es lo que busco|otro modelo|otra opcion|otros modelos|"
                        r"algo (diferente|distinto|mas)|otro estilo|mejor otro|muestrame otro|ensename otro|tienes otro|hay otro|"
                        r"no,? otro|ese no|este no|ninguno)\b")
TALLAS_REALES = ("S", "M", "L")
TALLAS_BAJO_PEDIDO = ("XS", "XL", "2XL", "XXL")   # «pre orden» en baruka.pe: se confeccionan


class Rechazado(RuntimeError):
    """El validador rechazó la respuesta dos veces: quien llama contesta V1."""


def _traza(respuesta: str, motivos, usadas, vueltas, ms) -> dict:
    return {"version": "v3", "motivos": motivos, "herramientas": usadas, "vueltas": vueltas, "ms": ms, "fuentes_usadas": []}


# Pregunta el precio («¿cuánto está?», «¿a cuánto?»): ahí se repite la promo aunque ya se haya dicho.
RE_PIDE_PRECIO = re.compile(r"\b(precio|cuanto (cuesta|sale|esta|vale|es|seria|queda)|a cuanto|cuesta|vale|total)\b")


def conversar(req, fichas, consultar_stock, pie=None, permitir_reserva: bool = False, embed=None) -> dict:
    """Un turno de V3. `req` es un ChatIn; `fichas` el catálogo vivo; `consultar_stock` E.stock.consultar. `embed`: el
    embedder del agente (E.emb) para la FAQ de la tienda; None = conocimiento.EMBED."""
    t0 = time.time()
    mensaje = (req.mensaje or "").strip()
    f = ficha_mod.normalizar(getattr(req, "memoria", None))
    if getattr(req, "etapa", ""):
        f["etapa"] = req.etapa

    # La encuesta de WhatsApp queda en el historial como «📊 pregunta · Opción A / Opción B» (bot Go, replyPoll). Repite
    # la pregunta del mensaje anterior y sus opciones parecen dichas: el 12-10-2026 el modelo leyó «Elegante y discreta»
    # como la elección de la clienta («¡elegante y discreta será ideal!») sin que la eligiera. Se descarta.
    # Antes de leer el mensaje: memoria.leer consume la pendiente «otras_opciones» con el sí/no y la borra.
    oferta_foto = f.get("pendiente") == "otras_opciones"   # se le preguntó si quiere ver otros modelos
    hist = [_sin_opciones(t) for t in (getattr(req, "historial", []) or [])]
    try:
        req.historial = hist
    except (AttributeError, ValueError):
        pass
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
    antes = dict(f.get("sabemos") or {})      # para notar un cambio de idea (prenda, color)
    # «¿ya o lo coordinamos en diciembre?» (prospectos, octubre de 2026): «mejor en diciembre» NO es la fecha del evento.
    cfg_pros = prospectos.config(req)
    coord_pend = f.get("pendiente") == "coordinar"
    if coord_pend:
        f["pendiente"] = ""
    lectura = memoria.leer(f, mensaje, ahora=memoria.ahora_lima())
    f["etapa"] = _etapa(f, lectura)
    # Temperatura (fría / tibia / caliente) en cada turno: el bot Go la mira para avisar de una clienta caliente.
    dudas_nuevas = _temperatura(f, mensaje, lectura)
    hoy = memoria.ahora_lima().date()
    if coord_pend and cfg_pros:
        decision = prospectos.leer_respuesta(mensaje)
        if decision:
            for k in ("fecha", "fecha_iso"):        # la fecha del evento no cambia con «en diciembre»
                if antes.get(k):
                    f["sabemos"][k] = antes[k]
            if decision == "despues":
                base = prospectos.aviso(f["sabemos"], cfg_pros, hoy)
                fa = prospectos.fecha_de_mes(mensaje, base, prospectos._evento(f["sabemos"]), hoy) if base else None
                if fa:
                    prospectos.anotar(f, "despues", fa, memoria.ahora_lima())
                    memoria.temperatura(f, hoy)
                    log.info("V3 prospecto: coordinar el %s", fa)
                    return _respuesta(f, prospectos.cierre(fa, getattr(req, "cliente", "")), "responder", [], None,
                                      _traza("", [], ["plantilla:prospecto"], 0, _ms(t0)), req)
            else:
                prospectos.anotar(f, "ahora", None, memoria.ahora_lima())

    # 2. Enrutador barato.
    if RE_ASESORA.search(mensaje):
        return _respuesta(f, "", "asesora", [], None, _traza("", [], ["asesora"], 0, _ms(t0)), req)
    if RE_FUERA_GIRO.search(mensaje):
        return _respuesta(f, "Eso no es mi giro 😅 ¿Te ayudo con algo de la tienda?", "responder", [], None,
                          _traza("", [], [], 0, _ms(t0)), req)
    # 2c. Nombre repetido sin decidir («el Holly»: vestido y pantalón) y nada lo resuelve (foco, lo que el bot nombró):
    # se pregunta cuál, sin LLM. Antes el código elegía el primero del catálogo en silencio (15-10-2026).
    ctx_nombres = {"foco": f.get("producto") or "", "bot": ultimo_bot, "contesta_cual": bool(RE_PREGUNTO_CUAL.search(ultimo_bot))}
    amb = _ambiguas(mensaje, fichas, ctx_nombres)
    if amb and not _nombradas(mensaje, fichas, ctx_nombres):
        txt = nombres.pregunta_cual(amb[0], fichas)
        log.info("V3 nombre repetido: %s → %s", amb[0][0], amb[0][1])
        res = _respuesta(f, txt, "responder", [], None, _traza("", [], ["plantilla:cual"], 0, _ms(t0)), req)
        res["respuestas_rapidas"] = [{"label": next((x.nombre for x in fichas if x.codigo.upper() == c), c)} for c in amb[0][1][:3]]
        return res

    # 2b. Pasos del guion por plantilla (app/v3/plantillas.py): el dato esperado y nada más, sin pago en curso.
    en_pago = (bool((f.get("pedido") or {}).get("confirmado")) or getattr(req, "etapa", "") == "venta_confirmada"
               or getattr(req, "estado", "") == "esperando_pago")
    foto_hecho = getattr(req, "foto_hecho", "") or ""   # /foto: lo que ya se sabe de la foto que mandó
    # Conocimiento de la tienda (JMD Ventas, 15-10-2026): promos vigentes, FAQ, políticas y guion. Sin él, todo como siempre.
    conoc = conocimiento.leer(getattr(req, "conocimiento", None))
    faq = None if (foto_hecho or en_pago) else conocimiento.buscar_faq(conoc, mensaje, embed)
    promo_q = bool(conocimiento.vigentes(conoc)) and conocimiento.pregunta_promo(mensaje)
    if faq:
        log.info("V3 FAQ de la tienda: %s (%.3f)", faq["faq"]["id"], faq["score"])
    # Bienvenida por anuncio (Ajustes → «Bienvenida por anuncio»): presentación, foto del modelo, garantía y colores/tallas.
    if not _bots(hist) and not en_pago and not foto_hecho and getattr(req, "bienvenida_anuncio", False) \
            and (getattr(req, "desde_anuncio", False) or getattr(req, "anuncio", "")):
        res = _bienvenida_anuncio(f, req, fichas, consultar_stock, pie, conoc, t0)
        if res:
            return res
    # «Ver tallas» (el botón de la bienvenida o escrito así, solo): las tallas con stock del modelo en foco, sin LLM.
    if not en_pago and not foto_hecho and prospectos.RE_VER_TALLAS.match(memoria._plano(mensaje)) and f.get("producto"):
        txt = _tallas_en_foco(f, fichas, consultar_stock)
        if txt:
            return _respuesta(f, txt, "responder", [], None, _traza("", [], ["plantilla:tallas"], 0, _ms(t0)), req)
    # Evento lejano: «¿lo dejamos listo ya o lo coordinamos a <mes>?» una vez (prospectos; si ella preguntó algo, después).
    if not foto_hecho and "?" not in mensaje and not faq and not promo_q:
        fa = prospectos.toca_preguntar(f, cfg_pros, hoy, en_pago)
        if fa:
            log.info("V3 prospecto: pregunta (aviso %s)", fa)
            return _respuesta(f, prospectos.pregunta(f, fa, getattr(req, "cliente", "")), "responder", [], None,
                              _traza("", [], ["plantilla:prospecto"], 0, _ms(t0)), req)
    # 2a. Pide el catálogo o ver todo: el enlace de LA SECCIÓN que pide con su captura (14-10-2026, como la tienda).
    if not en_pago and not foto_hecho and colecciones.pide_catalogo(mensaje):
        res = _catalogo(f, mensaje, fichas, hist, req, t0)
        if res:
            return res
    if not en_pago and not foto_hecho and not oferta_foto and not faq and not promo_q:
        txt = ""
        if RE_SALUDO_SOLO.match(mensaje) and not _bots(hist):
            txt = plantillas.saludo(getattr(req, "cliente", ""), hist)
            plantillas._marca(f, "ocasion")
        else:
            txt = (plantillas.toque(mensaje, hist) or plantillas.ok_sin_dato(f, mensaje, hist)
                   or plantillas.responder(f, lectura.get("datos") or {}, mensaje, hist, getattr(req, "cliente", "")) or "")
        if txt:
            sin = _sin_insistir(txt, f, hist)
            if sin != txt:              # la plantilla repetía una pregunta ya hecha dos veces: se cambia por otra
                txt = _con_siguiente(sin, f, hist) if sin else _con_siguiente("¡Perfecto! 😊", f, hist)
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
    rec.poner_conocimiento(conoc)
    # «el primero», «la otra», «ese mismo», «el verde»: la prenda ya mostrada a la que se refiere, sin nombrarla.
    ref_cod, ref_hecho = _referencia(f, mensaje, fichas, ctx_nombres)
    if ref_cod:
        f["producto"] = ref_cod
        if (f.get("pedido") or {}).get("codigo") and not (f.get("pedido") or {}).get("confirmado"):
            ficha_mod.fijar_pedido(f, codigo=ref_cod)
    cambio_hecho = _cambio_de_idea(rec, f, mensaje, antes, lectura.get("datos") or {}, ctx_nombres)
    if ref_hecho:
        cambio_hecho = (ref_hecho + "\n" + cambio_hecho).strip()
    confirma_hecho = "" if pago_ok else _confirma_pedido(rec, f, mensaje, ultimo_bot, permitir_reserva)
    if confirma_hecho:
        pago_ok = rec.hechos["pago_ok"] = True
        f["senales"] = (list(f.get("senales") or []) + ["compra"])[-20:]
        memoria.temperatura(f, memoria.ahora_lima().date())
    rec.color_pedido = (f.get("sabemos") or {}).get("color") or ""
    # Guion de noche: «¿llamar la atención o elegante y discreta?» → grupo de colores (vibrante | sobrio) que la tienda
    # define (jmdventas, Ajustes → Colores; si no manda nada, seed/colores_grupos.json). Ver app/colores.py (14-10-2026).
    rec.grupo = colores.GRUPO_DE_ESTILO.get((f.get("sabemos") or {}).get("estilo") or "", "")
    rec.grupos_cfg = getattr(req, "colores_grupos", None) or None
    # Pide ver los colores de la prenda en foco, o uno de ellos: las fotos de ESE modelo, una por color, las elige el código.
    colores_hecho = "" if (foto_hecho or oferta_foto or pago_ok) else _fotos_de_colores(rec, f, mensaje, ultimo_bot)
    clienta = " ".join(memoria._plano(x.get("texto", "") if isinstance(x, dict) else getattr(x, "texto", ""))
                       for x in hist if (x.get("rol", "") if isinstance(x, dict) else getattr(x, "rol", "")) == "cliente")
    hecho = _precalculo(rec, {**(lectura.get("datos") or {}),
                              "otra_persona": bool(plantillas.RE_OTRA_PERSONA.search(f"{clienta} {memoria._plano(mensaje)}")
                                                   or (f.get("sabemos") or {}).get("para_quien")),
                              "para_quien": (f.get("sabemos") or {}).get("para_quien") or "",
                              "ultimo_bot": ultimo_bot, "mensaje": memoria._plano(mensaje),
                              "estilo_ya": (f.get("sabemos") or {}).get("estilo") or "",
                              "producto": getattr(req, "producto", "") or f.get("producto")
                                          or ((f.get("mostrados") or [""])[-1])})
    # La prenda en foco (la de su foto o la última mostrada) queda respaldada aunque no haya pedido: el validador rechazaba
    # «el Vestido Salome V50…» por «código no respaldado» cuando el modelo no llamaba a ninguna herramienta (13-10-2026).
    foco = (f.get("producto") or "").strip().upper()
    if foco and rec._ficha(foco):
        rec.ejecutar("stock", {"codigo": foco})
    if conoc is not None:
        conoc_hecho = _hechos_conocimiento(rec, f, mensaje, getattr(req, "producto", "") or f.get("producto")
                                           or ((f.get("mostrados") or [""])[-1]), faq, ctx_nombres)
        if conoc_hecho:
            hecho = (hecho + "\n" + conoc_hecho).strip()
    pedido = _pedido_en_curso(rec, req, f)
    if pedido:
        hecho = (hecho + "\n" + pedido).strip()
    if confirma_hecho:
        hecho = (hecho + "\n" + confirma_hecho).strip()
    if cambio_hecho:
        hecho = (hecho + "\n" + cambio_hecho).strip()
    if colores_hecho:
        hecho = (hecho + "\n" + colores_hecho).strip()
    # Pide las fotos de los modelos que el bot acaba de nombrar («¿tienes fot?», «de todos», «los tres») sin decir cuál:
    # se mandan esos, no otro (13-10-2026: nombró Maura, Milan y Roberta y mandó el Blear).
    from .herramientas import RE_PIDE_FOTO, RE_PIDE_VARIAS
    nombrados_bot = [c for c in _nombradas(ultimo_bot, rec.fichas) if rec._ficha(c)][:3]
    if len(nombrados_bot) >= 1 and not _nombradas(mensaje, rec.fichas) and not foto_hecho and not rec.colores_ok \
            and (RE_PIDE_FOTO.search(memoria._plano(mensaje)) or RE_PIDE_VARIAS.search(memoria._plano(mensaje))):
        rec.contesta_cual = rec.varias_ok = True
        rec.ejecutar("enviar_fotos", {"codigos": nombrados_bot})
        hecho = (hecho + "\nPIDE VER LOS MODELOS QUE NOMBRASTE: ya se le enviaron las fotos de " + _fotos_con_color(rec)
                 + " (NO llames a enviar_fotos ni cambies de modelo). Preséntalos con nombre, precio y el color REAL de cada "
                 "foto (si pidió otro color y un modelo no lo tiene, dilo) y pregúntale cuál le gusta más.").strip()
    # Tras una foto que no se reconoció se le preguntó si quiere ver otros modelos: las fotos salen SOLO con su «sí».
    if oferta_foto:
        codigos = [c for c in (f.get("foto_parecidas") or []) if rec._ficha(c)][:3]
        if RE_SI.match(mensaje):
            rec.contesta_cual = True
            if codigos:                               # los más parecidos a su foto
                rec.varias_ok = True
                rec.ejecutar("enviar_fotos", {"codigos": codigos})
                hecho = (hecho + "\nPIDIÓ VER OTROS MODELOS: ya se le enviaron las fotos de " + _fotos_con_color(rec)
                         + " (NO llames a enviar_fotos). Preséntalos por nombre, precio y el color real de cada foto y "
                         "pregúntale cuál le gusta más.").strip()
            else:                                     # sin candidatos: que elija el modelo con lo que ella contó
                hecho = (hecho + "\nPIDIÓ VER OTROS MODELOS: muestra UNA prenda que cuadre con lo que contó (enviar_fotos), "
                         "nómbrala con su precio y pregúntale qué le gustó.").strip()
            f["foto_parecidas"], f["pendiente"] = [], ""
        elif RE_NO.match(mensaje):
            # Sin LLM: tendía a despedirse («aquí estaré si necesitas ayuda»). Sigue el guion con la pregunta que falte.
            f["foto_parecidas"], f["pendiente"] = [], ""
            txt = _con_siguiente("¡De acuerdo! 😊", f, hist)
            if "?" not in txt:
                txt = "¡De acuerdo! 😊 Cuéntame, ¿para qué ocasión estás buscando?"
            log.info("V3 plantilla: %s", txt[:80])
            return _respuesta(f, txt, "responder", [], None, _traza("", [], ["plantilla:no_otros"], 0, _ms(t0)), req)
    if foto_hecho:
        hecho = (hecho + "\n" + foto_hecho).strip()
        rec.oferta_ok = bool(getattr(req, "foto_oferta", False))
        if getattr(req, "foto_codigo", ""):      # respalda código, precio y tallas: el validador los exigía de una herramienta
            rec.ejecutar("stock", {"codigo": req.foto_codigo})
    paso = "" if (lectura.get("datos") or {}).get("horario") else _paso_guion(f, getattr(req, "historial", []) or [], pago_ok)
    if re.search(r"\bsepar", memoria._plano(mensaje)) and not (f.get("producto") or f.get("mostrados")
                                                               or (f.get("pedido") or {}).get("codigo")) \
            and not _nombradas(mensaje, rec.fichas, ctx_nombres):
        rec.sin_cual = True
        paso = ("QUIERE SEPARAR SIN DECIR QUÉ PRENDA: pregúntale cuál (el nombre del modelo o una foto); no inventes "
                "una ni mandes fotos. " + (paso or "")).strip()
    elif RE_ESTE_SIN_CUAL.search(mensaje) and not _nombradas(mensaje, rec.fichas, ctx_nombres) and not ref_cod:
        rec.sin_cual = True
        paso = ("PREGUNTA POR «ESTE» MODELO SIN DECIR CUÁL: pídele la foto o el nombre del modelo que vio; no "
                "mandes fotos ni adivines. " + (paso or "")).strip()
    elif RE_CATALOGO.search(mensaje):
        paso = ("PIDIÓ EL CATÁLOGO O LA PÁGINA: usa enviar_catalogo con la sección que pide (o «all» si no dice "
                "cuál) y pregúntale cuál le gustó; no escribas tú el enlace. " + (paso or "")).strip()
    elif rec._pide_modelos() and not pago_ok and not rec.colores_ok:
        paso = ("ELLA PIDIÓ VER MODELOS: muéstrale UNA prenda que cuadre con lo que contó (enviar_fotos), nómbrala con "
                "su precio y pregunta qué le gustó; el resto del guion sigue después. " + (paso or "")).strip()
    if paso:
        hecho = (hecho + "\n" + paso).strip()
    tono = "" if pago_ok else _tono(f, hist)
    if tono:
        hecho = (hecho + "\n" + tono).strip()
    for d in dudas_nuevas:
        if d in TEMOR_HECHO:
            hecho = (hecho + "\n" + TEMOR_HECHO[d]).strip()
    pres = "" if pago_ok else _hecho_presupuesto(f, req, rec.fichas)
    if pres:
        hecho = (hecho + "\n" + pres).strip()
    if "talla" in dudas_nuevas and not rec._pide_modelos():
        rec.bloquear_fotos = True          # miedo a que no le quede: se le contesta, no se le cambia de prenda
    # El precio de un modelo se dice una vez: al cambiar de color no se repite, salvo que lo pregunte (15-10-2026).
    rec.montos_ya, ya_nombres = _precios_ya_dichos(rec, f, mensaje, conoc, pago_ok or bool(confirma_hecho))
    if ya_nombres:
        hecho = (hecho + f"\nYA LE DISTE EL PRECIO de {', '.join(ya_nombres)}: no lo repitas (tampoco al cambiar de color o "
                 "talla) salvo que ella lo pregunte.").strip()
    # Precios solo si los pregunta (JMD Ventas → Ajustes, 15-10-2026): en el cierre de la compra van siempre.
    sin_precios = not precios.permitido(bool(getattr(req, "precios_solo_si_pregunta", False)), mensaje, getattr(req, "estado", ""),
                                        getattr(req, "etapa", ""), f.get("pedido"), pago_ok or bool(confirma_hecho))
    rec.sin_precios = sin_precios
    msgs = prompt.construir(f, getattr(req, "historial", []) or [], mensaje, getattr(req, "canal", ""), hecho=hecho,
                            cliente=getattr(req, "cliente", ""), pago=pago_ok, conoc=conoc, sin_precios=sin_precios)
    # Respuesta a una foto: todo lo necesario ya viene en el hecho; una sola vuelta (sin herramientas) ahorra 3–5 s.
    vueltas_max = 1 if foto_hecho else 0
    salida = cliente_llm.responder(msgs, rec.esquema(), rec.ejecutar, max_vueltas=vueltas_max)
    out = prompt.leer_salida(salida["contenido"])
    _foto_pedida(rec, f, mensaje)
    _recortar_fotos(rec, mensaje)
    _foto_de_la_nombrada(rec, out["respuesta"])
    motivos = validador.validar(out["respuesta"], rec.hechos, out["pregunta_hecha"], _respondidas(f))
    motivos += _pregunta_sabida(out["respuesta"], f)
    motivos += _no_avanza(out["respuesta"], "" if rec.sigue else mensaje, pago_ok, rec)
    motivos += _repite(out["respuesta"], getattr(req, "historial", []) or [])
    motivos += _ofrece(out["respuesta"], rec)
    motivos += _despedida(out["respuesta"], "" if rec.sigue else mensaje)
    motivos += _precio_sin_promo(out["respuesta"], rec)
    motivos += _precio_no_pedido(out["respuesta"], rec)
    motivos += _promo_repetida(out["respuesta"], rec)
    motivos_1, salida2 = bool(motivos), None
    if motivos:
        log.info("V3 rechazo 1: %s | %s", "; ".join(motivos), out["respuesta"][:160])

    # 4. Un rechazo: regenerar con los motivos. Dos: caer a V1.
    if motivos:
        msgs2 = msgs + [{"role": "user", "content": "Tu respuesta se rechazó por: " + "; ".join(motivos) +
                         ". Corrígela y vuelve a responder SOLO el JSON."}]
        salida2 = cliente_llm.responder(msgs2, rec.esquema(), rec.ejecutar, max_vueltas=vueltas_max)
        out = prompt.leer_salida(salida2["contenido"])
        motivos = validador.validar(out["respuesta"], rec.hechos, out["pregunta_hecha"], _respondidas(f))
        motivos += _no_avanza(out["respuesta"], "" if rec.sigue else mensaje, pago_ok, rec)
        motivos += _repite(out["respuesta"], getattr(req, "historial", []) or [])
        motivos += _ofrece(out["respuesta"], rec)
        motivos += _despedida(out["respuesta"], "" if rec.sigue else mensaje)
        motivos += _precio_sin_promo(out["respuesta"], rec)
        motivos += _precio_no_pedido(out["respuesta"], rec)
        motivos += _promo_repetida(out["respuesta"], rec)
        if motivos and all(m.startswith(("pregunta ya respondida", "más de una pregunta", "repite una frase",
                                         "no avanza", "ofrece modelos", "se despidió", "precio sin la promo",
                                         "dio un precio", "repite el precio", "repite la promo")) for m in motivos):
            if any(m.startswith("repite la promo") for m in motivos):
                out["respuesta"] = _sin_promo_repetida(out["respuesta"], rec)
            if any(m.startswith("dio un precio") for m in motivos):
                out["respuesta"] = precios.quitar_de_texto(out["respuesta"])
            if any(m.startswith("repite el precio") for m in motivos):
                out["respuesta"] = precios.quitar_de_texto(out["respuesta"], solo=rec.montos_ya)
            if any(m.startswith("precio sin la promo") for m in motivos):
                out["respuesta"] = _con_precio_promo(out["respuesta"], rec)
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
    if rec.bloquear_fotos:              # objeción al envío: ninguna foto, venga de donde venga
        rec.fotos = []
    if not pago_ok:
        sin = _sin_insistir(out["respuesta"], f, hist)
        if sin != out["respuesta"]:
            log.info("V3 sin insistir: %s → %s", out["respuesta"][:120], sin[:120])
            out["respuesta"], out["pregunta_hecha"] = _con_siguiente(sin or "¡Perfecto! 😊", f, hist), ""
    res = _cierra(f, rec, out, salida["usadas"], salida["vueltas"], _ms(t0), req)
    res["v3"]["uso"] = cliente_llm.sumar_uso(salida.get("uso"), (salida2 or {}).get("uso") if motivos_1 else None)
    res["fuentes_usadas"] = res["v3"]["fuentes_usadas"] = _fuentes_usadas(rec, res["respuesta"])
    _anotar_promos_dichas(f, conoc, res)
    if conoc is not None:
        res["v3"]["conocimiento"] = {"empresa": conoc.empresa, "version": conoc.version,
                                     "en_prompt": conocimiento.ids_prompt(conoc)}
    return res


def _recortar_fotos(rec: Recolector, mensaje: str) -> None:
    """Lo que se LE MANDA a la clienta: una foto, salvo que pida varias. La herramienta marca todo lo que
    el modelo pidió (eso lo necesita `enviar_fotos` tal cual para que un test con 2 códigos no pierda el 2°);
    el recorte de cuántas fotos salen es cosa de esta capa, que sí conoce el mensaje de la clienta."""
    from .herramientas import RE_PIDE_VARIAS, MAX_FOTOS, MAX_FOTOS_COLORES
    tope = MAX_FOTOS if rec.varias_ok or RE_PIDE_VARIAS.search((mensaje or "").lower()) else 1
    if rec.colores_ok:                   # los colores de UNA prenda: van todas las que eligió el código
        tope = max(tope, MAX_FOTOS_COLORES)
    if rec.catalogo_ok:                  # «vestidos» a secas son dos secciones (largos y cortos)
        tope = max(tope, 2)
        rec.fotos = [x for x in rec.fotos if x.get("coleccion")] + [x for x in rec.fotos if not x.get("coleccion")]
    if len(rec.fotos) > tope:
        rec.fotos = rec.fotos[:tope]


def _foto_pedida(rec: Recolector, f: dict, mensaje: str) -> None:
    """El código manda la foto que ella pidió y el modelo olvidó marcar: la prenda que nombra, o la que tiene en foco."""
    from .herramientas import RE_PIDE_FOTO
    m = mensaje.lower()
    if rec.fotos or not RE_PIDE_FOTO.search(m):
        return
    plano = memoria._plano(mensaje)
    nombrada = _nombradas(mensaje, rec.fichas, {"foco": f.get("producto") or ""})
    if nombrada:
        rec.ejecutar("enviar_fotos", {"codigos": nombrada[:1]})
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
    # Dijo cómo quiere sentirse (noche) y pide ver modelos sin nombrar color: los del grupo de colores que eligió.
    if rec.grupo and not rec.color_pedido and rec._pide_modelos() and not rec.colores_ok:
        lista = colores.disponibles(rec.fichas, rec.grupo, rec.grupos_cfg)
        if lista:
            partes.append(f"HECHO: quiere verse {datos.get('estilo_ya') or rec.grupo}: ofrécele prendas en colores "
                          f"{colores.NOMBRE_GRUPO[rec.grupo]} ({', '.join(lista[:12])}). buscar_catalogo las trae primero y "
                          "la foto irá en ese color: nómbralo.")
    num = RE_TALLA_NUMERO.search(memoria._plano(rec.mensaje))
    if num and not datos.get("talla") and 2 <= int(num.group(1)) <= 60:
        partes.append(f"HECHO YA VERIFICADO: dijo talla {num.group(1)} (en número). En Baruka las tallas son S, M y L, en "
                      "medidas peruanas (XS, XL y 2XL bajo pedido en algunos modelos). NO digas que tenemos la "
                      f"{num.group(1)} ni la des por buena: explícale nuestras tallas y ofrécele la tabla de medidas (o "
                      "pregúntale su busto y cintura) para ubicar la suya.")
    envio, ciudad = datos.get("envio"), datos.get("ciudad")
    if envio and re.search(r"\b(envio|envian|envias|mandan|delivery|llega|courier|olva|shalom|agencia)\b|^\s*(a |para )?\w+\s*$",
                           memoria._plano(rec.mensaje)):
        lugar = f" a {ciudad}" if ciudad and ciudad.lower() not in ("lima", "provincia") else ""
        costo = ("S/ 15 por Olva Courier, con entrega en su dirección" if envio == "lima" else
                 "S/ 15: un courier lleva el paquete a la agencia de buses" if envio == "agencia" else
                 "S/ 20 por Olva Courier o Shalom, hasta la agencia de su ciudad (pregúntale cuál prefiere)")
        partes.append(f"HECHO YA VERIFICADO: dijo el envío{lugar} ({envio}). Dile en ESTE mensaje el costo: {costo}. "
                      "Luego sigue con lo que falte del guion.")
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
    if horario == "noche" and datos.get("estilo_ya"):
        partes.append(f"HECHO: el evento es de NOCHE y ya eligió cómo quiere sentirse ({datos['estilo_ya']}). No vuelvas a "
                      "preguntarlo: sigue con lo que falte del guion.")
    elif horario == "noche":
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
    # Objeción al envío («¿tan caro?», «¿no hay rebaja?», «¿y si se pierde?») cuando se habla del envío: la respuesta del
    # guion (mejores servicios de entrega, precio fijo) y de vuelta a la pregunta pendiente (13-10-2026, pedido del usuario).
    if RE_OBJECION_ENVIO.search(datos.get("mensaje", "")) and re.search(r"envio|olva|shalom|lima o provincia|agencia",
                                                                        f"{datos.get('ultimo_bot', '')} {datos.get('mensaje', '')}"):
        rec.bloquear_fotos = True
        partes.append("OBJECIÓN AL ENVÍO. Dile casi textual: «Te entiendo 😊 Trabajamos con los mejores servicios de entrega "
                      "(Olva Courier y Shalom): tu pedido llega seguro y sin ningún problema, te lo garantizamos. El costo del "
                      "envío es el de la empresa de entrega y es fijo: S/ 20 a provincia, S/ 15 a Lima.» y vuelve a la pregunta "
                      "que quedó pendiente (¿Olva o Shalom?, ¿Lima o provincia?). NO pidas otros datos (talla, estatura, fecha) y NO "
                      "menciones ni ofrezcas prendas.")
    if datos.get("para_quien") and any("casi textual" in x for x in partes):
        partes.append(f"OJO: la prenda es para su {datos['para_quien']}: habla de ella en tercera persona («su talla», "
                      "«¿cuánto mide ella?»), nunca «tu».")
    elif datos.get("otra_persona") and any("casi textual" in x for x in partes):
        # 12-10-2026: «sí está diseñado para TU estatura» a quien compraba para su hermana.
        partes.append("OJO: compra para OTRA persona: di esas frases en tercera persona («su estatura», «su talla», "
                      "«cómo quiere sentirse ella»), nunca «tu».")
    color = datos.get("color")
    # Pregunta por un color de la prenda en foco: ya va su foto en ese color (_fotos_de_colores); buscar otras prendas de
    # ese color era ofrecerle el Enterizo Miara cuando preguntaba por el verde olivo del Salome (14-10-2026).
    if color and not rec.colores_ok:
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


def _hechos_conocimiento(rec: Recolector, f: dict, mensaje: str, producto: str, faq: dict | None, ctx: dict) -> str:
    """Lo que el conocimiento de la tienda (JMD Ventas, 15-10-2026) ya deja verificado para este turno:

    - FAQ de la tienda que contesta el mensaje → su respuesta, casi textual.
    - Prenda en foco (o nombrada) con una promo vigente → «HECHO YA VERIFICADO: PROMO …» con precio normal, precio con
      promo (calculado por código), condiciones y vigencia, UNA vez por conversación (`f["promos_dichas"]`); después solo
      si pregunta por la promo o el precio. La promo queda respaldada para el validador aunque no se mencione.
    - «¿tienen promo?» sin prenda → las vigentes (su `respuesta_promo` o un resumen).
    - Pide descuento de una prenda que NO entra → `respuesta_fuera` de la tienda o que su precio es el publicado.
    Sin promos vigentes no dice nada de descuentos: la veda de siempre la siguen aplicando el prompt y el validador."""
    partes = []
    if faq:
        rec.faq_usada = faq["faq"]["id"]
        partes.append(f"RESPUESTA DE LA TIENDA (pregunta frecuente): {faq['faq']['respuesta']} Dila casi textual (solo "
                      "ajusta el tuteo o une las frases), sin agregar datos que no estén ahí, y luego sigue con el paso del guion.")
    vig = conocimiento.vigentes(rec.conoc)
    if not vig:
        return "\n".join(partes)
    plano = memoria._plano(mensaje)
    pide_promo = conocimiento.pregunta_promo(mensaje)
    nombradas = _nombradas(mensaje, rec.fichas, ctx)
    cod = nombradas[0] if len(nombradas) == 1 else ("" if nombradas else (producto or "").strip().upper())
    ficha = rec._ficha(cod) if cod else None
    dichas = f.setdefault("promos_dichas", [])
    if ficha is not None:
        aplican = conocimiento.promos_para(rec.conoc, ficha)
        if aplican:
            for p in aplican:                      # respaldo para el validador, la mencione o no
                rec.respaldar_promo(p, [ficha])
            rec.usadas.append("promocion_vigente")
            nuevas = [p for p in aplican if p["id"] not in dichas]
            if nuevas or pide_promo or RE_PIDE_PRECIO.search(plano):
                lineas = " ".join(conocimiento.linea_promo(p, ficha) for p in aplican)
                apertura = next((p["mensaje_apertura"] for p in nuevas if p["mensaje_apertura"]), "")
                partes.append(f"HECHO YA VERIFICADO: {lineas} " + (
                    "Menciónala en este mensaje con estas cifras exactas (una sola vez en la conversación)"
                    + (f"; puedes abrir con: «{apertura}»" if apertura else "") + "." if nuevas else
                    "Usa estas cifras exactas si habla del precio o de la promo."))
                dichas.extend(p["id"] for p in nuevas)
            else:   # ya se dijo: no se repite, pero si da el precio, que sea el de la promo
                rec.promos_no_repetir = [p for p in aplican]
                lineas = " ".join(conocimiento.linea_promo(p, ficha) for p in aplican)
                partes.append(f"HECHO YA VERIFICADO (la promo ya se le dijo; no la repitas): {lineas} Si mencionas el precio "
                              "de esta prenda, da el precio con la promo, nunca solo el normal.")
        elif pide_promo:
            fuera = next((p["respuesta_fuera"] for p in vig if p["respuesta_fuera"]), "")
            partes.append(f"HECHO YA VERIFICADO: el {ficha.nombre} NO entra en ninguna promo vigente: su precio es el publicado "
                          f"y no tiene descuento. Dile casi textual: «{fuera or f'El {ficha.nombre} no entra en la promo: su precio es el publicado 😊'}». "
                          "No ofrezcas ningún otro descuento.")
    elif pide_promo:
        for p in vig:
            rec.respaldar_promo(p, [x for x in rec.fichas if conocimiento.cubre(p, x)])
        rec.usadas.append("promocion_vigente")
        lista = " ".join(conocimiento.resumen_promo(p) for p in vig)
        partes.append(f"PREGUNTA POR PROMOS. HECHO YA VERIFICADO (son las únicas vigentes): {lista} Díselo casi textual y "
                      "pregúntale qué prenda le interesa; no inventes otras promos ni otras cifras.")
        dichas.extend(p["id"] for p in vig if p["id"] not in dichas)
    return "\n".join(partes)


def _anotar_promos_dichas(f: dict, conoc, res: dict) -> None:
    """La promo cuenta como dicha si salió en el texto (por la herramienta promocion_vigente, sin pasar por el hecho del
    foco) o en el pie de una foto («con la promo «X»»). Antes solo la anotaba el hecho y se anunciaba dos veces (15-10-2026)."""
    vig = conocimiento.vigentes(conoc) if conoc is not None else []
    if not vig:
        return
    dichas = f.setdefault("promos_dichas", [])
    pies = " ".join((s.get("pie") or "") + " " + (s.get("titulo") or "") for s in (res.get("sugerencias") or []) if isinstance(s, dict))
    for p in vig:
        en_pie = p.get("titulo") and f"«{p['titulo']}»" in pies
        if (p["id"] in (res.get("fuentes_usadas") or []) or en_pie) and p["id"] not in dichas:
            dichas.append(p["id"])
    f["promos_dichas"] = dichas[-30:]


def _promo_repetida(texto: str, rec: Recolector) -> list[str]:
    """La promo ya dicha que el modelo vuelve a anunciar («¡Fiestas de Octubre! Esta semana hay 20 %…») sin que ella
    pregunte por promos ni por el precio (15-10-2026, prueba local). Dar el precio con promo sigue valiendo."""
    ps = getattr(rec, "promos_no_repetir", None) or []
    if not ps or not texto:
        return []
    plano = memoria._plano(texto)
    for p in ps:
        titulo = memoria._plano(p.get("titulo") or "")
        pct = p.get("valor") if p.get("mecanica") == "porcentaje" else None
        if (titulo and titulo in plano and not RE_PRECIO_PLANO.search(plano)) or \
                (pct and re.search(rf"\b{int(pct)}\s?%", plano)):
            return [f"repite la promo «{p.get('titulo')}» que ya le dijiste: no la anuncies otra vez"]
    return []


RE_PRECIO_PLANO = re.compile(r"s/\s?\d")


def _sin_promo_repetida(texto: str, rec: Recolector) -> str:
    """Arreglo de la segunda vuelta: se quitan las frases que vuelven a anunciar la promo."""
    frases = [x for linea in (texto or "").split("\n") for x in precios.RE_CORTE.split(linea)]
    quedan = [x for x in frases if x and x.strip() and not _promo_repetida(x, rec)]
    return " ".join(quedan).strip() or texto


def _precios_dichos(texto: str) -> set[float]:
    return {round(float(m.group(1).replace(",", ".")), 2) for m in validador.RE_PRECIO.finditer(texto or "")}


def _precio_no_pedido(texto: str, rec: Recolector) -> list[str]:
    """La tienda quiere precios solo si la clienta los pregunta y aquí no preguntó: ningún monto de prenda (15-10-2026).
    Y, siempre, el precio de un modelo ya dicho no se repite (al cambiar de color) si ella no lo pregunta."""
    from .herramientas import ENVIOS
    env = {round(e, 2) for e in ENVIOS}
    if getattr(rec, "sin_precios", False):
        malos = [m for m in _precios_dichos(texto) if m not in env]
        return [f"dio un precio (S/ {malos[0]:g}) sin que ella lo preguntara: quita los precios en soles"] if malos else []
    rep = sorted(m for m in _precios_dichos(texto) & set(getattr(rec, "montos_ya", None) or ()) if m not in env)
    return [f"repite el precio (S/ {rep[0]:g}) que ya le diste: no lo repitas si ella no lo pregunta"] if rep else []


def _precios_ya_dichos(rec: Recolector, f: dict, mensaje: str, conoc, en_compra: bool) -> tuple[set, list[str]]:
    """Montos y nombres de los modelos cuyo precio ya se le dio (memoria `precios_dichos`). Vacío si ella pregunta el
    precio ahora o si está cerrando la compra (resumen, total, pago)."""
    dichos = [c for c in (f.get("precios_dichos") or []) if isinstance(c, str)]
    if not dichos or en_compra or precios.pregunta_precio(mensaje) or \
            precios.permitido(True, "", "", f.get("etapa", ""), f.get("pedido")):
        return set(), []
    montos, nombres_ = set(), []
    for c in dichos[-5:]:
        fx = rec._ficha(c)
        if fx is not None:
            montos |= precios.montos(fx, conoc)
            nombres_.append(fx.nombre)
    return montos, nombres_


# Señales de temperatura sin el clasificador de V1: comprar o separar, preguntar el precio, o preguntar por talla, color,
# tela o stock (interés). Las frías («solo estoy viendo», «más adelante») y la urgencia las lee memoria.anotar_senales.
RE_COMPRA = re.compile(r"\b(lo|la|los|las) (quiero|compro|llevo|separo)\b|\bquiero (comprar|separar|llevar|pedir|hacer (el|mi) "
                       r"pedido)|\bme (lo|la) (llevo|separas|separan|apartas)|\bsepar(a|e)me(l[oa])?\b|\bcomo (pago|compro|hago "
                       r"(el|mi) pedido)|\bdonde (pago|deposito|yapeo)")
RE_INTERES = re.compile(r"\b(tallas?|colou?r(es)?|tela|material|stock|disponible|hay en|tienes en|tienen en|medidas?|largo)\b")


# Temores o dudas (dato 7 del guion de la tienda): se anotan en `objeciones` para la asesora (ficha y alerta del panel).
DUDAS = (("precio", re.compile(r"\b(muy |bien |algo )?car[oa]\b|no me alcanza|fuera de mi presupuesto|mas barat")),
         ("confianza", re.compile(r"\bestafa|\bconfiable|\bdesconfi|\bpagar antes|\bes seguro (pagar|comprar)|\bme da miedo pagar")),
         ("talla", re.compile(r"\bsi no me (queda|entra)|\bno (se|creo) (si|que) me (quede|entre)|\bmiedo (a )?que no me quede")),
         ("tiempo", re.compile(r"\bllega(ra)? a tiempo|\bcuanto (demora|tarda)|\bno (llegue|alcance) a tiempo")),
         ("pensarlo", re.compile(r"\b(lo|la) (voy a )?pens(o|ar)\b|\bdejame pensarlo|\blo pensare")),
         ("consultar", re.compile(r"\b(consultar|preguntar)(lo)? (con|a) (mi|mis)\b")))


def _temperatura(f: dict, mensaje: str, lectura: dict) -> list[str]:
    """Anota señales, temperatura y temores de este mensaje; devuelve los temores que trae (para contestarlos)."""
    plano = memoria._plano(mensaje)
    obj = f.setdefault("objeciones", [])
    dudas = [clave for clave, rx in DUDAS if rx.search(plano)]
    for clave in dudas:
        if clave not in obj:
            obj.append(clave)
    intent = ("intencion_compra" if RE_COMPRA.search(plano) else "consulta_precio" if precios.pregunta_precio(mensaje)
              else "consulta_disponibilidad" if RE_INTERES.search(plano) else "")
    memoria.anotar_senales(f, mensaje, intent, "alta", cita=bool(lectura.get("es_cita") and (f.get("sabemos") or {}).get("cita")))
    memoria.temperatura(f, memoria.ahora_lima().date())
    return dudas


# Qué hacer con cada temor (dato 7 del guion): contestarlo con datos de la tienda, no cambiar de tema ni de prenda.
TEMOR_HECHO = {
    "talla": "TEMOR: que no le quede. Tranquilízala con datos: la tabla de medidas del modelo en foco (si falta su medida, "
             "pídela) y la política de cambios de la tienda si la tienes. NO le muestres otra prenda.",
    "confianza": "TEMOR: desconfía de pagar antes. Tranquilízala solo con lo que la tienda ofrece (showroom con cita, "
                 "comprobante, política de cambios); no inventes garantías.",
    "tiempo": "TEMOR: que no llegue a tiempo. Dile los tiempos de envío de los datos de la tienda y que lo ideal es separarlo ya.",
    "precio": "TEMOR: el precio. Resalta el valor (tela, calce, acabado) y, si una promo vigente la cubre, el precio con la "
              "promo; no inventes descuentos.",
}


def _tono(f: dict, historial=()) -> str:
    """El tono según la temperatura (guion de la tienda, 15-10-2026): caliente = el evento es pronto («para la otra
    semana») o quiere comprar: urgencia amable; fría con fecha lejana (p. ej. en 3 meses): acompañarla sin presión."""
    t, mot = f.get("temperatura") or "frio", f.get("temperatura_motivo") or ""
    dias = memoria.dias_hasta((f.get("sabemos") or {}).get("fecha_iso"), memoria.ahora_lima().date())
    if t == "caliente" and plantillas.ya_hubo_escasez(historial):
        return (f"CLIENTA CALIENTE ({mot}). La urgencia YA se le dijo: no la repitas (nada de stock, «tenerlo listo antes» ni "
                "«¿no lo consideras?»). Sé concreta y ayúdala a decidir: resuelve su duda y avanza hacia separar la prenda.")
    if t == "caliente":
        return (f"CLIENTA CALIENTE ({mot}). Lleva la conversación hacia separar la prenda: sé concreta y crea urgencia "
                "amable UNA vez (tenerlo listo antes, mientras hay stock, por si necesita un ajuste), sin agobiarla.")
    if t == "frio" and dias is not None and dias > 30:
        return (f"CLIENTA FRÍA ({mot}): todavía falta mucho para su evento. Acompáñala con calidez y comentarios sobre el "
                "look y la ocasión; sin presión, sin urgencia y sin pedirle que lo separe ya. Mantén viva la conversación "
                "con una pregunta suave.")
    return ""


def _precio_sin_promo(texto: str, rec: Recolector) -> list[str]:
    """Da el precio normal de una prenda en promo sin el precio con promo (15-10-2026: «el Vestido Salome cuesta S/ 290»
    con la promo del 20 % vigente y ya dicha). El precio normal solo puede ir junto al de la promo."""
    dichos = _precios_dichos(texto)
    faltan = [(n, p) for n, p in rec.con_promo.items() if n in dichos and p not in dichos]
    return [f"precio sin la promo: dijiste S/ {n:.2f} y con la promo vigente es S/ {p:.2f}; da el precio con promo"
            for n, p in faltan[:1]]


def _con_precio_promo(texto: str, rec: Recolector) -> str:
    """Arreglo de la segunda vuelta: «S/ 290» → «S/ 290 (con la promo, S/ 232)»."""
    for n, p in rec.con_promo.items():
        if p in _precios_dichos(texto):
            continue
        texto = re.sub(r"(S/\.?\s*" + re.escape(f"{n:g}") + r"(?:[.,]00)?)(?!\d|[.,]\d)", lambda m: f"{m.group(1)} (con la promo, S/ {p:.2f})",
                       texto, count=1)
    return texto


def _fuentes_usadas(rec: Recolector, texto: str) -> list[int]:
    """Ids de las fuentes en las que se apoyó la respuesta de ESTE turno (contrato §3): las promos respaldadas que el texto
    de verdad menciona (por su título, una palabra de descuento o un precio que solo existe con la promo) y la FAQ usada.
    Las políticas y el guion van en el prompt siempre: no cuentan como «usadas»."""
    out = []
    if rec.promos_usadas and texto:
        plano = memoria._plano(texto)
        por_id = {p["id"]: p for p in conocimiento.vigentes(rec.conoc)}
        dice_descuento = bool(conocimiento.RE_MENCIONA_DESCUENTO.search(texto))
        precios = {round(float(m.group(1).replace(",", ".")), 2) for m in validador.RE_PRECIO.finditer(texto)}
        titulos = [pid for pid in rec.promos_usadas if por_id.get(pid, {}).get("titulo")
                   and memoria._plano(por_id[pid]["titulo"]) in plano]
        for pid in rec.promos_usadas:
            p = por_id.get(pid)
            if p is None:
                continue
            solo_promo = {round(x, 2) for x in rec.precios_promo.get(pid, set())}
            if pid in titulos or (dice_descuento and not titulos) or (precios & solo_promo):
                out.append(pid)
    if getattr(rec, "faq_usada", None) is not None:
        out.append(rec.faq_usada)
    return out


def _fotos_de_colores(rec: Recolector, f: dict, mensaje: str, ultimo_bot: str) -> str:
    """Pide ver los colores de la prenda en foco («enviame los colores en foto») o uno de ellos («¿cuál es el tono verde
    olivo que tienes?»): manda las fotos de ESA prenda en esos colores y devuelve el hecho para el modelo ("" si no aplica).

    14-10-2026, WhatsApp real: tras «El Vestido Salome también lo tenemos en Verde Olivo, Vino, Blanco…», «enviame los
    colores en foto» se leyó como «pide ver el modelo que nombraste»: salió otra vez la foto azul noche (una foto por
    código) y el bot dijo que no tenía fotos de los otros colores, habiendo siete. Luego «¿cuál es el tono verde olivo?»
    buscó prendas verdes en el catálogo y le ofreció otros modelos.
    La prenda: la que nombra el mensaje; si no, la ÚNICA que nombró el bot; si no nombró ninguna, la que está en foco."""
    from .herramientas import RE_PIDE_COLORES, RE_PIDE_FOTO
    plano = memoria._plano(mensaje)
    if not plano or RE_OTRA_PRENDA.search(plano):
        return ""
    en_msg = _nombradas(mensaje, rec.fichas)
    en_bot = [c for c in _nombradas(ultimo_bot, rec.fichas) if rec._ficha(c)]
    if len(en_msg) > 1:
        return ""
    cod = en_msg[0] if en_msg else (en_bot[0] if len(en_bot) == 1 else ("" if en_bot else (f.get("producto") or "")))
    ficha = rec._ficha(cod) if cod else None
    if ficha is None:
        return ""
    cod = ficha.codigo.upper()
    web = colores.colores_de(ficha)
    nombrados = fotos_color.colores_nombrados(mensaje, web)
    if nombrados:
        # «en negro porfa» justo después de que el bot listó los colores de esa prenda es elegir uno de la lista
        # (14-10-2026, prueba de 300 conversaciones: Elowen, Saira, Elvi, Athena, Persefone, Santana sin foto o con otro modelo).
        lista_bot = sum(1 for c in web if colores.normaliza(c) and colores.normaliza(c) in memoria._plano(ultimo_bot)) >= 2
        corto = len(plano.split()) <= 4
        if not ("?" in mensaje or RE_SOBRE_COLOR.search(plano) or lista_bot or corto):
            return ""
    elif not (RE_PIDE_COLORES.search(plano) and RE_PIDE_FOTO.search(plano)):
        return ""
    # Todos los colores: sin el de la foto principal si ya la vio (es la que mandó o la que se le mostró).
    vista = {str(c).upper() for c in f.get("mostrados") or []} | {(f.get("producto") or "").upper()}
    saltar = fotos_color.color_de(f"{cod.lower()}.jpg") if cod in vista else ""
    r = rec.enviar_colores(cod, nombrados or None, saltar=saltar)
    if not r["con_foto"] and nombrados:
        # Viene en ese color según baruka.pe pero no hay foto (Vestido Adana en rojo, 14-10-2026): se dice que SÍ lo hay
        # y no se cambia de modelo. Antes caía a buscar el color en todo el catálogo y le ofrecía otros vestidos rojos.
        rec.colores_ok = True
        return (f"PREGUNTA POR EL {ficha.nombre} {cod} EN {', '.join(nombrados).upper()}: SÍ viene en ese color (baruka.pe), "
                "pero no tengo foto en ese color. Díselo así (que sí lo hay) y ofrécele que una asesora le mande la foto "
                "en ese color; NO cambies de modelo ni ofrezcas otras prendas, y no mandes fotos de otro color.")
    if not r["con_foto"]:
        return ""
    rec.colores_ok = True
    con = ", ".join(r["con_foto"])
    nombre = f"{ficha.nombre} {cod}"
    if nombrados:
        falta = f" No hay foto en {', '.join(r['sin_foto'])}: dilo." if r["sin_foto"] else ""
        return (f"PREGUNTA POR EL {nombre} EN {con.upper()}: ya se le envió la foto de ESE modelo en {con} (NO llames a "
                f"enviar_fotos, NO cambies de modelo ni ofrezcas otras prendas).{falta} Dile que ahí lo ve en ese color y "
                "sigue con el paso del guion.")
    resto = [c for c in web if c not in r["con_foto"] and not fotos_color.es_personalizado(c)
             and colores.normaliza(c) != colores.normaliza(saltar)]
    extra = f" Ya lo vio en {saltar} (la primera foto)." if saltar and saltar not in r["con_foto"] else ""
    extra += f" También viene en {', '.join(resto)} (sin foto aquí)." if resto else ""
    if any(fotos_color.es_personalizado(c) for c in web):
        extra += " Y se puede pedir en color personalizado."
    return (f"PIDE VER LOS COLORES DEL {nombre}: ya se le enviaron las fotos de ESE modelo en {con} (NO llames a "
            f"enviar_fotos, NO cambies de modelo ni ofrezcas otras prendas).{extra} Dile que ahí van esos colores, nómbralos "
            "tal cual y pregúntale cuál le gusta más. NUNCA digas que solo tienes foto de un color.")


GENERICAS_NOMBRE = {"vestido", "conjunto", "blusa", "pantalon", "falda", "enterizo", "blazer"}


def _nombradas(texto: str, fichas, ctx: dict | None = None) -> list[str]:
    """Códigos de las prendas que el texto nombra («Vestido Pandora», «Kabanova rojo», «V31»), en orden de aparición.
    Lo decide app/nombres.py: un nombre repetido sin tipo (Holly) no cuenta —se pregunta— y uno que es palabra común
    (Alma, Sol, Lila) solo con contexto. `ctx` = {"foco", "bot", "contesta_cual"} para resolver los repetidos."""
    return nombres.nombradas(texto, fichas, ctx).codigos


def _ambiguas(texto: str, fichas, ctx: dict | None = None) -> list[tuple[str, list[str]]]:
    """Nombres repetidos que el texto deja sin decidir: [("holly", ["V41", "V88"])]."""
    return nombres.nombradas(texto, fichas, ctx).ambiguas


# Pistas de que habla de una prenda ya mostrada sin nombrarla: «el primero», «la otra», «ese mismo», «el de la foto».
RE_REFERENCIA = re.compile(r"\b(primer[oa]?|segund[oa]|tercer[oa]?|ultim[oa]|penultim[oa]|anterior|otr[oa])\b|"
                           r"\b(ese|esa|este|esta|aquel|aquella)\b\s*(mism[oa]|vestido|modelo|conjunto|blusa|prenda|pantalon|"
                           r"enterizo|blazer|falda)?|\bque me (mostraste|mandaste|pasaste|enviaste|ensenaste)\b|\bde la foto\b|"
                           r"\b(el|la) (de|del) (arriba|abajo|medio|antes|recien)\b")


def _referencia(f: dict, mensaje: str, fichas, ctx: dict) -> tuple[str, str]:
    """Resuelve «el primero», «la otra», «ese», «el verde» contra las prendas ya mostradas (app/catalogos/referencias.py).
    Devuelve (código, hecho para el modelo); ("", "") si no aplica. Si queda entre dos, el hecho pide preguntar cuál.
    Sin código a la vista (15-10-2026), estas referencias son la forma normal de elegir."""
    from ..catalogos import referencias as refs
    plano = memoria._plano(mensaje)
    mostrados = [str(c).upper() for c in (f.get("mostrados") or []) if c]
    if not mostrados or nombres.nombradas(mensaje, fichas, ctx) or not RE_REFERENCIA.search(plano):
        return "", ""
    por = {x.codigo.upper(): x for x in fichas}
    cands = [{"codigo": c, "nombre": por[c].nombre, "color": fotos_color.color_de(f"{c.lower()}.jpg") or (por[c].color or "")}
             for c in mostrados if c in por]
    if not cands:
        return "", ""
    r = refs.resolver_referencia(mensaje, cands)
    if r.tipo == "candidato" and r.candidato:
        cod = r.candidato["codigo"]
        return cod, (f"SE REFIERE AL {r.candidato['nombre']} ({r.motivo or 'la prenda ya mostrada'}): habla de ESA prenda, "
                     "no cambies de modelo ni mandes otra foto salvo que la pida.")
    if r.tipo == "ambiguo" and len(r.empatados) >= 2:
        lista = " o ".join(f"{nombres.articulo(x['nombre'])} {x['nombre']}" for x in r.empatados[:3])
        return "", f"NO QUEDA CLARO A CUÁL SE REFIERE ({lista}): pregúntale cuál, sin elegir por ella ni mandar fotos."
    return "", ""


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


def _fotos_con_color(rec: Recolector) -> str:
    """«V46 Vestido Blear (foto en Azul noche), V102 Vestido Maura (foto en Negro; no viene en azul)»."""
    partes = []
    for x in rec.fotos:
        color = x.get("color_foto") or "su color principal"
        extra = f"; no viene en {x['sin_color']}" if x.get("sin_color") else ""
        partes.append(f"{x['codigo']} {x['nombre']} (foto en {color}{extra})")
    return ", ".join(partes) or "ninguna"


def _pregunta_sabida(texto: str, f: dict) -> list[str]:
    """El texto pregunta un dato que la ficha ya tiene («¿Cuánto mides?» con la estatura guardada), aunque el modelo no
    lo haya marcado en pregunta_hecha (13-10-2026, WhatsApp real: «pero ya te dije cuál era»)."""
    sab = f.get("sabemos") or {}
    preguntas = " ".join(re.findall(r"¿[^?]*\?", memoria._plano(texto or "")))
    out = []
    for k, rx in RE_PREGUNTA_DE.items():
        sabido = sab.get(k) or (k == "fecha" and sab.get("fecha_iso"))
        if sabido and rx.search(preguntas):
            out.append(f"pregunta por {k} y ya lo sabes ({sabido}): no lo vuelvas a preguntar, úsalo")
    return out


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
    veces = _veces_preguntado(historial, f)
    q = next((q for k, q in PREGUNTAS_GUION if not (sab.get(k) or (k == "fecha" and sab.get("fecha_iso")))
              and veces.get(k, 0) < 2), "")   # se omite si ya se hizo dos veces en la conversación
    return f"{texto} {q}".strip() if q else texto


def _preguntas_de(texto: str) -> set[str]:
    """Qué datos pregunta un mensaje del bot (talla, horario, estatura, fecha, estilo, ocasión, confirmar)."""
    qs = " ".join(re.findall(r"¿[^?]*\?|[^.!¿?]*\?", memoria._plano(texto or "")))
    return {k for k, rx in {**RE_PREGUNTA_DE, **RE_PREGUNTA_EXTRA}.items() if qs and rx.search(qs)}


def _veces_preguntado(historial, f: dict | None = None) -> dict:
    """Cuántos mensajes del bot preguntaron cada dato: lo que dice el historial o, si es más, lo que lleva la ficha (el
    historial que manda el bot Go solo trae los últimos 11 mensajes)."""
    cuenta: dict[str, int] = {}
    for b in _bots(historial):
        for k in _preguntas_de(b):
            cuenta[k] = cuenta.get(k, 0) + 1
    for k, n in ((f or {}).get("veces") or {}).items():
        cuenta[k] = max(cuenta.get(k, 0), n)
    return cuenta


def _anotar_veces(f: dict, texto: str, historial) -> None:
    """Suma a la ficha las preguntas del mensaje que se va a enviar (partiendo de lo que ya muestra el historial)."""
    base = _veces_preguntado(historial, f)
    f["veces"] = {**base, **{k: base.get(k, 0) + 1 for k in _preguntas_de(texto)}}


def _sin_insistir(texto: str, f: dict, historial) -> str:
    """Quita del texto las preguntas por un dato que ya se preguntó dos veces y sigue sin respuesta."""
    sab = f.get("sabemos") or {}
    veces = _veces_preguntado(historial, f)
    agotadas = [k for k, n in veces.items() if n >= 2 and not (sab.get(k) or (k == "fecha" and sab.get("fecha_iso")))]
    if not agotadas or "?" not in (texto or ""):
        return texto
    reglas = {**RE_PREGUNTA_DE, **RE_PREGUNTA_EXTRA}
    insiste = lambda q: any(reglas[k].search(memoria._plano(q)) for k in agotadas)
    nuevo = re.sub(r"¿[^¿?]*\?", lambda m: "" if insiste(m.group(0)) else m.group(0), texto)
    partes = re.split(r"(?<=[.!?…])\s+|\n+", nuevo.strip())
    nuevo = " ".join(p for p in partes if not ("?" in p and "¿" not in p and insiste(p)))
    nuevo = re.sub(r"\s*,(\s*,)+", ",", nuevo)
    nuevo = re.sub(r"(\s*[,:;y]\s*)+(?=[.!]|$)", "", nuevo)
    nuevo = re.sub(r"\b(cu[eé]ntame|dime|y)\s*[:,]?\s*$", "", nuevo.strip(), flags=re.I)
    # La pregunta quitada dejaba su introducción colgando: «…medidas peruanas; para orientarte mejor con el largo» (15-10).
    nuevo = re.sub(r"\s*[;,]\s*(para|as[ií]|si|pero|y|entonces)\b[^.!?…;,¿]*$", ".", nuevo.strip(), flags=re.I)
    return re.sub(r"\s{2,}", " ", nuevo).strip(" ,;:")


def _catalogo(f: dict, mensaje: str, fichas, hist, req, t0) -> dict | None:
    """El enlace de la sección que pide, con su captura, sin LLM. Si nombra UN modelo («pásame el link del Holly»), el
    enlace de ese modelo con su foto. Termina con la pregunta que toque: el evento si aún no se sabe, si no «¿cuál te gustó?»."""
    nombrados = _nombradas(mensaje, fichas, {"foco": f.get("producto") or ""})
    tarjetas = []
    # Nombra UN modelo: va su enlace aunque diga el tipo («link del pantalón Holly»); con el tipo en plural («link de
    # los pantalones») o «todo», la sección.
    if len(nombrados) == 1 and not re.search(r"\b(vestidos|pantalones|blusas|enterizos|conjuntos|blazers|faldas|tod[oa]s?)\b",
                                             memoria._plano(mensaje)):
        x = next((y for y in fichas if y.codigo.upper() == nombrados[0]), None)
        w = ficha_producto.web(nombrados[0])
        if x is not None and w.get("url"):
            f["producto"] = x.codigo.upper()
            if x.codigo.upper() not in f["mostrados"]:
                f["mostrados"].append(x.codigo.upper())
            tarjetas.append({"codigo": x.codigo, "nombre": x.nombre, "imagen": f"/media/tienda/{x.codigo.lower()}.jpg",
                             "pie": f"Aquí te comparto el {x.nombre}, dale clic 👇👇👇👇\n{w['url']}", "titulo": "", "tallas": []})
    if not tarjetas:
        tarjetas = [t for t in (colecciones.tarjeta(h) for h in colecciones.elegir(mensaje, f)) if t]
    if not tarjetas:
        return None
    nombre = (getattr(req, "cliente", "") or "").strip().split(" ")[0]
    sab = f.get("sabemos") or {}
    if not sab.get("ocasion") and _veces_preguntado(hist, f).get("ocasion", 0) < 2:
        cierre = "Cuéntame, ¿qué evento se te aproxima? Así te ayudo a elegir 😊"
    else:
        cierre = "¿Cuál te gustó más? Dime su nombre y te cuento tallas y colores 😊"
    texto = f"¡Claro{', ' + nombre if nombre else ''}! 😊\n\n{cierre}"
    log.info("V3 catálogo: %s", [t.get("coleccion") or t["codigo"] for t in tarjetas])
    return _respuesta(f, texto, "responder", tarjetas, None, _traza(texto, [], ["catalogo"], 0, _ms(t0)), req)


def _cambio_de_idea(rec: Recolector, f: dict, mensaje: str, antes: dict, datos: dict, ctx: dict | None = None) -> str:
    """Nota un cambio de idea y deja la ficha al día, para que el bot no siga con lo anterior (14-10-2026):
    otro modelo («y el Holly?»), «no me gusta / otro modelo», otro tipo de prenda («mejor un enterizo») u otro color que
    la prenda en foco no tiene. Devuelve el hecho para el modelo ("" si no cambió nada)."""
    plano = memoria._plano(mensaje)
    previo = (f.get("producto") or "").upper()
    nombre = lambda c: next((x.nombre for x in rec.fichas if x.codigo.upper() == c), c)
    pedido = f.get("pedido") or {}
    partes = []
    nombrados = _nombradas(mensaje, rec.fichas, ctx)
    if len(nombrados) == 1 and previo and nombrados[0] != previo:
        nuevo = nombrados[0]
        f["producto"] = nuevo
        if pedido.get("codigo") and not pedido.get("confirmado"):
            ficha_mod.fijar_pedido(f, codigo=nuevo)
        partes.append(f"CAMBIO DE MODELO: ahora pregunta por el {nuevo} {nombre(nuevo)} (antes miraba el {previo} "
                      f"{nombre(previo)}). Habla SOLO del {nuevo}; no retomes el {previo} salvo que ella lo nombre.")
    elif previo and RE_RECHAZA.search(plano) and not re.search(r"\bcolou?r|tono\b", plano):
        f["producto"] = ""
        if pedido.get("codigo") == previo and not pedido.get("confirmado"):
            pedido["codigo"] = ""
        rec.contesta_cual = True        # puede ver otra prenda aunque no diga «muéstrame»
        vistos = ", ".join(f.get("mostrados") or []) or previo
        partes.append(f"NO LE CONVENCIÓ EL {previo} {nombre(previo)}: muéstrale OTRO modelo (enviar_fotos), distinto del "
                      f"{previo} y de los ya mostrados ({vistos}), que cuadre con lo que contó. No vuelvas a ofrecer el {previo}.")
    p_antes, p_ahora = memoria._plano(antes.get("prenda") or ""), memoria._plano((f.get("sabemos") or {}).get("prenda") or "")
    if p_antes and p_ahora and p_antes != p_ahora:
        foco = (f.get("producto") or "").upper()
        if foco and not memoria._plano(nombre(foco)).startswith(p_ahora.rstrip("s")):
            f["producto"] = ""
        partes.append(f"CAMBIO DE PRENDA: ahora busca {p_ahora} (antes {p_antes}). Ofrécele {p_ahora}; no sigas con {p_antes}.")
    color = (datos.get("color") or "").strip()
    foco = (f.get("producto") or "").upper()
    # «la Blusa Lila» es el nombre de la prenda, no un color pedido: no se avisa que «no viene en lila» (15-10-2026).
    if color and foco and memoria._plano(color).split()[0] in nombres.propio(nombre(foco)).split():
        color = ""
    if color and foco and not rec.colores_ok:
        web = colores.colores_de(rec._ficha(foco)) if rec._ficha(foco) else []
        tiene = [c for c in web if memoria._plano(color).split()[0] in memoria._plano(c)]
        if web and not tiene:
            partes.append(f"EL {foco} {nombre(foco)} NO VIENE EN {color.upper()} (viene en {', '.join(web)}): díselo claro y "
                          f"ofrécele modelos en {color} (buscar_catalogo); no digas que el {foco} existe en {color}.")
    return "\n".join(partes)


def _confirma_pedido(rec: Recolector, f: dict, mensaje: str, ultimo_bot: str, permitir: bool) -> str:
    """«¿Confirmamos tu pedido?» → «sí», «ya, cómo te pago», «ahí va el voucher»: el pedido queda confirmado (como si el
    modelo hubiera llamado a crear_reserva) y se pasa al pago. Antes el bot volvía a preguntar «¿Confirmamos tu pedido?»
    tres veces (14-10-2026, prueba de 300 conversaciones)."""
    if not permitir or not RE_PIDE_CONFIRMAR.search(memoria._plano(ultimo_bot)):
        return ""
    plano = memoria._plano(mensaje)
    if memoria.niega(mensaje) or not (memoria.afirma(mensaje) or RE_PAGAR.search(plano)):
        return ""
    cod = ((f.get("pedido") or {}).get("codigo") or f.get("producto") or ((f.get("mostrados") or [""])[-1])).upper()
    talla = ((f.get("pedido") or {}).get("talla") or (f.get("sabemos") or {}).get("talla") or "").upper()
    if not cod or not talla or rec._ficha(cod) is None:
        return ""
    rec.ejecutar("crear_reserva", {"codigo": cod, "talla": talla})
    if not rec.reserva:
        return ""
    voucher = bool(re.search(r"\b(voucher|comprobante|ya (te )?pague|te yape\w*|deposit\w*|transferi)\b", plano))
    return (f"CONFIRMÓ EL PEDIDO ({cod} talla {talla}): ya quedó registrado. " +
            ("Dice que ya pagó: agradécele, dile que lo validamos y pídele la foto del voucher si no la mandó." if voucher else
             "Dale el total con el envío elegido y los datos de pago, y pídele el voucher."))


def _sin_repetidas(texto: str, historial) -> str:
    dichas = {fr for b in _bots(historial[-24:]) for fr in _frases(b) if len(fr) >= 18}
    partes = re.split(r"(?<=[.!?…])\s+|\n+", (texto or "").strip())
    quedan = [x for x in partes if _norm(x) not in dichas]
    return " ".join(quedan).strip()


def _sin_opciones(turno):
    """La encuesta queda en el historial SOLO con su pregunta («¿No lo consideras?»), sin las opciones: la pregunta hace
    falta para entender un «sí» (paso 7) — desde que la pregunta va solo en la encuesta, el texto ya no la trae — y las
    opciones no, porque el modelo las leía como elegidas."""
    if not _es_encuesta(turno):
        return turno
    txt = (turno.get("texto", "") if isinstance(turno, dict) else getattr(turno, "texto", "")).lstrip().removeprefix("📊").strip()
    txt = txt.split(" · ")[0].strip()
    if isinstance(turno, dict):
        return {**turno, "texto": txt}
    try:
        return type(turno)(rol=getattr(turno, "rol", "bot"), texto=txt)
    except Exception:
        return {"rol": getattr(turno, "rol", "bot"), "texto": txt}


def _es_encuesta(turno) -> bool:
    rol = turno.get("rol", "") if isinstance(turno, dict) else getattr(turno, "rol", "")
    txt = turno.get("texto", "") if isinstance(turno, dict) else getattr(turno, "texto", "")
    return rol != "cliente" and (txt or "").lstrip().startswith("📊")


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
    veces = _veces_preguntado(historial, f)
    # Una pregunta hecha dos veces y no contestada no se hace una tercera (14-10-2026: «¿qué evento se te aproxima?»,
    # la talla o la estatura salían tres y cuatro veces cuando la clienta hablaba de otra cosa).
    if not sab.get("ocasion") and veces.get("ocasion", 0) < 2:
        return "PASO DEL GUION: 1 (preséntate solo si es tu primer mensaje y pregunta qué evento se le aproxima)."
    recien = {k for k in ("talla", "horario", "estatura") if veces.get(k, 0) >= 2}   # ya se le preguntó dos veces: no insistir
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
    if rec.oferta_ok or rec.fotos or rec._pide_modelos() or not RE_OFRECE.search(texto or ""):
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
    # Con una promo vigente que cubre la prenda, el total sale del precio con promo (calculado por código, 15-10-2026).
    ficha = rec._ficha(cod)
    promo = next((x for x in conocimiento.promos_para(rec.conoc, ficha)
                  if conocimiento.precio_promo(x, precio)["precio_final"] not in (None, precio)), None) if ficha else None
    if promo is not None and precio is not None:
        final = conocimiento.precio_promo(promo, precio)["precio_final"]
        rec.respaldar_promo(promo, [ficha])
        p += (f", precio normal S/ {float(precio):.2f}, con la promo «{promo['titulo']}» S/ {final:.2f}. Total = precio con "
              "promo + envío (Lima S/ 15, provincia S/ 20, agencia de viaje S/ 15).")
    elif precio is not None:
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
    (re.compile(r"otros modelos|otras opciones|modelos parecidos|otras prendas"), ("Sí, muéstrame", "No, gracias")),
    (re.compile(r"de dia o de noche"), ("De día", "De noche")),
    (re.compile(r"(en )?que talla (te encuentras|usas)|tu talla"), ("S", "M", "L")),
)


RE_DOS_EN_UNA = re.compile(r"(\by|,)\s+(cuanto|cuantos|que|como|cual|cuando|donde|para cuando|en que|de que)\b")

# Preguntas del guion que se pueden hacer juntas (paso 3) y cómo reconocerlas en el texto del bot.
RE_PREGUNTA_DE = {"talla": re.compile(r"\btalla\b"), "horario": re.compile(r"\bde dia o de noche\b"),
                  "estatura": re.compile(r"\bestatura\b|\bcuanto (mide|mides)\b|\bmedida\b.{0,14}\b(exact[ao]|precis[ao])\b|\bmide exact"),
                  "fecha": re.compile(r"\bpara cuando\b"),
                  "estilo": re.compile(r"\bsentir(se|te)?\b|\bllamar (un poco (mas )?)?la atencion\b")}
FALTA_TU = {"talla": "¿Y en qué talla te encuentras?", "horario": "¿Y el evento es de día o de noche?",
            "estatura": "¿Y cuánto mides de estatura?", "fecha": "¿Y para cuándo lo necesitas?",
            "estilo": "¿Y prefieres llamar un poco más la atención o verte elegante y discreta?"}
FALTA_ELLA = {"talla": "¿Y en qué talla se encuentra ella?", "horario": "¿Y el evento es de día o de noche?",
              "estatura": "¿Y cuánto mide de estatura?", "fecha": "¿Y para cuándo lo necesitan?",
              "estilo": "¿Y ella prefiere llamar un poco más la atención o verse elegante y discreta?"}


def _ultimo_turno_bot(historial) -> str:
    """Lo que dijo el bot desde el último mensaje de la clienta (en WhatsApp puede ir en varios mensajes)."""
    out = []
    for turno in reversed(list(historial or [])):
        rol = turno.get("rol", "") if isinstance(turno, dict) else getattr(turno, "rol", "")
        if rol == "cliente":
            break
        out.append(turno.get("texto", "") if isinstance(turno, dict) else getattr(turno, "texto", ""))
    return " ".join(reversed(out))


def _con_la_que_falta(texto: str, f: dict, historial) -> str:
    """Se le hicieron dos o más preguntas del guion y contestó solo una parte («de noche» a «¿de día o de noche y cuánto
    mide?»): la respuesta termina preguntando la que falta, una sola. Si no contestó ninguna, no se insiste aquí (eso ya lo
    hace el guion); si la respuesta ya la pregunta o ya trae dos preguntas, tampoco (12-10-2026)."""
    # Solo lo que va entre «¿…?»: «sí está diseñado para tu estatura» no es preguntar la estatura.
    previo = " ".join(re.findall(r"¿[^?]*\?", memoria._plano(_ultimo_turno_bot(historial))))
    hechas = [k for k, rx in RE_PREGUNTA_DE.items() if rx.search(previo)]
    if len(hechas) < 2:
        return texto
    sab = f.get("sabemos") or {}
    tiene = lambda k: bool(sab.get(k) or (k == "fecha" and sab.get("fecha_iso")))
    veces = _veces_preguntado(historial, f)
    faltan = [k for k in hechas if not tiene(k) and veces.get(k, 0) < 2]   # ni una tercera vez (14-10-2026)
    if not faltan or len(faltan) == len(hechas) or (texto or "").count("?") >= 2:
        return texto
    plano = " ".join(re.findall(r"¿[^?]*\?", memoria._plano(texto)))
    if any(RE_PREGUNTA_DE[k].search(plano) for k in faltan):
        return texto
    clienta = " ".join(memoria._plano(t.get("texto", "") if isinstance(t, dict) else getattr(t, "texto", ""))
                       for t in historial or []
                       if (t.get("rol", "") if isinstance(t, dict) else getattr(t, "rol", "")) == "cliente")
    preguntas = FALTA_ELLA if plantillas.RE_OTRA_PERSONA.search(clienta) else FALTA_TU
    return f"{(texto or '').rstrip()} {preguntas[faltan[0]]}".strip()


def _opciones(texto: str) -> list[str]:
    """Las opciones de la ÚLTIMA pregunta, si es de respuesta cerrada y va sola (el paso 3 con tres preguntas, no)."""
    t = texto or ""
    if t.count("?") == 0:
        return []
    ini = t.rfind("¿", 0, t.rfind("?"))
    ultima = memoria._plano(t[ini:] if ini >= 0 else t)
    # Encuesta solo con UNA pregunta (pedido del usuario, 12-10-2026): con dos, la encuesta contesta una y la otra queda
    # sin respuesta. También cuando van unidas en un solo «¿…?»: «¿es de día o de noche y cuánto mide de estatura?».
    if t.count("?") > 1 or RE_DOS_EN_UNA.search(ultima):
        return []
    for rx, ops in OPCIONES:
        if rx.search(ultima):
            return list(ops)
    return []


def _sin_escasez_repetida(texto: str, f: dict, historial) -> str:
    """La escasez («dispongo de stock», «mujer precavida»…) va una sola vez. Si ya se dijo y el modelo la reescribe, se
    quitan esas frases; si no queda nada, se pasa al envío (paso 7) cuando aún no se sabe."""
    if not plantillas.ya_hubo_escasez(historial) or not plantillas.RE_ESCASEZ.search(texto or ""):
        return texto
    frases = re.split(r"(?<=[.!?])\s+|\n+", texto)
    resto = " ".join(x for x in frases if x and not plantillas.RE_ESCASEZ.search(x)
                     and not re.fullmatch(r"\W*¿?no lo consideras\?\W*", memoria._plano(x)))
    if resto.strip(" 😊🥰💛🤭"):
        return resto.strip()
    return PLANTILLA_PASO7[0] if not (f.get("sabemos") or {}).get("envio") else texto


RE_PERFECTO = re.compile(r"(?<=[.!?…😊🥰]\s)¡?Perfecto[!,.]?\s*(😊\s*)?", re.I)


def _una_muletilla(texto: str) -> str:
    """Dos frases del guion juntas dejaban «¡Perfecto, sí lo tenemos…! … Perfecto 😊 Tengo colores…» (12-10-2026):
    desde el segundo «Perfecto», se quita la muletilla y queda el resto de la frase."""
    if len(re.findall(r"\bperfecto\b", texto or "", re.I)) < 2:
        return texto
    primero = re.search(r"\bperfecto\b", texto, re.I).end()
    resto = RE_PERFECTO.sub("", texto[primero:])
    return texto[:primero] + (resto[:1].upper() + resto[1:] if resto[:1].islower() else resto)


# La frase de noche del guion parte UNA pregunta en dos («¿Cómo quiere sentirse ese día? ¿Prefiere llamar…?»). Contada
# como dos, no salía la encuesta ni cabía la pregunta que faltaba (12-10-2026): se une en una.
RE_SENTIR_PARTIDA = re.compile(r"(¿[^?¿]*\bsentir(?:se|te)?\b[^?¿]*)\?\s*¿\s*(prefier\w*|busca\w*|quier\w*)", re.I)


def _respuesta(f: dict, texto: str, accion: str, fotos, reserva, traza: dict, req) -> dict:
    texto = prompt.RE_RESTO_JSON.sub("", texto or "").strip().rstrip("`{} ")
    texto = _una_muletilla(texto)
    texto = _sin_escasez_repetida(texto, f, getattr(req, "historial", []) or [])
    texto = RE_SENTIR_PARTIDA.sub(lambda m: f"{m.group(1)}: {m.group(2).lower()}", texto or "")
    if accion == "responder" and not ((f.get("pedido") or {}).get("confirmado")):
        texto = _con_la_que_falta(texto, f, getattr(req, "historial", []) or [])
    _anotar_veces(f, texto, getattr(req, "historial", []) or [])
    sugerencias = [{"codigo": x["codigo"], "fuente": _fuente(x["codigo"]), "imagen": x["imagen"], "pie": x["pie"],
                    "titulo": x.get("titulo", ""), "tallas": x.get("tallas", [])}
                   for x in (fotos or [])]
    return {"respuesta": texto, "accion": accion, "codigo": (reserva or {}).get("codigo", ""),
            "fuentes_usadas": list(traza.get("fuentes_usadas") or []),
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


def _codigo_anuncio(req, fichas) -> str:
    """El modelo del anuncio: el que nombra su título; si no nombra ninguno, el de la campaña (KD_PRODUCTO_DEMO)."""
    from .. import venta
    vivos = {x.codigo.upper() for x in fichas}
    for c in _nombradas(getattr(req, "anuncio", "") or "", fichas):
        if c in vivos:
            return c
    demo = (venta.PRODUCTO_DEMO or "").upper()
    return demo if demo in vivos else ""


def _bienvenida_anuncio(f: dict, req, fichas, consultar_stock, pie, conoc, t0) -> dict | None:
    cod = _codigo_anuncio(req, fichas)
    if not cod:
        return None
    rec = Recolector(fichas, consultar_stock, pie=pie, mostrados=f.get("mostrados") or [], contesta_cual=True)
    rec.poner_conocimiento(conoc)
    rec.ejecutar("enviar_fotos", {"codigos": [cod]})
    if not rec.fotos:
        return None
    ficha = rec._ficha(cod)
    f["producto"] = cod
    if cod not in f.setdefault("mostrados", []):
        f["mostrados"].append(cod)
    f["etapa"] = "seguimiento"
    f["pendiente"] = ""
    texto = prospectos.bienvenida(getattr(req, "cliente", ""), ficha.nombre, prospectos.garantia(conoc))
    log.info("V3 bienvenida por anuncio: %s", cod)
    res = _respuesta(f, texto, "responder", rec.fotos[:1], None, _traza("", [], ["plantilla:bienvenida"], 0, _ms(t0)), req)
    res["respuestas_rapidas"] = [{"label": "Ver colores"}, {"label": "Ver tallas"}]
    if getattr(req, "precio_al_llegar_por_anuncio", False):
        res["precio_al_llegar"] = True     # main no esconde el precio de esta foto aunque la tienda oculte precios
    return res


def _tallas_en_foco(f: dict, fichas, consultar_stock) -> str:
    cod = (f.get("producto") or "").upper()
    ficha = next((x for x in fichas if x.codigo.upper() == cod), None)
    if ficha is None:
        return ""
    try:
        st = (consultar_stock([cod]) or {}).get(cod) or {}
    except Exception:
        st = {}
    online = st.get("online") or {}
    orden = ["XS", "S", "M", "L", "XL", "XXL", "2XL"]
    hay = sorted([t for t, n in online.items() if (n or 0) > 0], key=lambda t: orden.index(t) if t in orden else 99)
    plantillas._marca(f, "talla")
    if not online:     # el stock no respondió (o no se conoce): las tallas del modelo, sin afirmar que está agotado
        tallas = (getattr(ficha, "tallas", "") or "").strip()
        if tallas:
            return f"El {ficha.nombre} viene en tallas {tallas} 😊 ¿Cuál es la tuya? Te confirmo el stock al toque."
        return ""
    if not hay:
        return f"Por ahora el {ficha.nombre} está agotado en todas las tallas 😔 ¿Te muestro uno parecido?"
    lista = hay[0] if len(hay) == 1 else ", ".join(hay[:-1]) + " y " + hay[-1]
    return f"El {ficha.nombre} está disponible en talla{'s' if len(hay) > 1 else ''} {lista} 😊 ¿Cuál es la tuya?"


def _hecho_presupuesto(f: dict, req, fichas) -> str:
    """Ajustes → «Presupuesto» (octubre de 2026, Luis: «hago consultas para no perder mi tiempo»). Después de conocer la
    ocasión y antes de recomendar, UNA pregunta amable por el rango de presupuesto; con el presupuesto ya dicho y por
    debajo de todo el catálogo, se le dice con cariño (el descarte), sin insistir."""
    if not getattr(req, "preguntar_presupuesto", False):
        return ""
    sab = f.get("sabemos") or {}
    if not sab.get("ocasion") or f.get("etapa") not in ("", "prospeccion", "seguimiento"):
        return ""
    pres = sab.get("presupuesto")
    if not pres:
        if "presupuesto" in (f.get("preguntado") or []) or f.get("mostrados"):
            return ""
        f.setdefault("preguntado", []).append("presupuesto")
        return ("PREGUNTA DE PRESUPUESTO (la tienda la pide): en este mensaje, como única pregunta y con cariño, pregúntale si "
                "tiene un rango de presupuesto en mente («¿Tienes un rango en mente? Así te muestro lo que mejor te queda 😊»). "
                "Primero escucha y resuelve; no hables de precios todavía.")
    try:
        tope = float(pres)
    except (TypeError, ValueError):
        return ""
    precios_ = [x.precio for x in fichas if isinstance(getattr(x, "precio", None), (int, float)) and x.precio > 0]
    if precios_ and tope < min(precios_):
        return (f"SU PRESUPUESTO (S/ {tope:g}) está por debajo de nuestros precios (desde S/ {min(precios_):g}): díselo con "
                "cariño y sin rodeos, ofrécele lo más cercano a su presupuesto y no insistas.")
    return ""

