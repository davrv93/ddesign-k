# Agente conversacional (servicio `agente`)

Hace que el bot de WhatsApp converse en lugar de sólo mostrar el menú. El bot Go le pasa
todo el **texto libre** (lo que no es un número del menú, un código ni una talla). Si el
agente no responde, el bot vuelve a Gemini como antes.

```
mensaje ─► embedding local ─► clasificador de intención ─┬─► acción del bot (catálogo, foto, pedido, asesora, código)
           (jina v2 es, ONNX)  clasificador de prenda     └─► RAG (fichas + ejemplos) ─► DeepSeek (OpenRouter) ─► respuesta
```

| Pieza | Qué es |
|---|---|
| Embeddings | `jinaai/jina-embeddings-v2-base-es` (open source, Apache-2.0, español/inglés), servido con fastembed + onnxruntime en CPU. Se hornea en la imagen. |
| Clasificador | Regresión logística sobre los embeddings: 20 intenciones y 4 categorías de prenda. |
| RAG | 100 fichas de `caracteristicas_y_tallas.txt` + el catálogo real de la tienda (`/api/public/catalog`, se refresca cada 5 min). |
| Few-shot | Los 4 ejemplos más parecidos de los datasets se pasan al LLM como guía de tono. |
| LLM | `deepseek/deepseek-v4-flash` por OpenRouter, con `deepseek/deepseek-chat-v3.1` de respaldo. Sin clave o sin red, devuelve la respuesta de referencia del ejemplo más parecido. |

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

**Sucursales**: `seed/sucursales.json` trae 3 sucursales con stock por talla para los 120 códigos.
**Son datos de demostración** (generados con semilla fija): reemplázalos por el inventario real de cada tienda.
`GET /sucursales` las lista. El stock de sucursal también lo usa el chat de texto («¿hay en talla M?»).

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

## Memoria

jina base ocupa **~940 MiB** en reposo y, con el modelo de imagen, el agente llega a **~1,52 GiB**; el límite
del contenedor es 1800m. Para apagar la búsqueda por foto y ahorrar ~580 MiB: `IMAGE_SEARCH=0`. En el EC2 de 2 GiB
eso no cabe junto al resto. Para allí, construye con el modelo ligero:

```bash
EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 docker compose build agente
```

Cambiar de modelo obliga a re-entrenar, cosa que la construcción ya hace sola. Las métricas de arriba son
las de jina; con MiniLM hay que volver a medirlas.

No ejecutes `python -m app.entrenar` ni cargues el modelo dentro del contenedor en marcha: sería una
segunda copia y el contenedor muere por falta de memoria. `app.evaluar` usa la API HTTP para no hacerlo.
