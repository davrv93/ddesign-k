"""Motores de decisión (spec §5–8, «Jev style»).

Un motor de decisión recibe el estado estructurado y devuelve decisiones con opciones de un conjunto cerrado. Nunca
escribe texto para la clienta. Implementaciones intercambiables:

- ReglasDecision: determinista, sin modelo. Es la referencia y el respaldo.
- JevStyleDecision: un juez que responde JSON con enums cerrados (llama-server local con json_schema, o cualquier
  función que reciba el prompt y devuelva texto). Si su salida no valida, lanza ValueError y el motor cae a reglas.

El estado de herramientas que leen las reglas: `herramientas.stock` = {codigo: "online" | "sucursal" | ""},
`herramientas.rag` = [codigos] (o no existe si aún no se buscó) y `herramientas.crm` = lo que sabemos de la clienta
por sus pedidos anteriores.

Las reglas también leen la intención del turno (`conversation.intent`, la del clasificador de V1) y si hay una
pregunta que el código quiere hacer (`conversation.next_question`)."""
from __future__ import annotations

import json
import re
from typing import Callable

from .interfaces import Decision

OPCIONES = {
    "next_action": ("consultar_stock", "buscar_alternativa", "recomendar", "preguntar", "responder",
                    "pedir_asesora", "derivar"),
    "intent": ("purchase", "product_information", "support", "greeting", "other"),
}


# La clienta pide una persona o pregunta fuera del giro (cripto, empleo, reclamos…):
# se deriva a la dueña en vez de recomendar lo más cercano. Capa léxica de actos
# inequívocos: solo patrones que no se confunden («mi persona favorita» no pide persona).
FUERA_DE_ALCANCE = frozenset({"asesora"})
_PIDE_PERSONA_RE = re.compile(r"(hablar|conversar).{0,25}persona|\basesora\b|\bencargad[oa]\b|\bhumano\b|eres un bot")
_FUERA_GIRO_RE = re.compile(r"criptomoneda|bitcoin|\b(quiero|buscan)\b.{0,25}(trabaj|empleo|personal)|bolsa de valores|pr[eé]stamo|\breclamo\b|libro de reclamaciones|\bdenuncia\b")


def plano(texto: str) -> str:
    """Minúsculas y sin tildes, para los patrones de arriba."""
    import unicodedata
    t = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def fuera_de_giro(mensaje: str) -> bool:
    """Pregunta ajena al negocio (cripto, empleo, bolsa, préstamo, denuncia). Es lo único que V2 deriva por su cuenta: pedir
    una persona («eres un bot», «tengo que hablarlo con alguien») ya lo resuelve V1 con sus guardas."""
    return bool(_FUERA_GIRO_RE.search(plano(mensaje)))


def _texto_plano(estado: dict) -> str:
    return plano((estado.get("conversation") or {}).get("last_user_message") or "")


# Intenciones en las que la clienta pide un dato o cierra: ahí se responde con hechos (o con un flujo de código); no se
# recomienda ni se pregunta otra cosa.
INTENCIONES_INFORMATIVAS = frozenset({
    "consulta_material", "consulta_precio", "consulta_talla", "consulta_color", "consulta_disponibilidad",
    "consulta_ubicacion", "consulta_horario", "consulta_delivery", "consulta_pago", "objecion_precio", "objecion",
    "despedida", "cancelacion", "intencion_compra", "confirmacion_compra",
})


class ReglasDecision:
    nombre = "reglas"

    def decide(self, estado: dict, decisiones: list[str]) -> list[Decision]:
        foco = (estado.get("product") or {}).get("focus")
        herr = estado.get("herramientas") or {}
        stock = herr.get("stock") or {}
        rag = herr.get("rag")                       # None = no buscado; [] = buscado y vacío
        out: list[Decision] = []
        if "intent" in decisiones:
            req = estado.get("requirements") or {}
            compra = bool(req.get("ocasion") or req.get("prenda") or foco)
            out.append(Decision("intent", "purchase" if compra else "other", 0.7))
        if "next_action" in decisiones:
            out.append(self._accion(estado, foco, stock, rag))
        return out

    @staticmethod
    def _accion(estado: dict, foco, stock: dict, rag) -> Decision:
        conv = estado.get("conversation") or {}
        prod = estado.get("product") or {}
        pregunta = conv.get("next_question")
        plano = _texto_plano(estado)
        if (conv.get("intent") in FUERA_DE_ALCANCE or _PIDE_PERSONA_RE.search(plano)
                or _FUERA_GIRO_RE.search(plano)):
            return Decision("next_action", "pedir_asesora", 0.9)   # fuera de alcance: deriva a la dueña
        if conv.get("intent") in INTENCIONES_INFORMATIVAS:
            return Decision("next_action", "responder", 0.8)                  # pidió un dato o cierra: se contesta
        if conv.get("wants_to_see") and (not foco or foco in (prod.get("shown") or [])):
            return ReglasDecision._para_ver(estado, stock, rag, pregunta)   # pide ver prendas: se buscan con el RAG
        if not foco:
            return Decision("next_action", "preguntar" if pregunta else "responder", 0.7)
        if foco not in stock:
            return Decision("next_action", "consultar_stock", 0.9, arg=foco)  # hecho que falta: consultarlo
        if stock[foco] == "online":
            if foco in (prod.get("shown") or []):                              # ya la vio: se avanza con la conversación
                return Decision("next_action", "preguntar" if pregunta else "responder", 0.8)
            return Decision("next_action", "recomendar", 0.9, arg=foco)
        if stock[foco] == "sucursal":
            return Decision("next_action", "responder", 0.8)                  # solo en tienda física
        # Sin stock de la prenda en foco: buscar alternativas con stock, sin inventarlas.
        if rag is None:
            return Decision("next_action", "buscar_alternativa", 0.8)
        con_stock = next((c for c in rag if stock.get(c) == "online"), None)
        if con_stock:                                                          # una alternativa ya tiene stock: basta
            return Decision("next_action", "recomendar", 0.8, arg=con_stock)
        por_mirar = next((c for c in rag if c != foco and c not in stock), None)
        if por_mirar:
            return Decision("next_action", "consultar_stock", 0.8, arg=por_mirar)
        return Decision("next_action", "preguntar", 0.8)                      # sin alternativas: preguntar si le interesa otra


def _para_ver(estado: dict, stock: dict, rag, pregunta) -> Decision:
    """Pidió ver prendas (u otras opciones) y no hay una prenda nueva en foco: busca candidatas con el RAG, las
    comprueba con el stock y recomienda la primera que tenga y que todavía no haya visto."""
    vistos = set((estado.get("product") or {}).get("shown") or [])
    if rag is None:
        return Decision("next_action", "buscar_alternativa", 0.8)
    ok = next((c for c in rag if c not in vistos and stock.get(c) == "online"), None)
    if ok:
        return Decision("next_action", "recomendar", 0.8, arg=ok)
    por_mirar = next((c for c in rag if c not in vistos and c not in stock), None)
    if por_mirar:
        return Decision("next_action", "consultar_stock", 0.8, arg=por_mirar)
    return Decision("next_action", "preguntar" if pregunta else "responder", 0.7)


ReglasDecision._para_ver = staticmethod(_para_ver)


def _prompt(estado: dict, decisiones: list[str]) -> str:
    compacto = json.dumps(estado, ensure_ascii=False, default=str)[:3000]
    opciones = {d: list(OPCIONES[d]) for d in decisiones if d in OPCIONES}
    return (
        "Eres el motor de decisión de una tienda. Devuelve SOLO JSON, sin texto alrededor, con la forma "
        '{"decisiones":[{"decision":"<nombre>","choice":"<opción>","confianza":0.0}]}. '
        "Elige únicamente opciones de la lista; no inventes productos, precios ni stock.\n"
        f"OPCIONES: {json.dumps(opciones, ensure_ascii=False)}\n"
        f"ESTADO: {compacto}"
    )


def parsear(crudo: str, pedidas: list[str]) -> list[Decision]:
    """Valida la salida del juez. Cualquier desvío lanza ValueError: nada no validado llega al plan."""
    texto = re.sub(r"^```(?:json)?|```$", "", (crudo or "").strip(), flags=re.M).strip()
    try:
        js = json.loads(texto)
    except ValueError as e:
        raise ValueError(f"JSON inválido: {e}")
    items = js.get("decisiones") if isinstance(js, dict) else None
    if not isinstance(items, list):
        raise ValueError("falta la lista «decisiones»")
    vistas: dict[str, Decision] = {}
    for it in items:
        if not isinstance(it, dict):
            raise ValueError("decisión que no es objeto")
        nombre, choice = it.get("decision"), it.get("choice")
        if nombre not in OPCIONES:
            if nombre in pedidas:
                raise ValueError(f"decisión sin conjunto cerrado: {nombre}")
            continue
        if choice not in OPCIONES[nombre]:
            raise ValueError(f"opción fuera de lista en {nombre}: {choice!r}")
        try:
            conf = float(it.get("confianza"))
        except (TypeError, ValueError):
            raise ValueError("confianza no numérica")
        if not 0.0 <= conf <= 1.0:
            raise ValueError(f"confianza fuera de 0–1: {conf}")
        vistas[nombre] = Decision(nombre, choice, conf, fuente="juez")
    faltan = [d for d in pedidas if d in OPCIONES and d not in vistas]
    if faltan:
        raise ValueError(f"faltan decisiones: {faltan}")
    return [vistas[d] for d in pedidas if d in vistas]


class JevStyleDecision:
    nombre = "jev-style"

    def __init__(self, juzgar: Callable[[str], str]):
        self.juzgar = juzgar

    def decide(self, estado: dict, decisiones: list[str]) -> list[Decision]:
        return parsear(self.juzgar(_prompt(estado, decisiones)), decisiones)


def juez_llama(url: str, timeout_s: float) -> Callable[[str], str]:
    """Juez local: llama-server (OpenAI-compatible) en la misma red. Temperatura 0 y JSON pedido al servidor.
    No apunta a ningún proveedor externo. Sin probar contra un servidor vivo hasta la fase 3 (benchmark)."""
    import httpx

    def juzgar(prompt: str) -> str:
        r = httpx.post(f"{url.rstrip('/')}/v1/chat/completions", timeout=timeout_s, json={
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": 200,
            "response_format": {"type": "json_object"},
        })
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    return juzgar


# --- Jev local (System One) --------------------------------------------------------------------------------------
# El servidor local de Jev (jev-style-0.8b-decision-v3) contesta preguntas con opciones cerradas y devuelve la
# probabilidad de cada una: POST {url}/v1/systemone {model, state, questions} → answers. ~70 ms con el modelo caliente.
ACCIONES_JEV = {
    "preguntar": "Falta un dato que el asistente ya sabe cuál es: hacer UNA sola pregunta, sin recomendar todavía.",
    "recomendar": "Ya hay una prenda adecuada que la clienta todavía no ha visto: recomendarla con su foto.",
    "responder": "La clienta pide un dato (precio, tela, talla, envío, pago) o se despide: contestarlo, sin recomendar.",
    "pedir_asesora": "La clienta pide hablar con una persona, o se queja, o el asistente no puede ayudar.",
}


def _estado_jev(estado: dict) -> dict:
    """El estado que ve Jev: en español, compacto y sin el nombre de la clienta."""
    conv = estado.get("conversation") or {}
    prod = estado.get("product") or {}
    req = {k: v for k, v in (estado.get("requirements") or {}).items() if v}
    st = {
        "etapa_de_venta": conv.get("stage") or "prospeccion",
        "mensaje_de_la_clienta": conv.get("last_user_message") or "",
        "historial": [{"rol": t["rol"], "texto": t["texto"]} for t in (conv.get("recent_turns") or [])][-4:],
        "lo_que_ya_sabemos": ", ".join(f"{k}: {v}" for k, v in req.items()) or "nada todavía",
        "prenda_en_foco": prod.get("focus") or "ninguna",
        "prenda_ya_mostrada": bool(prod.get("focus") and prod.get("focus") in (prod.get("shown") or [])),
        "intencion_detectada": conv.get("intent") or "desconocida",
        "la_clienta_pide_ver_prendas": bool(conv.get("wants_to_see")),
        "pregunta_que_quiere_hacer_el_codigo": (conv.get("next_question") or {}).get("tipo") or "ninguna",
    }
    return st


class JevSystemOneDecision:
    """Decide la siguiente acción con Jev local. Jev PROPONE una de cuatro acciones de negocio; el CÓDIGO decide si
    hace falta una herramienta (stock), degrada lo que no tiene respaldo y cae a reglas si Jev duda o falla."""
    nombre = "jev-systemone"

    def __init__(self, url: str, modelo: str = "jev-style-0.8b-decision-v3", timeout_s: float = 1.5,
                 umbral: float = 0.5, cliente=None):
        self.url = url.rstrip("/")
        self.modelo = modelo
        self.timeout_s = timeout_s
        self.umbral = umbral
        self._cliente = cliente
        self._reglas = ReglasDecision()
        self.ultima: dict | None = None            # para la traza: lo que contestó Jev en la última decisión

    def _post(self, cuerpo: dict) -> dict:
        if self._cliente is not None:
            return self._cliente(self.url + "/v1/systemone", cuerpo, self.timeout_s)
        import httpx
        r = httpx.post(self.url + "/v1/systemone", json=cuerpo, timeout=self.timeout_s)
        r.raise_for_status()
        return r.json()

    def _proponer(self, estado: dict) -> tuple[str, float, dict]:
        cuerpo = {"model": self.modelo, "state": _estado_jev(estado), "questions": {"next_action": {
            "type": "choice", "criteria": ACCIONES_JEV,
            "instructions": "¿Cuál es la siguiente acción correcta del asistente de ventas? Usa solo lo que dice el estado.",
        }}}
        resp = self._post(cuerpo)
        r = (resp.get("answers") or {}).get("next_action") or {}
        opcion, probs = r.get("choice"), r.get("probabilities") or {}
        if opcion not in ACCIONES_JEV:
            raise ValueError(f"opción fuera de lista: {opcion!r}")
        try:
            p = float(probs.get(opcion, r.get("confidence", 0)))
        except (TypeError, ValueError):
            raise ValueError("probabilidad no numérica")
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"probabilidad fuera de 0–1: {p}")
        return opcion, p, {k: round(float(v), 3) for k, v in probs.items()}

    def decide(self, estado: dict, decisiones: list[str]) -> list[Decision]:
        out = [d for d in self._reglas.decide(estado, [x for x in decisiones if x != "next_action"])]
        if "next_action" not in decisiones:
            return out
        herr = estado.get("herramientas") or {}
        if herr.get("jev_resuelta"):                 # la 2.ª vuelta del ciclo (ya hay stock): no se vuelve a preguntar a Jev
            propuesta = herr["jev_resuelta"]
        else:
            opcion, p, probs = self._proponer(estado)
            self.ultima = {"opcion": opcion, "p": round(p, 3), "probs": probs}
            if p < self.umbral:
                out.append(self._reglas._accion(estado, (estado.get("product") or {}).get("focus"),
                                                herr.get("stock") or {}, herr.get("rag")))
                out[-1] = Decision(out[-1].decision, out[-1].choice, out[-1].confianza, "respaldo", out[-1].arg)
                return out
            propuesta = opcion
            herr["jev_resuelta"] = opcion            # el motor guarda el estado: la siguiente vuelta lo reutiliza
            estado["herramientas"] = herr
        out.append(self._codigo_decide(estado, propuesta))
        return out

    def _codigo_decide(self, estado: dict, propuesta: str) -> Decision:
        """Jev propuso; el código decide qué se puede hacer con eso."""
        conv, prod = estado.get("conversation") or {}, estado.get("product") or {}
        foco = prod.get("focus")
        herr = estado.get("herramientas") or {}
        stock = herr.get("stock") or {}
        pregunta = conv.get("next_question")
        if propuesta == "recomendar":
            if not foco or foco in (prod.get("shown") or []):
                if conv.get("wants_to_see"):                       # pide ver: el RAG busca, el stock confirma
                    r = ReglasDecision._para_ver(estado, stock, herr.get("rag"), pregunta)
                    return Decision(r.decision, r.choice, r.confianza, "jev" if r.choice == "recomendar" else "regla", r.arg)
                return Decision("next_action", "preguntar" if pregunta else "responder", 0.6, "regla")   # nada nuevo
            r = self._reglas._accion({**estado, "conversation": {**conv, "intent": None}}, foco, stock, herr.get("rag"))
            return Decision(r.decision, r.choice, r.confianza, "jev" if r.choice == "recomendar" else "regla", r.arg)
        if propuesta == "preguntar":
            return Decision("next_action", "preguntar" if pregunta else "responder", 0.7, "jev" if pregunta else "regla")
        return Decision("next_action", propuesta, 0.7, "jev")
