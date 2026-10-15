"""Las herramientas de V3: el modelo no lleva datos en el prompt, los pide.

Precios, tallas, telas, direcciones y plazos solo existen en la respuesta de una herramienta. Cada llamada
suma a `hechos` lo que el modelo ya puede afirmar; el validador comprueba el texto contra esos hechos.

Siete herramientas (plan §3): buscar_catalogo, stock, ficha_tecnica, enviar_fotos, crear_reserva,
agendar_cita, derivar_asesora. Reusan datos.py, stock.py, ficha_producto.py y venta.py; no inventan nada.

`Recolector` es la única puerta: `esquema()` da la lista para el LLM (formato OpenAI `tools`) y
`ejecutar(nombre, args)` corre la herramienta y actualiza `hechos`, `fotos` y el pedido en la ficha.
"""
from __future__ import annotations

import re

from .. import colecciones, colores, datos, ficha_producto, fotos_color, memoria, stock as stk, venta
from .validador import hechos_vacios

MAX_CATALOGO = 6
RE_PIDE_VARIAS = re.compile(r"\b(fotos|fotitos|modelos|opciones|vestidos|conjuntos|otros|otras|cat[aá]logo|todos|todas|ambos|ambas|los (dos|tres)|las (dos|tres)|qu[eé] tienes|qu[eé] m[aá]s)\b")
# «fot», «fotito», «fotos» (13-10-2026: «¿tienes fot?» con tipeo no contaba como pedir la foto).
RE_PIDE_FOTO = re.compile(r"\b(fot\w*|ver|veo|mu[eé]stra\w*|p[aá]sa\w*|m[aá]nda\w*|env[ií]a\w*|c[oó]mo es(?!\s+(el|la|su)\s+(material|tela|forro|talla|envio|env[ií]o|pago))|c[oó]mo se ve)\b")
# Pide ver modelos (12-10-2026, la tienda: «que no ofrezca modelos a menos que la clienta lo solicite»). «Busco un
# vestido» es una necesidad, no un pedido de fotos: ahí se indaga (guion, pasos 1-4).
RE_PIDE_MODELOS = re.compile(r"\b(fotos?|ver|veo|verlo|verla|mu[eé]stra\w*|modelos?|opciones|cat[aá]logo|"
                             r"recomienda\w*|c[oó]mo es(?!\s+(el|la|su)\s+(material|tela|forro|talla|envio|env[ií]o|pago))|"
                             r"qu[eé] m[aá]s|otros?|otras?)\b|\bv\d{2,3}\b", re.I)
# «pásame», «tienen», «hay»… piden prendas solo si nombran una prenda: «pásame el yape» o «¿tienen tienda en Miraflores?»
# no son pedir modelos (12-10-2026: con 96 modelos el LLM aprovechaba y mandaba uno que nadie pidió).
RE_VERBO_PEDIR = re.compile(r"\b(p[aá]sa\w*|m[aá]nda\w*|env[ií]a\w*|tienes|tienen|tendr[aá]s|hay)\b", re.I)
RE_PRENDA = re.compile(r"\b(vestid\w*|conjunt\w*|blus\w*|pantal\w*|enteriz\w*|blazer\w*|fald\w*|ropa|prendas?|"
                       r"algo|outfits?|look)\b", re.I)
MAX_FOTOS = 3          # un mensaje con 6 fotos es un catálogo, no una conversación
# Pide ver los colores de UNA prenda («enviame los colores en foto», «muéstrame los tonos»): las fotos son del mismo
# modelo, una por color, así que el tope es otro (14-10-2026: el Salome viene en siete colores con foto).
RE_PIDE_COLORES = re.compile(r"\b(colores|tonos|tonalidades)\b", re.I)
MAX_FOTOS_COLORES = 6
GENERICAS = {"vestido", "conjunto", "blusa", "pantalon", "falda", "enterizo", "blazer"}
# Pide alternativas («algo parecido», «otras opciones»): ahí sí se muestran otras prendas aunque nombre un retirado.
RE_OTRAS = re.compile(r"\b(parecid\w*|similar\w*|otr[oa]s?|alternativ\w*|reemplaz\w*|en su lugar)\b", re.I)
ORDEN_TALLAS = ("XS", "S", "M", "L", "XL", "2XL", "XXL")
MONEDA = "S/"
ENVIOS = (15.0, 20.0)  # envío a Lima / provincia (tienda.md): precios que el modelo sí puede decir
COLORES = ("rojo", "vino", "azul", "negro", "negra", "blanco", "blanca", "verde", "rosa", "rosado", "celeste", "beige",
           "naranja", "turquesa", "morado", "lila", "amarillo", "gris", "plomo", "marron", "marrón", "dorado", "plateado", "fucsia")

ESQUEMA = [
    {"type": "function", "function": {
        "name": "buscar_catalogo",
        "description": "Busca prendas del catálogo por texto libre (nombre, color, tipo) y/o categoría. "
                       "Devuelve códigos, nombres, precio y disponibilidad. Úsala antes de nombrar una prenda.",
        "parameters": {"type": "object", "properties": {
            "consulta": {"type": "string", "description": "lo que busca la clienta (p. ej. «vestido rojo de noche»)"},
            "categoria": {"type": "string", "description": "vestido | conjunto | blazer | falda | jeans | pantalón | polo | blusa | enterizo (opcional)"}},
            "required": ["consulta"]}}},
    {"type": "function", "function": {
        "name": "stock",
        "description": "Disponibilidad real por talla de una prenda (tienda virtual y sucursales). Úsala antes de "
                       "afirmar una talla o si está disponible.",
        "parameters": {"type": "object", "properties": {"codigo": {"type": "string"}}, "required": ["codigo"]}}},
    {"type": "function", "function": {
        "name": "ficha_tecnica",
        "description": "Ficha técnica de una prenda (silueta, largo, escote, mangas, espalda, tela). Úsala antes de "
                       "describir cómo es o de qué tela es.",
        "parameters": {"type": "object", "properties": {"codigo": {"type": "string"}}, "required": ["codigo"]}}},
    {"type": "function", "function": {
        "name": "enviar_fotos",
        "description": "Marca las fotos que se le enviarán a la clienta después de tu texto. Solo las prendas aquí "
                       "marcadas se envían; no prometas fotos de prendas que no marques.",
        "parameters": {"type": "object", "properties": {
            "codigos": {"type": "array", "items": {"type": "string"}}}, "required": ["codigos"]}}},
    {"type": "function", "function": {
        "name": "enviar_catalogo",
        "description": "Manda el enlace de una sección del catálogo de baruka.pe con su captura (como la tienda). Úsala "
                       "cuando pida el catálogo, la página web o ver TODO de un tipo de prenda. Secciones: vestidos "
                       "(largos), vestido-cortos, enterizos, blusas, pantalon, conjuntos, sastre, blazer, all (todo).",
        "parameters": {"type": "object", "properties": {
            "seccion": {"type": "string", "enum": ["vestidos", "vestido-cortos", "enterizos", "blusas", "pantalon",
                                                   "conjuntos", "sastre", "blazer", "all"]}}, "required": ["seccion"]}}},
    {"type": "function", "function": {
        "name": "crear_reserva",
        "description": "Reserva una prenda en una talla para la clienta. Llámala SOLO cuando la clienta haya "
                       "confirmado el pedido en el turno anterior. El resumen #N lo arma el sistema.",
        "parameters": {"type": "object", "properties": {
            "codigo": {"type": "string"}, "talla": {"type": "string"}}, "required": ["codigo", "talla"]}}},
    {"type": "function", "function": {
        "name": "agendar_cita",
        "description": "Agenda una cita para probarse una prenda en el showroom. Valida día y hora (L–D 9–19 h, no en "
                       "refrigerio 13–14 h).",
        "parameters": {"type": "object", "properties": {
            "dia": {"type": "string", "description": "YYYY-MM-DD"}, "hora": {"type": "string", "description": "HH:MM"},
            "codigo": {"type": "string"}, "talla": {"type": "string"}}, "required": ["dia", "hora"]}}},
    {"type": "function", "function": {
        "name": "derivar_asesora",
        "description": "Deriva a una asesora humana cuando no puedes resolverlo o la clienta lo pide.",
        "parameters": {"type": "object", "properties": {"motivo": {"type": "string"}}, "required": []}}},
]


def _cat(c: str) -> str:
    """Categoría comparable: sin tildes, mayúsculas ni plural («Pantalones» = «pantalón»)."""
    t = memoria._plano(c or "").strip()
    return re.sub(r"(es|s)$", "", t) if len(t) > 4 else t


def _nombre_en(f, palabras) -> bool:
    """Alguna palabra buscada es parte del nombre propio de la prenda («zury» en «Pantalón Zury»)."""
    propio = [w for w in memoria._plano(f.nombre).split() if w not in GENERICAS]
    return any(p in propio for p in (memoria._plano(x) for x in palabras))


class Recolector:
    """Corre las herramientas de un turno y acumula lo que el modelo ya puede afirmar.

    - `fichas`: catálogo vivo (datos.Ficha).
    - `consultar_stock`: callable(codigos) -> {CODIGO: estado} (E.stock.consultar).
    - `pie`: callable(ficha, estado) -> texto del pie de foto (main._pie). Opcional.
    - `permitir_reserva`: False en sombra (la reserva la hace el flujo de V1).
    """

    def __init__(self, fichas, consultar_stock, pie=None, permitir_reserva: bool = False, mensaje: str = "",
                 mostrados=(), pago_ok: bool = False, contesta_cual: bool = False):
        self.contesta_cual = contesta_cual   # el bot le preguntó «¿cuál?»: lo que describa es pedir verlo
        self.sin_cual = False                # «¿tienen este vestido?» sin decir cuál: no se adivina
        self.sin_color = ""                  # pidió un color que no hay: no se manda otra prenda en su lugar
        self.retirado = ""                   # nombró un modelo que ya no se vende (no está en baruka.pe)
        self.oferta_ok = False               # /foto sin reconocer: la respuesta DEBE preguntar si quiere ver otros modelos
        self.sigue = False                   # «No, gracias» a esa oferta no es despedida: la charla sigue por el guion
        self.varias_ok = False               # dijo que sí quiere ver otros modelos: salen hasta MAX_FOTOS, no una
        self.bloquear_fotos = False          # objeción al envío: se contesta y se vuelve a la pregunta pendiente, sin fotos
        self.color_pedido = ""               # pidió un color: cada foto sale en ESE color si el modelo lo tiene (13-10-2026)
        self.colores_ok = False              # pidió ver los colores de una prenda: salen hasta MAX_FOTOS_COLORES (14-10-2026)
        self.catalogo_ok = False             # se mandó la captura de una sección del catálogo (enviar_catalogo)
        self.grupo = ""                      # «llamar la atención» → vibrante, «elegante y discreta» → sobrio (app/colores.py)
        self.grupos_cfg: dict | None = None  # grupos de color de la tienda (jmdventas); None = seed del agente
        self.mensaje = (mensaje or "").lower()
        self.mostrados = {str(c).upper() for c in mostrados or ()}
        self.fichas = list(fichas)
        self._stock = consultar_stock
        self._pie = pie
        self.permitir_reserva = permitir_reserva
        self.hechos = hechos_vacios()
        self.hechos["precios"].update(ENVIOS)
        # Tallas de la tienda (tienda.md): S, M y L en stock; XS, XL y 2XL bajo pedido en los modelos que lo dicen en
        # baruka.pe (catálogo oficial desde el 12-10-2026).
        self.hechos["tallas"].update({"S", "M", "L", "XS", "XL", "2XL", "XXL"})
        self.hechos["pago_ok"] = bool(pago_ok)
        self.fotos: list[dict] = []
        self.usadas: list[str] = []
        self.reserva: dict | None = None
        self.cita: dict | None = None
        self.derivar = False
        self.sugerencia_foto: str | None = None

    # -- puerta -----------------------------------------------------------
    def esquema(self) -> list[dict]:
        return ESQUEMA

    def ejecutar(self, nombre: str, args: dict) -> dict:
        fn = getattr(self, "_t_" + nombre, None)
        if fn is None:
            return {"error": f"herramienta desconocida: {nombre}"}
        self.usadas.append(nombre)
        try:
            return fn(args or {})
        except Exception as e:  # una herramienta rota no tumba el turno: el modelo recibe el error
            return {"error": str(e)}

    def _pide_modelos(self) -> bool:
        """El mensaje pide ver prendas o nombra una (por código o por su nombre), o contesta a «¿cuál?»."""
        if self.contesta_cual or RE_PIDE_MODELOS.search(self.mensaje):
            return True
        if RE_VERBO_PEDIR.search(self.mensaje) and RE_PRENDA.search(self.mensaje):
            return True
        plano = memoria._plano(self.mensaje)
        for f in self.fichas:
            clave = [p for p in memoria._plano(f.nombre).split() if len(p) >= 4 and p not in GENERICAS]
            if clave and all(re.search(r"\b" + re.escape(p) + r"\b", plano) for p in clave[:1]):
                return True
        return False

    # -- fichas -----------------------------------------------------------
    def _ficha(self, codigo: str):
        c = (codigo or "").strip().upper()
        return next((f for f in self.fichas if f.codigo.upper() == c), None)

    def _anota_prenda(self, f, st: dict | None = None) -> dict:
        self.hechos["codigos"].add(f.codigo.upper())
        if f.precio is not None:
            self.hechos["precios"].add(round(float(f.precio), 2))
            self.hechos["precios"].update(round(float(f.precio) + e, 2) for e in ENVIOS)   # total con envío
        if st is None:
            st = (self._stock([f.codigo]) or {}).get(f.codigo.upper(), {})
        disp, agot = stk.tallas_online(st)
        for t in list(disp) + list(agot):
            self.hechos["tallas"].add(t.upper())
        web = ficha_producto.web(f.codigo)
        # Colores y tallas bajo pedido de baruka.pe: sin ellos, «¿lo tienes en otros colores?» se contestaba «solo en
        # el que te mostré» aunque el modelo venga en tres (12-10-2026).
        return {"codigo": f.codigo, "nombre": f.nombre, "precio": f.precio,
                "colores": web.get("colores") or [c.strip() for c in (f.color or "").split(",") if c.strip()],
                "tallas": disp, "agotadas": agot, "bajo_pedido": web.get("bajo_pedido") or [],
                "disponible": stk.estado(st)}

    # -- herramientas -----------------------------------------------------
    def _t_buscar_catalogo(self, args: dict) -> dict:
        consulta = (args.get("consulta") or "").lower()
        cat = (args.get("categoria") or "").lower().strip()
        palabras = [p for p in consulta.replace(",", " ").split() if len(p) >= 3]
        out, sin_cat = [], []
        for f in self.fichas:
            texto = f"{f.codigo} {f.nombre} {f.categoria} {f.color} {f.detalle}".lower()
            if palabras and not any(p in texto for p in palabras):
                continue
            sin_cat.append(f)
            if cat and _cat(f.categoria) != _cat(cat):
                continue
            out.append(f)
        # La categoría que puso el modelo no calza («pantalones», «pantalon» frente a «pantalón») pero el nombre sí: vale
        # el nombre. 14-10-2026: «¿cuánto cuesta el Zury?» → «no encuentro el modelo Zury» con la foto del Zury enviada.
        if not out and palabras and any(_nombre_en(f, palabras) for f in sin_cat):
            out = [f for f in sin_cat if _nombre_en(f, palabras)]
        if not out:  # sin coincidencia por texto, devuelve la categoría entera (si la pidió)
            out = [f for f in self.fichas if cat and _cat(f.categoria) == _cat(cat)]
        if palabras:  # lo que mejor calza con lo pedido primero (nunca el primero del catálogo porque sí)
            # Se ordena ANTES de recortar: con 96 modelos (baruka.pe, 12-10-2026) «vestido Holly» calzaba con todos los
            # vestidos y el corte dejaba fuera al Holly. El nombre pesa más que el color o la descripción.
            out.sort(key=lambda f: -sum(3 * (p in f.nombre.lower()) + (p in f"{f.color} {f.detalle}".lower())
                                        for p in palabras))
        # Dijo cómo quiere sentirse (guion de noche) y no pidió un color: primero las prendas con colores de ese grupo.
        # Orden estable: dentro del grupo sigue mandando lo que calza con lo pedido (14-10-2026).
        grupo = self.grupo if self.grupo and not self.color_pedido and not any(c in consulta.split() for c in COLORES) else ""
        if grupo:
            out.sort(key=lambda f: 0 if colores.del_grupo(f, grupo, self.grupos_cfg) else 1)
        out = out[:MAX_CATALOGO]
        codigos = [f.codigo for f in out]
        estados = self._stock(codigos) if codigos else {}
        res = {"prendas": [self._anota_prenda(f, estados.get(f.codigo.upper())) for f in out]}
        if grupo:
            for p, f in zip(res["prendas"], out):
                p[f"colores_{colores.NOMBRE_GRUPO[grupo]}"] = colores.del_grupo(f, grupo, self.grupos_cfg)
            res["aviso_estilo"] = (f"Quiere verse {'llamativa' if grupo == 'vibrante' else 'elegante y discreta'}: ofrece primero "
                                   f"las prendas con colores_{colores.NOMBRE_GRUPO[grupo]} y nombra ese color (la foto irá en él).")
        # El color que pidió y que NINGUNA prenda tiene: se dice, no se disimula con otras prendas.
        hay = " ".join(f"{f.color} {f.nombre} {f.detalle}".lower() for f in self.fichas)
        falta = [c for c in COLORES if c in consulta.split() and c not in hay]
        if falta:
            res["sin_color"] = falta
            res["aviso"] = (f"NO tenemos nada color {', '.join(falta)}. Dilo claro y ofrece lo más cercano; "
                            "no afirmes que lo tienes.")
        return res

    def _t_stock(self, args: dict) -> dict:
        f = self._ficha(args.get("codigo", ""))
        if f is None:
            return {"error": "no conozco ese código"}
        return self._anota_prenda(f)

    def _t_ficha_tecnica(self, args: dict) -> dict:
        f = self._ficha(args.get("codigo", ""))
        if f is None:
            return {"error": "no conozco ese código"}
        self.hechos["codigos"].add(f.codigo.upper())
        det = ficha_producto.ficha(f.codigo) or {}
        tela = venta.tela(f.codigo, f.detalle)
        if tela:
            self.hechos["telas"].add(re.sub(r"^tela\s+", "", tela.strip().lower()))
        for v in det.values():
            if isinstance(v, str) and v.strip():
                self.hechos["telas"].add(v.strip().lower())
        return {"codigo": f.codigo, "nombre": f.nombre, "ficha": det, "tela": tela}

    def _t_enviar_fotos(self, args: dict) -> dict:
        out = []
        # Una foto ya enviada solo se repite si la pide de nuevo. El tope de cuántas mandar (1, salvo que
        # pida varias) lo aplica agente.py sobre lo que se le muestra a la clienta, no aquí: la herramienta
        # debe marcar TODO lo que el modelo pida explícitamente (si no, un test de 2 códigos pierde el 2°).
        explicita = bool(RE_PIDE_FOTO.search(self.mensaje))
        if self.sin_color:
            return {"error": f"No tenemos nada color {self.sin_color}: dilo y pregúntale si quiere ver otro color; no "
                             "mandes otra prenda como si fuera de ese color."}
        if self.bloquear_fotos:
            return {"error": "No mandes fotos en este turno: contesta la objeción y vuelve a la pregunta pendiente."}
        if self.retirado and not RE_OTRAS.search(self.mensaje):
            return {"error": f"El {self.retirado} ya no se vende: díselo y pregúntale si quiere ver otras opciones; no "
                             "mandes otra prenda en su lugar."}
        if self.sin_cual:
            return {"error": "Pregunta por «este» modelo sin decir cuál: pídele la foto o el nombre; no adivines."}
        if self.mensaje and not self._pide_modelos():   # sin mensaje = llamada interna, no un turno de la clienta
            return {"error": "No mandes fotos ni ofrezcas modelos: ella no pidió verlos. Sigue el guion (indagar) "
                             "y muéstralos solo cuando los pida."}
        for c in (args.get("codigos") or [])[:MAX_FOTOS]:
            f = self._ficha(c)
            if f is None or not f.imagen:
                continue
            # Ya mostrada: no se repite, salvo que pida otra vez la foto o que esta vaya en OTRO color que la principal (la
            # prenda vista en azul noche y ahora pregunta por el verde olivo, 14-10-2026: salía «no pude enviar la foto»).
            otro_color = bool(self.color_pedido) and fotos_color.foto_del_color(f.codigo, self.color_pedido)[0] \
                not in ("", f"{f.codigo.lower()}.jpg")
            if f.codigo.upper() in self.mostrados and not explicita and not otro_color:
                continue
            imagen, color_foto, sin_color = f.imagen, fotos_color.color_de(f"{f.codigo.lower()}.jpg"), ""
            if self.color_pedido:                # la foto del color que pidió («azul» → Blear azul noche, no la morada)
                archivo, c_web = fotos_color.foto_del_color(f.codigo, self.color_pedido)
                if archivo:
                    imagen, color_foto = f"/media/tienda/{archivo}", c_web
                else:                            # no hay foto en ese color: va la principal y se dice (13-10-2026)
                    sin_color = self.color_pedido
            elif self.grupo:                     # sin color pedido: la foto en un color del grupo que eligió (14-10-2026)
                archivo, c_web = colores.foto_del_grupo(f.codigo, self.grupo, self.grupos_cfg)
                if archivo:
                    imagen, color_foto = f"/media/tienda/{archivo}", c_web
            out.append(self._foto(f, imagen, color_foto, sin_color))
        # Se SUMAN a las ya marcadas (sin repetir): cuando el código mandó los candidatos de «¿quieres ver otros modelos?»
        # y el modelo llamaba igual a enviar_fotos con uno solo, pisaba la lista y salía una foto de tres (13-10-2026).
        vistos = {x["codigo"].upper() for x in self.fotos}
        self.fotos += [x for x in out if x["codigo"].upper() not in vistos]
        res = {"enviadas": [{"codigo": x["codigo"], "foto_en_color": x["color_foto"] or "el de la foto principal"} for x in out]}
        if faltan := [f"{x['codigo']} no tiene foto en {x['sin_color']}: va en {x['color_foto'] or 'su color principal'}"
                      for x in out if x["sin_color"]]:
            res["aviso"] = "Nombra el color REAL de cada foto; " + "; ".join(faltan) + ". Dilo tal cual, no digas que va en el color pedido."
        return res

    def _foto(self, f, imagen: str, color_foto: str, sin_color: str = "", pie_color: bool = False) -> dict:
        """La tarjeta de una foto (imagen, pie, título, tallas) y lo que ya se puede afirmar de ella."""
        st = self._stock([f.codigo]).get(f.codigo.upper(), {})
        pie = self._pie(f, st) if self._pie else f"{f.codigo} {f.nombre}"
        if pie_color and color_foto:             # varias fotos del mismo modelo: cada pie dice su color
            primera, _, resto = pie.partition("\n")
            pie = f"{primera} · {color_foto}" + (f"\n{resto}" if resto else "")
        online = (st.get("online") or {}) if st.get("product") else {}
        tallas = [{"talla": t, "disponible": online[t] > 0, "precio": f.precio, "codigo": f.codigo}
                  for t in sorted(online, key=lambda t: ORDEN_TALLAS.index(t) if t in ORDEN_TALLAS else 99)]
        titulo = f"*{f.codigo}* {f.nombre}" + (f" — *{MONEDA} {f.precio:.2f}*" if f.precio is not None else "")
        self.hechos["codigos"].add(f.codigo.upper())
        self.hechos["fotos"].add(f.codigo.upper())
        if f.precio is not None:
            self.hechos["precios"].add(round(float(f.precio), 2))
        return {"codigo": f.codigo, "nombre": f.nombre, "imagen": imagen, "pie": pie, "titulo": titulo, "tallas": tallas,
                "color_foto": color_foto, "sin_color": sin_color}

    def enviar_colores(self, codigo: str, pedidos=None, saltar: str = "") -> dict:
        """Las fotos de UNA prenda en sus colores: los `pedidos` (los que nombró) o, sin ellos, todos los que tienen foto,
        hasta MAX_FOTOS_COLORES, sin el color `saltar` (el que ya vio) si quedan otros. No es una herramienta del LLM: la
        llama el código cuando ella pide ver los colores de la prenda en foco (14-10-2026, Vestido Salome).
        Devuelve {"con_foto": [colores], "sin_foto": [colores]}; las fotos se SUMAN a las ya marcadas."""
        f = self._ficha(codigo)
        if f is None:
            return {"con_foto": [], "sin_foto": []}
        web = colores.colores_de(f)
        lista = list(pedidos) if pedidos else [c for c in web if not fotos_color.es_personalizado(c)]
        fotos = fotos_color.fotos_de_colores(f.codigo, lista)
        if not pedidos and saltar and len(fotos) > 1:
            fotos = [x for x in fotos if colores.normaliza(x[1]) != colores.normaliza(saltar)] or fotos
        fotos = fotos[:MAX_FOTOS_COLORES]
        sin_foto = [c for c in lista if not fotos_color.foto_del_color(f.codigo, c)[0]]
        ya = {(x["codigo"].upper(), x["imagen"]) for x in self.fotos}
        for archivo, color in fotos:
            imagen = f"/media/tienda/{archivo}"
            if (f.codigo.upper(), imagen) not in ya:
                self.fotos.append(self._foto(f, imagen, color, pie_color=True))
        return {"con_foto": [c for _, c in fotos], "sin_foto": sin_foto}

    def _t_enviar_catalogo(self, args: dict) -> dict:
        t = colecciones.tarjeta((args.get("seccion") or "").strip())
        if t is None:
            return {"error": "esa sección no existe"}
        if t["imagen"] not in {x["imagen"] for x in self.fotos}:
            self.fotos.append(t)
        self.catalogo_ok = True
        return {"enviado": t["url"], "nota": "La captura y el enlace van como foto: no repitas el enlace en tu texto."}

    def _t_crear_reserva(self, args: dict) -> dict:
        if not self.permitir_reserva:
            return {"error": "la reserva todavía la maneja el flujo base"}
        f = self._ficha(args.get("codigo", ""))
        talla = (args.get("talla") or "").strip().upper()
        if f is None or not talla:
            return {"error": "faltan código o talla"}
        self.reserva = {"codigo": f.codigo, "talla": talla}
        self.hechos["codigos"].add(f.codigo.upper())
        return {"ok": True, "codigo": f.codigo, "talla": talla}

    def _t_agendar_cita(self, args: dict) -> dict:
        dia, hora = (args.get("dia") or "").strip(), (args.get("hora") or "").strip()
        err = memoria.validar_cita(dia, hora, memoria.ahora_lima())
        if err:
            return {"ok": False, "motivo": err}
        self.cita = {"dia": dia, "hora": hora, "codigo": (args.get("codigo") or "").upper(),
                     "talla": (args.get("talla") or "").upper()}
        return {"ok": True, **self.cita}

    def _t_derivar_asesora(self, args: dict) -> dict:
        self.derivar = True
        return {"ok": True, "motivo": (args.get("motivo") or "").strip()}
