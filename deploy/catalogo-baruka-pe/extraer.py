"""Paso 1/3. Lee el catálogo de baruka.pe (Shopify) y deja salida/catalogo_baruka_pe.json.

Uso: python3 extraer.py [--sin-ocr]      (solo stdlib; pausa de 1,2 s entre peticiones)

De dónde sale cada dato (todo es público y de solo lectura; robots.txt lo permite y se comprueba):
- /products.json (API pública de Shopify): nombre, tipo, etiquetas, descripción HTML, variantes color × talla con
  precio, precio tachado (compare_at_price) y disponibilidad (`available`; Shopify NO publica el stock por unidades),
  y todas las fotos con el color al que pertenece cada una.
- /collections.json + /collections/<c>/products.json: la categoría de los productos sin tipo («KERO», «LUZ»…).
- La tabla de medidas por talla NO está en texto en ninguna parte: es una IMAGEN dentro de la descripción («Consulta tu
  talla en la tabla inferior»). La app de guía de tallas del tema (Kiwi Sizing) está instalada pero sin tablas
  (`getSizingChart` devuelve `sizings: []`). Por eso las imágenes de la descripción se leen con el OCR de macOS
  (Vision, `ocr_vision.swift`) y se reconstruye la tabla por filas y columnas. Cada tabla trae `valida` y `problemas`:
  solo las válidas pasan a las fichas. Las imágenes se bajan a una caché FUERA del repo (CACHE_DIR, por defecto en
  $TMPDIR), nunca a git.
"""
import html as H
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone

BASE = "https://baruka.pe"
UA = "BarukaCatalogoBot/1.0 (+https://proyectopostventa.site/baruka; extractor de solo lectura del catálogo para el CRM de Baruka)"
PAUSA = 1.2
AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(AQUI, "salida")
CACHE = os.environ.get("CACHE_DIR", os.path.join(tempfile.gettempdir(), "catalogo-baruka-pe-cache"))
SIN_OCR = "--sin-ocr" in sys.argv

_robots = urllib.robotparser.RobotFileParser(BASE + "/robots.txt")
_ultima = [0.0]


def get(url, binario=False, intentos=4):
    """GET con pausa mínima entre peticiones, reintento ante 429/5xx y comprobación de robots.txt para baruka.pe."""
    if url.startswith(BASE) and not _robots.can_fetch(UA, url):
        raise PermissionError(f"robots.txt no permite {url}")
    for i in range(intentos):
        espera = PAUSA - (time.time() - _ultima[0])
        if espera > 0:
            time.sleep(espera)
        _ultima[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                data = r.read()
            return data if binario else data.decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < intentos - 1:
                time.sleep(5 * (i + 1))
                continue
            raise


def get_json(url):
    return json.loads(get(url))


# ───────────────────────── texto ─────────────────────────
_BLOQUE = re.compile(r"(?i)<\s*(br|/p|/li|/h\d|/div|/ul|/ol|li|p|h\d|div|tr|/tr)\b[^>]*>")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍⭐⬆↔-⇿]")


def texto_de_html(h):
    """Descripción HTML → líneas de texto limpias (las etiquetas en línea no parten la frase)."""
    h = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", h or "")
    h = _BLOQUE.sub("\n", h)
    h = re.sub(r"<[^>]+>", "", h)
    h = H.unescape(h).replace("\xa0", " ")
    lineas = []
    for ln in h.split("\n"):
        ln = re.sub(r"\s+", " ", ln).strip()
        ln = re.sub(r"\s+([,.;:])", r"\1", ln)
        if not ln or ln in ("•", "-", "*"):
            continue
        # varias viñetas en un mismo párrafo («✔️ Silueta… ✔️ Diseño…») o «Diseño: … Tiro: Alto Tela: Morgan …» seguidos
        for trozo in re.split(r"\s+(?=(?:✔️|✔|✅|❤️|❤|•|▪|🔹)\s*\S)", ln):
            if len(re.findall(_CLAVE_VALOR, trozo)) >= 2:
                lineas += [t.strip() for t in re.split(r"\s+(?=" + _CLAVE_VALOR + ")", trozo) if t.strip()]
            else:
                lineas.append(trozo.strip())
    return lineas


_CLAVE_VALOR = r"[A-ZÁÉÍÓÚ][a-záéíóúñ]+(?: [a-záéíóúñ]+){0,2}:\s"


def limpio(ln):
    """Quita viñetas y emojis del principio/fin de una línea."""
    ln = _EMOJI.sub("", ln)
    ln = re.sub(r"^[\s•·\-–*✔✓✅❤♥📌📏🧵🧼🔥]+", "", ln)
    return re.sub(r"\s+", " ", ln).strip(" .;")


# Atributos que se buscan en las líneas de la descripción (el valor es la frase de la web, sin reescribir).
REGLAS = {
    "tela": r"\b(material|tela|tejido|composici[oó]n|confeccionad[oa] en)\b",
    "forro": r"\bforr(o|ad[oa]s?)\b",
    "cierre": r"\bcierres?\b|\bcremallera\b|\bbotones\b",
    "mangas": r"\bmangas?\b|\bsin mangas\b|\btirantes?\b|\bstrapless\b|\bun (solo )?hombro\b|\bhombros? descubiertos?\b",
    "largo": r"\blarg[oa]\b(?! de brazo)|\bmini\b|\bmidi\b|\bmaxi\b|\brodilla\b|\btobillo\b|\bal piso\b",
    "escote": r"\bescote\b|\bcuello\b|\bhalter\b",
    "silueta": r"\bcorte\b|\bsilueta\b|\bentallad[oa]\b|\bpalazzo\b|\bsirena\b|\bevas[eé]\b|\bajustad[oa]\b|\bholgad[oa]\b",
    "cintura": r"\bcintura\b|\bpretina\b|\bcintur[oó]n\b",
    "espalda": r"\bespalda\b",
    "transparencias": r"\btransparen\w*|\btrasl[uú]cid\w*",
    "estampado": r"\bestampad\w*|\bfloral\b|\ba rayas\b|\banimal print\b",
    "cuidados": r"\blavar\b|\blavado\b|\bplanch\w*|\bsecar\b|\bciclo delicado\b|\bsecadora\b|\blej[ií]a\b",
}
RE_REGLAS = {k: re.compile(v, re.I) for k, v in REGLAS.items()}
# frases que contienen la palabra pero no hablan de ese atributo («manga larga» no es el largo de la prenda)
_NO_CUENTA = {"largo": r"\bmangas? larg\w*", "cierre": r"\bbotones decorativ\w*"}
RE_NO_ATRIBUTO = re.compile(r"(?i)consulta tu talla|tabla (inferior|de medidas)|gu[ií]a|^beneficios$|para qu[eé] sirve|"
                            r"qu[eé] problema resuelve|para qui[eé]n|^detalles del producto|^caracter[ií]sticas")


def ficha_web(lineas):
    """Lo que la descripción dice de cada atributo: {atributo: [frases]} (vacío si no lo dice)."""
    out = {}
    for ln in lineas:
        c = limpio(ln)
        if len(c) < 4 or RE_NO_ATRIBUTO.search(c):
            continue
        for k, rx in RE_REGLAS.items():
            if rx.search(re.sub(_NO_CUENTA.get(k, r"$^"), " ", c, flags=re.I)):
                if k == "cintura" and re.search(r"(?i)\b(talla|consulta)\b", c):
                    continue
                out.setdefault(k, [])
                if c not in out[k]:
                    out[k].append(c)
    ocas = [limpio(ln) for ln in lineas if re.search(r"(?i)\bideal(es)? para\b|\bocasiones\b", ln)]
    if ocas:
        out["ocasiones"] = ocas
    return out


# ───────────────────────── tallas y colores ─────────────────────────
RE_TALLA = re.compile(r"(?i)^\s*(?:talla\s*)?(xxs|xs|s|m|l|xl|xxl|2xl|3xl|talla [uú]nica)\b")


def talla_norm(v):
    """«XS - Pre orden», «Pre orden XL», «2XL-Pre-orden», «Talla única» → (talla, preorden)."""
    s = (v or "").strip()
    pre = bool(re.search(r"(?i)pre[\s-]*orden", s))
    base = re.sub(r"(?i)pre[\s-]*orden", "", s).strip(" -")
    m = RE_TALLA.match(base)
    t = m.group(1).upper() if m else base.upper()
    return ("Talla única" if t.upper().startswith("TALLA") else t), pre


def es_opcion_talla(valores):
    return sum(1 for v in valores if RE_TALLA.match(re.sub(r"(?i)pre[\s-]*orden", "", v).strip(" -"))) >= max(1, len(valores) // 2 + 1)


TIPOS = {"vestido": "vestido", "blusa": "blusa", "pantalon": "pantalón", "pantalón": "pantalón", "enterizo": "enterizo",
         "conjunto": "conjunto", "blazer": "blazer", "falda": "falda", "top": "top", "overol": "overol"}
COLECCION_TIPO = {"blusas": "blusa", "enterizos": "enterizo", "pantalon": "pantalón", "conjuntos": "conjunto",
                  "vestido-cortos": "vestido", "vestidos": "vestido", "blazer": "blazer"}


def sin_tildes(s):
    for a, b in zip("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN"):
        s = s.replace(a, b)
    return s


# ───────────────────────── OCR de la tabla de medidas ─────────────────────────
def binario_ocr():
    if SIN_OCR or platform.system() != "Darwin" or not shutil.which("swiftc"):
        return None
    os.makedirs(CACHE, exist_ok=True)
    fuente = os.path.join(AQUI, "ocr_vision.swift")
    exe = os.path.join(CACHE, "ocr_vision")
    if not os.path.exists(exe) or os.path.getmtime(exe) < os.path.getmtime(fuente):
        subprocess.run(["swiftc", "-O", fuente, "-o", exe], check=True)
    return exe


def imagen_local(url):
    """Baja la imagen a la caché (fuera del repo). Si es pequeña, guarda además una copia ampliada ×2 para el OCR."""
    clave = re.sub(r"[^A-Za-z0-9_.-]", "_", url.split("/files/")[-1])[:150]
    ipath = os.path.join(CACHE, clave)
    if not os.path.exists(ipath):
        u = url if url.startswith("http") else "https:" + url
        u += ("&" if "?" in u else "?") + "width=1600"
        open(ipath, "wb").write(get(u, binario=True))
    r = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", ipath], capture_output=True, text=True)
    dims = [int(x) for x in re.findall(r"pixel(?:Width|Height): (\d+)", r.stdout)]
    if dims and dims[0] < 1400:
        up = ipath + ".x2.png"
        if not os.path.exists(up):
            subprocess.run(["sips", "-Z", str(2 * max(dims)), "-s", "format", "png", ipath, "--out", up], capture_output=True, check=True)
        return up
    return ipath


def ocr(exe, ruta, rois=None):
    """OCR de la imagen entera o de varias regiones (x, y, w, h normalizadas). Caché junto a la imagen."""
    sufijo = "" if not rois else "." + str(abs(hash(tuple(rois))) % 10**10)
    jpath = ruta + sufijo + ".ocr.json"
    if os.path.exists(jpath):
        return json.load(open(jpath))
    args = [exe, ruta] + [",".join(f"{v:.4f}" for v in r) for r in (rois or [])]
    r = subprocess.run(args, capture_output=True, text=True, timeout=300)
    obs = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else ([] if not rois else [[] for _ in rois])
    json.dump(obs, open(jpath, "w"), ensure_ascii=False)
    return obs


RE_NUM = re.compile(r"\d{1,3}(?:[.,]\d{1,2})?")
TALLAS_CANON = ["XXS", "XS", "S", "M", "L", "XL", "XXL"]
RE_TOK_TALLA = r"(XXS|XS|S|M|L|XL|XXL|2XL|3XL)"
CLAVES_FILA = [("busto", r"BUSTO|PECHO"), ("cintura", r"CINTURA"), ("cadera", r"CADERA"),
               ("talla_pantalon", r"TALLA\s*(DE|POR)\s*PANTAL"), ("largo_manga", r"MANGA"), ("ancho_hombro", r"HOMBRO"),
               ("tiro", r"\bTIRO\b"), ("largo", r"LARGO"), ("estatura", r"ESTATURA")]
RANGO = {"busto": (60, 160), "cintura": (45, 150), "cadera": (65, 170), "largo": (15, 200), "talla_pantalon": (20, 50)}


def cifra(o):
    """(texto, vía) si la observación es UNA cifra: la lectura principal o, si no, una de las hipótesis de Vision."""
    for i, t in enumerate([o["t"]] + list(o.get("alt") or [])):
        t = t.strip()
        if RE_NUM.fullmatch(t):
            return t, ("ocr" if i == 0 else "ocr_alternativa")
    return None


def _tallas_cabecera(obs):
    """Observaciones que son tallas de la cabecera → [(talla, x, y)] (separa «TALLAS| TALLAM» si llegó en un bloque)."""
    out = []
    for o in obs:
        for t in [o["t"]] + list(o.get("alt") or []):
            u = re.sub(r"[^A-Z0-9 ]", " ", sin_tildes(t).upper()).strip()
            u = re.sub(r"\s+", " ", u)
            if re.fullmatch(r"(TALLA\s*)?" + RE_TOK_TALLA, u):
                out.append((re.sub(r"^TALLA\s*", "", u).replace("2XL", "XXL"), o["x"], o["y"]))
                break
            if re.fullmatch(r"(\s*TALLA\s*" + RE_TOK_TALLA + r")+\s*", u):
                toks = re.findall(r"TALLA\s*" + RE_TOK_TALLA + r"(?![A-Z])", u)
                if len(toks) >= 2:
                    x0 = o["x"] - o["w"] / 2
                    out += [(tk.replace("2XL", "XXL"), x0 + o["w"] * (i + 0.5) / len(toks), o["y"]) for i, tk in enumerate(toks)]
                    break
    return out


def tabla_de(obs, releer=None):
    """Reconstruye la tabla de medidas a partir del OCR. None si la imagen no es una tabla de tallas.
    `releer(rois)` vuelve a leer con Vision las celdas que faltan (una región por celda)."""
    cab = sorted(_tallas_cabecera(obs), key=lambda c: c[2])
    grupos = []
    for c in cab:
        if grupos and abs(c[2] - grupos[-1][-1][2]) < 0.015:
            grupos[-1].append(c)
        else:
            grupos.append([c])
    fila = sorted(max(grupos, key=len), key=lambda c: c[1]) if grupos else []
    if len(fila) < 3 or any(c[0] not in TALLAS_CANON for c in fila):
        return None
    idx = [TALLAS_CANON.index(c[0]) for c in fila]
    if idx != sorted(set(idx)):
        return None
    notas_ocr = []
    # columnas que el OCR no leyó en la cabecera (p. ej. «TALLA S» pegada al borde): se deducen por orden y posición
    d = min((b[1] - a[1]) / (ib - ia) for a, b, ia, ib in zip(fila, fila[1:], idx, idx[1:]))
    completa = [fila[0]]
    for a, b, ia, ib in zip(fila, fila[1:], idx, idx[1:]):
        k = ib - ia
        if k > 1 and abs((b[1] - a[1]) - k * d) < 0.25 * d * k:
            for s in range(1, k):
                completa.append((TALLAS_CANON[ia + s], a[1] + (b[1] - a[1]) * s / k, a[2]))
            notas_ocr.append(f"cabecera: {', '.join(TALLAS_CANON[ia + 1:ib])} deducidas por su posición entre {a[0]} y {b[0]}")
        completa.append(b)
    fila = completa
    yc = sum(c[2] for c in fila) / len(fila)
    # columnas de los bordes que el OCR no leyó («TALLA L», «TALLA XL» al final): solo si hay cifras en ≥2 filas justo ahí
    for lado in (1, -1):
        while True:
            borde = fila[-1] if lado == 1 else fila[0]
            k = TALLAS_CANON.index(borde[0]) + lado
            if not 0 <= k < len(TALLAS_CANON):
                break
            x = borde[1] + lado * d
            hay = {round(o["y"], 2) for o in obs if o["y"] > yc + 0.005 and cifra(o) and abs(o["x"] - x) < 0.3 * d}
            if len(hay) < 2 or not 0 < x < 1:
                break
            nueva = (TALLAS_CANON[k], x, borde[2])
            fila = fila + [nueva] if lado == 1 else [nueva] + fila
            notas_ocr.append(f"cabecera: {TALLAS_CANON[k]} deducida por su posición (hay cifras en esa columna)")
    tallas, xs = [c[0] for c in fila], [c[1] for c in fila]
    paso = (xs[-1] - xs[0]) / (len(xs) - 1)

    def columna(x):
        j = min(range(len(xs)), key=lambda k: abs(xs[k] - x))
        return j if abs(xs[j] - x) < 0.6 * paso else None

    nums = []
    for o in obs:
        if o["y"] <= yc + 0.005:
            continue
        c = cifra(o)
        if c:
            j = columna(o["x"])
            if j is not None:
                nums.append((o["y"], j, c[0], c[1]))
        elif re.fullmatch(r"(\d{1,3}(?:[.,]\d{1,2})?\s+){1,6}\d{1,3}(?:[.,]\d{1,2})?", o["t"].strip()):  # varias cifras juntas
            partes = o["t"].split()
            x0 = o["x"] - o["w"] / 2
            for i, p in enumerate(partes):
                j = columna(x0 + o["w"] * (i + 0.5) / len(partes))
                if j is not None:
                    nums.append((o["y"], j, p, "ocr"))
    nums.sort()
    filas = []
    for n in nums:
        if filas and n[0] - filas[-1]["y"] < 0.012:
            filas[-1]["celdas"].append(n)
        else:
            filas.append({"y": n[0], "celdas": [n]})
    filas = [f for f in filas if len({c[1] for c in f["celdas"]}) >= max(2, len(xs) // 2)]
    bloque = []
    for f in filas:          # solo el bloque contiguo bajo la cabecera
        if not bloque:
            if f["y"] - yc > 0.15:
                break
        else:
            gaps = [b["y"] - a["y"] for a, b in zip([{"y": yc}] + bloque, bloque)]
            if f["y"] - bloque[-1]["y"] > 2.5 * (sum(gaps) / len(gaps)):
                break
        bloque.append(f)
    if not bloque:
        return None
    gap = (bloque[-1]["y"] - yc) / len(bloque)
    etiquetas = {i: [] for i in range(len(bloque))}
    for o in sorted(obs, key=lambda o: o["y"]):
        if o["x"] < xs[0] - 0.5 * paso and re.search(r"[A-Za-zÁÉÍÓÚáéíóú]", o["t"]) and yc + 0.005 < o["y"] < bloque[-1]["y"] + 0.6 * gap:
            i = min(range(len(bloque)), key=lambda k: abs(bloque[k]["y"] - o["y"]))
            if abs(bloque[i]["y"] - o["y"]) < 0.6 * gap:
                etiquetas[i].append(o["t"].strip())
    celdas = [{} for _ in bloque]       # celdas[i][talla] = (texto, vía)
    problemas = []
    for i, f in enumerate(bloque):
        for _, j, t, via in f["celdas"]:
            if tallas[j] in celdas[i] and celdas[i][tallas[j]][0] != t:
                problemas.append(f"dos cifras distintas en la fila {i + 1}, talla {tallas[j]}")
            celdas[i][tallas[j]] = (t, via)
    # segunda lectura de las celdas vacías, cada una en su región
    faltan = [(i, j) for i in range(len(bloque)) for j in range(len(tallas)) if tallas[j] not in celdas[i]]
    if faltan and releer:
        # dos regiones por celda (justa y holgada): vale si coinciden, o si solo una lee una cifra; si discrepan, vacía
        rois = []
        for i, j in faltan:
            rois.append((xs[j] - 0.45 * paso, bloque[i]["y"] - 0.45 * gap, 0.9 * paso, 0.9 * gap))
            rois.append((xs[j] - 0.6 * paso, bloque[i]["y"] - 0.6 * gap, 1.2 * paso, 1.2 * gap))
        lecturas = releer(rois)
        for n, (i, j) in enumerate(faltan):
            leidas = []
            for lect in lecturas[2 * n:2 * n + 2]:
                h = [c for c in (cifra(o) for o in lect) if c]
                if len(h) == 1:
                    leidas.append(h[0])
            if leidas and len({t for t, _ in leidas}) == 1:
                t, via = leidas[0]
                celdas[i][tallas[j]] = (t, "ocr_celda" if via == "ocr" else "ocr_celda_alternativa")
            elif leidas:
                problemas.append(f"fila {i + 1}, talla {tallas[j]}: dos lecturas distintas ({', '.join(t for t, _ in leidas)})")
    filas_out, texto_out, vias = {}, {}, {}
    for i, f in enumerate(bloque):
        etiqueta = " ".join(etiquetas[i]).strip() or f"fila {i + 1}"
        u = sin_tildes(etiqueta).upper()
        clave = next((k for k, rx in CLAVES_FILA if re.search(rx, u)), re.sub(r"[^a-z0-9]+", "_", sin_tildes(etiqueta).lower()).strip("_"))
        if clave == "largo":
            resto = re.sub(r"(?i)^largo\s*(del|de|total)?\s*", "", sin_tildes(etiqueta)).strip().lower()
            if resto and resto not in ("vestido", "prenda", "total", "enterizo", "total del vestido"):
                clave = "largo_" + re.sub(r"[^a-z0-9]+", "_", resto).strip("_")   # largo_pantalon, largo_top, largo_blazer…
        if clave in filas_out:
            clave = f"{clave}_{i + 1}"
        falt = [t for t in tallas if t not in celdas[i]]
        if falt:
            problemas.append(f"«{etiqueta}» sin cifra para {', '.join(falt)}")
        valores = {}
        for t in tallas:
            if t not in celdas[i]:
                continue
            txt, via = celdas[i][t]
            v = float(txt.replace(",", "."))
            if clave != "talla_pantalon" and re.search(r"[.,]", txt) and v < 3:
                v = v * 100          # «1.10» = metros → 110 cm
            valores[t] = int(round(v)) if abs(v - round(v)) < 0.01 else round(v, 1)
            if via != "ocr":
                vias[f"{etiqueta}/{t}"] = via
        lo, hi = RANGO.get("largo" if clave.startswith("largo") else clave, (0, 999))
        fuera = [f"{t}={v}" for t, v in valores.items() if not lo <= v <= hi]
        if fuera:
            problemas.append(f"«{etiqueta}» fuera de rango ({', '.join(fuera)})")
        if clave in ("busto", "cintura", "cadera", "talla_pantalon") or clave.startswith("largo"):
            orden = [valores[t] for t in tallas if t in valores]
            if any(b < a for a, b in zip(orden, orden[1:])):
                problemas.append(f"«{etiqueta}» decrece con la talla ({orden})")
        filas_out[clave] = valores
        texto_out[etiqueta] = {t: celdas[i][t][0] for t in tallas if t in celdas[i]}
    fin = bloque[-1]["y"] + gap
    notas = [o["t"].strip() for o in sorted(obs, key=lambda o: o["y"])
             if fin - 0.3 * gap < o["y"] < fin + 0.08 and (o["t"].strip().startswith(("*", "\"", "'")) or re.search(r"(?i)hombro|importante", o["t"]))]
    return {"tallas": tallas, "filas": filas_out, "texto_original": texto_out, "notas": notas,
            "unidad": "cm (las cifras en metros, p. ej. «1.10», se pasan a cm; talla_pantalon es la talla, no cm)",
            "lecturas_no_directas": vias, "notas_ocr": notas_ocr, "problemas": problemas, "valida": not problemas}


def contrastar_con_tabla_comun(productos):
    """Busto, cintura y cadera se repiten igual en casi todas las tablas de la tienda. La más repetida (calculada aquí,
    no escrita a mano) sirve de control: una tabla que difiera se marca dudosa y se dice si parece tener las filas
    corridas (le pasa a la imagen del Pantalón Lexy: dice «CINTURA 85…110», que en las demás es el busto)."""
    from collections import Counter
    unicas = {t["imagen"]: t for p in productos for t in p["tablas_medidas"] if t["valida"]}
    comun = {}
    for k in ("busto", "cintura", "cadera"):
        c = Counter(tuple(sorted(t["filas"][k].items(), key=lambda kv: TALLAS_CANON.index(kv[0]) if kv[0] in TALLAS_CANON else 9))
                    for t in unicas.values() if k in t["filas"])
        if c:
            comun[k] = dict(c.most_common(1)[0][0])
    for p in productos:
        for t in p["tablas_medidas"]:
            if not t["valida"]:
                continue
            nuevos = []
            for k, ref in comun.items():
                v = t["filas"].get(k)
                if not v:
                    continue
                if any(ref.get(s) not in (None, x) for s, x in v.items()):
                    otra = next((o for o, r in comun.items() if o != k and all(r.get(s) in (None, x) for s, x in v.items())), None)
                    nuevos.append(f"«{k}» trae las cifras que en las demás tablas son de {otra}: la imagen tiene las filas corridas"
                                  if otra else f"«{k}» difiere de la tabla común de la tienda ({ref}): revisar a mano")
            if nuevos:
                t["problemas"] = t["problemas"] + nuevos
                t["valida"] = False
    return comun


# ───────────────────────── principal ─────────────────────────
def main():
    os.makedirs(SALIDA, exist_ok=True)
    _robots.read()
    portada = get(BASE + "/")
    m = re.search(r'Shopify\.currency\s*=\s*\{"active":"([A-Z]{3})"', portada)
    moneda = m.group(1) if m else None

    productos, page = [], 1
    while True:
        lote = get_json(f"{BASE}/products.json?limit=250&page={page}")["products"]
        if not lote:
            break
        productos += lote
        page += 1
    print(f"{len(productos)} productos en /products.json")

    colecciones = {}
    for c in get_json(f"{BASE}/collections.json?limit=250")["collections"]:
        for p in get_json(f"{BASE}/collections/{c['handle']}/products.json?limit=250")["products"]:
            colecciones.setdefault(p["handle"], []).append({"handle": c["handle"], "titulo": c["title"]})

    exe = binario_ocr()
    if not exe:
        print("aviso: sin OCR (no es macOS, falta swiftc o --sin-ocr): las tablas de medidas quedan solo como URL")
    cache_ocr = {}
    salida = []
    for p in productos:
        opts = p.get("options") or []
        i_talla = next((o["position"] for o in opts if es_opcion_talla(o["values"])), None)
        i_color = next((o["position"] for o in opts if o["position"] != i_talla), None)
        variantes = []
        for v in p["variants"]:
            color = (v.get(f"option{i_color}") or "").strip() if i_color else ""
            traw = (v.get(f"option{i_talla}") or "").strip() if i_talla else ""
            talla, pre = talla_norm(traw) if traw else ("", False)
            variantes.append({"id": v["id"], "sku": v.get("sku"), "color": color, "talla": talla, "talla_texto": traw,
                              "preorden": pre, "disponible": v.get("available"), "precio": float(v["price"]),
                              "precio_tachado": float(v["compare_at_price"]) if v.get("compare_at_price") else None})
        colores = list(dict.fromkeys(v["color"] for v in variantes if v["color"]))
        tallas = []
        for t in dict.fromkeys(v["talla"] for v in variantes if v["talla"]):
            vs = [v for v in variantes if v["talla"] == t]
            tallas.append({"talla": t, "preorden": any(v["preorden"] for v in vs),
                           "disponible_en": [v["color"] for v in vs if v["disponible"]]})
        color_de_variante = {v["id"]: v["color"] for v in variantes}
        fotos, fotos_por_color = [], {}
        for im in p.get("images") or []:
            fotos.append(im["src"])
            for vid in im.get("variant_ids") or []:
                c = color_de_variante.get(vid)
                if c and im["src"] not in fotos_por_color.setdefault(c, []):
                    fotos_por_color[c].append(im["src"])
        lineas = texto_de_html(p.get("body_html"))
        imgs_desc = [("https:" + u if u.startswith("//") else u) for u in re.findall(r'<img[^>]+src="([^"]+)"', p.get("body_html") or "")]
        tablas, texto_imgs = [], []
        for u in imgs_desc:
            if not exe:
                continue
            if u not in cache_ocr:
                ruta = imagen_local(u)
                obs = ocr(exe, ruta)
                cache_ocr[u] = (obs, tabla_de(obs, releer=lambda rois, ruta=ruta: ocr(exe, ruta, rois)))
            obs, tabla = cache_ocr[u]
            if tabla:
                tablas.append(dict(imagen=u, **tabla))
            else:
                txt = " ".join(o["t"] for o in sorted(obs, key=lambda o: (round(o["y"], 2), o["x"])))
                if txt:
                    texto_imgs.append({"imagen": u, "texto_ocr": txt})
        primera = sin_tildes(p["title"].split()[0]).lower() if p["title"].split() else ""
        categoria = (TIPOS.get(primera) or TIPOS.get(sin_tildes(p.get("product_type") or "").lower())
                     or next((COLECCION_TIPO[c["handle"]] for c in colecciones.get(p["handle"], []) if c["handle"] in COLECCION_TIPO), None))
        precios = [v["precio"] for v in variantes] or [0.0]
        tachados = [v["precio_tachado"] for v in variantes if v["precio_tachado"] and v["precio_tachado"] > v["precio"]]
        nombres_desc = sorted({m.group(2).lower() for m in re.finditer(r"\b(Vestido|Pantal[oó]n|Blusa|Enterizo|Conjunto|Overol)\s+([A-ZÁÉÍÓÚ][a-záéíóúñ]+)", " ".join(lineas))})
        avisos = [f"la descripción nombra «{n}», que no es este producto" for n in nombres_desc
                  if n not in sin_tildes(p["title"]).lower() and sin_tildes(n) not in sin_tildes(p["title"]).lower()]
        for tb in tablas:      # la tabla dice lo que dice; si no casa con la prenda, se avisa (no se corrige)
            for k, vals in tb["filas"].items():
                if k.startswith("largo") and categoria in ("blusa", "top") and vals and max(vals.values()) > 90:
                    avisos.append(f"la tabla de medidas da {k} = {min(vals.values())}–{max(vals.values())} cm para una {categoria}: "
                                  "parece copiada de otra prenda; confirmar con la tienda")
        salida.append({
            "id": p["id"], "handle": p["handle"], "url": f"{BASE}/products/{p['handle']}", "nombre": p["title"].strip(),
            "tipo_shopify": p.get("product_type") or "", "categoria": categoria,
            "colecciones": [c["titulo"] for c in colecciones.get(p["handle"], [])], "etiquetas": p.get("tags") or [],
            "moneda": moneda, "precio": min(precios), "precio_max": max(precios),
            "precio_tachado": max(tachados) if tachados else None, "en_oferta": bool(tachados),
            "disponible": any(v["disponible"] for v in variantes), "colores": colores, "tallas": tallas,
            "variantes": variantes, "descripcion": "\n".join(lineas), "descripcion_html": p.get("body_html") or "",
            "ficha_web": ficha_web(lineas), "tablas_medidas": tablas, "imagenes_descripcion": imgs_desc,
            "texto_en_imagenes": texto_imgs, "fotos": fotos, "fotos_por_color": fotos_por_color, "avisos": avisos,
            "publicado": p.get("published_at"), "actualizado": p.get("updated_at"),
        })
    contrastar_con_tabla_comun(salida)
    for p in salida:
        tb = p["tablas_medidas"][0] if p["tablas_medidas"] else None
        print(f"{p['nombre'][:28]:28} | S/ {p['precio']:>6} | {len(p['colores']):2} colores | {[t['talla'] for t in p['tallas']]} | "
              f"{len(p['fotos']):2} fotos | medidas: {'sí' if tb and tb['valida'] else ('DUDOSA' if tb else 'no')}")
    meta = {"fuente": BASE, "extraido": datetime.now(timezone.utc).isoformat(timespec="seconds"), "metodo":
            "Shopify /products.json + /collections; tablas de medidas por OCR (Vision) de las imágenes de la descripción",
            "moneda": moneda, "total": len(salida), "productos": salida}
    ruta = os.path.join(SALIDA, "catalogo_baruka_pe.json")
    json.dump(meta, open(ruta, "w"), ensure_ascii=False, indent=1)
    con = sum(1 for p in salida if any(t["valida"] for t in p["tablas_medidas"]))
    print(f"\n{len(salida)} productos → {ruta}\ncon tabla de medidas leída y válida: {con}; "
          f"con tabla dudosa: {sum(1 for p in salida if p['tablas_medidas'] and not any(t['valida'] for t in p['tablas_medidas']))}")


if __name__ == "__main__":
    main()
