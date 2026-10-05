# Agente conversacional (servicio `agente`)

Hace que el bot de WhatsApp converse en lugar de sólo mostrar el menú. El bot Go le pasa
todo el **texto libre** (lo que no es un número del menú, un código ni una talla). Si el
agente no responde, el bot vuelve a Gemini como antes.

```
mensaje ─► embedding local ─┬─► clasificador de intención ─► acción del bot (catálogo, foto, pedido, asesora)
           (e5-small, ONNX)  ├─► clasificador de prenda
                             └─► clasificador comercial ─► máquina de etapas ─► RAG + guía de la etapa ─► LLM ─► respuesta
```

## Agente comercial: tres etapas (04-10-2026)

La tienda vende en tres etapas y el bot no puede saltárselas: **prospección** (conocer a la clienta) →
**seguimiento** (resolver dudas y validar el interés) → **cierre** (concretar) → **venta confirmada** (envío,
total, pago y comprobante). Antes, decir la talla ya disparaba «¿Confirmamos tu pedido?» y el bot se quedaba
repitiendo «Responde SI o NO».

| Pieza | Archivo | Qué hace |
|---|---|---|
| Clasificador comercial | `data/comercial.csv` (529 frases, 20 intenciones), `app/entrenar.py` | Qué quiere la clienta en términos de venta: `interesado`, `intencion_compra`, `confirmacion_compra`, `objecion`, `objecion_precio`, `consulta_precio`, `consulta_material`… Se mide con `data/prueba_comercial.csv` (68 frases que no entran al entrenamiento): **98,5 %** (97,1 % antes de la prueba con conversaciones) |
| Máquina de etapas | `app/etapas.py` | Reglas explícitas, sin LLM. Devuelve la etapa nueva y el motivo |
| Guion por etapa | `app/venta.py` | Prompt de sistema, guía de cada etapa, totales ya calculados, datos de pago |
| Pruebas | `app/prueba_etapas.py` | 40 casos (los 7 del encargo, el primer contacto, la cita para probarse, la indagación y «¿me lo apartas?»). Se ejecutan al construir la imagen: si falla uno, no hay imagen |
| Memoria y hilo | `app/memoria.py`, `app/prueba_memoria.py` | Lo que ya sabemos de la clienta, la pregunta pendiente, la siguiente pregunta, la temperatura y la cita (secciones siguientes). 235 casos, también en el build |

Reglas de `etapas.py`:

- **Interés no es compra.** «Sí, me interesa» o preguntar el precio lleva a seguimiento, nunca a cierre.
- Solo una intención clara de compra («quiero comprarlo», «resérvamelo», «¿cómo pago?») lleva a cierre. **Pedir cita para
  probárselo** también («quiero ir a probármelo», o «sí» a «¿te lo pruebas o te lo separo?»): marca `cita` en la decisión.
- Mientras se indaga la necesidad sin haber mostrado prenda (`indagando`), contar la fecha o preguntar no saca de prospección.
- **El «sí» depende de la pregunta pendiente** (memoria, ver «Memoria y hilo»): con `pendiente: "confirmar"` es una
  confirmación; con cualquier otra es interés. Una confirmación solo vale en cierre. Sin memoria (pruebas,
  llamadas viejas) la pendiente se deduce del último mensaje del bot.
- **Umbrales de confianza:** ≥ 0,80 se usa tal cual; entre 0,60 y 0,80 solo avanza un paso prudente (una
  intención de compra dudosa llega a seguimiento, no a cierre); < 0,60 la etapa no cambia.
- Una objeción («está caro», «lo voy a pensar») devuelve la conversación a seguimiento, incluso desde el cierre.
- **El primer mensaje se queda en prospección** («hola, ¿todavía tienen este vestido?»), salvo una compra explícita.
- Decir la talla no arma el pedido; solo lo arma en cierre.

La etapa **no se guarda en el agente**: quien llama la manda en `etapa` y la recibe de vuelta. El bot Go la
guarda en el contexto de la conversación; la UI web, en memoria. Cada decisión queda en el log:

```
[CLASSIFIER] {"conversation_id": "12", "mensaje": "ya, resérvamelo", "stage_anterior": "seguimiento",
 "intent": "intencion_compra", "confidence": 0.9, "nivel": "alta", "stage_nuevo": "cierre",
 "motivo": "señal fuerte de compra", "accion": "pedido", ...}
```

`docker logs kddesign_agente 2>&1 | grep CLASSIFIER` muestra por qué el bot está en cada etapa.

**Vestido del anuncio** (`PRODUCTO_DEMO=V42`): la clienta llega desde un anuncio de clic a WhatsApp y dice
«este vestido» sin nombrarlo. **Solo si llegó por el anuncio** (`desde_anuncio`, que el bot Go saca de
`contextInfo.externalAdReply` del mensaje y recuerda toda la conversación) se asume la prenda: primero la que
nombre el título del anuncio (`anuncio`) y, si no nombra ninguna, la de `PRODUCTO_DEMO`. Sin anuncio, «¿tienen
este vestido?» recibe «¿me compartes la foto o el nombre del vestido que viste?» (`pide_cual`): una vendedora
no adivina. En el chat de prueba, el botón **Desde anuncio** simula la llegada por el anuncio. Mientras el bot espera saber cuál es
(pendiente `cual_prenda` o `describir_prenda` en la memoria), lo que no la identifica («oh sí», «a ver un momento», «ahora te digo el nombre») recibe
«aquí te espero» sin fotos (`espera_cual`); «no la tengo / no me acuerdo» recibe «cuéntame cómo era». Solo con
una descripción (color, largo, mangas, brillos…: `RE_DESCRIBE`) o un nombre se buscan prendas, y el LLM las
presenta como posibles («¿es alguno de estos?»), nunca como «el que mencionaste». Con anuncio, la
prenda es la prenda en foco, se enseña una sola vez y no se mezclan
otros modelos salvo que los pida («otros modelos», «vestidos»). Lo que el catálogo no guarda (material,
ocasiones, lámina de materiales) está en `seed/producto_demo.json`; la lámina se manda cuando pregunta por
el material.

**Datos de venta:** costos de envío en `seed/venta.json` (el total lo calcula el código, no el LLM). Los
datos de pago (Yape, titular) van en `seed/pago.md`, que **no está en el repositorio** (`.gitignore`): se
copia al servidor con el rsync del deploy. Sin ese archivo, el bot deriva el pago a una asesora. Plantilla:
`seed/pago.md.ejemplo`. Solo se entregan con el pedido confirmado.

**WhatsApp** (`backend/internal/bot/bot.go`): en los estados de talla y confirmación, lo que no es una talla
ni un sí/no va al agente con `etapa`, `producto` y `talla`; si sigue en cierre se recuerda el paso, y si dudó
o pidió ver otros modelos se suelta el pedido (se libera la reserva). Tras el *SI* el estado es
`esperando_pago`: el agente lleva Lima/provincia → total → pago, la foto que llegue es el comprobante (queda
anotado en el pedido) y luego se pide la dirección.

| Pieza | Qué es |
|---|---|
| Embeddings | `Xenova/multilingual-e5-small` cuantizado (MIT, multilingüe, 384 dimensiones), servido con fastembed + onnxruntime en CPU. Se hornea en la imagen. Los mensajes llevan el prefijo `query: ` y las fichas `passage: `. |
| Clasificador | Estandarización + regresión logística sobre los embeddings: intenciones del bot, categorías de prenda e intenciones comerciales (tres cabezas, un solo embedding por mensaje). |
| RAG | 100 fichas de `caracteristicas_y_tallas.txt` + el catálogo real de la tienda (`/api/public/catalog`, se refresca cada 5 min). Sólo lo semiestático: diseño, color, precio. **Nunca stock.** |
| Stock | Herramienta, no conocimiento: `GET /api/public/stock?codes=…` del backend en el momento de responder (`app/stock.py`). Disponible = físico − reservas vigentes, más stock por sucursal. |
| Few-shot | Los 4 ejemplos más parecidos de los datasets se pasan al LLM como guía de tono. |
| LLM | `deepseek/deepseek-v4-flash` por OpenRouter, con `deepseek/deepseek-chat-v3.1` de respaldo. Sin clave o sin red, devuelve la respuesta de referencia del ejemplo más parecido. |

## Memoria y hilo (04-10-2026)

Antes el agente no tenía memoria: en cada mensaje releía los últimos 8–14 mensajes y adivinaba. Tras «¿me
compartes la foto o el nombre del vestido?», un «oh sí» llegaba al LLM, que inventaba un vestido («te paso el que
mencionaste») y mandaba fotos al azar; volvía a preguntar «¿qué te gustó del modelo?» o la ocasión ya contestada.
Cada síntoma tenía su parche (`esperando_cual`, `RE_PIDE_CONFIRMAR`, `talla_conocida`, `_ultimos_del_bot`). Ahora
hay un solo mecanismo: una **ficha de la conversación** que se actualiza en cada mensaje y viaja con la petición,
igual que la etapa.

```json
{"etapa":"seguimiento","producto":"V42","mostrados":["V42"],"pendiente":"que_le_gusto",
 "sabemos":{"ocasion":"matrimonio","horario":"noche","fecha":null,"talla":"M","estatura":null,"color":null,
            "presupuesto":null,"envio":null,"ciudad":null,"le_gusto":null},
 "objeciones":["precio"],"llego_por":"anuncio V42","preguntado":["ocasion","horario","talla","fecha","que_le_gusto"]}
```

1. **La pregunta pendiente (el hilo).** Cada vez que el bot pregunta algo se anota qué espera: `cual_prenda`,
   `describir_prenda`, `ocasion`, `horario`, `talla`, `estatura`, `color`, `fecha`, `que_le_gusto`, `separar`, `probar`,
   `cita`, `confirmar`, `lima_o_provincia`, `pago`, `voucher`, `direccion`, `otras_opciones`, `foto`. Si la pregunta la
   hace el código (flujo del pedido, «¿cuál es?») la fija él; si la redacta el LLM, se reconoce en su texto
   (`memoria.DETECTOR`, solo en las frases con «?»). El mensaje siguiente se lee **primero** como respuesta a esa
   pendiente (`memoria.leer`): si la responde, se guarda el dato y se limpia; si no («a ver un momento»), sigue
   pendiente y nadie la inventa. `cual_prenda`, `describir_prenda`, `confirmar`, `voucher`, `direccion`, `foto` y `cita`
   no se sueltan solas: hasta que se respondan. Las demás se sueltan si el bot no vuelve a preguntar.
2. **Extraer, no adivinar.** De cada mensaje de la clienta se sacan con reglas los datos que trae (talla, fecha,
   estatura, ciudad y Lima/provincia, ocasión, día/noche, color, presupuesto). Una letra suelta solo es talla si se
   preguntó la talla o el mensaje es corto («mido 1.60 m» no es talla M); «hoy» solo es fecha si se preguntó para
   cuándo; una ocasión genérica («la fiesta es de noche») no pisa una concreta («matrimonio»).
   Con Jev en `cascada`, la **misma** llamada de la cascada lleva además preguntas tipadas de memoria (ocasión y
   talla como `choice`, día/noche, y `noul` «¿el mensaje responde a la pregunta pendiente?»). Se llama a Jev si el
   clasificador local duda **o** si hay una pendiente de ocasión, talla, día/noche o «¿cuál es?» que las reglas no
   resolvieron; nunca dos veces por mensaje. Jev propone (umbral 0,80, `JEV_UMBRAL_MEMORIA`); si las reglas
   encontraron el dato, mandan ellas. En `sombra` lo de Jev solo queda en `[JEV]`. Sin Jev, solo reglas.
3. **La siguiente pregunta la elige el código** (`memoria.siguiente`): la primera de la etapa que no se sepa ni
   se haya hecho ya (`memoria.ORDEN`):

   | Etapa | Orden |
   |---|---|
   | prospección | ocasión → para cuándo → día/noche → talla (solo con una prenda ya mostrada) |
   | seguimiento | para cuándo → día/noche → talla → ¿probártelo o separarlo? (`probar`, con la talla sabida) |
   | cierre | talla → confirmar (lo lleva el flujo del pedido) |
   | venta confirmada | Lima o provincia → ¿te paso los datos de pago? → comprobante |

   Desde el método de venta (sección siguiente), la temperatura cambia el seguimiento: caliente pregunta `probar` antes
   que la talla; fría no lo pregunta. «¿Qué le gustó?», «¿separarlo?», estatura y color ya no están en el orden (siguen
   en `PREGUNTAS`/`DETECTOR` por si el LLM los pregunta).

   El LLM recibe en el prompt `LO QUE YA SABEMOS`, `ESTÁS ESPERANDO` y `SIGUIENTE PREGUNTA: «…» (hazla tal cual,
   o no preguntes nada)`; el historial queda como contexto de tono. Si aun así el LLM repite una pregunta ya hecha
   o ya contestada, `memoria.quitar_repetidas` la quita de su texto (con el porqué que cuelga de ella).

**Dónde vive.** El agente sigue sin estado: `ChatIn.memoria` y `FotoIn.memoria` (opcionales) y la respuesta trae
`memoria` actualizada, `siguiente_pregunta` y `lectura` (qué pasó con la pendiente). Si no llega memoria (llamadas
viejas), `memoria.reconstruir` la arma repasando el historial con las mismas reglas.

- **Bot Go** (`backend/internal/bot/memoria.go`): la guarda en `convContext.Memoria` (JSON opaco, para no perder
  campos que el agente añada), la manda en `askAgent` y `agentPhoto` y guarda la que vuelve. Los reinicios del
  flujo (`setState(..., convContext{})`) conservan la memoria, como el anuncio, pero sueltan la pendiente. Al entrar
  en un estado del pedido Go fija su pendiente: `esperando_talla` → `talla`, `esperando_confirmacion` →
  `confirmar` (y `sabemos.talla`), `esperando_pago` → `lima_o_provincia`, `esperando_ubicacion` → `direccion`. Si
  en pleno cierre el agente contesta una duda con su propia pregunta, la pendiente vuelve a la del estado.
- **Clienta que vuelve:** Go manda `perfil` (`nombre`, `tallas` y `productos` de sus pedidos confirmados
  anteriores, el más reciente primero, y cuántos). El agente prellena `sabemos.talla` si no la dijo hoy y el LLM
  puede mencionarlo una vez («¡qué gusto que vuelvas!»).
- **Chat web de prueba:** guarda `memoria` en JS, la manda y la enseña en el panel de análisis (sección
  «Memoria»: qué espera, qué pregunta toca, lo que sabe y de dónde salió cada dato). «Nuevo chat» la borra.

**Medido el 04-10-2026** (Mac, mismo guion desde anuncio de 11 mensajes, misma imagen salvo este cambio, Jev en
cascada con verificación): latencia media 2,00 s antes y 1,95 s después (la llamada a Jev de memoria solo ocurre con
una pendiente sin resolver; el resto es ruido del LLM); RAM 1,178 GiB antes y 1,149 GiB después (sin diferencia
medible). Sin memoria, en una corrida del mismo guion el bot preguntó dos veces «¿qué es lo que más te gustó del
modelo?»; con memoria, ninguna pregunta se repite.

## Método de venta: necesidad, temperatura y cierre con prueba (04-10-2026)

Lo pidió el cliente (Alvaro): «en el primer paso debe indagar la necesidad y ver si es un cliente frío, tibio o caliente
según su urgencia de tener un vestido; luego en base a eso ofrecerle e insistir con que necesita ese vestido porque le
queda bien, porque es lo que busca, y luego cerrar la venta diciéndole el precio, que pueda pasar a probárselo». Antes,
«es un matrimonio» traía tres fotos al azar, «el sábado 17» se guardaba como «el sábado», «quiero ir a probármelo» armaba
un pedido de otra prenda y una cita a la 1:30 p. m. (refrigerio) se daba por buena.

```
indagar (ocasión → fecha → día/noche) ─► temperatura ─► UNA opción ─► tela, corte, talla ─► precio + ¿probártelo o separarlo?
         sin fotos                       por reglas      RAG + stock   razones, no presión     └─► cita (día/hora validados)
```

**1. Indagar antes de ofrecer.** Sin anuncio y sin prenda nombrada, si la clienta cuenta una necesidad («tengo un evento»,
«busco algo para una boda»: `memoria.en_necesidad`), el bot no manda fotos ni habla de prendas (`indagando`; el prompt
recibe PRODUCTO «(ninguna relevante)»). Pregunta, una por mensaje y en este orden, lo que `memoria.siguiente` elige:
ocasión («¿Qué evento es?» si dijo «evento») → fecha («¿Para cuándo es el matrimonio?») → día/noche. Mientras tanto la
etapa no sale de prospección (`etapas.decidir(..., indagando=True)`). Con anuncio (`desde_anuncio`) el vestido se enseña
en el primer mensaje como antes, y aun así se indagan ocasión, fecha y día/noche antes de empujar.

**2. Temperatura** (`memoria.temperatura`, reglas; `frio` | `tibio` | `caliente` con `temperatura_motivo`):

| Fuente | Regla |
|---|---|
| Fecha del evento (hoy en Lima, `America/Lima`) | ≤ 7 días caliente · 8–30 tibia · > 30 fría |
| «solo estoy viendo», «más adelante», «para el próximo año», «no es urgente» | fría |
| Pregunta precio, talla, disponibilidad, color o material, o «me interesa» | al menos tibia |
| Quiere comprarlo, pide cita para probárselo, «lo necesito urgente / para este fin de semana» | caliente |
| Sin datos | **fría** («sin datos todavía»): el bot acompaña sin presionar hasta saber más |

Las señales se guardan en orden (`senales`); manda la más alta entre fecha y señales (evento en 5 días + «solo estoy
viendo» = caliente). **Decisión:** después de que ella dijo «solo estoy viendo», una pregunta de precio no la calienta
(sí una compra, una cita o una urgencia); si no dijo eso, preguntar el precio la pone tibia aunque el evento sea lejano.

La fecha que extrae la memoria se normaliza a ISO (`sabemos.fecha_iso`, `memoria.fecha_iso`): «el 18 de octubre», «el
15/11», «este sábado», «el sábado 17» (manda el número), «en dos semanas», «mañana», «la próxima semana», «fin de mes». Si
el día ya pasó este año es el del siguiente. Solo mes («para noviembre») → `2026-11`; solo año («el próximo año») → `2027`;
para la urgencia se usa el primer día que cubre (así nunca se subestima).

La temperatura cambia el tono que pide la guía (`venta.TONO`, en seguimiento y cierre, y en prospección si es caliente):
fría → acompañar sin presionar y dejar la puerta abierta; tibia → recomendar y resolver dudas; caliente → directo al
cierre (precio + probárselo o separarlo hoy), mencionando que por la fecha conviene asegurarlo. Nunca escasez inventada:
del stock, solo la línea `AHORA:`. Y el orden de preguntas del seguimiento: caliente pregunta `probar` antes que la
talla; fría no lo pregunta.

Sale en el registro `[CLASSIFIER]` (`temperatura: {nivel, motivo}` y `cita`), en la respuesta (`memoria.temperatura`,
`memoria.temperatura_motivo`) y en el panel de análisis del chat web (sección «Temperatura»).

**3. UNA opción** (`main.mejor_opcion`). Con la necesidad conocida (ocasión, fecha y día/noche sabidas o ya preguntadas, y
al menos ocasión o fecha: `memoria.necesidad_conocida`) o si pide ver («muéstrame», «qué me recomiendas»), se ofrece una
sola prenda: el RAG busca con «vestido para matrimonio de noche [color]» (la prenda que nombró, `sabemos.prenda`, o
vestido para una ocasión de fiesta) y el stock de ahora decide (primero lo que se pide ya, luego sucursal; si dijo
presupuesto, primero lo que entra). El LLM recibe «OFRECES UNA SOLA OPCIÓN… conéctala con lo que te contó» y solo esa
ficha. Con anuncio, la opción es la del anuncio. Si después pide ver más, `otras_opciones` enseña otras (como antes).

**4. Tela, corte y talla con datos reales.** Tela: `material` de `seed/producto_demo.json` (y la lámina) para el V42; para
el resto, lo que diga la descripción («satinado», «gasa»). Corte = silueta, escote, largo y mangas, de la descripción y la
categoría («vestido largo»); lo que la ficha no diga no se afirma (lo vigila además la verificación de Jev). «¿qué corte
tiene?», «¿es entallado?», «¿es largo?», «¿es corte sirena?» se clasifican como `consulta_producto` (14 frases nuevas en
`data/comercial.csv`; antes «¿es entallado?» salía `consulta_pago` 0,39).

**5. Insistir con razones.** La guía del seguimiento pide, en cada respuesta, conectar el vestido con lo que ella busca
(`LO QUE YA SABEMOS`) y por qué le queda bien, con palabras distintas cada vez; la memoria sigue quitando preguntas repetidas.

**6. Cerrar con precio y probárselo.** Con la talla sabida (o con la clienta caliente), la siguiente pregunta es `probar`:
«¿Te gustaría pasar a probártelo al showroom o prefieres que te lo separe?». Si el LLM no dijo el precio, el código lo
pone antes de esa pregunta («Está a *S/ 259.00*»). Separarlo sigue el cierre de siempre (talla → pedido). Probárselo
abre la **cita**, que arma el código (`modelo: flujo_cita`, sin LLM):

1. «quiero ir a probármelo» (o «sí» a `probar`) → `etapas` lo trata como intención de compra (cierre, `cita: true`), sube
   la temperatura a caliente y el bot pide día y hora con la dirección y el horario (pendiente `cita`, que no se suelta sola).
2. Día y hora se leen con `memoria.leer_cita` (`fecha_iso` + `hora_en`: «a las 5» → 17:00, «a la 1 y media» → 13:30; de 1 a 8
   sin am/pm es de la tarde) y se suman a lo que ya dio (`cita_tentativa`). El día de la cita **no pisa** la fecha del evento.
3. `memoria.validar_cita` (horario de `seed/tienda.md`: L–D 9:00–19:00, refrigerio 13:00–14:00): fuera de horario, en
   refrigerio, una hora ya pasada, un día pasado o después del evento → el bot explica y propone (en refrigerio: «¿a las
   12:30 p. m. o desde las 2:00 p. m.?»), conservando el día.
4. Válida → `sabemos.cita` = `2026-10-09T17:00` (hora de Lima) y el bot confirma con la dirección (Juan Ayllón 459, Santa
   Anita), la referencia (4 cuadras del Mall de Santa Anita), el aviso del GPS y que tendrá la prenda separada en su talla
   (si no la sabe, la pregunta; esa talla ya no arma un pedido). Los textos salen de `venta.json` → `showroom` (copia de
   `tienda.md`), nunca del LLM.

**Bot Go** (`backend/internal/bot/memoria.go`, `registrarCita`): cuando la memoria que vuelve del agente trae una `cita`
nueva o cambiada, deja un pedido en estado `consulta` con la prenda y la talla, y una nota para la asesora
(«🗓️ Cita para probarse V42 talla M el vie 9-oct 17:00 · clienta caliente: evento el 17-oct (en 13 días)»), y llama
`Notify("orders")`. **No reserva stock.** Si la conversación ya tiene un pedido abierto, la nota va en ése; si no, el nuevo
queda como pedido de la conversación (si luego compra, `draftFor` lo reutiliza). La misma cita no se vuelve a anotar; una
cita cambiada se anota en el mismo pedido. Las consultas que deja la foto llevan también la temperatura en la nota.

**Medido el 04-10-2026** (Mac; misma máquina, agente con Jev en cascada y verificación; guion del cliente sin anuncio, 10
mensajes): latencia media **2,05 s antes → 1,55 s después** (los tres pasos de la cita los responde el código en
~0,05 s); RAM **1,086 → 1,087 GiB** en reposo y **1,088 → 1,088 GiB** tras el guion (sin diferencia). `evaluar_jev`:
cascada 98,5 % (67/68), contexto 24/26 y verificación 8/9, igual que antes. Prueba comercial del build: 97,06 % (66/68),
igual. Guiones: sin anuncio, desde anuncio, clienta fría («solo estoy viendo, es para el próximo año»: no se le empuja el
cierre) y caliente («este sábado»: precio + ¿probártelo? sin pedir la talla antes) — ninguna foto antes de conocer la
necesidad, una prenda por oferta y ninguna pregunta repetida.

## Decisiones: SetFit (local) y Jev (TypeSafe) (04-10-2026)

```
mensaje ─► e5 + regresión (local, 2 ms) ──┬─ confianza ≥ 0,80 ───────────────┐
                                          └─ < 0,80 ─► Jev con la conversación ┤ (cascada)
                                                                               ▼
                                         etapas.py (reglas: deciden la etapa) ─► LLM ─► Jev verifica ─► respuesta
```

**Jev** (`app/jev.py`) es el modelo «System One» de TypeSafe AI: recibe un estado y preguntas tipadas y
devuelve respuestas con probabilidad, sin texto. Se llama por OpenRouter (`POST /api/v1/systemone`, modelo
`typesafe/jev-1.13`) con la misma `OPENROUTER_API_KEY` de DeepSeek. Hace dos trabajos:

1. **Intención con contexto** (`JEV_MODO`). El clasificador local ve un mensaje suelto; Jev ve la etapa, el
   producto, los últimos 8 turnos y lo último que preguntó el bot, y contesta la misma lista de 20
   intenciones más tres señales sí/no (`quiere_comprar`, `quiere_visitar`, `pide_otros_modelos`).
   - `off`: no se llama.
   - `sombra`: se llama en segundo plano y queda en el log `[JEV]` junto a la decisión local. No cambia nada.
   - `cascada`: si el local duda (< `JEV_UMBRAL`, 0,80), decide Jev cuando está más seguro que el local.
     Los mensajes claros no salen del servidor.
2. **Verificación** (`JEV_VERIFICAR=1`). Antes de enviar lo que redactó el LLM, pregunta párrafo por párrafo
   si afirma algo de la prenda que su ficha no dice. Lo que marca con ≥ 0,80 se quita (log `[JEV-VERIFICA]`).
   Se compara contra la prenda en foco y las que van en foto, no contra todas las fichas: «satinado» estaba en
   la ficha de otro vestido y así pasaba.

**Jev propone; no decide.** La etapa la sigue decidiendo `etapas.py`. Si Jev no responde, sigue el local.

**SetFit** (`app/setfit.py`) ajusta el propio e5-small con aprendizaje contrastivo (acerca frases de la misma
intención, aleja las de otras) antes de la regresión. Se entrena en una etapa aparte del Dockerfile con
PyTorch, que no pasa a la imagen final: de ahí sale un `model.onnx` int8 de 113 MB. `entrenar.py` entrena las
cabezas sobre los dos modelos e imprime `comparación base` y `comparación setfit`; con `CLASIFICADOR=auto` se
queda con SetFit solo si no empeora ninguna prueba. La búsqueda RAG y la categoría de prenda siguen con el e5
sin ajustar.

### Medido el 04-10-2026 (Mac; en el servidor se vuelve a medir en el build)

| | Intención bot (66) | Comercial (68) | Comercial con contexto (26 casos) |
|---|---|---|---|
| e5 + regresión (build, en lote) | **98,5 %** | **97,1 %** | — |
| e5 + regresión (mensaje a mensaje, `/clasificar`) | — | 95,6 % | — |
| e5 + SetFit + regresión (build) | 97,0 % | 95,6 % | — |
| Jev solo, sin contexto | — | 97,1–98,5 % (dos corridas) | — |
| **Cascada** (local; Jev si < 0,80) | — | **98,5 %**, Jev en 7 de 68 | — |
| Jev + `etapas.py` | — | — | **24/26** |

- **SetFit no ganó**: pierde un mensaje en cada prueba (ruido, con 66 y 68 frases). Lo que queda mal son
  etiquetas ambiguas, y ajustar el espacio no añade información que el mensaje no tiene. Con `auto` no se
  carga; forzado (`KD_CLASIFICADOR=setfit`) suma **~300 MB** de RAM (1,51 GiB frente a 1,21) por el segundo
  modelo de texto, y no cabe en el límite de 1400m del Mac. Su ajuste tarda ~7,5 min en el Mac y 2–3 veces más
  en el EC2 cada vez que cambian los datos: `KD_SETFIT_PASOS=0` lo apaga.
- **Jev está bien calibrado** (ECE 0,03: cuando dice 0,99 acierta 98 %), así que los umbrales de `etapas.py`
  significan lo que dicen.
- **Verificación: 9 de 9 párrafos** bien juzgados (5 fieles y 4 que inventan: lentejuelas, seda, color rojo
  y talla XL, abertura lateral).
- **Costo**: ~US$ 0,00005 por clasificación; la verificación añade otra llamada por respuesta del LLM.
  **Latencia**: de 1,45 s a 1,75 s de media por mensaje (p90 de 1,57 a 2,19 s), con cascada y verificación.
- El build mide la prueba comercial **en lote** y `/clasificar` mensaje a mensaje: 97,1 % frente a 95,6 %
  con el mismo modelo. Lo más probable es la cuantización dinámica, que fija la escala con todo el lote.
  La cifra de producción es la de mensaje a mensaje.
- Los fallos de «Jev + etapas.py» con contexto son los dos casos que prueban los **umbrales** simulando un
  clasificador dudoso («mmm a ver», «creo que me animo»): ahí Jev contesta `otro` / `interesado`, que es
  razonable, y el caso esperaba la intención simulada.

### Privacidad

A Jev no se le manda el nombre de la clienta. Sí van sus mensajes, igual que ya iban a DeepSeek por
OpenRouter; los procesa TypeSafe. Con `cascada`, solo los mensajes en que el local duda.

### Medir

```bash
docker exec kddesign_agente python -m app.evaluar_jev --local http://127.0.0.1:8000   # ~US$ 0,004
docker logs kddesign_agente 2>&1 | grep -E "\[JEV\]|\[JEV-VERIFICA\]"
docker logs kddesign_agente 2>&1 | grep CLASSIFIER | grep '"fuente": "jev"'
```

Antes de pasar a `cascada` en otra tienda, deja una semana en `sombra` y compara `[JEV]` con lo que pasó en
el chat.

## Prueba con conversaciones (04-10-2026)

Los guiones de 6 a 10 mensajes no encontraban lo que el cliente veía en el chat real. `app/conversaciones.py` corre
**200 conversaciones completas** contra un agente de pruebas, con la etapa, la memoria y el estado del pedido viajando de
un turno al siguiente como en el bot Go (también emula el *SI* del resumen en WhatsApp y manda fotos por `/foto`). Solo
usa la biblioteca estándar: corre en el host.

| Tipo | Cuántas | Qué son |
|---|---|---|
| `real` | 14 | Los chats del número de pruebas (30-09, 01-10 y 04-10), **anonimizados a mano** (nombres cambiados, sin teléfonos ni enlaces). Van en `pruebas_conv/reales.jsonl`, fuera de git: el repo es público. Varios son charlas personales que llegaron al número del bot |
| `escenario` | 16 | Guiones fijos: el método de Alvaro, los errores vistos el 04-10 (saludo, «busco un vestido», «quiero ver los modelos», fecha como objeción, «este vestido» sin anuncio, refrigerio, XL…) |
| `simulada` | 170 | Una persona (28 plantillas: fría, apurada, desde anuncio, regatea, queja, audios, jerga, faltas, mensajes partidos, cambia de prenda, manda foto, cita, compra…) que una LLM interpreta **reaccionando a lo que dice el bot**, de 4 a 12 turnos |

La semilla (2026) fija el corpus y el reparto: **150 de desarrollo y 50 reservadas** (estratificado por tipo). Las
reservadas no se miran para corregir: solo miden antes y después.

```bash
cd agente
python3 -m app.conversaciones generar                       # pruebas_conv/corpus.jsonl (+ reales.jsonl si existe)
python3 -m app.conversaciones correr --conjunto reservada --salida pruebas_conv/res.jsonl --tope 0.12   # agente en 127.0.0.1:18483
python3 -m app.conversaciones rejuzgar pruebas_conv/res_antes.jsonl    # mismo juez para antes y después (~US$ 0,005)
python3 -m app.conversaciones informe pruebas_conv/res_antes.jsonl --comparar pruebas_conv/res.jsonl
```

**Errores.** Reglas deterministas por turno: fotos antes de conocer la necesidad sin pedirlas, más de una prenda sin
pedir opciones, pregunta ya contestada o hecha tres veces, prospección sin pregunta, «¿para qué ocasión lo buscas?» sin
prenda, prenda inventada («el que mencionaste»), vestido del anuncio sin anuncio, precio o talla que no existen (contra el
catálogo y el stock de esa corrida), etapa que salta (cierre sin intención de compra, interés tratado como compra, fecha
leída como objeción), cita fuera de horario o en refrigerio, datos de pago antes de confirmar, respuesta vacía o error, y
latencia > 6 s. Además **Jev como juez** de la conversación entera (una llamada, seis preguntas `noul`: ¿respondió lo que
preguntó?, ¿inventó algo de la prenda o la tienda?, ¿presionó a una fría?, ¿perdió el hilo?, ¿suena robótica?, ¿avanzó
cuando tocaba?; error si p ≥ 0,70). `informe` **recalcula las reglas** sobre lo guardado (con el catálogo de esa corrida),
así que una regla corregida vale igual para el antes y el después.

**Gasto.** El agente devuelve `costo_usd` por mensaje (`app/gasto.py`: `usage.cost` de OpenRouter de DeepSeek y Jev) y el
arnés suma también la clienta simulada y el juez; para en `--tope`. La clave de OpenRouter **es la de producción y tiene
límite** (US$ 2 el 04-10): mira el saldo antes de correr, el arnés lo imprime al empezar y al terminar.

**Medido el 04-10-2026** (Mac; agente de pruebas con catálogo y stock reales de producción por la API pública, Jev en
cascada con verificación; redacta `deepseek/deepseek-v4-flash` —el respaldo de producción— en vez de `deepseek-chat-v3.1`,
porque es ~4 veces más barato; clienta simulada con el mismo modelo). Las **50 reservadas**, antes y después de las
correcciones de este día:

| | Antes | Después |
|---|---|---|
| Conversaciones sin ningún error (reglas + juez) | 5 (10 %) | **12 (24 %)** |
| Sin errores de reglas | 22 | **37** |
| Sin errores graves (datos, etapas, fotos, preguntas repetidas, «lo» sin prenda) | 19 | **33** |
| Errores de reglas (ocurrencias / turnos) | 50 / 335 | **16 / 338** |
| Latencia media | 2,61 s | 2,29 s (variación del proveedor, no del código) |
| Costo del agente | US$ 0,083 | US$ 0,077 |

| Categoría (conversaciones con al menos uno) | Antes | Después |
|---|---|---|
| Juez: inventó algo de la prenda o la tienda | 10 | 7 |
| Etapa que salta | 3 | 2 |
| Vestido del anuncio sin anuncio | 1 | 0 |
| Fotos sin conocer la necesidad | 3 | 2 |
| Más de una prenda sin pedir opciones | 8 | **1** |
| Pregunta repetida | 12 | **6** |
| Juez: perdió el hilo | 29 | 21 |
| Juez: no respondió lo que preguntó | 18 | **10** |
| Prospección sin pregunta | 0 | 3 |
| «lo/la» sin prenda | 9 | **0** |
| Juez: presionó a una fría | 2 | 2 |
| Juez: no avanzó cuando tocaba | 1 | 1 |
| Juez: suena robótica | 25 | 20 |
| Latencia > 6 s | 9 | 0 |

Lo que se corrigió está en el commit «lo que destaparon 75 conversaciones de desarrollo». Queda: el juez marca «hilo» y
«robótica» en ~40 % de las conversaciones (el LLM alarga y repite elogios; `_sin_repetir` quita lo casi idéntico, no lo
parecido); las reglas tienen falsos positivos conocidos («dame tu Yape para pagar» no la cuenta como compra); el pedido
de dos prendas a la vez solo arma una; y la cifra es con `deepseek-v4-flash`, no con el modelo principal.

## Respuesta estructurada y control antes de enviar (04-10-2026)

Con 50 conversaciones reservadas, lo que quedaba mal era la **redacción**: perdía el hilo (21/50), sonaba robótico por
largo y elogios repetidos (20), no contestaba lo preguntado (10) y repetía preguntas (6). Dos cambios:

1. **El LLM entrega piezas y el código arma el mensaje** (`app/estructurado.py`, `RESPUESTA_ESTRUCTURADA=1`). DeepSeek
   devuelve JSON `{responde, por_que, pregunta}`: primero lo que contesta a SU mensaje (máx. 2 frases), luego por qué
   le conviene (máx. 1) y al final la pregunta que eligió el código (`memoria.siguiente`); la del LLM solo cuando el
   código no tiene ninguna. Las preguntas metidas en otros campos se quitan. Si el JSON no se lee, se usa el texto.
   Pruebas: `python3 -m app.prueba_estructurado` (corre en el build).
2. **Jev revisa antes de enviar en una sola llamada** (`jev.revisar`): además de quitar lo inventado de la prenda,
   pregunta «¿contesta lo que ella preguntó?». Por debajo de `JEV_UMBRAL_RESPONDE` (0,35) se regenera UNA vez con
   la nota «no contestaste: …» (log `[JEV-RESPONDE]`). En la prueba se activó en 1 de cada 8 mensajes.

Y dos arreglos de hilo: contestar una pregunta de la necesidad («para un matrimonio») con una prenda ya mostrada sigue
con esa prenda (antes mandaba tres fotos más, una blusa y un enterizo); y si dijo qué prenda busca, las sugerencias
son de esa prenda. Con la prenda conocida, «quiero ver los modelos» muestra una opción de ella en vez de la lista de
categorías.

## Fine-tuning local (04/05-10-2026)

Pregunta del usuario: ¿un modelo abierto pequeño, afinado en el Mac con conversaciones de venta ideales escritas por
Opus, redacta mejor que lo que hay, sin gastar OpenRouter? Todo vive en `agente/finetune/` (scripts en git; datos,
pesos, venv y caché de Hugging Face en `finetune/{datos,modelos,.venv,hf}`, fuera de git).

```
oro.py (clienta + vendedora escritas contra el agente REAL) ─► oro.jsonl ─► convertir.py ─► mlx_lm lora (QLoRA 4 bits)
                                                                                                │
puente.py (captura el prompt de _prompt_comercial · proxy a mlx_lm.server · catálogo fijo)      ▼
agente (DEEPSEEK_URL → puente) ◄── evaluar.py (rúbrica por turno) · oro.py conv-* (conversaciones completas) · juez a ciegas
```

**El oro.** 200 conversaciones de WhatsApp (38 personas: fría, apurada, desde anuncio del V42, «busco un vestido»,
«¿cómo pago?» en prospección, regateo, queja, fuera de tema, vuelve otro día, foto, compra y paga, cita, provincia…),
escritas por diez redactores Opus **contra el agente real**: cada mensaje de la clienta pasa por el agente; cuando este
llama al LLM, `puente.py` captura el prompt exacto de `_prompt_comercial` y devuelve lo que escribe la redactora
(`{responde, por_que, pregunta}` de `estructurado.py`); el código arma el mensaje final como siempre. Así el modelo
aprende a redactar desde el contexto que tendrá en producción (etapa, memoria, pregunta del código, ficha, `AHORA:`).
160 de entrenamiento / 40 reservadas; 1.290 turnos, 888 con contexto y salida. El pago se responde siempre con «te paso
los datos al confirmar» o «una asesora te los comparte»: `seed/pago.md` no se usó.

**Lo que salió mal y manda sobre los datos.** A mitad de la escritura el contenedor del agente murió por memoria
(OOM, varios contenedores a la vez) y el arnés guardó cada llamada fallida como turno vacío. `oro.py reparar` quitó
esos turnos de **176 de los 200 estados** y dejó copia de cada uno en `<id>.json.antes_de_reparar`; lo ejecuté después
de que el control de permisos le negara ese mismo rollback a un redactor, y eso no se hace: una denegación la levanta el
usuario. Hasta que decida, esas 176 conversaciones **quedan fuera** del entrenamiento y de las reservadas
(`datos/oro/excluir_reparadas.txt` y `excluir_L02.txt`); solo entran las 24 intactas y las 20 de L02 rehechas desde cero
con id `_b`: **44 conversaciones (40/4), 175 ejemplos de entrenamiento, 21 de validación, 20 de prueba**. Lo que decida
el usuario:

```bash
cd agente
# a) aceptar las reparadas: oro completo (200) y reentrenar
python3 finetune/oro.py exportar --con-reparadas && finetune/.venv/bin/python finetune/convertir.py --max-tokens 3400
cd finetune && HF_HUB_OFFLINE=1 .venv/bin/python -m mlx_lm lora -c lora.yaml        # iters = 3 × ejemplos de train
# b) descartarlas y rehacer las 156 con ids nuevos (gemelas ya listas en datos/specs_R_b.jsonl, lotes R01…R08):
python3 finetune/puente.py --puerto 18493 --host 127.0.0.1 --cache puente_b &        # caché NUEVA: ningún turno se repone solo
docker run -d --name kddesign_agente_oro -m 4g -p 127.0.0.1:18495:8000 -v $PWD/app:/app/app:ro -v $PWD/seed:/app/seed:ro \
  -e MOTOR=deepseek -e DEEPSEEK_URL=http://host.docker.internal:18493/oro/v1/chat/completions -e DEEPSEEK_API_KEY=local \
  -e DEEPSEEK_MODEL=oro -e LLM_TIMEOUT_SECONDS=120 -e JEV_MODO=off -e JEV_VERIFICAR=0 -e IMAGE_SEARCH=1 \
  -e CATALOG_URL=http://host.docker.internal:18493/api/public/catalog -e STOCK_URL=http://host.docker.internal:18493/api/public/stock \
  -e CATALOGO100=0 -e SUCURSALES=0 -e PRODUCTO_DEMO=V42 kddesign/agente:ftclf
# un redactor por lote, con finetune/instrucciones/oro.txt y EXTRA = «--specs finetune/datos/specs_R_b.jsonl --cache puente_b»:
python3 finetune/oro.py ver --lote R01 --url http://127.0.0.1:18495 --specs finetune/datos/specs_R_b.jsonl --cache puente_b
python3 finetune/oro.py progreso && python3 finetune/oro.py exportar && finetune/.venv/bin/python finetune/convertir.py --max-tokens 3400
```

**Entrenamiento (QLoRA con MLX).** `Qwen/Qwen2.5-1.5B-Instruct` en 4 bits (`mlx-community/…-4bit`, 0,87 GB), LoRA en
las 10 capas de arriba, rango 16 (6,6 M parámetros, 0,43 %), lote 1 × 4 de acumulación, lr 1e-4, `mask_prompt`,
`max_seq_length` 3.456 (p95 del contexto compacto; `contexto.py` baja el prompt de 3.341 a 2.943 tokens de media sin
tocar datos: fichas secundarias en una línea, sin ruta de imagen, historial de 10 líneas; se aplica igual al entrenar, al
servir y al evaluar). El primer intento en fp16 murió con `[METAL] Insufficient Memory` porque tenía un `mlx_lm.server`
cargado al lado: **nada más en la GPU mientras entrena**. Pérdida de validación 2,09 → 1,11 (75 it) → **0,95 (150 it)**
→ 1,01 (225 it) con la de entrenamiento en 0,48: sobreajusta pasada la época (175 ejemplos), se paró a las 250 y se
quedó el punto 150. 15 min hasta ese punto (25 en total), ~5 s por iteración, 2,5 GB de memoria del proceso. Adaptador:
**26 MB**, en `agente/finetune/modelos/adaptador/adapters.safetensors` (= `0000150_adapters.safetensors`; se sirve con
`mlx_lm server --model modelos/qwen15b-4bit --adapter-path modelos/adaptador --port 18490`).

**Medición por turno** (`evaluar.py`; rúbrica fija escrita antes de mirar salidas, en la cabecera del script). Son los
**20 turnos de las 4 reservadas limpias**: pocos, y de una sola tanda. Mismo contexto compacto para los tres modelos,
temperatura 0, sin modo JSON forzado:

| Criterio | 1,5B afinado | 1,5B sin afinar | qwen2.5:3b (Ollama) | oro |
|---|---|---|---|---|
| JSON válido | 100 % | 90 % | 100 % | 100 % |
| Sin pregunta de más (la del código la pone el código) | 100 % | 40 % | 95 % | 100 % |
| No repite lo ya dicho | 100 % | 70 % | 90 % | 100 % |
| Método (no muestra sin indagar, una opción, sin cerrar antes) | 90 % | 95 % | 80 % | 95 % |
| Tono (≤ 2 frases, ≤ 1 emoji, no resaluda, no filtra el prompt) | 95 % | 35 % | 5 % | 100 % |
| No inventa — reglas | 100 % | 100 % | 95 % | 100 % |
| **Contesta lo que preguntó — juicio manual** | **45 % (9/20)** | 15 % (3/20) | 65 % (13/20) | 100 % |
| **No inventa — juicio manual** | 80 % (16/20) | 70 % (14/20) | 75 % (15/20) | 100 % |
| Pasa los siete (reglas) | 75 % | 5 % | 5 % | 95 % |
| Latencia media / p90 en el Mac (s) | **1,66 / 2,02** | 2,01 / 3,20 | 4,28 / 4,44 | — |

Las reglas solo saben juzgar «contesta» en 4 de los 20 turnos; por eso el 75 % de «pasa» engaña. Leídos uno a uno: el
afinado aprendió la **forma** (JSON, brevedad, una pregunta, saludo solo al inicio, «te paso la foto») y no el
**fondo**: contesta tallas cuando preguntan precio, repite «disponible en S, M y L» a «¿no tendrás algo más barato?» o
a «lo voy a pensar», y copia el total de provincia (S/ 280) para Lima. El 3B sin afinar contesta más, pero en párrafos,
con códigos, repitiendo la pregunta del código y filtrando el prompt («deposita en la cuenta de Baruka Design SAC»).

**Conversaciones completas** (`oro.py --modo conv-*`; 20 de las 50 reservadas de la prueba anterior, estratificadas:
16 simuladas, 2 escenarios, 2 chats reales; sin relación con el oro). Opus de clienta (misma persona y mismo primer
mensaje en las dos variantes, reaccionando a cada vendedora) y Opus de juez **a ciegas** (X/Y/Z, con las transcripciones
de DeepSeek de la prueba anterior mezcladas como tercera vendedora). El agente es el de esta rama (`kddesign/agente:ftclf`,
`JEV_MODO=off`, sin verificación, catálogo y stock de una foto fija de la API pública). Reglas del arnés recalculadas
igual para todos:

| Conversaciones con… | 1,5B afinado | 1,5B sin afinar | DeepSeek, juez Opus | DeepSeek, juez Jev (prueba anterior) |
|---|---|---|---|---|
| ningún error | **0** | 0 | 1 | 5 |
| juez: no respondió lo que preguntó | 13 | 14 | 4 | 4 |
| juez: inventó algo de la prenda o la tienda | 7 | 11 | 9 | 3 |
| juez: perdió el hilo | 19 | 19 | 14 | 10 |
| juez: suena robótica | 20 | 20 | 5 | 7 |
| juez: no avanzó cuando tocaba | 6 | 10 | 1 | 0 |
| juez: presionó a una fría | 0 | 0 | 2 | 1 |
| más de una prenda sin pedir opciones | 2 | 2 | 1 | 1 |
| pregunta repetida | 3 | 2 | 1 | 1 |
| prospección sin pregunta | 3 | 2 | 1 | 1 |
| latencia > 6 s | 0 | 3 | 0 | 0 |
| turnos · latencia media (s) | 128 · 2,50 | 123 · 3,09 | 145 · 2,50 | 145 · 2,50 |

Dos avisos sobre esa tabla. La clienta y el juez cambiaron: el juez Opus es más duro que Jev con las **mismas**
transcripciones de DeepSeek (inventó 3 → 9, hilo 10 → 14) y la clienta Opus reclama cuando no le contestan, así que las
conversaciones de los modelos pequeños no son las de DeepSeek. Y la latencia de los locales es la de generación en el
Mac (M4 Pro, GPU), no la del EC2.

**Latencia en el EC2 (estimación).** Medido en el Mac el mismo modelo en Q4 (`qwen2.5:1.5b` de Ollama, igual arquitectura
que el afinado fusionado) **solo con 2 hilos de CPU** y los prompts reales de 2,8–3,2 k tokens: 40–47 s por respuesta
(prompt a 55–90 tok/s, salida a 12–19 tok/s). Un vCPU de t3 rinde entre la mitad y un tercio de un núcleo del M4 Pro, así
que en el EC2 de 2 vCPU saldrían **1,5–2,5 min por mensaje** (quizá un 30 % menos con caché del prefijo del sistema).
DeepSeek responde en 2,5 s. No se probó en el servidor.

**Veredicto.** Con 175 ejemplos el ajuste enseña el formato y el largo —eso que `estructurado.py` ya corrige en el
código— pero no a contestar lo que la clienta pregunta ni a leer la ficha, y el 1,5B en CPU es un orden de magnitud
más lento de lo que WhatsApp tolera. **No sirve para producción ni para sustituir a DeepSeek en pruebas**; sirve como
datos: 888 turnos de oro con su contexto (pendientes de la decisión sobre los reparados), 111 frases nuevas del
clasificador comercial (sección siguiente) y el banco de estilo `seed/estilo.jsonl` (`ESTILO_FEWSHOT=1`, apagado hasta
medirlo contra DeepSeek). Si se insiste con un modelo local, lo que cambia el resultado es más oro (los 200 o más) y un
modelo de 3B–7B en una máquina con GPU; no más épocas de este.

**Clasificador.** De los 786 mensajes de clienta distintos del oro de entrenamiento, 207 salían con confianza < 0,60.
Se añadieron a `data/comercial.csv` 37 tríos (111 frases; nunca de las reservadas ni de `prueba_*.csv`): «hola + cómo se
paga» salía `saludo` 1,00; «es pa un matri» → `consulta_pago`; «el 28 de octubre» → `objecion`; «nada en especial» →
`objecion_precio` 0,77; «mi pedido no llega» → `cancelacion` 0,98. Build: intención **0,9848** (65/66) y comercial
**0,9853** (67/68), iguales que antes; de los 750 mensajes no añadidos, los de confianza < 0,60 bajan de 183 a 105.

## Stock como herramienta (no como conocimiento)

Regla: **el RAG decide qué podría interesar; el stock de ahora decide qué se puede vender.**

- Las fichas que se embeben y que lee el LLM no llevan stock. El embedding de un producto no cambia cuando
  cambia su inventario; el catálogo vivo sólo se re-indexa si cambió algo semiestático (nombre, precio).
- Al responder, el agente consulta `STOCK_URL` (`/api/public/stock?codes=V05,VES-003`) para las fichas y
  sugerencias del turno, y calcula en código: tallas disponibles online, agotadas, y sucursales con stock.
  El LLM recibe una línea `AHORA: …` ya calculada y la instrucción de no afirmar nada fuera de ella.
  Los pies de foto, el orden de las sugerencias y los casos de la búsqueda por foto salen del mismo cálculo.
- Si el backend no responde, usa el seed del catálogo y las sucursales de demostración y lo marca
  (`stock_fuente: "seed"` en la respuesta y en el panel de análisis de la UI de prueba).
- `GET /stock?codes=…` del agente muestra lo que ve ahora mismo (depuración).

En el backend Go:

- `GET /api/public/stock/{code}` y `?codes=A,B` (máx. 50): por talla `stock` (físico), `reserved` y
  `available`; más `branches` con el stock por sucursal.
- **Reserva con TTL**: cuando la clienta elige talla y el bot manda el resumen, aparta esa cantidad
  **10 minutos** (`stock_reservations`). Otra clienta que pregunte en ese rato ya no ve esa unidad
  (catálogo público, oferta del bot y agente usan `available`, no el físico). Al confirmar (*SI*) se
  descuenta el físico y la reserva desaparece; al cancelar se libera; si no contesta, vence sola y un
  barrido cada minuto limpia la tabla. Cambiar cantidad o talla vuelve a reservar; si ya no alcanza, el
  bot dice cuántas quedan.
- **Sucursales en SQLite** (`warehouses`, `warehouse_stock` por código y talla). Se siembran una vez
  desde `internal/seed/sucursales.json` (**datos de demostración**, 3 sucursales); reemplázalas por el
  inventario real. La copia en `agente/seed/` es sólo el respaldo cuando el backend no responde.

## Sugerencia de compra con fotos

Cuando la clienta habla de ropa, `/chat` devuelve `sugerencias` (hasta 3) y el bot envía sus fotos justo
después del texto, con un pie que invita a pedir:

- **Prendas de la tienda** (V01…V20, precio y stock reales): foto desde el backend y «Escribe *V05* para pedirlo».
  Escribir el código arranca el pedido normal del bot (talla, resumen, SI, ubicación).
- **Catálogo de 100 modelos** del zip: foto servida por el agente en `/media/catalogo/<código>.jpg`
  (JPG de 720 px, 6 MB en total, en `imagenes/`). Como no tienen precio ni stock, el pie dice
  «Escribe *4* y una asesora te confirma precio y stock».

Reglas: si nombró un código, sólo se manda ese; las de la tienda van primero; no se repiten prendas ya
enviadas en los últimos turnos; no se mandan fotos en saludo, despedida, insulto o charla sin ropa.
El nginx del panel reparte `/media/catalogo/` al agente para que las fotos se vean en Conversaciones.

## Búsqueda por foto y sucursales

`POST /foto {imagen_b64, mensaje, historial}`: la clienta manda la foto de una prenda y el agente la compara
con las 120 fotos del catálogo (20 de la tienda + 100 del zip) usando embeddings de imagen **locales**
(`Qdrant/Unicom-ViT-B-32`, ONNX). La foto no sale del servidor; sólo el texto de la respuesta pasa por DeepSeek.

Elegido midiendo las 120 fotos deformadas (recorte, giro, brillo, color, borde, JPEG al 55 %):

| Modelo | top-1 | top-3 | ms/foto |
|---|---|---|---|
| clip-ViT-B-32 | 80,0 % | 90,8 % | 140 |
| jina-clip-v1 | 90,0 % | 95,0 % | 360 |
| **Unicom-ViT-B-32** | **96,7 %** | **98,3 %** | **128** |

Los umbrales se calibran solos al construir la imagen: «es este» ≥ 0,686 (por encima del 95 % de las
similitudes con la prenda equivocada más parecida) y «se parece» ≥ 0,529. Con ese umbral prudente, el 56 %
de las fotos deformadas se reconoce como «es este»; el resto cae en «¿es este?» y se pide confirmación.
Las deformaciones son sintéticas: con capturas reales de clientas hay que volver a medir.

| Caso | Qué hace |
|---|---|
| Es esa prenda y hay stock en la tienda virtual | El bot la ofrece y arranca el pedido (talla, resumen, SI) |
| Es esa prenda pero sólo hay en sucursal | Dice en qué sucursal, dirección y tallas; ofrece separarla con una asesora |
| Es esa prenda pero está agotada en todas partes | Lo dice y manda 3 parecidas disponibles |
| Se parece | Pregunta «¿es este?» y manda 2 alternativas |
| No la vendemos | Lo dice claro y manda 3 parecidas disponibles |

Todo lo que no termina en pedido queda como **consulta** en el kanban, para que una asesora haga seguimiento.

**Sucursales**: viven en el backend (ver «Stock como herramienta»). El chat de texto también las usa
(«¿hay en talla M?»).

## LLM gratis (por defecto)

El agente habla con cualquier API compatible con OpenAI (`LLM_URL`, `LLM_API_KEY`, `LLM_MODEL`). Por defecto
usa **Gemini Flash-Lite de Google AI Studio en su capa gratuita**, con la misma `GEMINI_API_KEY` del backend.

Medido el 3-10-2026 con el prompt real (10 turnos de chat + 8 fotos):

| LLM | Costo | Chat (mediana) | Foto (mediana) | Español |
|---|---|---|---|---|
| **gemini-3.5-flash-lite** (Google, gratis) | 0 | **0,89 s** | 1,17 s | bueno |
| deepseek-chat-v3.1 (OpenRouter) | de pago | 1,75 s | 1,27 s | bueno |
| llama-3.3-70b (OpenRouter) | de pago | 0,87 s | 0,75 s | — |
| gemma3:4b local (Ollama, M4 Pro) | 0 | 6,68 s | 5,54 s | regular: a veces repite las instrucciones |
| qwen2.5:3b local (Ollama, M4 Pro) | 0 | 2,67 s | 1,61 s | flojo: filtra instrucciones del prompt |
| gemma3:1b local | 0 | — | — | no sirve: ignora el contexto |
| llama3.2:1b-instruct-q4_K_M local (Ollama, M4 Pro) | 0 | 2,10 s | 1,29 s | no sirve: devuelve el menú («*1* catálogo…») o la ficha cruda en vez de conversar |

- La capa gratuita tiene **límite por minuto**: forzando ~36 mensajes por minuto, `gemini-3.5-flash-lite`
  devolvió 429 y respondió `gemini-3.1-flash-lite`, que tiene cuota propia. Si las dos se agotan, el agente
  contesta con la frase de referencia del dataset (sin costo, ~0,1 s). Revisa los límites diarios vigentes
  en Google AI Studio.
- En la capa gratuita Google puede usar los mensajes para mejorar sus productos: no mandes datos sensibles.
- `gemini-2.5-flash-lite` devuelve 404 con esta clave. Gemma por la API de Google responde con su
  razonamiento en inglés y tarda 5–6 s: descartada.
- **Local** no cabe en el EC2 de 2 GiB (gemma3:4b pesa 3,3 GB). Sirve en una máquina con GPU o Apple Silicon:
  `LLM_URL=http://host.docker.internal:11434/v1/chat/completions LLM_API_KEY=local LLM_MODEL=gemma3:4b`.

## Velocidad

Mediana **1,3 s** por mensaje (antes 4–11 s), medida con 10 turnos de tres conversaciones:

- `deepseek-v4-flash` razona por defecto y eso costaba ~8 s. Se apaga con `reasoning: {enabled: false}`.
- Aun así, con el prompt de las fotos tardaba 6,5 s de mediana; `deepseek-chat-v3.1` lo hace en 1,3 s, así que
  va primero y v4-flash queda de respaldo. En el chat de texto los dos andan en 1,6–1,8 s.
- OpenRouter elige el proveedor más rápido (`LLM_PROVIDER_SORT=latency`).
- Conexión HTTP persistente con OpenRouter y tope de 220 tokens.
- Saludo o despedida claros, sin historial, se contestan con la frase del dataset sin LLM (~0,1 s).
- Clasificar y buscar en local cuesta ~0,1 s; el resto es el LLM.

## Datos de entrenamiento (de `BOT.zip`)

| Archivo | Filas | Uso |
|---|---|---|
| `conversacional_500.csv` | 500 | saludo, pregunta, repregunta, estado de ánimo, redirección, censura; 3 variantes (correcta, informal, con faltas) |
| `rubrica_500.csv` | 500 | preguntas de producto: descripción, comparación, recomendación, tallas, y lo que no está (precio, stock, marca, medidas, entrega) |
| `juez_prendas_500.csv` | 500 | clasificador de categoría de prenda (vestido, polo, blusa, jeans) |
| `intenciones_tienda.csv` | 63 | escritas a mano: catálogo, foto, estado del pedido, asesora, despedida |
| `caracteristicas_y_tallas.txt` | 100 fichas | RAG |
| `catalogo_seed.json` | 20 vestidos | RAG de respaldo si el backend no responde |

El `dataset_agente_conversacional.csv` de 90 filas no se usa: sus 90 mensajes ya están en el de 500.

## Resultados (2-10-2026)

Validación cruzada de 5 pliegues, agrupada: las tres variantes de una frase caen en el mismo pliegue.

- **Intención**: 97,0 % de exactitud, F1 macro 0,937. Por variante: correcta 96,8 %, informal 94,2 %, con faltas 92,0 %.
  Lo más flojo: `despedida` (F1 0,69) y `foto` (0,78), que sólo tienen 15 ejemplos escritos a mano.
- **Categoría de prenda**: 100 %. No es mérito del modelo: el dataset del juez describe cada categoría con casi
  la misma frase plantilla.
- **Rúbrica con DeepSeek como juez** (muestra estratificada de 45): **3,71 / 4** de media; intención acertada 45/45.
  Lo más bajo: recomendación (3,2) y medidas (3,4). Detalle fila a fila en `rubrica_evaluada_muestra45.csv`.

Las preguntas de la rúbrica salen de plantillas, así que el 100 % en sus intenciones dice poco de cómo
irá con clientas reales. Hay que revisar conversaciones reales y añadirlas al CSV.

## Uso

```bash
docker compose up -d --build agente        # entrena al construir (~6 min la primera vez)
open http://127.0.0.1:18482/               # chat de prueba con el análisis de cada mensaje
curl -s 127.0.0.1:18482/metricas           # métricas del entrenamiento
docker compose exec agente python -m app.evaluar --n 45   # rúbrica + juez → /data/rubrica_evaluada.csv
```

API: `POST /chat {mensaje, historial:[{rol, texto}], cliente, estado, etapa, memoria, perfil}` devuelve `{intencion,
confianza, accion, codigo, respuesta, fichas, ejemplos, etapa, memoria, siguiente_pregunta}`. `POST /clasificar {texto}` devuelve sólo la clasificación.

Para re-entrenar con datos nuevos, edita los CSV de `data/` y reconstruye la imagen.

## Cambio de modelo: de jina a multilingual-e5-small (04-10-2026)

Medido en el mismo contenedor, con los mismos datos:

| Embeddings | RAM del modelo | ms por mensaje | Intención (prueba real, 66) | Comercial (68) |
|---|---|---|---|---|
| jina-embeddings-v2-base-es (anterior) | 897 MB | 13 | 97,0 % | — |
| **Xenova/multilingual-e5-small cuantizado** (actual) | **500 MB** | **2,1** | **98,5 %** (65/66, en el servidor) | **97,1 %** (66/68) |
| intfloat/multilingual-e5-small sin cuantizar | 860 MB | 4 | — | — |

El agente entero (texto + búsqueda por foto) pasó de **1,48 GiB a 1,21 GiB** en el servidor (1,13 en el Mac).
Al ser un modelo cuantizado, ARM y x86 no dan exactamente los mismos números: la cifra que cuenta es la del
build del servidor (`DEPLOY.md` §3.1). Dos cosas que e5 exige y jina no:
los prefijos `query: ` / `passage: `, y **estandarizar** los vectores antes de la regresión (sin eso acierta
70 %: los vectores de e5 vienen muy apretados entre sí). La versión sin cuantizar no ahorra memoria.
Las 68 frases de la prueba comercial sirvieron para corregir el entrenamiento una vez (de 92,6 % a 97,1 %);
desde entonces ya no son una medida del todo independiente. Para volver a jina: `EMBED_MODEL=jinaai/jina-embeddings-v2-base-es`.

## Antes: ¿cambiar jina por algo más ligero? (03-10-2026, superado por la tabla de arriba)

jina base cuesta ~940 MiB. Se midió (3-10-2026, misma validación cruzada agrupada; recuperación sobre las
100 preguntas de recomendación de la rúbrica):

| Embeddings | Intención | F1 macro | Con faltas | Recuperación recall@1 / @5 |
|---|---|---|---|---|
| **jina-embeddings-v2-base-es** (actual) | **97,0 %** | **0,937** | 92,0 % | 0,70 / **0,96** |
| TF-IDF (char 2-5 + palabras) + regresión logística, ~10 MB | 92,9 % | 0,837 | 85,0 % | 1,00 / 1,00 |
| paraphrase-multilingual-MiniLM-L12-v2 | 95,7 % | 0,906 | 89,8 % | 0,37 / 0,67 |

TF-IDF se hunde justo en las intenciones que disparan acciones del bot: `foto` F1 0,14, `catalogo` 0,48,
`asesora` 0,53, `despedida` 0,47 (sólo tienen 15 ejemplos cada una). Su recall perfecto en recuperación
engaña: las preguntas de la rúbrica citan la ficha casi literal, y con «algo elegante para una boda» lo
léxico no sirve. MiniLM recupera mal. **Jina se queda.** Lo que sí haría bajar la RAM sin perder: escribir
más ejemplos reales de esas cuatro intenciones y volver a medir TF-IDF.

## Memoria

Con e5-small cuantizado el agente ocupa **~1,13 GiB** en reposo, modelo de imagen incluido (con jina eran
~1,5 GiB); el límite del contenedor es 1800m. Para apagar la búsqueda por foto y ahorrar ~580 MiB: `IMAGE_SEARCH=0`. En el EC2 de 2 GiB
eso no cabe junto al resto. Para allí, construye con el modelo ligero:

```bash
EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 docker compose build agente
```

Cambiar de modelo obliga a re-entrenar, cosa que la construcción ya hace sola. Las métricas de arriba son
las de jina; con MiniLM hay que volver a medirlas.

No ejecutes `python -m app.entrenar` ni cargues el modelo dentro del contenedor en marcha: sería una
segunda copia y el contenedor muere por falta de memoria. `app.evaluar` usa la API HTTP para no hacerlo.
