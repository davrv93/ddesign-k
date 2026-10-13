"""Tablero de colores del catálogo (baruka.pe) agrupados según la pregunta del guion de noche (speech.md, paso 4):
«¿Prefieres llamar un poco más la atención (colores vibrantes), o buscas verte elegante, sofisticada y más discreta
(colores sobrios)?». Propuesta de agrupación para revisar: salida/tablero_colores.html y .pdf (Chrome headless).

    python3 tablero_colores.py
"""
import collections, json, os, subprocess, unicodedata

AQUI = os.path.dirname(os.path.abspath(__file__))
SOBRIOS = {"negro": "#141414", "azul noche": "#1b2a4a", "azul petróleo": "#1f4e5f", "vino": "#6b1d2a", "verde olivo": "#6b7a3a",
           "verde botella": "#1f4d3a", "verde cobalto": "#2e6b5a", "terracota": "#b5563a", "marrón": "#6e4a2f", "beige": "#d9c3a5",
           "beige oscuro": "#b89b78", "nude": "#e3c8b4", "camel": "#c19a6b", "champagne": "#e9d8b8", "acero": "#7f8c99", "gris": "#9a9a9a",
           "plateado": "#c0c0c8", "blanco": "#f4f4f4"}
VIBRANTES = {"rojo": "#d1202a", "fucsia": "#e0218a", "azulino": "#1f6fd1", "azul cobalto": "#0a3fb5", "celeste": "#7cc4f5",
             "celeste bebé": "#a9d8f7", "celeste oscuro": "#3a8fd0", "rosa": "#f08fb5", "palo rosa": "#e7b4b9", "chicle": "#ff6fb5",
             "lila": "#b9a0e8", "barney": "#7a3ea8", "dalia": "#c4418f", "turquesa": "#2cc1c0", "verde jade": "#2faa7c",
             "verde agua": "#9fe3d2", "verde": "#2e9e45", "amarillo": "#f2d230", "mostaza": "#d4a516", "maíz": "#f0c74a",
             "dorado": "#d4af37", "a rayas": "rayas", "geométrico": "geo", "animal": "animal"}
POR_DEFINIR = {"clara": "#f1e9dc", "italia": "#bbbbbb", "italiano": "#bbbbbb", "color personalizado": "#ffffff"}
ALIAS = {"black": "negro", "green": "verde", "camell": "camel", "turqueza": "turquesa", "maiz": "maíz", "marron": "marrón",
         "azul petrolio": "azul petróleo", "geometrico": "geométrico", "celeste bebe": "celeste bebé"}


def pl(s):
    return unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode().strip().rstrip(".")


# Colores que la IA (mapa_fotos.py) ve en las fotos sin color en la web → el color de la web más cercano.
IA_A_WEB = {"azul marino": "azul noche", "azul oscuro": "azul noche", "navy": "azul noche", "borgona": "vino", "burdeos": "vino",
            "guinda": "vino", "granate": "vino", "verde oliva": "verde olivo", "oliva": "verde olivo", "verde militar": "verde olivo",
            "verde esmeralda": "verde cobalto", "verde oscuro": "verde botella", "beige claro": "beige", "crema": "champagne",
            "arena": "beige", "camel": "camel", "marron claro": "camel", "cafe": "marrón", "chocolate": "marrón", "gris claro": "gris",
            "gris oscuro": "acero", "plata": "plateado", "rosa claro": "palo rosa", "rosado": "rosa", "rosa palo": "palo rosa",
            "magenta": "fucsia", "morado": "barney", "purpura": "barney", "violeta": "lila", "lavanda": "lila", "celeste claro": "celeste bebé",
            "azul claro": "celeste", "azul cielo": "celeste", "azul": "azulino", "azul electrico": "azul cobalto", "turquesa": "turquesa",
            "verde agua": "verde agua", "verde menta": "verde agua", "verde": "verde", "amarillo": "amarillo", "mostaza": "mostaza",
            "dorado": "dorado", "naranja": "terracota", "coral": "terracota", "ladrillo": "terracota", "rojo": "rojo", "negro": "negro",
            "blanco": "blanco", "nude": "nude", "beige": "beige", "vino": "vino", "fucsia": "fucsia", "celeste": "celeste", "lila": "lila"}
TIENDA = os.path.normpath(os.path.join(AQUI, "..", "..", "agente", "imagenes", "tienda"))
MINIS = os.path.join(os.environ.get("TMPDIR", "/tmp"), "tablero-minis")


def color_grupo(color_web, color_ia):
    """(nombre del color del tablero, 'web'|'ia') o (None, None)."""
    todos = {pl(k): k for k in (*SOBRIOS, *VIBRANTES, *POR_DEFINIR)}
    if color_web:
        k = ALIAS.get(pl(color_web), pl(color_web))
        if k in todos:
            return todos[k], "web"
    c = pl(color_ia or "")
    if c in IA_A_WEB:
        k = pl(IA_A_WEB[c])
        return todos.get(k, IA_A_WEB[c]), "ia"
    for frase, web in sorted(IA_A_WEB.items(), key=lambda x: -len(x[0])):   # «verde oliva claro» → verde olivo
        if frase in c:
            return todos.get(pl(web), web), "ia"
    return None, None


def mini(archivo):
    os.makedirs(MINIS, exist_ok=True)
    dst = os.path.join(MINIS, archivo)
    if not os.path.exists(dst):
        subprocess.run(["sips", "-Z", "260", os.path.join(TIENDA, archivo), "--out", dst], check=True, capture_output=True)
    return dst


def paginas_fotos(productos):
    """Páginas con las fotos reales del catálogo, por grupo y color (seed/fotos_color.json)."""
    fc = json.load(open(os.path.normpath(os.path.join(AQUI, "..", "..", "agente", "seed", "fotos_color.json")), encoding="utf-8"))
    nombre = {p["code"]: p["name"] for p in productos}
    por_color = collections.defaultdict(list)
    sin = []
    for archivo, d in fc.items():
        if not os.path.exists(os.path.join(TIENDA, archivo)):
            continue
        k, via = color_grupo(d.get("color_web"), d.get("color_ia"))
        if k:
            por_color[k].append((archivo, d["codigo"], via))
        else:
            sin.append((archivo, d["codigo"], d.get("color_ia") or "?"))
    def foto(archivo, cod, nota=""):
        return (f"<figure><img src='file://{mini(archivo)}'><figcaption><b>{cod}</b> {nombre.get(cod, '')[:22]}"
                f"{(' · ' + nota) if nota else ''}</figcaption></figure>")
    def bloque(titulo, colores, clase):
        secs = []
        for k in sorted(colores, key=lambda c: -len(por_color.get(c, []))):
            fotos = por_color.get(k, [])
            if not fotos:
                continue
            ia = sum(1 for _, _, v in fotos if v == "ia")
            secs.append(f"<h3><span class='dot' style='background:{colores[k] if colores[k].startswith('#') else '#999'}'></span>"
                        f"{k} <small>{len(fotos)} fotos{f' · {ia} por la IA' if ia else ''}</small></h3><div class='fotos'>"
                        + "".join(foto(a, c, "" if v == "web" else "IA") for a, c, v in fotos) + "</div>")
        return f"<section class='{clase} fp'><h2>{titulo} · fotos</h2>" + "".join(secs) + "</section>"
    html = bloque("Llamar la atención · colores vibrantes", VIBRANTES, "vib") + bloque("Elegante y discreta · colores sobrios", SOBRIOS, "sob")
    html += bloque("Por definir", POR_DEFINIR, "def")
    if sin:
        html += ("<section class='def fp'><h2>Fotos sin color claro</h2><p class='sub'>La web no dice el color y la IA vio algo que no "
                 "está en el tablero.</p><div class='fotos'>" + "".join(foto(a, c, ia[:14]) for a, c, ia in sin) + "</div></section>")
    return ("<style>.fp{page-break-before:always}.fotos{display:grid;grid-template-columns:repeat(7,1fr);gap:6px;margin-bottom:8px}"
            "figure{margin:0;break-inside:avoid}figure img{width:100%;height:118px;object-fit:cover;border-radius:6px}"
            "figcaption{font-size:8.5px;color:#333;line-height:1.2;margin-top:2px}h3{font-size:12px;margin:10px 0 4px}"
            "h3 small{font-weight:normal;color:#666}.dot{display:inline-block;width:12px;height:12px;border-radius:50%;"
            "vertical-align:middle;margin-right:5px;border:1px solid #ccc}</style>" + html)


def main():
    d = json.load(open(os.path.join(AQUI, "salida", "catalogo_oficial.json"), encoding="utf-8"))["productos"]
    modelos = collections.defaultdict(list)
    for p in d:
        for c in p["colores"]:
            k = ALIAS.get(pl(c), pl(c))
            k = next((x for x in (*SOBRIOS, *VIBRANTES, *POR_DEFINIR) if pl(x) == k), k)
            if p["name"] not in modelos[k]:
                modelos[k].append(p["name"])

    def tarjeta(nombre, hexc):
        fondo = {"rayas": "repeating-linear-gradient(45deg,#333 0 6px,#eee 6px 12px)",
                 "geo": "repeating-linear-gradient(90deg,#222 0 5px,#c9a 5px 10px,#eee 10px 15px)",
                 "animal": "radial-gradient(#5a3a1a 3px, #d9b36a 4px)"}.get(hexc, hexc)
        borde = " style='border:1px solid #ccc'" if hexc in ("#f4f4f4", "#ffffff", "#f1e9dc") else ""
        ms = modelos.get(nombre, [])
        ej = ", ".join(ms[:4]) + (f" y {len(ms) - 4} más" if len(ms) > 4 else "")
        return (f"<div class='c'><div class='sw'{borde} style='background:{fondo}'></div>"
                f"<div class='n'>{nombre}</div><div class='k'>{len(ms)} modelo{'s' if len(ms) != 1 else ''}</div>"
                f"<div class='e'>{ej}</div></div>")

    def grupo(titulo, sub, colores, clase):
        orden = sorted(colores, key=lambda k: -len(modelos.get(k, [])))
        return (f"<section class='{clase}'><h2>{titulo}</h2><p class='sub'>{sub}</p><div class='grid'>"
                + "".join(tarjeta(k, colores[k]) for k in orden) + "</div></section>")

    total = len(d)
    html = f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Tablero de colores · Baruka</title>
<style>
 @page {{ size: A4; margin: 14mm 12mm; }}
 body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; color:#222; margin:0; }}
 h1 {{ font-size: 22px; margin: 0 0 4px; }} .lead {{ color:#555; font-size: 12px; margin: 0 0 14px; line-height:1.45; }}
 h2 {{ font-size: 16px; margin: 14px 0 2px; }} .sub {{ margin: 0 0 8px; color:#666; font-size: 11px; }}
 .grid {{ display:grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }}
 .c {{ break-inside: avoid; }} .sw {{ height: 46px; border-radius: 8px; }}
 .n {{ font-weight: 600; font-size: 12px; margin-top: 4px; text-transform: capitalize; }}
 .k {{ font-size: 10px; color:#666; }} .e {{ font-size: 9.5px; color:#444; line-height:1.3; }}
 section.vib h2 {{ color:#c0206a; }} section.sob h2 {{ color:#1b2a4a; }} section.def h2 {{ color:#777; }}
 .pie {{ margin-top: 14px; font-size: 10px; color:#666; line-height:1.45; }}
</style></head><body>
<h1>Tablero de colores · Baruka Design</h1>
<p class="lead">Los {len(modelos)} colores que baruka.pe lista en sus {total} modelos (13-10-2026), agrupados según la pregunta del guion
para eventos de noche: <b>«¿Prefieres llamar un poco más la atención, o buscas verte elegante, sofisticada y más discreta?»</b>.
Cada tarjeta: muestra aproximada del tono, cuántos modelos vienen en él y ejemplos. Propuesta para revisar: si un color debe
cambiar de grupo, se ajusta en <code>tablero_colores.py</code>.</p>
{grupo("Llamar la atención · colores vibrantes", "Lo que el bot ofrece cuando ella elige «llamar la atención».", VIBRANTES, "vib")}
{grupo("Elegante y discreta · colores sobrios", "Lo que el bot ofrece cuando ella elige «elegante, sofisticada y más discreta».", SOBRIOS, "sob")}
{grupo("Por definir", "Nombres de la web que no describen un tono claro: conviene que la tienda diga qué son.", POR_DEFINIR, "def")}
<p class="pie">Fuente: catálogo oficial (deploy/catalogo-baruka-pe/salida/catalogo_oficial.json). «black» cuenta como negro,
«green» como verde, «camell» como camel, «turqueza» como turquesa. Hoy el bot NO filtra modelos por este grupo: la elección
«llamar la atención / elegante» solo se guarda y se usa en el tono del texto; con este tablero aprobado puede empezar a
ofrecer primero los modelos del grupo elegido.</p>
</body></html>"""
    html = html.replace("</body></html>", paginas_fotos(d) + "</body></html>")
    out = os.path.join(AQUI, "salida", "tablero_colores.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    pdf = os.path.join(AQUI, "salida", "tablero_colores.pdf")
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={pdf}", f"file://{out}"],
                   check=True, capture_output=True, timeout=120)
    sin_grupo = [k for k in modelos if k not in SOBRIOS and k not in VIBRANTES and k not in POR_DEFINIR]
    print(f"{pdf}\ncolores sin grupo: {sin_grupo}")


if __name__ == "__main__":
    main()
