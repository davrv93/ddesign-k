"""Prueba con conversaciones completas: 200 chats estilo WhatsApp peruano contra el agente, con detección de errores.

Tres pasos, todos desde `agente/` y solo con la biblioteca estándar (corre en el host, no en la imagen):

    python3 -m app.conversaciones generar                    # corpus fijo (semilla 2026): 150 de desarrollo + 50 reservadas
    python3 -m app.conversaciones correr --conjunto reservada --salida pruebas_conv/res_antes.jsonl
    python3 -m app.conversaciones informe pruebas_conv/res_antes.jsonl [--comparar pruebas_conv/res_despues.jsonl]

- **Corpus** (`pruebas_conv/corpus.jsonl`, fuera de git): conversaciones `real` (anonimizadas, de
  `pruebas_conv/reales.jsonl` si existe: no van al repo, que es público), `escenario` (guiones fijos del cliente y
  de los errores ya vistos) y `simulada` (una persona —fría, apurada, con anuncio, regatea, manda audios…— que una
  LLM interpreta reaccionando a lo que dice el bot: bucle real, no guion). Las 50 `reservada` NO se miran para
  corregir: solo miden antes y después.
- **Correr**: guarda etapa y memoria entre turnos como el bot Go (`historial`, `etapa`, `memoria`, `estado`), manda
  las fotos por `/foto` y emula lo mínimo del bot Go (resumen del pedido y el *SI* en WhatsApp). Lleva la cuenta de
  gasto (`costo_usd` del agente + clienta simulada + juez) y para en `--tope`.
- **Errores**: reglas deterministas por turno (`reglas_turno`, `reglas_conversacion`) y Jev como juez de la
  conversación entera (`juzgar`, preguntas tipadas). `informe` los agrupa por categoría, de más a menos grave.

La clave de OpenRouter se lee de `OPENROUTER_API_KEY` o de `--env` (un `.env`); nunca se imprime.
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
import threading
import time
import urllib.error
import urllib.request

from . import etapas, memoria

AQUI = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(AQUI, "..", "pruebas_conv")          # fuera de git (.gitignore)
CORPUS = os.path.join(DIR, "corpus.jsonl")
REALES = os.path.join(DIR, "reales.jsonl")
IMAGENES = os.path.join(AQUI, "..", "imagenes", "tienda")
OPENROUTER = "https://openrouter.ai/api/v1"
SEMILLA = 2026
N_TOTAL, N_RESERVADAS = 200, 50

# ---------------------------------------------------------------------------
# Corpus

NOMBRES = ["Carmen", "Rosa", "Lucía", "Milagros", "Katherine", "Diana", "Gabriela", "Jessica", "Fiorella", "Andrea",
           "Pamela", "Yesenia", "Lorena", "Claudia", "Sofía", "Valeria", "Mariela", "Estefany", "Roxana", "Gisela",
           "Patricia", "Nataly", "Elena", "Karla", "Maricarmen", "Flor", "Janet", "Silvia", "Liz", "Maribel"]

ESTILOS = {
    "correcta": "Escribes bien, con tildes y signos, pero corto como en WhatsApp.",
    "informal": "Escribes en minúsculas, sin tildes ni signos de apertura, con abreviaturas (q, xq, pa, tmb, x fa, pls).",
    "jerga": "Hablas con jerga peruana de Lima: «ps», «pe», «causa», «al toque», «xfa», «chévere», «bacán», «oe», «ya fue», "
             "«manyas», «che» (una o dos por mensaje, sin exagerar).",
    "errores": "Escribes con faltas de ortografía y de tipeo («ke», «bestido», «cuanto questa», «tiens», «nesesito», «aver»), sin tildes.",
    "partidos": "Mandas tus ideas en varios mensajes cortitos seguidos: sepáralos con « || » (2 o 3 por turno a veces).",
    "audios": "A veces, en vez de escribir, mandas un audio: escribe «[audio] » y lo que dirías hablando, largo y con muletillas.",
}

OCASIONES = [("el matrimonio de mi prima", "matrimonio"), ("una boda de una amiga", "matrimonio"),
             ("la graduación de mi hija", "graduacion"), ("mi graduación", "graduacion"), ("un quinceañero", "quinceanero"),
             ("la cena de fin de año de mi empresa", "cena"), ("un bautizo", "bautizo"), ("mi cumpleaños", "cumpleanos"),
             ("una gala de la promoción", "gala"), ("una entrevista de trabajo", "trabajo"), ("ir a la oficina", "trabajo"),
             ("un compromiso", "fiesta")]

# Plantillas de persona: (clave, descripción, objetivo, plan en pasos, anuncio: None=al azar / True / False, peso)
PERSONAS = [
    ("fria_viendo", "Estás curioseando sin evento definido; quizá para algo del próximo año. No tienes apuro.",
     "Ver qué tienen sin comprometerte.", ["saluda", "di que solo estás viendo", "pregunta qué vestidos o modelos tienen",
     "pregunta el precio de lo que te muestren", "di que lo vas a pensar y despídete"], False, 9),
    ("apurada", "Tienes {ocasion} en 3 días ({fecha_caliente}) y no tienes qué ponerte. Estás apurada.",
     "Conseguir algo YA y, si se puede, probártelo mañana.", ["cuenta tu urgencia", "contesta lo que te pregunten",
     "pide que te recomiende algo", "pregunta precio y si hay en tu talla (M)", "pide ir a probártelo mañana a la 1:30 pm",
     "si no se puede a esa hora, acepta otra hora razonable"], False, 9),
    ("anuncio_precio", "Viste un anuncio del vestido en Instagram/Facebook y escribes desde ahí. No dices el nombre.",
     "Saber precio, tallas, tela y envío a provincia ({ciudad}).", ["pregunta el precio del vestido del anuncio",
     "pregunta si hay en tu talla (S)", "pregunta de qué tela es", "pregunta cuánto cuesta el envío a {ciudad}",
     "di que lo quieres comprar"], True, 9),
    ("anuncio_duda", "Viste el anuncio del vestido. Te gustó pero dudas del precio y de cómo te quedará (mides 1.55).",
     "Resolver dudas; probablemente no compras hoy.", ["pregunta si todavía tienen ese vestido", "pregunta si es muy largo para tu estatura",
     "di que está un poco caro", "pregunta si hay descuento", "di que lo vas a pensar"], True, 7),
    ("este_vestido", "Viste un vestido en un estado de WhatsApp de una amiga, pero NO llegaste por anuncio y no sabes el nombre.",
     "Saber si tienen ese vestido.", ["pregunta «hola tienen este vestido?» sin decir cuál", "responde algo como «ah sí, un momento» sin dar el dato",
     "describe el vestido: azul, largo, con brillitos en el pecho", "pregunta el precio", "pregunta si lo puedes ver en persona"], False, 6),
    ("manda_foto", "Tienes una captura del vestido que te gustó.", "Saber si lo tienen y en qué talla.",
     ["saluda y di que te gustó un vestido", "manda la foto ([FOTO])", "pregunta si hay en M", "pregunta cuánto cuesta el envío en Lima",
     "di que lo quieres"], False, 6),
    ("pide_ver_ya", "Quieres ver fotos de modelos de inmediato, sin dar explicaciones.", "Ver opciones.",
     ["pide que te manden fotos de sus vestidos", "si te preguntan algo antes de mostrar, contesta de mala gana o insiste en ver",
     "cuando veas uno, pide ver otros", "pregunta el precio del que más te guste"], False, 8),
    ("cambia_prenda", "Empiezas buscando vestido, pero a mitad de la charla cambias de idea a un conjunto o blazer para la oficina.",
     "Encontrar algo formal para trabajar.", ["di que buscas un vestido", "cambia: mejor algo para la oficina, tipo conjunto o blazer",
     "pregunta qué blazers tienen", "pregunta la tela", "pregunta el precio"], False, 6),
    ("tela_corte", "Eres detallista: te importa la tela, el forro, el corte y si es entallado. Es para {ocasion} ({fecha_tibia}).",
     "Entender bien la prenda antes de decidir.", ["di para qué ocasión buscas", "contesta lo que te pregunten",
     "pregunta de qué tela es", "pregunta si tiene forro y si es entallado", "pregunta si es largo o midi", "pregunta el precio"], False, 7),
    ("tallas", "No sabes tu talla: mides 1.62 y pesas 68 kilos; normalmente usas L o XL.", "Saber qué talla te queda.",
     ["pregunta por un vestido para {ocasion}", "pregunta qué talla te quedaría con tus medidas", "pregunta si tienen XL",
     "pregunta si se puede ajustar", "di que lo vas a pensar"], False, 6),
    ("envio_pago", "Vives en {ciudad} (provincia). Ya casi decidiste comprar.", "Saber envío, pago y comprar.",
     ["di que eres de {ciudad} y preguntas si envían", "pregunta el costo del envío", "pregunta cómo se paga (yape, contraentrega?)",
     "di qué prenda quieres (el vestido Pandora talla M)", "confirma la compra"], False, 6),
    ("ubicacion", "Prefieres ver en persona antes de comprar.", "Ir a la tienda.",
     ["pregunta dónde quedan", "pregunta si puedes ir hoy en la noche a las 8", "pregunta el horario del domingo",
     "pide una cita para probarte un vestido de noche", "propón una hora válida"], False, 6),
    ("regateo", "Te encantó un vestido pero sientes que está caro. Regateas como en Gamarra.", "Conseguir descuento.",
     ["pregunta por el vestido Holly", "pregunta el precio", "di que está caro, que en Gamarra hay más barato",
     "pide que te lo dejen en 250", "pregunta si llevando dos hay descuento", "di que lo vas a pensar"], False, 6),
    ("queja", "Compraste hace una semana y tu pedido no llega; estás molesta.", "Que te solucionen.",
     ["di que tu pedido no llega y estás molesta", "insiste en que nadie te responde", "pide hablar con una persona",
     "pregunta por la política de devoluciones"], False, 4),
    ("fuera_rubro", "Eres bromista y preguntas cosas que no tienen nada que ver.", "Pasar el rato y luego preguntar algo real.",
     ["pregunta quién ganó el partido de ayer", "pregunta cuánto es 25 por 4", "pregunta si eres un bot",
     "pregunta si tienen vestidos para {ocasion}"], False, 5),
    ("lo_pienso_vuelve", "Te interesa algo para {ocasion} ({fecha_tibia}) pero lo piensas; vuelves otro día.",
     "Decidir con calma.", ["cuenta tu ocasión", "contesta lo que te pregunten", "pregunta el precio de lo que te recomienden",
     "di que lo vas a pensar", "vuelve: «hola de nuevo, te escribí el otro día por el vestido»", "pregunta si aún lo tienen en tu talla (S)"], False, 6),
    ("compra_rapida", "Ya sabes lo que quieres: el vestido Irla en talla M. Quieres pagar rápido.", "Comprar hoy.",
     ["pide el vestido Irla en talla M", "di que lo quieres comprar", "confirma el pedido (SI)", "di que es para Lima", "pide los datos para pagar",
     "di que ya pagaste"], False, 6),
    ("cita", "Quieres probártelo antes. Es para {ocasion} ({fecha_tibia}).", "Agendar una cita.",
     ["cuenta para qué lo buscas", "contesta lo que te pregunten", "di que quieres ir a probártelo",
     "propón el domingo a las 7:30 de la noche", "si no se puede, propón el sábado a las 11 de la mañana", "di tu talla (L)"], False, 7),
    ("nombra_prenda", "Viste el catálogo en Diners y ya conoces los nombres.", "Comparar dos modelos.",
     ["pregunta si el vestido Kendall tiene en M", "pide la foto del Holly", "pregunta cuál le recomiendas para una cena de noche",
     "pregunta la tela del que te recomiende"], False, 6),
    ("audio", "Prefieres mandar audios; hablas mucho.", "Encontrar un vestido para {ocasion}.",
     ["manda un audio largo contando tu evento ({fecha_tibia}, de noche)", "contesta lo que te pregunten", "pregunta el precio",
     "pregunta si hacen delivery a Lima"], False, 5),
    ("para_otra", "Buscas un regalo para tu mamá (talla L, 1.58 m) para su cumpleaños; tú no eres la que lo usará.",
     "Regalo para mamá.", ["di que buscas algo para tu mamá", "da su talla y estatura", "pregunta qué le recomiendan", "pregunta el precio"], False, 4),
    ("hombre_pareja", "Eres un hombre buscando ropa formal de regalo para tu pareja; no sabes mucho de ropa.", "Comprar un regalo.",
     ["di que buscas ropa formal para tu pareja", "di que no sabes su talla, que es delgada", "pregunta qué le recomiendan",
     "pregunta si hay otros colores", "pregunta si se puede cambiar de talla si no le queda"], False, 4),
    ("emojis", "Respondes con poco: emojis, «jaja», «ok», «mmm», «ya».", "No queda claro qué quieres.",
     ["saluda con un emoji", "responde «mmm» o «jaja» a lo que te digan", "di «ok» ", "pregunta qué hay de nuevo"], False, 4),
    ("colores", "Te gustó un modelo pero quieres otro color.", "Encontrar el color que buscas.",
     ["pregunta si el vestido Irla lo tienen en rojo", "pregunta qué otros colores hay", "pide ver opciones en rojo o vino",
     "pregunta el precio"], False, 4),
    ("evento_lejano", "Tienes {ocasion} recién en febrero del próximo año, pero ya te interesa mucho.", "Adelantarte.",
     ["cuenta tu ocasión y que es en febrero", "contesta lo que te pregunten", "pregunta si te pueden separar algo",
     "pregunta el precio"], False, 4),
    ("presupuesto", "Tu presupuesto es de máximo 250 soles.", "Algo bonito dentro del presupuesto.",
     ["di que buscas vestido para {ocasion} y que tienes máximo 250 soles", "contesta lo que te pregunten", "pide que te muestre",
     "pregunta si hay algo más barato"], False, 5),
    ("todo_junto", "Escribes todo de una: ocasión, fecha, día/noche y talla en el primer mensaje.", "Que te recomienden ya.",
     ["escribe en un solo mensaje: es {ocasion} el {fecha_tibia} en la noche, soy talla M, qué me recomiendas", "pregunta el precio",
     "pregunta si te lo puedes probar"], False, 5),
    ("novia", "TÚ te casas (eres la novia) en una ceremonia civil sencilla de día.", "Vestido para tu boda civil.",
     ["di que te casas por civil y buscas vestido", "di que es de día", "pregunta si tienen algo blanco o claro", "pregunta el precio"], False, 3),
]

ESCENARIOS = [  # guiones fijos: el método de Alvaro y los errores ya vistos (README y capturas)
    ("E01_metodo_alvaro", False, "", ["hola", "busco un vestido", "es para un matrimonio", "el 24 de octubre", "de noche",
                                      "qué tela es?", "cuánto cuesta?", "quiero ir a probármelo", "el sábado a la 1:30", "a las 3 entonces"]),
    ("E02_saludo_info", False, "", ["hola", "quisiera información", "de vestidos", "para una graduación"]),
    ("E03_busco_vestido", False, "", ["busco un vestido", "para una boda", "en noviembre", "de día"]),
    ("E04_ver_modelos", False, "", ["hola", "me gustaría ver los modelos", "para un cumpleaños", "muéstrame más"]),
    ("E05_fecha_objecion", False, "", ["hola busco vestido para graduación", "el 24 de octubre", "en la noche", "me gusta", "cuánto está?"]),
    ("E06_este_sin_anuncio", False, "", ["hola tienen este vestido?", "oh sí", "ahora te digo el nombre", "era azul largo con pedrería",
                                         "sí ese", "precio?"]),
    ("E07_anuncio_cita", True, "", ["hola info", "es para una gala", "el 15 de noviembre", "de noche", "qué tela es?", "tienes en M?",
                                    "quiero probármelo", "el domingo a las 8 de la noche", "a las 11 am"]),
    ("E08_catalogo", False, "web", ["muéstrame tu catálogo", "vestidos", "otros", "el Holly qué precio tiene?"]),
    ("E09_irla_web", False, "web", ["pásame la foto del Irla", "qué tela es?", "tienes en L?", "lo quiero", "L", "sí"]),
    ("E10_compra_web", False, "web", ["quiero ver conjuntos", "Talla M del V24", "Sí, confirmar", "Lima", "cómo pago?"]),
    ("E11_fria", False, "", ["solo estoy viendo, es para el próximo año", "qué vestidos tienen para matrimonio?",
                             "y cuánto cuestan más o menos?", "ok gracias lo voy a pensar"]),
    ("E12_caliente", False, "", ["necesito un vestido para este sábado urgente", "es un matrimonio de noche", "talla S", "cuánto?",
                                 "puedo ir mañana a las 10?"]),
    ("E13_fuera_rubro", False, "", ["quién ganó el partido ayer?", "ya pues dime", "ok, tienen blazers?"]),
    ("E14_refrigerio", False, "", ["quiero probarme el vestido Holly", "mañana a la 1 y media", "a las 2 entonces", "talla M"]),
    ("E15_talla_xl", False, "", ["el Kendall lo tienen en XL?", "y en XXL?", "qué medidas tiene la L?"]),
    ("E16_provincia", False, "", ["hola soy de Arequipa, hacen envíos?", "cuánto cuesta el envío?", "y cómo pago?",
                                  "quiero el vestido Pandora talla M"]),
]


def _fechas(hoy: dt.date, rng: random.Random) -> dict:
    meses = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")
    f = lambda d: f"{d.day} de {meses[d.month - 1]}"
    return {"fecha_caliente": rng.choice(["este sábado", "el " + f(hoy + dt.timedelta(days=3)), "pasado mañana"]),
            "fecha_tibia": f(hoy + dt.timedelta(days=rng.randint(10, 28)))}


def generar(semilla: int = SEMILLA, hoy: dt.date | None = None) -> list[dict]:
    """El corpus completo, determinista: reales (si hay archivo) + escenarios + simuladas hasta 200; 50 reservadas."""
    rng = random.Random(semilla)
    hoy = hoy or dt.date(2026, 10, 4)
    convs = []
    if os.path.exists(REALES):
        for line in open(REALES, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                convs.append(dict(r, tipo="real"))
    for cid, anuncio, canal, msgs in ESCENARIOS:
        convs.append({"id": cid, "tipo": "escenario", "canal": canal, "desde_anuncio": anuncio, "mensajes": msgs,
                      "cliente": rng.choice(NOMBRES)})
    pesos = [p[5] for p in PERSONAS]
    k = 0
    while len(convs) < N_TOTAL:
        clave, desc, obj, plan, anuncio, _ = rng.choices(PERSONAS, weights=pesos)[0]
        oc_txt, _oc = rng.choice(OCASIONES)
        ciudad = rng.choice(["Arequipa", "Trujillo", "Piura", "Cusco", "Chiclayo", "Huancayo"])
        fechas = _fechas(hoy, rng)
        fmt = dict(ocasion=oc_txt, ciudad=ciudad, **fechas)
        estilo = rng.choices(list(ESTILOS), weights=[3, 4, 3, 2, 2, 1])[0]
        k += 1
        convs.append({
            "id": f"S{k:03d}_{clave}", "tipo": "simulada", "persona_clave": clave,
            "persona": desc.format(**fmt), "objetivo": obj.format(**fmt), "plan": [p.format(**fmt) for p in plan],
            "estilo": estilo, "canal": "web" if rng.random() < 0.25 else "",
            "desde_anuncio": anuncio if anuncio is not None else rng.random() < 0.2,
            "turnos": rng.randint(max(4, len(plan) - 1), min(12, len(plan) + 3)),
            "cliente": rng.choice(NOMBRES), "semilla": rng.randint(1, 10**6),
            "foto": rng.choice(["v35", "v31", "v41", "v21", "v28", "v42", "v24"]),
        })
    # Reparto estratificado y fijo: ~1/4 de cada tipo va a las reservadas.
    por_tipo: dict[str, list] = {}
    for c in convs:
        por_tipo.setdefault(c["tipo"], []).append(c)
    reservadas = set()
    cuota = {t: round(N_RESERVADAS * len(v) / len(convs)) for t, v in por_tipo.items()}
    cuota["simulada"] = N_RESERVADAS - sum(v for t, v in cuota.items() if t != "simulada")
    for t, v in por_tipo.items():
        ids = sorted(c["id"] for c in v)
        random.Random(semilla + len(t)).shuffle(ids)
        reservadas |= set(ids[:cuota[t]])
    for c in convs:
        c["conjunto"] = "reservada" if c["id"] in reservadas else "dev"
    return convs


# ---------------------------------------------------------------------------
# Llamadas

def _clave(env: str | None) -> str:
    k = os.environ.get("OPENROUTER_API_KEY", "")
    if not k and env and os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            if line.startswith("OPENROUTER_API_KEY="):
                k = line.split("=", 1)[1].strip()
    return k


def _post(url: str, cuerpo: dict, clave: str = "", timeout: float = 90) -> dict:
    h = {"content-type": "application/json"}
    if clave:
        h["Authorization"] = "Bearer " + clave
    r = urllib.request.Request(url, json.dumps(cuerpo).encode(), h)
    return json.load(urllib.request.urlopen(r, timeout=timeout))


def saldo(clave: str) -> dict:
    """Lo que queda de la clave de OpenRouter (solo cifras)."""
    try:
        r = urllib.request.Request(OPENROUTER + "/key", headers={"Authorization": "Bearer " + clave})
        d = json.load(urllib.request.urlopen(r, timeout=20))["data"]
        return {k: d.get(k) for k in ("usage", "limit", "limit_remaining")}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:80]}


class Cuenta:
    def __init__(self, tope: float):
        self.tope, self.total, self.lock = tope, 0.0, threading.Lock()
        self.partes = {"agente": 0.0, "clienta": 0.0, "juez": 0.0}

    def sumar(self, parte: str, x: float | None):
        with self.lock:
            self.total += float(x or 0)
            self.partes[parte] += float(x or 0)

    def agotada(self) -> bool:
        return self.total >= self.tope


SISTEMA_CLIENTA = """Eres una clienta peruana que escribe por WhatsApp a Baruka Design, una tienda de vestidos y ropa de mujer de Lima.
Tú NO eres la vendedora: eres la clienta y te llamas {nombre}. Persona: {persona}
Tu objetivo: {objetivo}
Tu plan, en orden (adáptalo a lo que te responda la vendedora; no tienes que cumplirlo todo ni al pie de la letra):
{plan}
Estilo: {estilo}
Reglas:
- Escribe SOLO tu siguiente mensaje, corto (normalmente 1 frase), como escribe la gente real en WhatsApp.
- Reacciona a lo último que dijo la vendedora: si te preguntó algo, contéstalo según tu persona (o no, si tu persona es así).
  Si te dijo algo raro o no te respondió, reclama o vuelve a preguntar como lo haría una clienta.
- No inventes que la vendedora dijo algo que no dijo. No uses códigos de producto sueltos (V35); di el nombre.
- Para mandar la foto del vestido escribe exactamente [FOTO] (y si quieres, un texto después).
- Si ya terminaste (te despediste, compraste, agendaste o te aburriste), escribe solo [FIN]."""


def clienta(conv: dict, transcript: list[dict], turno: int, clave: str, modelo: str, cuenta: Cuenta) -> str:
    """El siguiente mensaje de la clienta simulada (LLM), reaccionando a lo que dijo el bot."""
    lineas = []
    for t in transcript:
        lineas.append(f"Tú: {t['cliente']}")
        if t.get("bot"):
            lineas.append(f"Vendedora: {t['bot']}")
        for f in t.get("fotos_txt", []):
            lineas.append(f"(La vendedora te envió la foto de {f})")
    usuario = ("Conversación hasta ahora:\n" + ("\n".join(lineas) if lineas else "(todavía no escribes nada: este es tu primer mensaje)")
               + f"\n\nVas por tu mensaje {turno + 1} de {conv['turnos']} como máximo. Escribe tu siguiente mensaje.")
    sistema = SISTEMA_CLIENTA.format(nombre=conv.get("cliente", ""), persona=conv["persona"], objetivo=conv["objetivo"],
                                     plan="\n".join(f"{i + 1}. {p}" for i, p in enumerate(conv["plan"])), estilo=ESTILOS[conv["estilo"]])
    cuerpo = {"model": modelo, "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
              "temperature": 0.9, "max_tokens": 160, "seed": conv["semilla"] + turno, "reasoning": {"enabled": False},
              "usage": {"include": True}}
    ultimo = None
    for intento in range(3):
        try:
            j = _post(OPENROUTER + "/chat/completions", cuerpo, clave, timeout=60)
            cuenta.sumar("clienta", (j.get("usage") or {}).get("cost"))
            txt = (j["choices"][0]["message"].get("content") or "").strip().strip('"')
            txt = re.sub(r"^(t[uú]|clienta)\s*:\s*", "", txt, flags=re.I)
            if txt:
                return txt
        except Exception as e:  # noqa: BLE001
            ultimo = e
            time.sleep(2 + 3 * intento)
    if ultimo is None:
        return "[FIN]"   # contestó vacío tres veces: se da la conversación por terminada
    raise RuntimeError(f"clienta simulada sin respuesta: {ultimo}")


def _imagen_b64(codigo: str) -> str:
    ruta = os.path.join(IMAGENES, f"{codigo.lower()}.jpg")
    with open(ruta, "rb") as fh:
        return base64.b64encode(fh.read()).decode()


RE_SI = re.compile(r"^\s*(s[ií]+|si+p|ok|dale|confirmo|claro|ya|listo|s[ií],? confirm\w*|confirmar)\b[\s.!👍]*$", re.I)


def correr_una(conv: dict, url: str, clave: str, modelo_clienta: str, cuenta: Cuenta) -> dict:
    """Una conversación completa. Guarda etapa, memoria y estado entre turnos como el bot Go."""
    historial, etapa, mem, estado, producto, talla = [], "", None, "", "", ""
    turnos, guion = [], list(conv.get("mensajes") or [])
    n_max = len(guion) if guion else conv["turnos"]
    error = ""
    k = 0
    while k < n_max and not cuenta.agotada():
        try:
            crudo = guion[k] if guion else clienta(conv, turnos, k, clave, modelo_clienta, cuenta)
        except Exception as e:  # noqa: BLE001
            error = str(e)[:200]
            break
        fin = False
        if isinstance(crudo, dict):
            partes = [crudo]
        else:
            fin = "[FIN]" in crudo.upper()
            crudo = re.sub(r"\[fin\]", "", crudo, flags=re.I).strip()
            if not crudo:
                break
            partes = [p.strip() for p in crudo.split("||") if p.strip()] or [crudo]
        for parte in partes:
            foto = None
            if isinstance(parte, dict):            # reales: {"foto": "v35", "texto": "..."}
                foto, parte = parte.get("foto"), parte.get("texto", "")
            elif "[FOTO]" in parte.upper():
                foto, parte = conv.get("foto", "v35"), re.sub(r"\s*\[foto\]\s*", " ", parte, flags=re.I).strip()
            t0 = time.time()
            go = None
            # Emulación mínima del bot Go en WhatsApp: el «SI» al resumen del pedido lo resuelve Go, no el agente.
            if conv.get("canal") != "web" and estado == "esperando_confirmacion" and RE_SI.match(parte or ""):
                go = {"respuesta": "✅ ¡Listo! Tu pedido quedó confirmado.\n\n¿El envío es para *Lima* o para *provincia*?",
                      "etapa": "venta_confirmada", "accion": "go_confirmar", "sugerencias": [], "modelo_llm": "go", "comercial": {}}
                estado = "esperando_pago"
                if mem:
                    mem["pendiente"], mem["etapa"] = "lima_o_provincia", "venta_confirmada"
            cuerpo = {"historial": historial[-30:], "cliente": conv.get("cliente", ""), "canal": conv.get("canal", ""),
                      "motor": "deepseek", "etapa": etapa, "conversacion": conv["id"], "desde_anuncio": bool(conv.get("desde_anuncio")),
                      "estado": estado, "producto": producto, "talla": talla}
            if mem is not None:
                cuerpo["memoria"] = mem
            mem_antes = json.loads(json.dumps(mem)) if mem is not None else None
            etapa_antes = etapa
            try:
                if go is not None:
                    j = go
                elif foto:
                    cuerpo |= {"imagen_b64": _imagen_b64(foto), "mensaje": parte}
                    j = _post(url + "/foto", cuerpo, timeout=90)
                else:
                    cuerpo["mensaje"] = parte or "."
                    j = _post(url + "/chat", cuerpo, timeout=90)
                http = 200
            except urllib.error.HTTPError as e:
                j, http = {"respuesta": "", "error": f"HTTP {e.code} {e.read()[:200]!r}"}, e.code
            except Exception as e:  # noqa: BLE001
                j, http = {"respuesta": "", "error": str(e)[:200]}, 0
            ms = int((time.time() - t0) * 1000)
            cuenta.sumar("agente", j.get("costo_usd"))
            etapa = j.get("etapa") or etapa
            mem = j.get("memoria", mem)
            if j.get("accion") == "pedido":
                estado, producto, talla = "esperando_confirmacion", j.get("codigo", ""), j.get("talla", "")
            elif j.get("accion") != "go_confirmar" and estado == "esperando_confirmacion" and etapa != "cierre":
                estado = ""
            sug = j.get("sugerencias") or []
            com = j.get("comercial") or {}
            turnos.append({
                "k": len(turnos), "cliente": ("[FOTO] " if foto else "") + (parte or ""), "foto": foto,
                "bot": j.get("respuesta", ""), "http": http, "error": j.get("error", ""), "ms": ms,
                "fotos": [s.get("codigo") for s in sug if "_material" not in (s.get("imagen") or "") and s.get("pie", "")[:12] != "✨ *Material"],
                "fotos_txt": [f"{s.get('codigo')} {s.get('nombre')}" for s in sug],
                "etapa_antes": etapa_antes, "etapa": etapa, "intent": com.get("intent"), "confianza": com.get("confianza"),
                "fuente": com.get("fuente"), "motivo": com.get("motivo"), "accion": j.get("accion"), "modelo": j.get("modelo_llm"),
                "costo": j.get("costo_usd"), "mem_antes": mem_antes, "memoria": mem, "lectura": j.get("lectura"),
                "siguiente": j.get("siguiente_pregunta"), "tallas": [x.get("talla") for x in j.get("tallas") or []],
                "foto_caso": (j.get("foto") or {}).get("caso"),
            })
            historial.append({"rol": "cliente", "texto": ("" if not foto else "[foto] ") + (parte or "")})
            for p in (j.get("respuesta") or "").split("\n\n"):
                if p.strip():
                    historial.append({"rol": "bot", "texto": p})
            for s in sug:
                historial.append({"rol": "bot", "texto": s.get("pie", "")})
        k += 1
        if fin:
            break
    return {"id": conv["id"], "tipo": conv["tipo"], "conjunto": conv["conjunto"], "canal": conv.get("canal", ""),
            "desde_anuncio": bool(conv.get("desde_anuncio")), "persona_clave": conv.get("persona_clave", conv["id"]),
            "persona": conv.get("persona", ""), "turnos": turnos, "error": error}


# ---------------------------------------------------------------------------
# Reglas (deterministas)

def catalogo(url_cat: str, url_stock: str) -> dict:
    try:
        js = json.load(urllib.request.urlopen(url_cat, timeout=20))
        prods = js.get("products", js) if isinstance(js, dict) else js
    except Exception:  # noqa: BLE001
        return {}
    cat = {p["code"]: {"nombre": p.get("name", ""), "precio": p.get("price"), "tallas": p.get("sizes") or [], "color": p.get("color", ""),
                       "descripcion": p.get("description", ""), "categoria": p.get("category", "")} for p in prods}
    try:
        st = json.load(urllib.request.urlopen(url_stock + "?codes=" + ",".join(cat), timeout=20))
        for s in st.get("stock", []):
            if s["code"] in cat:
                cat[s["code"]]["stock"] = {t: v.get("available", 0) for t, v in (s.get("online") or {}).items()}
    except Exception:  # noqa: BLE001
        pass
    return cat


_P = memoria._plano
RE_PIDE_FOTOS = re.compile(r"muestr|ensen|foto|ver (los|las|unos|unas|el|la|tus|sus|algun|otros|otras|mas|que)|quiero ver|modelos|opciones|catalogo|"
                           r"recomiend|sugier|que (tienes|tienen|hay)|(tienen?|tienes) (vestidos|conjuntos|blazers?|blusas|faldas|pantalones|enterizos|algo)|"
                           r"(vestidos|conjuntos|blazers|blusas|faldas|pantalones|enterizos)\b|otro|mas barat|algo mas|pasame|mandame|enviame|ensename|"
                           r"\bv\d{2}\b|cual (me )?(recomiendas|sugieres)")
RE_VARIAS = re.compile(r"otr[oa]s?\b|\bmas\b|opciones|modelos|catalogo|vestidos|conjuntos|blazers|blusas|faldas|pantalones|que (tienes|tienen|hay)|"
                       r"variedad|alternativ|parecid")
RE_LO_SIN_PRENDA = re.compile(r"\b(lo|la|los|las) (buscas|necesitas|quieres|usaras|usarias|vas a usar|estas buscando)\b|"
                              r"\b(buscarlo|usarlo|necesitarlo|buscarla|usarla)\b")
RE_PRENDA_CLIENTA = re.compile(r"vestid|conjunt|blaz|blus|fald|pantal|enteriz|jean|polo|set\b|prenda|ropa|modelo|algo\b")
RE_INVENTA_MENCION = re.compile(r"(el|la) que (me )?(mencionaste|comentaste|dijiste|me (pasaste|mandaste|enviaste))")
RE_PAGO_DATOS = re.compile(r"(yape|plin|cuenta|transferencia|titular|bcp|interbank|bbva)[^\n]{0,60}\b9\d{8}\b|\b\d{3}-\d{7,}-\d|\bcci\b")
RE_PRECIO = re.compile(r"s/\.?\s*\*?\s*(\d{2,4}(?:[.,]\d{2})?)")
RE_TALLA_AFIRMA = re.compile(r"(tenemos|hay|disponible|queda[n]?)[^.?!\n]{0,40}\b(xxl|xl|xs)\b")
RE_FECHA_MSG = re.compile(r"\b\d{1,2} de (ene|feb|mar|abr|may|jun|jul|ago|sep|set|oct|nov|dic)|\b(lunes|martes|miercoles|jueves|viernes|sabado|domingo)\b|"
                          r"\b(manana|pasado manana|hoy|fin de mes|proxima semana|en \w+ semanas|para (enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
                          r"septiembre|octubre|noviembre|diciembre))\b|\b\d{1,2}/\d{1,2}\b")
RE_INTERES = re.compile(r"^(me gusta|me encanta|me interesa|que lindo|que bonito|esta lindo|esta bonito|si me gusta|wow|bonito|lindo)\b")
RE_PREGUNTA_CLI = re.compile(r"\?|^(que|cual|cuanto|como|donde|cuando|tienen|tienes|hay|hacen|puedo|se puede)\b")
RE_FUERA = re.compile(r"partido|25 por 4|presidente|clima|carro|reunion|capacitar|boletas|teclados|codigo por fa|tramite|dominio|cierre\b")


def _precios_validos(cat: dict) -> set[int]:
    vals = set()
    envios = (15, 20)
    for p in cat.values():
        if p.get("precio") is not None:
            pr = int(round(float(p["precio"])))
            vals |= {pr, pr + 15, pr + 20, pr * 2}
    base = [int(round(float(p["precio"]))) for p in cat.values() if p.get("precio") is not None]
    vals |= {a + b for a in base for b in base}   # «el conjunto completo me sale S/ 450»: suma de dos prendas
    return vals | set(envios)


def reglas_turno(conv: dict, t: dict, prev: list[dict], cat: dict) -> list[tuple[str, str]]:
    """Errores de un turno: [(categoría, detalle)]."""
    e = []
    bot, cli = t["bot"] or "", _P(t["cliente"])
    pb = _P(bot)
    mem_a = t.get("mem_antes") or memoria.nueva()
    sab_a = (mem_a.get("sabemos") or {})
    anuncio = conv.get("desde_anuncio")
    if t["http"] != 200 or t.get("error") or (not bot.strip() and t.get("accion") in (None, "responder")):
        e.append(("error_o_vacia", f"HTTP {t['http']} {t.get('error', '')[:80]}"))
        return e
    if t["ms"] > 6000:
        e.append(("latencia", f"{t['ms']} ms"))
    # Fotos antes de conocer la necesidad sin que las pida (sin anuncio, sin foto suya, sin prenda nombrada).
    cliente_previo = " ".join(_P(x["cliente"]) for x in prev) + " " + cli
    nombra = bool(re.search(r"\bv\d{2}\b", cli)) or any(_P(p["nombre"]).split()[-1] in cli for p in cat.values() if p.get("nombre"))
    fotos = [f for f in t["fotos"] if f]
    mem_d = t.get("memoria") or mem_a
    sab_d = mem_d.get("sabemos") or {}
    if fotos and not anuncio and not t.get("foto") and not nombra and not mem_d.get("pidio_ver"):
        # Con lo que sabe DESPUÉS de leer el mensaje: «es un matrimonio de noche» completa la necesidad en ese turno.
        conoce = all(sab_d.get(memoria.DATO_DE[k]) or k in (mem_a.get("preguntado") or []) for k in memoria.INDAGAR)
        if not conoce and not RE_PIDE_FOTOS.search(cli):
            e.append(("fotos_sin_necesidad", f"{len(fotos)} foto(s) {fotos} sin conocer la necesidad ni pedirlas"))
    describiendo = mem_a.get("pendiente") in ("cual_prenda", "describir_prenda")   # «¿es alguno de estos?»: por diseño
    if len(set(fotos)) > 1 and not RE_VARIAS.search(cli) and not t.get("foto") and not describiendo:
        e.append(("varias_opciones", f"{len(set(fotos))} prendas {fotos} sin pedir opciones"))
    # Pregunta repetida: ya contestada (dato en la memoria antes del turno) o ya hecha dos veces antes.
    for q in memoria.preguntas_en(bot):
        kq = memoria.clave_de(q)
        if not kq or kq in ("confirmar", "otras_opciones", "cita", "cual_prenda", "describir_prenda", "foto"):
            continue
        dato = memoria.DATO_DE.get(kq)
        if dato and sab_a.get(dato):
            e.append(("pregunta_repetida", f"pregunta «{q.strip()[:70]}» y ya sabíamos {dato}={sab_a.get(dato)}"))
        elif sum(1 for x in prev if kq in [memoria.clave_de(z) for z in memoria.preguntas_en(x["bot"] or "")]) >= 2:
            e.append(("pregunta_repetida", f"tercera vez que pregunta {kq}: «{q.strip()[:70]}»"))
    # Prospección sin pregunta (el hilo se corta), salvo despedida, fuera del rubro o flujo del código.
    ya_abierta = "que_busca" in (mem_a.get("preguntado") or [])   # ya hizo la pregunta abierta: callar es correcto
    if (t["etapa"] == "prospeccion" and "?" not in bot and t.get("accion") == "responder" and t.get("intent") not in ("despedida", "cancelacion")
            and not ya_abierta
            and not re.search(r"no es mi giro|asesora|\*4\*", pb) and not RE_FUERA.search(cli) and t.get("modelo") not in ("espera_cual",)):
        e.append(("prospeccion_sin_pregunta", "en prospección y el bot no pregunta nada"))
    # «¿Para qué ocasión lo buscas?» sin prenda.
    preguntas_bot = " ".join(memoria.preguntas_en(pb))
    if RE_LO_SIN_PRENDA.search(preguntas_bot) and not (sab_a.get("prenda") or mem_a.get("producto") or mem_a.get("mostrados") or anuncio
                                             or RE_PRENDA_CLIENTA.search(cliente_previo) or t.get("foto") or fotos):
        e.append(("lo_sin_prenda", f"«{RE_LO_SIN_PRENDA.search(preguntas_bot).group(0)}» sin saber de qué prenda habla"))
    # Inventa una prenda que ella no mencionó.
    if RE_INVENTA_MENCION.search(pb) and not nombra and not any(x.get("foto") for x in prev + [t]):
        e.append(("prenda_inventada", f"«{RE_INVENTA_MENCION.search(pb).group(0)}» sin que ella la nombrara"))
    # Sin anuncio, el vestido del anuncio (V42) aparece sin que ella lo pida.
    if (not anuncio and ("V42" in fotos) and len(prev) < 2 and not re.search(r"gala|capa|azul|v42", cliente_previo)
            and not any(x.get("foto") for x in prev + [t])):
        e.append(("anuncio_asumido", "manda el vestido del anuncio sin que haya llegado por el anuncio"))
    # Precios que no existen.
    validos = _precios_validos(cat)
    for m in RE_PRECIO.finditer(pb):
        v = int(round(float(m.group(1).replace(",", "."))))
        if validos and v not in validos:
            e.append(("dato_falso", f"precio S/ {m.group(1)} no está en el catálogo"))
    if RE_TALLA_AFIRMA.search(pb) and not re.search(r"\bno (tenemos|hay|manejamos|contamos)|solo (tenemos|hay|manejamos)|hasta la l\b", pb):
        e.append(("dato_falso", f"afirma una talla que la tienda no tiene: «{RE_TALLA_AFIRMA.search(pb).group(0)}»"))
    # Talla agotada que se da por disponible (prenda en foco).
    foco = (t.get("memoria") or {}).get("producto")
    if foco in cat and cat[foco].get("stock"):
        for tl, n in cat[foco]["stock"].items():
            if n <= 0 and re.search(rf"(tenemos|hay|disponible)[^.?!\n]{{0,30}}\b{tl.lower()}\b", pb):
                e.append(("dato_falso", f"da por disponible la talla {tl} del {foco}, agotada"))
    # Etapas.
    ea, en = t["etapa_antes"] or "prospeccion", t["etapa"]
    compra = bool(etapas.RE_COMPRA.search(cli) or memoria.RE_CITA.search(cli) or etapas.RE_BOTON_TALLA.search(cli)
                  or re.search(r"lo quiero|quiero (ese|este|el)|me lo llevo|me llevo|probarmel|probarme|cita|separ|reserv|apart|confirm|"
                               r"pedido|compr|voy por|me quedo|lo llevo|puedo (ir|pasar)|ir a ver|paso (manana|el|hoy)", cli))
    pend_a = mem_a.get("pendiente")
    afirma_ok = pend_a in ("probar", "cita") or (pend_a in ("confirmar", "separar") and etapas.RE_AFIRMA.match(cli))
    if en in ("cierre", "venta_confirmada") and ea in ("prospeccion", "seguimiento") and not compra and not afirma_ok and t.get("accion") != "go_confirmar":
        e.append(("etapa_salto", f"{ea}→{en} sin intención de compra («{t['cliente'][:50]}»)"))
    if t.get("accion") == "pedido" and RE_INTERES.match(cli):
        e.append(("etapa_salto", "interés tratado como compra (arma pedido)"))
    if t.get("intent") in ("objecion", "objecion_precio") and RE_FECHA_MSG.search(cli) and not re.search(r"caro|pensar|no se|despues|luego", cli):
        e.append(("etapa_salto", f"fecha leída como objeción: «{t['cliente'][:50]}»"))
    # Cita aceptada fuera de horario o en refrigerio.
    cita = ((t.get("memoria") or {}).get("sabemos") or {}).get("cita")
    if cita and cita != sab_a.get("cita"):
        # Horario real del showroom (seed/tienda.md), escrito aquí a propósito: si memoria.py se equivoca, esto lo ve.
        hora = str(cita)[11:16]
        if not ("09:00" <= hora < "19:00") or "13:00" <= hora < "14:00":
            e.append(("cita_invalida", f"cita aceptada a las {cita}"))
    # Datos de pago antes de confirmar.
    if RE_PAGO_DATOS.search(pb) and en != "venta_confirmada":
        e.append(("pago_antes", "da datos de pago sin pedido confirmado"))
    return e


def reglas_conversacion(conv: dict, r: dict) -> list[tuple[int, str, str]]:
    out = []
    for i, t in enumerate(r["turnos"]):
        for cat_, det in reglas_turno(conv, t, r["turnos"][:i], CAT):
            out.append((i, cat_, det))
    if r.get("error"):
        out.append((len(r["turnos"]), "error_arnes", r["error"][:100]))   # falla de la clienta simulada, no del bot
    return out


CAT: dict = {}   # el catálogo real al momento de correr (se carga en main)

# ---------------------------------------------------------------------------
# Juez: Jev con preguntas tipadas sobre la conversación entera

JUEZ = {
    "respondio": ("noul", "¿La vendedora respondió (o reconoció y derivó) TODAS las preguntas concretas que hizo la clienta en `conversacion`? "
                  "Responde «true» si no dejó ninguna pregunta de la clienta sin atender."),
    "invento": ("noul", "¿La vendedora afirmó en algún momento algo sobre una prenda (precio, tela, color, tallas, detalles) o sobre la tienda "
                "(dirección, horario, envíos, pagos, promociones) que NO está en `datos_reales` o lo contradice?"),
    "presiono": ("noul", "¿La vendedora presionó a una clienta que dijo que solo estaba viendo, que lo iba a pensar o que su evento era lejano "
                 "(insistió en separarlo, en pagar o en apurarse)? «false» si la clienta no era así o si no hubo presión."),
    "hilo": ("noul", "¿La vendedora perdió el hilo: preguntó algo que la clienta ya había contestado, ignoró lo que la clienta acababa de "
             "decir, cambió de prenda sin que se lo pidieran o habló de algo que nadie mencionó?"),
    "robotica": ("noul", "¿El tono de la vendedora suena robótico, de formulario o repetitivo (las mismas frases), en vez de una vendedora "
                 "peruana cálida y natural?"),
    "avanzo": ("noul", "Si la clienta mostró interés en comprar o probarse algo, ¿la vendedora la llevó al siguiente paso (recomendar una "
               "prenda, dar el precio, ofrecer probárselo o separarlo)? «true» también si la clienta no mostró ese interés."),
}
UMBRAL_JUEZ = 0.70
ERROR_SI = {"respondio": False, "invento": True, "presiono": True, "hilo": True, "robotica": True, "avanzo": False}


def _datos_reales(r: dict) -> str:
    cods = set()
    for t in r["turnos"]:
        cods |= set(t["fotos"])
        if (t.get("memoria") or {}).get("producto"):
            cods.add(t["memoria"]["producto"])
        cods |= set(re.findall(r"\bV\d{2}\b", t["bot"] or ""))
        for c, p in CAT.items():
            if p.get("nombre") and _P(p["nombre"]).split()[-1] in _P(t["bot"] or ""):
                cods.add(c)
    fichas = [f"{c} {CAT[c]['nombre']}: S/ {CAT[c]['precio']}, color {CAT[c]['color']}, tallas {'/'.join(CAT[c]['tallas'])}, "
              f"stock {CAT[c].get('stock', {})}. {CAT[c]['descripcion'][:400]}" for c in sorted(cods) if c in CAT]
    tienda = ("TIENDA: showroom Juan Ayllón 459, Santa Anita (a 4 cuadras del Mall de Santa Anita), solo con cita, lunes a domingo "
              "9:00–19:00, refrigerio 13:00–14:00. Envíos: Lima S/ 15 (Olva), provincia S/ 20 (Olva o Shalom). Tallas S, M, L. "
              "Cambios solo por falla de fábrica en 7 días. Promociones/descuentos y datos de pago: los da una asesora (*4*).")
    try:
        demo = json.load(open(os.path.join(AQUI, "..", "seed", "producto_demo.json"), encoding="utf-8"))
        tienda += f" Material del {demo['codigo']} (lámina de la tienda): {demo.get('material', '')}"
    except (OSError, ValueError, KeyError):
        pass
    return tienda + "\nPRENDAS:\n" + ("\n".join(fichas) or "(ninguna)")


def juzgar(r: dict, clave: str, cuenta: Cuenta) -> dict:
    conv = []
    for t in r["turnos"]:
        x = {"clienta": t["cliente"], "vendedora": t["bot"]}
        if t["fotos"]:
            x["vendedora_envio_fotos_de"] = ", ".join(t["fotos"])
        conv.append(x)
    estado = {"tarea": "Evaluar a una vendedora de ropa por WhatsApp (bot) en una conversación con una clienta peruana.",
              "datos_reales": _datos_reales(r), "conversacion": conv}
    preguntas = {k: {"type": tipo, "instructions": ins} for k, (tipo, ins) in JUEZ.items()}
    for intento in range(2):
        try:
            j = _post(OPENROUTER + "/systemone", {"model": "typesafe/jev-1.13", "state": estado, "questions": preguntas}, clave, timeout=40)
            cuenta.sumar("juez", (j.get("usage") or {}).get("cost"))
            ans = j.get("answers") or {}
            return {k: round(float((ans.get(k) or {}).get("noul", 0.5)), 3) for k in JUEZ}
        except Exception as e:  # noqa: BLE001
            err = str(e)[:120]
            time.sleep(2)
    return {"error": err}


def errores_juez(j: dict) -> list[str]:
    out = []
    for k, malo in ERROR_SI.items():
        p = j.get(k)
        if p is None:
            continue
        if (malo and p >= UMBRAL_JUEZ) or (not malo and p <= 1 - UMBRAL_JUEZ):
            out.append("juez_" + k)
    return out


# ---------------------------------------------------------------------------
# Informe

GRAVEDAD = ["error_o_vacia", "dato_falso", "juez_invento", "pago_antes", "cita_invalida", "etapa_salto", "prenda_inventada",
            "anuncio_asumido", "fotos_sin_necesidad", "varias_opciones", "pregunta_repetida", "juez_hilo", "juez_respondio",
            "prospeccion_sin_pregunta", "lo_sin_prenda", "juez_presiono", "juez_avanzo", "juez_robotica", "latencia"]


def resumir(resultados: list[dict]) -> dict:
    por_cat: dict[str, dict] = {}
    sin_error, lat, costo = 0, [], 0.0
    for r in resultados:
        cats = {}
        for i, c, d in r.get("errores", []):
            cats.setdefault(c, []).append((i, d))
        for c in errores_juez(r.get("juez") or {}):
            cats.setdefault(c, []).append((-1, f"p={r['juez'].get(c[5:])}"))
        if not [c for c in cats if c != "error_arnes"]:
            sin_error += 1
        for c, v in cats.items():
            x = por_cat.setdefault(c, {"conversaciones": 0, "ocurrencias": 0, "ejemplos": []})
            x["conversaciones"] += 1
            x["ocurrencias"] += len(v)
            x["ejemplos"].append((r["id"], v[0]))
        lat += [t["ms"] for t in r["turnos"] if t.get("accion") != "go_confirmar"]
        costo += sum(float(t.get("costo") or 0) for t in r["turnos"])
    n = len(resultados) or 1
    return {"n": len(resultados), "sin_error": sin_error, "tasa_sin_error": round(sin_error / n, 3),
            "latencia_media_s": round(sum(lat) / max(1, len(lat)) / 1000, 2), "latencia_p90_s": round(sorted(lat)[int(0.9 * (len(lat) - 1))] / 1000, 2) if lat else 0,
            "turnos": sum(len(r["turnos"]) for r in resultados), "costo_agente_usd": round(costo, 4),
            "por_categoria": dict(sorted(por_cat.items(), key=lambda kv: GRAVEDAD.index(kv[0]) if kv[0] in GRAVEDAD else 99))}


def transcripcion(r: dict, marcar: int | None = None) -> str:
    out = [f"## {r['id']} ({r['tipo']}, canal {'web' if r['canal'] == 'web' else 'whatsapp'}{', desde anuncio' if r['desde_anuncio'] else ''})"]
    if r.get("persona"):
        out.append(f"   persona: {r['persona'][:160]}")
    for i, t in enumerate(r["turnos"]):
        flecha = " ◀" if i == marcar else ""
        out.append(f"👤 {t['cliente']}{flecha}")
        out.append(f"   [{t['etapa_antes'] or 'prospeccion'}→{t['etapa']} | {t.get('intent')} {t.get('confianza')} | {t.get('modelo')} | {t['ms']} ms]")
        for p in (t["bot"] or "").split("\n\n"):
            if p.strip():
                out.append("   🤖 " + p.replace("\n", " / ")[:300])
        if t["fotos"]:
            out.append("   📷 " + ", ".join(t["fotos"]))
    return "\n".join(out)


def cargar(ruta: str) -> list[dict]:
    """Resultados de una corrida, con los errores de reglas RECALCULADOS con las reglas de hoy y el catálogo de esa
    corrida: así una regla nueva o corregida se aplica igual al antes y al después."""
    res = [json.loads(x) for x in open(ruta, encoding="utf-8") if x.strip()]
    cat_ruta = ruta + ".catalogo.json"
    if os.path.exists(cat_ruta):
        CAT.clear()
        CAT.update(json.load(open(cat_ruta, encoding="utf-8")))
        convs = {}
        if os.path.exists(CORPUS):
            convs = {c["id"]: c for c in (json.loads(x) for x in open(CORPUS, encoding="utf-8") if x.strip())}
        for r in res:
            r["errores"] = reglas_conversacion(convs.get(r["id"], r), r)
    return res


def informe(ruta: str, comparar: str | None = None, ejemplos: int = 2) -> str:
    res = cargar(ruta)
    s = resumir(res)
    lineas = [f"# Informe {os.path.basename(ruta)}", "",
              f"Conversaciones: {s['n']} · sin error: {s['sin_error']} ({s['tasa_sin_error']:.0%}) · turnos: {s['turnos']} · "
              f"latencia media {s['latencia_media_s']} s (p90 {s['latencia_p90_s']} s) · costo del agente US$ {s['costo_agente_usd']}"]
    otro = resumir(cargar(comparar)) if comparar else None
    if comparar:
        res = cargar(ruta)   # cargar() deja en CAT el catálogo de la última corrida leída
    if otro:
        lineas.append(f"Comparado con {os.path.basename(comparar)}: sin error {otro['sin_error']}/{otro['n']} ({otro['tasa_sin_error']:.0%}) · "
                      f"latencia {otro['latencia_media_s']} s · costo US$ {otro['costo_agente_usd']}")
    lineas += ["", "| Categoría | Conversaciones | Ocurrencias |" + (" Después (conv.) |" if otro else ""), "|---|---|---|" + ("---|" if otro else "")]
    cats = list(s["por_categoria"]) + [c for c in (otro or {}).get("por_categoria", {}) if c not in s["por_categoria"]]
    cats.sort(key=lambda c: GRAVEDAD.index(c) if c in GRAVEDAD else 99)
    for c in cats:
        x = s["por_categoria"].get(c, {"conversaciones": 0, "ocurrencias": 0})
        fila = f"| {c} | {x['conversaciones']} | {x['ocurrencias']} |"
        if otro:
            fila += f" {otro['por_categoria'].get(c, {}).get('conversaciones', 0)} |"
        lineas.append(fila)
    por_id = {r["id"]: r for r in res}
    for c, x in s["por_categoria"].items():
        lineas += ["", f"### {c} ({x['conversaciones']} conversaciones)"]
        for cid, (i, d) in x["ejemplos"][:ejemplos]:
            lineas += [f"- {cid}, turno {i + 1 if i >= 0 else '—'}: {d}", "```", transcripcion(por_id[cid], i if i >= 0 else None), "```"]
    return "\n".join(lineas)


# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generar")
    g.add_argument("--semilla", type=int, default=SEMILLA)
    c = sub.add_parser("correr")
    c.add_argument("--url", default="http://127.0.0.1:18483")
    c.add_argument("--conjunto", choices=("dev", "reservada", "todas"), default="dev")
    c.add_argument("--ids", default="", help="solo estas conversaciones (coma)")
    c.add_argument("--salida", required=True)
    c.add_argument("--hilos", type=int, default=4)
    c.add_argument("--tope", type=float, default=0.30, help="US$ máximos (agente + clienta + juez)")
    c.add_argument("--clienta", default="deepseek/deepseek-v4-flash")
    c.add_argument("--sin-juez", action="store_true")
    c.add_argument("--env", default=os.path.join(AQUI, "..", "..", ".env"))
    c.add_argument("--catalogo", default="https://proyectopostventa.site/baruka/api/public/catalog")
    c.add_argument("--stock", default="https://proyectopostventa.site/baruka/api/public/stock")
    rj = sub.add_parser("rejuzgar", help="vuelve a pasar el juez (Jev) sobre resultados guardados: mismo juez para antes y después")
    rj.add_argument("ruta")
    rj.add_argument("--tope", type=float, default=0.03)
    rj.add_argument("--env", default=os.path.join(AQUI, "..", "..", ".env"))
    i = sub.add_parser("informe")
    i.add_argument("ruta")
    i.add_argument("--comparar")
    i.add_argument("--ejemplos", type=int, default=2)
    a = ap.parse_args(argv)

    if a.cmd == "generar":
        os.makedirs(DIR, exist_ok=True)
        convs = generar(a.semilla)
        with open(CORPUS, "w", encoding="utf-8") as fh:
            for x in convs:
                fh.write(json.dumps(x, ensure_ascii=False) + "\n")
        n = {}
        for x in convs:
            n[(x["tipo"], x["conjunto"])] = n.get((x["tipo"], x["conjunto"]), 0) + 1
        print(f"{len(convs)} conversaciones en {CORPUS}: {dict(sorted(n.items()))}")
        return
    if a.cmd == "informe":
        print(informe(a.ruta, a.comparar, a.ejemplos))
        return

    clave = _clave(a.env)
    if not clave:
        sys.exit("falta OPENROUTER_API_KEY (o --env)")
    if a.cmd == "rejuzgar":
        res = cargar(a.ruta)
        cuenta = Cuenta(a.tope)
        with cf.ThreadPoolExecutor(4) as ex:
            jueces = list(ex.map(lambda r: juzgar(r, clave, cuenta) if r["turnos"] and not cuenta.agotada() else r.get("juez"), res))
        with open(a.ruta, "w", encoding="utf-8") as fh:
            for r, j in zip(res, jueces):
                fh.write(json.dumps(dict(r, juez=j), ensure_ascii=False) + "\n")
        print(f"rejuzgadas {len(res)} · gasto US$ {cuenta.total:.4f}")
        return
    convs = [json.loads(x) for x in open(CORPUS, encoding="utf-8") if x.strip()]
    if a.ids:
        ids = set(a.ids.split(","))
        convs = [x for x in convs if x["id"] in ids]
    elif a.conjunto != "todas":
        convs = [x for x in convs if x["conjunto"] == a.conjunto]
    CAT.update(catalogo(a.catalogo, a.stock))
    with open(a.salida + ".catalogo.json", "w", encoding="utf-8") as fh:
        json.dump(CAT, fh, ensure_ascii=False)
    antes = saldo(clave)
    print(f"{len(convs)} conversaciones · tope US$ {a.tope} · saldo de la clave: {antes}", flush=True)
    cuenta = Cuenta(a.tope)
    hechas = set()
    if os.path.exists(a.salida):   # reanudar: no repite las ya corridas
        hechas = {json.loads(x)["id"] for x in open(a.salida, encoding="utf-8") if x.strip()}
    lock = threading.Lock()

    def una(conv):
        if conv["id"] in hechas or cuenta.agotada():
            return None
        r = correr_una(conv, a.url.rstrip("/"), clave, a.clienta, cuenta)
        r["errores"] = reglas_conversacion(conv, r)
        if not a.sin_juez and r["turnos"] and not cuenta.agotada():
            r["juez"] = juzgar(r, clave, cuenta)
        with lock:
            with open(a.salida, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"  {r['id']}: {len(r['turnos'])} turnos, {len(r['errores'])} errores de reglas, juez {errores_juez(r.get('juez') or {})} "
              f"· gasto {cuenta.total:.4f}", flush=True)
        return r

    with cf.ThreadPoolExecutor(a.hilos) as ex:
        list(ex.map(una, convs))
    print(f"gasto US$ {cuenta.total:.4f} {({k: round(v, 4) for k, v in cuenta.partes.items()})} · saldo: {saldo(clave)}")
    if cuenta.agotada():
        print("TOPE ALCANZADO: se dejaron conversaciones sin correr")


if __name__ == "__main__":
    main()
