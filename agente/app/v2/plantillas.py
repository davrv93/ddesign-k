"""Catálogo de plantillas semánticas (plantillas.yaml) y selector: decide QUÉ se dice con código, nunca con un modelo.

El selector mira el plan de V2 (acción, prenda, pregunta que toca) y lo que V1 acaba de entender en este turno (qué dato dio
ella: su nombre, la ocasión, una categoría que preguntó…) y arma un `Mensaje`: una lista de bloques, cada uno una plantilla con
sus datos. Los datos salen SOLO del backend estructurado (`Hechos`): nunca de un modelo ni de un texto libre.

Si falta un dato obligatorio, o no hay plantilla para la pregunta que V1 quiere hacer, el selector no inventa: lanza
`SinPlantilla` y habla V1."""
from __future__ import annotations

import os
import re
import zlib
from dataclasses import dataclass, field
from typing import Callable, Protocol

import yaml

from .delex import SLOT_RE, Protegidos

RUTA = os.path.join(os.path.dirname(__file__), "plantillas.yaml")
PART_RE = re.compile(r"(?<!\{)\{([a-z_]+)\}(?!\})")

# Etiquetas de «prohibido» y la regla determinista de la compuerta factual que las cubre (factual.LEXICOS o «estructura»). Una
# prueba comprueba que TODA etiqueta de plantillas.yaml está aquí: no hay una prohibición que nadie haga cumplir.
PROHIBIDO_A_REGLA: dict[str, tuple[str, ...]] = {
    "inventar_nombre_cliente": ("nombres propios",), "inventar_producto": ("prenda", "nombres propios"),
    "inventar_promocion": ("descuento o promoción",), "inventar_promociones": ("descuento o promoción",),
    "inventar_descuento": ("descuento o promoción",), "inventar_descuentos": ("descuento o promoción",),
    "falso_descuento": ("descuento o promoción",), "inventar_stock": ("stock o escasez",),
    "inventar_escasez": ("stock o escasez",), "falsa_urgencia": ("urgencia",), "prometer_reserva": ("stock o escasez",),
    "prometer_restock": ("stock o escasez", "fecha o plazo"), "prometer_reposicion": ("stock o escasez", "fecha o plazo"),
    "inventar_fecha_reposicion": ("fecha o plazo",), "inventar_talla": ("talla",), "inferir_talla": ("talla",),
    "decir_que_talla_le_quedara": ("talla",), "afirmar_talla_como_actual_sin_confirmacion": ("talla",),
    "agregar_tallas": ("talla",), "inventar_precio": ("precio",), "modificar_precio": ("precio",), "cambiar_precio": ("precio",),
    "inventar_cuotas": ("precio",), "agregar_cargos": ("precio",), "inventar_color": ("color",), "inventar_colores": ("color",),
    "asumir_color": ("color",), "agregar_colores": ("color",), "inventar_fecha": ("fecha o plazo",), "asumir_fecha": ("fecha o plazo",),
    "asumir_dia_noche": ("fecha o plazo",), "recomendar_producto_sin_validacion": ("prenda",),
    "mezclar_atributos_del_producto_anterior": ("prenda", "color", "tela o material"),
    "asociarlo_a_otro_producto_sin_confirmacion": ("prenda", "nombres propios"), "inventar_metodo_pago": ("medio de pago",),
    "inventar_direccion": ("ubicación",), "inventar_delivery": ("entrega o envío",), "prometer_tiempo_de_respuesta": ("fecha o plazo",),
    "inventar_intencion": ("estructura",), "hacer_multiples_preguntas": ("estructura",), "cambiar_de_tema": ("estructura",),
}


class SinPlantilla(Exception):
    """El código no tiene una plantilla segura para este turno: habla V1."""


@dataclass
class Plantilla:
    id: str
    tipo: str
    activa: bool
    acciones: list[str]
    forma: str
    partes: dict[str, list[str]]
    protegidos: list[str] = field(default_factory=list)
    prohibido: list[str] = field(default_factory=list)
    max_frases: int = 2
    clave: str | None = None
    objetivo: str = ""
    semantica: list[str] = field(default_factory=list)
    sin_modelo: bool = False
    id_spec: int | None = None
    fijas: dict[str, int] = field(default_factory=dict)


class Catalogo:
    def __init__(self, plantillas: dict[str, Plantilla], config: dict):
        self.plantillas = plantillas
        self.config = config

    def __getitem__(self, id_: str) -> Plantilla:
        return self.plantillas[id_]

    def por_clave(self, clave: str) -> list[Plantilla]:
        return [p for p in self.plantillas.values() if p.tipo == "pregunta" and p.clave == clave and p.activa]


def cargar(ruta: str = RUTA) -> Catalogo:
    with open(ruta, encoding="utf-8") as fh:
        y = yaml.safe_load(fh)
    out = {}
    for id_, d in (y.get("plantillas") or {}).items():
        out[id_] = Plantilla(
            id=id_, tipo=d["tipo"], activa=bool(d.get("activa", False)), acciones=list(d.get("acciones") or []), forma=d["forma"],
            partes={k: list(v) for k, v in (d.get("partes") or {}).items()}, protegidos=list(d.get("protegidos") or []),
            prohibido=list(d.get("prohibido") or []), max_frases=int(d.get("max_frases", 2)), clave=d.get("clave"),
            objetivo=d.get("objetivo", ""), semantica=list(d.get("semantica") or []), sin_modelo=bool(d.get("sin_modelo", False)),
            id_spec=d.get("id_spec"), fijas={k: int(v) for k, v in (d.get("fijas") or {}).items()})
    return Catalogo(out, y.get("config") or {})


def validar(cat: Catalogo) -> list[str]:
    """Errores de estructura del catálogo (vacío = bien). Se corre en las pruebas y al arrancar."""
    errores: list[str] = []
    for p in cat.plantillas.values():
        if p.tipo not in ("acuse", "pregunta", "mensaje"):
            errores.append(f"{p.id}: tipo desconocido {p.tipo!r}")
        marcadores = set(PART_RE.findall(p.forma))
        if marcadores - set(p.partes):
            errores.append(f"{p.id}: la forma usa partes que no existen: {sorted(marcadores - set(p.partes))}")
        usados: set[str] = set()
        for parte, variantes in p.partes.items():
            if not variantes:
                errores.append(f"{p.id}.{parte}: sin variantes")
            for v in variantes:
                usados |= set(SLOT_RE.findall(v))
        if usados - set(p.protegidos):
            errores.append(f"{p.id}: usa datos sin proteger: {sorted(usados - set(p.protegidos))}")
        if set(p.protegidos) - usados:
            errores.append(f"{p.id}: declara datos protegidos que nunca usa: {sorted(set(p.protegidos) - usados)}")
        for t in p.prohibido:
            if t not in PROHIBIDO_A_REGLA:
                errores.append(f"{p.id}: etiqueta «prohibido» sin regla en la compuerta: {t}")
        if p.tipo == "pregunta" and not p.clave:
            errores.append(f"{p.id}: una pregunta necesita su clave de V1")
        if p.activa and not p.acciones:
            errores.append(f"{p.id}: activa pero sin acciones")
    return errores


# ---------------------------------------------------------------------------------------------------------------------
# Armado

def _variante_ok(v: str, valores: dict[str, str]) -> bool:
    return all(valores.get(s) for s in SLOT_RE.findall(v))


def elegibles(p: Plantilla, parte: str, valores: dict[str, str]) -> list[int]:
    """Índices de las variantes de una parte cuyos datos existen."""
    return [i for i, v in enumerate(p.partes.get(parte, [])) if _variante_ok(v, valores)]


def _limpia(t: str) -> str:
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" +([.,;:!?])", r"\1", t)
    t = re.sub(r"[ \t]*\n[ \t]*", "\n", t)
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip()


@dataclass
class Bloque:
    plantilla: Plantilla
    valores: dict[str, str]                     # slot → valor REAL (del backend); no sale de aquí sin token

    def partes_con_opciones(self) -> dict[str, list[int]]:
        """Las partes donde hay algo que elegir: más de una variante elegible y que no estén fijas por la plantilla."""
        p = self.plantilla
        return {k: e for k in p.partes if not (k in p.fijas and p.fijas[k] in elegibles(p, k, self.valores))
                and len(e := elegibles(p, k, self.valores)) > 1}

    def ensamblar(self, protegidos: Protegidos, indices: dict[str, int] | None, semilla: str) -> str:
        """El texto del bloque con sus datos ya cambiados por tokens. `indices`: variante elegida por parte (si no, por hash)."""
        p, elegidas = self.plantilla, {}
        for parte in p.partes:
            ok = elegibles(p, parte, self.valores)
            if not ok:
                continue
            fija = p.fijas.get(parte)
            i = fija if fija in ok else (indices or {}).get(parte)
            if i not in ok:
                i = ok[zlib.crc32(f"{semilla}|{p.id}|{parte}".encode()) % len(ok)]
            elegidas[parte] = p.partes[parte][i]
        texto = PART_RE.sub(lambda m: elegidas.get(m.group(1), ""), p.forma)
        return _limpia(protegidos.proteger(texto, self.valores))


@dataclass
class Mensaje:
    bloques: list[Bloque]
    clave_pregunta: str | None = None            # clave de V1 de la pregunta con la que termina (si termina en una)

    def ids(self) -> list[str]:
        return [b.plantilla.id for b in self.bloques]

    def max_frases(self) -> int:
        return sum(b.plantilla.max_frases for b in self.bloques)

    def sin_modelo(self) -> bool:
        return any(b.plantilla.sin_modelo for b in self.bloques)

    def opciones(self) -> dict[str, list[int]]:
        """«bloque.parte» → índices elegibles, solo donde hay algo que elegir."""
        return {f"{i}.{k}": e for i, b in enumerate(self.bloques) for k, e in b.partes_con_opciones().items()}

    def ensamblar(self, protegidos: Protegidos, elegidas: dict[str, int] | None = None, semilla: str = "") -> str:
        """Párrafos separados por línea en blanco, como los manda el bot (un párrafo = un mensaje de WhatsApp)."""
        out = []
        for i, b in enumerate(self.bloques):
            ind = {k.split(".", 1)[1]: v for k, v in (elegidas or {}).items() if k.startswith(f"{i}.")}
            out.append(b.ensamblar(protegidos, ind, semilla))
        return "\n\n".join(x for x in out if x)


# ---------------------------------------------------------------------------------------------------------------------
# Selector

class Hechos(Protocol):
    """Los datos que usan las plantillas. SOLO del backend estructurado (catálogo, stock, memoria de V1): nunca de un modelo."""

    def producto(self, codigo: str) -> dict | None:
        """{'PRODUCTO': 'el *V31* Vestido Pandora', 'MOTIVO': 'frase de su ficha' | None}; None si no está en el catálogo."""

    def categoria(self, clave: str) -> str | None:
        """El plural de la categoría («vestidos») si hoy hay stock de ella; None si no."""

    def enlace_catalogo(self) -> str | None: ...


class Selector:
    def __init__(self, cat: Catalogo, hechos: Hechos, ocasion_art: dict[str, str], ocasion_nom: dict[str, str] | None = None,
                 habla: tuple[str, ...] = ("recomendar", "preguntar")):
        self.cat, self.hechos, self.habla = cat, hechos, habla
        self.ocasion_art = ocasion_art
        self.ocasion_nom = ocasion_nom or {k: re.sub(r"^(el|la|los|las) ", "", v) for k, v in ocasion_art.items()}

    # -- piezas -----------------------------------------------------------------------------------------------------
    def _p(self, id_: str) -> Plantilla:
        p = self.cat[id_]
        if not p.activa:
            raise SinPlantilla(f"la plantilla {id_} todavía no está activa")
        return p

    def _ocasion(self, ctx: dict) -> dict[str, str]:
        oc = ((ctx.get("requirements") or {}).get("ocasion") or "").strip()
        if oc in self.ocasion_art:
            return {"OCASION_ART": self.ocasion_art[oc], "OCASION_NOM": self.ocasion_nom[oc]}
        return {}

    def _pregunta(self, plan: dict, ctx: dict) -> Bloque | None:
        preg = plan.get("pregunta") or {}
        tipo = preg.get("tipo")
        if not tipo:
            return None
        valores = self._ocasion(ctx)
        if (preg.get("texto") or "").strip():
            valores["PREGUNTA_V1"] = preg["texto"].strip()        # la redacción de V1: evento, segunda vez, talla anterior…
        talla_previa = (ctx.get("customer") or {}).get("talla_perfil")
        if tipo == "talla" and talla_previa:
            valores["TALLA_PREVIA"] = f"*{talla_previa}*"
            return Bloque(self._p("CONFIRM_KNOWN_SIZE"), valores)
        candidatas = self.cat.por_clave(tipo)
        if not candidatas:
            raise SinPlantilla(f"sin plantilla para la pregunta «{tipo}»")
        return Bloque(candidatas[0], valores)

    def _acuse(self, ctx: dict) -> Bloque | None:
        conv = ctx.get("conversation") or {}
        cap = conv.get("captured") or {}
        if cap.get("nombre"):
            return Bloque(self._p("ACK_NAME"), {"NOMBRE": str(cap["nombre"]).strip().title()})
        cat = conv.get("category_asked")
        if cat:
            plural = self.hechos.categoria(cat)
            if not plural:
                raise SinPlantilla(f"de «{cat}» no hay stock: lo cuenta V1")
            return Bloque(self._p("CATEGORY_AVAILABLE"), {"CATEGORIA": plural})
        oc = self._ocasion(ctx)
        if cap.get("ocasion") and oc:
            return Bloque(self._p("OCCASION_CAPTURED"), oc)
        if cap or conv.get("responded"):
            return Bloque(self._p("ACK_NOTED"), {})
        return None

    # -- elegir -----------------------------------------------------------------------------------------------------
    def elegir(self, plan: dict, ctx: dict) -> Mensaje:
        accion = plan.get("accion")
        if accion not in self.habla:
            raise SinPlantilla(f"V2 no habla en «{accion}»")
        conv = ctx.get("conversation") or {}
        bloques: list[Bloque] = []
        if accion == "recomendar":
            codigo = plan.get("producto")
            datos = self.hechos.producto(codigo) if codigo else None
            if not datos or not datos.get("PRODUCTO"):
                raise SinPlantilla("la prenda del plan no está en el catálogo")
            valores = {"PRODUCTO": datos["PRODUCTO"], **self._ocasion(ctx)}
            if datos.get("PRODUCTO_DE"):
                valores["PRODUCTO_DE"] = datos["PRODUCTO_DE"]
            if datos.get("MOTIVO"):
                valores["MOTIVO"] = datos["MOTIVO"]
            plantilla = "SEND_PRODUCT_PHOTOS" if conv.get("wants_to_see") else "RECOMMEND_ONE_PRODUCT"
            bloques.append(Bloque(self._p(plantilla), valores))
        elif accion == "preguntar":
            if not (plan.get("pregunta") or {}).get("tipo"):
                raise SinPlantilla("sin pregunta del código")
            acuse = self._acuse(ctx)
            if acuse:
                bloques.append(acuse)
        else:
            raise SinPlantilla(f"sin plantilla para «{accion}»")
        pregunta = self._pregunta(plan, ctx)
        if pregunta:
            bloques.append(pregunta)
        if not bloques:
            raise SinPlantilla("nada que decir")
        return Mensaje(bloques, clave_pregunta=pregunta.plantilla.clave if pregunta else None)
