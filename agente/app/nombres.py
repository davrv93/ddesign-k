"""Las prendas se nombran por su NOMBRE, no por su código (pedido de la tienda, 15-10-2026).

Dos cosas viven aquí:

1. `nombradas(texto, fichas, ctx)`: qué prendas nombra un texto. Vale el código si lo escribe la clienta («V50»), y vale el
   nombre («Salome», «vestido Holly», «la Lila»). Dos trampas que el código tapaba y que sin él pasan en silencio:
   - Nombres REPETIDOS (Holly es vestido y pantalón; Camila, blusa y enterizo; Zoraida y Zoraida Clásico): «el Holly» a
     secas no decide; se devuelve en `ambiguas` para preguntar «¿el vestido o el pantalón?». Lo resuelve el tipo («pantalón
     Holly»), la prenda en foco o la única que el bot acaba de nombrar.
   - Nombres que son PALABRAS COMUNES o colores (Alma, Sol, Luz, Lila, Nieve, Paloma, Cristal, Rose, Eva…): «con toda el
     alma» no nombra al Vestido Alma y «la blusa lila» es un color. Cuentan solo con contexto: el tipo delante («vestido
     Alma»), un determinante («el Sol», «la Lila», «modelo Luz») o que el bot le haya preguntado cuál.

2. `sin_codigos(texto, fichas)`: la red de seguridad de la salida. Quita los códigos de lo que se le escribe a la clienta
   («el *V50*» → «el *Vestido Salome*», «*V50* Vestido Salome» → «*Vestido Salome*», «Escribe *V50* para pedirlo» → «Dime tu
   talla y te lo separo»). El código sigue viajando por dentro (herramientas, pedido, CRM); solo deja de decirse.
   `KD_SIN_CODIGOS=0` lo apaga.
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field

TIPOS = ("vestido", "conjunto", "blusa", "pantalon", "falda", "enterizo", "blazer", "polo", "jean", "jeans", "set")
# Palabra que, delante del nombre, dice que se habla de una prenda: «el Sol», «modelo Luz», «vestido Alma», «tu Lila».
DETERMINANTES = {"el", "la", "del", "al", "un", "una", "ese", "esa", "este", "esta", "aquel", "aquella", "modelo", "mi", "tu",
                 "su", "ese", "esa", "los", "las", *TIPOS, "pantalones"}
# Nombres de prenda que también son palabras corrientes o colores en el chat. Lista a mano: la clienta las usa sin hablar
# de la prenda («con toda el alma», «hace sol», «la luz», «en lila», «nieve», «una paloma», «cristal», «rosa»).
COMUNES = {"alma", "sol", "luz", "lila", "nieve", "paloma", "cristal", "rose", "rosa", "eva", "ambar", "aurora", "margarita",
           "narciso", "tulipan", "amapola", "verona", "milan", "tokio", "riviera", "petra", "dakota", "isis", "donna", "merida",
           "athena", "bianca", "charlotte", "sophia", "camila", "samara", "selma", "nala"}
# Giros donde la palabra común NO es la prenda aunque lleve artículo: «con toda el alma», «a la luz de», «de color lila».
RE_GIROS = re.compile(r"\b(tod[oa] el alma|en el alma|del alma|hace sol|el sol (sale|esta|pega|quema)|bajo el sol|al sol|"
                      r"a la luz|la luz (de|del|se|no)|(color|tono|en|de) (lila|rosa|cristal|nieve)|rosa (claro|palo|pastel|viejo)|"
                      r"palo rosa|paloma mensajera|la paloma de)\b")
RE_CODIGO = re.compile(r"\b(V)\s*-?\s*(\d{1,3})\b", re.I)
SIN_CODIGOS = os.environ.get("KD_SIN_CODIGOS", "1") != "0"


def plano(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").strip()


def propio(nombre: str) -> str:
    """El nombre sin el tipo: «Vestido Zoraida Clásico» → «zoraida clasico»."""
    return " ".join(p for p in plano(nombre).split() if p not in TIPOS)


def tipo(nombre: str) -> str:
    return next((p for p in plano(nombre).split() if p in TIPOS), "")


def articulo(nombre: str) -> str:
    return "la" if tipo(nombre) in ("blusa", "falda") else "el"


def codigo_canon(c: str) -> str:
    m = RE_CODIGO.fullmatch((c or "").strip())
    return f"V{int(m.group(2)):02d}" if m else (c or "").strip().upper()


@dataclass
class Lectura:
    codigos: list[str] = field(default_factory=list)            # resueltas, en orden de aparición
    ambiguas: list[tuple[str, list[str]]] = field(default_factory=list)   # (nombre escrito, códigos posibles)

    def __bool__(self) -> bool:
        return bool(self.codigos)


def _con_contexto(plano_txt: str, ini: int, fin: int, contesta_cual: bool) -> bool:
    """Un nombre común cuenta solo si va con determinante o tipo delante, es todo el mensaje o contesta a «¿cuál?»."""
    if any(g.start() <= ini < g.end() for g in RE_GIROS.finditer(plano_txt)):
        return False
    antes = plano_txt[:ini].split()
    if antes and antes[-1] in DETERMINANTES:
        return True
    if contesta_cual or not plano_txt[:ini].strip() and not plano_txt[fin:].strip(" .!?¡¿"):
        return True
    return False


def nombradas(texto: str, fichas, ctx: dict | None = None) -> Lectura:
    """Qué prendas nombra `texto`. `ctx`: {"foco": código en foco, "bot": último texto del bot, "contesta_cual": bool}.
    Un nombre dentro de otro más largo («Zoraida» en «Zoraida Clásico») vale el largo. Los códigos escritos valen siempre."""
    ctx = ctx or {}
    p = plano(texto)
    if not p:
        return Lectura()
    fichas = list(fichas)
    por_propio: dict[str, list] = {}
    for x in fichas:
        por_propio.setdefault(propio(x.nombre), []).append(x)
    hallados: list[tuple[int, int, str, list[str]]] = []      # (ini, fin, clave, códigos candidatos)
    for clave, xs in por_propio.items():
        if len(clave) < 3:
            continue
        for m in re.finditer(r"\b" + re.escape(clave) + r"\b", p):
            if clave in COMUNES and not _con_contexto(p, m.start(), m.end(), bool(ctx.get("contesta_cual"))):
                continue
            cands = xs
            antes = p[:m.start()].split()
            if len(xs) > 1 and antes and antes[-1] in TIPOS + ("pantalones",):   # «pantalón Holly»
                t = antes[-1].rstrip("es") if antes[-1] == "pantalones" else antes[-1]
                cands = [x for x in xs if tipo(x.nombre) == t] or xs
            hallados.append((m.start(), m.end(), clave, [x.codigo.upper() for x in cands]))
        # el nombre completo con su tipo («vestido holly») también cuenta y, por largo, decide
        for x in xs:
            completo = plano(x.nombre)
            if completo != clave:
                for m in re.finditer(r"\b" + re.escape(completo) + r"\b", p):
                    hallados.append((m.start(), m.end(), completo, [x.codigo.upper()]))
    for x in fichas:
        for m in re.finditer(r"\b" + re.escape(x.codigo.lower()) + r"\b", p):
            hallados.append((m.start(), m.end(), x.codigo.lower(), [x.codigo.upper()]))
    hallados.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    out, fin = Lectura(), -1
    foco = (ctx.get("foco") or "").upper()
    bot = plano(ctx.get("bot") or "")
    for ini, f_, clave, cands in hallados:
        if ini < fin:
            continue
        fin = f_
        if len(cands) > 1:
            # repetido sin tipo: la prenda en foco, o la única que el bot acaba de nombrar; si no, se pregunta
            if foco in cands:
                cands = [foco]
            else:
                en_bot = [c for c in cands if next((x for x in fichas if x.codigo.upper() == c and plano(x.nombre) in bot), None)]
                if len(en_bot) == 1:
                    cands = en_bot
        if len(cands) > 1:
            if (clave, cands) not in out.ambiguas:
                out.ambiguas.append((clave, cands))
            continue
        if cands[0] not in out.codigos:
            out.codigos.append(cands[0])
    return out


def pregunta_cual(ambigua: tuple[str, list[str]], fichas) -> str:
    """«Tenemos el Vestido Holly y el Pantalón Holly 😊 ¿Cuál de los dos te interesa?»"""
    nombres = [next((x.nombre for x in fichas if x.codigo.upper() == c), c) for c in ambigua[1]]
    lista = ", ".join(f"{articulo(n)} {n}" for n in nombres[:-1]) + f" y {articulo(nombres[-1])} {nombres[-1]}"
    cual = "de los dos" if len(nombres) == 2 else "de ellos"
    return f"Tenemos {lista} 😊 ¿Cuál {cual} te interesa?"


# --- salida: sin códigos --------------------------------------------------------------------------------------------

RE_ESCRIBE = re.compile(r"👉?\s*Escribe\s+\*?V\s*-?\s*\d{1,3}\*?\s+para\s+pedirlo\.?", re.I)
RE_COD_SUELTO = re.compile(r"(\*?)\b(V\s*-?\s*\d{1,3})\b(\*?)")


def sin_codigos(texto: str, fichas) -> str:
    """Cambia cada código del catálogo por el nombre de su prenda. Un código que no está en el catálogo se deja (el
    validador de V3 ya rechaza los inventados)."""
    if not texto or not SIN_CODIGOS:
        return texto
    nombre_de = {x.codigo.upper(): x.nombre for x in fichas}
    texto = RE_ESCRIBE.sub("👉 Dime tu talla y te lo separo 😊", texto)

    def cambio(m: re.Match) -> str:
        cod = codigo_canon(m.group(2))
        nombre = nombre_de.get(cod)
        if not nombre:
            return m.group(0)
        bold = "*" if (m.group(1) or m.group(3)) else ""
        resto = texto[m.end():]
        # «*V50* Vestido Salome» / «V50 — Vestido Salome»: el nombre ya está, solo sobra el código
        sig = re.match(r"[\s*—–-]*(" + re.escape(nombre) + r")\b\*?", resto, re.I)
        if sig:
            cambio.saltar = m.end() + sig.end()
            return f"{bold}{nombre}{bold}"
        return f"{bold}{nombre}{bold}"

    cambio.saltar = -1
    out, pos = [], 0
    for m in RE_COD_SUELTO.finditer(texto):
        if m.start() < pos:
            continue
        out.append(texto[pos:m.start()])
        out.append(cambio(m))
        pos = max(m.end(), cambio.saltar)
        cambio.saltar = -1
    out.append(texto[pos:])
    t = "".join(out)
    # «el *Vestido Salome* Vestido Salome» no debería quedar; por si acaso, se funde
    for nombre in nombre_de.values():
        t = re.sub(r"(\*?" + re.escape(nombre) + r"\*?)\s+" + re.escape(nombre) + r"\b", r"\1", t)
    return t


def respuesta_sin_codigos(res: dict, fichas) -> dict:
    """Aplica `sin_codigos` a todo lo que la clienta lee de una respuesta del agente: el texto, los pies y títulos de las
    fotos. Los campos internos (`codigo`, `sugerencias[].codigo`, `tallas[].codigo`) no se tocan."""
    if not SIN_CODIGOS or not isinstance(res, dict):
        return res
    if isinstance(res.get("respuesta"), str):
        res["respuesta"] = sin_codigos(res["respuesta"], fichas)
    for s in res.get("sugerencias") or []:
        if isinstance(s, dict):
            for k in ("pie", "titulo"):
                if isinstance(s.get(k), str):
                    s[k] = sin_codigos(s[k], fichas)
    return res
