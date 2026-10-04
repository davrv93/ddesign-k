"""Carga de los datasets de BOT.zip y del catálogo, en una forma común.

Todo lo que entrena o consulta el agente sale de aquí, para que el índice que se
hornea en la imagen (entrenar.py) y el que usa el servidor (main.py) lean igual.
"""
from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass, field

DATA_DIR = os.environ.get("AGENTE_DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))

# tipo_pregunta de la rúbrica -> intención del agente
RUBRICA_A_INTENCION = {
    "descripcion": "producto_descripcion",
    "comparacion": "producto_comparacion",
    "recomendacion": "producto_recomendacion",
    "tallas_y_material": "producto_tallas_material",
    "informacion_no_disponible_precio": "consulta_precio",
    "informacion_no_disponible_stock": "consulta_stock",
    "informacion_no_disponible_entrega": "consulta_entrega",
    "informacion_no_disponible_medidas": "consulta_medidas",
    "informacion_no_disponible_marca": "consulta_marca",
}

# categoria del dataset conversacional -> intención (la "pregunta" genérica no es de la tienda)
CONVERSACIONAL_A_INTENCION = {"pregunta": "pregunta_general"}

# Intenciones de intenciones_tienda.csv que no vienen de BOT.zip: ayuda («tengo dudas»), como_comprar,
# tienda_info (dirección, horario, pago, cambios). Las contesta el LLM con los datos de TIENDA.

# Intenciones que el bot Go resuelve con su propio flujo determinista.
ACCIONES = {"catalogo", "foto", "pedido_estado", "asesora"}

CATEGORIAS_PRENDA = ["vestido", "polo", "blusa", "jeans"]


def _csv(nombre: str) -> list[dict]:
    with open(os.path.join(DATA_DIR, nombre), encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


@dataclass
class Ejemplo:
    texto: str
    intencion: str
    variante: str = ""
    respuesta: str = ""
    contexto: str = ""
    grupo: str = ""


def ejemplos_intencion() -> list[Ejemplo]:
    """Ejemplos etiquetados para el clasificador de intención."""
    out: list[Ejemplo] = []
    for r in _csv("conversacional_500.csv"):
        cat = r["categoria"].strip()
        out.append(Ejemplo(
            texto=r["mensaje"].strip(),
            intencion=CONVERSACIONAL_A_INTENCION.get(cat, cat),
            variante=r["variante"].strip(),
            respuesta=r["respuesta_sugerida"].strip(),
            contexto=r["contexto"].strip(),
            grupo=r["grupo_id"].strip(),
        ))
    for r in _csv("rubrica_500.csv"):
        out.append(Ejemplo(
            texto=r["pregunta"].strip(),
            intencion=RUBRICA_A_INTENCION[r["tipo_pregunta"].strip()],
            respuesta=r["respuesta_referencia"].strip(),
            grupo=r["id"].strip(),
        ))
    for i, r in enumerate(_csv("intenciones_tienda.csv")):
        out.append(Ejemplo(texto=r["mensaje"].strip(), intencion=r["intencion"].strip(),
                           variante=r["variante"].strip(), grupo=f"T{i // 3:03d}"))
    return out


def ejemplos_categoria() -> list[tuple[str, str, str]]:
    """(texto, categoria_prenda, tipo_prueba) del dataset del juez."""
    return [(r["pregunta"].strip(), r["categoria_esperada"].strip(), r["tipo_prueba"].strip())
            for r in _csv("juez_prendas_500.csv")]


# ---------------------------------------------------------------------------
# Catálogo

@dataclass
class Ficha:
    codigo: str
    nombre: str
    categoria: str
    color: str
    detalle: str
    tallas: str
    fuente: str               # "seed" (tienda real) | "catalogo100" (ficticio, sin precio ni stock)
    precio: float | None = None
    stock: dict = field(default_factory=dict)
    imagen: str = ""
    tejido: str = ""

    def texto(self) -> str:
        """Lo que se indexa y lo que ve el LLM: sólo lo semiestático (diseño, color, precio)."""
        partes = [f"{self.codigo} · {self.nombre}", f"categoría: {self.categoria}", f"color: {self.color}",
                  f"características: {self.detalle}"]
        if self.tejido:
            partes.append(f"tejido propuesto: {self.tejido}")
        # Sin stock a propósito: el stock es dato vivo y se consulta aparte (app/stock.py).
        # Así el embedding no cambia cuando cambia el inventario.
        if self.fuente == "seed":
            partes.append(f"precio: S/ {self.precio:.2f}" if self.precio is not None else "precio: no registrado")
            partes.append(f"tallas del modelo: {self.tallas}")
        else:
            partes.append(f"tallas sugeridas: {self.tallas}")
            if self.precio is not None:  # precio de seed/precios_catalogo100.json, puesto al arrancar
                partes.append(f"precio: S/ {self.precio:.2f}")
                partes.append("marca, medidas y entrega: NO documentados")
            else:
                partes.append("precio, marca, medidas y entrega: NO documentados")
        if self.imagen:
            partes.append(f"imagen: {self.imagen}")
        return " | ".join(partes)


def _cat_prenda(texto: str) -> str:
    t = texto.lower()
    for c, claves in (("jeans", ("jean",)), ("polo", ("polo",)), ("blusa", ("blusa",)), ("vestido", ("vestido",))):
        if any(k in t for k in claves):
            return c
    return ""


def fichas_catalogo100() -> list[Ficha]:
    with open(os.path.join(DATA_DIR, "caracteristicas_y_tallas.txt"), encoding="utf-8-sig") as f:
        txt = f.read()
    out = []
    for bloque in re.split(r"\n-{10,}\n|\n={10,}\n", txt):
        campos = dict(re.findall(r"^([A-ZÁÉÍÓÚa-záéíóú ]+):\s*(.+)$", bloque, re.M))
        if "CÓDIGO" not in campos:
            continue
        out.append(Ficha(
            codigo=campos["CÓDIGO"].strip(),
            nombre=campos.get("Modelo", "").strip(),
            categoria=campos.get("Categoría", "").strip(),
            color=campos.get("Color", "").strip(),
            detalle=campos.get("Características", "").strip().rstrip("."),
            tallas=campos.get("Tallas sugeridas", "").strip(),
            tejido=campos.get("Tejido propuesto", "").strip().rstrip("."),
            imagen=campos.get("Archivo", "").strip(),
            fuente="catalogo100",
        ))
    return out


def productos_seed() -> list[dict]:
    with open(os.path.join(DATA_DIR, "catalogo_seed.json"), encoding="utf-8") as f:
        return json.load(f)


def fichas_seed(productos: list[dict] | None = None) -> list[Ficha]:
    """Catálogo real de la tienda. `productos` viene de /api/public/catalog; si no, del seed."""
    if productos is None:
        with open(os.path.join(DATA_DIR, "catalogo_seed.json"), encoding="utf-8") as f:
            productos = json.load(f)
    out = []
    for p in productos:
        stock = p.get("sizes")  # seed: {"S": 3}; catálogo público: ["S", "M"] (solo tallas con stock)
        if isinstance(stock, list):
            stock = {t: None for t in stock}
        if stock is None and p.get("variants") is not None:
            stock = {v.get("size", "?"): v.get("stock", 0) for v in p["variants"]}
        out.append(Ficha(
            codigo=str(p.get("code", "")).upper(),
            nombre=p.get("name", ""),
            categoria=p.get("category", ""),
            color=p.get("color", ""),
            detalle=", ".join(x for x in (p.get("description", ""), p.get("tags") or p.get("ai_tags", "")) if x),
            tallas=", ".join(stock or {}),
            precio=p.get("price"),
            stock=stock or {},
            imagen=p.get("image", ""),
            fuente="seed",
        ))
    return out


RE_CODIGO = re.compile(r"\b((?:VES|POL|BLU|JEA)\s*-?\s*\d{1,3}|V\s*-?\s*\d{1,2})\b", re.I)


def codigos_en(texto: str) -> list[str]:
    """Códigos mencionados, normalizados a la forma del catálogo (VES-001, V05)."""
    out = []
    for m in RE_CODIGO.findall(texto or ""):
        m = re.sub(r"[\s-]", "", m.upper())
        pref, num = re.match(r"([A-Z]+)(\d+)", m).groups()
        cod = f"{pref}-{int(num):03d}" if len(pref) == 3 else f"V{int(num):02d}"
        if cod not in out:
            out.append(cod)
    return out


def categoria_por_nombre(ficha: Ficha) -> str:
    return _cat_prenda(ficha.categoria + " " + ficha.nombre) or "vestido"
