"""Catálogos semánticos dentro de V2 (app/catalogos/): una lectura compacta de lo que dijo la clienta.

El mensaje se enruta a UN catálogo (preguntas de producto, objeciones, señales de compra…) y solo ese lo clasifica: las puntuaciones
de un catálogo no se comparan con las de otro. Si el clasificador no está seguro, se abstiene y la lectura dice «sin lectura».
Esta capa CLASIFICA; no contesta ni inventa datos de la prenda.

V2_CATALOGOS:  0 = apagado · sombra (por defecto) = se lee y se mide, nada cambia · activo = la lectura ayuda al detector de temas.

Nunca tira el turno: si los catálogos no cargan, `leer` devuelve None y V2 sigue como siempre."""
from __future__ import annotations

import logging
import os
import threading
from typing import Callable

log = logging.getLogger("agente.v2.semantica")

MODOS = ("0", "sombra", "activo")
# Los catálogos que V2 lee. `ocasion` y `estilo` quedan fuera a propósito: V1 ya saca la ocasión con reglas y el estilo aún no tiene a quién servir.
CATALOGOS = ("preguntas_producto", "objeciones", "rechazo", "senales_compra", "referencias_contextuales")


def modo_por_defecto(env=os.environ) -> str:
    m = (env.get("V2_CATALOGOS") or "sombra").strip().lower()
    return m if m in MODOS else "sombra"


# Intención del catálogo → tema del detector de temas (v2/temas.py). Solo lo que tiene un tema claro.
_TEMA_PRODUCTO = {
    "material": "material", "textura": "material", "elasticidad": "material", "encogimiento": "material", "despintado": "material",
    "resistencia_superficie": "material", "desgaste": "material", "transparencia": "material", "caida_peso": "material",
    "lavado": "material", "secado_planchado": "material", "arrugas": "material", "calidad_durabilidad": "material",
    "costuras": "material", "clima_temporada": "material",
    "tallas": "talla_info", "ajuste_cuerpo": "talla_info", "largo": "talla_info", "busto_escote": "talla_info",
    "espalda_tirantes_mangas": "talla_info", "comodidad_movimiento": "talla_info",
    "color": "producto", "detalles_modificaciones": "producto", "ocasion_uso": "producto", "cierre_colocacion": "producto",
}
_TEMA_SENAL = {"pregunta_envio": "delivery", "pregunta_pago": "pago", "pregunta_precio": "precio"}


def tema_sugerido(lectura: dict | None) -> str | None:
    """El tema que apunta la lectura, o None si se abstuvo o el catálogo no tiene tema (rechazo, referencias…)."""
    if not lectura or not lectura.get("intent"):
        return None
    cat, intent = lectura.get("catalogo"), lectura["intent"]
    if cat == "preguntas_producto":
        return _TEMA_PRODUCTO.get(intent)
    if cat == "objeciones":
        return "objecion"
    if cat == "senales_compra":
        return _TEMA_SENAL.get(intent)
    return None


class Semantica:
    """Carga perezosa: el primer mensaje paga 1–2 s por catálogo (o menos con CATALOGOS_CACHE_DIR); después ~3 ms por mensaje."""

    def __init__(self, embed: Callable | None = None, modo: str | None = None, directorio: str | None = None,
                 cargador: Callable | None = None, nombres: tuple[str, ...] = CATALOGOS):
        self.modo = modo if modo in MODOS else modo_por_defecto()
        self.embed, self.directorio, self.nombres = embed, directorio, nombres
        self._cargador = cargador            # (embed, directorio, nombres) → (enrutador, {nombre: clasificador}); para pruebas
        self._lock = threading.Lock()
        self._listo = False
        self._roto: str | None = None
        self.enrutador = None
        self.clasificadores: dict = {}

    @property
    def activa(self) -> bool:
        return self.modo != "0"

    def _cargar(self) -> bool:
        if self._listo:
            return True
        if self._roto:
            return False
        with self._lock:
            if self._listo:
                return True
            try:
                if self._cargador:
                    self.enrutador, self.clasificadores = self._cargador(self.embed, self.directorio, self.nombres)
                else:
                    from ..catalogos import Enrutador, cargar_todos
                    self.clasificadores = cargar_todos(self.embed, directorio=self.directorio, nombres=list(self.nombres))
                    self.enrutador = Enrutador(self.clasificadores)
                self._listo = True
            except Exception as e:           # sin catálogos V2 sigue igual
                self._roto = type(e).__name__
                log.warning("catálogos semánticos no cargaron (%s): V2 sigue sin ellos", self._roto)
        return self._listo

    def precargar(self) -> bool:
        return self.activa and self._cargar()

    def leer(self, mensaje: str, candidatos: list[dict] | None = None) -> dict | None:
        """{catalogo, intent, score, margen, accion, motivo_abstencion, ruteo, hechos_requeridos, strength, stage, referencia, tema}
        o None si está apagado, no hay texto o no cargó."""
        if not self.activa or not (mensaje or "").strip() or not self._cargar():
            return None
        try:
            ruteo = self.enrutador.enrutar(mensaje)
            cat = ruteo[0][0]
            clf = self.clasificadores[cat]
            r = clf.clasificar(mensaje)
            intent = r.get("intent")
            out = {"catalogo": cat, "intent": intent, "score": round(float(r.get("score") or 0), 3),
                   "margen": round(float(r.get("margen") or 0), 3), "accion": r.get("accion") if intent else None,
                   "motivo_abstencion": None if intent else r.get("motivo_abstencion"),
                   "ruteo": [(n, round(float(p), 2)) for n, p in ruteo[:2]]}
            if intent:
                it = clf.catalogo.intenciones.get(intent)
                if it is not None:
                    out["hechos_requeridos"] = list(getattr(it, "hechos_requeridos", None) or [])
                    fuente = getattr(it, "fuente", "") or getattr(clf.catalogo, "fuente", "") or ""
                    out["fuente"] = fuente
                    if out["hechos_requeridos"] and not fuente:
                        out["sin_fuente"] = True     # sin fuente no se responde como hecho
                for k in ("strength", "stage", "etapa_bot"):
                    if r.get(k) is not None:
                        out[k] = r[k]
            if cat == "referencias_contextuales" and intent and candidatos:
                from ..catalogos import resolver_referencia
                ref = resolver_referencia(mensaje, candidatos)
                out["referencia"] = {"tipo": ref.tipo, "codigo": (ref.candidato or {}).get("codigo"), "motivo": getattr(ref, "motivo", None)}
            out["tema"] = tema_sugerido(out)
            return out
        except Exception as e:
            log.warning("lectura semántica falló (%s)", type(e).__name__)
            return {"error": type(e).__name__}
