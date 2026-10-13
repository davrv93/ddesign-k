"""Cada recorte (uno por foto del catálogo) por /foto en producción, como lo mandaría una clienta. Mide modelo, nivel y foto."""
import base64, collections, concurrent.futures as cf, json, os, re, sys, time, urllib.request
U, DIR = "http://127.0.0.1:18482", sys.argv[1]
fs = sorted(f for f in os.listdir(DIR) if f.endswith(".jpg"))
def uno(f):
    esp = f[:-4].split("_")[0].upper()
    b = base64.b64encode(open(os.path.join(DIR, f), "rb").read()).decode()
    t0 = time.time()
    try:
        r = urllib.request.Request(U + "/foto", json.dumps({"imagen_b64": b, "mensaje": "me gusta este", "usar_llm": False, "conversacion": "prueba-recortes"}).encode(), {"Content-Type": "application/json"})
        d = json.load(urllib.request.urlopen(r, timeout=240))
    except Exception as e:
        return {"archivo": f, "esperado": esp, "error": str(e)[:120]}
    fo = d.get("foto") or {}; v = fo.get("vision") or {}
    fotos = [s.get("imagen", "") for s in d.get("sugerencias") or []]
    return {"archivo": f, "esperado": esp, "codigo": fo.get("codigo"), "nivel": fo.get("nivel"), "caso": fo.get("caso"), "sim": fo.get("similitud"),
            "vis": v.get("codigo"), "conf": v.get("confianza"), "color": v.get("color"), "fotos": fotos, "top": [t["codigo"] for t in fo.get("top") or []][:5],
            "ms": int((time.time() - t0) * 1000)}
res = []
with cf.ThreadPoolExecutor(3) as ex:
    for i, r in enumerate(ex.map(uno, fs), 1):
        res.append(r)
        if i % 50 == 0:
            print(f"{i}/{len(fs)}", flush=True)
json.dump(res, open("/tmp/recortes_resultado.json", "w"), ensure_ascii=False, indent=0)
ok = [r for r in res if "error" not in r]
exacto_ok = [r for r in ok if r["nivel"] == "exacto" and r["codigo"] == r["esperado"]]
parecido_ok = [r for r in ok if r["nivel"] == "parecido" and r["codigo"] == r["esperado"]]
ninguno = [r for r in ok if r["nivel"] == "ninguno"]
mal = [r for r in ok if r["nivel"] in ("exacto", "parecido") and r["codigo"] != r["esperado"]]
misma = [r for r in exacto_ok if r["fotos"] and r["fotos"][0].endswith("/" + r["archivo"])]
mismo_modelo = [r for r in exacto_ok if r["fotos"] and re.search(r"/(v\d+)", r["fotos"][0]) and re.search(r"/(v\d+)", r["fotos"][0]).group(1).upper() == r["esperado"]]
n = len(ok)
print(f"\nRECORTES: {n} probados, {len(res) - n} errores de red")
print(f"  modelo correcto y seguro (exacto): {len(exacto_ok)}/{n} ({100*len(exacto_ok)/n:.1f} %)")
print(f"  modelo correcto pero en duda («¿quieres ver otros?»): {len(parecido_ok)}")
print(f"  no reconoció («no lo tengo, ¿quieres ver otros?»): {len(ninguno)}")
print(f"  MODELO EQUIVOCADO (exacto/parecido con otro código): {len(mal)}")
print(f"  de los exactos, foto devuelta = la misma foto: {len(misma)}/{len(exacto_ok)}; del mismo modelo: {len(mismo_modelo)}/{len(exacto_ok)}")
print(f"  tiempo medio: {sum(r['ms'] for r in ok)//n} ms")
print("\nequivocados:")
for r in mal: print(f"  {r['archivo']:14} esperado {r['esperado']} → {r['codigo']} {r['nivel']} (vis {r['vis']} {r['conf']}) top {r['top']}")
print("\nno reconocidos:")
for r in ninguno: print(f"  {r['archivo']:14} esperado {r['esperado']} (vis {r['vis']!r} {r['conf']}) top {r['top']}")
print("\nen duda:")
for r in parecido_ok: print(f"  {r['archivo']:14} (vis {r['conf']})")
