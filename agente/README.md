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

jina base ocupa **~940 MiB** en reposo, así que el límite del contenedor es 1200m. En el EC2 de 2 GiB
eso no cabe junto al resto. Para allí, construye con el modelo ligero:

```bash
EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 docker compose build agente
```

Cambiar de modelo obliga a re-entrenar, cosa que la construcción ya hace sola. Las métricas de arriba son
las de jina; con MiniLM hay que volver a medirlas.

No ejecutes `python -m app.entrenar` ni cargues el modelo dentro del contenedor en marcha: sería una
segunda copia y el contenedor muere por falta de memoria. `app.evaluar` usa la API HTTP para no hacerlo.
