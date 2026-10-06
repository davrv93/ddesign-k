"""Respuesta de venta correcta: la métrica compuesta de Baruka (2.8).

Una respuesta acierta solo si cumple las siete condiciones (cada una se
evalúa solo si el caso la especifica; acuerdo con V1 no es acierto: si V1
se equivoca, coincidir con ella también es un error):

1. identifica la prenda o la intención;
2. precio, talla y stock iguales a la base;
3. la foto es de la prenda nombrada;
4. no inventa (códigos ni cifras fuera de los hechos);
5. responde lo que preguntó;
6. la siguiente pregunta es la que toca en la etapa;
7. no repite lo ya dicho.

El conjunto dorado es ``data/venta_oro.jsonl``: faltas de ortografía,
mensajes cortos, continuaciones y 12 fuera de alcance. Nada de aquí se usa
para entrenar. Regla de encendido: V2 solo si supera a V1 en esta métrica
**sin empeorar la invención** (check 4).

Uso sin servidor (puras + tabla)::

    python3 -m app.prueba_v2_medicion

Contra un agente vivo (como ``comparar``)::

    python3 -m app.v2.medicion --url http://127.0.0.1:18497
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

CODIGO_RE = re.compile(r"\bV\d{2}\b")
ACCIONES_DERIVA = ("pedir_asesora", "derivar", "asesora")


def _cods(texto: str) -> list[str]:
    return CODIGO_RE.findall(texto or "")


def venta_correcta(espera: dict, obs: dict) -> dict:
    """espera: lo del caso oro. obs: lo enviado (respuesta, sugerencias con
    foto, plan, etapa, siguiente_pregunta, ultimo_bot, conocidos)."""
    espera, obs = espera or {}, obs or {}
    checks: dict[str, bool | None] = {}
    fallos: list[str] = []
    resp = obs.get("respuesta") or ""
    resp_l = resp.lower()
    cods_resp = _cods(resp)
    sug = [str(s) for s in (obs.get("sugerencias") or [])]
    conocidos = set(obs.get("conocidos") or []) | set(sug)

    def check(nombre: str, ok: bool, fallo: str) -> None:
        checks[nombre] = ok
        if not ok:
            fallos.append(f"{nombre}: {fallo}")

    # 1. identifica la prenda o la intención
    if espera.get("codigo"):
        cod = espera["codigo"]
        prod = obs.get("plan_producto")
        ok = prod == cod or cod in cods_resp or cod in sug
        check("identifica", ok, f"no identifica {cod}")
    elif espera.get("accion"):
        check("identifica", obs.get("plan_accion") == espera["accion"],
              f"acción {obs.get('plan_accion')} ≠ {espera['accion']}")
    else:
        checks["identifica"] = None

    # 2. precio, talla y stock iguales a la base
    if espera.get("precio_txt"):
        check("datos_base", espera["precio_txt"].lower() in resp_l, "precio distinto de la base")
    elif espera.get("talla"):
        check("datos_base", espera["talla"].lower() in resp_l, "talla distinta de la base")
    else:
        checks["datos_base"] = None

    # 3. la foto es de la prenda nombrada
    if espera.get("codigo") and sug:
        check("foto", espera["codigo"] in sug, "la foto no es de la prenda nombrada")
    else:
        checks["foto"] = None

    # 4. no inventa
    inventados = [c for c in cods_resp if conocidos and c not in conocidos]
    cifras = "s/" in resp_l or "s/." in resp_l
    if conocidos or cifras or cods_resp:
        ok = not inventados and (not cifras or espera.get("precio_txt") is not None)
        check("no_inventa", ok, f"inventa {inventados or 'cifras'}")
    else:
        checks["no_inventa"] = None

    # 5. responde lo que preguntó
    if espera.get("contiene"):
        faltan = [s for s in espera["contiene"] if s.lower() not in resp_l]
        check("intencion", not faltan, f"no responde: falta {faltan}")
    elif espera.get("contiene_alguno"):
        ok = any(s.lower() in resp_l for s in espera["contiene_alguno"])
        check("intencion", ok, f"no responde: falta uno de {espera['contiene_alguno']}")
    elif espera.get("no_contiene"):
        mal = [s for s in espera["no_contiene"] if s.lower() in resp_l]
        check("intencion", not mal, f"dice lo que no debe: {mal}")
    else:
        checks["intencion"] = None

    # 6. la siguiente pregunta es la que toca
    if espera.get("pregunta"):
        sig = f"{obs.get('siguiente_pregunta') or ''}\n{resp}".lower()
        check("pregunta", espera["pregunta"].lower() in sig, "la pregunta no es la que toca")
    else:
        checks["pregunta"] = None

    # 7. no repite lo ya dicho
    if obs.get("ultimo_bot") is not None or espera.get("no_repite"):
        repite = bool(obs.get("ultimo_bot")) and resp.strip() == obs["ultimo_bot"].strip()
        mal = [s for s in (espera.get("no_repite") or []) if s.lower() in resp_l]
        ok = not repite and not mal
        check("no_repite", ok, "repite lo ya dicho")
    else:
        checks["no_repite"] = None

    # Fuera de alcance: deriva a la dueña, nunca recomienda lo más cercano.
    if espera.get("deriva"):
        ok = (obs.get("plan_accion") in ACCIONES_DERIVA or "asesor" in resp_l or "*4*" in resp)
        ok = ok and obs.get("plan_accion") != "recomendar"
        check("deriva", ok, "no deriva a la dueña")
    else:
        checks["deriva"] = None

    return {"pasa": not fallos, "fallos": fallos, "checks": checks}


def tabla_fallo(filas: list[dict]) -> dict:
    """Dónde falla la elección (2.5): con el esperado en el top-20 pero no
    primero, el problema es ordenarlo, no recuperar más (ahorra construir
    una búsqueda nueva que no serviría)."""
    n = len(filas)
    if not n:
        return {"n": 0}
    top20 = sum(1 for f in filas if f.get("esperado") in (f.get("diversa") or []))
    top1 = sum(1 for f in filas if (f.get("diversa") or [])[:1] == [f.get("esperado")])
    elegido = sum(1 for f in filas if f.get("elegido") == f.get("esperado"))
    return {"n": n, "top20": round(top20 / n, 3), "top1": round(top1 / n, 3),
            "elegido": round(elegido / n, 3)}


def _oro(ruta: str) -> list[dict]:
    with open(ruta, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def medir(url: str, oro: list[dict]) -> tuple[list[dict], list[dict]]:
    """Corre el oro contra un agente vivo. Devuelve (resultados, filas_tabla)."""
    from .. import regresion as R
    res, tabla = [], []
    for caso in oro:
        turnos = [{"cliente": m} for m in (caso.get("hilo") or [])] + [{"cliente": caso["mensaje"]}]
        ch = R.Chat(url, {"id": caso["id"], "tipo": "venta_oro", "turnos": turnos})
        j = None
        for t in turnos:
            est, j, _ = ch.turno(t["cliente"], "", 0, None)
        if not isinstance(j, dict):
            res.append({"id": caso["id"], "error": "sin respuesta"})
            continue
        v2 = j.get("v2") or {}
        sb = v2.get("sombra") or {}
        plan = sb.get("plan") or {}
        obs = {"respuesta": j.get("respuesta"), "plan_accion": plan.get("accion"),
               "plan_producto": plan.get("producto"),
               "sugerencias": [s.get("codigo") for s in (j.get("sugerencias") or []) if isinstance(s, dict)],
               "conocidos": [f.get("codigo") for f in (j.get("fichas") or []) if isinstance(f, dict)],
               "siguiente_pregunta": j.get("siguiente_pregunta"), "etapa": j.get("etapa")}
        r = venta_correcta(caso.get("espera") or {}, obs)
        res.append({"id": caso["id"], **r})
        rag = v2.get("rag") or {}
        if caso.get("espera", {}).get("codigo"):
            tabla.append({"esperado": caso["espera"]["codigo"], "diversa": rag.get("diversa") or [],
                          "elegido": rag.get("elegido")})
    return res, tabla


def main() -> int:
    ap = argparse.ArgumentParser(description="PAS-venta y tabla de fallo sobre el oro")
    ap.add_argument("--url", default="http://127.0.0.1:18497")
    ap.add_argument("--oro", default="")
    a = ap.parse_args()
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")
    oro = _oro(a.oro or os.path.join(base, "venta_oro.jsonl"))
    res, tabla = medir(a.url, oro)
    ok = [r for r in res if r.get("pasa")]
    print(f"venta correcta: {len(ok)}/{len(res)}")
    for r in res:
        if not r.get("pasa"):
            print(f"  ✗ {r['id']}: {'; '.join(r.get('fallos', [r.get('error', '?')]))}")
    t = tabla_fallo(tabla)
    if t.get("n"):
        print(f"recuperación top-20: {t['top20']} · orden top-1: {t['top1']} · elegido: {t['elegido']} (n={t['n']})")
    inv = [r for r in res if any(f.startswith("no_inventa") for f in r.get("fallos", []))]
    print(f"invención: {len(inv)}/{len(res)}")
    return 0 if len(ok) == len(res) else 1


if __name__ == "__main__":
    sys.exit(main())
