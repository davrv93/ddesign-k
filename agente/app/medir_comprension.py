"""¿Cuánto entiende hoy el bot de lo que pide la clienta, con frases que NINGUNA regla vio?

    python3 -m app.medir_comprension generar [--tope 0.10]     # DeepSeek escribe el conjunto → data/comprension_prueba.jsonl
    python3 -m app.medir_comprension evaluar --url http://127.0.0.1:18497

Para cada pedido (`ETIQUETAS`) se generan mensajes que lo piden, TRAMPAS que se le parecen pero no lo piden, y
combinaciones de dos pedidos en un mensaje. Se evalúa el sistema como lo usa el bot: las reglas (`SolicitudCliente`,
`respuestas.hechos`) más la intención del clasificador local (`/clasificar` del agente). Mide recall (lo pide y se
detecta) y falsas alarmas (no lo pide y se detecta). El conjunto NO entra al entrenamiento: sirve para decidir si hace
falta un clasificador entrenado para la comprensión (la medición del 06-10 decía «≈1,5 % ayudaría la semántica», pero
sobre frases con las que se afinaron las reglas).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import urllib.request

from . import memoria, respuestas, venta
from .solicitud import color_que_pide, interpretar

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(AQUI, "..", "data", "comprension_prueba.jsonl")
ENTRENAMIENTO = os.path.join(AQUI, "..", "data", "comprension_entrenamiento.jsonl")
MODELO = "deepseek/deepseek-chat-v3.1"

# etiqueta → (lo que pide, en palabras para el generador)
ETIQUETAS = {
    "precio": "pregunta cuánto cuesta una prenda",
    "talla": "pregunta si hay su talla o qué tallas hay, o dice qué talla usa",
    "color": "pregunta si la prenda viene en un color o qué colores hay",
    "envio": "pregunta por el envío o delivery: si envían, a dónde, cuánto cuesta o cuánto demora",
    "ubicacion": "pregunta dónde queda la tienda o el showroom, o el horario de atención",
    "cita": "quiere ir a probarse la prenda o agendar una visita en un día u hora",
    "pago": "pregunta cómo se paga o qué medios de pago aceptan (todavía no compró)",
    "rebaja": "pide un descuento o regatea el precio",
    "otras_opciones": "pide ver otros modelos u otras opciones distintas a la que le mostraron",
    "mas_barato": "pide algo más barato o más económico",
    "catalogo": "pide ver el catálogo o todo lo que tienen",
    "elogio": "elogia una prenda que le mostraron (le encantó, qué linda) sin preguntar nada",
    "tela": "pregunta la tela o el material de la prenda",
    "despedida": "se despide o agradece para cerrar la conversación",
    "ya_pago": "avisa que ya pagó, yapeó o transfirió, o que manda el comprobante",
    "no_mostrar": "pide que NO le manden más fotos u otros modelos",
}
COMBOS = [("precio", "talla"), ("precio", "envio"), ("tela", "envio"), ("talla", "envio"), ("precio", "color"),
          ("precio", "cita"), ("rebaja", "precio"), ("color", "talla"), ("ubicacion", "cita"), ("elogio", "precio")]


def detecta(msg: str, intent: str) -> set[str]:
    """Lo que el sistema de hoy detecta en el mensaje (reglas + intención comercial del clasificador)."""
    p = memoria._plano(msg)
    s = interpretar(msg)
    out = set()
    if intent == "consulta_precio" or respuestas.RE_PRECIO.search(p):
        out.add("precio")
    if intent in ("consulta_talla", "consulta_disponibilidad") or respuestas.RE_TALLAS.search(p) or re.search(r"\b(talla|soy|uso)\s+(xs|s|m|l|xl)\b", p):
        out.add("talla")
    if s.pide_color or color_que_pide(msg) or intent == "consulta_color":
        out.add("color")
    if intent == "consulta_delivery" or respuestas.RE_ENVIO.search(p):
        out.add("envio")
    if intent in ("consulta_ubicacion", "consulta_horario") or respuestas.RE_UBICACION.search(p):
        out.add("ubicacion")
    if s.pide_cita:
        out.add("cita")
    if intent == "consulta_pago":
        out.add("pago")
    if s.pide_rebaja or respuestas.RE_DESCUENTO.search(p):
        out.add("rebaja")
    if s.mas_opciones or intent == "comparacion":
        out.add("otras_opciones")
    if s.mas_barato:
        out.add("mas_barato")
    if s.catalogo:
        out.add("catalogo")
    if s.elogia or intent == "interesado":
        out.add("elogio")
    if venta.pregunta_material(msg, intent):
        out.add("tela")
    if intent == "despedida":
        out.add("despedida")
    if s.avisa_pago:
        out.add("ya_pago")
    if s.no_mostrar:
        out.add("no_mostrar")
    return out


# ---------------------------------------------------------------------------
# Generación (DeepSeek por OpenRouter; la clave no se imprime)

def _clave() -> str:
    k = os.environ.get("OPENROUTER_API_KEY", "")
    for ruta in (os.path.join(AQUI, "..", "..", "..", "ddesign-k", ".env"), os.path.join(AQUI, "..", "..", ".env")):
        if not k and os.path.exists(ruta):
            for linea in open(ruta, encoding="utf-8"):
                if linea.startswith("OPENROUTER_API_KEY="):
                    k = linea.split("=", 1)[1].strip()
    return k


BASE = ("Eres una clienta peruana que escribe por WhatsApp a Baruka Design, una tienda de vestidos, conjuntos y blusas de mujer en Lima. "
        "Escribe como escribe la gente de verdad: jerga peruana (pe, causa, al toque, nomás), sin tildes o con faltas de ortografía a veces, "
        "frases cortas y largas, indirectas, a veces con saludo o con algo más en el mismo mensaje. Nombres de prendas posibles: Pandora, Holly, "
        "Kendall, Irla, Azra, Xela, Kaylee. Cada mensaje distinto en arranque y estructura. Responde SOLO JSON: {\"mensajes\": [\"...\", ...]}.\n\n")


def _pedir(clave: str, prompt: str) -> tuple[list[str], float]:
    cuerpo = {"model": MODELO, "temperature": 1.0, "max_tokens": 2500, "response_format": {"type": "json_object"},
              "reasoning": {"enabled": False}, "usage": {"include": True}, "messages": [{"role": "user", "content": BASE + prompt}]}
    r = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", json.dumps(cuerpo).encode(),
                               {"content-type": "application/json", "Authorization": "Bearer " + clave})
    j = json.load(urllib.request.urlopen(r, timeout=120))
    txt = j["choices"][0]["message"]["content"]
    msgs = json.loads(re.search(r"\{.*\}", txt, re.S).group(0)).get("mensajes") or []
    return [str(m).strip() for m in msgs if str(m).strip()], float((j.get("usage") or {}).get("cost") or 0)


def generar(args) -> int:
    clave = _clave()
    if not clave:
        raise SystemExit("falta OPENROUTER_API_KEY")
    entr = args.conjunto == "entrenamiento"
    # El de entrenamiento: el doble de frases, en tandas con otro estilo pedido, para que no se parezca al de prueba.
    n_si, n_no, n_combo, tandas = (15, 8, 5, [""]) if not entr else (20, 8, 6, [
        "Que sean mensajes cortos, de una línea, muy coloquiales. ", "Que sean mensajes más largos, contando su situación antes de pedir. "])
    tareas = []
    for estilo in tandas:
        for et, desc in ETIQUETAS.items():
            tareas.append(({"si": [et], "no": []}, f"{estilo}Escribe {n_si} mensajes distintos en los que la clienta {desc}."))
            tareas.append(({"si": [], "no": [et]}, f"{estilo}Escribe {n_no} mensajes que se PAREZCAN a cuando una clienta {desc}, pero que NO lo sean "
                                                   "(confusiones típicas: mismas palabras con otro sentido, o hablando de otra cosa). "
                                                   f"Ninguno debe pedir esto: {desc}."))
        for a, b in COMBOS:
            tareas.append(({"si": [a, b], "no": []}, f"{estilo}Escribe {n_combo} mensajes en los que la clienta, en el MISMO mensaje, "
                                                     f"{ETIQUETAS[a]} Y ADEMÁS {ETIQUETAS[b]}."))
    gasto, filas = 0.0, []

    def hacer(t):
        try:
            return t[0], *_pedir(clave, t[1])
        except Exception as e:  # noqa: BLE001
            return t[0], [], 0.0

    with cf.ThreadPoolExecutor(8) as pool:
        for etq, msgs, costo in pool.map(hacer, tareas):
            gasto += costo
            filas += [{"mensaje": m, **etq} for m in msgs]
            if gasto > args.tope:
                break
    destino = ENTRENAMIENTO if entr else SALIDA
    if entr:      # ninguna frase del de prueba (ni casi igual) entra al de entrenamiento
        prueba = {memoria._plano(json.loads(l)["mensaje"]) for l in open(SALIDA, encoding="utf-8") if l.strip()}
        filas = [x for x in filas if memoria._plano(x["mensaje"]) not in prueba]
    with open(destino, "w", encoding="utf-8") as f:
        for x in filas:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    print(f"{len(filas)} mensajes → {os.path.relpath(destino)} · gasto US$ {gasto:.4f}")
    return 0


# ---------------------------------------------------------------------------
# Evaluación

def evaluar(args) -> int:
    filas = [json.loads(l) for l in open(SALIDA, encoding="utf-8") if l.strip()]
    tp, fn, fp, neg = ({e: 0 for e in ETIQUETAS} for _ in range(4))
    perdidas, alarmas = {e: [] for e in ETIQUETAS}, {e: [] for e in ETIQUETAS}
    vecs = None
    if args.sistema != "reglas":       # el clasificador entrenado (app/comprension.py) necesita el vector e5 de cada frase
        from . import comprension
        from .modelo import Embedder
        vecs = Embedder()([x["mensaje"] for x in filas])
    for i, x in enumerate(filas):
        r = urllib.request.Request(args.url + "/clasificar", json.dumps({"texto": x["mensaje"][:2000]}).encode(), {"content-type": "application/json"})
        cl = json.load(urllib.request.urlopen(r, timeout=30))
        reglas = detecta(x["mensaje"], (cl.get("comercial") or {}).get("intent", ""))
        clf = comprension.detecta(vecs[i], x["mensaje"], todas=args.sistema == "clf") if vecs is not None else set()
        d = reglas if args.sistema == "reglas" else clf if args.sistema == "clf" else (reglas | clf)
        for e in x["si"]:
            if e in d:
                tp[e] += 1
            else:
                fn[e] += 1
                perdidas[e].append(x["mensaje"])
        for e in x["no"]:
            neg[e] += 1
            if e in d:
                fp[e] += 1
                alarmas[e].append(x["mensaje"])
    print(f"{len(filas)} mensajes nunca vistos por las reglas\n")
    print("| Pedido | Detecta (recall) | Falsas alarmas | Ejemplo perdido |\n|---|---|---|---|")
    T = F = 0
    for e in ETIQUETAS:
        n = tp[e] + fn[e]
        T += tp[e]
        F += n
        rec = f"{tp[e]}/{n} ({tp[e] / n:.0%})" if n else "—"
        fa = f"{fp[e]}/{neg[e]} ({fp[e] / neg[e]:.0%})" if neg[e] else "—"
        ej = (perdidas[e][0][:70] if perdidas[e] else "").replace("|", "/")
        print(f"| {e} | {rec} | {fa} | {ej} |")
    tot_fp, tot_neg = sum(fp.values()), sum(neg.values())
    print(f"\nTotal: detecta {T}/{F} ({T / F:.0%}) · falsas alarmas {tot_fp}/{tot_neg} ({tot_fp / max(1, tot_neg):.0%})")
    if args.json:
        json.dump({"perdidas": perdidas, "alarmas": alarmas}, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generar")
    g.add_argument("--tope", type=float, default=0.10)
    g.add_argument("--conjunto", choices=["prueba", "entrenamiento"], default="prueba")
    e = sub.add_parser("evaluar")
    e.add_argument("--url", default="http://127.0.0.1:18497")
    e.add_argument("--json", default="")
    e.add_argument("--sistema", choices=["reglas", "clf", "ambos"], default="reglas",
                   help="reglas de hoy, solo el clasificador entrenado, o los dos (O lógico, como en el bot)")
    a = ap.parse_args()
    raise SystemExit(generar(a) if a.cmd == "generar" else evaluar(a))


if __name__ == "__main__":
    main()
