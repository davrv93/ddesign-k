"""Resolvedor DETERMINISTA de referencias contextuales («ese», «el otro», «el segundo», «el rojo», «el de la foto»).

    resolver_referencia("el otro", [{"codigo": "V35", "nombre": "Vestido Irla", "color": "negro"},
                                    {"codigo": "V42", "nombre": "Vestido Gala", "color": "azul marino"}])
    -> Resolucion(tipo="candidato", candidato=<V35>, motivo="otro: el que no es el último")

Contrato de `candidatos`: lista de dicts con `codigo`, `nombre`, `color` (y opcionales `foto`/`origen`, `precio`) en ORDEN
CRONOLÓGICO: el índice 0 es la prenda que se mostró/mencionó primero y el último es la más reciente («ese» = el último).
Sin dependencias (ni numpy ni modelo). No clasifica: se asume que el texto YA es una referencia (la clasificación la hace el
catálogo `referencias_contextuales`). Devuelve:

    tipo = "candidato"  → `candidato` es la prenda resuelta
    tipo = "ambiguo"    → `empatados` son las prendas que quedan (el agente debe preguntar «¿cuál: …?»)
    tipo = "ninguno"    → no hay pista, no hay candidatos o la pista no coincide con ninguno; `motivo` dice por qué
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

# --- normalización -------------------------------------------------------------------------------------------------


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFD", (texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"(.)\1{2,}", r"\1", t)             # «eseee» → «ese», «rojooo» → «rojo»
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# --- colores -------------------------------------------------------------------------------------------------------
# canon → (familia, alias). El alias con «o» final genera su femenino con «a». Las equivalencias van en el mismo canon:
# guinda ≈ borgoña ≈ vino ≈ burdeos ≈ granate ≈ «rojo oscuro».
_COLORES = {
    "rojo": ("rojo", ["rojo", "colorado", "rojo fuego", "rojo intenso", "rojo rojo", "rojito"]),
    "vino": ("rojo", ["vino", "guinda", "borgona", "burdeos", "bordo", "granate", "tinto", "marsala", "rojo vino", "rojo guinda",
                      "rojo oscuro", "rojo borgona", "rojo burdeos", "rojo granate", "color vino"]),
    "rosa": ("rosa", ["rosa", "rosado", "palo rosa", "rosa palo", "rosa pastel", "rosa viejo", "rosa claro", "rosadito"]),
    "fucsia": ("rosa", ["fucsia", "fuxia", "rosa fuerte", "rosa chicle", "magenta"]),
    "coral": ("coral", ["coral", "salmon", "durazno", "melocoton"]),
    "naranja": ("naranja", ["naranja", "anaranjado", "naranjado"]),
    "amarillo": ("amarillo", ["amarillo", "mostaza", "limon"]),
    "verde": ("verde", ["verde", "verde claro", "verde lima"]),
    "verde_botella": ("verde", ["verde botella", "verde oscuro", "esmeralda", "verde esmeralda", "verde bosque", "verde jade"]),
    "verde_olivo": ("verde", ["verde olivo", "olivo", "oliva", "verde oliva", "verde militar", "militar", "verde musgo"]),
    "menta": ("verde", ["menta", "verde menta", "verde agua", "verde pastel", "aqua"]),
    "turquesa": ("azul", ["turquesa", "turquez"]),
    "azul": ("azul", ["azul", "azul claro"]),
    "celeste": ("azul", ["celeste", "azul cielo", "azul bebe"]),
    "azul_marino": ("azul", ["azul marino", "azul noche", "marino", "navy", "azul oscuro", "azul casi negro", "azul petroleo"]),
    "azul_rey": ("azul", ["azul rey", "azul electrico", "azul royal", "royal", "azulino"]),
    "morado": ("morado", ["morado", "violeta", "purpura", "uva"]),
    "lila": ("morado", ["lila", "lavanda", "malva", "lilas"]),
    "negro": ("negro", ["negro", "negrito"]),
    "blanco": ("blanco", ["blanco", "blanquito", "blanco nieve"]),
    "crema": ("crema", ["crema", "hueso", "ivory", "marfil", "blanco roto", "off white", "ostion", "perla"]),
    "nude": ("nude", ["nude", "piel", "color piel", "beige", "arena", "carne", "nudes"]),
    "champagne": ("dorado", ["champagne", "champan", "champana"]),
    "dorado": ("dorado", ["dorado", "oro", "golden", "cobre", "bronce"]),
    "plateado": ("plata", ["plateado", "plata", "silver"]),
    "gris": ("gris", ["gris", "plomo", "plomito", "grafito", "gris oscuro", "gris claro"]),
    "marron": ("marron", ["marron", "cafe", "chocolate", "camel", "tabaco", "caramelo"]),
}
_FAMILIA = {c: f for c, (f, _) in _COLORES.items()}


def _alias_colores() -> list:
    pares = []
    for canon, (_, alias) in _COLORES.items():
        for a in alias:
            for forma in {a, a[:-1] + "a" if a.endswith("o") else a, a + "s", a[:-1] + "os" if a.endswith("o") else a}:
                pares.append((tuple(forma.split()), canon))
    pares.sort(key=lambda p: -len(p[0]))        # la coincidencia más larga primero: «rojo vino» antes que «rojo»
    return pares


_ALIAS = _alias_colores()


def colores_en(texto_normalizado: str) -> list:
    """Colores canónicos mencionados, con su posición (token inicial): [(canon, i)]."""
    toks = texto_normalizado.split()
    usados = [False] * len(toks)
    salida = []
    for alias, canon in _ALIAS:
        n = len(alias)
        for i in range(len(toks) - n + 1):
            if tuple(toks[i:i + n]) == alias and not any(usados[i:i + n]):
                for j in range(i, i + n):
                    usados[j] = True
                salida.append((canon, i))
    return sorted(salida, key=lambda p: p[1])


def _nivel_color(canon_texto: str, colores_cand: set) -> int:
    """2 = el mismo tono (o equivalente); 1 = misma familia; 0 = no coincide."""
    if canon_texto in colores_cand:
        return 2
    fam = _FAMILIA[canon_texto]
    return 1 if any(_FAMILIA[c] == fam for c in colores_cand) else 0


# --- pistas del texto ----------------------------------------------------------------------------------------------
_ORDINALES = {
    "primero": 0, "primera": 0, "primer": 0, "1ro": 0, "1ero": 0, "1ra": 0, "1er": 0,
    "segundo": 1, "segunda": 1, "2do": 1, "2da": 1, "2ndo": 1,
    "tercero": 2, "tercera": 2, "tercer": 2, "3ro": 2, "3ra": 2, "3er": 2,
    "cuarto": 3, "cuarta": 3, "4to": 3, "4ta": 3,
    "quinto": 4, "quinta": 4, "5to": 4, "5ta": 4,
}
_ULTIMO = {"ultimo", "ultima", "final", "ultimito", "ultimita"}
_PENULTIMO = {"penultimo", "penultima", "anterior", "antepenultimo"}
_GENERICAS_NOMBRE = {"vestido", "vestidos", "conjunto", "conjuntos", "blusa", "falda", "enterizo", "pantalon", "set", "modelo",
                     "de", "la", "el", "los", "las", "con", "y", "en", "para", "del", "un", "una", "mini", "maxi", "midi", "largo",
                     "corto", "talla", "baruka", "design"}

RE_FOTO = re.compile(r"\b(foto|fotos|fotito|fotografia|imagen|imagencita|captura|pantallazo|screenshot)\b")
RE_FOTO_CLIENTA = re.compile(r"\b(que te (?:mande|envie|pase|mostre|subi)|de mi foto|que (?:mande|envie|pase)|mi foto|mi captura)\b")
OTRO_P = (r"(?:(?:el|la|del|al|de la|a la|de ese|de esa|con el|con la|por el|por la|ese|esa|aquel|aquella)\s+otr[oa]"
          r"|otr[oa]\s+(?:vestido|prenda|modelo|conjunto))")
ESE_P = r"(?:ese mismo|esa misma|el mismito|el mismo|la misma|ese|esa|este|esta|esto|eso)"
RE_OTRO = re.compile(r"\b" + OTRO_P + r"\b")
RE_ESE = re.compile(r"\b" + ESE_P + r"\b")
RE_PLURAL = re.compile(r"\b(ambos|ambas|todos|todas|los dos|las dos|los tres|las tres|los otros|las otras|esos|esas|estos|estas)\b")
RE_MOSTRADO = re.compile(r"\b(el|la) que (?:me |nos )?(?:mostraste|ensenaste|mandaste|pasaste|enviaste|mostro|mandaron)\b")
RE_ARRIBA = re.compile(r"\b(arriba|encima|tope)\b")
RE_ABAJO = re.compile(r"\b(abajo|debajo|fondo)\b")
RE_PRINCIPIO = re.compile(r"\b(al principio|de entrada|el inicial|al inicio)\b")
RE_MEDIO = re.compile(r"\b(de en medio|del medio|del centro|de en el medio|de enmedio|de al medio|al medio|en medio|en el medio)\b")
RE_ANTES = re.compile(r"\b(?:de|del|al)\s+antes\b|\b(?:el|la) que (?:\w+\s+){0,3}?antes\b|\bhace (?:un |unos )?(?:rato|ratito|momento|momentos|minutos)\b|\bde ayer\b")
RE_RECIEN = re.compile(r"\b(de recien|recien|ahorita|ahora mismo|de ahora)\b")
RE_NUMKW = re.compile(r"\b(?:numero|nro|opcion|modelo|vestido|foto|imagen)\s*(?:#|no)?\s*([1-9]|uno|dos|tres|cuatro|cinco)\b")
_NUMPAL = {"uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5}
RE_PARTITIVO = re.compile(r"\b(?:de|entre|del)\s+(?:todos|todas|los\s+(?:dos|tres|cuatro)|las\s+(?:dos|tres|cuatro))\b")
RE_NEGADO = re.compile(r"\b(no|ni|tampoco)\b")
RE_NUM_SUELTO = re.compile(r"\b(?:el|la|ese|esa|del|al)\s+([1-9])\b(?!\s*\d)")
RE_BARATO = re.compile(r"\b(mas barato|mas economico|mas baratito)\b")
RE_CARO = re.compile(r"\b(mas caro)\b")


def _negado(clausula: str, patron: str) -> bool:
    """¿Está negado el demostrativo/«el otro» dentro de la cláusula? «no ese», «no quiero el otro», «ese no», «ese vestido no»."""
    antes = rf"\b(?:no|ni|tampoco)\s+(?:(?:quiero|me gusta|es|era|me llevo|me lo llevo|ese|el|la)\s+)*{patron}"
    despues = rf"{patron}\s+(?:\w+\s+)?(?:no|tampoco)\b"
    return bool(re.search(antes, clausula) or re.search(despues, clausula))


def _clausulas(texto: str) -> list:
    partes = re.split(r"[,.;:!?¡¿\n]+|\bpero\b|\bsino\b|\by no\b", (texto or "").lower())
    return [c for c in (normalizar(p) for p in partes) if c]


@dataclass
class Resolucion:
    tipo: str                                   # candidato | ambiguo | ninguno
    candidato: Optional[dict] = None
    empatados: list = field(default_factory=list)
    motivo: str = ""
    nivel: str = ""                             # alta | media (media = coincidió solo la familia del color, o sin 100 %)
    pistas: dict = field(default_factory=dict)

    def a_dict(self) -> dict:
        return {"tipo": self.tipo, "candidato": self.candidato, "empatados": self.empatados, "motivo": self.motivo,
                "nivel": self.nivel, "pistas": self.pistas}


def _codigo_norm(c: str) -> tuple:
    m = re.match(r"^([a-z]*)0*(\d+)$", re.sub(r"[^a-z0-9]", "", (c or "").lower()))
    return (m.group(1), m.group(2)) if m else (re.sub(r"[^a-z0-9]", "", (c or "").lower()), "")


def _mencion_codigo(norm: str, codigo: str) -> bool:
    letras, nums = _codigo_norm(codigo)
    if not nums:
        return bool(letras) and re.search(rf"\b{re.escape(letras)}\b", norm) is not None
    if letras:
        # con 2+ dígitos, «el 42» basta para «V42»; con un dígito («el 2») es un ordinal, así que exige la letra
        prefijo = rf"(?:{re.escape(letras)}\s*)?" if len(nums) >= 2 else rf"{re.escape(letras)}\s*"
        return re.search(rf"\b{prefijo}0*{nums}\b", norm) is not None
    return re.search(rf"\b0*{nums}\b", norm) is not None


def _tokens_nombre(cand: dict) -> set:
    colores = {tok for canon, (_, al) in _COLORES.items() for a in al for tok in a.split()}
    toks = set(normalizar(cand.get("nombre", "")).split())
    return {t for t in toks if len(t) >= 3 and t not in _GENERICAS_NOMBRE and t not in colores and not t.isdigit()}


def _colores_candidato(cand: dict) -> set:
    base = normalizar(cand.get("color") or "") or normalizar(cand.get("nombre") or "")
    return {c for c, _ in colores_en(base)}


def _es_de_foto(cand: dict) -> bool:
    return bool(cand.get("foto") or cand.get("foto_clienta") or cand.get("origen") in ("foto_clienta", "foto"))


_UNION = {"el", "la", "o", "u", "y", "e", "ese", "esa", "de", "del", "los", "las", "que", "al"}


def _ordinal_token(t: str):
    if t in _ORDINALES:
        return _ORDINALES[t]
    if t in _ULTIMO:
        return -1
    if t in _PENULTIMO:
        return -2
    return None


def _posiciones(norm: str, toks: list, hay_codigo: bool) -> list:
    """Pistas de posición del texto: lista de ("ord", k) (k índice; negativo = desde el final) o ("medio", 0).
    «el primero o el último» da dos; «el segundo que me mandaste primero» da solo la primera (la segunda es un adverbio)."""
    anclas, ultimo_i = [], None
    for i, t in enumerate(toks):
        k = _ordinal_token(t)
        if k is None:
            continue
        if ultimo_i is None or (i - ultimo_i <= 4 and all(x in _UNION for x in toks[ultimo_i + 1:i]) and
                                any(x in ("o", "u", "y", "e") for x in toks[ultimo_i + 1:i])):
            anclas.append(("ord", k))
            ultimo_i = i
    if anclas:
        return anclas
    m = RE_NUMKW.search(norm)
    if m:
        v = m.group(1)
        return [("ord", (int(v) if v.isdigit() else _NUMPAL[v]) - 1)]
    m = RE_NUM_SUELTO.search(norm)
    if m and not hay_codigo:
        return [("ord", int(m.group(1)) - 1)]
    if RE_MEDIO.search(norm):
        return [("medio", 0)]
    if RE_PRINCIPIO.search(norm):
        return [("ord", 0)]
    if RE_ARRIBA.search(norm):
        return [("ord", 0)]
    if RE_ABAJO.search(norm):
        return [("ord", -1)]
    if RE_ANTES.search(norm):          # «el de antes», «el que me mostraste hace rato»: el anterior al último
        return [("ord", -2)]
    return []


def resolver_referencia(texto: str, candidatos: list) -> Resolucion:
    """Resuelve la referencia de `texto` contra `candidatos` (ver el contrato en la cabecera del módulo)."""
    norm = normalizar(texto)
    cands = [c for c in (candidatos or []) if isinstance(c, dict)]
    pistas: dict = {}
    if not cands:
        return Resolucion("ninguno", motivo="sin_candidatos", pistas=pistas)
    if not norm:
        return Resolucion("ninguno", motivo="texto_vacio", pistas=pistas)
    toks = norm.split()
    clausulas = _clausulas(texto)
    S = list(range(len(cands)))                  # índices vivos, en orden cronológico
    nivel = "alta"
    motivos = []

    def cerrar(tipo, idx, motivo):
        sel = [cands[i] for i in idx]
        return Resolucion(tipo, sel[0] if tipo == "candidato" else None, sel if tipo != "ninguno" else [], motivo,
                          nivel if tipo != "ninguno" else "", pistas)

    # 0) plural: «los dos», «ambos», «los otros»: no es una sola prenda (salvo «el segundo de los dos»)
    if RE_PLURAL.search(RE_PARTITIVO.sub(" ", norm)) and not any(t in _ORDINALES or t in _ULTIMO or t in _PENULTIMO for t in toks):
        pistas["plural"] = True
        return Resolucion("ninguno", motivo="plural: pide varias prendas a la vez", pistas=pistas)

    # 1) código explícito («el V42», «el 42»)
    por_codigo = [i for i in S if _mencion_codigo(norm, cands[i].get("codigo", ""))]
    if por_codigo:
        pistas["codigo"] = [cands[i].get("codigo") for i in por_codigo]
        S = por_codigo
        motivos.append("código")
    else:
        # un código con forma de código (V99) que no está entre los candidatos
        m = re.search(r"\bv\s*-?\s*0*(\d{1,4})\b", norm)
        if m:
            pistas["codigo_no_listado"] = "V" + m.group(1)
            return Resolucion("ninguno", motivo=f"el código V{m.group(1)} no está entre las prendas de la conversación", pistas=pistas)

    # 2) nombre («el Irla»)
    if not por_codigo:
        por_nombre = [i for i in S if _tokens_nombre(cands[i]) & set(toks)]
        if por_nombre:
            pistas["nombre"] = [cands[i].get("nombre") for i in por_nombre]
            S = por_nombre
            motivos.append("nombre")

    anclas = _posiciones(norm, toks, bool(por_codigo))
    ancla = anclas[0] if anclas else None

    # 3) «el de la foto» (si hay posición, «la segunda foto» es solo el sustantivo: manda la posición)
    if (RE_FOTO.search(norm) or RE_FOTO_CLIENTA.search(norm)) and not (ancla and RE_FOTO.search(norm) and not RE_FOTO_CLIENTA.search(norm)):
        con_foto = [i for i in S if _es_de_foto(cands[i])]
        pistas["foto"] = True
        if con_foto:
            S = con_foto
            motivos.append("foto de la clienta")
        elif RE_FOTO_CLIENTA.search(norm) or "mi foto" in norm:
            return Resolucion("ninguno", motivo="dice que mandó una foto pero no hay prenda de foto de la clienta", pistas=pistas)
        else:
            motivos.append("foto (todas las mostradas son fotos)")
            nivel = "media" if len(S) > 1 else nivel

    # 4) color (por cláusula: «no el rojo, el azul» descarta rojo y pide azul)
    negados, positivos = [], []
    for cl in clausulas:
        ctoks = cl.split()
        for canon, i in colores_en(cl):
            previo = ctoks[max(0, i - 4):i]
            (negados if any(t in ("no", "ni", "sin", "tampoco") for t in previo) else positivos).append(canon)
    if positivos:
        pistas["color"] = positivos

        def puntaje(i):
            cc = _colores_candidato(cands[i])
            ns = [_nivel_color(c, cc) for c in positivos]
            return (sum(1 for n in ns if n), sum(ns))

        pt = {i: puntaje(i) for i in S}
        mejor = max(pt.values())
        if mejor[0] == 0:
            return Resolucion("ninguno", motivo=f"ningún candidato es {'/'.join(positivos)}", pistas=pistas)
        S = [i for i in S if pt[i] == mejor]
        if mejor[1] < 2 * mejor[0]:
            nivel = "media"
        motivos.append("color")
    if negados:
        pistas["color_negado"] = negados
        resto = [i for i in S if not any(c in _colores_candidato(cands[i]) for c in negados)]
        if resto:
            S = resto
            motivos.append("color descartado")
        else:
            return Resolucion("ninguno", motivo="todos los candidatos tienen el color descartado", pistas=pistas)

    # 5) precio y posición (`ancla` ya calculada arriba)
    barato, caro = RE_BARATO.search(norm), RE_CARO.search(norm)
    if barato or caro:
        con_precio = [i for i in S if isinstance(cands[i].get("precio"), (int, float))]
        if con_precio:
            f = min if barato else max
            tope = f(cands[i]["precio"] for i in con_precio)
            S = [i for i in con_precio if cands[i]["precio"] == tope]
            motivos.append("precio")
            pistas["precio"] = "mas_barato" if barato else "mas_caro"
        else:
            return Resolucion("ninguno", motivo="pide por precio pero los candidatos no traen precio", pistas=pistas)
    if anclas:
        pistas["posicion"] = [a[1] if a[0] == "ord" else "medio" for a in anclas]

        def pick(base: list) -> list:
            n, elegidos = len(base), []
            for tipo, k in anclas:
                if tipo == "medio":
                    if n >= 3:
                        elegidos += [base[n // 2]] if n % 2 else [base[n // 2 - 1], base[n // 2]]
                elif -n <= k < n:
                    elegidos.append(base[k])
            return sorted(set(elegidos))

        sel = pick(S)
        if not sel and len(S) < len(cands):    # «el rojo, el segundo»: la posición vale sobre toda la lista si el color la contradice
            sel = [i for i in pick(list(range(len(cands)))) if i in S]
            if not sel:
                return Resolucion("ninguno", motivo="la posición y el filtro (color/nombre) se contradicen", pistas=pistas)
        if not sel:
            return Resolucion("ninguno", motivo=f"posición fuera de rango (hay {len(S)} prendas)", pistas=pistas)
        S = sel
        motivos.append("posición")

    # 6) deícticos: «el otro» (no el último) y «ese» (el último), con su negación («ese no» = el otro)
    votos = set()
    for cl in clausulas:
        sin_otro = RE_OTRO.sub(" ", cl)
        hay_otro, hay_ese = bool(RE_OTRO.search(cl)), bool(RE_ESE.search(sin_otro)) and not ancla
        if hay_otro and hay_ese:
            votos.add("otro" if _negado(cl, ESE_P) else "ambos")
        elif hay_otro:
            votos.add("ese" if _negado(cl, OTRO_P) else "otro")
        elif hay_ese:
            votos.add("otro" if _negado(cl, ESE_P) else "ese")
    if RE_MOSTRADO.search(norm):
        motivos.append("mostrado")
    if not votos and not ancla and RE_RECIEN.search(norm):
        votos.add("ese")
    if len(votos) > 1:
        pistas["deictico"] = "contradictorio"
        return cerrar("ambiguo", S, "dos pistas a la vez (ese y el otro)")
    voto = next(iter(votos), None)
    if voto == "ambos":              # «ese y el otro» sin negación: señala las dos
        pistas["deictico"] = "ese+otro"
        return cerrar("ambiguo", S, "dos pistas a la vez (ese y el otro)")
    if voto == "otro":
        pistas["deictico"] = "otro"
        if len(cands) == 1:
            return Resolucion("ninguno", motivo="«el otro» pero solo hay una prenda", pistas=pistas)
        # «el otro» = el que NO es el último mencionado (entre las que siguen vivas)
        restantes = [i for i in S if i != len(cands) - 1]
        if not restantes:
            return Resolucion("ninguno", motivo="«el otro»: la única que queda es la última mencionada", pistas=pistas)
        S = restantes
        motivos.append("otro: el que no es el último")
    elif voto == "ese":
        pistas["deictico"] = "ese"
        if len(S) > 1:
            S = [S[-1]]            # el más reciente de los que siguen vivos
        motivos.append("ese: el último mencionado")

    if not motivos:
        if any(a in norm for a in ("mas barato", "mas caro", "mangas", "escote", "brillo", "largo", "corto")):
            return Resolucion("ninguno", motivo="referencia por atributo: este resolvedor no mira atributos", pistas=pistas)
        return Resolucion("ninguno", motivo="sin_pista", pistas=pistas)
    if len(S) == 1:
        return cerrar("candidato", S, " + ".join(motivos))
    return cerrar("ambiguo", S, " + ".join(motivos) + f": {len(S)} prendas empatadas")
