"""Contenido de seguimiento por modelo desde baruka.pe (octubre de 2026, requerimientos de Luis).

    python3 contenido.py            # usa salida/catalogo_baruka_pe.json y salida/catalogo_oficial.json (ya extraídos)

Deja `salida/contenido_seguimiento.json`, una lista para `POST /api/contenido/importar` de JMD Ventas:
[{"codigo": "V50", "clave": "beneficio", "texto": "…", "media_url": ""}, …]

- **beneficio**: la sección «Beneficios» de la descripción del producto, copiada tal cual (sin reescribir), en viñetas.
- **testimonio**: la web NO lo tiene en un formato que se pueda leer. Las reseñas son de la app Trustoo, que se cargan
  con JavaScript (el modelo revisado el 16-10-2026 tenía 0), y robots.txt no permite /products/<handle>.js, donde
  estarían los videos. Se cargan a mano en el panel (Productos → Seguimiento).
- **tip**: no está en la web; lo carga Luis en el panel.
"""
from __future__ import annotations

import json
import os
import re
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(AQUI, "salida")
RE_TITULO_BENEF = re.compile(r"(?i)^\W*beneficios\W*$")
# Lo que corta la sección: otro título con emoji o de los habituales de la tienda.
RE_FIN = re.compile(r"(?i)^\W*(¿para qu[eé] sirve\?|para qu[eé] sirve|ocasiones|ideal para|cuidados|caracter[ií]sticas|"
                    r"detalles|descripci[oó]n|medidas|consulta tu talla|composici[oó]n|tela)\b")
RE_VINETA = re.compile(r"^\s*(?:[-•▪●✔✓✅❤️♥️💖💕✨⭐🔸🔹➡️👉]|❤️?|\W)\s*", re.U)


def beneficios(descripcion: str) -> str:
    lineas = [l.strip() for l in (descripcion or "").splitlines()]
    out, dentro = [], False
    for l in lineas:
        if not l:
            continue
        if RE_TITULO_BENEF.match(l) or re.match(r"(?i)^\W*beneficios\s*:", l):
            dentro = True
            resto = re.sub(r"(?i)^\W*beneficios\s*:?\s*", "", l)
            if resto:
                out.append(resto)
            continue
        if dentro:
            limpio = RE_VINETA.sub("", l).strip().lstrip("\ufe0f\u200d\u2764 ").strip()
            if RE_FIN.match(limpio) or (len(limpio) < 40 and limpio.endswith("?")):
                break
            if limpio:
                out.append(limpio)
    out = [x for x in out if len(x) >= 8][:8]
    return "\n".join("• " + x.rstrip(".") for x in out)


def main() -> int:
    cat = json.load(open(os.path.join(SALIDA, "catalogo_baruka_pe.json"), encoding="utf-8"))["productos"]
    ofi = json.load(open(os.path.join(SALIDA, "catalogo_oficial.json"), encoding="utf-8"))["productos"]
    codigo_de = {p["handle"]: p["code"] for p in ofi if p.get("handle") and p.get("code")}
    filas, sin = [], []
    for p in cat:
        cod = codigo_de.get(p.get("handle"))
        if not cod:
            continue
        b = beneficios(p.get("descripcion", ""))
        if b:
            filas.append({"codigo": cod, "clave": "beneficio", "texto": b, "media_url": ""})
        else:
            sin.append(cod)
    with open(os.path.join(SALIDA, "contenido_seguimiento.json"), "w", encoding="utf-8") as f:
        json.dump(filas, f, ensure_ascii=False, indent=1)
    print(f"beneficios: {len(filas)} modelos; sin sección «Beneficios» en la web: {len(sin)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
