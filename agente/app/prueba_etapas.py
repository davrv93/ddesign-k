"""Pruebas de la máquina de estados comercial. Se ejecutan al construir la imagen y a mano:

    python3 -m app.prueba_etapas

Cada caso: (etapa actual, intención que dio el clasificador, confianza, mensaje, último mensaje del bot)
→ (etapa esperada, intención esperada).
"""
from __future__ import annotations

import sys

from .etapas import decidir

CASOS = [
    # Los siete casos del encargo
    ("1 · interés no es compra", "seguimiento", "interesado", 0.96, "Sí, estoy interesado.", "", "seguimiento", "interesado"),
    ("1b · interés desde prospección", "prospeccion", "interesado", 0.96, "Sí, me interesa.", "", "seguimiento", "interesado"),
    ("2 · quiere comprarlo", "seguimiento", "intencion_compra", 0.91, "Sí, quiero comprarlo.", "", "cierre", "intencion_compra"),
    ("3 · pregunta el precio", "prospeccion", "consulta_precio", 0.93, "¿Cuánto cuesta?", "", "seguimiento", "consulta_precio"),
    ("4 · objeción de precio", "seguimiento", "objecion_precio", 0.90, "Está muy caro.", "", "seguimiento", "objecion_precio"),
    ("5 · lo va a pensar", "seguimiento", "objecion", 0.88, "Bueno, lo voy a pensar.", "", "seguimiento", "objecion"),
    ("5b · lo va a pensar en el cierre", "cierre", "objecion", 0.88, "Bueno, lo voy a pensar.", "¿Confirmamos tu pedido?", "seguimiento", "objecion"),
    ("6 · quiere reservarlo", "seguimiento", "intencion_compra", 0.90, "Quiero reservarlo.", "", "cierre", "intencion_compra"),
    ("7a · «sí» a ¿sigues interesada?", "seguimiento", "saludo", 0.40, "Sí.", "¿Sigues interesada en el vestido?", "seguimiento", "interesado"),
    ("7b · «sí» a ¿confirmamos la compra?", "cierre", "saludo", 0.40, "Sí.", "¿Confirmamos la compra?", "venta_confirmada", "confirmacion_compra"),
    # Lo que nunca debe pasar
    ("no salta de prospección a venta", "prospeccion", "confirmacion_compra", 0.95, "sí confirmo", "¿Confirmamos tu pedido?", "seguimiento", "interesado"),
    ("«sí» en seguimiento sin pregunta de confirmación", "seguimiento", "confirmacion_compra", 0.90, "si", "¿La boda es de día o de noche?", "seguimiento", "interesado"),
    ("confirmación sin que el bot la pida", "cierre", "confirmacion_compra", 0.92, "confirmo el pedido", "¿Qué talla necesitas?", "cierre", "interesado"),
    # Umbrales
    ("confianza baja no mueve la etapa", "prospeccion", "intencion_compra", 0.45, "mmm a ver", "", "prospeccion", "intencion_compra"),
    ("confianza media no llega a cierre", "prospeccion", "intencion_compra", 0.70, "creo que me animo", "", "seguimiento", "intencion_compra"),
    ("señal fuerte aunque el clasificador dude", "prospeccion", "otro", 0.30, "ya, resérvamelo", "", "cierre", "intencion_compra"),
    # Contexto y botones
    ("botón de talla de la tarjeta", "seguimiento", "consulta_talla", 0.80, "Talla M del V42", "", "cierre", "intencion_compra"),
    ("decir la talla no es comprar", "prospeccion", "consulta_talla", 0.90, "soy talla M", "", "seguimiento", "consulta_talla"),
    ("«no» a la confirmación cancela", "cierre", "otro", 0.30, "no", "¿Confirmamos tu pedido?", "seguimiento", "cancelacion"),
    ("«no» suelto no mueve nada", "seguimiento", "otro", 0.30, "no", "¿La boda es de día?", "seguimiento", "otro"),
    ("en el cierre una pregunta no retrocede", "cierre", "consulta_delivery", 0.90, "¿hacen envíos a Cusco?", "", "cierre", "consulta_delivery"),
    ("venta confirmada se mantiene", "venta_confirmada", "consulta_pago", 0.90, "¿a qué número yapeo?", "", "venta_confirmada", "consulta_pago"),
    ("saludo no cambia la etapa", "seguimiento", "saludo", 0.99, "hola", "", "seguimiento", "saludo"),
    # Primer contacto
    ("primer mensaje: preguntar no saca de prospección", "prospeccion", "consulta_disponibilidad", 0.99,
     "Hola, quisiera saber si todavía tienen este vestido", "", "prospeccion", "consulta_disponibilidad"),
    ("primer mensaje: el precio tampoco", "prospeccion", "consulta_precio", 0.99, "hola cuánto cuesta?", "", "prospeccion", "consulta_precio"),
    ("primer mensaje: comprar sí lo saca", "prospeccion", "intencion_compra", 0.95, "hola, quiero comprar este vestido", "", "cierre", "intencion_compra"),
]

# Método de venta: pedir cita para probárselo es intención de compra (cierre) y marca `cita`.
# (nombre, etapa, intención del clasificador, confianza, mensaje, pendiente) → (etapa, intención, cita)
CITA = [
    ("cita · quiere ir a probárselo", "seguimiento", "consulta_ubicacion", 0.97, "sí, quiero ir a probármelo", "", "cierre", "intencion_compra", True),
    ("cita · ¿puedo ir a probarme?", "seguimiento", "consulta_ubicacion", 1.0, "¿puedo ir a probarme el vestido?", "", "cierre", "intencion_compra", True),
    ("cita · sacar una cita", "seguimiento", "objecion", 0.40, "quiero sacar una cita", "", "cierre", "intencion_compra", True),
    ("cita · «sí» a ¿te lo pruebas o te lo separo?", "seguimiento", "saludo", 0.40, "sí", "probar", "cierre", "intencion_compra", True),
    ("cita · «sí» a otra pregunta no es cita", "seguimiento", "saludo", 0.40, "sí", "horario", "seguimiento", "interesado", False),
    ("cita · desde prospección también cierra", "prospeccion", "consulta_ubicacion", 0.99, "hola, quiero ir a probarme el vestido azul", "", "cierre", "intencion_compra", True),
    ("cita · «se puede probar?» es solo una consulta", "seguimiento", "consulta_ubicacion", 1.0, "¿se puede probar?", "", "seguimiento", "consulta_ubicacion", False),
    ("cita · separarlo sigue siendo pedido", "seguimiento", "intencion_compra", 0.95, "mejor sepáramelo", "probar", "cierre", "intencion_compra", False),
    ("cita · venta confirmada no retrocede", "venta_confirmada", "consulta_ubicacion", 0.9, "quiero ir a probármelo", "", "venta_confirmada", "intencion_compra", False),
]


def main() -> int:
    fallos = 0
    for nombre, etapa, intent, conf, msg, bot, e_esp, i_esp in CASOS:
        d = decidir(etapa, intent, conf, msg, bot, primer_mensaje=nombre.startswith("primer mensaje"))
        ok = d["etapa"] == e_esp and d["intent"] == i_esp
        fallos += not ok
        if not ok:
            print(f"   ✗ {nombre}: {etapa} + «{msg}» → {d['etapa']}/{d['intent']} (esperado {e_esp}/{i_esp}) [{d['motivo']}]")
    for nombre, etapa, intent, conf, msg, pend, e_esp, i_esp, c_esp in CITA:
        d = decidir(etapa, intent, conf, msg, "", primer_mensaje=etapa == "prospeccion", pendiente=pend)
        ok = (d["etapa"], d["intent"], d["cita"]) == (e_esp, i_esp, c_esp)
        fallos += not ok
        if not ok:
            print(f"   ✗ {nombre}: {etapa} + «{msg}» → {d['etapa']}/{d['intent']}/cita={d['cita']} (esperado {e_esp}/{i_esp}/{c_esp}) [{d['motivo']}]")
    # Indagando la necesidad (sin prenda mostrada): contar la fecha o preguntar no saca de prospección; comprar sí.
    for nombre, intent, conf, msg, e_esp in [
            ("indagando · la fecha no es seguimiento", "consulta_horario", 0.83, "el sábado 17", "prospeccion"),
            ("indagando · «solo estoy viendo» tampoco", "objecion", 0.99, "solo estoy viendo", "prospeccion"),
            ("indagando · comprar sí cierra", "intencion_compra", 0.95, "quiero comprar un vestido", "cierre")]:
        d = decidir("prospeccion", intent, conf, msg, "", indagando=True)
        fallos += d["etapa"] != e_esp
        if d["etapa"] != e_esp:
            print(f"   ✗ {nombre}: → {d['etapa']} (esperado {e_esp}) [{d['motivo']}]")
    total = len(CASOS) + len(CITA) + 3
    print(f"etapas     máquina de estados: {total - fallos}/{total} casos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
