"""Paso 3/3. Borrador de fichas técnicas con lo que dice baruka.pe → salida/fichas_producto_propuestas.json
(+ salida/fichas_nuevos_web.json para los productos de la web que aún no tienen código en el CRM).

Uso: python3 fichas.py      (lee salida/catalogo_baruka_pe.json, salida/mapeo.json y agente/seed/fichas_producto.json)

Mismo formato que agente/seed/fichas_producto.json (lo lee agente/app/ficha_producto.py), más:
- `medidas` = {talla: {busto, cintura, cadera, largo…}} en cm, SOLO de tablas que el OCR leyó completas y coherentes;
  `medidas_origen` dice de qué imagen salen y cómo se leyeron.
- `web` = de qué producto de baruka.pe sale (url, colores, tallas, precio).
Reglas: lo que la web dice entra con `fuente: "baruka.pe"`; lo que la web no dice se conserva tal cual; si la web dice
otra cosa que la ficha actual, manda la web y la ficha anterior queda en `antes` y en `discrepancias` para revisar.
Ninguna cifra se completa a mano: si la tabla no se leyó bien, las medidas siguen en `pendiente_tienda`.
No toca agente/seed/: el borrador se revisa y se copia a mano.
"""
import copy
import json
import os
import re

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(AQUI, "salida")
SEED = os.path.join(AQUI, "..", "..", "agente", "seed", "fichas_producto.json")
ATRIBUTOS = ["silueta", "largo", "escote", "mangas", "cintura", "espalda", "cierre", "tela", "forro", "transparencias", "estampado"]
SIN_TORSO = {"pantalón", "falda"}            # no se les ponen mangas ni escote aunque la frase lo mencione
VACIAS = {"con", "para", "que", "una", "unos", "unas", "los", "las", "del", "sin", "muy", "mas", "tipo", "parte", "tiene",
          "lleva", "aporta", "elegante", "diseno", "corte", "estilo", "look", "visualmente", "desde", "hasta", "este", "esta"}
RE_COMO_QUEDA = re.compile(r"(?i)\b(estiliz\w*|realz\w*|disimul\w*|alarga\w*|favorec\w*|resalt\w*|moldea\w*|define\w*|"
                           r"marca la cintura|cubrir\w*)\b")


# Lo que se ve en una foto (la ficha actual lo sacó de TODAS las fotos, con detalle): se conserva y la frase de la web va
# al lado. Lo que solo sabe la tienda (tela, forro, cierre): manda la web.
VISUALES = {"silueta", "largo", "escote", "mangas", "cintura", "espalda", "transparencias", "estampado"}
REGLA = {"silueta": r"corte|silueta|dise[nñ]o|entallad|palazzo|sirena|recto", "largo": r"larg|mini|midi|maxi|rodilla|tobillo",
         "escote": r"escote|cuello|halter", "mangas": r"manga|tirante|hombro", "cintura": r"cintura|pretina|cintur[oó]n|tiro",
         "espalda": r"espalda", "transparencias": r"transparen", "estampado": r"estampad|floral|rayas"}
RE_ROTULO = re.compile(r"(?i)^(tiro|pretina|dise[nñ]o|largo|bolsillos?|botones|acabados?|detalles?|abertura|tirantes?|"
                       r"falda|top|blazer|pantal[oó]n|capa|cola)\s*:\s*\S")     # «Tiro: Alto», «Pretina: Estructurada…»
RE_DETALLE = re.compile(r"(?i)bolsillo|abertura|botones|lazo|drapead|bordad|pedrer|volante|flecos|tablones|pliegue|"
                        r"tirante|herraje|\bcola\b")
RE_NO_DETALLE = re.compile(r"(?i)consulta tu talla|^color\s*:|^ideal|^beneficios|para qu[eé]|^ocasiones")


def plano(s):
    s = (s or "").lower()
    for a, b in zip("áéíóúüñ", "aeiouun"):
        s = s.replace(a, b)
    return s


def claves(s):
    """Palabras con contenido, reducidas a su raíz de 5 letras («largas» = «larga», «entallado» = «entallada»)."""
    return {w[:5] for w in re.findall(r"[a-z]{4,}", plano(s)) if w not in VACIAS}


def compatibles(a, b):
    """¿Dos frases hablan de lo mismo sin contradecirse? Heurística: comparten alguna palabra con contenido."""
    return bool(claves(a) & claves(b))


def elegir(frases, attr):
    """La frase de la web que mejor sirve de valor. Se copia de la web; solo se le quita su propia etiqueta («Material: …»)."""
    if not frases:
        return None
    if attr == "cuidados":
        return ". ".join(dict.fromkeys(frases))[:300]
    propia = {"tela": r"material|tela|composici[oó]n|tejido", "cierre": r"cierre", "forro": r"forro"}.get(attr)
    if propia:
        rot = [f for f in frases if re.match(rf"(?i)({propia})\s*:\s*\S", f)]
        if rot:
            return re.sub(rf"(?i)^({propia})\s*:\s*", "", rot[0])[:200]
    if attr == "tela":
        hecha = [f for f in frases if re.search(r"(?i)confeccionad[oa] en|\btela [A-ZÁÉÍÓÚ]|\bmaterial [a-záéíóú]+:", f)]
        return re.sub(r"(?i)^material\s+", "", (hecha or frases)[0])[:200]
    rx = re.compile(REGLA.get(attr, r"$^"), re.I)
    al_inicio = [f for f in frases if rx.search(" ".join(f.split()[:3]))]
    f = min(al_inicio or frases, key=len)
    if re.match(r"^[A-ZÁÉÍÓÚ][\wáéíóúñ ]{1,20}:\s*\S", f) and not rx.search(f.split(":")[0]):
        return f[:200]                                    # «Cierre: Invisible en la espalda» como dato de espalda
    cabeza = f.split(":")[0].strip() if ":" in f else re.split(r",\s*que\b|\s+que\s+", f)[0].strip()
    if ":" in f and len(cabeza.split()) <= 2:
        return f[:200]                                    # «Pretina: Estructurada con soporte» va entera
    return (cabeza if len(cabeza) >= 6 and rx.search(cabeza) else f)[:200]


def medidas_de(producto, avisos):
    """{talla: {fila: valor}} uniendo las tablas válidas del producto (el Almudena trae dos: top y pantalón)."""
    validas = [t for t in producto["tablas_medidas"] if t["valida"]]
    if not validas:
        return None, None
    med, notas, vias = {}, [], {}
    for t in validas:
        for fila, vals in t["filas"].items():
            if fila.startswith("largo") and any(fila in a for a in avisos):
                continue                     # largo que no casa con la prenda (Bernadette): va a discrepancias
            for talla, v in vals.items():
                med.setdefault(talla, {})[fila] = v
        notas += [n for n in t["notas"] if n not in notas]
        vias.update(t["lecturas_no_directas"])
    origen = {"fuente": "baruka.pe", "imagenes": [t["imagen"] for t in validas],
              "metodo": "OCR (Vision de macOS) de la tabla de medidas, que en la web es una imagen; validada por rangos, "
                        "crecimiento por talla y contra la tabla común de la tienda",
              "unidad": "cm; talla_pantalon es la talla equivalente de pantalón, no cm",
              "notas_de_la_tabla": notas,
              "observacion": "busto, cintura y cadera son iguales en todas las tablas de baruka.pe y la imagen trae "
                             "«Recomendaciones para tomar tus medidas»: sirven para comparar con las medidas de la clienta; "
                             "lo propio de cada prenda es el largo"}
    if vias:
        origen["celdas_releidas"] = vias
    return med, origen


def de_la_web(producto, actual=None):
    """Atributos, detalles y cómo queda según la web (frases copiadas)."""
    fw = producto["ficha_web"]
    cat = producto.get("categoria")
    attrs = {}
    for a in ATRIBUTOS:
        if cat in SIN_TORSO and a in ("mangas", "escote"):
            continue
        v = elegir(fw.get(a), a)
        if v:
            attrs[a] = v
    usadas = {f for a in ATRIBUTOS for f in fw.get(a, [])} | set(fw.get("ocasiones", [])) | set(fw.get("cuidados", []))
    lineas = [re.sub(r"^[\s•·\-–*✔✓✅❤️♥📌🧵]+", "", ln).strip(" .") for ln in producto["descripcion"].split("\n")]
    como = [ln for ln in lineas if RE_COMO_QUEDA.search(ln) and 8 < len(ln) < 160 and not re.match(r"(?i)ideal|ocasiones", ln)][:4]
    detalles = [ln for ln in lineas if ln not in usadas and ln not in como and 4 < len(ln) <= 110
                and (RE_ROTULO.match(ln) or RE_DETALLE.search(ln)) and not RE_NO_DETALLE.search(ln)][:6]
    return attrs, detalles, como, (elegir(fw.get("cuidados"), "cuidados") if fw.get("cuidados") else None)


def actualizar_pendientes(pend, ficha):
    out = []
    tela = (ficha["atributos"].get("tela") or {}).get("valor") or ""
    for p in pend:
        q = plano(p)
        if "medidas" in q and ficha.get("medidas"):
            continue
        if "forro" in q and (ficha["atributos"].get("forro") or {}).get("valor"):
            continue
        if re.search(r"^tela|tela del|composicion|que tela", q) and tela:
            continue
        if re.search(r"estira|elasticidad", q) and re.search(r"(?i)stretch|el[aá]stic|elasticidad|licra|spandex", tela):
            continue
        if re.search(r"\bcierre\b", q) and (ficha["atributos"].get("cierre") or {}).get("valor"):
            continue
        if re.search(r"cuidado|lavado|lavar", q) and ficha.get("cuidados"):
            continue
        out.append(p)
    return out


def main():
    web = json.load(open(os.path.join(SALIDA, "catalogo_baruka_pe.json")))
    por_handle = {p["handle"]: p for p in web["productos"]}
    mapeo = json.load(open(os.path.join(SALIDA, "mapeo.json")))
    fila_de = {f["codigo"]: f for f in mapeo["crm_a_web"]}
    actuales = json.load(open(SEED))
    propuestas, cambios = [], {}
    for actual in actuales:
        cod = actual["codigo"]
        f = fila_de.get(cod) or {}
        nueva = copy.deepcopy(actual)
        if f.get("estado") != "mapeado":
            nueva["web"] = None
            nueva.setdefault("discrepancias", [])
            nueva["discrepancias"] = nueva["discrepancias"] + [f"no figura en baruka.pe ({web['extraido'][:10]}): confirmar si sigue a la venta"]
            propuestas.append(nueva)
            continue
        p = por_handle[f["web"]["handle"]]
        attrs, detalles, como, cuidados = de_la_web(p)
        disc, n = list(nueva.get("discrepancias") or []), 0
        for a, v in attrs.items():
            cur = (nueva["atributos"] or {}).get(a)
            if not cur or not cur.get("valor"):
                nueva["atributos"][a] = {"valor": v, "fuente": "baruka.pe"}
                n += 1
            elif a in VISUALES:
                nueva["atributos"][a] = dict(cur, fuente=f"{cur['fuente']}+baruka.pe", baruka_pe=v)
                if not compatibles(cur["valor"], v):
                    disc.append(f"{a}: baruka.pe dice «{v}»; la ficha dice «{cur['valor']}» (fuente: {cur['fuente']}). "
                                "¿Dicen lo mismo con otras palabras? Revisar.")
            elif compatibles(cur["valor"], v):
                nueva["atributos"][a] = {"valor": v, "fuente": f"{cur['fuente']}+baruka.pe", "antes": cur}
                n += 1
            else:
                nueva["atributos"][a] = {"valor": v, "fuente": "baruka.pe", "antes": cur}
                disc.append(f"{a}: baruka.pe dice «{v}»; la ficha decía «{cur['valor']}» (fuente: {cur['fuente']}). "
                            "Se toma la web: confirmar con la tienda.")
                n += 1
        ya = " ".join(d["valor"] if isinstance(d, dict) else str(d) for d in nueva.get("detalles") or [])
        nueva["detalles"] = list(nueva.get("detalles") or []) + [{"valor": d, "fuente": "baruka.pe"} for d in detalles
                                                                  if not (claves(d) and claves(d) <= claves(ya))]
        ya = " ".join(d["valor"] if isinstance(d, dict) else str(d) for d in nueva.get("como_queda") or [])
        nueva["como_queda"] = list(nueva.get("como_queda") or []) + [{"valor": c, "fuente": "baruka.pe"} for c in como
                                                                      if not (claves(c) and claves(c) <= claves(ya))]
        if cuidados and not nueva.get("cuidados"):
            nueva["cuidados"] = cuidados
            nueva["cuidados_fuente"] = "baruka.pe"
        med, origen = medidas_de(p, p["avisos"])
        if med:
            nueva["medidas"], nueva["medidas_origen"] = med, origen
        elif p["tablas_medidas"]:
            nueva["pendiente_tienda"] = list(nueva.get("pendiente_tienda") or []) + [
                "medidas por talla: la tabla de baruka.pe es una imagen que el OCR no leyó bien; revisarla a mano: "
                + p["tablas_medidas"][0]["imagen"]]
        disc += [a for a in p["avisos"] if a not in disc]
        disc += [x for x in f.get("notas", []) if x.startswith("baruka.pe no vende el color")]
        nueva["discrepancias"] = disc
        nueva["pendiente_tienda"] = actualizar_pendientes(nueva.get("pendiente_tienda") or [], nueva)
        nueva["web"] = {"url": p["url"], "handle": p["handle"], "nombre": p["nombre"], "colores": p["colores"],
                        "color_que_casa": f.get("colores_web_que_casan"),
                        "tallas": [t["talla"] + (" (pre orden)" if t["preorden"] else "") for t in p["tallas"]],
                        "precio": f.get("precio_web_del_color") or [p["precio"]], "mapeo": f.get("por")}
        propuestas.append(nueva)
        cambios[cod] = {"atributos_nuevos_o_cambiados": n, "medidas": bool(med), "discrepancias_nuevas": len(disc) - len(actual.get("discrepancias") or [])}
    json.dump(propuestas, open(os.path.join(SALIDA, "fichas_producto_propuestas.json"), "w"), ensure_ascii=False, indent=1)

    # Ficha SOLO con baruka.pe para TODOS los modelos de la web, también los que ya tenían código en el CRM: desde el
    # 12-10-2026 la web es el catálogo oficial y el usuario pidió que las fichas sean solo según ella (nada de Diners
    # ni del análisis de fotos). Lo que la web no dice queda en pendiente_tienda.
    nuevos, todas = [], []
    sin_codigo = {x["handle"] for x in mapeo["nuevos_en_web"]}
    for p in por_handle.values():
        attrs, detalles, como, cuidados = de_la_web(p)
        med, origen = medidas_de(p, p["avisos"])
        cat = p.get("categoria")
        ficha = {"codigo": f"WEB:{p['handle']}", "nombre": p["nombre"], "piezas": [cat] if cat and cat != "conjunto" else [],
                 "atributos": {a: ({"valor": attrs[a], "fuente": "baruka.pe"} if a in attrs else None) for a in ATRIBUTOS},
                 "detalles": [{"valor": d, "fuente": "baruka.pe"} for d in detalles],
                 "como_queda": [{"valor": c, "fuente": "baruka.pe"} for c in como], "cuidados": cuidados, "resumen": "",
                 "discrepancias": list(p["avisos"]), "pendiente_tienda": []}
        ficha["atributos"]["color_visto"] = None
        if med:
            ficha["medidas"], ficha["medidas_origen"] = med, origen
        pend = ficha["pendiente_tienda"]
        if not med:
            pend.append("medidas por talla" + (": la tabla de baruka.pe es una imagen que el OCR no leyó bien; revisarla a mano: "
                                               + p["tablas_medidas"][0]["imagen"] if p["tablas_medidas"] else " (la web no trae tabla)"))
        if "tela" not in attrs:
            pend.append("tela")
        if "forro" not in attrs:
            pend.append("si tiene forro")
        if not cuidados:
            pend.append("cuidados de lavado")
        if cat == "conjunto" or not cat:
            pend.append("qué piezas trae")
        if not p["descripcion"].strip():
            pend.append("silueta, largo, escote y mangas (la web no trae descripción)")
        ficha["web"] = {"url": p["url"], "handle": p["handle"], "colores": p["colores"],
                        "tallas": [t["talla"] + (" (pre orden)" if t["preorden"] else "") for t in p["tallas"]],
                        "precio": sorted({v["precio"] for v in p["variantes"]}), "fotos": p["fotos"]}
        todas.append(ficha)
        if p["handle"] in sin_codigo:
            nuevos.append(ficha)
    json.dump(nuevos, open(os.path.join(SALIDA, "fichas_nuevos_web.json"), "w"), ensure_ascii=False, indent=1)
    json.dump(todas, open(os.path.join(SALIDA, "fichas_web.json"), "w"), ensure_ascii=False, indent=1)
    for cod, c in cambios.items():
        print(cod, c)
    print(f"{len(propuestas)} fichas propuestas (V21–V42) · {len(nuevos)} fichas de productos nuevos de la web "
          f"({sum(1 for x in nuevos if x.get('medidas'))} con medidas)")


if __name__ == "__main__":
    main()
