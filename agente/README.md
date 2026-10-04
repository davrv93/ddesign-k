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
| Clasificador comercial | `data/comercial.csv` (496 frases, 20 intenciones), `app/entrenar.py` | Qué quiere la clienta en términos de venta: `interesado`, `intencion_compra`, `confirmacion_compra`, `objecion`, `objecion_precio`, `consulta_precio`, `consulta_material`… Se mide con `data/prueba_comercial.csv` (68 frases que no entran al entrenamiento): **97,1 %** |
| Máquina de etapas | `app/etapas.py` | Reglas explícitas, sin LLM. Devuelve la etapa nueva y el motivo |
| Guion por etapa | `app/venta.py` | Prompt de sistema, guía de cada etapa, totales ya calculados, datos de pago |
| Pruebas | `app/prueba_etapas.py` | 26 casos (los 7 del encargo y el primer contacto incluidos). Se ejecutan al construir la imagen: si falla uno, no hay imagen |

Reglas de `etapas.py`:

- **Interés no es compra.** «Sí, me interesa» o preguntar el precio lleva a seguimiento, nunca a cierre.
- Solo una intención clara de compra («quiero comprarlo», «resérvamelo», «¿cómo pago?») lleva a cierre.
- **El «sí» depende de lo último que preguntó el bot**: a «¿Confirmamos tu pedido?» es una confirmación; a
  cualquier otra pregunta es interés. Una confirmación solo vale en cierre.
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

**Vestido del anuncio** (`PRODUCTO_DEMO=V42`): la clienta llega desde un anuncio y dice «este vestido» sin
nombrarlo. Con la variable puesta, ese vestido es la prenda en foco, se enseña una sola vez y no se mezclan
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

API: `POST /chat {mensaje, historial:[{rol, texto}], cliente, estado}` devuelve `{intencion, confianza,
accion, codigo, respuesta, fichas, ejemplos}`. `POST /clasificar {texto}` devuelve sólo la clasificación.

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
