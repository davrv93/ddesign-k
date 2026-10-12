"""Paso 2/3. Cruza salida/catalogo_baruka_pe.json con el catálogo del CRM (V21–V44) y deja salida/mapeo.json.

Uso: python3 mapear.py [--crm archivo.json]
Sin --crm lee el catálogo PÚBLICO vivo (GET, solo lectura): https://proyectopostventa.site/baruka/api/public/catalog

Cómo se cruza: por el nombre del modelo («Pantalón Codman» ↔ «Pantalon Codman»; tipo + modelo, sin tildes) y luego por
color (sinónimos «black» = negro y familias: «beige oscuro» es de la familia beige). Lo que no se puede deducir del nombre
va en MANUAL, con el porqué. Nada se escribe en el CRM.
"""
import json
import os
import re
import sys
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(AQUI, "salida")
CRM_URL = "https://proyectopostventa.site/baruka/api/public/catalog"
UA = "BarukaCatalogoBot/1.0 (+https://proyectopostventa.site/baruka; extractor de solo lectura)"

# Correspondencias que el nombre no da: código → (handle en baruka.pe, color de la web o None, evidencia).
MANUAL = {
    "V42": ("vestido-alma", "Azul Cobalto",
            "V42 «Vestido Gala Capa Azul» tiene nombre provisional. Su lámina (escote en V, corpiño bordado, cintura "
            "marcada, mangas tipo capa de gasa, base de podesuá con acabado en gasa) coincide con la descripción del "
            "Vestido Alma, y la foto real del V42 es el mismo modelo en el mismo montaje (maniquí, sala, ventana) que las "
            "fotos del Alma en baruka.pe; el tono es el de la variante «Azul Cobalto» (su foto en la web se llama "
            "«Elegantevestidodegalacolorteal.png»). Comparado a ojo el 12-10-2026."),
}

SINONIMO = {"black": "negro", "negra": "negro", "green": "verde", "rosado": "rosa", "rosada": "rosa", "turqueza": "turquesa",
            "camell": "camel", "blanca": "blanco", "roja": "rojo", "nude.": "nude", "azul petrolio": "azul petroleo"}
FAMILIA = {
    "negro": "negro", "blanco": "blanco", "beige": "beige", "beige oscuro": "beige", "camel": "beige",
    "rosa": "rosa", "palo rosa": "rosa", "chicle": "rosa", "fucsia": "rosa", "rojo": "rojo", "vino": "rojo",
    "azul": "azul", "azul noche": "azul", "azulino": "azul", "azul cobalto": "azul", "celeste": "azul",
    "celeste oscuro": "azul", "celeste bebe": "azul", "turquesa": "azul", "azul petroleo": "azul", "acero": "azul",
    "verde": "verde", "verde botella": "verde", "verde olivo": "verde", "verde cobalto": "verde", "verde jade": "verde",
    "verde agua": "verde", "naranja": "naranja", "terracota": "naranja", "mostaza": "amarillo", "amarillo": "amarillo",
    "maiz": "amarillo", "lila": "morado", "barney": "morado", "dalia": "morado", "marron": "marron",
}
TIPOS = {"vestido", "blusa", "pantalon", "enterizo", "conjunto", "blazer", "falda", "top", "overol"}


def plano(s):
    s = (s or "").lower().strip()
    for a, b in zip("áéíóúüñ", "aeiouun"):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s)


def color_norm(c):
    c = plano(c)
    return SINONIMO.get(c, c)


def coincidencia_color(crm, web):
    """('exacta'|'familia'|None, colores web que casan)."""
    c = color_norm(crm)
    exactas = [w for w in web if color_norm(w) == c]
    if exactas:
        return "exacta", exactas
    fam = FAMILIA.get(c)
    familia = [w for w in web if fam and FAMILIA.get(color_norm(w)) == fam]
    return ("familia", familia) if familia else (None, [])


def tipo_y_modelo(nombre, color=""):
    palabras = plano(nombre).replace("baruka para mujer", "").split()
    tipo = palabras[0] if palabras and palabras[0] in TIPOS else ""
    resto = palabras[1:] if tipo else palabras
    c = plano(color).split()
    while resto and (resto[-1] in c or resto[-1] in {"azul", "rojo", "turquesa", "palo", "rosa"}):
        resto = resto[:-1]
    return tipo, " ".join(resto)


def leer_crm():
    if "--crm" in sys.argv:
        ruta = sys.argv[sys.argv.index("--crm") + 1]
        datos = json.load(open(ruta))
        origen = ruta
    else:
        with urllib.request.urlopen(urllib.request.Request(CRM_URL, headers={"User-Agent": UA}), timeout=30) as r:
            datos = json.loads(r.read().decode())
        origen = CRM_URL
    datos = datos if isinstance(datos, list) else datos.get("products", [])
    return [p for p in datos if re.fullmatch(r"V(2[1-9]|3\d|4[0-4])", str(p.get("code", "")).upper())], origen


def main():
    web = json.load(open(os.path.join(SALIDA, "catalogo_baruka_pe.json")))
    productos = web["productos"]
    por_handle = {p["handle"]: p for p in productos}
    indice = {}
    for p in productos:
        t, m = tipo_y_modelo(p["nombre"])
        t = t or plano(p.get("categoria") or "").replace("pantalón", "pantalon")
        for clave in {m, plano(p["handle"]).replace("-", " ")}:
            indice.setdefault(clave, []).append((t, p))
    crm, origen = leer_crm()
    filas, usados = [], set()
    for c in sorted(crm, key=lambda x: int(x["code"][1:])):
        cod = c["code"].upper()
        tipo, modelo = tipo_y_modelo(c.get("name", ""), c.get("color", ""))
        candidato, por, notas = None, None, []
        if cod in MANUAL:
            candidato, por = por_handle.get(MANUAL[cod][0]), "manual"
            notas.append(MANUAL[cod][2])
        else:
            cands = indice.get(modelo, [])
            mismos = [p for t, p in cands if t == tipo]
            if mismos:
                candidato, por = mismos[0], "nombre y tipo"
            elif cands:
                candidato, por = cands[0][1], "solo nombre (el tipo difiere)"
        fila = {"codigo": cod, "nombre_crm": c.get("name"), "color_crm": c.get("color") or None, "precio_crm": c.get("price"),
                "tallas_crm": c.get("sizes"), "estado": "mapeado" if candidato else "no_esta_en_web"}
        if candidato:
            usados.add(candidato["handle"])
            tipo_c, cols = coincidencia_color(c.get("color") or "", candidato["colores"]) if c.get("color") else (None, [])
            if cod in MANUAL and MANUAL[cod][1]:
                tipo_c, cols = "manual", [MANUAL[cod][1]]
            if not c.get("color"):
                notas.append("el CRM no le pone color (es la ficha genérica del modelo)")
            elif not tipo_c:
                notas.append(f"baruka.pe no vende el color «{c['color']}»: solo {', '.join(candidato['colores'])}")
                slug = plano(c["color"]).replace(" ", "")
                fotos = [f for f in candidato["fotos"] if slug and slug in plano(f.split("/")[-1]).replace("_", "")]
                if fotos:
                    notas.append(f"aunque hay una foto con «{c['color']}» en el nombre: {fotos[0]}")
            precios = sorted({v["precio"] for v in candidato["variantes"] if not cols or v["color"] in cols})
            if c.get("price") is not None and precios and any(abs(float(c["price"]) - p) > 0.005 for p in precios):
                notas.append(f"precio distinto: CRM S/ {c['price']} · web S/ {'–'.join(f'{p:g}' for p in precios)}"
                             + (f" (en {', '.join(cols)})" if cols else ""))
            fila["precio_web_del_color"] = precios
            tallas_web = [t["talla"] + (" (pre orden)" if t["preorden"] else "") for t in candidato["tallas"]]
            fila.update({"por": por, "coincidencia_color": tipo_c, "colores_web_que_casan": cols,
                         "web": {"handle": candidato["handle"], "nombre": candidato["nombre"], "url": candidato["url"],
                                 "precio": candidato["precio"], "precio_max": candidato["precio_max"],
                                 "precio_tachado": candidato["precio_tachado"], "colores": candidato["colores"],
                                 "tallas": tallas_web, "medidas_validas": any(t["valida"] for t in candidato["tablas_medidas"])}})
        else:
            notas.append(f"no hay en baruka.pe un producto «{tipo} {modelo}» (ni por nombre ni por handle)")
        fila["notas"] = notas
        filas.append(fila)
    nuevos = [{"handle": p["handle"], "nombre": p["nombre"], "categoria": p["categoria"], "precio": p["precio"],
               "colores": p["colores"], "tallas": [t["talla"] for t in p["tallas"]], "url": p["url"],
               "medidas_validas": any(t["valida"] for t in p["tablas_medidas"])}
              for p in productos if p["handle"] not in usados]
    resumen = {"productos_web": len(productos), "codigos_crm": len(filas),
               "codigos_crm_mapeados": sum(1 for f in filas if f["estado"] == "mapeado"),
               "codigos_crm_que_ya_no_estan": [f["codigo"] for f in filas if f["estado"] == "no_esta_en_web"],
               "productos_web_con_codigo": len(usados), "productos_web_nuevos": len(nuevos)}
    out = {"generado_desde": {"web": web["fuente"], "extraido": web["extraido"], "crm": origen}, "resumen": resumen,
           "crm_a_web": filas, "nuevos_en_web": nuevos}
    json.dump(out, open(os.path.join(SALIDA, "mapeo.json"), "w"), ensure_ascii=False, indent=1)
    for f in filas:
        w = f.get("web") or {}
        print(f"{f['codigo']:4} {f['nombre_crm'][:24]:24} {str(f['color_crm'] or '—')[:10]:10} → "
              f"{(w.get('nombre') or '— no está en baruka.pe')[:22]:22} {f.get('coincidencia_color') or ''} {f.get('colores_web_que_casan') or ''}")
    print(json.dumps(resumen, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
