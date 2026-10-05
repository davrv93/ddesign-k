"""Pruebas del aviso al CRM del chat web (app/crm.py). Sin dependencias: `python3 -m app.prueba_crm`."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

from . import crm

ENVIOS = {"lima": {"costo": 15}, "provincia": {"costo": 20}}
RES = {"etapa": "cierre", "accion": "pedido", "codigo": "V35", "talla": "M", "respuesta": "¡Listo! Te lo separo en M.",
       "comercial": {"intent": "intencion_compra"}, "sugerencias": [{"codigo": "V35"}, {"imagen": "x"}],
       "memoria": {"producto": "V35", "temperatura": "caliente", "sabemos": {"envio": "lima", "talla": "M"}}}

fallos: list[str] = []


def ok(cond: bool, nombre: str) -> None:
    print(("✓ " if cond else "✗ ") + nombre)
    if not cond:
        fallos.append(nombre)


def req(**kw):
    base = {"canal": "web", "conversacion": "web-abc123def456", "mensaje": "Talla M del V35", "desde_anuncio": False, "anuncio": ""}
    base.update(kw)
    return SimpleNamespace(**base)


def main() -> None:
    p = crm.payload(req(), RES, ENVIOS)
    ok(p is not None and p["canal"] == "web" and p["conversacion"] == "web-abc123def456", "turno web → aviso con su sesión")
    ok(p["etapa"] == "cierre" and p["intencion"] == "intencion_compra" and p["codigo"] == "V35" and p["talla"] == "M", "etapa, intención y pedido")
    ok(p["sugerencias"] == ["V35"], "solo las tarjetas con código")
    ok(p["envio_costo"] == 15.0, "costo del envío desde venta.json (Lima)")
    ok(p["memoria"]["temperatura"] == "caliente", "la memoria viaja entera")
    ok(crm.payload(req(canal=""), RES, ENVIOS) is None, "WhatsApp (sin canal) no avisa: lo lleva el backend")
    ok(crm.payload(req(conversacion="web"), RES, ENVIOS) is None, "«web» a secas (UI vieja) no identifica una sesión")
    ok(crm.payload(req(conversacion="../../etc"), RES, ENVIOS) is None, "sesión con caracteres raros: no")
    ok(crm.payload(req(), "no es un dict", ENVIOS) is None, "respuesta rara: no revienta")
    ok(crm.payload(req(mensaje="x" * 9000), RES, ENVIOS)["mensaje"] == "x" * 2000, "mensaje recortado")

    # Sin URL o sin secreto no se manda nada.
    crm.URL, crm.SECRETO = "", ""
    ok(crm.avisar(req(), RES, ENVIOS) is False, "apagado sin CRM_EVENT_URL/CRM_EVENT_SECRET")

    # Con un backend lento, avisar() vuelve al instante y el POST llega con el secreto.
    recibido: dict = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            time.sleep(1.0)
            recibido["secreto"] = self.headers.get("X-CRM-Secret")
            recibido["cuerpo"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(202)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    crm.URL, crm.SECRETO = f"http://127.0.0.1:{srv.server_port}/api/internal/crm/evento", "secreto-de-prueba"
    t0 = time.time()
    salio = crm.avisar(req(), RES, ENVIOS)
    ok(salio and time.time() - t0 < 0.2, "avisar no espera al backend (va en un hilo)")
    for _ in range(50):
        if recibido:
            break
        time.sleep(0.1)
    ok(recibido.get("secreto") == "secreto-de-prueba" and recibido.get("cuerpo", {}).get("codigo") == "V35", "el POST llega con el secreto")
    srv.shutdown()

    # Backend caído: avisar no lanza nada.
    crm.URL = "http://127.0.0.1:9/nada"
    try:
        crm.avisar(req(), RES, ENVIOS)
        time.sleep(0.3)
        ok(True, "backend caído: ni excepción ni espera")
    except Exception as e:  # noqa: BLE001
        ok(False, f"backend caído lanzó {e}")

    print(f"\n{'CRM OK' if not fallos else 'FALLAN: ' + ', '.join(fallos)}")
    raise SystemExit(1 if fallos else 0)


if __name__ == "__main__":
    main()
