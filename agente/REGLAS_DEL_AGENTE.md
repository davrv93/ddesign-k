# Reglas del agente de Baruka (V1 + V2)

Mapa de las reglas de código que hoy deciden qué hace y qué dice el bot. Estado al **06-10-2026** (rama `feat/agente-v2`, commit
`6a0c788`). Las líneas (`archivo:línea`) se mueven con cada cambio: úsalas para ubicarte, no como contrato. El apéndice A está
generado automáticamente del código; el resto está escrito a mano a partir de la lectura de esos archivos.

Qué es una «regla» aquí: una decisión tomada por código determinista (expresión regular, umbral, tabla o `if`), no por un modelo.
Lo que decide un modelo (clasificador e5, Jev, DeepSeek) está en la sección 1 solo para ubicar dónde empieza y dónde acaba la regla.

---

## 1. Capas y quién manda

| # | Capa | Qué decide | Dónde | ¿Regla o modelo? |
|---|---|---|---|---|
| 0 | **Bot Go** | menú numérico, código suelto (`V25`), pasos del pedido (talla, confirmación, ubicación), pausa por asesora | `backend/internal/bot/bot.go` (`step()`) | reglas |
| 1 | **Clasificador de intención** | qué quiere decir el mensaje (intención general, intención comercial, tipo de prenda). Las acciones propias del bot son 4: catálogo, foto, estado del pedido, asesora | `agente/app/modelo.py` | **modelo** (e5-small + regresión logística) |
| 2 | **Lectura de lo dicho** | extrae ocasión, fecha, día/noche, talla, color, presupuesto, ciudad, nombre; entiende «sí/no»; sabe qué pregunta dejó pendiente | `agente/app/memoria.py` | reglas |
| 3 | **Etapa de la venta** | prospección → seguimiento → cierre → venta confirmada | `agente/app/etapas.py` | reglas (con umbrales sobre el clasificador) |
| 4 | **Decisión del turno (V1)** | qué se contesta, si hay foto, si hay stock del color, si se agenda cita, si se arma el pedido | `agente/app/main.py`, `conversar()` | reglas (873 líneas) |
| 5 | **Redacción** | el texto: plantilla de código, o DeepSeek con una nota del código | `main.py` (`respaldo_codigo`, `redactar`) | modelo (DeepSeek) o reglas |
| 6 | **Filtros del texto del LLM** | quitar lo que el modelo no puede saber o inventó | `main.py` (`pulir`), `estructurado.py`, `jev` | reglas + Jev (modelo) |
| 7 | **V2** | reanaliza el turno con lo que V1 entendió; si coincide, redacta con plantillas y control de calidad; deriva lo fuera de giro | `agente/app/v2/` | reglas + plantillas (modelo local opcional) |

Orden real de un turno en modo V2 activo: **0 → 1 → 2 → 3 → 4 (V1 sin LLM) → V2 analiza → si V2 habla, 5 con plantilla; si no, V1 de
nuevo con LLM (5 → 6)**. V2 no reemplaza a V1: hereda sus decisiones.

---

## 2. Cadena de decisión de un turno en `conversar()` (V1)

Cada `if/elif` es una regla; **el orden decide quién gana**. Líneas aproximadas en `main.py`.

### 2.1 Antes de decidir qué contestar (≈1550–1700)

| Línea | Regla |
|---|---|
| 1575 | Si espera «¿cuál prenda?» y no hay filtro ni códigos → se queda esperando (`ESPERANDO_CUAL`) |
| 1587 | Si Jev está activo, propone la intención cuando el clasificador duda (< 0,80) |
| 1625 | `indaga_antes_de_ver`: sin anuncio ni prenda nombrada, primero se conoce la necesidad (ocasión → fecha → día/noche) sin mandar fotos |
| 1631 | Si contestó la pregunta pendiente de indagar y no hizo otra pregunta, se sigue indagando |
| 1638 | Quiere comprar pero no hay prenda → la etapa no cambia (`compra_sin_prenda`) |
| 1647 | Pide **otro color** que el de la prenda en foco → no pasa a cierre (`_pide_otro_color`) |
| 1658 | Un turno de cita en prospección/seguimiento sube a cierre |
| 1661 | Se echó atrás de una cita pendiente (objeción, cancelación) → se limpia la cita |
| 1674 | Con prenda en foco y pendiente «¿cuál?» → resuelve cuál |
| 1687 | «Comparación» de confianza alta sin ser variante → cuenta como pedir ver más |
| 1695 | `pide` (pide ver más) fuerza la intención a `otras_opciones` |
| 1702 | En cierre con prenda en foco → flujos del pedido |

### 2.2 Qué se contesta (cadena `if/elif`, ≈1710–1910)

| Línea | Condición (resumida) | Resultado |
|---|---|---|
| 1710 | turno de cita | arma o corrige la cita (`flujo_cita`): día, hora, horario, refrigerio |
| 1728 | cita hecha + despedida | cierra la cita |
| 1734 | ya tiene cita y se le preguntó la talla | esa talla es la que se va a probar, no un pedido |
| 1748 | dijo una talla | en cierre arma el pedido (`pedido`); si la talla no hay, lo dice y da las que sí |
| 1761 | cierre + intención de compra + prenda en foco + no pide otro color | «necesito tu talla» (`flujo_cierre`) |
| 1768 | venta confirmada + «ya pagué» | pide el comprobante |
| 1773 | venta confirmada + dijo a dónde va (Lima o provincia) | el total (lo suma el código) y los datos de pago van juntos |
| 1783–1791 | venta confirmada: pide los datos de pago, dice que sí a «¿te paso los datos?», o que sí con el voucher pendiente | pasa los datos de pago o espera el comprobante |
| 1793 | confirmó el pedido (transición a venta confirmada) | «quedó separado» + Lima o provincia |
| 1812 | «cambiar talla» | pregunta la talla |
| 1815 | `compra_sin_prenda` | pregunta cuál prenda |
| 1821 | «este vestido» sin anuncio ni nombre | pregunta cuál |
| 1833 | espera «¿cuál?» y no describe | «mándame la foto o el nombre» |
| 1851 | pidió una foto | manda esa foto |
| 1858 | `opcion` ≠ catálogo (menú numérico) | resuelve la opción |
| 1862 | indagando | pregunta de indagación |
| 1868 | catálogo genérico | dice qué tipos de prenda hay con stock |
| 1883–1903 | respuesta de flujo; pedir asesora explícito; acción del clasificador con confianza ≥ 0,55; código único en cierre; saludo/despedida directo | acción de bot / asesora / foto / pedido |
| 1903 `else` | cualquier otro mensaje | **búsqueda y redacción** (2.3) |

### 2.3 Dentro del `else` final (búsqueda y redacción)

1. `no_hay = lo_que_no_hay(...)`: ¿pide una prenda o un color que no hay con stock? Si sí: se dice «Por ahora no tengo…», **no se mandan
   fotos** y se pregunta «¿Quieres ver otras opciones?».
2. `una_opcion = mejor_opcion(...)`: la UNA prenda que ofrecería una vendedora (RAG + stock + color + presupuesto).
3. Si pidió un color que no hay y la mejor opción es de otro color → se convierte en `no_hay` (solo en prospección/seguimiento).
4. `pide` → `otras_opciones` (prendas que no vio, con stock, primero de la misma categoría); `es_catalogo` → vitrina por categoría.
5. Qué se manda como foto, cuánto (`MAX_SUGERENCIAS = 3`) y si se **ofrece** ver otras (`ofrecer`).
6. Siguiente pregunta: `memoria.siguiente(...)` (ver 3.7).
7. Redacción: DeepSeek con una **nota** que dicta el código («OFRECES UNA SOLA OPCIÓN…», «NO TENEMOS vestidos en rojo…»), o `respaldo_codigo()`.
8. Filtros `pulir` (ver 3.9).

---

## 3. Reglas por tema

### 3.1 Leer lo que dijo la clienta (`memoria.py`)
- **Talla**: `RE_TALLA_EXPLICITA` («soy M», «talla L»), `RE_TALLA_EN` («en M»), `RE_TALLA_SUELTA`, `RE_TALLA_ALIAS` («mediana»),
  `RE_TALLA_LA`/`LETRA` («la ele», solo si se preguntó la talla). `en S/ 330` no es talla S.
- **Fecha del evento**: `RE_FECHA`, `RE_FECHA_DIA`, `RE_FECHA_CORTA` («mañana» solo si se preguntó para cuándo), `RE_FECHA_PARA`.
- **Ocasión** (`RE_OCASION`/`OCASIONES`), **sin ocasión** (`RE_SIN_OCASION`), **no sabe** (`RE_NO_SABE`), **día/noche** (`RE_NOCHE`, `RE_DIA`).
- **Color** (`RE_COLOR`/`COLORES`), **presupuesto** (`RE_PRESUPUESTO`), **estatura**, **ciudad/distrito** (provincias y distritos de Lima).
- **Nombre**: `RE_NOMBRE` («me llamo»), `RE_SOY` («soy Alvaro» sí; «soy talla M» no); un nombre no es un color ni una ocasión.
- **Sí / no**: `RE_AFIRMA`, `RE_AFIRMA_INICIO`, `RE_NIEGA`; `afirma()` acepta hasta 6 palabras sin «?».
- **Espera**: `RE_ESPERA` («un momento», «ahora te digo»), `RE_SIN_DATO` («no la tengo»).
- **Abreviaturas de chat** (`_ABREV`): pa→para, q→que, xq→porque…
- **Pregunta pendiente**: `DETECTOR` reconoce qué preguntó el bot por su texto (el orden importa: «¿Confirmamos tu pedido en talla M?» es
  confirmar, no talla). `PENDIENTES`, `PERSISTENTES`, `ESPERA`.
- **Temperatura** (`RE_FRIO`, `RE_URGENTE`, `temperatura()`): ≤ 7 días caliente; 8–30 tibia; > 30 fría; señales de compra/cita suben.

### 3.2 Etapa de la venta (`etapas.py`, `decidir()`)
Umbrales sobre el clasificador: **≥ 0,80** se usa tal cual; **0,60–0,80** solo transiciones prudentes; **< 0,60** la etapa no cambia.
Reglas, en orden:
1. Botón de talla de la tarjeta (`RE_BOTON_TALLA`) → intención de compra.
2. Pide cita (`memoria.RE_CITA`, no negado) → compra + cita.
3. «Sí» corto con pendiente `probar` → cita; «sí» corto con pendiente `confirmar` → confirmación; «sí» sin contexto → interés, no compra.
4. «No» corto con `confirmar` pendiente → cancelación; sin contexto → no cambia la etapa.
5. **Confirma y pregunta a la vez** (`RE_CONFIRMA_COMPUESTO`, no con `RE_DUDA_CONFIRMA`) → confirmación.
6. Señal fuerte de compra (`RE_COMPRA`) → intención de compra (salvo con venta confirmada: ahí «¿a qué número yapeo?» es pago).
7. Sí «dicho de otra manera» (`ya pues`) con `confirmar`/`probar` pendiente.
8. Confirmación fuera de cierre → se trata como interés.
9. El primer mensaje es siempre prospección, salvo compra explícita.

### 3.3 Cita y horario (`memoria.py`)
- **Detectar pedir cita** (`RE_CITA`): «quiero probármelo», «¿puedo ir mañana a las 10?», «¿puedo pasarme a probar?», «¿me podrías agendar
  el domingo…?», «¿el sábado a las 11 te parece bien?», «cerrar la cita».
- **Leer día y hora** (`leer_cita`, `RE_HORA`, `fecha_iso`): suma lo que ya dio; si el día nombrado no coincide con el número, propone el
  más cercano.
- **Validar** (`validar_cita`): `dia_pasado`, `hora_pasada`, `fuera_horario` (abre 9:00, cierra 19:00), `refrigerio` (13:00–14:00),
  `despues_evento`. Solo con día y hora válidos queda `sabemos.cita`.
- **El bot Go** convierte la cita nueva en un pedido en `consulta` con una nota para la asesora; no reserva stock.

### 3.4 Confirmación y pedido (`main.py`, `etapas.py`)
- Decir la talla **no** arma el pedido salvo en cierre con prenda en foco.
- El «sí» a «¿Confirmamos tu pedido?»: en WhatsApp lo resuelve el bot Go; en la web, el agente (`dec["transicion"]` a venta confirmada).
- Con pedido confirmado: envío Lima S/ 15 o provincia S/ 20 (de `seed/venta.json`, lo suma el código), luego datos de pago, luego voucher.
- Los datos de pago **solo** salen con venta confirmada.

### 3.5 Colores, categorías y stock
> **Desde el 06-10 (noche) la interpretación del texto vive en `app/solicitud.py`:** `interpretar(mensaje)` devuelve un `SolicitudCliente`
> (color, `pide_color`, `pide_cita`, `mas_opciones`, `mas_barato`, `catalogo`, `no_otro`, `regatea_comparando`, `busca_cambio`, `no_mostrar`). Las regex de
> color y de «otras opciones» de esta sección y de la 3.6 se movieron ahí sin cambiar su lógica; `main.py` las importa. Las pruebas están en
> `app/prueba_solicitud.py` (37 casos). La respuesta de `/chat` lleva `solicitud` con lo que el texto pide.

- `color_dicho` / `color_que_pide`: el color que PIDE (con verbo: «¿tienen en rojo?»; sin verbo si va tras una prenda o «algún/uno/otro»:
  «algún vestido rojo?», «vestido rojo?», «y rojo?»). «¿Combina con zapatos dorados?» no cuenta. «Rojo no» lo descarta.
- `_raiz_color`: «negra/negro» → `negr`; «roja/rojo» → `roj`; «rosado/palo rosa» → `ros`. `_de_color` compara contra el campo color de la ficha.
- `lo_que_no_hay`: si la tienda no tiene (con stock vivo) la categoría o el color pedidos → «Por ahora no tengo vestidos en rojo». Sin datos de
  stock **no** afirma que no hay.
- `mejor_opcion`: prefiere el color pedido si lo hay; si no hay ninguno de ese color y la clienta lo dijo, **no se manda otra prenda**.
- `_pide_otro_color`: «yo quiero uno rojo» mirando el Kendall negro no es compra de ese vestido.
- `del_color_que_pide`: «otras opciones» solo del color pedido.
- La talla pedida debe existir: `tallas_de()` y `_sin_tallas_falsas` (sección 3.9).

### 3.6 Pedir ver más, fotos y catálogo (`main.py`)
- `pide_mas`: «otras opciones/modelos» (`RE_MAS_OPCIONES`), «sí» a la oferta (`acepta_oferta`), «más barato» (`RE_MAS_BARATO`).
  **No** cuenta si: `RE_NO_OTRO` («no quiero otro vestido, quiero el Holly»), compara con otra tienda (`RE_COMPARA_TIENDA`) sin pedir ver,
  `no_mostrar` («no me muestres nada»), `pregunta_variante` («¿lo tienes en otros colores?»). **Sí** cuenta pedir una categoría distinta a la del foco
  con «busco/quiero…».
- `RE_CATALOGO`: «muéstrame tu catálogo» → dice qué categorías hay con stock y pregunta cuál; no manda fotos al azar.
- `pide_foto_de` / `RE_PIDE_FOTO`: «pásame la foto del Irla» (manda esa aunque ya la vio). «Te mando una foto» es la clienta enviando.
- `ofrecida_hace_poco`: no se repite «¿Quieres ver otras opciones?» si se ofreció en los últimos 6 mensajes del bot (salvo color sin stock).
- Tope: **3 fotos** por turno (`MAX_SUGERENCIAS`). Solo se mandan si pide opciones o abre una búsqueda nueva.

### 3.7 Qué pregunta el bot después (`memoria.siguiente`)
- Orden por etapa (`ORDEN`): prospección `ocasion → fecha → horario → talla`; seguimiento `fecha → horario → talla → probar`;
  cierre `talla → confirmar`; venta confirmada `lima_o_provincia → pago → voucher`.
- Lo ya preguntado o sabido no se repite (`quitar_repetidas`). Seguimiento caliente/frío reordena.
- Primer mensaje: «¿qué estás buscando?»; «tengo un evento» → «¿qué evento es?».
- `INDAGAR = ocasión, fecha, horario`: mientras se indaga no se muestran prendas, salvo que las pida.

### 3.8 Derivar a una persona (asesora)
- `RE_ASESORA_EXPLICITA` / `RE_PIDE_ASESORA`: pedir hablar con alguien, reclamo, queja. **No** deriva: `RE_PREGUNTA_SI_BOT` («¿eres un bot?» se contesta) ni
  «tengo que hablarlo con alguien primero» (`duda_propia`).
- Fuera del rubro (historia, cálculos…): `FUERA_DE_GIRO` («no es mi giro»).
- **V2** (`v2/decision.py`): `FUERA_DE_ALCANCE` + `_FUERA_GIRO_RE` (cripto, empleo, bolsa, préstamo, reclamo, denuncia) → `pedir_asesora`;
  `v2/agente._derivar_fuera_de_giro` lo convierte en acción `asesora` solo en modo activo (`V2_DERIVA=0` lo apaga).
- Pasar a asesora **pausa** el bot en WhatsApp: por eso V1 solo lo hace si lo piden con palabras.

### 3.9 Filtros del texto del LLM (`pulir`, `estructurado.py`, Jev)
Se aplican al texto de DeepSeek antes de enviarlo, frase a frase:
- `_whatsapp`, `_sin_resaludo`, `_sin_pies` (quita pies de foto copiados), `_sin_escasez` (`RE_ESCASEZ`: «no suele durar»).
- `_sin_inventos`: quita tiempos de entrega y descuentos (`RE_TIEMPO_ENTREGA`, `RE_DESCUENTO_INVENTADO`), precios que no existen en el catálogo
  (`RE_PRECIO_DICHO`) y telas que la ficha no nombra (`RE_TELA_DICHA`).
- **`_sin_tallas_falsas`** (06-10): una frase que afirma una talla que ninguna prenda en juego tiene en stock se cambia por «En talla XL no hay… Hay en S, M y L».
- `_sin_repetir`, `_sin_nombre`, `memoria.quitar_repetidas`: no repite preguntas ni nombres.
- Si no queda texto, contesta `respaldo_codigo()`.
- `_frases` no parte «a. m.»/«p. m.» (06-10).
- **Jev** (modelo, `JEV_VERIFICAR=1`): quita lo que el LLM afirme de la prenda y su ficha no diga; regenera una vez si no contestó la pregunta.

### 3.10 Reglas de V2 (`agente/app/v2/`)
- `decision.ReglasDecision`, en orden: fuera de alcance → `pedir_asesora`; intención informativa → `responder`; pide ver y no vio → `recomendar`;
  sin prenda → `preguntar`/`responder`; prenda sin stock consultado → `consultar_stock`; con stock online → `recomendar` (o `preguntar` si ya la vio);
  solo en sucursal → `responder`; sin stock → `buscar_alternativa` por RAG → `recomendar` o `preguntar`.
- **Cuándo habla V2** (`_motivo_no_habla`): no en flujos fijos (pedido, pago, cita, menú), no si su plan difiere del de V1, no si no es una acción
  de `V2_HABLA` (por defecto `recomendar,preguntar`), no en el primer mensaje, no si falta plantilla segura o la foto no es la del plan.
- **Control de calidad** (`calidad.py`, `factual.py`): rechaza precios, nombres o hechos que el plan no respalda.
- **Plantillas** (`plantillas.yaml`): variantes elegidas por código; el modelo local (opcional) solo elige o reescribe, con compuerta factual.
- **Cambios de tema** (`temas.py`): «suspender, no cancelar» una pregunta pendiente (`RE_PIVOTE`, `RE_ACUSE`, `RE_NO_SABE_TALLA`…).
- **Aclaración** (`motor.duda_rag`): si el reranker duda entre opciones → pregunta con las 3; sin evidencia → deriva.
- **Recuperación híbrida** (`rag.py`, `lexico.py`): BM25F por campos + vector a peso bajo (RRF); `rerank.elegir` con regla «no quita» y gate de tiempo.

### 3.11 Valores de negocio que actúan como reglas
- Showroom Juan Ayllón 459, Santa Anita; solo con cita; L–D 9:00–19:00; refrigerio 13:00–14:00.
- Envío Lima S/ 15 (Olva), provincia S/ 20 (Olva o Shalom). Tallas S, M, L. Cambios solo por falla de fábrica en 7 días.
- Descuentos y promociones: los da una asesora (`escribe *4*`). Datos de pago: solo con venta confirmada.
- Umbrales: `INTENT_THRESHOLD 0,35`, `ACTION_THRESHOLD 0,55`, `FAST_THRESHOLD 0,85`, `NO_RAG_THRESHOLD 0,6`, `MAX_SUGERENCIAS 3`.

### 3.12 Reglas de evaluación (qué cuenta como error en las 100 conversaciones)
Categorías de `conversaciones.py`, de más a menos grave: `error_o_vacia`, `dato_falso` (precio, tallas), `juez_invento`, `pago_antes`, `cita_invalida`,
`etapa_salto`, `prenda_inventada`, `anuncio_asumido`, `fotos_sin_necesidad`, `varias_opciones`, `pregunta_repetida`, `juez_hilo`, `juez_respondio`,
`prospeccion_sin_pregunta`, `lo_sin_prenda`, `juez_presiono`, `juez_avanzo`, `juez_robotica`, `latencia`.
Son reglas de la **prueba**, no del bot; algunas dan falsos positivos (p. ej. `prospeccion_sin_pregunta` marca «estoy viendo nomás» aunque el bot haga bien en no insistir).

---

## 4. Duplicaciones y huecos conocidos

**La misma idea escrita en varios sitios** (hay que cambiarla en todos o divergen):
- «Pide un color»: `lo_que_no_hay`, `color_que_pide`, `mejor_opcion`, `otras_opciones`, `pide_mas`, `_pide_otro_color`, y `memoria.RE_COLOR`.
- «Sí» / «no»: `main.RE_SI`, `memoria.RE_AFIRMA`/`RE_NIEGA`/`RE_AFIRMA_INICIO`, `etapas.RE_AFIRMA`/`RE_NIEGA`, `v2/temas.RE_SI_ROTUNDO`/`RE_ACUSE`.
- «Pide una persona»: `main.RE_PIDE_ASESORA`, `main.RE_ASESORA_EXPLICITA`, `v2/decision._PIDE_PERSONA_RE`.
- «Pide cita»: `memoria.RE_CITA`, `etapas` (la usa), y `leer_cita` más el estado `pend == "cita"`.
- El horario del showroom: `memoria.ABRE/CIERRA/REFRIGERIO`, `seed/venta.json` y `seed/tienda.md` (CLAUDE.md: «si cambia, se cambia en los tres»).

**Huecos vistos en las 100 conversaciones y sin arreglar**
- Regateo: se repite «el precio es S/ 330, como te comenté» y se sigue preguntando la fecha.
- Preguntas de ajuste o largo («¿es entallado o suelto?», «¿con vuelo?») se contestan con la descripción genérica de la ficha.
- «Mi presupuesto» se guarda pero no filtra las opciones que se ofrecen.
- «Sí, quiero el de la foto» cuando la clienta describe un vestido que vio: depende del RAG, no de una regla.
- La foto del V24 (Conjunto Kabanova Azul) muestra un vestido: dato del catálogo, no del código.

**Frágil por diseño**: las regex cubren las frases que vimos; cada frase nueva puede ser un hueco. Por eso cada arreglo lleva su caso en
`data/regresion_conversaciones.jsonl` (C32–C51).

---

## Apéndice A. Todas las expresiones regulares (generado del código)

Columnas: nombre · `archivo:línea` · comentario del código (si lo tiene) · comienzo del patrón.

**Alcance del apéndice:** solo las regex definidas a nivel de módulo (112). No incluye las ≈ 60 llamadas `re.search/re.match` escritas dentro de funciones (en `main.py` y `memoria.py`), ni las listas de palabras (`COLORES`, `OCASIONES`, `PRENDAS`, `PROVINCIAS`, `_DISTRITOS`, `_TELAS`) con las que se arman varias regex.

### `app/main.py`

| Nombre | Línea | Qué dice el código | Comienzo del patrón |
|---|---|---|---|
| `RE_NO_TEXTO` | 263 |  | `re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")` |
| `RE_PIE` | 496 | Un pie de foto del bot: «*V24* Conjunto… / Tallas: … / 👉 Escribe *V24* para pedirlo». | `re.compile(r"👉\|^\*?[A-Z]{1,3}-?\d{1,3}\*?\s+\S[^\n]*\n(Tallas:\|Agotado\|Hay en:)")` |
| `RE_PARRAFO_PIE` | 497 |  | `re.compile(r"👉\|Escribe \*?[A-Z]{1,3}-?\d{1,3}\*? para pedirlo", re.I)` |
| `RE_SALUDO_INICIAL` | 718 |  | `re.compile(r"^[¡!]*\s*(hola\|holi\|buenas\|buen d[ií]a\|buenos d[ií]as\|buenas (tardes\|noches)` |
| `RE_PREGUNTA_FOTO` | 763 | «¿Te gustaría que te pase la foto?» cuando la foto ya va: sobra la pregunta (y quita el sitio a la de verdad). | `re.compile(r"¿[^?¿]*\b(te (pas\|mand\|env[ií]\|muestr\|compart)\w*\|quieres (ver\|que te)\|te g` |
| `RE_ROPA` | 833 |  | `re.compile(r"\b(vestid\|blus\|polo\|jean\|pantal\|palazzo\|conjunt\|blazer\|saco\|falda\|enteri` |
| `RE_LOGISTICA` | 849 | Preguntas de logística: ir a la tienda, pagar, envío. Aunque nombren «el vestido», no piden ver más modelos. | `re.compile(r"\b(d[oó]nde\|direcci[oó]n\|ubicaci[oó]n\|ubicad\|queda[ns]?\|local\|sucursal\|tien` |
| `RE_MAS_OPCIONES` | 853 | Pedir más opciones de forma explícita. | `re.compile(r"\b(otr[oa]s? (modelos?\|opci\|vestid\|colou?r\|prendas?\|conjunt\|blus\|fald\|blaz` |
| `RE_PROMESA_FOTOS` | 871 | «Te paso las fotos.» sin fotos que mandar: promesa vacía, se quita. | `re.compile(r"[^.!?¿¡\n]*\b(te (paso\|mando\|env[ií]o\|muestro\|dejo)\|aqu[ií] (tienes\|van)\|ah` |
| `RE_PREGUNTA_OFERTA` | 872 |  | `re.compile(r"¿[^?¿]*\b(te (pas\|muestr\|mand\|env[ií]\|enseñ)\w*\|quieres ver\|te gustar[ií]a v` |
| `RE_SI` | 874 |  | `re.compile(r"^\s*(s[ií]+\|dale\|ok(ey)?\|claro\|ya\|bueno\|porfa\|por favor\|sip\|de una\|obvio` |
| `RE_OTRO_DE_ESA` | 899 |  | `re.compile(r"\b(otros? colou?r(es)?\|otras? tallas?\|qu[eé] colores\|en qu[eé] color)\b", re.I)` |
| `RE_CATALOGO` | 907 |  | `re.compile(r"\bcat[aá]logo\|\b(ver\|mu[eé]str[ae]me\|ens[eé][nñ][ae]me)\s+(los\|tus\|sus\|todos` |
| `RE_OTRAS` | 908 |  | `re.compile(r"\b(otr[oa]s?\|m[aá]s\|parecid\|alternativ\|diferente\|distint)", re.I)` |
| `RE_MAS_BARATO` | 911 |  | `re.compile(r"\bmas (barat\|economic\|comod\|bajo)\w*\|\bmenos precio\b\|\balgo (barat\|economic` |
| `RE_NO_OTRO` | 918 |  | `re.compile(r"\bno (quiero\|busco\|necesito\|me interesa\|deseo\|quisiera)\b[^.?!]{0,20}\botr[oa` |
| `RE_COMPARA_TIENDA` | 921 |  | `re.compile(r"\b(gamarra\|mesa redonda\|otra tienda\|otras tiendas\|en otro lado\|en otros lados` |
| `RE_PIDE_EXPLICITO` | 922 |  | `re.compile(r"\b(muestr\|ensen\|pasame\|mandame\|enviame\|otras? opcion\|otros? modelo\|ver (otr` |
| `RE_BUSCA_CAMBIO` | 923 |  | `re.compile(r"\b(busco\|quiero\|necesito\|prefiero\|mejor\|no me sirve\|no me gusta)\b")` |
| `RE_TALLA` | 1014 |  | `re.compile(r"\b(?:talla\s+)?(xxl\|xl\|xs\|s\|m\|l)\b(?!\s*/)", re.I)` |
| `RE_CONFIRMA` | 1015 |  | `re.compile(r"^\s*(s[ií]+,?\s*confirm\|confirm\|s[ií]+\s*,?\s*(lo\|la)\s*quiero\|s[ií]+$\|dale\|` |
| `RE_PIDE_FOTO` | 1072 | «pásame / mándame / ¿tienes la foto…?» pide que el BOT la mande; «te mando una foto» es la clienta enviando (intención «foto» del clasificador, flujo de búsqueda por foto). | `re.compile(r"\b(m[aá]nd[ae]me\|p[aá]s[ae]me\|env[ií][ae]me\|ens[eé][nñ][ae]me\|mu[eé]str[ae]me\` |
| `RE_PIDE_COLOR_VERBO` | 1198 | Un color cuenta si lo está pidiendo («¿tienen en rojo?», «busco uno verde», «¿y en azul?»), no si lo comenta («¿combina con zapatos dorados?»). | `re.compile(r"\b(tien\w+\|hay\|busc\w+\|quier\w+\|quisiera\|necesit\w+\|tendr\w+\|vend\w+\|manej` |
| `RE_ANTES_DEL_COLOR` | 1202 |  | `re.compile(rf"(?:\b(?:alg\w*\|un[oa]s?\|otr[oa]s?)\s+(?:{_PRENDA_ANTES})?\|\b{_PRENDA_ANTES}\|^` |
| `RE_CATEGORIA` | 1278 | Prenda que la clienta nombra. El orden importa: «conjunto de blusa y falda» es conjunto. | `[("conjunto", re.compile(r"\bconjunt\|\bset\b\|dos piezas", re.I)), ("enterizo", re.compile(r"\` |
| `RE_NO_PRENDA` | 1295 |  | `re.compile(r"\bno\s+(?:es\s+)?(?:un\|una\|el\|la\|los\|las)?\s*\w+", re.I)` |
| `RE_ESTA_PRENDA` | 1327 | «este vestido», «ese modelo», «el del anuncio»: habla de una prenda concreta que no nombra. | `re.compile(r"\b(est[ea]\|es[ea]\|aquel\|aquella)\s+(vestido\|modelo\|conjunto\|blusa\|prenda\|e` |
| `RE_PIDE_ASESORA` | 1350 |  | `re.compile(r"\b(asesor[ae]?s?\|vendedor[ae]?s?\|persona\|humano\|alguien\|encargad[oa]\|operado` |
| `RE_ASESORA_EXPLICITA` | 1353 | Pedir una persona con todas sus letras: deriva aunque el clasificador no lo vea («oe quiero poner un reclamo»). | `re.compile( r"\b(hablar\|hable\|conversar\|comunic\w+\|contact\w+\|atienda\|atiendan\|atender\|` |
| `RE_PREGUNTA_SI_BOT` | 1361 | «¿eres un bot o una persona?» pregunta qué es; no pide que la atienda otra. | `re.compile(r"\b(eres\|sos\|es usted\|hablo con\|estoy hablando con\|me atiende\|me responde\|es` |
| `RE_YA_PAGO` | 1364 | Dice que ya pagó: lo que toca es el comprobante (no volver a mandar el Yape). | `re.compile(r"\bya (te \|les \|lo \|le )?(pague\|yapee\|deposite\|transferi\|cancele\|hice (el\|` |
| `RE_TIEMPO_ENTREGA` | 1368 | Lo que el LLM no puede saber porque no está en TIENDA: tiempos de entrega y descuentos. Si lo afirma, lo inventó. | `re.compile( r"\b(lleg\w*\|entreg\w*\|demor\w*\|tard\w*\|recib\w*\|lo tienes\|estar[aá] (ah[ií]\` |
| `RE_DESCUENTO_INVENTADO` | 1374 |  | `re.compile( r"\b\d{1,2}\s?%\|\bdescuento (de\|del)\b\|\bte (hago\|puedo hacer\|doy\|dejo\|aplic` |
| `RE_PRECIO_DICHO` | 1377 |  | `re.compile(r"S/\.?\s*\*?\s*(\d{2,4})(?:[.,]\d{2})?", re.I)` |
| `RE_TELA_DICHA` | 1380 |  | `re.compile(rf"\b({_TELAS})\b", re.I)` |
| `RE_TEMA_TIENDA` | 1382 | Palabras que dicen que la pregunta es de la tienda o de una prenda, aunque el clasificador la lea como general. | `re.compile(r"\b(corte\|pegad\w*\|entallad\w*\|ajustad\w*\|suelt\w*\|tela\|mangas?\|escote\|forr` |
| `RE_CORTE_EMOJI` | 1384 |  | `re.compile(r"(?<=[\U0001F300-\U0001FAFF☀-➿])\s+(?=[A-ZÁÉÍÓÚÑ¡¿])")` |
| `RE_TALLAS_DICHAS` | 1425 |  | `re.compile(r"\btallas?\s+((?:\*?(?:XXL\|XL\|XS\|S\|M\|L)\*?(?![A-Za-z])(?:\s*(?:,\|y\|o\|e\|/)\` |
| `RE_NEGA_TALLA` | 1426 |  | `re.compile(r"\b(no\|ni\|sin\|agotad[oa]s?\|nunca)\b\|se nos agot", re.I)` |
| `RE_PIDE_ESTADO` | 1493 |  | `re.compile(r"\b(pedido\|orden\|compra\|env[ií]o\|paquete\|lleg[oóa]\|estado\|seguimiento\|track` |
| `RE_PIDE_PAGO` | 1494 |  | `re.compile(r"\b(datos\|pasos\|formas?\|medios?\|m[eé]todos?) (de\|del\|para el) pago\|\bc[oó]mo` |
| `RE_PROMETE_PAGO` | 1497 |  | `re.compile(r"[^.!?\n]*\bte (paso\|env[ií]o\|mando\|comparto\|dejo)\b[^.!?\n]*\bdatos\b[^.!?\n]*` |
| `RE_QUIERE_FOTO` | 1499 | «¿cómo es?» pide verla; «¿cómo es el corte?» o «¿cómo es la tela?» pregunta un detalle (no se reenvía la foto). | `re.compile(r"\bfotos?\b\|\bim[aá]gen(es)?\b\|\bquiero verl[oa]\b\|\bmu[eé]stra(me)?l[oa]\b\|" r` |
| `RE_ESCASEZ` | 1536 | Escasez que el LLM inventa («no suele durar», «se agota rápido», «quedan poquitos»): del stock solo vale la línea AHORA. | `re.compile(r"[^.!?\n]*\b(no (suele\|suelen) durar\|se (nos )?agota\w* (r[aá]pido\|pronto\|volan` |
| `RE_VARIOS` | 1546 |  | `re.compile(r"\b(vestidos\|modelos\|opciones\|cat[aá]logo\|otr[oa]s?\|diferentes?\|variedad)\b",` |

### `app/memoria.py`

| Nombre | Línea | Qué dice el código | Comienzo del patrón |
|---|---|---|---|
| `_FORMA` | 98 | La forma que debe tener lo que llega de fuera en la memoria (lo demás se descarta al normalizar). | `{"cita": re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$"), "fecha_iso": re.compile(r"^\d{4}(-\d{2` |
| `_RE_ABREV` | 183 |  | `re.compile(r"(?<![\w/])(?:" + "\|".join(_ABREV) + r")(?![\w/])")` |
| `DETECTOR` | 197 | Qué pregunta hizo el bot, por su texto. Se mira solo la parte con «?» (las frases que preguntan). El orden importa: «¿Confirmamos tu pedido en talla M?» es confirmar, no talla. | `[ ("cual_prenda", re.compile(r"la foto o el nombre")), ("describir_prenda", re.compile(r"cuenta` |
| `RE_PREGUNTA` | 223 |  | `re.compile(r"[^.!?\n¿]*¿[^?]*\?\|[^.!?\n]*\?")` |
| `RE_TALLA_EXPLICITA` | 248 |  | `re.compile(r"\b(?:talla\|soy\|uso\|usaria\|visto\|seria)\s+(?:es\s+)?(?:una\s+\|la\s+)?(xxl\|xl` |
| `RE_TALLA_EN` | 250 | «¿lo tienen en M?», «sería en L»: la talla que pide, dicha con «en». «en S/ 330» no es talla S. | `re.compile(r"\ben\s+(?:talla\s+\|la\s+)?(xxl\|xl\|xs\|s\|m\|l)\b(?!\s*/)")` |
| `RE_TALLA_LA` | 253 | La letra dicha con su nombre («la ele», «eme»): solo si se preguntó la talla, porque «ese» es también «ese vestido». «ya pues, la S», «dame la M»: la talla con su artículo. | `re.compile(r"\bla\s+(xxl\|xl\|xs\|s\|m\|l)\b(?!\s*/)")` |
| `RE_TALLA_LETRA` | 254 |  | `re.compile(r"^(?:la \|talla \|una \|en )?(ele\|eme\|ese)$")` |
| `RE_TALLA_SUELTA` | 256 |  | `re.compile(r"(?<!\d)(?<!\d )\b(xxl\|xl\|xs\|s\|m\|l)\b(?!\s*/)")` |
| `RE_TALLA_ALIAS` | 257 |  | `re.compile(r"\b(?:talla\|soy\|uso)\s+(chica\|pequena\|small\|mediana\|medium\|grande\|large)\b"` |
| `RE_FECHA` | 262 |  | `re.compile( rf"\b(\d{{1,2}}\s*(?:de\s+)?(?:{_MESES})\b\|\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?" # «` |
| `RE_FECHA_DIA` | 272 |  | `re.compile(r"\b(el\s+\d{1,2})\b(?!\s*(?:soles\|anos\|cm\|%\|/\|:\|de la\|y media\|hrs?\b\|h\b\|` |
| `RE_FECHA_CORTA` | 274 | «hoy», «mañana»: solo son la fecha del evento si el bot preguntó para cuándo («¿puedo ir hoy?» no lo es)… | `re.compile(r"\b(pasado manana\|(?<!la )manana\|hoy\|esta semana\|este fin de semana)\b")` |
| `RE_FECHA_PARA` | 276 | …o si lo dice ella: «es para mañana», «lo necesito para este fin de semana». | `re.compile(r"\b(?:para\|es\|sera\|seria)\s+(pasado manana\|manana\|hoy\|esta semana\|este fin d` |
| `RE_PRENDA` | 281 |  | `[(k, re.compile(rx)) for k, rx in PRENDAS]` |
| `RE_ESTATURA` | 282 |  | `re.compile(r"\b(1[.,]\s?[4-9]\d?\|1\s[4-9]\d)\b(?:\s*m\b\|\s*mts?\b\|\s*metros?\b)?\|\bmido\s+(` |
| `RE_CIUDAD` | 287 |  | `re.compile(r"\b(" + "\|".join(PROVINCIAS) + r"\|lima\|callao)\b")` |
| `RE_DISTRITO` | 298 |  | `re.compile(r"\b(" + _DISTRITOS + r")\b")` |
| `RE_DISTRITO_DICHO` | 299 |  | `re.compile(r"\b(?:soy de\|vivo en\|estoy en\|envi\w* a\|mand\w* a\|llega\w* a\|delivery a\|desp` |
| `RE_SIN_OCASION` | 323 | «ninguna», «nada en especial», «no es para nada» a «¿para qué ocasión?»: no hay evento (es para el día a día). | `re.compile(r"\bningun[ao]?\b\|\bnada en especial\b\|\bno es para (nada\|ningun)\|\bsin ocasion\` |
| `RE_NO_SABE` | 326 | «todavía no tengo fecha», «no sé», «aún no»: contestó que no sabe. No se le vuelve a preguntar lo mismo. | `re.compile(r"\bno (lo )?se\b\|\bni idea\b\|\bno tengo (fecha\|idea\|dia\|claro)\b\|\b(todavia\|` |
| `RE_OCASION` | 328 |  | `[(k, re.compile(rf"\b(?:{rx})")) for k, rx in OCASIONES]` |
| `RE_NOCHE` | 329 |  | `re.compile(r"\b(de\|en la\|por la\|a la) noche\b\|\bnocturn\|\bnoche\b")` |
| `RE_DIA` | 330 |  | `re.compile(r"\b(de\|en el\|durante el) dia\b\|\b(en\|por) la (manana\|mananita\|tarde\|tardecit` |
| `RE_SALUDO_NOCHE` | 332 |  | `re.compile(r"buenas noches\|buenos dias\|buen dia\|buenas tardes")` |
| `RE_COLOR` | 336 |  | `re.compile(r"\b(" + "\|".join(COLORES) + r")(?:e?s)?\b")` |
| `RE_PRESUPUESTO` | 337 |  | `re.compile(r"(?:presupuesto\|hasta\|maximo\|no mas de\|menos de\|gastar\|tengo\|cuento con)\s*(` |
| `RE_ESPERA` | 340 | «oh sí», «a ver un momento», «ahora te digo el nombre»: todavía no responde, pero va a hacerlo. | `re.compile(r"\b(un momento\|un momentito\|un segundo\|un ratito\|a ver\|dejame (ver\|buscar\|re` |
| `RE_SIN_DATO` | 343 | «no la tengo», «no sé el nombre», «no me acuerdo»: no puede mandar foto ni nombre. | `re.compile(r"\bno (la \|lo )?(tengo\|se\|recuerdo\|me acuerdo\|encuentro\|guarde)\b\|\bno tengo` |
| `RE_DESCRIBE` | 346 | Lo que describe una prenda: con esto sí se buscan parecidos. | `re.compile(r"\b(azul\|roj[oa]\|rosad[oa]\|rosa\|palo rosa\|negr[oa]\|blanc[oa]\|beige\|nude\|ve` |
| `RE_AFIRMA` | 351 |  | `re.compile(r"^(s+i+p?\|claro\|ok\w*\|dale\|ya\|bueno\|listo\|perfecto\|de acuerdo\|por supuesto` |
| `RE_NIEGA` | 352 |  | `re.compile(r"^(no+\|nop\|nones\|nel\|nah\|no gracias\|mejor no\|todavia no\|aun no\|no todavia\` |
| `RE_AFIRMA_INICIO` | 356 | Un «sí» dicho de cualquier manera a una pregunta de sí/no: «si ca ver», «ya pues», «claro que sí pásamelos». | `re.compile(r"^(s+i+p?\|sip\|claro\|ya\|dale\|ok\w*\|listo\|bueno\|perfecto\|de acuerdo\|por sup` |
| `RE_NOMBRE` | 359 | «me llamo Alvaro», «mi nombre es Ana María»: el nombre, no un pedido de hablar con una persona. | `re.compile(r"\b(?:me llamo\|mi nombre es\|(?:te\|le\|les\|los) saluda\|habla)\s+([a-zñ]{2,20}(?` |
| `RE_SOY` | 361 | «soy alvaro» también es presentarse; «soy talla M», «soy de Tacna», «soy bajita» no. Solo en mensajes cortos. | `re.compile(r"^(?:hola[\s,]+\|buenas[\s,]+)?soy\s+([a-zñ]{3,20})(?:\s+([a-zñ]{3,20}))?[\s.!,]*$"` |
| `RE_HORA` | 607 |  | `re.compile( r"\b(?:a\s+)?(?:las?\|eso de las?\|tipo\|como a las?)\s+(\d{1,2})(?:\s*[:.h]\s*(\d{` |
| `RE_CITA` | 666 | Pedir cita para probarse es querer comprar (etapas.py lo trata como intención de compra). | `re.compile( r"\b(quiero\|quisiera\|me gustaria\|puedo\|podria\|voy a\|deseo\|vamos a\|iria\|ire` |
| `RE_NECESIDAD` | 681 | Cuenta una necesidad sin nombrar prenda: «tengo un evento», «busco algo para una boda». | `re.compile(r"\bevento\b\|\b(busco\|necesito\|quiero)\s+(un\|una\|algo)\b\|\bpara (un\|una\|mi) ` |
| `RE_PIDE_VER` | 684 | Pide ver prendas: entonces se le muestra una opción aunque falte saber algo de la necesidad. | `re.compile(r"\b(muestr\w*\|ensen\w*\|que (modelos\|vestidos\|opciones) (tienes\|tienen\|hay)\|q` |
| `RE_NO_MOSTRAR` | 688 | «no me muestres nada todavía», «no me mandes fotos»: lo contrario de pedir ver. | `re.compile(r"\bno (me \|nos )?(muestr\|ensen\|mand\|pas\|envi)\w*")` |
| `RE_FRIO` | 729 |  | `re.compile(r"\bsolo (estoy \|ando )?(viendo\|mirando\|averiguando\|preguntando\|cotizando)\|\bn` |
| `RE_URGENTE` | 733 |  | `re.compile(r"\burgen(te\|cia)\b\|\bme urge\b\|\blo antes posible\|\bcuanto antes\|\blo mas pron` |
| `RE_HAY_EVENTO` | 957 | «tengo un evento», «es para una ocasión especial»: ya dijo que hay algo que celebrar; toca saber qué. | `re.compile(r"\bevento\b\|\bcelebraci\w+\|\bocasion\b\|\breunion\b\|\bceremonia\b")` |
| `RE_BUSCA_ROPA` | 959 | Dice que busca algo de vestir sin decir qué prenda ni para qué («quisiera ropa formal para mi pareja»). | `re.compile(r"\bropa\b\|\bprendas?\b\|\bformal(es)?\b\|\belegantes?\b\|\bcasual(es)?\b\|\boutfit` |

### `app/etapas.py`

| Nombre | Línea | Qué dice el código | Comienzo del patrón |
|---|---|---|---|
| `RE_AFIRMA` | 43 | Respuestas cortas que solo se entienden por lo que el bot acaba de preguntar. | `re.compile(r"^(s+i+p?\|sii+\|claro( que si)?\|ok(ey\|is)?\|dale\|ya\|bueno\|listo\|correcto\|as` |
| `RE_NIEGA` | 44 |  | `re.compile(r"^(no+\|nop\|nel\|no gracias\|mejor no\|todavia no\|aun no)[\s.!,]*$")` |
| `RE_COMPRA` | 46 | Señales fuertes de compra. Con una de estas no hace falta que el clasificador esté seguro. | `re.compile( r"\b(quiero (comprar\|llevar\|reservar\|separar\|pedir\|adquirir\|ordenar\|apartar)` |
| `RE_BOTON_TALLA` | 59 | El botón de talla de las tarjetas de la web: elegir talla es querer esa prenda. | `re.compile(r"^talla\s+\w+\s+del\s+[a-z]{1,3}-?\d+")` |
| `RE_CONFIRMA_COMPUESTO` | 60 |  | `re.compile(r"^(s+i+p?\|sii+\|claro\|ya\|dale\|ok(ey\|is)?\|listo\|bueno\|perfecto\|de acuerdo\|` |
| `RE_DUDA_CONFIRMA` | 64 | Lo que convierte ese «sí» en duda o cambio: no se confirma, se atiende. | `re.compile(r"\b(pero\|no (quiero\|es\|me\|lo\|la\|todavia\|aun)\|cambi\w+\|otra? (talla\|color\` |

### `app/estructurado.py`

| Nombre | Línea | Qué dice el código | Comienzo del patrón |
|---|---|---|---|
| `RE_FRASE` | 38 |  | `re.compile(r"[^.!?¡¿]*(?:¿[^?]*\?\|¡[^!]*!\|[^.!?]+[.!?]+\|[^.!?]+$)", re.S)` |
| `RE_JSON` | 39 |  | `re.compile(r"\{.*\}", re.S)` |
| `RE_SALUDO` | 40 |  | `re.compile(r"(hola\|holi\|buenas\|buen[oa]s?\s+(d[ií]as?\|tardes\|noches)\|bienvenid[ao]s?\|qu[` |
| `RE_PRESENTA` | 42 |  | `re.compile(r"[Ss]oy (?:tu \|la )?[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b")` |
| `RE_SIN_ANTECEDENTE` | 44 | Frases que hablan de la prenda sin decir cuál: antes de la foto no se entienden («¿qué es ideal?»). | `re.compile( r"(es\|est[aá]\|este\|esta\|ese\|esa\|esto\|lo\|la\|le\|su\|sus\|tiene\|viene\|cae\` |

### `app/venta.py`

| Nombre | Línea | Qué dice el código | Comienzo del patrón |
|---|---|---|---|
| `RE_MATERIAL` | 51 |  | `re.compile(r"\b(material\w*\|tela\w*\|de qu[eé] (es\|est[aá] hech[oa])\|gasa\|forro)\b", re.I)` |
| `RE_TELA` | 63 |  | `[re.compile(rx, re.I) for rx in ( rf"tipo de tela:?\s*(?:vestido\s+)?({_TELA})\b", rf"tejido de` |

### `app/v2/decision.py`

| Nombre | Línea | Qué dice el código | Comienzo del patrón |
|---|---|---|---|
| `_PIDE_PERSONA_RE` | 35 |  | `re.compile(r"(hablar\|conversar).{0,25}persona\|\basesora\b\|\bencargad[oa]\b\|\bhumano\b\|eres` |
| `_FUERA_GIRO_RE` | 36 |  | `re.compile(r"criptomoneda\|bitcoin\|\b(quiero\|buscan)\b.{0,25}(trabaj\|empleo\|personal)\|bols` |

### `app/v2/temas.py`

| Nombre | Línea | Qué dice el código | Comienzo del patrón |
|---|---|---|---|
| `RE_PIVOTE` | 120 | Cambio total: rechaza lo que veníamos viendo o cambia el rumbo. | `re.compile( r"\bya no (?:quiero\|me interesa\|me gusta\|busco\|lo quiero\|la quiero\|necesito)\` |
| `RE_DESPEDIDA` | 126 |  | `re.compile(r"\b(?:chau\|chao\|adios\|hasta luego\|bye\|nos vemos\|buenas noches\|hasta manana)\` |
| `RE_SI_ROTUNDO` | 128 | «sí», «claro», «dale» a «¿Qué talla usas: S, M o L?» no dice cuál. «ok», «ya», «listo» son un acuse, no una respuesta ambigua. | `re.compile(r"^(?:s+i+p?\|claro\|dale\|por supuesto\|de acuerdo\|si+ (?:por favor\|porfa\|claro)` |
| `RE_ACUSE` | 129 |  | `re.compile(r"^(?:ok\w*\|ya+\|yap\|dale\|listo\|ah+ ya\|ah+\|mmm+\|jaja\w*\|jeje\w*\|aja+\|entie` |
| `RE_NO_SABE_TALLA` | 131 | Talla: no sabe, o duda entre dos. | `re.compile(r"\bno (?:se\|sabria\|conozco\|tengo claro\|tengo idea)\b.{0,24}\b(?:talla\|medida\|` |
| `RE_LETRA_TALLA` | 134 |  | `re.compile(r"(?<![\w/.])(xxl\|xl\|xs\|s\|m\|l)(?![\w/])")` |

### `app/v2/accion.py`

| Nombre | Línea | Qué dice el código | Comienzo del patrón |
|---|---|---|---|
| `SALUDO` | 14 | Saludo, presentación y la invitación o el «Así te digo…» que V1 pone junto a su pregunta («Cuéntame y te ayudo»). | `re.compile(r"^\W*(hola\|holi\|buen[oa]s?\|bienvenid\|soy\s+\w+\|qu[eé] gusto\|mucho gusto\|as[i` |
| `CORTE` | 15 |  | `re.compile(r"(?<=[.!?])\s+\|\n+")` |


---

## Tabla de respuestas del código (`app/respuestas.py`, 07-10-2026)

La respuesta que arma el código cuando no redacta el LLM ya no es una cadena de `if/elif` dentro de `main.py`: son
**hechos con nombre** (`hechos()`, el mensaje se lee una vez) y una **tabla de reglas por grupo**. Se regenera con
`python3 -m app.respuestas`; la integridad la prueba `python3 -m app.prueba_respuestas` (corre en el build).

- **Políticas:** `encabezado` y `acuse` → la primera que dispara. **Datos** (de la prenda y de la tienda) → se
  **colectan**: varias preguntas en un mensaje, varias respuestas (`MAX_DATOS` = 3). `EXCLUYE` = una regla cubre a otra;
  `SOLO` = solo valen sin otro dato; un dato **extra** solo entra si el texto lo pide explícitamente (`EXPLICITO`), y lo que
  la clienta niega («no me digas talla») no se le dice. `RESPUESTAS_COLECTAR=0` vuelve a «la primera gana» en todo.
- **Cómo se decidió (medido, no a ojo):** el refactor dio la MISMA salida en los 613 mensajes de las 100 conversaciones
  web (0 diferencias). La traza de colisiones mostró ~20 respuestas que perdían una pregunta; con «colectar» cambian 17
  respuestas, revisadas a mano (precio + talla, tela + envío, talla + envío, precio + color, precio + descuento).
- **La traza** viaja en cada respuesta: `reglas_codigo` = reglas elegidas y las que también disparaban (`colisiones`).

| Grupo | Regla | Dispara cuando | Efecto |
|---|---|---|---|
| encabezado (primera) | `no_hay` | pidió algo que no tenemos (color, prenda, talla) |  |
| encabezado (primera) | `es_bot` | pregunta si es un bot |  |
| encabezado (primera) | `fuera_de_giro` | pregunta general ajena a la tienda | responde solo esto |
| encabezado (primera) | `cuenta` | pide una cuenta («cuánto es 25 x 4») | responde solo esto |
| encabezado (primera) | `una_opcion` | el método de venta eligió UNA prenda para recomendar |  |
| encabezado (primera) | `vitrina` | hay fotos y pidió ver opciones, el catálogo o está describiendo una prenda |  |
| encabezado (primera) | `sin_mas_opciones` | pidió más opciones y no quedan |  |
| encabezado (primera) | `foto` | va una foto sugerida |  |
| encabezado (primera) | `tenemos_categoria` | pregunta por un tipo de prenda sin una en foco («¿tienen blusas?») |  |
| encabezado (primera) | `tenemos_prenda` | nombra la prenda en foco sin pedir otra cosa (y no regatea) |  |
| dato_prenda (colectar) | `tela` | pregunta la tela |  |
| dato_prenda (colectar) | `envio_no_es_precio` | habla de envío: «¿cuánto sale el envío?» no pregunta el precio de la prenda |  |
| dato_prenda (colectar) | `precio` | pregunta el precio |  |
| dato_prenda (colectar) | `talla_dicha` | dijo o preguntó una talla concreta |  |
| dato_prenda (colectar) | `tallas` | pregunta tallas o stock |  |
| dato_prenda (colectar) | `color` | pregunta el color o por otra variante |  |
| dato_prenda (colectar) | `elogio` | elogia la prenda y no hay nada más que decir |  |
| dato_prenda (colectar) | `precio_al_nombrar` | la nombró sin preguntar nada: el precio abre la charla |  |
| dato_prenda (colectar) | `detalle` | pregunta algo de la prenda que no es precio, talla, tela ni color |  |
| dato_tienda (colectar) | `descuento` | pide descuento o rebaja (política: no hay para nadie) |  |
| dato_tienda (colectar) | `envio` | pregunta por el envío |  |
| dato_tienda (colectar) | `showroom` | pregunta dónde están o el horario |  |
| dato_tienda (colectar) | `pago` | pregunta cómo pagar sin pedido confirmado |  |
| dato_tienda (colectar) | `duda` | duda o pospone (sin regatear ni dar datos) | sin la pregunta siguiente |
| dato_tienda (colectar) | `despedida` | se despide | sin la pregunta siguiente |
| dato_tienda (colectar) | `groseria` | mensaje grosero (no una duda ni un pedido apurado) |  |
| acuse (primera) | `acuse` | respondió la pregunta pendiente y no hay otro texto |  |


---

## Comprensión entrenada (`app/comprension.py`, 07-10-2026)

Qué PIDE el mensaje, multi-etiqueta, con el e5 del agente congelado + una regresión logística por pedido (la
estandarización plegada en los pesos). Se entrena en el build (`python -m app.comprension entrenar`, ~1.000 frases de
`data/comprension_entrenamiento.jsonl`); el umbral de cada pedido sale de validación cruzada (F0,5, precisión ≥ 80 %).

**Medición** (`python3 -m app.medir_comprension evaluar --sistema reglas|clf|ambos`, sobre `data/comprension_prueba.jsonl`:
418 frases que ni las reglas ni el modelo vieron):

| Sistema | Detecta | Falsas alarmas |
|---|---|---|
| Reglas solas | 66 % | 22 % |
| Clasificador solo (todas las etiquetas) | 60 % | 5 % |
| Reglas + clasificador (todas) | 82 % | 23 % |
| **Reglas + clasificador (las ACTIVAS, lo que usa el bot)** | **78 %** | 23 % |

**ACTIVAS** = «ya pagué», despedida, tela, elogio, cita, color, más barato. Fuera, decidido con la repetición de las 613
frases de las 100 conversaciones: `no_mostrar` (quitaba las fotos pedidas), `otras_opciones` y `catalogo` (mandaban fotos y
se perdía el precio), `ubicacion` (metía el showroom en «quiero ver conjuntos»), y las que las reglas ya resuelven (talla,
envío, pago). Precio y rebaja: el modelo se abstiene. La despedida no cuenta si el mensaje es un saludo.

En el bot: `solicitud_de(req)` (reglas O modelo) alimenta `SolicitudCliente`, los hechos de la tabla de respuestas, la tela y
la cita. `COMPRENSION=activo` (defecto) · `sombra` (solo traza) · `0` (apagado). Traza: `comprension` en cada respuesta.

## Ficha técnica de cada prenda (`app/ficha_producto.py`, 07-10-2026)

El catálogo solo trae el párrafo de Diners, escrito para vender. «¿Tiene mangas?», «¿es largo?» o «¿cómo es la espalda?»
dependían de que el párrafo lo dijera: si no, el LLM adivinaba (y Jev le quitaba la frase) o se derivaba a una asesora algo
que se ve en la foto. `seed/fichas_producto.json` tiene una ficha por prenda real (V21–V42) con silueta, largo, escote,
mangas, cintura, espalda, cierre, tela, forro, transparencias, estampado, piezas, detalles, «cómo queda» y cuidados.

- **Fuente de cada dato** (`fuente`): `diners` (texto de la tienda; lámina de materiales en V42), `foto` (análisis visual de
  TODAS las fotos de la prenda: solo lo visible) o `ambas`. La tela nunca sale de una foto y «cómo queda» solo de la tienda
  (`prueba_ficha_producto` lo exige). Lo que no se sabe va a `pendiente_tienda` (las medidas por talla, siempre).
- **El texto de Diners se contradice con las fotos** en varias prendas (Kabanova «falda lisa» y es floreada; Irla «palo rosa»
  y es negro…). Quedan en `discrepancias`; para el LLM manda la ficha («si choca con la descripción, vale esta»).
- **En el bot:** la ficha se suma a lo que lee el LLM y a lo que verifica Jev (`venta.extras_texto`). `pedidos()` detecta el
  atributo preguntado; con DeepSeek va una nota con el dato (o «no figura → asesora»), y si el texto no lo dice lo antepone
  el código (`dicho()`). Sin LLM, la regla `atributo` de la tabla. «¿La falda es lisa?» / «¿viene con la blusa?» preguntan
  por una pieza de la prenda que mira, no buscan faldas ni blusas (`memoria.RE_PIEZA_DE_FOCO`).
- `FICHAS_PRODUCTO=0` lo apaga. Prenda nueva → su ficha (mismo esquema), o el bot solo tendrá el párrafo.
