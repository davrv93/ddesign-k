"""El motivo de una recomendación: una frase corta y completa de la ficha de la prenda (texto del backend, no de un modelo).

Las fichas vienen del catálogo con descripciones largas, a veces en MAYÚSCULAS, que repiten el nombre de la prenda y que un
corte ciego deja a medias («…con un escote corazón y, a la vez,.»). Aquí se arma una frase que se pueda decir tal cual o no se
dice ninguna: sin motivo, la recomendación sale igual, solo con el nombre."""
from __future__ import annotations

import re

FINAL_COLGANTE = frozenset({"y", "o", "a", "la", "el", "los", "las", "con", "de", "en", "que", "vez", "un", "una", "su", "sus", "al", "del",
                            "e", "u", "para", "por", "como", "más", "mas", "muy", "se", "lo", "le"})
MAX_CHARS = 150
MIN_PALABRAS = 3


def primera_frase(texto: str) -> str:
    """Hasta el primer punto que cierra una frase («330.00» y «S/.» no cuentan)."""
    t = re.sub(r"(\d)\.(\d)", r"\1·\2", texto.strip())
    m = re.search(r"(?<=[a-záéíóúñ0-9\)])[.!?](\s|$|(?=[A-ZÁÉÍÓÚÑ]))", t)       # también «talle.La falda» (sin espacio)
    frase = t[: m.start()] if m else t
    return frase.replace("·", ".").strip()


def motivo(nombre: str, detalle: str) -> str | None:
    detalle = (detalle or "").strip()
    if not detalle:
        return None
    frase = primera_frase(detalle).rstrip(".:;, ").strip()
    letras = [c for c in frase if c.isalpha()]
    if letras and sum(c.isupper() for c in letras) / len(letras) > 0.5:
        frase = frase.lower()
    frase = re.sub(r"\b[A-ZÁÉÍÓÚÑ]{4,}\b", lambda m: m.group(0).lower(), frase)      # «CLÁSICO ATEMPORAL» → «clásico atemporal»
    if nombre:
        frase = re.sub(re.escape(nombre), nombre, frase, flags=re.I)          # el nombre propio vuelve a su forma oficial
        m = re.match(rf"^{re.escape(nombre)}\s+(es|fue|está|presenta|tiene)\b\s*(.*)$", frase)
        if m:                                                                   # «Vestido Pandora es un clásico…» → «Es un clásico…»
            frase = f"{m.group(1).capitalize()} {m.group(2)}".strip()
    if len(frase) > MAX_CHARS:
        corte = frase[:MAX_CHARS].rfind(",")
        frase = frase[:corte].strip() if corte > 40 else ""
    palabras = frase.split()
    while palabras and palabras[-1].lower().strip(",;:") in FINAL_COLGANTE:
        palabras.pop()
    frase = " ".join(palabras).rstrip(",;: ")
    if len(frase.split()) < MIN_PALABRAS:
        return None
    return frase[0].upper() + frase[1:]
