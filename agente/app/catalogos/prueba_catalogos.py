"""Pruebas estructurales de los catálogos: carga, validación, resolvedor de referencias y abstención. Sin modelo y sin red
(un embedder falso por hash de n-gramas). Necesita numpy y, para los catálogos reales, PyYAML (ambos vienen en la imagen):

    python3 -m app.catalogos.prueba_catalogos
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import numpy as np

from . import catalogos as C
from .clasificador import Clasificador, cargar_clasificador, cargar_umbrales
from .evaluar import calibrar, confusiones, curva, solapes, top1_top3
from .referencias import normalizar, resolver_referencia

fallos: list = []
total = 0


def caso(nombre: str, obtenido, esperado) -> None:
    global total
    total += 1
    if obtenido != esperado:
        fallos.append(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


def lanza(nombre: str, f, exc=C.CatalogoInvalido, contiene: str = "") -> None:
    global total
    total += 1
    try:
        f()
    except exc as e:
        if contiene not in str(e):
            fallos.append(f"   ✗ {nombre}: el error no menciona {contiene!r}: {e}")
        return
    fallos.append(f"   ✗ {nombre}: no lanzó {exc.__name__}")


# --- embedder falso: hash de n-gramas de caracteres (determinista, sin modelo) ----------------------------------------
def falso(textos, dim: int = 512):
    out = np.zeros((len(textos), dim), dtype=np.float32)
    for i, t in enumerate(textos):
        t = f"  {C.normalizar_clave(t)}  "
        for n in (3, 4):
            for k in range(len(t) - n + 1):
                out[i, hash_estable(t[k:k + n]) % dim] += 1.0
    return out / (np.linalg.norm(out, axis=1, keepdims=True) + 1e-9)


def hash_estable(s: str) -> int:   # hash() de Python cambia por proceso; este no
    h = 2166136261
    for ch in s.encode("utf-8"):
        h = ((h ^ ch) * 16777619) & 0xFFFFFFFF
    return h


def mini(extra: dict = None, minimo: int = 3) -> C.Catalogo:
    cat = C.Catalogo(nombre="mini", minimo_ejemplos=minimo)
    datos = {"lavado": ["como se lava el vestido", "se puede lavar en lavadora", "lavar a mano o maquina"],
             "largo": ["cuanto mide de largo", "hasta donde llega la falda", "es largo o corto el vestido"],
             "pago": ["donde puedo yapear", "como pago el pedido", "aceptan transferencia para pagar"]}
    datos.update(extra or {})
    for n, ex in datos.items():
        cat.intenciones[n] = C.Intencion(nombre=n, ejemplos=list(ex), origen=["propio"] * len(ex), accion="a_" + n)
    return cat


# === 1. normalización, sustituciones y validación =========================================================================
caso("preparar_texto quita ¿¡ y mayúsculas", C.preparar_texto("  ¿Se ESTIRA?  "), "se estira?")
caso("normalizar_clave sin acentos ni signos", C.normalizar_clave("¿Se destiñe, Sí?"), "se destine si")
caso("sustituir <PRODUCTO>", C.sustituir("¿De qué es <PRODUCTO>?"), "¿De qué es este vestido?")
caso("sustituir <ALTURA>", C.sustituir("si mido <ALTURA>"), "si mido 1,60")

C.validar(mini())
caso("catálogo mínimo válido", True, True)
lanza("duplicado exacto en la misma intención", lambda: C.validar(mini({"lavado": ["a b c", "a b c", "d e f"]})), contiene="duplicado")
lanza("duplicado salvo acentos y signos", lambda: C.validar(mini({"lavado": ["¿Cómo lavo?", "como lavo", "otro"]})), contiene="duplicado")
lanza("duplicado entre intenciones", lambda: C.validar(mini({"largo": ["como se lava el vestido", "x y z", "p q r"]})), contiene="en «largo» y en «lavado»")
lanza("ejemplo vacío", lambda: C.validar(mini({"lavado": ["a b c", "  ", "d e f"]})), contiene="vacío")
lanza("pocos ejemplos", lambda: C.validar(mini({"lavado": ["solo uno", "solo dos"]})), contiene="mínimo 3")
lanza("marcador sin sustituir", lambda: C.validar(mini({"lavado": ["de <COSA> es", "a b c", "d e f"]})), contiene="marcador")


def _strength_malo():
    cat = mini()
    cat.intenciones["lavado"].strength = 1.5
    C.validar(cat)


lanza("strength fuera de rango", _strength_malo, contiene="strength")


# === 2. cargador del faq y de los catálogos reales =========================================================================
D = C.directorio_datos()
RUTA_FAQ = os.path.join(D, "faq_vestidos.txt")
if os.path.exists(RUTA_FAQ):
    g = C.cargar_faq_txt(RUTA_FAQ)
    caso("faq: 25 intenciones", len(g), 25)
    caso("faq: 250 ejemplos", sum(len(v["ejemplos"]) for v in g.values()), 250)
    caso("faq: 10 por intención", {len(v["ejemplos"]) for v in g.values()}, {10})
    caso("faq: orden A–Y", [v["letra"] for v in g.values()], list("ABCDEFGHIJKLMNOPQRSTUVWXY"))
    caso("faq: primera intención", next(iter(g)), "material")
    with tempfile.TemporaryDirectory() as t:
        malo = os.path.join(t, "x.txt")
        open(malo, "w", encoding="utf-8").write("## A | uno | Uno\n001 s hola\nesto no es una línea válida\n")
        lanza("faq: línea mal formada", lambda: C.cargar_faq_txt(malo), contiene="x.txt:3")
        suelto = os.path.join(t, "y.txt")
        open(suelto, "w", encoding="utf-8").write("001 s hola\n")
        lanza("faq: ejemplo fuera de un grupo", lambda: C.cargar_faq_txt(suelto), contiene="fuera de un grupo")
else:
    fallos.append("   ✗ falta faq_vestidos.txt")

try:
    import yaml  # noqa: F401
    HAY_YAML = True
except ImportError:
    HAY_YAML = False
    print("(sin PyYAML: se omiten las pruebas de los YAML reales; corre en la imagen del agente)")

REALES = {"preguntas_producto": 25, "ocasion": 15, "estilo": 16, "objeciones": 11, "rechazo": 9, "senales_compra": 11,
          "referencias_contextuales": 10}
if HAY_YAML:
    caso("listar_catalogos incluye los 7", set(REALES) <= set(C.listar_catalogos()), True)
    caso("listar_catalogos no incluye pruebas ni ruido", [n for n in C.listar_catalogos() if "prueba" in n or n.startswith("ruido")], [])
    for nombre, n_int in REALES.items():
        cat = C.cargar_catalogo(nombre)
        caso(f"{nombre}: {n_int} intenciones", len(cat.intenciones), n_int)
        caso(f"{nombre}: ≥20 ejemplos por intención", min(len(i.ejemplos) for i in cat.intenciones.values()) >= 20, True)
        caso(f"{nombre}: sin marcadores <...>", any("<" in t for t, _, _ in cat.ejemplos()), False)
        pr = C.cargar_catalogo_prueba(nombre)
        caso(f"{nombre}: prueba aparte con las mismas intenciones", set(pr or {}) == set(cat.intenciones), True)
        caso(f"{nombre}: prueba aparte ≥4 por intención", min(len(v) for v in pr.values()) >= 4, True)
        iguales, _ = solapes(cat, pr)
        caso(f"{nombre}: la prueba aparte no repite el entrenamiento", iguales, [])
        caso(f"{nombre}: toda intención declara accion", all(i.accion for i in cat.intenciones.values()), True)
    pp = C.cargar_catalogo("preguntas_producto")
    caso("preguntas_producto: conserva los 250 del faq", sum(1 for _, _, o in pp.ejemplos() if o == "faq"), 250)
    caso("preguntas_producto: «<PRODUCTO>» → «este vestido»", "¿De qué material es este vestido?" in pp.intenciones["material"].ejemplos, True)
    caso("preguntas_producto: «<ALTURA>» → «1,60»", "¿Cómo me quedaría si mido 1,60?" in pp.intenciones["largo"].ejemplos, True)
    caso("preguntas_producto: ampliaciones de elasticidad (jerga)",
         {"cede?", "tiene licra?", "es flexible?"} <= set(pp.intenciones["elasticidad"].ejemplos), True)
    sc = C.cargar_catalogo("senales_compra")
    caso("señales: toda intención con strength y stage",
         all(i.strength is not None and i.stage for i in sc.intenciones.values()), True)
    caso("señales: strength crece hacia el cierre",
         sc.intenciones["solo_mirando"].strength < sc.intenciones["quiere_separar"].strength < sc.intenciones["pago_realizado"].strength, True)
    caso("señales: etapa_bot válida", {i.extra.get("etapa_bot") for i in sc.intenciones.values()} <=
         {"prospeccion", "seguimiento", "cierre", "venta_confirmada"}, True)
    caso("objeciones: las 11 del documento", set(C.cargar_catalogo("objeciones").intenciones) ==
         {"caro", "lo_voy_a_pensar", "no_estoy_segura", "duda_talla", "duda_color", "miedo_no_llegue", "muy_corto", "muy_escotado",
          "quiero_ver_otros", "mas_barato_otra_tienda", "no_confio_internet"}, True)
    caso("rechazo: los 9 del documento", len(C.cargar_catalogo("rechazo").intenciones), 9)
    caso("la frase prohibida no aparece en ningún ejemplo", any("alguna ocasión especial" in t.lower() or "alguna ocasion especial" in t.lower()
                                                               for n in REALES for t, _, _ in C.cargar_catalogo(n).ejemplos()), False)
    with tempfile.TemporaryDirectory() as t:
        open(os.path.join(t, "malo.yaml"), "w", encoding="utf-8").write("catalogo: malo\nintenciones:\n  a:\n    ejemplos: [uno, uno]\n")
        lanza("YAML con duplicados y pocos ejemplos", lambda: C.cargar_catalogo("malo", t), contiene="inválido")
        open(os.path.join(t, "sin.yaml"), "w", encoding="utf-8").write("catalogo: sin\nversion: 1\n")
        lanza("YAML sin «intenciones»", lambda: C.cargar_catalogo("sin", t), contiene="intenciones")
        ok = os.path.join(t, "ok.yaml")
        open(ok, "w", encoding="utf-8").write("catalogo: ok\nminimo_ejemplos: 2\nintenciones:\n  a:\n    accion: x\n    ejemplos: [uno dos, tres cuatro]\n"
                                              "  b:\n    ejemplos: ['cinco seis', 'siete ocho']\n")
        caso("YAML propio mínimo carga", C.cargar_catalogo("ok", t).n_ejemplos, 4)

# === 3. clasificador con embedder falso ===============================================================================
cat = mini()
clf = Clasificador(cat, falso, metodo="max", umbrales={"score": 0.5, "margen": 0.02})
r = clf.clasificar("como se lava el vestido")
caso("ejemplo idéntico → su intención", r["intent"], "lavado")
caso("ejemplo idéntico → score ≈ 1", r["score"] > 0.99, True)
caso("resultado trae margen y alternativas", (r["margen"] > 0.1, len(r["alternativas"]), r["alternativas"][0][0]), (True, 3, "lavado"))
caso("resultado trae acción de la intención", r["accion"], "a_lavado")
caso("catálogo en el resultado", r["catalogo"], "mini")
r = clf.clasificar("donde puedo yapear porfa")
caso("paráfrasis cercana → pago", r["intent"], "pago")
r = clf.clasificar("xqzwv kjh wwq")
caso("ruido sin letras en común → se abstiene", (r["intent"], r["motivo_abstencion"]), (None, "score_bajo"))
caso("al abstenerse sigue informando la mejor", r["mejor"] in cat.intenciones, True)
# margen: dos intenciones con el mismo ejemplo (sin validar) empatan → margen 0 → abstención por margen
dup = mini({"largo": ["como se lava el vestido", "x y z", "p q r"]})
c2 = Clasificador(dup, falso, metodo="max", umbrales={"score": 0.5, "margen": 0.05})
r = c2.clasificar("como se lava el vestido")
caso("empate entre intenciones → abstención por margen", (r["intent"], r["motivo_abstencion"]), (None, "margen_bajo"))
# umbral de score
c3 = Clasificador(cat, falso, metodo="max", umbrales={"score": 0.999, "margen": 0.0})
caso("score por debajo del umbral → abstención", c3.clasificar("como se lava un vestido")["intent"], None)
# métodos
for m in ("knn", "max", "centroide", "mixto", "hibrido"):
    cm = Clasificador(cat, falso, metodo=m, umbrales={"score": 0.3, "margen": 0.0})
    caso(f"método {m} acierta un ejemplo", cm.clasificar("cuanto mide de largo")["intent"], "largo")
lanza("método desconocido", lambda: Clasificador(cat, falso, metodo="magia"), exc=ValueError)
# clase negativa «ninguno»
neg = Clasificador(cat, falso, metodo="max", umbrales={"score": 0.1, "margen": 0.0},
                   negativos=["viste el partido de ayer", "cuanto es 25 por 4", "q tal el clima hoy"])
r = neg.clasificar("viste el partido de anoche")
caso("clase «ninguno» atrapa el ruido parecido", (r["intent"], r["motivo_abstencion"]), (None, "fuera_de_giro"))
caso("«ninguno» no estorba a lo propio", neg.clasificar("como pago el pedido")["intent"], "pago")
# lote y vacío
caso("clasificar_lote vacío", clf.clasificar_lote([]), [])
caso("clasificar_lote tamaño", len(clf.clasificar_lote(["a", "b", "como pago"])), 3)
# matriz inyectada = misma que calculada
c4 = Clasificador(cat, falso, matriz=falso([t for t, _, _ in cat.ejemplos()]), metodo="max", umbrales={"score": 0.5, "margen": 0.0})
caso("matriz precalculada inyectable", c4.clasificar("como se lava el vestido")["intent"], "lavado")
# leave-one-out: con 3 ejemplos por intención cada uno encuentra a sus vecinos
from .evaluar import loo  # noqa: E402
P, y = loo(clf)
caso("LOO forma", P.shape, (9, 3))
caso("LOO nunca se cuenta a sí mismo (score < 1)", bool((P.max(1) < 0.999).all()), True)
caso("LOO top-1 en el mini catálogo (hash de n-gramas) ≥ 4/9", top1_top3(P, y)[0] >= 4 / 9, True)
# caché de embeddings en disco
with tempfile.TemporaryDirectory() as t:
    veces = []

    def contador(x):
        veces.append(len(x))
        return falso(x)
    contador.model_name = "falso"
    Clasificador(cat, contador, cache=t)
    Clasificador(cat, contador, cache=t)
    caso("la caché evita recalcular los ejemplos", veces, [9])
# cargar_clasificador con embedder inyectado y umbrales.json
with tempfile.TemporaryDirectory() as t:
    json.dump({"mini": {"score": 0.77, "margen": 0.033, "metodo": "knn", "k": 2}}, open(os.path.join(t, "umbrales.json"), "w"))
    caso("cargar_umbrales lee el json", cargar_umbrales("mini", os.path.join(t, "umbrales.json"))["score"], 0.77)
    caso("cargar_umbrales sin entrada → valores por defecto", cargar_umbrales("otro", os.path.join(t, "umbrales.json"))["score"], 0.80)
    caso("cargar_umbrales sin archivo → valores por defecto", cargar_umbrales("mini", os.path.join(t, "no.json"))["margen"], 0.01)
if HAY_YAML:
    cp = cargar_clasificador("senales_compra", embed=falso)
    caso("cargar_clasificador: catálogo real con embedder falso", (cp.catalogo.nombre, len(cp.nombres)), ("senales_compra", 11))
    caso("cargar_clasificador: lee umbrales.json del repo", cp.umbrales["score"] > 0.5, True)
    caso("cargar_clasificador: carga los negativos de ruido", len(cp.negativos) > 50, True)
    r = cp.clasificar("ya yapee")
    caso("catálogo real: respuesta con forma completa", {"intent", "score", "margen", "alternativas"} <= set(r), True)
    if r["intent"]:
        caso("catálogo real: si responde trae strength y stage", ("strength" in r, "stage" in r), (True, True))

# enrutador entre catálogos (embedder falso)
from .enrutador import Enrutador  # noqa: E402
cl2 = {"mini": Clasificador(mini(), falso, metodo="max", umbrales={"score": 0.5, "margen": 0.0}),
       "otro": Clasificador(mini({"lavado": ["hola buenas tardes", "buenos dias amiga", "que tal como estas"],
                                  "largo": ["adios hasta luego", "nos vemos mañana", "chau cuidate mucho"],
                                  "pago": ["gracias por todo", "muchas gracias amiga", "mil gracias de verdad"]}), falso, metodo="max",
                            umbrales={"score": 0.5, "margen": 0.0})}
enr = Enrutador(cl2)
caso("enrutador: elige el catálogo de la frase", enr.enrutar("como se lava el vestido")[0][0], "mini")
caso("enrutador: otro catálogo", enr.enrutar("hola buenas tardes amiga")[0][0], "otro")
caso("enrutador: pesos suman 1", round(sum(p for _, p in enr.enrutar("donde puedo yapear")), 2), 1.0)
rr = enr.clasificar("como se lava el vestido")
caso("enrutador: clasifica con el catálogo elegido", (rr["catalogo"], rr["resultado"]["intent"]), ("mini", "lavado"))

# === 4. evaluación: calibración, curva, confusiones ====================================================================
y_ = np.array([0, 0, 1, 1, 2, 2, 0, 1])
P_ = np.array([[.95, .1, .1], [.9, .2, .1], [.1, .93, .2], [.2, .5, .52], [.1, .1, .9], [.1, .88, .2], [.97, .1, .1], [.1, .9, .3]])
pred_ = P_.argmax(1)
s1_ = P_.max(1)
mg_ = s1_ - np.sort(P_, 1)[:, -2]
caso("top-1 de juguete", top1_top3(P_, y_)[0], 6 / 8)
caso("top-3 de juguete con 3 clases", top1_top3(P_, y_)[1], 1.0)
cv = curva(pred_, y_, s1_, mg_, [0.0, 0.92], "score")
caso("curva: umbral 0 cubre todo", cv[0][1], 1.0)
caso("curva: umbral alto cubre menos y acierta más", (cv[1][1] < 1.0, cv[1][2] >= cv[0][2]), (True, True))
cal = calibrar(pred_, y_, s1_, mg_, np.array([0.6, 0.55]), np.array([0.05, 0.02]), objetivo=0.99, tope_fp=0.0)
caso("calibrar: cumple el objetivo con ruido bajo", (cal["cumple"], cal["precision"] >= 0.99, cal["falsos_positivos"]), (True, True, 0.0))
caso("confusiones ordena por frecuencia", confusiones(P_, y_, ["a", "b", "c"])[0][:3], ("b", "c", 2))

# === 5. resolvedor de referencias ======================================================================================
V35 = {"codigo": "V35", "nombre": "Vestido Irla", "color": "negro"}
V42 = {"codigo": "V42", "nombre": "Vestido Gala Capa", "color": "azul marino"}
V21 = {"codigo": "V21", "nombre": "Vestido Kendall", "color": "vino"}
TRES = [V35, V42, V21]          # cronológico: V21 es el último mostrado


def cod(texto, cands, esperado_tipo="candidato"):
    r = resolver_referencia(texto, cands)
    if r.tipo != esperado_tipo:
        return f"{r.tipo}:{r.motivo}"
    if r.tipo == "candidato":
        return r.candidato["codigo"]
    if r.tipo == "ambiguo":
        return [c["codigo"] for c in r.empatados]
    return None


def rc(texto, esperado, cands=TRES, tipo="candidato"):
    caso(f"ref «{texto}»", cod(texto, cands, tipo), esperado)


caso("normalizar: acentos, ñ y repeticiones", normalizar("¡EL BORGOÑA, eeese!"), "el borgona ese")
# ordinales
rc("el primero", "V35"); rc("el segundo", "V42"); rc("la tercera", "V21"); rc("el último", "V21"); rc("el ultimo vestido", "V21")
rc("el penúltimo", "V42"); rc("el anterior", "V42"); rc("el 1ro", "V35"); rc("el 2", "V42"); rc("la segunda foto", "V42")
rc("el de arriba", "V35"); rc("el de abajo", "V21"); rc("el del medio", "V42"); rc("el de más arriba", "V35")
rc("el cuarto", None, tipo="ninguno"); rc("el segundo", None, cands=[V35], tipo="ninguno")
rc("el del medio", None, cands=[V35, V42], tipo="ninguno")
rc("el del medio", ["V42", "V21"], cands=[V35, V42, V21, {"codigo": "V09", "nombre": "Zeta", "color": "rojo"}], tipo="ambiguo")
# colores y equivalencias
rc("el negro", "V35"); rc("la negra", "V35"); rc("el vestido negro", "V35"); rc("el negro pe", "V35"); rc("EL NEGRO", "V35")
for sin in ("guinda", "borgoña", "borgona", "vino", "burdeos", "granate", "rojo oscuro", "rojo vino", "color vino"):
    rc(f"el {sin}", "V21")
for sin in ("azul", "azul noche", "azul marino", "navy", "marino", "azul oscuro"):
    rc(f"el {sin}", "V42")
rc("el rojo", "V21")                                  # sin rojo puro, el vino es de la familia
caso("rojo → vino: coincidencia solo de familia = nivel media", resolver_referencia("el rojo", TRES).nivel, "media")
caso("guinda → vino: mismo tono = nivel alta", resolver_referencia("el guinda", TRES).nivel, "alta")
rc("el verde", None, tipo="ninguno")
rc("no el negro, el azul", "V42"); rc("no me gusta el negro, quiero el azul", "V42"); rc("el que no es negro", ["V42", "V21"], tipo="ambiguo")
ROJOS = [{"codigo": "V01", "nombre": "A", "color": "rojo"}, {"codigo": "V02", "nombre": "B", "color": "azul"},
         {"codigo": "V03", "nombre": "C", "color": "rojo"}]
rc("el rojo", ["V01", "V03"], cands=ROJOS, tipo="ambiguo")
rc("el segundo rojo", "V03", cands=ROJOS); rc("el último rojo", "V03", cands=ROJOS); rc("el primer rojo", "V01", cands=ROJOS)
RV = [{"codigo": "V10", "nombre": "Uno", "color": "rojo"}, {"codigo": "V11", "nombre": "Dos", "color": "vino"}]
rc("el rojo", "V10", cands=RV); rc("el guinda", "V11", cands=RV); rc("el rojo vino", "V11", cands=RV); rc("el rojo oscuro", "V11", cands=RV)
EQ = [{"codigo": "V20", "nombre": "Uno", "color": "vino"}, {"codigo": "V21", "nombre": "Dos", "color": "borgoña"}]
rc("el guinda", ["V20", "V21"], cands=EQ, tipo="ambiguo")           # guinda ≈ borgoña ≈ vino: dos prendas del mismo tono
AZ = [{"codigo": "V30", "nombre": "Uno", "color": "azul marino"}, {"codigo": "V31", "nombre": "Dos", "color": "azul rey"}]
rc("el azul", ["V30", "V31"], cands=AZ, tipo="ambiguo"); rc("el azul marino", "V30", cands=AZ); rc("el azul rey", "V31", cands=AZ)
rc("el nude", "V40", cands=[{"codigo": "V40", "nombre": "Uno", "color": "nude"}, V35]); rc("el color piel", "V40", cands=[{"codigo": "V40", "nombre": "Uno", "color": "nude"}, V35])
rc("el champagne", "V41", cands=[{"codigo": "V41", "nombre": "Uno", "color": "champagne"}, V35])
rc("el negro", "V50", cands=[{"codigo": "V50", "nombre": "Vestido Negro Largo", "color": ""}, V42])    # sin color: lo toma del nombre
rc("el negro", "V51", cands=[{"codigo": "V51", "nombre": "Uno", "color": "negro con dorado"}, V42])     # prenda de dos colores
# deícticos
rc("ese", "V21"); rc("este", "V21"); rc("ese mismo", "V21"); rc("este vestido", "V21"); rc("esa", "V21"); rc("el mismo", "V21"); rc("eeese", "V21")
rc("el otro", ["V35", "V42"], tipo="ambiguo")                  # con 3 candidatos: los dos que no son el último
rc("el otro", "V35", cands=[V35, V42]); rc("no, el otro", "V35", cands=[V35, V42]); rc("la otra", "V35", cands=[V35, V42])
rc("ese no, el otro", "V35", cands=[V35, V42]); rc("no ese, el otro", "V35", cands=[V35, V42]); rc("el otro no, ese", "V42", cands=[V35, V42])
rc("ese no", "V35", cands=[V35, V42]); rc("el otro", None, cands=[V35], tipo="ninguno"); rc("ese", "V35", cands=[V35])
rc("ese y el otro", ["V35", "V42"], cands=[V35, V42], tipo="ambiguo")
rc("el otro vestido", "V35", cands=[V35, V42])
rc("ese rojo", "V03", cands=ROJOS)
# código y nombre
rc("el v35", "V35"); rc("el V42", "V42"); rc("el V 21", "V21"); rc("quiero el v-42 en M", "V42"); rc("el 42", "V42"); rc("el 35", "V35")
rc("el irla", "V35"); rc("el vestido Kendall", "V21"); rc("el KENDALL", "V21"); rc("el v99", None, tipo="ninguno")
rc("el Irla negro", "V35"); rc("el vestido Gala", "V42")
# foto
FT = [V35, V42 | {"foto": True}, V21]
rc("el de la foto", "V42", cands=FT); rc("el de la foto que te mandé", "V42", cands=FT); rc("como el de la imagen", "V42", cands=FT)
rc("el de la foto", "V35", cands=[V35]); rc("el de la foto", ["V35", "V42"], cands=[V35, V42], tipo="ambiguo")
rc("el de la foto que te mandé", None, cands=[V35, V42], tipo="ninguno")
rc("el de mi foto", "V21", cands=[V35, {"codigo": "V21", "nombre": "K", "color": "vino", "origen": "foto_clienta"}])
rc("el de la foto", ["V42", "V21"], cands=[V35, V42 | {"foto": True}, V21 | {"foto": True}], tipo="ambiguo")
# plural, atributo, sin pista
rc("los dos", None, tipo="ninguno"); rc("ambos", None, tipo="ninguno"); rc("me gustan todos", None, tipo="ninguno"); rc("esos dos", None, tipo="ninguno")
rc("el segundo de los dos", "V42", cands=[V35, V42])
rc("el largo", None, tipo="ninguno"); rc("el de mangas", None, tipo="ninguno")
caso("atributo: el motivo lo dice", "atributo" in resolver_referencia("el de mangas", TRES).motivo, True)
rc("hola buenas", None, tipo="ninguno"); rc("", None, tipo="ninguno"); rc("el rojo", None, cands=[], tipo="ninguno")
caso("sin candidatos: motivo", resolver_referencia("ese", []).motivo, "sin_candidatos")
PR = [{"codigo": "V60", "nombre": "A", "color": "negro", "precio": 300}, {"codigo": "V61", "nombre": "B", "color": "rojo", "precio": 250},
      {"codigo": "V62", "nombre": "C", "color": "azul", "precio": 400}]
rc("el más barato", "V61", cands=PR); rc("el más caro", "V62", cands=PR); rc("el más barato", None, cands=TRES, tipo="ninguno")
# la resolución se puede serializar
caso("a_dict serializable", json.dumps(resolver_referencia("el otro", [V35, V42]).a_dict(), ensure_ascii=False)[:20], '{"tipo": "candidato"')
caso("no modifica los candidatos", TRES == [V35, V42, V21] and V35 == {"codigo": "V35", "nombre": "Vestido Irla", "color": "negro"}, True)

# frases nuevas, escritas después de ajustar el resolvedor, con V35 (negro), V42 (azul marino, foto) y V21 (vino, el último)
NUEVAS = [("pásame ese de color guinda", "V21"), ("mejor el negrito", "V35"), ("no, ese no, el que era azul", "V42"),
          ("el que me enseñaste al principio", "V35"), ("el del medio nomas", "V42"), ("esa de ahi", "V21"), ("el azul de la foto", "V42"),
          ("mmm el primero o el último?", ["V35", "V21"]), ("el de color rojo, el segundo", None), ("el que esta debajo", "V21"),
          ("ese vestidito", "V21"), ("dame el otro q me mostraste", ["V35", "V42"]), ("el mas largo", None), ("el V-42 en M", "V42"),
          ("el segundo q me mandaste primero", "V42"), ("el burdeos pues", "V21"), ("la del escote", None), ("ese ese ese", "V21"),
          ("el primero y el segundo", ["V35", "V42"]), ("el azul oscuro", "V42"), ("el de hace rato", "V42"),
          ("el penultimo vestido que vimos", "V42"), ("ya vi, quiero el negro nomas", "V35"), ("ese no me gusta, el negro", "V35"),
          ("el que mande en la foto", "V42"), ("el 3", "V21"), ("la 1", "V35"), ("Mejor EL OTRO", ["V35", "V42"]), ("el rojito", "V21"),
          ("la azul", "V42"), ("el ultimo q vi", "V21")]
FT3 = [V35, V42 | {"foto": True}, V21]
for t_, e_ in NUEVAS:
    rc(t_, e_, cands=FT3, tipo=("ninguno" if e_ is None else "ambiguo" if isinstance(e_, list) else "candidato"))


def main() -> int:
    for f in fallos:
        print(f)
    print(f"catálogos semánticos (carga, validación, clasificador con abstención, evaluación, resolvedor de referencias): "
          f"{total - len(fallos)}/{total} casos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
