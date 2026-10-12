"""El ciclo de V3: enrutador barato → LLM con herramientas → validador → respuesta.

Principio: el modelo decide qué hacer y redacta; el código valida y ejecuta. Un solo dueño del estado (la
ficha de `ficha.py`), que V3 escribe y el bot Go guarda opaca. Si algo falla (sin clave, rechazo doble,
excepción), se lanza y quien llama contesta V1.
"""
from __future__ import annotations

import re
import time

from .. import memoria
from . import cliente_llm, ficha as ficha_mod, prompt, validador
from .herramientas import Recolector

# Enrutador barato (antes del LLM): casos que no merecen una llamada.
RE_ASESORA = re.compile(r"\b(asesora|reclamo|queja|persona real|hablar con (alguien|una persona|un humano)|agente humano)\b", re.I)
RE_FUERA_GIRO = re.compile(r"\b(historia|c[oó]mo (se )?calcul|capital de|pol[ií]tica|presidente|matem[aá]tica|clima|"
                           r"software|programaci[oó]n|desarroll\w* (de|un) (software|app|web|sistema)|contrat\w* (de|un) "
                           r"(software|desarroll\w*)|tarea (de|del) colegio|receta de cocina)\b", re.I)
TALLAS_REALES = ("S", "M", "L")


class Rechazado(RuntimeError):
    """El validador rechazó la respuesta dos veces: quien llama contesta V1."""


def _traza(respuesta: str, motivos, usadas, vueltas, ms) -> dict:
    return {"version": "v3", "motivos": motivos, "herramientas": usadas, "vueltas": vueltas, "ms": ms}


def conversar(req, fichas, consultar_stock, pie=None, permitir_reserva: bool = False) -> dict:
    """Un turno de V3. `req` es un ChatIn; `fichas` el catálogo vivo; `consultar_stock` E.stock.consultar."""
    t0 = time.time()
    mensaje = (req.mensaje or "").strip()
    f = ficha_mod.normalizar(getattr(req, "memoria", None))
    if getattr(req, "etapa", ""):
        f["etapa"] = req.etapa

    # 1. Extracción determinista (lo que la clienta ya dijo) y la pregunta pendiente.
    lectura = memoria.leer(f, mensaje, ahora=memoria.ahora_lima())
    f["etapa"] = _etapa(f, lectura)

    # 2. Enrutador barato.
    if RE_ASESORA.search(mensaje):
        return _respuesta(f, "", "asesora", [], None, _traza("", [], ["asesora"], 0, _ms(t0)), req)
    if RE_FUERA_GIRO.search(mensaje):
        return _respuesta(f, "Eso no es mi giro 😅 ¿Te ayudo con algo de la tienda?", "responder", [], None,
                          _traza("", [], [], 0, _ms(t0)), req)

    # 3. LLM con herramientas.
    rec = Recolector(fichas, consultar_stock, pie=pie, permitir_reserva=permitir_reserva, mensaje=mensaje,
                     mostrados=f.get("mostrados") or [], pago_ok=bool((f.get("pedido") or {}).get("confirmado")))
    hecho = _precalculo(rec, lectura.get("datos") or {})
    msgs = prompt.construir(f, getattr(req, "historial", []) or [], mensaje, getattr(req, "canal", ""), hecho=hecho)
    salida = cliente_llm.responder(msgs, rec.esquema(), rec.ejecutar)
    out = prompt.leer_salida(salida["contenido"])
    _foto_pedida(rec, f, mensaje)
    motivos = validador.validar(out["respuesta"], rec.hechos, out["pregunta_hecha"], f.get("preguntado", []))

    # 4. Un rechazo: regenerar con los motivos. Dos: caer a V1.
    if motivos:
        msgs2 = msgs + [{"role": "user", "content": "Tu respuesta se rechazó por: " + "; ".join(motivos) +
                         ". Corrígela y vuelve a responder SOLO el JSON."}]
        salida2 = cliente_llm.responder(msgs2, rec.esquema(), rec.ejecutar)
        out = prompt.leer_salida(salida2["contenido"])
        motivos = validador.validar(out["respuesta"], rec.hechos, out["pregunta_hecha"], f.get("preguntado", []))
        if motivos and all(m.startswith(("pregunta ya respondida", "más de una pregunta")) for m in motivos):
            out["respuesta"], out["pregunta_hecha"] = _sin_preguntas(out["respuesta"]), ""   # el resto vale: se quita la pregunta
            motivos = validador.validar(out["respuesta"], rec.hechos, "", [])
        if motivos:
            raise Rechazado("; ".join(motivos))

    return _cierra(f, rec, out, salida["usadas"], salida["vueltas"], _ms(t0), req)


def _foto_pedida(rec: Recolector, f: dict, mensaje: str) -> None:
    """El código manda la foto que ella pidió y el modelo olvidó marcar: la prenda que nombra, o la que tiene en foco."""
    from .herramientas import RE_PIDE_FOTO
    m = mensaje.lower()
    if rec.fotos or not RE_PIDE_FOTO.search(m):
        return
    plano = memoria._plano(mensaje)
    for x in rec.fichas:
        nombre = memoria._plano(x.nombre)
        clave = [p for p in nombre.split() if len(p) >= 4 and p not in ("vestido", "conjunto", "blusa", "pantalon", "falda", "enterizo", "blazer")]
        if x.codigo.lower() in plano or (clave and all(p in plano for p in clave[:1])):
            rec.ejecutar("enviar_fotos", {"codigos": [x.codigo]})
            return
    if f.get("producto") and re.search(r"\b(como es|ver(lo|la)?|foto)\b", plano):
        rec.ejecutar("enviar_fotos", {"codigos": [f["producto"]]})


def _precalculo(rec: Recolector, datos: dict) -> str:
    """Lo que el código ya sabe sin preguntarle al modelo si lo consulta: evita que la revise «después» del
    embudo de ocasión/fecha (11-10-2026: con 20 reglas en el prompt, la excepción pierde contra el guion)."""
    partes = []
    talla = (datos.get("talla") or "").upper()
    if talla and talla not in TALLAS_REALES:
        partes.append(f"HECHO YA VERIFICADO: pidió talla {talla}; esa talla no existe, solo hay S, M y L. "
                       "Díselo en tu respuesta (no lo dejes para después ni preguntes la ocasión primero).")
    color = datos.get("color")
    if color:
        r = rec.ejecutar("buscar_catalogo", {"consulta": color})
        prendas = (r or {}).get("prendas") or []
        if prendas:
            desc = "; ".join(f"{p['codigo']} {p['nombre']} S/{p['precio']}" for p in prendas[:3])
            partes.append(f"HECHO YA VERIFICADO (color {color}): {desc}. Nómbralas en tu respuesta y mándalas "
                          "con enviar_fotos si corresponde; no preguntes la ocasión antes de decir esto.")
        else:
            partes.append(f"HECHO YA VERIFICADO: no tenemos nada color {color}. Dilo tal cual, no lo disimules "
                          "con otra prenda ni preguntes la ocasión antes.")
    return "\n".join(partes)


def _sin_preguntas(texto: str) -> str:
    """Quita las frases con «?» (la pregunta repetida) y deja lo demás; evita caer a V1 por una pregunta de más."""
    partes = re.split(r"(?<=[.!?])\s+|\n+", texto.strip())
    resto = [p for p in partes if "?" not in p]
    return " ".join(resto).strip() or texto.strip()


def _cierra(f: dict, rec: Recolector, out: dict, usadas, vueltas, ms, req) -> dict:
    """Actualiza la ficha con lo que hizo el turno y arma la respuesta para el bot Go."""
    f["herramientas"] = list(rec.usadas)
    accion = "responder"
    if rec.derivar:
        accion = "asesora"
    if rec.fotos:
        f["producto"] = rec.fotos[0]["codigo"]
        for x in rec.fotos:
            if x["codigo"] not in f["mostrados"]:
                f["mostrados"].append(x["codigo"])
    if rec.reserva:
        ficha_mod.fijar_pedido(f, rec.reserva["codigo"], rec.reserva["talla"], confirmado=True)
        f["etapa"] = "cierre"
        accion = "pedido"
    if rec.cita:
        f["sabemos"]["cita"] = f"{rec.cita['dia']}T{rec.cita['hora']}"
        accion = "responder"
    preg = out.get("pregunta_hecha", "")
    if preg:
        f["pendiente"] = preg if preg in memoria.PENDIENTES else ""
        if preg not in f["preguntado"]:
            f["preguntado"].append(preg)
    elif not rec.cita:
        f["pendiente"] = "" if rec.reserva else f["pendiente"]
    return _respuesta(f, out["respuesta"], accion, rec.fotos, rec.reserva,
                      _traza(out["respuesta"], [], usadas, vueltas, ms), req)


def _respuesta(f: dict, texto: str, accion: str, fotos, reserva, traza: dict, req) -> dict:
    sugerencias = [{"codigo": x["codigo"], "fuente": _fuente(x["codigo"]), "imagen": x["imagen"], "pie": x["pie"]}
                   for x in (fotos or [])]
    return {"respuesta": texto, "accion": accion, "codigo": (reserva or {}).get("codigo", ""),
            "talla": (reserva or {}).get("talla", ""), "sugerencias": sugerencias, "etapa": f.get("etapa", "prospeccion"),
            "memoria": f, "modelo_llm": cliente_llm.MODELO, "motor": "v3", "version": "v3", "v3": traza}


def _fuente(codigo: str) -> str:
    return "seed"  # el catálogo de 100 va apagado en producción (CATALOGO100=0)


def _etapa(f: dict, lectura: dict) -> str:
    if lectura.get("es_cita") or f["pedido"].get("confirmado"):
        return "venta_confirmada" if f["pedido"].get("confirmado") else "cierre"
    if f.get("producto") or f.get("mostrados"):
        return "seguimiento" if f.get("etapa") in ("", "prospeccion") else f["etapa"]
    return f.get("etapa") or "prospeccion"


def _ms(t0: float) -> int:
    return int((time.time() - t0) * 1000)
