"""Contexto compacto para el modelo local: el mismo prompt del agente, sin lo que no hace falta para redactar.

El prompt que arma `_prompt_comercial` ronda los 3.400 tokens (p95 4.200). Para DeepSeek no importa; para un modelo de
1,5B en un Mac de 24 GB (o en un EC2 sin GPU) es memoria al entrenar y segundos al responder. Aquí se recorta, sin
tocar ningún dato que la vendedora pueda necesitar:

- las fichas de las que NO se habla (ni son la del foco, ni la opción ofrecida, ni van en foto, ni las nombra ella) quedan
  en una línea: código, nombre, categoría, color, precio y la línea AHORA;
- fuera la ruta de la imagen de cada ficha, y el material del V42 repetido dentro de su descripción;
- el historial, las últimas 10 líneas.

El prompt de sistema, TIENDA, la etapa, la memoria y el formato de salida van intactos. Es determinista e idempotente, y
se aplica IGUAL al entrenar (`convertir.py`), al servir (`puente.py`) y al evaluar, a todos los modelos que se comparan.
"""
from __future__ import annotations

import re
import unicodedata

RE_FICHAS = re.compile(r"(PRODUCTO \(fichas; la primera es de la que se habla\):\n)(.*?)(\n\n)", re.S)
RE_HIST = re.compile(r"(HISTORIAL:\n)(.*?)(\n\nPRODUCTO)", re.S)
RE_COD = re.compile(r"\bV\d{2}\b")
MAX_HISTORIAL = 10
SE_QUEDA = ("categoría:", "color:", "precio:", "AHORA:")


def _plano(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _enteras(u: str) -> set[str]:
    """Códigos cuya ficha va completa: foco, opción ofrecida y fotos."""
    cods = set()
    for rx in (r"PRODUCTO DEL QUE SE HABLA: ([^\n]*)", r"OFRECES UNA SOLA OPCIÓN: ([^\n.]*)",
               r"FOTOS QUE EL BOT ENVIARÁ DESPUÉS DE TU TEXTO: ([^\n]*)", r"TELA DEL (V\d{2})"):
        for m in re.finditer(rx, u):
            cods |= set(RE_COD.findall(m.group(1)))
    return cods


def compactar_usuario(u: str) -> str:
    u = re.sub(r" \| imagen: [^|\n]*", "", u)
    # El V42 trae el material dos veces (dentro de la descripción del catálogo y en «material:»).
    u = re.sub(r" Material: [^|\n]*?(?= \| precio:)(?=[^\n]*\| material:)", "", u)
    msg = re.search(r"MENSAJE NUEVO DE[L LA]* CLIENT[AE]:\n(.*?)(?:\n\nFORMATO DE SALIDA|$)", u, re.S)
    dice = _plano(msg.group(1)) if msg else ""
    enteras = _enteras(u)

    def fichas(m: re.Match) -> str:
        out = []
        for i, linea in enumerate(m.group(2).split("\n")):
            cab = re.match(r"- (V\d{2}) · ([^|]+)", linea)
            if i == 0 or not cab:
                out.append(linea)
                continue
            cod, nombre = cab.group(1), _plano(cab.group(2)).split()
            if cod in enteras or cod.lower() in dice or (nombre and re.search(rf"\b{re.escape(nombre[-1])}\b", dice)):
                out.append(linea)
                continue
            partes = [p.strip() for p in linea.split(" | ")]
            out.append(" | ".join([partes[0]] + [p for p in partes[1:] if p.startswith(SE_QUEDA)]))
        return m.group(1) + "\n".join(out) + m.group(3)

    u = RE_FICHAS.sub(fichas, u, count=1)

    def hist(m: re.Match) -> str:
        lineas = m.group(2).split("\n")
        return m.group(1) + "\n".join(lineas[-MAX_HISTORIAL:]) + m.group(3)

    return RE_HIST.sub(hist, u, count=1)


def compactar(mensajes: list[dict]) -> list[dict]:
    """Los mismos mensajes con el último `user` compacto (los demás, intactos)."""
    out = [dict(m) for m in mensajes]
    for m in reversed(out):
        if m.get("role") == "user":
            m["content"] = compactar_usuario(m.get("content") or "")
            break
    return out
