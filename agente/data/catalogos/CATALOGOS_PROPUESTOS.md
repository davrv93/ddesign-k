# Catálogos semánticos propuestos para el agente de Baruka (vestidos)

Fuente: propuesta del dueño, 05-10-2026. Idea central: **varios catálogos separados, no un solo clasificador gigante**. No se hacen
1.000 intents: se hacen ~35–50 intents canónicos, cada uno con 20–100 expresiones equivalentes. Un catálogo CLASIFICA; no responde
ni inventa datos de la prenda.

Los cinco que más valor dan primero (prioridad): **1) preguntas de producto, 2) ocasión/estilo, 3) objeciones, 4) señales de compra,
5) referencias contextuales («ese / el otro / el segundo»).**

Forma sugerida de un intent:

```yaml
intent: ask_elasticity
examples: [se estira?, cede?, tiene lycra?, es flexible?, estira bastante?, la tela cede?, es elasticado?]
slots: {product_id: optional}
required_facts: [elasticity]
action: answer_product_attribute
```
```yaml
intent: buying_signal
examples: [quiero ese, me lo llevo, separame ese, ese me gusta, cómo pago, dónde yapear]
stage_transition: {from: [recommendation, evaluation], to: purchase_intent}
```

## Los 20 catálogos

1. **Intención de compra**: solo está mirando · quiere catálogo · quiere recomendación · compara opciones · pregunta precio · pregunta
   disponibilidad · quiere reservar · quiere comprar · quiere pagar · quiere delivery · quiere probarse · quiere hablar con asesora.
2. **Ocasión**: matrimonio · graduación · quinceañero · cumpleaños · bautizo · cena · fiesta · gala · evento corporativo · iglesia ·
   playa · sesión de fotos · civil · recepción · fiesta de promoción.
3. **Estilo deseado**: elegante · casual · sexy · sobrio · romántico · juvenil · clásico · moderno · minimalista · llamativo · delicado ·
   formal · vintage · boho · femenino · discreto.
4. **Preferencias físicas de la prenda**: largo/corto · con mangas/sin mangas · escote alto/bajo · espalda abierta/cerrada ·
   ceñido/suelto · con abertura/sin abertura · con brillo/sin brillo · con pedrería · estampado/liso · cintura marcada · falda amplia ·
   recto · asimétrico.
5. **Color** (también lenguaje informal): rojo vino · borgoña · guinda · nude · palo rosa · champagne · verde botella · verde olivo ·
   azul noche · azul marino · negro · crema · hueso · ivory · dorado · plateado. Equivalencias: «rojo oscuro» → borgoña/guinda ·
   «color piel» → nude · «azul casi negro» → azul noche.
6. **Talla y ajuste** (importantísimo): me queda apretado · me queda flojo · soy S arriba y M abajo · tengo mucho busto · tengo poca
   cintura · tengo cadera ancha · soy bajita · soy alta · tengo brazos gruesos · quiero ocultar barriga · no quiero que marque ·
   quiero que marque cintura. **No se convierte directamente en una talla: se convierte en RESTRICCIONES para recomendar.**
7. **Objeciones de compra** (muy valioso): está caro · lo voy a pensar · no estoy segura · no sé si me quedará · no sé si el color me
   favorece · tengo miedo que no llegue · está muy corto · está muy escotado · quiero ver otros · en otra tienda está más barato ·
   no confío en comprar por internet.
8. **Señales de interés / de compra** (para decidir cuándo dejar de preguntar y cerrar): me gusta · ese sí · qué lindo · me encanta ·
   ¿tienes mi talla? · ¿cuánto cuesta? · ¿cómo pago? · ¿hacen envío? · ¿me lo separas? · quiero ese. Salida tipo
   `{intent: buying_signal, strength: 0.93, stage: closing}`.
9. **Rechazo** (distinto de una objeción; modifica de inmediato el perfil temporal de la conversación): no me gusta · no es mi estilo ·
   muy largo · muy corto · muy serio · demasiado llamativo · no quiero ese color · no me convence · muéstrame otro.
10. **Comparación**: cuál me queda mejor · cuál es más elegante · cuál es más fresco · cuál se arruga menos · cuál estiliza más ·
    cuál recomiendas · cuál sirve mejor para matrimonio · diferencia entre este y este. Necesita dos o más product_id y atributos reales.
11. **Urgencia**: lo necesito hoy · para mañana · para este sábado · para el fin de semana · urgente · viajo mañana. Debe elevar la
    importancia de: stock real, tienda disponible, entrega, horario, ubicación.
12. **Disponibilidad logística**: hacen delivery · cuánto demora · llega a provincia · hacen envío a Lima · puedo recoger · dónde están ·
    qué horario tienen · llega hoy · cuánto cuesta envío.
13. **Pago**: Yape · Plin · transferencia · tarjeta · efectivo · cuotas · adelanto · separación · pago contra entrega.
14. **Confianza** (muy importante en WhatsApp): ¿son tienda real? · ¿tienen local? · ¿tienen Instagram? · ¿tienen referencias? ·
    ¿hacen cambios? · ¿qué pasa si no me queda? · ¿puedo devolverlo? · ¿emiten comprobante? · ¿cómo sé que llegará?
15. **Cambios y devoluciones**: puedo cambiar talla · puedo cambiar color · aceptan devolución · cuánto tiempo tengo · si no me queda qué
    hago · vestido en oferta tiene cambio · quién paga el envío de cambio.
16. **Conversación social** (para sonar humano; evita tratar cada mensaje como una intención comercial nueva): hola · buenas · gracias ·
    ok · ya · perfecto · jajaja · lindo · genial · listo · ahí te aviso · luego te escribo.
17. **Referencias contextuales** (fundamental en WhatsApp; se resuelven contra el contexto visual/conversacional): ese · este ·
    el primero · el de arriba · el rojo · el otro · ese mismo · ese vestido · el segundo · el de la foto.
18. **Correcciones** (sobrescriben el estado anterior): no, el otro · no dije rojo · quise decir M · me equivoqué · era para noche ·
    no es matrimonio, es graduación.
19. **Ambigüedad** (no deben disparar decisiones fuertes; el agente pregunta, no infiere): puede ser · quizá · no sé · algo así ·
    más o menos · cualquiera · sorpréndeme · no tengo idea.
20. **Etapa comercial**: DISCOVERY → NEED_IDENTIFIED → RECOMMENDATION → EVALUATION → OBJECTION → PURCHASE_INTENT → CHECKOUT →
    POSTSALE; cada frase modifica probabilidades. «¿Qué vestidos tienes?» → DISCOVERY · «Es para un matrimonio de noche» →
    NEED_IDENTIFIED · «Me gusta el V05» → EVALUATION · «Está un poco caro» → OBJECTION · «¿Me lo separas?» → PURCHASE_INTENT ·
    «¿Dónde yapear?» → CHECKOUT.

El catálogo 0 (preguntas de producto) son las 250 preguntas de `faq_vestidos.txt`, agrupadas en 25 intenciones (A–Y).
