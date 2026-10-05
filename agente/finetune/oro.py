"""Conversaciones paso a paso contra el agente real, para escribir el «oro» o para evaluar un modelo.

El agente corre en su contenedor con `DEEPSEEK_URL` apuntando a `puente.py`. Este script guarda el estado de cada
conversación entre turnos como el bot Go (historial, etapa, memoria, estado del pedido) y deja que alguien escriba
lo que falta, por lotes y en rondas:

- **modo oro** (`/oro` del puente): la clienta y la vendedora las escribe una persona (o un modelo). Cada mensaje de la
  clienta se manda al agente; si el agente necesita al LLM, el puente devuelve un marcador con la clave del prompt y
  este script enseña el CONTEXTO que vería el LLM (el mismo que arma `_prompt_comercial`). La redactora escribe el JSON
  de `estructurado.py` ({responde, por_que, pregunta}); se registra en el puente y se vuelve a llamar al agente con la
  misma entrada, que ahora arma el mensaje final con su código de siempre. El par (contexto, JSON) es un ejemplo de
  entrenamiento.
- **modo eval** (`/up/<variante>` del puente → `mlx_lm.server`): la vendedora es el modelo; solo se escribe la clienta.

    python3 finetune/oro.py specs                                    # 200 conversaciones, 160 entrenamiento / 40 reservadas
    python3 finetune/oro.py ver --lote L01                           # qué toca escribir en el lote
    python3 finetune/oro.py avanzar --lote L01 --entrada r.json      # aplica lo escrito y enseña lo siguiente
    python3 finetune/oro.py exportar                                 # datos/oro.jsonl (una conversación por línea)

Formato de `--entrada`: lista de {"id": …, "vendedora": {"responde", "por_que", "pregunta"} | "vendedora_texto": "…",
"clienta": "…" | "[FOTO] …" | "[FIN]"}. La vendedora se aplica antes que la clienta.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import datetime as dt
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, ".."))
from app import memoria  # noqa: E402  (solo biblioteca estándar)

DATOS = os.path.join(AQUI, "datos")
IMAGENES = os.path.join(AQUI, "..", "imagenes", "tienda")
RE_PEND = re.compile(r"PENDIENTE-([0-9a-f]{16})")
RE_SI = re.compile(r"^\s*(s[ií]+|si+p|ok|dale|confirmo|claro|ya|listo|s[ií],? confirm\w*|confirmar)\b[\s.!👍]*$", re.I)

# ---------------------------------------------------------------------------
# Personas: (clave, persona, objetivo, plan, anuncio, peso). Las 28 de app/conversaciones.py inspiraron varias; aquí
# hay más y se reparten distinto. «anuncio»: True llega por el anuncio del V42; False no; None al azar (20 %).
PERSONAS = [
    ("saludo_solo", "Solo saludas («hola», «buenas») y esperas a que te pregunten. No das datos si no te los piden.",
     "Ver qué te ofrecen.", ["saluda y nada más", "contesta corto a lo que te pregunten (es para un cumpleaños)",
                              "pregunta qué tienen", "pregunta el precio de lo que te muestren"], False, 6),
    ("busco_vestido", "Escribes «busco un vestido» sin más.", "Encontrar vestido para una boda de día.",
     ["di que buscas un vestido", "di que es para una boda", "di la fecha (en noviembre)", "di que es de día",
      "pregunta el precio"], False, 6),
    ("ver_modelos", "Quieres ver los modelos sin contar para qué.", "Ver catálogo.",
     ["di que te gustaría ver los modelos", "contesta la ocasión de mala gana (un cumpleaños)", "pide «muéstrame más»",
      "pregunta el precio del que te guste"], False, 6),
    ("fecha_objecion", "Contestas con la fecha cuando te preguntan algo («el 24 de octubre»).", "Vestido de graduación.",
     ["di que buscas vestido para graduación", "di «el 24 de octubre»", "di que es de noche", "di «me gusta»",
      "pregunta cuánto está"], False, 5),
    ("como_pago_temprano", "Antes de elegir nada preguntas cómo se paga y si hay contraentrega.", "Saber cómo pagar.",
     ["saluda y pregunta cómo se paga", "pregunta si hay contraentrega", "di que es para un bautizo",
      "contesta lo que te pregunten", "pregunta el precio"], False, 5),
    ("muestrame_mas", "Te muestran uno y siempre pides más.", "Ver varias opciones.",
     ["di que buscas algo para una cena de noche", "contesta lo que te pregunten", "pide «muéstrame más»",
      "pide otro más", "pregunta el precio del que más te guste"], False, 5),
    ("fria_viendo", "Estás curioseando sin evento definido; quizá para algo del próximo año. No tienes apuro.",
     "Ver qué tienen sin comprometerte.", ["saluda", "di que solo estás viendo", "pregunta qué vestidos tienen",
                                           "pregunta el precio de lo que te muestren", "di que lo vas a pensar y despídete"], False, 7),
    ("apurada", "Tienes {ocasion} en 3 días ({fecha_caliente}) y no tienes qué ponerte. Estás apurada.",
     "Conseguir algo YA y probártelo mañana.", ["cuenta tu urgencia", "contesta lo que te pregunten",
                                                 "pide que te recomiende", "pregunta precio y si hay en M",
                                                 "pide ir a probártelo mañana a la 1:30 pm", "acepta otra hora razonable"], False, 8),
    ("anuncio_precio", "Viste el anuncio del vestido en Facebook y escribes desde ahí sin decir el nombre.",
     "Saber precio, tallas, tela y envío a provincia ({ciudad}).", ["pregunta el precio del vestido", "pregunta si hay en S",
                                                                   "pregunta de qué tela es", "pregunta el envío a {ciudad}",
                                                                   "di que lo quieres comprar"], True, 8),
    ("anuncio_duda", "Viste el anuncio. Te gustó pero dudas del precio y del largo (mides 1.55).", "Resolver dudas; no compras hoy.",
     ["pregunta si todavía tienen ese vestido", "pregunta si es muy largo para tu estatura", "di que está un poco caro",
      "pregunta si hay descuento", "di que lo vas a pensar"], True, 6),
    ("anuncio_compra", "Llegas por el anuncio del vestido azul y lo quieres. Eres de Lima, talla M.", "Comprarlo y pagar hoy.",
     ["pregunta por el vestido del anuncio", "di que es para una gala de noche", "pregunta precio", "di que lo quieres en M",
      "confirma el pedido (SI)", "di que es para Lima", "pide los datos de pago", "[FOTO] del voucher"], True, 6),
    ("pide_de_frente", "Llegas por el anuncio y quieres ver el vestido de espalda y de cerca, y un video.", "Ver más del vestido.",
     ["pide ver el vestido de espalda", "pregunta si tienen video", "pregunta si la pedrería es cosida o pegada",
      "di que es para un matrimonio de noche", "pregunta el precio"], True, 4),
    ("este_vestido", "Viste un vestido en un estado de WhatsApp de una amiga; NO llegaste por anuncio y no sabes el nombre.",
     "Saber si lo tienen.", ["pregunta «hola tienen este vestido?» sin decir cuál", "responde «ah sí, un momento»",
                             "describe el vestido: negro, a la rodilla, hombros descubiertos", "pregunta el precio",
                             "pregunta si lo puedes ver en persona"], False, 5),
    ("manda_foto", "Tienes una captura del vestido que te gustó.", "Saber si lo tienen y en qué talla.",
     ["saluda y di que te gustó un vestido", "manda la foto ([FOTO])", "pregunta si hay en M", "pregunta el envío en Lima",
      "di que lo quieres"], False, 5),
    ("pide_ver_ya", "Quieres fotos de inmediato, sin explicar.", "Ver opciones.",
     ["pide fotos de sus vestidos", "contesta de mala gana o insiste en ver", "pide ver otros", "pregunta el precio"], False, 5),
    ("cambia_prenda", "Empiezas buscando vestido y cambias a algo para la oficina (conjunto o blazer).", "Algo formal para trabajar.",
     ["di que buscas un vestido", "cambia: mejor algo para la oficina", "pregunta qué blazers tienen", "pregunta la tela",
      "pregunta el precio"], False, 5),
    ("tela_corte", "Detallista: tela, forro, corte, si es entallado. Es para {ocasion} ({fecha_tibia}).", "Entender la prenda.",
     ["di para qué ocasión buscas", "contesta lo que te pregunten", "pregunta de qué tela es", "pregunta si tiene forro y si es entallado",
      "pregunta si es largo", "pregunta el precio"], False, 6),
    ("tallas", "No sabes tu talla: mides 1.62, pesas 68 kilos, usas L o XL.", "Saber qué talla te queda.",
     ["pregunta por un vestido para {ocasion}", "pregunta qué talla te quedaría", "pregunta si tienen XL",
      "pregunta si se puede ajustar", "di que lo vas a pensar"], False, 5),
    ("envio_provincia", "Vives en {ciudad}. Ya casi decidiste.", "Saber envío, pago y comprar.",
     ["di que eres de {ciudad} y preguntas si envían", "pregunta el costo del envío", "pregunta cómo se paga",
      "di que quieres el vestido Pandora talla M", "confirma la compra (SI)", "di que es para provincia, {ciudad}"], False, 5),
    ("ubicacion", "Prefieres ver en persona.", "Ir al showroom.",
     ["pregunta dónde quedan", "pregunta si puedes ir hoy a las 8 de la noche", "pregunta el horario del domingo",
      "pide cita para probarte un vestido de noche", "propón una hora válida"], False, 5),
    ("regateo", "Te encantó un vestido pero está caro. Regateas como en Gamarra.", "Conseguir descuento.",
     ["pregunta por el vestido Holly", "pregunta el precio", "di que en Gamarra hay más barato", "pide que te lo dejen en 250",
      "pregunta si llevando dos hay descuento", "di que lo vas a pensar"], False, 5),
    ("queja", "Compraste hace una semana y tu pedido no llega; estás molesta.", "Que te solucionen.",
     ["di que tu pedido no llega", "insiste en que nadie te responde", "pide hablar con una persona",
      "pregunta por la política de devoluciones"], False, 4),
    ("fuera_rubro", "Bromista: preguntas cosas que no tienen nada que ver.", "Pasar el rato y luego algo real.",
     ["pregunta quién ganó el partido de ayer", "pregunta cuánto es 25 por 4", "pregunta si eres un bot",
      "pregunta si tienen vestidos para {ocasion}"], False, 4),
    ("lo_pienso_vuelve", "Te interesa algo para {ocasion} ({fecha_tibia}) pero lo piensas; vuelves otro día.", "Decidir con calma.",
     ["cuenta tu ocasión", "contesta lo que te pregunten", "pregunta el precio de lo que te recomienden", "di que lo vas a pensar",
      "vuelve: «hola de nuevo, te escribí el otro día por el vestido»", "pregunta si aún lo tienen en S"], False, 5),
    ("clienta_vuelve", "Ya compraste antes el vestido Irla en talla M. Vuelves por otro para {ocasion}.", "Otra compra.",
     ["saluda y di que ya compraste antes", "cuenta tu ocasión", "contesta lo que te pregunten", "pregunta el precio",
      "pide separarlo"], False, 4),
    ("compra_rapida", "Ya sabes lo que quieres: el vestido Irla en talla M. Pagas rápido.", "Comprar hoy.",
     ["pide el vestido Irla en talla M", "di que lo quieres comprar", "confirma el pedido (SI)", "di que es para Lima",
      "pide los datos para pagar", "di que ya pagaste"], False, 5),
    ("cita", "Quieres probártelo antes. Es para {ocasion} ({fecha_tibia}).", "Agendar una cita.",
     ["cuenta para qué lo buscas", "contesta lo que te pregunten", "di que quieres ir a probártelo",
      "propón el domingo a las 7:30 de la noche", "propón el sábado a las 11 de la mañana", "di tu talla (L)"], False, 6),
    ("nombra_prenda", "Viste el catálogo en Diners y conoces los nombres.", "Comparar dos modelos.",
     ["pregunta si el vestido Kendall tiene en M", "pide la foto del Holly", "pregunta cuál te recomienda para una cena de noche",
      "pregunta la tela del que te recomiende"], False, 5),
    ("audio", "Prefieres mandar audios; hablas mucho.", "Vestido para {ocasion}.",
     ["manda un [audio] largo contando tu evento ({fecha_tibia}, de noche)", "contesta lo que te pregunten", "pregunta el precio",
      "pregunta si hacen delivery en Lima"], False, 5),
    ("para_mama", "Buscas un regalo para tu mamá (talla L, 1.58 m) para su cumpleaños.", "Regalo para mamá.",
     ["di que buscas algo para tu mamá", "da su talla y estatura", "pregunta qué le recomiendan", "pregunta el precio"], False, 4),
    ("hombre_pareja", "Eres un hombre buscando ropa formal de regalo para tu pareja; no sabes de ropa.", "Comprar un regalo.",
     ["di que buscas ropa formal para tu pareja", "di que no sabes su talla, que es delgada", "pregunta qué le recomiendan",
      "pregunta si hay otros colores", "pregunta si se puede cambiar de talla si no le queda"], False, 3),
    ("emojis", "Respondes con poco: emojis, «jaja», «ok», «mmm».", "No queda claro qué quieres.",
     ["saluda con un emoji", "responde «mmm» o «jaja»", "di «ok»", "pregunta qué hay de nuevo"], False, 4),
    ("colores", "Te gustó un modelo pero quieres otro color.", "Encontrar el color.",
     ["pregunta si el vestido Irla lo tienen en rojo", "pregunta qué otros colores hay", "pide ver opciones en rojo",
      "pregunta el precio"], False, 4),
    ("evento_lejano", "Tienes {ocasion} recién en febrero del próximo año.", "Adelantarte.",
     ["cuenta tu ocasión y que es en febrero", "contesta lo que te pregunten", "pregunta si te pueden separar algo",
      "pregunta el precio"], False, 4),
    ("presupuesto", "Tu presupuesto es de máximo 250 soles.", "Algo bonito dentro del presupuesto.",
     ["di que buscas vestido para {ocasion} y que tienes máximo 250 soles", "contesta lo que te pregunten", "pide que te muestre",
      "pregunta si hay algo más barato"], False, 5),
    ("todo_junto", "Escribes todo de una: ocasión, fecha, día/noche y talla.", "Que te recomienden ya.",
     ["escribe: es {ocasion} el {fecha_tibia} en la noche, soy talla M, qué me recomiendas", "pregunta el precio",
      "pregunta si te lo puedes probar"], False, 5),
    ("novia", "TÚ te casas por civil, ceremonia sencilla de día.", "Vestido para tu boda civil.",
     ["di que te casas por civil y buscas vestido", "di que es de día", "pregunta si tienen algo blanco o claro",
      "pregunta el precio"], False, 3),
    ("cambios", "Antes de comprar quieres saber si puedes cambiar la talla si no te queda.", "Comprar sin riesgo.",
     ["pregunta por el conjunto Kabanova azul", "pregunta si lo puedes cambiar de talla si no te queda",
      "pregunta cuánto demora el envío", "pregunta el precio"], False, 4),
]

OCASIONES = ["el matrimonio de mi prima", "una boda de una amiga", "la graduación de mi hija", "mi graduación",
             "un quinceañero", "la cena de fin de año de mi empresa", "un bautizo", "mi cumpleaños", "una gala de la promoción",
             "una entrevista de trabajo", "un compromiso", "el aniversario de mis papás"]
CIUDADES = ["Arequipa", "Trujillo", "Piura", "Cusco", "Chiclayo", "Huancayo", "Ica", "Tacna"]
NOMBRES = ["Carmen", "Rosa", "Lucía", "Milagros", "Katherine", "Diana", "Gabriela", "Jessica", "Fiorella", "Andrea",
           "Pamela", "Yesenia", "Lorena", "Claudia", "Sofía", "Valeria", "Mariela", "Estefany", "Roxana", "Gisela"]
ESTILOS = {
    "correcta": "Escribes bien, con tildes, pero corto como en WhatsApp.",
    "informal": "Minúsculas, sin tildes ni signos de apertura, abreviaturas (q, xq, pa, tmb, xfa, pls).",
    "jerga": "Jerga peruana de Lima: «ps», «pe», «al toque», «xfa», «chévere», «bacán», «oe», «ya fue» (una o dos por mensaje).",
    "errores": "Faltas de ortografía y de tipeo («ke», «bestido», «cuanto questa», «tiens», «nesesito», «aver»), sin tildes.",
    "partidos": "Mandas ideas en mensajes cortitos seguidos: sepáralos con « || » (a veces).",
    "audios": "A veces mandas un audio: escribe «[audio] » y lo que dirías hablando, con muletillas.",
}
N_TOTAL, N_RESERVADAS, N_LOTES = 200, 40, 10
SEMILLA = 4242


def specs(semilla: int = SEMILLA, hoy: dt.date = dt.date(2026, 10, 4)) -> list[dict]:
    rng = random.Random(semilla)
    meses = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")
    f = lambda d: f"{d.day} de {meses[d.month - 1]}"
    # Al menos dos de cada persona; el resto por peso.
    elegidas = [p for p in PERSONAS for _ in range(2)]
    while len(elegidas) < N_TOTAL:
        elegidas.append(rng.choices(PERSONAS, weights=[p[5] for p in PERSONAS])[0])
    rng.shuffle(elegidas)
    convs, n_por = [], {}
    for clave, desc, obj, plan, anuncio, _ in elegidas:
        n_por[clave] = n_por.get(clave, 0) + 1
        fmt = {"ocasion": rng.choice(OCASIONES), "ciudad": rng.choice(CIUDADES),
               "fecha_caliente": rng.choice(["este sábado", "el " + f(hoy + dt.timedelta(days=3)), "pasado mañana"]),
               "fecha_tibia": f(hoy + dt.timedelta(days=rng.randint(10, 28)))}
        c = {"id": f"{clave}_{n_por[clave]:02d}", "persona_clave": clave, "persona": desc.format(**fmt),
             "objetivo": obj.format(**fmt), "plan": [p.format(**fmt) for p in plan],
             "estilo": rng.choices(list(ESTILOS), weights=[3, 4, 3, 2, 2, 1])[0],
             "desde_anuncio": anuncio if anuncio is not None else rng.random() < 0.2,
             "canal": "", "cliente": rng.choice(NOMBRES), "turnos_max": rng.randint(max(4, len(plan) - 1), min(10, len(plan) + 3)),
             "foto": rng.choice(["v35", "v31", "v41", "v21", "v28", "v42", "v24"])}
        if clave == "clienta_vuelve":
            c["perfil"] = {"nombre": c["cliente"], "tallas": ["M"], "productos": ["V35"], "pedidos": 1}
        convs.append(c)
    # 40 reservadas, estratificadas: una de cada persona que tenga 3 o más, y el resto al azar entre las demás.
    por = {}
    for c in convs:
        por.setdefault(c["persona_clave"], []).append(c)
    res = []
    for k in sorted(por):
        if len(por[k]) >= 3:
            res.append(rng.choice(por[k]))
    resto = [c for c in convs if c not in res and len(por[c["persona_clave"]]) >= 2]
    rng.shuffle(resto)
    ya = {c["persona_clave"] for c in res}
    for c in resto:
        if len(res) >= N_RESERVADAS:
            break
        # Nunca todas las de una persona a reservadas: el modelo tiene que haber visto el caso.
        if sum(1 for x in res if x["persona_clave"] == c["persona_clave"]) + 1 < len(por[c["persona_clave"]]):
            res.append(c)
    ids_res = {c["id"] for c in res}
    for i, c in enumerate(sorted(convs, key=lambda x: x["id"])):
        c["conjunto"] = "reservada" if c["id"] in ids_res else "entrenamiento"
    orden = sorted(convs, key=lambda x: (x["conjunto"], x["id"]))
    for i, c in enumerate(orden):
        c["lote"] = f"L{i % N_LOTES + 1:02d}"
    return orden


# ---------------------------------------------------------------------------
# Estado

def _ruta_estado(cid: str, modo: str) -> str:
    d = os.path.join(DATOS, modo, "estado")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, cid + ".json")


def cargar_estado(c: dict, modo: str) -> dict:
    r = _ruta_estado(c["id"], modo)
    if os.path.exists(r):
        with open(r, encoding="utf-8") as fh:
            return json.load(fh)
    return {"id": c["id"], "historial": [], "etapa": "", "memoria": None, "estado": "", "producto": "", "talla": "",
            "turnos": [], "espera": None, "fin": False}


def guardar_estado(s: dict, modo: str) -> None:
    with open(_ruta_estado(s["id"], modo), "w", encoding="utf-8") as fh:
        json.dump(s, fh, ensure_ascii=False)


def _post(url: str, cuerpo: dict, timeout: float = 300) -> dict:
    r = urllib.request.Request(url, json.dumps(cuerpo).encode(), {"content-type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=timeout))


def _img(codigo: str) -> str:
    with open(os.path.join(IMAGENES, f"{codigo.lower()}.jpg"), "rb") as fh:
        return base64.b64encode(fh.read()).decode()


def _cuerpo(c: dict, s: dict, texto: str, foto: str | None) -> dict:
    b = {"historial": s["historial"][-30:], "cliente": c.get("cliente", ""), "canal": c.get("canal", ""), "motor": "deepseek",
         "etapa": s["etapa"], "conversacion": c["id"], "desde_anuncio": bool(c.get("desde_anuncio")), "estado": s["estado"],
         "producto": s["producto"], "talla": s["talla"]}
    if s["memoria"] is not None:
        b["memoria"] = s["memoria"]
    if c.get("perfil"):
        b["perfil"] = c["perfil"]
    if foto:
        b |= {"imagen_b64": _img(foto), "mensaje": texto}
    else:
        b["mensaje"] = texto or "."
    return b


def _go(s: dict, texto: str, foto: str | None) -> dict | None:
    """Lo mínimo del bot Go en WhatsApp: el SI del resumen, el voucher y la dirección no pasan por el agente."""
    if s["estado"] == "esperando_confirmacion" and not foto and RE_SI.match(texto or ""):
        s["estado"] = "esperando_pago"
        if s["memoria"]:
            s["memoria"]["pendiente"], s["memoria"]["etapa"] = "lima_o_provincia", "venta_confirmada"
        return {"respuesta": "✅ ¡Listo! Tu pedido quedó confirmado.\n\n¿El envío es para *Lima* o para *provincia*?",
                "etapa": "venta_confirmada", "accion": "go_confirmar", "sugerencias": []}
    if s["estado"] == "esperando_pago" and foto:
        s["estado"] = "esperando_ubicacion"
        if s["memoria"]:
            s["memoria"]["pendiente"] = "direccion"
        return {"respuesta": "🙌 ¡Recibí tu comprobante! Lo anoto en tu pedido.\n\n¿Me compartes la dirección de entrega (o la agencia)?",
                "etapa": "venta_confirmada", "accion": "go_voucher", "sugerencias": []}
    if s["estado"] == "esperando_ubicacion" and not foto:
        s["estado"] = "entregado_a_asesora"
        return {"respuesta": "¡Gracias! 💙 Con eso programamos tu envío y una asesora te escribe para confirmar la entrega.",
                "etapa": "venta_confirmada", "accion": "go_ubicacion", "sugerencias": []}
    return None


def _aplicar(s: dict, c: dict, texto: str, foto: str | None, j: dict, ms: int, extra: dict) -> None:
    """Guarda el turno y deja el estado como lo dejaría el bot Go."""
    mem_antes = json.loads(json.dumps(s["memoria"])) if s["memoria"] is not None else None
    etapa_antes = s["etapa"]
    s["etapa"] = j.get("etapa") or s["etapa"]
    s["memoria"] = j.get("memoria", s["memoria"])
    if j.get("accion") == "pedido":
        s["estado"], s["producto"], s["talla"] = "esperando_confirmacion", j.get("codigo", ""), j.get("talla", "")
    elif j.get("accion") not in ("go_confirmar", "go_voucher", "go_ubicacion") and s["estado"] == "esperando_confirmacion" and s["etapa"] != "cierre":
        s["estado"] = ""
    sug = j.get("sugerencias") or []
    com = j.get("comercial") or {}
    s["turnos"].append({
        "k": len(s["turnos"]), "cliente": ("[FOTO] " if foto else "") + (texto or ""), "foto": foto, "bot": j.get("respuesta", ""),
        "http": extra.pop("http", 200), "error": j.get("error", ""), "ms": ms,
        "fotos": [x.get("codigo") for x in sug if "_material" not in (x.get("imagen") or "") and x.get("pie", "")[:12] != "✨ *Material"],
        "fotos_txt": [f"{x.get('codigo')} {x.get('nombre')}" for x in sug],
        "etapa_antes": etapa_antes, "etapa": s["etapa"], "intent": com.get("intent"), "confianza": com.get("confianza"),
        "fuente": com.get("fuente"), "motivo": com.get("motivo"), "accion": j.get("accion"), "modelo": j.get("modelo_llm"),
        "mem_antes": mem_antes, "memoria": s["memoria"], "siguiente": j.get("siguiente_pregunta"),
        "tallas": [x.get("talla") for x in j.get("tallas") or []], "foto_caso": (j.get("foto") or {}).get("caso"), **extra})
    s["historial"].append({"rol": "cliente", "texto": ("" if not foto else "[foto] ") + (texto or "")})
    for p in (j.get("respuesta") or "").split("\n\n"):
        if p.strip():
            s["historial"].append({"rol": "bot", "texto": p})
    for x in sug:
        s["historial"].append({"rol": "bot", "texto": x.get("pie", "")})


def _llamar(url: str, c: dict, s: dict, texto: str, foto: str | None) -> tuple[dict, int, int]:
    t0 = time.time()
    try:
        j = _post(url + ("/foto" if foto else "/chat"), _cuerpo(c, s, texto, foto))
        http = 200
    except urllib.error.HTTPError as e:
        j, http = {"respuesta": "", "error": f"HTTP {e.code} {e.read()[:200]!r}"}, e.code
    except Exception as e:  # noqa: BLE001
        j, http = {"respuesta": "", "error": str(e)[:200]}, 0
    return j, int((time.time() - t0) * 1000), http


def enviar_clienta(url: str, c: dict, s: dict, crudo: str, modo: str) -> None:
    """Un mensaje de la clienta (puede traer varios con « || »). En modo oro se para en el primero que necesite al LLM."""
    fin = "[FIN]" in crudo.upper()
    crudo = re.sub(r"\[fin\]", "", crudo, flags=re.I).strip()
    partes = [p.strip() for p in crudo.split("||") if p.strip()]
    for i, parte in enumerate(partes):
        foto = None
        if "[FOTO]" in parte.upper():
            foto, parte = c.get("foto", "v35"), re.sub(r"\s*\[foto\]\s*", " ", parte, flags=re.I).strip()
        go = _go(s, parte, foto)
        if go is not None:
            _aplicar(s, c, parte, foto, go, 0, {"http": 200})
            continue
        j, ms, http = _llamar(url, c, s, parte, foto)
        if http == 0 and modo == "oro":
            # El agente no respondió (caído, reiniciándose): no se toca el estado; se reintenta el mismo mensaje después.
            s["reenviar"] = " || ".join(partes[i:]) + (" [FIN]" if fin else "")
            return
        m = RE_PEND.search(j.get("respuesta") or "")
        if modo == "oro" and m:
            pend = _leer_pendiente(m.group(1))
            s["espera"] = {"texto": parte, "foto": foto, "clave": m.group(1), "resto": " || ".join(partes[i + 1:]),
                           "fin": fin, "json": pend.get("json", True)}
            return
        _aplicar(s, c, parte, foto, j, ms, {"http": http})
    if fin or len(s["turnos"]) >= c.get("turnos_max", 10) + 2:
        s["fin"] = True


def enviar_guion(url: str, c: dict, s: dict, m) -> None:
    """Un mensaje de guion fijo. Los chats reales traen {"foto": "v35", "texto": "..."}."""
    if isinstance(m, dict):
        foto, texto = m.get("foto"), m.get("texto", "")
        go = _go(s, texto, foto)
        if go is not None:
            _aplicar(s, c, texto, foto, go, 0, {"http": 200})
            return
        j, ms, http = _llamar(url, c, s, texto, foto)
        _aplicar(s, c, texto, foto, j, ms, {"http": http})
        return
    enviar_clienta(url, c, s, str(m), "eval")
    s["fin"] = False   # el guion decide cuándo termina


def _leer_pendiente(k: str) -> dict:
    r = os.path.join(DATOS, "puente", "pendientes", k + ".json")
    with open(r, encoding="utf-8") as fh:
        return json.load(fh)


def escribir_vendedora(url: str, c: dict, s: dict, salida, modo: str) -> str:
    """Registra lo que escribió la vendedora para el prompt pendiente y vuelve a llamar al agente con la misma entrada."""
    esp = s["espera"]
    pend = _leer_pendiente(esp["clave"])
    if isinstance(salida, dict):
        js = {k: str(salida.get(k) or "").strip() for k in ("responde", "por_que", "pregunta")}
        texto = json.dumps(js, ensure_ascii=False)
    else:
        js, texto = None, str(salida).strip()
    d = os.path.join(DATOS, "puente", "respuestas")
    os.makedirs(d, exist_ok=True)
    for kk in (pend["clave"], pend["clave_suave"]):
        with open(os.path.join(d, kk + ".json"), "w", encoding="utf-8") as fh:
            json.dump({"texto": texto}, fh, ensure_ascii=False)
    j, ms, http = _llamar(url, c, s, esp["texto"], esp["foto"])
    if http == 0:
        return f"{c['id']}: el agente no respondió ({j.get('error', '')[:60]}); manda la misma vendedora en la ronda siguiente"
    if RE_PEND.search(j.get("respuesta") or ""):
        return f"{c['id']}: el agente pidió OTRO prompt al repetir la llamada (no coincide la clave); reintenta este turno"
    _aplicar(s, c, esp["texto"], esp["foto"], j, ms, {"http": http, "llm": True, "oro": {"messages": pend["messages"],
                                                                                         "json": pend.get("json", True),
                                                                                         "salida": js if js is not None else texto}})
    s["espera"] = None
    if esp.get("resto"):
        enviar_clienta(url, c, s, esp["resto"] + (" [FIN]" if esp.get("fin") else ""), modo)
    elif esp.get("fin"):
        s["fin"] = True
    return ""


# ---------------------------------------------------------------------------
# Vista para quien escribe

RE_TIENDA = re.compile(r"\nTIENDA:\n.*?\n\nHISTORIAL:", re.S)
RE_FICHAS = re.compile(r"(PRODUCTO \(fichas; la primera es de la que se habla\):\n)(.*?)(\n\n)", re.S)


def contexto_compacto(user: str) -> str:
    """El prompt del agente sin lo que no cambia (TIENDA) y con las fichas secundarias en una línea."""
    u = RE_TIENDA.sub("\nTIENDA: (la de siempre)\n\nHISTORIAL:", user)

    def fichas(m):
        lineas = m.group(2).split("\n")
        corto = [lineas[0]] + [re.sub(r"\| características:.*?(\| precio:[^|]*)?\|.*", r"\1", x)[:110] for x in lineas[1:]]
        return m.group(1) + "\n".join(corto) + m.group(3)

    u = RE_FICHAS.sub(fichas, u, count=1)
    u = re.sub(r"\nFORMATO DE SALIDA.*", "", u, flags=re.S)
    u = re.sub(r" \| imagen: [^|\n]*", "", u)
    # La guía de la etapa es siempre la misma (está en las instrucciones): se deja solo lo que cambia.
    g = re.search(r"(ETAPA ACTUAL: [^\n]*\n)(.*?)(\n\nLO QUE YA SABEMOS|\n\nLO QUE ACABA)", u, re.S)
    if g:
        quedan = [x for x in g.group(2).split("\n")
                  if re.match(r"(TEMPERATURA|- PASO PENDIENTE|TOTALES|- Envío|PAGO|El pedido ya está confirmado)", x)]
        u = u[:g.start(2)] + "\n".join(quedan) + u[g.end(2):]
    h = re.search(r"HISTORIAL:\n(.*?)\n\nPRODUCTO", u, re.S)
    if h:
        lineas = h.group(1).split("\n")
        if len(lineas) > 10:
            u = u.replace(h.group(1), "(…)\n" + "\n".join(lineas[-10:]))
    return u.strip()


def regla_salida(user: str) -> str:
    m = re.search(r"El código cierra tu mensaje con esta pregunta: «(.*?)»", user)
    if m:
        return f"PREGUNTA DEL CÓDIGO: «{m.group(1)}» → deja \"pregunta\" vacío y NO preguntes nada en responde/por_que."
    if "Si hace falta UNA pregunta" in user:
        return "SIN PREGUNTA DEL CÓDIGO: puedes poner UNA pregunta en \"pregunta\" (p. ej. «¿es alguno de estos?») o dejarlo vacío."
    if "FORMATO DE SALIDA" in user:
        return "SIN PREGUNTAS: deja \"pregunta\" vacío."
    return "TEXTO LIBRE (no JSON): escribe el mensaje en \"vendedora_texto\"."


def vista(c: dict, s: dict) -> str:
    out = [f"=== {c['id']} · {c['conjunto']} · {'ANUNCIO' if c.get('desde_anuncio') else 'sin anuncio'} · estilo {c['estilo']} · "
           f"turno {len(s['turnos'])}/{c['turnos_max']}"]
    if s["fin"]:
        return out[0] + " · TERMINADA"
    if s["espera"]:
        pend = _leer_pendiente(s["espera"]["clave"])
        u = pend["messages"][-1]["content"]
        out.append(">>> ESCRIBE LA VENDEDORA (\"vendedora\": {responde, por_que, pregunta})")
        out.append(regla_salida(u))
        out.append(contexto_compacto(u))
    else:
        out.append(f">>> ESCRIBE LA CLIENTA ({c['cliente']}) · persona: {c['persona']} · objetivo: {c['objetivo']}")
        out.append("plan: " + " → ".join(c["plan"]))
        for t in s["turnos"][-3:]:
            out.append(f"  👤 {t['cliente']}")
            out.append("  🤖 " + (t["bot"] or "").replace("\n\n", " ¶ ") + (f"  📷 {', '.join(t['fotos'])}" if t["fotos"] else ""))
    return "\n".join(out)


# ---------------------------------------------------------------------------

def cargar_specs(con_rehechas: bool = False) -> list[dict]:
    """Las 200 de specs.jsonl. Con `con_rehechas`, además las rehechas con ids nuevos (`specs_*_b.jsonl`) y sin las que
    figuran en `oro/excluir_*.txt` (las originales que se rehicieron)."""
    with open(os.path.join(DATOS, "specs.jsonl"), encoding="utf-8") as fh:
        cs = [json.loads(x) for x in fh if x.strip()]
    if not con_rehechas:
        return cs
    import glob
    fuera = set()
    for r in glob.glob(os.path.join(DATOS, "oro", "excluir_*.txt")):
        fuera |= {x.strip() for x in open(r, encoding="utf-8") if x.strip() and not x.startswith("#")}
    cs = [c for c in cs if c["id"] not in fuera]
    for r in sorted(glob.glob(os.path.join(DATOS, "specs_*_b.jsonl"))):
        cs += [json.loads(x) for x in open(r, encoding="utf-8") if x.strip()]
    return cs


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("specs")
    for nombre in ("ver", "avanzar"):
        p = sub.add_parser(nombre)
        p.add_argument("--lote", required=True)
        p.add_argument("--modo", default="oro", help="oro | eval-<variante> (carpeta de estado propia)")
        p.add_argument("--url", default="http://127.0.0.1:18491")
        p.add_argument("--specs", default="", help="otro archivo de specs (modo eval)")
        if nombre == "avanzar":
            p.add_argument("--entrada", default="")
    cg = sub.add_parser("ciego", help="transcripciones a ciegas de dos variantes para el juez")
    cg.add_argument("--specs", default=os.path.join(DATOS, "specs_eval.jsonl"))
    cg.add_argument("--variantes", default="afinado,base")
    cg.add_argument("--jueces", type=int, default=2)
    sub.add_parser("progreso",help="cuántas conversaciones y turnos de oro hay por lote")
    sub.add_parser("reparar",help="quita los turnos caídos (agente sin responder) y deja el mensaje para reenviarlo")
    se = sub.add_parser("specs-eval",help="las conversaciones reservadas de app/conversaciones.py como specs")
    se.add_argument("--corpus", default=os.path.join(AQUI, "..", "pruebas_conv", "corpus.jsonl"))
    se.add_argument("--lotes", type=int, default=4)
    rs = sub.add_parser("resultados", help="estado de una corrida eval → formato de app/conversaciones.py (informe)")
    rs.add_argument("--modo", required=True)
    rs.add_argument("--specs", default=os.path.join(DATOS, "specs_eval.jsonl"))
    rs.add_argument("--juicios", default="", help="JSON {id: {respondio, invento, presiono, hilo, robotica, avanzo}}")
    rs.add_argument("--salida", required=True)
    e = sub.add_parser("exportar")
    e.add_argument("--salida", default=os.path.join(DATOS, "oro.jsonl"))
    a = ap.parse_args(argv)
    os.makedirs(DATOS, exist_ok=True)

    if a.cmd == "specs":
        cs = specs()
        with open(os.path.join(DATOS, "specs.jsonl"), "w", encoding="utf-8") as fh:
            for c in cs:
                fh.write(json.dumps(c, ensure_ascii=False) + "\n")
        from collections import Counter
        print(len(cs), Counter(c["conjunto"] for c in cs), Counter(c["lote"] for c in cs))
        print("personas:", len({c["persona_clave"] for c in cs}),
              "· reservadas por persona:", dict(Counter(c["persona_clave"] for c in cs if c["conjunto"] == "reservada")))
        return

    if a.cmd == "ciego":
        # Transcripciones de las dos variantes, a ciegas (X/Y al azar por conversación), para que un juez las puntúe.
        from app import conversaciones as cv
        cs = [json.loads(x) for x in open(a.specs, encoding="utf-8") if x.strip()]
        cat = json.load(open(os.path.join(DATOS, "catalogo.json"), encoding="utf-8"))["products"]
        cv.CAT.clear()
        cv.CAT.update({p["code"]: {"nombre": p["name"], "precio": p["price"], "tallas": p["sizes"], "color": p["color"],
                                   "descripcion": p["description"], "categoria": p["category"]} for p in cat})
        rng = random.Random(99)
        clave, grupos = {}, {}
        variantes = a.variantes.split(",")
        for i, c in enumerate(cs):
            orden = variantes[:]
            rng.shuffle(orden)
            clave[c["id"]] = dict(zip("XY", orden))
            partes = [f"################ {c['id']} · {'desde anuncio' if c.get('desde_anuncio') else 'sin anuncio'} · canal "
                      f"{'web' if c.get('canal') == 'web' else 'whatsapp'}", f"persona de la clienta: {c.get('persona') or '(chat real o guion)'}"]
            reales = set()
            for letra, v in zip("XY", orden):
                s = cargar_estado(c, "eval-" + v)
                r = {"turnos": s["turnos"]}
                reales.add(cv._datos_reales(r))
                partes.append(f"======== VENDEDORA {letra}")
                for t in s["turnos"]:
                    partes.append(f"👤 {t['cliente']}")
                    for p in (t["bot"] or "(sin respuesta)").split("\n\n"):
                        partes.append("   🤖 " + p.replace("\n", " / "))
                    if t["fotos"]:
                        partes.append("   📷 fotos enviadas: " + ", ".join(t["fotos"]))
            partes.insert(2, "DATOS REALES:\n" + "\n".join(sorted(reales)))
            grupos.setdefault(f"J{i % a.jueces + 1}", []).append("\n".join(partes))
        d = os.path.join(DATOS, "juicio")
        os.makedirs(d, exist_ok=True)
        for g, txts in grupos.items():
            with open(os.path.join(d, f"{g}.txt"), "w", encoding="utf-8") as fh:
                fh.write("\n\n".join(txts) + "\n")
        with open(os.path.join(d, "clave.json"), "w", encoding="utf-8") as fh:
            json.dump(clave, fh, ensure_ascii=False, indent=1)
        print({g: len(v) for g, v in grupos.items()}, "→", d)
        return

    if a.cmd == "progreso":
        por = {}
        for c in cargar_specs():
            s = cargar_estado(c, "oro")
            x = por.setdefault(c["lote"], [0, 0, 0, 0])
            x[0] += 1
            x[1] += bool(s["fin"])
            x[2] += sum(1 for t in s["turnos"] if t.get("oro"))
            x[3] += bool(s.get("reenviar") or s.get("espera"))
        for k in sorted(por):
            print(f"{k}: {por[k][1]}/{por[k][0]} terminadas · {por[k][2]} turnos de oro · {por[k][3]} esperando")
        print(f"total: {sum(v[1] for v in por.values())} terminadas · {sum(v[2] for v in por.values())} turnos de oro")
        return

    if a.cmd == "reparar":
        # Turnos que fallaron porque el agente estaba caído (http 0) al final de cada conversación: se quitan (con su
        # entrada del historial) y el mensaje de la clienta queda para reenviarse en la próxima ronda. Copia antes.
        import shutil
        for c in cargar_specs():
            ruta = _ruta_estado(c["id"], "oro")
            if not os.path.exists(ruta):
                continue
            s = cargar_estado(c, "oro")
            caidos = []
            while s["turnos"] and s["turnos"][-1].get("http") == 0:
                caidos.insert(0, s["turnos"].pop())
                if s["historial"] and s["historial"][-1]["rol"] == "cliente":
                    s["historial"].pop()
            if not caidos:
                continue
            shutil.copy(ruta, ruta + ".antes_de_reparar")
            if s["turnos"]:
                t = s["turnos"][-1]
                s["etapa"], s["memoria"] = t["etapa"], t["memoria"]
            else:
                s["etapa"], s["memoria"] = "", None
            s["espera"], s["fin"] = None, False
            s["reenviar"] = " || ".join(t["cliente"] for t in caidos)
            guardar_estado(s, "oro")
            print(f"{c['id']}: quitados {len(caidos)} turnos caídos; se reenviará «{s['reenviar'][:60]}»")
        return

    if a.cmd == "specs-eval":
        corpus = [json.loads(x) for x in open(a.corpus, encoding="utf-8") if x.strip()]
        res = sorted((c for c in corpus if c["conjunto"] == "reservada"), key=lambda c: c["id"])
        out = []
        simuladas = [c for c in res if not c.get("mensajes")]
        for i, c in enumerate(res):
            x = {"id": c["id"], "tipo": c["tipo"], "conjunto": "reservada", "persona_clave": c.get("persona_clave", c["id"]),
                 "persona": c.get("persona", ""), "objetivo": c.get("objetivo", ""), "plan": c.get("plan", []),
                 "estilo": c.get("estilo", "correcta"), "desde_anuncio": bool(c.get("desde_anuncio")), "canal": c.get("canal", ""),
                 "cliente": c.get("cliente", ""), "turnos_max": c.get("turnos") or len(c.get("mensajes") or []),
                 "foto": c.get("foto", "v35")}
            if c.get("mensajes"):
                x["mensajes"], x["lote"] = c["mensajes"], "G"
            else:
                x["lote"] = f"E{simuladas.index(c) % a.lotes + 1}"
            out.append(x)
        with open(os.path.join(DATOS, "specs_eval.jsonl"), "w", encoding="utf-8") as fh:
            for x in out:
                fh.write(json.dumps(x, ensure_ascii=False) + "\n")
        from collections import Counter
        print(len(out), Counter(x["lote"] for x in out))
        return

    if a.cmd == "resultados":
        cs = [json.loads(x) for x in open(a.specs, encoding="utf-8") if x.strip()]
        juicios = json.load(open(a.juicios, encoding="utf-8")) if a.juicios else {}
        with open(a.salida, "w", encoding="utf-8") as fh:
            for c in cs:
                s = cargar_estado(c, a.modo)
                r = {"id": c["id"], "tipo": c.get("tipo", "simulada"), "conjunto": "reservada", "canal": c.get("canal", ""),
                     "desde_anuncio": bool(c.get("desde_anuncio")), "persona_clave": c.get("persona_clave", c["id"]),
                     "persona": c.get("persona", ""), "turnos": s["turnos"], "error": "" if s["fin"] else "sin terminar"}
                j = juicios.get(c["id"])
                if j:   # Opus juzga con sí/no: se guarda como probabilidad 1/0 para que `informe` lo lea igual que a Jev
                    r["juez"] = {k: (1.0 if bool(v) else 0.0) for k, v in j.items() if k in ("respondio", "invento", "presiono",
                                                                                          "hilo", "robotica", "avanzo")}
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        # El catálogo de la corrida, para que `informe` recalcule las reglas con los mismos precios y stock.
        cat = json.load(open(os.path.join(DATOS, "catalogo.json"), encoding="utf-8"))["products"]
        st = {x["code"]: x for x in json.load(open(os.path.join(DATOS, "stock.json"), encoding="utf-8"))["stock"]}
        catalogo = {p["code"]: {"nombre": p.get("name", ""), "precio": p.get("price"), "tallas": p.get("sizes") or [],
                                "color": p.get("color", ""), "descripcion": p.get("description", ""), "categoria": p.get("category", ""),
                                "stock": {t: v.get("available", 0) for t, v in (st.get(p["code"], {}).get("online") or {}).items()}}
                    for p in cat}
        with open(a.salida + ".catalogo.json", "w", encoding="utf-8") as fh:
            json.dump(catalogo, fh, ensure_ascii=False)
        print(f"{len(cs)} conversaciones → {a.salida}")
        return

    if a.cmd == "exportar":
        n = 0
        with open(a.salida, "w", encoding="utf-8") as fh:
            for c in cargar_specs(con_rehechas=True):
                s = cargar_estado(c, "oro")
                if not s["turnos"]:
                    continue
                turnos = []
                for t in s["turnos"]:
                    x = {"clienta": t["cliente"], "vendedora": t["bot"], "etapa": t["etapa"], "intent": t["intent"],
                         "fotos": t["fotos"], "siguiente": t["siguiente"]}
                    if t.get("oro"):
                        x["contexto"] = t["oro"]["messages"]
                        x["json"] = t["oro"]["json"]
                        x["salida"] = t["oro"]["salida"]
                    turnos.append(x)
                # «reparada»: `oro.py reparar` le quitó turnos caídos tras la caída del agente (copia previa en
                # <id>.json.antes_de_reparar). Se marca para poder separarla del resto si se decide descartarla.
                reparada = os.path.exists(_ruta_estado(c["id"], "oro") + ".antes_de_reparar")
                fh.write(json.dumps({"id": c["id"], "persona": c["persona_clave"], "conjunto": c["conjunto"],
                                     "desde_anuncio": c["desde_anuncio"], "terminada": s["fin"], "reparada": reparada,
                                     "turnos": turnos},
                                    ensure_ascii=False) + "\n")
                n += 1
        print(f"{n} conversaciones → {a.salida}")
        return

    cs = [json.loads(x) for x in open(a.specs, encoding="utf-8")] if a.specs else cargar_specs()
    lote = [c for c in cs if c["lote"] == a.lote]
    if a.cmd == "avanzar":
        entradas = {}
        if a.entrada:
            with open(a.entrada, encoding="utf-8") as fh:
                entradas = {x["id"]: x for x in json.load(fh)}
        avisos = []

        def una(c):
            x = entradas.get(c["id"])
            if c.get("mensajes") and a.modo != "oro":
                # Guion fijo (escenarios y chats reales del corpus anterior): se manda solo, un mensaje por ronda.
                s = cargar_estado(c, a.modo)
                i = s.get("guion_i", 0)
                if s["fin"] or i >= len(c["mensajes"]):
                    s["fin"] = True
                else:
                    enviar_guion(a.url, c, s, c["mensajes"][i])
                    s["guion_i"] = i + 1
                    s["fin"] = s["guion_i"] >= len(c["mensajes"])
                guardar_estado(s, a.modo)
                return
            s = cargar_estado(c, a.modo)
            if s.get("reenviar") and not s["espera"] and not s["fin"]:
                # Un mensaje que no llegó (agente caído): se reenvía y lo escrito en esta ronda para este id se ignora,
                # porque el contexto que vio quien escribe ya no es el actual.
                r = s.pop("reenviar")
                enviar_clienta(a.url, c, s, r, a.modo)
                guardar_estado(s, a.modo)
                if x:
                    avisos.append(f"{c['id']}: se reenvió «{r[:40]}» (el agente estaba caído); lo de esta ronda no se aplicó, mira la vista nueva")
                return
            if not x:
                return
            if s["fin"]:
                return
            if s["espera"] and ("vendedora" in x or "vendedora_texto" in x):
                aviso = escribir_vendedora(a.url, c, s, x.get("vendedora") or x.get("vendedora_texto"), a.modo)
                if aviso:
                    avisos.append(aviso)
            if not s["espera"] and not s["fin"] and x.get("clienta"):
                enviar_clienta(a.url, c, s, x["clienta"], a.modo)
            guardar_estado(s, a.modo)

        with cf.ThreadPoolExecutor(4 if a.modo == "oro" else 2) as ex:
            list(ex.map(una, lote))
        for av in avisos:
            print("AVISO:", av)
    for c in lote:
        print(vista(c, cargar_estado(c, a.modo)))
        print()
    pend = sum(1 for c in lote if not cargar_estado(c, a.modo)["fin"])
    print(f"--- lote {a.lote}: {pend} de {len(lote)} sin terminar")


if __name__ == "__main__":
    main()
