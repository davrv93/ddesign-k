"""100 conversaciones en canal web contra el agente (V2 activo, con el LLM de pago), con la traza completa y un log.

    cd agente
    python3 -m app.conversaciones_web generar                                  # pruebas_conv/corpus_web100.jsonl
    python3 -m app.conversaciones_web correr --url http://127.0.0.1:18497 --tope 0.45 --salida pruebas_conv/log_web.jsonl
    python3 -m app.conversaciones_web informe pruebas_conv/log_web.jsonl       # dónde falla + métricas de V2

Reutiliza el runner de `app/conversaciones.py` (etapa, memoria y estado viajan entre turnos como en el bot Go, clienta
simulada por LLM, reglas deterministas) y le añade: las 100 en `canal=web`, la traza `v2` íntegra por turno, un log JSONL
completo, una transcripción legible por conversación y un control de saldo de la clave de OpenRouter (la comparte el bot de
producción: nunca se baja de `--reserva`). Todo va a `pruebas_conv/`, fuera de git (el repo es público).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import statistics
import threading
import time
from collections import Counter, defaultdict

from . import conversaciones as C

AQUI = os.path.dirname(os.path.abspath(__file__))
DIR = C.DIR
CORPUS_WEB = os.path.join(DIR, "corpus_web100.jsonl")
N_SIMULADAS, N_RESERVADAS = 80, 30

# Guiones nuevos (web) con los fallos que ya vimos en capturas: el color que no hay, la talla que no hay, lo ajeno al giro.
NUEVOS = [
    ("X01_rojo_desde_inicio", False, "web", ["hola", "busco un vestido rojo para una cena formal", "es el 8 de octubre", "de noche", "sí"]),
    ("X02_rojo_tras_ver_negro", False, "web", ["hola", "busco un vestido para una cena formal", "es el 8 de octubre", "de noche",
                                               "yo quiero uno rojo", "sí", "me gusta el Pandora", "talla M"]),
    ("X03_bitcoin", False, "web", ["hola", "quiero comprar bitcoin", "y cómo invierto en bolsa de valores?"]),
    ("X04_trabajar_con_ustedes", False, "web", ["hola", "buscan personal? quiero trabajar con ustedes", "y a quién le envío mi cv?"]),
]


def generar() -> list[dict]:
    """100 conversaciones fijas: 16 guiones del runner + 4 nuevos (todos en web) y 80 simuladas, 30 de ellas reservadas."""
    base = C.generar()
    convs: list[dict] = []
    for cid, anuncio, _canal, msgs in list(C.ESCENARIOS) + NUEVOS:
        convs.append({"id": cid, "tipo": "escenario", "canal": "web", "desde_anuncio": anuncio, "mensajes": msgs,
                      "cliente": "Prueba"})
    sims = sorted((c for c in base if c["tipo"] == "simulada"), key=lambda c: c["id"])
    paso = len(sims) / N_SIMULADAS
    elegidas = [sims[int(i * paso)] for i in range(N_SIMULADAS)]
    for c in elegidas:
        convs.append(dict(c, canal="web"))
    ids = sorted(c["id"] for c in convs)
    reservadas = set(sorted(ids, key=lambda i: hashlib.sha1(i.encode()).hexdigest())[:N_RESERVADAS])
    for c in convs:
        c["conjunto"] = "reservada" if c["id"] in reservadas else "dev"
    return convs


def _cargar_corpus() -> list[dict]:
    if not os.path.exists(CORPUS_WEB):
        raise SystemExit("falta el corpus: python3 -m app.conversaciones_web generar")
    return [json.loads(x) for x in open(CORPUS_WEB, encoding="utf-8") if x.strip()]


# ---------------------------------------------------------------------------
# Transcripción legible, con la traza

def _resumen_v2(v2: dict | None) -> list[str]:
    if not v2:
        return []
    out = []
    ets = v2.get("etapas") or []
    if ets:
        out.append("etapas: " + " · ".join(f"{e['etapa']} {e['ms']} ms" + (f" ({e['estado']})" if e.get("estado") not in (None, "ok") else "") for e in ets))
    sb = v2.get("sombra") or {}
    plan = (sb.get("plan") or {}).get("accion")
    out.append(f"v2: modo={v2.get('modo')} enviado={v2.get('enviado')} plan={plan} no_habla={sb.get('no_habla')} motivo_v1={v2.get('motivo_v1')}")
    if v2.get("fuera_de_giro"):
        out.append(f"fuera_de_giro: {v2['fuera_de_giro']}")
    if v2.get("rag"):
        rg = v2["rag"]
        out.append(f"rag: fuente={rg.get('fuente')} rerank={(rg.get('rerank') or {}).get('motivo')} elegido={rg.get('elegido')}")
    for d in v2.get("degradaciones") or []:
        out.append(f"↳ degradación: {d}")
    tm = v2.get("temas") or {}
    if tm.get("evento"):
        out.append(f"temas: {tm['evento'].get('tipo')} {tm['evento'].get('causa', '')}")
    return out


def transcripcion_completa(r: dict) -> str:
    out = [f"# {r['id']} · {r['tipo']} · {r['conjunto']} · canal web" + (" · desde anuncio" if r.get("desde_anuncio") else ""), ""]
    if r.get("persona"):
        out += [f"Persona: {r['persona']}", ""]
    for i, t in enumerate(r["turnos"]):
        out.append(f"## Turno {i + 1}")
        out.append(f"👤 {t['cliente']}")
        out.append(f"   [{t['etapa_antes'] or 'prospeccion'}→{t['etapa']} | intent={t.get('intent')} {t.get('confianza')} | acción={t.get('accion')} | "
                   f"modelo={t.get('modelo')} | {t['ms']} ms | US$ {t.get('costo')}]")
        for p in (t["bot"] or "").split("\n\n"):
            if p.strip():
                out.append("   🤖 " + p.strip().replace("\n", " / "))
        for c in t.get("tarjetas") or []:
            out.append(f"   📷 {c.get('codigo')} {c.get('nombre')} · tallas {c.get('tallas')}")
        if t.get("botones"):
            out.append(f"   botones: {t['botones']}")
        for linea in _resumen_v2(t.get("v2")):
            out.append("   · " + linea)
        mem = t.get("memoria") or {}
        sab = {k: v for k, v in (mem.get("sabemos") or {}).items() if v}
        out.append(f"   memoria: pendiente={mem.get('pendiente') or '—'} sabemos={sab}")
        for cat_, det in t.get("errores_turno", []):
            out.append(f"   ✗ {cat_}: {det}")
        out.append("")
    for i, cat_, det in r.get("errores", []):
        out.append(f"✗ [turno {i + 1}] {cat_}: {det}")
    if r.get("error"):
        out.append(f"error del arnés: {r['error']}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Corrida

def correr(args) -> int:
    clave = C._clave(args.env)
    if not clave:
        raise SystemExit("falta OPENROUTER_API_KEY (variable o --env)")
    convs = _cargar_corpus()
    if args.conjunto != "todas":
        convs = [c for c in convs if c["conjunto"] == args.conjunto]
    if args.ids:
        want = set(args.ids.split(","))
        convs = [c for c in convs if c["id"] in want or any(c["id"].startswith(w) for w in want)]
    if args.max:
        convs = convs[:args.max]
    os.makedirs(os.path.dirname(os.path.abspath(args.salida)), exist_ok=True)
    tdir = os.path.splitext(os.path.abspath(args.salida))[0] + "_transcripciones"
    os.makedirs(tdir, exist_ok=True)
    C.CAT.clear()
    C.CAT.update(C.catalogo(args.catalogo, args.stock))
    json.dump(C.CAT, open(args.salida + ".catalogo.json", "w", encoding="utf-8"), ensure_ascii=False)
    s0 = C.saldo(clave)
    print(f"saldo antes: {s0}; reserva {args.reserva}; tope de esta corrida US$ {args.tope}; {len(convs)} conversaciones")
    cuenta = C.Cuenta(args.tope)
    lock, hechas, parar = threading.Lock(), [], threading.Event()
    ya = set()
    if args.reanudar and os.path.exists(args.salida):
        ya = {json.loads(x)["id"] for x in open(args.salida, encoding="utf-8") if x.strip()}
    pendientes = [c for c in convs if c["id"] not in ya]
    fh = open(args.salida, "a", encoding="utf-8")

    def una(conv):
        if parar.is_set() or cuenta.agotada():
            return None
        restante = (C.saldo(clave).get("limit_remaining") or 0)
        if restante < args.reserva:
            print(f"  ⛔ saldo {restante:.3f} < reserva {args.reserva}: se detiene la corrida")
            parar.set()
            return None
        r = C.correr_una(conv, args.url, clave, args.clienta, cuenta)
        r["errores"] = C.reglas_conversacion(conv, r)
        r["costo_conversacion"] = round(sum(float(t.get("costo") or 0) for t in r["turnos"]), 5)
        with lock:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            hechas.append(r)
            with open(os.path.join(tdir, f"{r['id']}.md"), "w", encoding="utf-8") as f:
                f.write(transcripcion_completa(r))
            n = len(hechas)
            print(f"  [{n}/{len(pendientes)}] {r['id']}: {len(r['turnos'])} turnos, {len(r['errores'])} errores, US$ {r['costo_conversacion']:.4f} "
                  f"(agente {cuenta.partes['agente']:.3f} + clienta {cuenta.partes['clienta']:.3f})")
        return r

    t0 = time.time()
    with cf.ThreadPoolExecutor(args.hilos) as ex:
        list(ex.map(una, pendientes))
    fh.close()
    s1 = C.saldo(clave)
    print(f"listo en {int(time.time() - t0)} s: {len(hechas)} conversaciones; gasto medido US$ {cuenta.total:.4f}; saldo después: {s1}")
    return 0


# ---------------------------------------------------------------------------
# Informe: dónde falla + métricas de V2

def _pct(xs, p):
    xs = sorted(xs)
    return xs[int(p * (len(xs) - 1))] if xs else 0


def informe(args) -> int:
    res = [json.loads(x) for x in open(args.ruta, encoding="utf-8") if x.strip()]
    cat_ruta = args.ruta + ".catalogo.json"
    if os.path.exists(cat_ruta):
        C.CAT.clear()
        C.CAT.update(json.load(open(cat_ruta, encoding="utf-8")))
    convs = {c["id"]: c for c in _cargar_corpus()}
    for r in res:      # las reglas de hoy, igual para antes y después
        r["errores"] = C.reglas_conversacion(convs.get(r["id"], r), r)
    out = []
    turnos = [t for r in res for t in r["turnos"]]
    por_etapa, ms_v2 = defaultdict(list), []
    habla, motivos, degr, planes = Counter(), Counter(), Counter(), Counter()
    for t in turnos:
        v2 = t.get("v2") or {}
        for e in v2.get("etapas") or []:
            por_etapa[e["etapa"]].append(e["ms"])
        if v2.get("ms") is not None:
            ms_v2.append(v2["ms"])
        if v2:
            habla[v2.get("enviado")] += 1
            if v2.get("enviado") != "v2":
                motivos[(v2.get("motivo_v1") or (v2.get("sombra") or {}).get("no_habla") or "—")[:70]] += 1
            planes[((v2.get("sombra") or {}).get("plan") or {}).get("accion")] += 1
        for d in v2.get("degradaciones") or []:
            degr[d.split(":")[0][:40]] += 1
    cats = defaultdict(list)
    for r in res:
        for i, c, d in r["errores"]:
            cats[c].append((r["id"], i, d))
    sin_error = sum(1 for r in res if not [e for e in r["errores"] if e[1] != "error_arnes"])
    costo = sum(r.get("costo_conversacion", 0) for r in res)
    lat = [t["ms"] for t in turnos if t.get("accion") != "go_confirmar"]
    out += [f"# Informe de {os.path.basename(args.ruta)}", "",
            f"- Conversaciones: {len(res)} · turnos: {len(turnos)} · sin ningún error: {sin_error} ({sin_error / max(1, len(res)):.0%})",
            f"- Latencia por turno: p50 {_pct(lat, .5) / 1000:.2f} s · p90 {_pct(lat, .9) / 1000:.2f} s · máx {max(lat or [0]) / 1000:.2f} s",
            f"- Costo del agente: US$ {costo:.4f} ({costo / max(1, len(res)):.4f} por conversación)", "",
            "## Dónde falla", "", "| Categoría | Conversaciones | Ocurrencias | Ejemplo |", "|---|---|---|---|"]
    orden = sorted(cats, key=lambda c: C.GRAVEDAD.index(c) if c in C.GRAVEDAD else 99)
    for c in orden:
        ids = {x[0] for x in cats[c]}
        ej = cats[c][0]
        out.append(f"| {c} | {len(ids)} | {len(cats[c])} | {ej[0]} t{ej[1] + 1}: {ej[2][:90]} |")
    if not orden:
        out.append("| (ninguna) | | | |")
    out += ["", "## V2", "", f"- Quién habló: {dict(habla)}", f"- Plan de V2: {dict(planes)}",
            "- Por qué V2 no habló (top 8): " + "; ".join(f"{k} ×{v}" for k, v in motivos.most_common(8)),
            "- Degradaciones: " + ("; ".join(f"{k} ×{v}" for k, v in degr.most_common()) or "ninguna"),
            f"- Capa V2 por turno: p50 {_pct(ms_v2, .5)} ms · p90 {_pct(ms_v2, .9)} ms", "", "| Etapa | n | p50 ms | p90 ms |", "|---|---|---|---|"]
    for e, xs in sorted(por_etapa.items(), key=lambda kv: -statistics.mean(kv[1])):
        out.append(f"| {e} | {len(xs)} | {_pct(xs, .5)} | {_pct(xs, .9)} |")
    out += ["", "## Detalle por categoría", ""]
    por_id = {r["id"]: r for r in res}
    for c in orden:
        out.append(f"### {c}")
        for cid, i, d in cats[c][:args.ejemplos]:
            out += [f"- {cid} t{i + 1}: {d}", "```", C.transcripcion(por_id[cid], i if i >= 0 else None), "```"]
        out.append("")
    txt = "\n".join(out)
    if args.salida:
        open(args.salida, "w", encoding="utf-8").write(txt)
    print(txt)
    return 0


def ab(args) -> int:
    """V1 contra V2 sobre LAS MISMAS conversaciones: se reproducen los mensajes que escribió la clienta en una corrida anterior
    (`--origen`) contra dos agentes (`--url-v1`, `--url-v2`). Sin clienta simulada ni LLM de pago: costo 0."""
    os.environ["CONV_SIN_LLM"] = "1"
    C.CAT.clear()
    C.CAT.update(C.catalogo(args.catalogo, args.stock))
    origen = [json.loads(x) for x in open(args.origen, encoding="utf-8") if x.strip()]
    corpus = {c["id"]: c for c in _cargar_corpus()}
    res = {"v1": [], "v2": []}
    for r in origen:
        msgs = []
        for t in r["turnos"]:
            txt = t["cliente"]
            if t.get("foto"):
                msgs.append({"foto": t["foto"], "texto": txt.replace("[FOTO] ", "", 1)})
            else:
                msgs.append(txt)
        base = corpus.get(r["id"], {})
        conv = {"id": r["id"], "tipo": r["tipo"], "conjunto": r["conjunto"], "canal": "web", "desde_anuncio": r.get("desde_anuncio"),
                "mensajes": msgs, "cliente": base.get("cliente", "Prueba"), "persona": r.get("persona", ""),
                "persona_clave": r.get("persona_clave", r["id"])}
        for v, url in (("v1", args.url_v1), ("v2", args.url_v2)):
            x = C.correr_una(dict(conv, id=conv["id"] + ("" if v == "v2" else "")), url, "", "", C.Cuenta(1.0))
            x["errores"] = C.reglas_conversacion(conv, x)
            res[v].append(x)
    out = ["# V1 contra V2 activo, mismos mensajes de la clienta", "",
           f"Conversaciones: {len(origen)} (se reproducen los mensajes de {os.path.basename(args.origen)}; las respuestas del bot cambian solas)", ""]
    def cuenta(rs):
        c = Counter()
        sin = 0
        for x in rs:
            cats = {cc for _, cc, _ in x["errores"] if cc != "error_arnes"}
            sin += not cats
            for cc in cats:
                c[cc] += 1
        return sin, c
    s1, c1 = cuenta(res["v1"]); s2, c2 = cuenta(res["v2"])
    n = len(origen)
    out += [f"- Sin ningún error: V1 {s1}/{n} · V2 activo {s2}/{n}", "", "| Categoría | V1 (conversaciones) | V2 activo |", "|---|---|---|"]
    for cc in sorted(set(c1) | set(c2), key=lambda c: C.GRAVEDAD.index(c) if c in C.GRAVEDAD else 99):
        out.append(f"| {cc} | {c1.get(cc, 0)} | {c2.get(cc, 0)} |")
    turnos = habla = igual = 0
    lat = {"v1": [], "v2": []}
    difs = []
    for a_, b_ in zip(res["v1"], res["v2"]):
        for i, (t1, t2) in enumerate(zip(a_["turnos"], b_["turnos"])):
            turnos += 1
            habla += (t2.get("v2") or {}).get("enviado") == "v2"
            igual += (t1["bot"] or "").strip() == (t2["bot"] or "").strip()
            lat["v1"].append(t1["ms"]); lat["v2"].append(t2["ms"])
            if (t1["bot"] or "").strip() != (t2["bot"] or "").strip() and (t2.get("v2") or {}).get("enviado") == "v2":
                difs.append((a_["id"], i + 1, t1["cliente"], t1["bot"], t2["bot"]))
    out += ["", f"- Turnos comparados: {turnos}; V2 habló en {habla} ({habla / max(1, turnos):.0%}); respuestas idénticas: {igual} ({igual / max(1, turnos):.0%})",
            f"- Latencia p50/p90: V1 {_pct(lat['v1'], .5)}/{_pct(lat['v1'], .9)} ms · V2 {_pct(lat['v2'], .5)}/{_pct(lat['v2'], .9)} ms", "",
            "## Turnos en los que V2 habló y dijo algo distinto a V1 (primeros 25)", ""]
    for cid, i, cl, b1, b2 in difs[:25]:
        out += [f"**{cid} t{i}** 👤 {cl[:120]}", f"- V1: {(b1 or '').replace(chr(10), ' / ')[:230]}", f"- V2: {(b2 or '').replace(chr(10), ' / ')[:230]}", ""]
    txt = "\n".join(out)
    open(args.salida, "w", encoding="utf-8").write(txt)
    for v in ("v1", "v2"):
        with open(os.path.splitext(args.salida)[0] + f"_{v}.jsonl", "w", encoding="utf-8") as f:
            for x in res[v]:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
    print(txt)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generar")
    c = sub.add_parser("correr")
    c.add_argument("--url", default="http://127.0.0.1:18497")
    c.add_argument("--conjunto", choices=("dev", "reservada", "todas"), default="todas")
    c.add_argument("--ids", default="")
    c.add_argument("--max", type=int, default=0)
    c.add_argument("--salida", required=True)
    c.add_argument("--hilos", type=int, default=3)
    c.add_argument("--tope", type=float, default=0.45, help="US$ máximos de esta corrida (agente + clienta)")
    c.add_argument("--reserva", type=float, default=0.25, help="saldo mínimo de la clave que no se toca (la usa el bot de producción)")
    c.add_argument("--clienta", default="deepseek/deepseek-v4-flash")
    c.add_argument("--reanudar", action="store_true")
    c.add_argument("--env", default=os.path.join(AQUI, "..", "..", ".env"))
    c.add_argument("--catalogo", default="https://proyectopostventa.site/baruka/api/public/catalog")
    c.add_argument("--stock", default="https://proyectopostventa.site/baruka/api/public/stock")
    b = sub.add_parser("ab")
    b.add_argument("--origen", required=True)
    b.add_argument("--url-v1", default="http://127.0.0.1:18498")
    b.add_argument("--url-v2", default="http://127.0.0.1:18497")
    b.add_argument("--salida", required=True)
    b.add_argument("--catalogo", default="https://proyectopostventa.site/baruka/api/public/catalog")
    b.add_argument("--stock", default="https://proyectopostventa.site/baruka/api/public/stock")
    i = sub.add_parser("informe")
    i.add_argument("ruta")
    i.add_argument("--salida", default="")
    i.add_argument("--ejemplos", type=int, default=3)
    a = ap.parse_args(argv)
    if a.cmd == "generar":
        convs = generar()
        os.makedirs(DIR, exist_ok=True)
        with open(CORPUS_WEB, "w", encoding="utf-8") as f:
            for c_ in convs:
                f.write(json.dumps(c_, ensure_ascii=False) + "\n")
        print(f"{len(convs)} conversaciones → {CORPUS_WEB} ({Counter(c_['tipo'] for c_ in convs)}, "
              f"{sum(c_['conjunto'] == 'reservada' for c_ in convs)} reservadas)")
        return 0
    return correr(a) if a.cmd == "correr" else ab(a) if a.cmd == "ab" else informe(a)


if __name__ == "__main__":
    raise SystemExit(main())
