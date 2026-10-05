"""Puente local entre el agente (contenedor) y quien redacta: sin OpenRouter, sin DeepSeek, sin Jev.

El agente llama a su LLM por la ruta del motor `deepseek` (`DEEPSEEK_URL`). Este servidor se pone en medio y sirve
tres cosas, solo con la biblioteca estándar:

- `POST /oro/v1/chat/completions` — **captura** (para escribir el oro): guarda el prompt que el agente armó y contesta
  con lo que haya escrito la redactora para ese prompt. Si todavía no hay nada, contesta un marcador y deja el prompt
  en `pendientes/` para que `oro.py` se lo enseñe. La clave es el hash de los mensajes (y, de respaldo, el del
  historial + mensaje nuevo, por si cambia algo menor entre la captura y la respuesta).
- `POST /up/<nombre>/v1/chat/completions` — **proxy** a un `mlx_lm.server` (`--upstream nombre=url`). Las llamadas a un
  mismo servidor se ponen en fila (un candado) y se mide solo el tiempo de generación, sin la espera en la fila. Cada
  llamada queda en `llamadas_<nombre>.jsonl` (prompt, respuesta, ms).
- `GET /api/public/catalog` y `/api/public/stock?codes=` — el catálogo y el stock de una **foto fija** de producción
  (`datos/catalogo.json`, `datos/stock.json`), para no pedirle nada a producción en cada turno.

    python3 finetune/puente.py --puerto 18493 --upstream afinado=http://127.0.0.1:18490 --upstream base=http://127.0.0.1:18492
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
DATOS = os.path.join(AQUI, "datos")

UPSTREAM: dict[str, str] = {}
CANDADOS: dict[str, threading.Lock] = {}
LOG_LOCK = threading.Lock()


def clave(mensajes: list[dict]) -> str:
    return hashlib.sha1(json.dumps(mensajes, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


RE_HIST = re.compile(r"HISTORIAL:\n(.*?)\n\n(?:PRODUCTO|FICHAS)", re.S)
RE_MSG = re.compile(r"MENSAJE NUEVO DE[L LA]* CLIENT[AE]:\n(.*?)(?:\n\nFORMATO DE SALIDA|$)", re.S)


def clave_suave(mensajes: list[dict]) -> str:
    u = mensajes[-1].get("content", "") if mensajes else ""
    h = RE_HIST.search(u)
    m = RE_MSG.search(u)
    # Con el nombre de la clienta: sin él, dos conversaciones que empiezan con «hola» compartían la clave y la segunda
    # recibía el saludo escrito para la primera («¡Hola, Diana!» a otra clienta), sin pasar por quien redacta.
    cli = re.search(r"^CLIENTE: .*$", u, re.M)
    base = (cli.group(0) if cli else "") + "\n" + (h.group(1) if h else "") + "\n##\n" + (m.group(1) if m else u[-400:])
    return hashlib.sha1(base.encode()).hexdigest()[:16]


def _dir(*p: str) -> str:
    d = os.path.join(DATOS, "puente", *p)
    os.makedirs(d, exist_ok=True)
    return d


def respuesta_oro(cuerpo: dict) -> str:
    msgs = cuerpo.get("messages") or []
    k, ks = clave(msgs), clave_suave(msgs)
    for kk in (k, ks):
        ruta = os.path.join(_dir("respuestas"), kk + ".json")
        if os.path.exists(ruta):
            with open(ruta, encoding="utf-8") as fh:
                return json.load(fh)["texto"]
    with open(os.path.join(_dir("pendientes"), k + ".json"), "w", encoding="utf-8") as fh:
        json.dump({"clave": k, "clave_suave": ks, "messages": msgs, "json": bool(cuerpo.get("response_format")),
                   "t": time.time()}, fh, ensure_ascii=False)
    # El marcador lleva la clave: oro.py la saca de la respuesta del agente para saber qué prompt enseñar.
    return ('{"responde": "PENDIENTE-%s", "por_que": "", "pregunta": ""}' % k) if cuerpo.get("response_format") else f"PENDIENTE-{k}"


def proxy(nombre: str, cuerpo: dict) -> tuple[dict, float]:
    url = UPSTREAM[nombre].rstrip("/") + "/v1/chat/completions"
    cuerpo = dict(cuerpo)
    cuerpo.pop("model", None)              # mlx_lm.server usa el modelo con que arrancó
    cuerpo.pop("response_format", None)    # el modelo pequeño no tiene modo JSON forzado: se mide si lo da solo
    cuerpo["temperature"] = 0.0            # misma salida para la misma entrada: compara modelos, no la suerte
    cuerpo.setdefault("max_tokens", 350)
    req = urllib.request.Request(url, json.dumps(cuerpo).encode(), {"content-type": "application/json"})
    with CANDADOS.setdefault(nombre, threading.Lock()):
        t0 = time.time()
        js = json.load(urllib.request.urlopen(req, timeout=300))
        ms = (time.time() - t0) * 1000
    return js, ms


class Manejador(BaseHTTPRequestHandler):
    def log_message(self, *a):   # silencio
        pass

    def _json(self, codigo: int, obj) -> None:
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path.endswith("/api/public/catalog"):
            with open(os.path.join(DATOS, "catalogo.json"), encoding="utf-8") as fh:
                return self._json(200, json.load(fh))
        if u.path.endswith("/api/public/stock") or "/api/public/stock/" in u.path:
            with open(os.path.join(DATOS, "stock.json"), encoding="utf-8") as fh:
                st = json.load(fh)
            q = urllib.parse.parse_qs(u.query).get("codes", [""])[0]
            codes = {c.strip().upper() for c in q.split(",") if c.strip()} or {u.path.rsplit("/", 1)[-1].upper()}
            return self._json(200, {"at": st.get("at"), "stock": [s for s in st["stock"] if s["code"] in codes]})
        if u.path == "/health":
            return self._json(200, {"ok": True, "upstream": list(UPSTREAM)})
        self._json(404, {"error": "no existe"})

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        cuerpo = json.loads(self.rfile.read(n) or b"{}")
        partes = self.path.strip("/").split("/")
        try:
            if partes[0] == "oro":
                texto = respuesta_oro(cuerpo)
                return self._json(200, {"choices": [{"message": {"role": "assistant", "content": texto}}], "usage": {}})
            if partes[0] == "up" and len(partes) > 1 and partes[1] in UPSTREAM:
                js, ms = proxy(partes[1], cuerpo)
                texto = (js.get("choices") or [{}])[0].get("message", {}).get("content", "")
                with LOG_LOCK, open(os.path.join(DATOS, f"llamadas_{partes[1]}.jsonl"), "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"t": time.time(), "ms": round(ms), "messages": cuerpo.get("messages"),
                                         "json": bool(cuerpo.get("response_format")), "texto": texto,
                                         "usage": js.get("usage")}, ensure_ascii=False) + "\n")
                js.pop("usage", None)   # el agente suma usage.cost: aquí no hay costo
                return self._json(200, js)
        except Exception as e:  # noqa: BLE001
            return self._json(502, {"error": str(e)[:300]})
        self._json(404, {"error": "ruta desconocida"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--puerto", type=int, default=18493)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--upstream", action="append", default=[], help="nombre=url de un mlx_lm.server")
    a = ap.parse_args()
    for x in a.upstream:
        k, v = x.split("=", 1)
        UPSTREAM[k] = v
    srv = ThreadingHTTPServer((a.host, a.puerto), Manejador)
    print(f"puente en {a.host}:{a.puerto} · upstream {UPSTREAM}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
