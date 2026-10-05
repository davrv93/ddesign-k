# Catálogos semánticos del agente (Baruka)

Varios catálogos de intenciones **separados**, no un clasificador gigante. Un catálogo **clasifica** una frase de WhatsApp;
no contesta ni inventa datos de la prenda. Especificación: `data/catalogos/CATALOGOS_PROPUESTOS.md` y
`data/catalogos/faq_vestidos.txt`. Todo el contenido de entrenamiento y de prueba es **sintético** (no hay datos de clientas
reales). Nada de esto está conectado todavía a `main.py`: la integración queda pendiente.

## Qué hay

| Archivo | Para qué |
|---|---|
| `catalogos.py` | Carga y valida catálogos (YAML + el `faq_vestidos.txt`): sin duplicados (ni salvo acentos/signos), sin vacíos, mínimo de ejemplos por intención (20), `<PRODUCTO>`→«este vestido», `<ALTURA>`→«1,60». |
| `clasificador.py` | `Clasificador`: embeddings del agente (e5-small, prefijo `query:`, normalizados) + kNN/centroides + **abstención** por score, margen y clase «ninguno». Embedder inyectable. |
| `referencias.py` | `resolver_referencia(texto, candidatos)`: resolvedor **determinista** («ese», «el otro», «el segundo», «el guinda», «el de la foto»). Sin modelo ni dependencias. |
| `enrutador.py` | Opcional: a qué catálogo pertenece la frase (kNN sobre la unión de ejemplos). Ver «Limitaciones». |
| `evaluar.py` | Leave-one-out, prueba aparte, confusiones, curva cobertura/precisión, ruido, calibración de umbrales, verificación de solapes. |
| `prueba_catalogos.py` | 276 casos estructurales sin modelo ni red. |
| `../../data/catalogos/*.yaml` | Un YAML por catálogo, su `<catalogo>_prueba.yaml` (NO entra al entrenamiento), `ruido_entrenamiento.yaml` (clase «ninguno»), `ruido_prueba.yaml` (mide falsos positivos), `umbrales.json`, `informe_evaluacion.json`. |

Catálogos (7): `preguntas_producto` (25 intenciones A–Y del faq), `ocasion` (15), `estilo` (16), `objeciones` (las 11 del documento),
`rechazo` (9), `senales_compra` (11, con `strength` y `stage`), `referencias_contextuales` (10).

## Cómo se usa

```python
from app.catalogos import cargar_clasificador, resolver_referencia
from app.modelo import Embedder                      # el mismo embedder del agente (self.emb en main.py)

emb = Embedder()
pp = cargar_clasificador("preguntas_producto", emb)  # lee data/catalogos/preguntas_producto.yaml + faq_vestidos.txt + umbrales.json
r = pp.clasificar("se estira?")
# {'intent': 'elasticidad', 'score': 0.96, 'margen': 0.046, 'alternativas': [('elasticidad', .96), ('textura', .91), ...],
#  'mejor': 'elasticidad', 'catalogo': 'preguntas_producto', 'accion': 'responder_atributo_producto'}
if r["intent"]:                                      # None = se abstiene (r["motivo_abstencion"]: score_bajo | margen_bajo | fuera_de_giro)
    hechos = pp.catalogo.intenciones[r["intent"]].hechos_requeridos   # ['elasticidad']: qué mirar en la ficha

sc = cargar_clasificador("senales_compra", emb).clasificar("ya yapee")
# {'intent': 'pago_realizado', ..., 'strength': 0.99, 'stage': 'CHECKOUT', 'etapa_bot': 'venta_confirmada'}

ref = resolver_referencia("el otro", [
    {"codigo": "V35", "nombre": "Vestido Irla", "color": "negro"},          # lista CRONOLÓGICA: 0 = el primero mostrado,
    {"codigo": "V42", "nombre": "Vestido Gala", "color": "azul marino"}])    # el último = el más reciente
ref.tipo        # "candidato" | "ambiguo" | "ninguno"
ref.candidato   # {"codigo": "V35", ...}   (si ambiguo: ref.empatados; ref.motivo dice por qué)
```

Variables útiles: `CATALOGOS_DIR` (por defecto `AGENTE_DATA_DIR/catalogos`), `CATALOGOS_CACHE_DIR` (guarda en disco los embeddings de los
ejemplos; sin ella se recalculan al arrancar: 1–2 s por catálogo). Medido con el modelo real: **~3 ms por mensaje**.

### Resolvedor de referencias

- Contrato: `candidatos` = lista de dicts `codigo`, `nombre`, `color` (opcionales `foto`/`origen:"foto_clienta"`, `precio`), en orden cronológico.
- Pistas: ordinales (primero, segundo… último, penúltimo, «el 2», «la opción dos»), posición («el de arriba» = el primero de la lista, «el de abajo» = el
  último, «el del medio»), «el anterior / el de antes / el de hace rato» (= el penúltimo), color con equivalencias
  (guinda ≈ borgoña ≈ vino ≈ burdeos ≈ granate ≈ «rojo oscuro»; «azul noche» ≈ «azul marino» ≈ navy; «color piel» → nude), deícticos («ese/este/ese mismo» =
  el último; «el otro» = el que NO es el último; «ese no» = el otro), «el de la foto» (la prenda con `foto`/`origen:"foto_clienta"`), código («el V42», «el 42») y
  nombre («el Irla»).
- Color: coincidir el tono (o su equivalente) da `nivel="alta"`; coincidir solo la familia («el rojo» y la prenda es vino) da `nivel="media"`; «el azul» con
  azul marino y azul rey da `ambiguo`. Se combina con posición: «el segundo rojo» = el segundo de los rojos.
- No resuelve: plurales («los dos», «ambos» → `ninguno`), atributos («el largo», «el de mangas» → `ninguno`, motivo «atributo»; solo «el más barato/caro» si
  los candidatos traen `precio`), prendas que no están en la lista («el V99» → `ninguno`).

## Cómo agregar un catálogo

1. Crear `data/catalogos/<nombre>.yaml`:
   ```yaml
   catalogo: <nombre>
   version: 1
   minimo_ejemplos: 20
   intenciones:
     mi_intencion:
       descripcion: "..."
       slots: {producto: opcional}
       hechos_requeridos: [elasticidad]     # qué hay que mirar en la ficha (solo metadato)
       accion: responder_algo
       stage: PURCHASE_INTENT               # opcionales (señales de compra); cualquier otra clave va a `extra`
       strength: 0.9
       ejemplos: ["frase uno", "frase dos", ...]   # ≥ 20, sin duplicados, SIN la frase prohibida «¿Es para alguna ocasión especial?»
   ```
   (Si parte de un `.txt` en el formato del faq, añadir `fuente_txt: archivo.txt`: se fusiona por nombre de intención.)
2. Escribir **antes de medir** `data/catalogos/<nombre>_prueba.yaml` (≥ 4 frases por intención, distintas del entrenamiento, formato `intenciones: {x: {ejemplos: [...]}}`).
3. `python -m app.catalogos.evaluar --solapes` (la prueba no repite ni se parece demasiado al entrenamiento), y luego
   `python -m app.catalogos.evaluar <nombre> --calibrar --guardar` (escribe `umbrales.json` e `informe_evaluacion.json`).
4. Si mide mal, **mejorar los ejemplos de entrenamiento** y volver a medir; no tocar la prueba para que salga mejor.

Correr con el modelo real (imagen local con el e5 en `/models`):
```
docker run --rm -e HF_HUB_OFFLINE=1 -v $PWD/agente/app:/app/app:ro -v $PWD/agente/data/catalogos:/app/data/catalogos:ro \
  kddesign/agente:v2activo python -m app.catalogos.evaluar          # (:rw y --guardar para escribir umbrales/informe)
docker run --rm ... python -m app.catalogos.prueba_catalogos       # 276 casos, sin modelo
```

## Cifras medidas (modelo real e5-small, 05-10-2026)

Método por defecto `hibrido` = ½ kNN (top-3) + ½ centroide; elegido por leave-one-out (gana o empata en los 7). La **prueba aparte** (≥ 5 frases por
intención, redactadas aparte y sin repetir el entrenamiento) es la cifra que importa; el leave-one-out es más duro porque incluye los ejemplos
ambiguos del entrenamiento y es optimista en otro sentido (hay paráfrasis casi iguales).

| Catálogo | Intenc. | Ejemplos (por intención) | LOO top-1 / top-3 | Prueba aparte (n) top-1 / top-3 | Umbral score / margen | Cobertura / precisión (misma prueba) | Calibración cruzada cob./prec./FP | FP «fuera de giro» |
|---|---|---|---|---|---|---|---|---|
| preguntas_producto | 25 | 691 (24–32; 250 originales del faq) | 75,8 % / 92,8 % | 128: 84,4 % / 94,5 % | 0,8847 / 0,0056 | 79,7 % / 95,1 % | 84,2 / 91,2 / 5,1 % | 1,6 % |
| ocasion | 15 | 331 (22–23) | 88,5 % / 96,7 % | 75: 97,3 % / 100 % | 0,8887 / 0,0 | 100 % / 97,3 % | 94,4 / 97,1 / 0 % | 0,0 % |
| estilo | 16 | 460 (26–30) | 68,3 % / 89,1 % | 80: 91,2 % / 96,3 % | 0,8853 / 0,0002 | 93,8 % / 96,0 % | 89,6 / 95,3 / 8,5 % | 8,2 % |
| objeciones | 11 | 260 (22–28) | 86,2 % / 96,5 % | 55: 94,5 % / 98,2 % | 0,905 / 0,0 | 96,4 % / 96,2 % | 91,7 / 95,9 / 3,2 % | 4,9 % |
| rechazo | 9 | 246 (22–30) | 72,4 % / 97,6 % | 45: 86,7 % / 97,8 % | 0,9015 / 0,0085 | 73,3 % / 97,0 % | 82,4 / 91,5 / 0 % | 0,0 % |
| senales_compra | 11 | 278 (22–30) | 88,8 % / 99,3 % | 55: 94,5 % / 100 % | 0,8934 / 0,0032 | 94,6 % / 96,2 % | 94,7 / 96,8 / 11,7 % | 6,6 % |
| referencias_contextuales | 10 | 249 (22–30) | 86,8 % / 98,0 % | 50: 84,0 % / 98,0 % | 0,8702 / 0,0021 | 88,0 % / 95,5 % | 87,5 / 92,7 / 6,7 % | 8,2 % |

- Los umbrales se calibran con la prueba aparte + 61 frases de ruido «fuera de giro» (`ruido_prueba.yaml`): maximizar cobertura con precisión ≥ 95 % y
  falsos positivos ≤ 10 %. Por eso la columna «misma prueba» es **en muestra**; la «calibración cruzada» (se calibra con una mitad y se mide en la otra) es la
  estimación honesta: la precisión baja a 91–97 % (queda por debajo del 95 % en preguntas_producto, rechazo y referencias).
- Los scores de e5 vienen muy comprimidos (0,85–0,97): los umbrales son estrechos y **cambiar de embedder obliga a recalibrar** (`--calibrar --guardar`).
- Ruido «otros catálogos no construidos» (saludos, horarios, boleta, direcciones; n = 40): FP entre 2,5 % y 22,5 % (senales_compra 22,5 %, objeciones 17,5 %,
  preguntas_producto 15 %). Es el punto débil: la charla social corta se parece a «ok / ya / me lo llevo».
- Mejora entre la primera medición y la actual (ampliando ejemplos débiles + clase «ninguno»): LOO preguntas_producto 74,2→75,8 %, estilo 62,8→68,3 %,
  senales 86,0→88,8 %; prueba aparte preguntas_producto 81,2→84,4 %, rechazo 82,2→86,7 %, referencias 88,0→84,0 % (−2 frases de 50). La clase «ninguno» (ruido de entrenamiento) baja
  los falsos positivos de ruido; sin ella, con umbrales calibrados, preguntas_producto pasaba de 1,6 % a 8,2 % y ocasión de 0 % a 3,3 %.
- Enrutador entre catálogos (kNN sobre la unión): 91,6 % de las frases de prueba al catálogo correcto (referencias 84 %, objeciones 85,5 %, rechazo 88,9 %).

## Limitaciones (leer antes de integrar)

1. **La prueba la escribí yo, en el mismo estilo que el entrenamiento**: es optimista. Sin chats reales no hay forma de medir la distribución de verdad. Cuando
   existan mensajes reales anonimizados, usarlos como prueba y recalibrar.
2. **Los catálogos no saben lo que no es suyo.** Solos, responden fuera de su dominio («¿viste el partido?» → preguntas_producto: `transparencia`; «uy q caro» en
   senales_compra → `interes_gusto`; «no me gusta» en objeciones → `no_estoy_segura`). Los scores **no son comparables entre catálogos** (comparar la «holgura»
   sobre el umbral acierta catálogo e intención solo 51–95 % según el catálogo). Decidir antes a qué catálogo preguntar: por la etapa/pendiente de la conversación o con `Enrutador`.
   Además hay solapes legítimos por diseño (objeciones/rechazo «muy corto», estilo/ocasión).
3. **Antónimos**: e5 casi no separa «muy largo» de «muy corto» (15 confusiones en LOO) ni «no me convence» de «no me gusta» (14); `demasiado_llamativo`↔`muy_serio` (12);
   `discreto`↔`sobrio`, `elegante`↔`sexy`, `boho`↔`romantico` (estilos vecinos). Para largo/corto conviene una regla léxica o Jev encima de la salida.
4. **`pregunta_pago` vs `pago_realizado`** se confunden (en la prueba, «dame el yape» salió `pago_realizado`): nunca confirmar un pago solo por esta señal.
5. **`strength` es una propuesta ordinal** (0,05 mirando … 0,99 pagó), no una probabilidad medida. `stage` usa los nombres del catálogo 20 del documento; `etapa_bot`
   los de `etapas.py`. Decidir etapas sigue siendo cosa de las reglas.
6. En `preguntas_producto` muchas «confusiones» son defendibles (¿Es caluroso? → clima o comodidad; calidad_durabilidad es un cajón de sastre: 47 % de acierto en LOO, la peor intención).
   Como la respuesta sale de la ficha, mirar también las `alternativas` (top-3 = 94,5 %) y `hechos_requeridos`.
7. Resolvedor: asume el orden cronológico y «arriba = primero, abajo = último»; no invierte la negación de posiciones («no me gusta el de abajo» devuelve el de abajo);
   los diminutivos de color solo están para los más comunes (negrito, rojito…); un nombre de prenda con palabra común («Gala») puede disparar sin querer si el texto no es ya una referencia.
   Medido: 276 casos de la prueba estructural (incluidas 31 frases nuevas escritas después de ajustar), pero con candidatos de juguete, no con conversaciones reales.
8. PyYAML no está en `agente/requirements.txt` (la imagen lo trae por una dependencia de `huggingface-hub`/`fastembed`): fijarlo explícitamente al integrar.
9. Una frase de 1–2 palabras («ese», «el rojo») es ruido para los embeddings: para referencias, usar el resolvedor; el catálogo solo sirve para decidir que *es* una referencia.
