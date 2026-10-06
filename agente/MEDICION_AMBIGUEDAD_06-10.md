# ¿Cuánta ambigüedad real hay? Medición sobre 613 mensajes (06-10-2026)

Pregunta: ¿hace falta un modelo (extractor pequeño, Jev de preguntas) o bastan mejores reglas? Método: se reprodujeron los 613 mensajes de
las 100 conversaciones web contra el agente (V2 activo, **sin LLM de pago**: solo lo que el código entiende), y se leyeron a mano los turnos
con respuesta débil. Datos: `pruebas_conv/ab_base_pre_v2.jsonl` (fuera de git). Los casos están nombrados por conversación y turno.

## 1. Respuestas débiles a mensajes con contenido: 45 de 613 (7,3 %)

«Débil» = relleno («¡Dale! Aquí estoy»), «escribe *4*», acuse suelto («¡Claro! 😊») o solo el saludo, ante un mensaje de ≥ 4 palabras o con «?».

| Qué eran | Turnos | Qué hace falta |
|---|---|---|
| **Respuesta correcta** (despedida, «lo voy a pensar», «dame un momento», «¿eres un bot?»…) | 17 | nada |
| **Política de descuentos** («¿me haces descuento?», «¿me lo dejas en 250?» insistido) | 9 | decisión de negocio: hoy se repite «escribe *4*» aunque diga que no quiere escribir a nadie |
| **Hueco de regla** | 15 | mejor regla, sin modelo |
| **Ayudaría la semántica** (typo «bestido», alabanza, «¿apretado o suelto?») | 4 | catálogo semántico o fuzzy |
| **Necesita un modelo generativo** | **0** | — |

Los 15 huecos de regla: «¿lo tienen en rojo?» contestado «¡Sí, tenemos el Irla!» (ignora el color, 2 veces); «me lo dejas en 250» → «sin apuro»;
«no me mandes fotos, quiero el Holly» → «¡Claro!»; «¿puedo probar antes? ¿a qué hora?» y «¿te lo separo o voy a probarlo?» → «escribe *4*» (es una cita);
«¿qué hay de nuevo?» → «escribe *4*» (es el catálogo); «dame los datos, te deposito» sin pedido → relleno; «busco algo elegante pero fresco» → relleno; mensajes con [audio].

## 2. Fallos con respuesta fluida (no los ve la métrica anterior)

- **Color** (36 mensajes piden un color): en 8 casos reales la respuesta ignora el color o no dice que no hay (rosado del anuncio ×3, rojo/vino ×3, azul turquesa → otra prenda).
- **Cita** (35 preguntas de visita): 3–4 casos reales sin horario ni cita («¿puedo pasarlo a probar este finde?», «¿lo puedo probar antes?»).
- **Describir un vestido que vio** («azul, largo, con brillitos en el pecho»): ~4 casos, depende del RAG y de la ficha, no de una regla.

## 3. Resumen

| | Turnos | % de 613 |
|---|---|---|
| Huecos de regla (en débiles + color + cita) | ≈ 26 | 4,2 % |
| Ayudaría la semántica (e5) | ≈ 9 | 1,5 % |
| Política de descuentos | 9 | 1,5 % |
| Necesitan un modelo generativo | **0** | 0 % |

**Conclusión:** con esta evidencia no se justifica un extractor generativo ni un «Jev de preguntas». El problema de selección de la
siguiente pregunta casi no aparece (solo «escribe *4*» seguido de «¿el evento es de día o de noche?»). Lo que sí se justifica: arreglar los
≈ 26 huecos de regla sobre el contrato `SolicitudCliente` y decidir la política de descuentos.

## 4. Catálogos semánticos en sombra: ¿coinciden con las reglas?

Los catálogos (e5) corren en V2 desde el 05-10 y dan lectura en 610 de 613 turnos, pero solo **316** traen una intención (en el resto se abstienen).

| Campo | Ambos | Solo regla | Solo semántica | Quién acierta cuando discrepan |
|---|---|---|---|---|
| Cita | 13 | 33 | 8 | la semántica añade ≈ 6 reales («¿puedo pasarlo a probar este finde?», «¿lo puedo ver en persona?», «¿puede ser a las 10 am?»); la regla cubre 33 en los que la semántica se abstiene. **Se complementan.** |
| Otras opciones | 12 | 31 | **67** | la semántica **sobre-dispara**: «busco un vestido» y «¿tienen este vestido?» salen como «pide opciones». **No sirve** para este campo. |
| Color | 7 | 19 | 5 | la semántica distingue «¿viene en otros colores?» (4 de 5 reales), pero no sabe de «quiero ese color concreto». **Complementaria.** |

**Conclusión:** la semántica de e5-small no reemplaza a las reglas; sirve como segunda opinión, sobre todo para cita. No hay motivo, por ahora, para activarla como decisora.
