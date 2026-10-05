"""Evaluación por turno: mismo contexto, varios modelos, una rúbrica fija.

Sobre los turnos de las conversaciones **reservadas** del oro (`datos/mlx/test.jsonl`, que no se entrenan), cada modelo
recibe exactamente los mismos mensajes (system + contexto del agente) y se guarda lo que contesta y cuánto tarda.

    python3 finetune/evaluar.py generar --modelo afinado=http://127.0.0.1:18490 --modelo base=http://127.0.0.1:18492 \
        --modelo qwen3b=ollama:qwen2.5:3b
    python3 finetune/evaluar.py puntuar        # datos/eval/puntos.json y la tabla por criterio
    python3 finetune/evaluar.py muestra --n 40 # turnos lado a lado para el juicio manual

RÚBRICA (fijada el 04-10-2026 ANTES de ver ninguna salida; no se cambia después de mirar resultados). Por respuesta:

1. `json`      — ¿JSON válido con `responde` no vacío? (`estructurado.leer`). Si el turno pedía texto libre, ¿hay texto?
2. `contesta`  — Si la clienta preguntó algo concreto (precio, tela, talla, envío, dirección, horario, pago, descuento,
                 cambios), ¿la respuesta trae el dato (o lo deriva a la asesora *4* cuando el dato no está)? Reglas por
                 tema; lo que las reglas no saben juzgar queda «sin juzgar» y lo decide el juez manual en la muestra.
3. `inventa`   — ¿Afirma algo fuera de la ficha o de la tienda? Precio que no está en el contexto, talla XL/XS/XXL como
                 disponible, prenda (código o nombre) que no aparece en el contexto, medios de pago concretos (Yape,
                 Plin, BCP, número de cuenta), «envío gratis», descuentos o promociones afirmados, escasez («quedan
                 pocos», «se agota»).
4. `pregunta`  — Si el código trae la pregunta (FORMATO: «El código cierra tu mensaje…»), ¿el modelo NO pregunta nada
                 (ni en `responde`/`por_que` ni en `pregunta`, salvo que repita la misma)? Si no la trae, ¿hace como
                 mucho una?
5. `repite`    — ¿Pregunta algo que ya está en LO QUE YA SABEMOS, o copia una frase entera suya del HISTORIAL?
6. `metodo`    — Con PRODUCTO «(ninguna relevante)», ¿no nombra ni promete prendas? Con «OFRECES UNA SOLA OPCIÓN», ¿no
                 nombra otra prenda? ¿No da la venta por hecha ni pide confirmar el pedido fuera del cierre? ¿No da datos
                 de pago fuera de la venta confirmada?
7. `tono`      — Breve y cálido: `responde` ≤ 2 frases y ≤ 280 caracteres, `por_que` ≤ 1 frase; ≤ 1 emoji en total; no
                 saluda si YA ESTÁN CONVERSANDO; no filtra el prompt (SIGUIENTE PREGUNTA, FICHAS, AHORA:, PRODUCTO…); en
                 español.

Una respuesta «pasa» si cumple los siete. Las tasas se dan por criterio y en total. La latencia es la de generación
en el Mac (temperatura 0, sin forzar JSON: el modelo pequeño no tiene modo JSON y se mide si lo da solo).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, ".."))
from app import estructurado, memoria  # noqa: E402

DATOS = os.path.join(AQUI, "datos")
EVAL = os.path.join(DATOS, "medicion")
CRITERIOS = ("json", "contesta", "inventa", "pregunta", "repite", "metodo", "tono")


def _post(url: str, cuerpo: dict, timeout: float = 300) -> dict:
    r = urllib.request.Request(url, json.dumps(cuerpo).encode(), {"content-type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=timeout))


def llamar(destino: str, mensajes: list[dict]) -> tuple[str, float]:
    t0 = time.time()
    if destino.startswith("ollama:"):
        js = _post("http://127.0.0.1:11434/api/chat", {"model": destino[7:], "messages": mensajes, "stream": False,
                                                        "options": {"temperature": 0, "num_predict": 350, "num_ctx": 8192}})
        return js["message"]["content"], time.time() - t0
    js = _post(destino.rstrip("/") + "/v1/chat/completions", {"messages": mensajes, "temperature": 0.0, "max_tokens": 350})
    return js["choices"][0]["message"]["content"], time.time() - t0


def generar(modelos: list[str], test: str, salida: str, limite: int = 0) -> None:
    filas = [json.loads(x) for x in open(test, encoding="utf-8") if x.strip()]
    if limite:
        filas = filas[:limite]
    os.makedirs(EVAL, exist_ok=True)
    for m in modelos:
        nombre, destino = m.split("=", 1)
        ruta = os.path.join(EVAL, f"{nombre}.jsonl")
        hechos = sum(1 for _ in open(ruta, encoding="utf-8")) if os.path.exists(ruta) else 0
        with open(ruta, "a", encoding="utf-8") as fh:
            for i, f in enumerate(filas[hechos:], start=hechos):
                ctx = f["messages"][:-1]
                try:
                    txt, s = llamar(destino, ctx)
                except Exception as e:  # noqa: BLE001
                    txt, s = f"__ERROR__ {e}", 0.0
                fh.write(json.dumps({"i": i, "texto": txt, "s": round(s, 3)}, ensure_ascii=False) + "\n")
                fh.flush()
                print(f"{nombre} {i + 1}/{len(filas)} {s:.2f}s", flush=True)


# ---------------------------------------------------------------------------
# Rúbrica automática

TEMAS = [  # (tema, cómo se pregunta, qué debe traer la respuesta para contar como contestada)
    ("precio", r"cu[aá]nto (cuesta|est[aá]|sale|vale|questa)|precio|cuanto es\b|\bcuanto\?", r"s/\s*\*?\d|\d{3}\s*soles|asesora|\*4\*"),
    ("tela", r"\btela|material|de qu[eé] es\b|forro|tejido", r"tela|gasa|podesu|prada|seda|denim|jackard|catania|organza|lino|forro|asesora|\*4\*|no (lo )?(tengo|figura|dice)"),
    ("talla", r"\btalla|\bxl\b|\bxxl\b|\ben (s|m|l)\b|me quedar", r"\btalla|\b(s|m|l)\b|asesora|\*4\*"),
    ("envio", r"env[ií]o|delivery|mandan|env[ií]an|provincia|olva|shalom|demora", r"s/\s*\*?(15|20)|15|20|olva|shalom|asesora|\*4\*"),
    ("ubicacion", r"d[oó]nde (queda|est[aá]|son|ubic)|direcci[oó]n|showroom|tienda f[ií]sica|en persona|ir a ver", r"juan ayll[oó]n|santa anita|showroom"),
    ("horario", r"horario|a qu[eé] hora|abren|atienden|domingo", r"\b9\b|9:00|7:00|\b7\b|19|lunes a domingo|cita"),
    ("pago", r"c[oó]mo (pago|se paga|pagar)|yape|plin|contraentrega|contra entrega|transferencia|tarjeta|datos (de|para) (pago|pagar)", r"asesora|\*4\*|comprobante|voucher|datos|pago|al confirmar"),
    ("descuento", r"descuento|rebaja|promo|m[aá]s barato|dejas en|d[eé]jamelo|llevando dos", r"asesora|\*4\*|precio"),
    ("cambios", r"cambi(ar|o)|devoluci|devolver|no me queda", r"falla|7 d[ií]as|cambio|devoluci|asesora|\*4\*"),
]
RE_PAGO_CONCRETO = re.compile(r"\byape\b|\bplin\b|\bbcp\b|interbank|bbva|n[uú]mero de cuenta|\bcci\b|\b9\d{8}\b", re.I)
RE_PROMESA = re.compile(r"env[ií]o gratis|gratis|te (hago|doy|dejo) (un )?descuento|tenemos (descuento|promo)|precio especial|"
                        r"quedan? pocos|se agota|muy pedido|no suele durar|[uú]ltimas? unidades", re.I)
RE_TALLA_RARA = re.compile(r"(tenemos|hay|disponible|queda)[^.?!\n]{0,40}\b(xxl|xl|xs)\b", re.I)
RE_FUGA = re.compile(r"SIGUIENTE PREGUNTA|LO QUE YA SABEMOS|ESTÁS ESPERANDO|FICHAS|AHORA:|PRODUCTO DEL QUE|ETAPA ACTUAL|FORMATO DE SALIDA|"
                     r"\bresponde\b\s*:|\bpor_que\b", re.I)
RE_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐❤]")
RE_INGLES = re.compile(r"\b(the|and|you|your|we have|dress|price|size)\b", re.I)
RE_COD = re.compile(r"\bV\d{2}\b")
RE_SALUDO = re.compile(r"^\s*(¡?hola|buen[oa]s (d[ií]as|tardes|noches))", re.I)


def _plano(s: str) -> str:
    return memoria._plano(s)


def _bloque(user: str, titulo: str, hasta: str) -> str:
    m = re.search(re.escape(titulo) + r"(.*?)" + hasta, user, re.S)
    return m.group(1) if m else ""


def contexto(msgs: list[dict]) -> dict:
    u = msgs[-1]["content"]
    sabemos = _bloque(u, "LO QUE YA SABEMOS DE ELLA (no lo vuelvas a preguntar): ", r"\n")
    producto = _bloque(u, "PRODUCTO (fichas; la primera es de la que se habla):\n", r"\n\n")
    hist = _bloque(u, "HISTORIAL:\n", r"\n\nPRODUCTO")
    msg = _bloque(u, "MENSAJE NUEVO DE LA CLIENTA:\n", r"(\n\nFORMATO DE SALIDA|$)") or _bloque(u, "MENSAJE NUEVO DEL CLIENTE:\n", r"$")
    q = re.search(r"El código cierra tu mensaje con esta pregunta: «(.*?)»", u)
    nombres = set()
    for linea in producto.split("\n"):
        m = re.match(r"- (V\d{2}) · ([^|]+)", linea)
        if m:
            nombres.add(m.group(1))
            nombres.add(m.group(2).strip())
    una = re.search(r"OFRECES UNA SOLA OPCIÓN: (V\d{2}) ([^.]+)\.", u)
    return {"user": u, "sabemos": sabemos, "producto": producto, "hist": hist, "mensaje": msg.strip(),
            "q_codigo": q.group(1) if q else "", "permite_q": "Si hace falta UNA pregunta" in u, "json": "FORMATO DE SALIDA" in u,
            "sin_producto": "(ninguna relevante)" in producto, "una": una.group(1) if una else "",
            "etapa": _bloque(u, "ETAPA ACTUAL: ", r"\n").strip(), "ya_hablaron": "YA ESTÁN CONVERSANDO" in u,
            "codigos_ctx": set(RE_COD.findall(u)), "nombres_ctx": nombres,
            "precios_ctx": {int(round(float(x.replace(",", ".")))) for x in re.findall(r"S/\s*(\d{2,4}(?:[.,]\d{2})?)", u)}}


TODOS_LOS_NOMBRES: set[str] = set()


def _nombres_catalogo() -> set[str]:
    if not TODOS_LOS_NOMBRES:
        try:
            cat = json.load(open(os.path.join(DATOS, "catalogo.json"), encoding="utf-8"))
            for p in cat["products"]:
                TODOS_LOS_NOMBRES.add(_plano(p["name"]).split()[-1])
        except (OSError, ValueError, KeyError):
            pass
    return TODOS_LOS_NOMBRES


def puntuar_una(msgs: list[dict], texto: str) -> dict:
    c = contexto(msgs)
    js = estructurado.leer(texto)
    r = {k: True for k in CRITERIOS}
    det = {}
    # 1. json
    if c["json"]:
        r["json"] = bool(js and js["responde"])
    else:
        r["json"] = bool(texto.strip()) and not texto.strip().startswith("{")
    cuerpo = (js["responde"] + " " + js["por_que"]) if js else texto
    todo = (cuerpo + " " + (js["pregunta"] if js else "")).strip()
    p = _plano(todo)
    # 2. contesta
    pm = _plano(c["mensaje"])
    temas = [t for t, rx_q, _ in TEMAS if re.search(rx_q, pm)]
    if temas:
        falta = [t for t, _, rx_a in TEMAS if t in temas and not re.search(rx_a, p)]
        r["contesta"] = not falta
        if falta:
            det["contesta"] = "no trae: " + ", ".join(falta)
    else:
        r["contesta"] = None   # sin juzgar por reglas
    # 3. inventa
    malos = []
    for m in re.finditer(r"s/\.?\s*\*?\s*(\d{2,4}(?:[.,]\d{2})?)", p):
        v = int(round(float(m.group(1).replace(",", "."))))
        if v not in c["precios_ctx"] and v not in (15, 20) and not any(v == a + b for a in c["precios_ctx"] for b in (15, 20)):
            malos.append(f"precio {v}")
    for cod in set(RE_COD.findall(todo)) - c["codigos_ctx"]:
        malos.append(f"código {cod}")
    ctx_plano = _plano(c["user"])
    for n in _nombres_catalogo():
        if re.search(rf"\b{n}\b", p) and not re.search(rf"\b{n}\b", ctx_plano):
            malos.append(f"prenda {n}")
    if RE_PAGO_CONCRETO.search(todo) and not RE_PAGO_CONCRETO.search(c["user"]):
        malos.append("medio de pago")
    if RE_PROMESA.search(todo):
        malos.append("promesa/escasez: " + RE_PROMESA.search(todo).group(0))
    if RE_TALLA_RARA.search(todo) and not re.search(r"\bno (tenemos|hay|manejamos)", p):
        malos.append("talla inexistente")
    r["inventa"] = not malos
    if malos:
        det["inventa"] = ", ".join(malos)
    # 4. pregunta
    preguntas = memoria.preguntas_en(cuerpo) + ([js["pregunta"]] if js and js["pregunta"].strip() else [])
    if c["q_codigo"]:
        extra = [q for q in preguntas if _plano(q).strip(" ¿?") != _plano(c["q_codigo"]).strip(" ¿?")]
        r["pregunta"] = not extra
        if extra:
            det["pregunta"] = "pregunta de más: " + extra[0][:80]
    else:
        r["pregunta"] = len(preguntas) <= 1
        if not r["pregunta"]:
            det["pregunta"] = f"{len(preguntas)} preguntas"
    # 5. repite
    sab = _plano(c["sabemos"])
    rep = []
    for q in preguntas:
        k = memoria.clave_de(q)
        dato = memoria.DATO_DE.get(k)
        nombre = {"ocasion": "ocasion", "horario": "dia/noche", "fecha": "para cuando", "talla": "talla", "estatura": "estatura",
                  "color": "color", "envio": "envio"}.get(dato or "", "")
        if nombre and nombre + ":" in sab:
            rep.append(f"pregunta {k} ya sabido")
    lineas_bot = [_plano(x.split(":", 1)[1]) for x in c["hist"].split("\n") if x.startswith("bot:") and len(x) > 40]
    for frase in re.split(r"(?<=[.!?])\s+", cuerpo):
        fp = _plano(frase)
        if len(fp) > 35 and any(fp in lb for lb in lineas_bot):
            rep.append("copia: " + frase[:60])
    r["repite"] = not rep
    if rep:
        det["repite"] = "; ".join(rep)
    # 6. método
    mal = []
    if c["sin_producto"] and (RE_COD.search(todo) or any(re.search(rf"\b{n}\b", p) for n in _nombres_catalogo())
                              or re.search(r"te (paso|env[ií]o|mando) (la|las) fotos?", p)):
        mal.append("muestra prenda sin indagar")
    if c["una"]:
        otros = set(RE_COD.findall(todo)) - {c["una"]}
        if otros:
            mal.append(f"otra prenda {otros}")
    if c["etapa"] in ("PROSPECCIÓN", "SEGUIMIENTO") and re.search(r"confirmamos (tu|el) pedido|ya (est[aá]|qued[oó]) (tu )?pedido|"
                                                                  r"tu pedido (est[aá]|qued[oó]) confirmado", p):
        mal.append("da la venta por hecha")
    if c["etapa"] != "VENTA CONFIRMADA" and RE_PAGO_CONCRETO.search(todo):
        mal.append("pago antes de confirmar")
    r["metodo"] = not mal
    if mal:
        det["metodo"] = ", ".join(mal)
    # 7. tono
    tmal = []
    if js:
        if len(estructurado._frases(js["responde"])) > 2 or len(js["responde"]) > 280:
            tmal.append("responde largo")
        if len(estructurado._frases(js["por_que"])) > 1 or len(js["por_que"]) > 200:
            tmal.append("por_que largo")
    elif len(texto) > 450:
        tmal.append("largo")
    if len(RE_EMOJI.findall(todo)) > 1:
        tmal.append("emojis")
    if c["ya_hablaron"] and RE_SALUDO.search(cuerpo):
        tmal.append("vuelve a saludar")
    if RE_FUGA.search(cuerpo):
        tmal.append("filtra el prompt")
    if len(RE_INGLES.findall(todo)) >= 2:
        tmal.append("inglés")
    r["tono"] = not tmal
    if tmal:
        det["tono"] = ", ".join(tmal)
    r["pasa"] = all(v is not False for v in r.values())
    return {"r": r, "det": det}


def puntuar(nombres: list[str], test: str) -> dict:
    filas = [json.loads(x) for x in open(test, encoding="utf-8") if x.strip()]
    out = {}
    for n in nombres:
        ruta = os.path.join(EVAL, f"{n}.jsonl")
        if not os.path.exists(ruta):
            continue
        res = [json.loads(x) for x in open(ruta, encoding="utf-8") if x.strip()]
        punt = [puntuar_una(filas[x["i"]]["messages"][:-1], x["texto"]) | {"i": x["i"], "s": x["s"]} for x in res]
        tasas = {}
        for k in CRITERIOS + ("pasa",):
            vals = [p["r"][k] for p in punt if p["r"].get(k) is not None]
            tasas[k] = (sum(vals), len(vals))
        lat = sorted(p["s"] for p in punt if p["s"])
        out[n] = {"tasas": tasas, "n": len(punt), "lat_media": round(sum(lat) / max(1, len(lat)), 2),
                  "lat_p90": round(lat[int(0.9 * (len(lat) - 1))], 2) if lat else 0, "detalle": punt}
    # el oro, con la misma rúbrica (sanidad: debería pasar casi todo)
    oro = [puntuar_una(f["messages"][:-1], f["messages"][-1]["content"]) for f in filas]
    out["oro"] = {"tasas": {k: (sum(1 for p in oro if p["r"].get(k)), sum(1 for p in oro if p["r"].get(k) is not None))
                            for k in CRITERIOS + ("pasa",)}, "n": len(oro), "detalle": oro}
    return out


def tabla(out: dict) -> str:
    cols = list(out)
    lin = ["| Criterio | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for k in CRITERIOS + ("pasa",):
        celdas = []
        for n in cols:
            a, b = out[n]["tasas"][k]
            celdas.append(f"{100 * a / b:.0f} % ({a}/{b})" if b else "—")
        lin.append(f"| {k} | " + " | ".join(celdas) + " |")
    lin.append("| latencia media / p90 (s) | " + " | ".join(f"{out[n].get('lat_media', '—')} / {out[n].get('lat_p90', '—')}" for n in cols) + " |")
    return "\n".join(lin)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generar")
    g.add_argument("--modelo", action="append", required=True, help="nombre=url de mlx_lm.server | nombre=ollama:modelo")
    g.add_argument("--test", default=os.path.join(DATOS, "mlx", "test.jsonl"))
    g.add_argument("--limite", type=int, default=0)
    p = sub.add_parser("puntuar")
    p.add_argument("--modelos", default="afinado,base,qwen3b")
    p.add_argument("--test", default=os.path.join(DATOS, "mlx", "test.jsonl"))
    m = sub.add_parser("muestra")
    m.add_argument("--modelos", default="afinado,base,qwen3b")
    m.add_argument("--test", default=os.path.join(DATOS, "mlx", "test.jsonl"))
    m.add_argument("--n", type=int, default=40)
    m.add_argument("--desde", type=int, default=0)
    a = ap.parse_args(argv)
    if a.cmd == "generar":
        generar(a.modelo, a.test, EVAL, a.limite)
    elif a.cmd == "puntuar":
        out = puntuar(a.modelos.split(","), a.test)
        with open(os.path.join(EVAL, "puntos.json"), "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
        print(tabla(out))
    else:
        filas = [json.loads(x) for x in open(a.test, encoding="utf-8") if x.strip()]
        res = {n: {json.loads(x)["i"]: json.loads(x)["texto"] for x in open(os.path.join(EVAL, f"{n}.jsonl"), encoding="utf-8")}
               for n in a.modelos.split(",") if os.path.exists(os.path.join(EVAL, f"{n}.jsonl"))}
        paso = max(1, len(filas) // a.n)
        for i in list(range(a.desde, len(filas), paso))[:a.n]:
            c = contexto(filas[i]["messages"][:-1])
            print(f"### turno {i} · {c['etapa']} · sabemos: {c['sabemos'][:120]}")
            print(f"    código pregunta: «{c['q_codigo']}» · una: {c['una'] or '-'} · sin producto: {c['sin_producto']}")
            print(f"    👤 {c['mensaje'][:200]}")
            print(f"    ORO: {filas[i]['messages'][-1]['content'][:300]}")
            for n, r in res.items():
                print(f"    {n}: {r.get(i, '')[:300]!s}".replace("\n", " "))
            print()


if __name__ == "__main__":
    main()
