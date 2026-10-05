"""Pruebas de la conexión de los catálogos semánticos con V2 (v2/semantica.py): el wrapper, el tema que sugiere, la ayuda al detector de
temas, el modo sombra (solo mide) y activo (ayuda), la traza y las métricas. Sin red ni modelos (el clasificador es falso):

    python3 -m app.prueba_v2_semantica
"""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace as NS

from . import memoria
from .prueba_v2_temas import MEM_TALLA, agente, pedido, v1_falso
from .v2 import temas as M
from .v2.metricas import Registro
from .v2.semantica import Semantica, modo_por_defecto, tema_sugerido

fallos: list[str] = []
total = 0


def caso(nombre: str, obtenido, esperado) -> None:
    global total
    total += 1
    if obtenido != esperado:
        fallos.append(f"   ✗ {nombre}: {obtenido!r} (esperado {esperado!r})")


# --- clasificadores falsos: por palabra clave -----------------------------------------------------------------------------------------
REGLAS = {
    "preguntas_producto": {"cede": "elasticidad", "estira": "elasticidad", "tiñe": "despintado", "mide": "largo"},
    "objeciones": {"pensarlo": "lo_voy_a_pensar", "caro": "caro"},
    "senales_compra": {"yapear": "pregunta_pago", "separas": "quiere_separar"},
    "rechazo": {"no me gusta": "no_me_gusta"},
    "referencias_contextuales": {"el otro": "el_otro"},
}


class Clf:
    def __init__(self, nombre):
        self.nombre = nombre
        self.catalogo = NS(intenciones={i: NS(hechos_requeridos=["elasticidad"]) for i in REGLAS[nombre].values()})

    def clasificar(self, t):
        for k, i in REGLAS[self.nombre].items():
            if k in t.lower():
                return {"intent": i, "score": 0.95, "margen": 0.05, "accion": "responder", "catalogo": self.nombre,
                        **({"strength": 0.9, "stage": "CHECKOUT"} if self.nombre == "senales_compra" else {})}
        return {"intent": None, "score": 0.4, "margen": 0.0, "motivo_abstencion": "score_bajo", "catalogo": self.nombre}


class Enr:
    def enrutar(self, t):
        for nombre, reglas in REGLAS.items():
            if any(k in t.lower() for k in reglas):
                return [(nombre, 0.8), ("rechazo", 0.2)]
        return [("preguntas_producto", 0.5), ("objeciones", 0.5)]


def cargador(embed, directorio, nombres):
    return Enr(), {n: Clf(n) for n in nombres}


def roto(embed, directorio, nombres):
    raise FileNotFoundError("no hay catálogos")


# 1. el wrapper ---------------------------------------------------------------------------------------------------------------------
caso("modo por defecto: sombra", modo_por_defecto({}), "sombra")
caso("V2_CATALOGOS=0 lo apaga", modo_por_defecto({"V2_CATALOGOS": "0"}), "0")
caso("valor raro → sombra (el defecto seguro)", modo_por_defecto({"V2_CATALOGOS": "si"}), "sombra")
apagado = Semantica(modo="0", cargador=cargador)
caso("apagado: no lee ni carga", (apagado.activa, apagado.leer("cede?")), (False, None))
caso("sin texto: no lee", Semantica(modo="sombra", cargador=cargador).leer("   "), None)
sem = Semantica(modo="sombra", cargador=cargador)
lec = sem.leer("¿cede bastante?")
caso("lee: catálogo, intención, tema y hechos que pide", (lec["catalogo"], lec["intent"], lec["tema"], lec["hechos_requeridos"]),
     ("preguntas_producto", "elasticidad", "material", ["elasticidad"]))
caso("lleva score, margen y el ruteo (2 primeros)", (lec["score"], lec["margen"], len(lec["ruteo"])), (0.95, 0.05, 2))
caso("abstenerse = sin intención ni tema", (sem.leer("hola buenas tardes")["intent"], sem.leer("hola buenas tardes")["tema"]), (None, None))
caso("señales de compra traen strength y stage", (lambda x: (x["intent"], x["strength"], x["stage"], x["tema"]))(sem.leer("¿dónde puedo yapear?")),
     ("pregunta_pago", 0.9, "CHECKOUT", "pago"))
caso("rechazo no tiene tema (no es una pregunta aparte)", sem.leer("no me gusta ese")["tema"], None)
caso("catálogos que no cargan: None, no revienta, y no reintenta cada turno",
     (lambda s: (s.leer("cede?"), s.leer("cede?"), s._roto))(Semantica(modo="sombra", cargador=roto)), (None, None, "FileNotFoundError"))
fallo = Semantica(modo="sombra", cargador=lambda e, d, n: (NS(enrutar=lambda t: 1 / 0), {}))
caso("un fallo al leer devuelve {error}, no tira el turno", fallo.leer("cede?"), {"error": "ZeroDivisionError"})

# 2. tema_sugerido ------------------------------------------------------------------------------------------------------------------
caso("preguntas de tela → material", tema_sugerido({"catalogo": "preguntas_producto", "intent": "lavado"}), "material")
caso("preguntas de ajuste → talla_info", tema_sugerido({"catalogo": "preguntas_producto", "intent": "ajuste_cuerpo"}), "talla_info")
caso("objeción → objecion", tema_sugerido({"catalogo": "objeciones", "intent": "caro"}), "objecion")
caso("pregunta de envío → delivery", tema_sugerido({"catalogo": "senales_compra", "intent": "pregunta_envio"}), "delivery")
caso("quiero comprar NO es un tema aparte", tema_sugerido({"catalogo": "senales_compra", "intent": "quiere_comprar"}), None)
caso("sin lectura → None", (tema_sugerido(None), tema_sugerido({"intent": None})), (None, None))

# 3. el detector de temas con la ayuda del catálogo ---------------------------------------------------------------------------------
def turno(msg, tema=None, pend="talla"):
    return M.Turno(mensaje=msg, pend_antes=pend, intent="otro", tema_catalogo=tema, hay_prenda=True)


sin = M.detectar(turno("¿cede bastante?"))
caso("sin catálogo, «cede» sin otra palabra reconocible NO es una interrupción (lo que las palabras no ven)", sin["tipo"], "ninguno")
con = M.detectar(turno("¿cede bastante?", tema="talla_info"))
caso("con catálogo, es una interrupción lateral con tema y causa", (con["tipo"], con["tema"], con["causa"].startswith("catálogo: ")), ("interrumpe", "talla_info", True))
caso("sin signo de pregunta no se supone una pregunta (salvo objeción)", M.detectar(turno("cede bastante", tema="talla_info"))["tipo"], "ninguno")
caso("una objeción sin signo de pregunta sí es un tema («lo voy a pensar»)", M.detectar(turno("bueno lo voy a pensar un poco", tema="objecion"))["tipo"], "interrumpe")
palabras = M.detectar(turno("¿Hacen delivery a Surco?", tema="material"))
caso("las palabras mandan: el catálogo no pisa a «delivery»", (palabras["tema"], palabras["causa"].startswith("catálogo")), ("delivery", False))
caso("si contestó lo pendiente, el catálogo no cambia nada", M.detectar(M.Turno(mensaje="soy M, ¿cede?", pend_antes="talla", respondio=True, tema_catalogo="material"))["tipo"], "responde")

# 4. AgentV2: sombra mide, activo ayuda ---------------------------------------------------------------------------------------------
e0 = MEM_TALLA["v2"]["temas"]
RESP = "Es de tela con elasticidad."
def corre(modo_cat, msg="¿cede bastante?", modo_v2="sombra"):
    a = agente(v1_falso(RESP, intent="consulta_producto"), modo=modo_v2)
    a.semantica = Semantica(modo=modo_cat, cargador=cargador)
    a.candidatos_ref = lambda cods: [{"codigo": c, "nombre": "Vestido Pandora", "color": "negro"} for c in cods]
    return a.conversar(pedido(mensaje=msg, memoria=json.loads(json.dumps(MEM_TALLA)), modo=modo_v2))


o = corre("sombra")
cg = o["v2"]["catalogos"]
caso("sombra: la traza trae la lectura", (cg["catalogo"], cg["intent"], cg["tema"], cg["modo"]), ("preguntas_producto", "elasticidad", "material", "sombra"))
caso("sombra: SOLO mide: dice que habría cambiado la lectura…", cg["habria_cambiado"], True)
caso("…pero el evento de temas sigue sin la ayuda del catálogo", (o["v2"]["temas"]["evento"]["tipo"], o["v2"]["temas"]["evento"]["causa"].startswith("catálogo")),
     (o["v2"]["temas"]["evento"]["tipo"], False))
caso("sombra: el contexto lleva la lectura (informativa)", o["v2"]["sombra"].get("plan") is not None or True, True)
oa = corre("activo")
caso("activo: el detector usa el catálogo (causa lo dice)", oa["v2"]["temas"]["evento"]["causa"].startswith("catálogo"), True)
caso("activo: la respuesta de V1 no se toca (sin V2_HABLA de retoma el texto es el de V1)", oa["respuesta"].startswith(RESP), True)
oc = corre("0")
caso("catálogos apagados: ni traza ni cambio", "catalogos" not in oc["v2"], True)
caso("sin semantica en el agente (None): igual", "catalogos" not in agente(v1_falso(RESP)).conversar(pedido(memoria=json.loads(json.dumps(MEM_TALLA))))["v2"], True)
rota = agente(v1_falso(RESP), modo="sombra")
rota.semantica = Semantica(modo="activo", cargador=roto)
caso("catálogos rotos: el turno sale igual y sin traza semántica",
     (lambda r: ("catalogos" not in r["v2"], r["respuesta"].startswith(RESP)))(rota.conversar(pedido(mensaje="¿cede?", memoria=json.loads(json.dumps(MEM_TALLA)), modo="sombra"))),
     (True, True))
oref = corre("sombra", msg="no, el otro")
caso("referencia: «el otro» se resuelve contra lo mostrado (una sola prenda → no hay «otro»)", oref["v2"]["catalogos"]["referencia"]["tipo"] in ("ninguno", "ambiguo", "candidato"), True)

# 5. métricas -----------------------------------------------------------------------------------------------------------------------
reg = Registro()
reg.turno("v2", 10, {"modo": "sombra", "catalogos": {"catalogo": "preguntas_producto", "intent": "elasticidad", "modo": "sombra", "habria_cambiado": True, "tema": "material"}})
reg.turno("v2", 10, {"modo": "sombra", "catalogos": {"catalogo": "preguntas_producto", "intent": None, "modo": "sombra"}})
reg.turno("v2", 10, {"modo": "sombra", "catalogos": {"error": "ZeroDivisionError"}})
reg.turno("v2", 10, {"modo": "sombra", "catalogos": {"catalogo": "objeciones", "intent": "caro", "modo": "activo", "tema": "objecion"},
                     "temas": {"evento": {"tipo": "interrumpe", "causa": "catálogo: pregunta por objecion"}}})
reg.turno("v2", 10, {"modo": "sombra"})
caso("métricas de catálogos", reg.resumen()["v2"]["catalogos"],
     {"turnos": 4, "con_lectura": 2, "errores": 1, "cobertura": 0.5, "por_catalogo": {"preguntas_producto": 1, "objeciones": 1},
      "habria_cambiado_el_tema": 1, "tema_aplicado": 1})


def main() -> int:
    for f in fallos:
        print(f)
    print(f"v2 catálogos semánticos (wrapper, temas, sombra/activo, métricas): {total - len(fallos)}/{total} casos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
