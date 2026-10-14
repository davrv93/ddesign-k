"""Grupos de color (app/colores.py): seed completo, normalización, prioridad de la tienda, foto del grupo y el orden de
buscar_catalogo. Sin LLM.    python3 -m app.prueba_colores"""
from __future__ import annotations

import sys

from . import colores, datos, ficha_producto, fotos_color
from .v3.herramientas import Recolector

fallos = []


def ok(cond, msg):
    if not cond:
        fallos.append(msg)
        print("  FALLO:", msg)


def fichas_web() -> list[datos.Ficha]:
    """El catálogo oficial (seed/fichas_producto.json, baruka.pe) como fichas vivas: lo que E.fichas trae en producción."""
    out = []
    for codigo, d in ficha_producto.cargar().items():
        w = d.get("web") or {}
        if d.get("retirado") or not w:
            continue
        f = datos.Ficha(codigo=codigo, nombre=d.get("nombre", ""), categoria="", color=", ".join(w.get("colores") or []),
                        detalle="", tallas="", fuente="seed", precio=w.get("precio"), imagen=f"/media/tienda/{codigo.lower()}.jpg")
        f.categoria = datos.categoria_por_nombre(f)
        out.append(f)
    return out


def main() -> int:
    fichas = fichas_web()
    ok(len(fichas) >= 90, f"catálogo oficial cargado: {len(fichas)} fichas")
    # 1. Todos los colores de baruka.pe tienen grupo (o están declarados sin grupo), sin acentos ni mayúsculas de por medio.
    sin = sorted({c for f in fichas for c in colores.colores_de(f) if colores.normaliza(c) not in colores.grupos()})
    ok(not sin, f"colores de la web sin entrada en el seed: {sin}")
    ok(colores.grupo_de("Azul Noche") == "sobrio" and colores.grupo_de("Marrón.") == "sobrio", "normalización (mayúsculas, acento, punto)")
    ok(colores.grupo_de("Black") == "sobrio" and colores.grupo_de("Turqueza") == "vibrante", "alias (black, turqueza)")
    ok(colores.grupo_de("Clara") == "", "«clara» queda sin grupo")
    # 2. Lo que manda la tienda pisa el seed; lo que no manda, no existe.
    cfg = {"Rojo": "sobrio", "Azul noche": "vibrante", "verde": "otra cosa"}
    ok(colores.grupo_de("rojo", cfg) == "sobrio" and colores.grupo_de("azul noche", cfg) == "vibrante", "config de la tienda manda")
    ok(colores.grupo_de("negro", cfg) == "" and colores.grupo_de("verde", cfg) == "", "config: sin entrada o grupo inválido = sin grupo")
    ok(colores.grupos({}) == colores.grupos(None), "config vacía = seed")
    # 3. Foto del grupo: el Blear (V45) tiene azul noche y vino (sobrios) y barney (vibrante).
    a, c = colores.foto_del_grupo("V45", "sobrio")
    ok(a == "v45.jpg" and c == "Azul noche", f"foto sobria del V45: {a} {c}")
    a, c = colores.foto_del_grupo("V45", "vibrante")
    ok(a == "v45_3.jpg" and c == "Barney", f"foto vibrante del V45: {a} {c}")
    ok(colores.foto_del_grupo("V45", "") == ("", ""), "sin grupo, sin foto")
    # 4. disponibles(): colores reales del catálogo, los más frecuentes primero.
    vib = colores.disponibles(fichas, "vibrante")
    sob = colores.disponibles(fichas, "sobrio")
    ok("rojo" in vib[:5] and "negro" in sob[:5], f"disponibles: {vib[:5]} / {sob[:5]}")
    ok(not set(vib) & set(sob), "un color no está en los dos grupos")
    # 5. buscar_catalogo con grupo: las prendas con colores del grupo van primero y se anotan.
    def stock(codigos):
        return {c.upper(): {"product": {"code": c}, "online": {"S": 1, "M": 1, "L": 1}} for c in codigos}
    rec = Recolector(fichas, stock, mensaje="muestrame vestidos")
    rec.grupo = "vibrante"
    res = rec.ejecutar("buscar_catalogo", {"consulta": "vestido", "categoria": "vestido"})
    prendas = res["prendas"]
    ok(all(p.get("colores_vibrantes") for p in prendas), f"con estilo, las primeras prendas traen colores vibrantes: {[(p['codigo'], p.get('colores_vibrantes')) for p in prendas]}")
    ok("aviso_estilo" in res, "aviso de estilo en la respuesta de buscar_catalogo")
    rec.grupo = "sobrio"
    res = rec.ejecutar("buscar_catalogo", {"consulta": "vestido rojo", "categoria": "vestido"})
    ok("aviso_estilo" not in res and not any("colores_sobrios" in p for p in res["prendas"]), "pidió un color: el grupo no manda")
    rec.color_pedido = "azul"
    res = rec.ejecutar("buscar_catalogo", {"consulta": "vestido", "categoria": "vestido"})
    ok("aviso_estilo" not in res, "color ya pedido en la memoria: el grupo no manda")
    # 6. enviar_fotos con grupo: la foto del Blear sale en un color del grupo.
    rec = Recolector(fichas, stock, mensaje="muestrame el blear")
    rec.grupo = "vibrante"
    res = rec.ejecutar("enviar_fotos", {"codigos": ["V45"]})
    ok(rec.fotos and rec.fotos[0]["imagen"].endswith("v45_3.jpg") and rec.fotos[0]["color_foto"] == "Barney",
       f"foto del Blear en vibrante: {rec.fotos[0] if rec.fotos else res}")
    rec = Recolector(fichas, stock, mensaje="muestrame el blear")
    rec.grupo, rec.color_pedido = "vibrante", "vino"
    rec.ejecutar("enviar_fotos", {"codigos": ["V45"]})
    ok(rec.fotos and rec.fotos[0]["color_foto"] == "Vino", f"el color pedido gana al grupo: {rec.fotos[0]['color_foto'] if rec.fotos else '-'}")
    print(f"prueba_colores: {len(fallos)} fallos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
