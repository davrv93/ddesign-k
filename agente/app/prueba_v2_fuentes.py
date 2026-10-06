"""Pruebas de la puerta de datos (v2/fuentes.py) y las fuentes de catálogos:
sin fuente no se responde como hecho; las plantillas no traen datos duros:

    python3 -m app.prueba_v2_fuentes
"""
from __future__ import annotations

import importlib.util
import os
import sys

_BASE = os.path.dirname(os.path.abspath(__file__))


def _mod_catalogos():
    spec = importlib.util.spec_from_file_location(
        "catalogos_suelto", os.path.join(_BASE, "catalogos", "catalogos.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["catalogos_suelto"] = mod
    spec.loader.exec_module(mod)
    return mod


_cat = _mod_catalogos()
Catalogo, Intencion = _cat.Catalogo, _cat.Intencion
fuente_efectiva, resumen_fuentes = _cat.fuente_efectiva, _cat.resumen_fuentes

from .v2.fuentes import auditar, puerta_datos, revisar_plantilla

fallos: list[str] = []
total = 0


def caso(nombre: str, obtenido, esperado) -> None:
    global total
    total += 1
    if obtenido != esperado:
        fallos.append(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


caso("ficha con fuente pasa", puerta_datos("precio", "fichas")["pasa"], True)
caso("sin fuente no pasa", puerta_datos("plazo", "")["pasa"], False)
caso("sin revisión no pasa", puerta_datos("horario", "tienda.md")["pasa"], False)
caso("slot no es dato", revisar_plantilla("Cuesta {precio} en tienda."), [])
caso("dato fijo se marca", revisar_plantilla("Cuesta S/ 299 solo hoy, 20% off."), ["dato en texto fijo: S/ 299", "dato en texto fijo: 20%"])

cat = Catalogo(nombre="x", fuente="mensaje de la dueña 06-10", revisado_por_humano=True)
cat.intenciones = {"a": Intencion(nombre="a", hechos_requeridos=["tela"]),
                   "b": Intencion(nombre="b", hechos_requeridos=["tela"],
                                  fuente="otro mensaje", revisado_por_humano=True)}
r = resumen_fuentes(cat)
caso("hereda fuente catálogo", (r["con_fuente"], r["revisadas"], r["sin_fuente"]), (2, 2, []))
caso("fuente efectiva", fuente_efectiva(cat.intenciones["b"], cat), ("otro mensaje", True))

# Las plantillas reales: ningún texto fijo con datos (los datos van en slots).
try:
    import yaml
except ImportError:
    yaml = None
if yaml is None:
    print("(sin PyYAML: salto la auditoría de plantillas.yaml)")
else:
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "v2", "plantillas.yaml")
    docs = yaml.safe_load(open(ruta, encoding="utf-8"))
    plats = docs.get("plantillas") if isinstance(docs, dict) else docs
    a = auditar(plats if isinstance(plats, list) else [])
    caso("plantillas sin datos duros", a["con_dato"], {})

print(f"fuentes: {total - len(fallos)}/{total} ok")
if fallos:
    print("\n".join(fallos))
    raise SystemExit(1)
