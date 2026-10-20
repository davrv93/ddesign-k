# Contrato: prospectos, bienvenida, seguimiento, números de WhatsApp y campañas

Plan: `PjgFactSalud_completo/PLAN_TRABAJO_BARUKA_REQUERIMIENTOS_LUIS.md`, de octubre de 2026. Todo va por empresa, con la
ruta `/<slug>/api/…` y `s.st(r)`. «Admin» quiere decir que va con `a(...)`; lo demás es para todo el equipo.

## 1. Ajustes nuevos (`GET` y `PUT /api/settings`, siempre texto)

| Clave | Defecto | Qué hace |
|---|---|---|
| `bienvenida_anuncio` | `"false"` | Quien llega por un anuncio recibe presentación, foto del modelo, garantía y «Ver colores / Ver tallas» |
| `precio_al_llegar_por_anuncio` | `"false"` | Esa foto lleva el precio aunque `precios_solo_si_pregunta` esté encendido |
| `prospectos_habilitado` | `"false"` | Con un evento lejano el bot pregunta «¿ahora o coordinamos en <mes>?» |
| `prospecto_desde_dias` | `"45"` | Desde cuántos días el evento cuenta como lejano |
| `prospecto_aviso_dias` | `"30"` | Cuántos días antes del evento se le vuelve a escribir (vencimiento de la tarea) |
| `preguntar_presupuesto` | `"false"` | El bot pregunta un rango de presupuesto después de conocer la ocasión |
| `seguimiento_horario` | `"08:00-21:00"` | Horario de Lima en el que pueden salir pasos de seguimiento |

La garantía no es un ajuste: es una **fuente de tipo `politica`** cuyo título o etiquetas contienen «garantía».

## 2. Etapa nueva del embudo

`mas_adelante`, con la etiqueta «Más adelante». Va entre `venta_confirmada` y `perdida` en `store.Etapas`. La pone el bot
cuando la clienta elige coordinar más adelante; se crea además una tarea con vencimiento en la fecha del aviso.

## 3. Contenido de seguimiento por modelo

`clave` ∈ `beneficio`, `testimonio`, `tip`. Hay como máximo uno por clave y producto. `origen` ∈ `web`, `manual`.

- `GET /api/productos/{id}/contenido` → `[{clave, texto, media_url, origen, actualizado_en}]`
- `PUT /api/productos/{id}/contenido/{clave}` (admin), con el cuerpo `{texto, media_url}`. Queda con origen `manual`.
  Si `texto` y `media_url` llegan vacíos, se borra.
- `POST /api/contenido/importar` (admin), con el cuerpo `[{codigo, clave, texto, media_url}]` → `{importados, omitidos}`.
  Nunca pisa lo `manual`.
- `GET /api/contenido/resumen` → `[{product_id, codigo, nombre, beneficio, testimonio, tip}]`, con booleanos que dicen
  si hay contenido.

## 4. Secuencias de seguimiento

```json
{"id": 1, "nombre": "Baruka · vestidos", "activa": false, "alcance": "todas", "alcance_valor": "",
 "pasos": [{"espera_min": 60, "tipo": "emoji", "texto": "🥰", "promo_id": 0, "plantilla": ""},
           {"espera_min": 300, "tipo": "beneficio", "texto": "", "promo_id": 0, "plantilla": ""},
           {"espera_min": 4320, "tipo": "oferta", "texto": "", "promo_id": 12, "plantilla": "oferta_dia3"}],
 "fuera_de_ventana": "saltar", "al_responder": "continuar", "marcar_perdida": false,
 "creada_en": "…", "cifras": {"en_curso": 3, "enviados_hoy": 5, "respondieron": 2, "terminados": 1}}
```

- **`alcance`:**
  - `todas`;
  - `categoria`, con `alcance_valor` = una categoría del producto, por ejemplo «vestido»;
  - `productos`, con `alcance_valor` = códigos separados por coma.
  Se elige la más específica: productos, luego categoría, luego todas. Solo cuentan las activas.
- **`tipo` de cada paso:**
  - `emoji` y `texto`: envían `texto`;
  - `beneficio`, `testimonio` y `tip`: usan el contenido del modelo en foco, con `texto` como introducción opcional; sin
    contenido, el paso se salta;
  - `oferta`: usa la promo `promo_id`, que debe estar vigente y cubrir el modelo; si no, se salta.
- **`fuera_de_ventana`:** qué hacer si pasaron más de 24 h desde el último mensaje de la clienta.
  - `saltar`: no se envía;
  - `enviar`: se envía igual; solo vale para el canal evolution;
  - `plantilla`: solo para la API oficial; usa `plantilla` del paso;
  - `numero_secundario`: sale por un número de uso `salida`.
- **`al_responder`:** qué pasa cuando la clienta contesta.
  - `continuar`: sigue desde el siguiente paso cuando vuelva a callarse;
  - `reiniciar`: vuelve al paso 1;
  - `detener`: no sigue.
- **Rutas:**
  - `GET /api/secuencias`;
  - `POST /api/secuencias` (admin);
  - `PUT /api/secuencias/{id}` (admin);
  - `DELETE /api/secuencias/{id}` (admin);
  - `POST /api/secuencias/{id}/activa` (admin), con el cuerpo `{activa: bool}`;
  - `GET /api/secuencias/{id}/vista-previa?producto=<codigo>` →
    `[{paso, espera_min, tipo, texto, media_url, omitido, motivo}]`.
- **Errores:** las validaciones dan 400 con `{"error": "…"}`. Los casos son:
  - nombre vacío;
  - sin pasos o con más de 12;
  - `espera_min` < 1 o > 20160, que son 14 días;
  - tipo desconocido;
  - `oferta` sin `promo_id`.

**Por conversación:**

- `GET /api/conversations/{id}/seguimiento` → `{secuencia_id, nombre, paso, total, proximo_en, estado}` o `null`.
  `estado` ∈ `activo`, `pausado`, `terminado`, `detenido`. `paso` es el número del próximo paso (1…total).
- `POST /api/conversations/{id}/seguimiento/detener` (todo el equipo) → `{"ok": true}`.

Avisos SSE: tema `seguimiento` cuando cambia una secuencia o sale un paso.

## 4b. Flujos visuales (editor de flujos, 20-10-2026)

Una secuencia puede llevar un **grafo** (`grafo`): se edita en el lienzo del panel (zoom, mover, conectar, deshacer) y el
motor lo recorre. Sin `grafo`, la secuencia es la lista de pasos de §4 y nada cambia para ella. Al abrir una lista vieja
en el editor se convierte (inicio → esperar → mensaje → … → fin) y, al guardarla, queda como flujo con el mismo
comportamiento.

```json
{"nombre": "Baruka · vestidos", "alcance": "todas", "pasos": [],
 "grafo": {
   "nodos": [{"id": "inicio", "tipo": "inicio", "x": 40, "y": 160},
             {"id": "e1", "tipo": "esperar", "minutos": 60, "x": 320, "y": 160},
             {"id": "m1", "tipo": "mensaje", "mensaje": "emoji", "texto": "🥰", "media_url": "", "promo_id": 0, "plantilla": "", "x": 600, "y": 160},
             {"id": "c1", "tipo": "condicion", "condicion": "respondio", "x": 880, "y": 160},
             {"id": "t1", "tipo": "tarea", "titulo": "Atender a {nombre}", "x": 1160, "y": 60},
             {"id": "f", "tipo": "fin", "x": 1440, "y": 160}],
   "aristas": [{"desde": "inicio", "salida": "siguiente", "hasta": "e1"}, {"desde": "e1", "salida": "siguiente", "hasta": "m1"},
               {"desde": "m1", "salida": "siguiente", "hasta": "c1"}, {"desde": "c1", "salida": "si", "hasta": "t1"},
               {"desde": "c1", "salida": "no", "hasta": "f"}, {"desde": "t1", "salida": "siguiente", "hasta": "f"}],
   "vista": {"x": 40, "y": 40, "zoom": 0.8}}}
```

- **Nodos** (`tipo`):
  - `inicio`: uno solo. Arranca cuando la clienta deja de responder con un modelo en foco (el alcance de la secuencia).
  - `esperar`: `minutos`, de 1 a 20160.
  - `mensaje`: `mensaje` es uno de los tipos de paso de §4 (`emoji`, `texto`, `beneficio`, `testimonio`, `tip`, `oferta`),
    con `texto`, `media_url`, `promo_id` y `plantilla` como en un paso.
  - `condicion`: `condicion` ∈ `respondio` (escribió después del último mensaje del flujo), `temperatura` (`valor`:
    caliente, tibia o fria), `categoria` (`valor`: la categoría del modelo en foco) y `tiene_contenido` (`valor`:
    beneficio, testimonio o tip). Dos salidas: `si` y `no`.
  - `tarea`: `titulo` (acepta `{nombre}` y `{modelo}`); vence al crearse.
  - `etapa`: `etapa` del embudo.
  - `ir_a`: `destino` es el id de otra secuencia **con grafo**; sigue desde su inicio. Si está apagada, se salta.
  - `fin`.
  - `nota` en cualquier nodo: texto para el equipo, no se envía.
- **Aristas:** de la salida `salida` (`siguiente`, o `si` / `no` en la condición) del nodo `desde` al nodo `hasta`. Cada
  salida va a un solo nodo; una salida sin conectar termina el flujo.
- **Validación (400):**
  - un solo inicio, conectado;
  - al menos un mensaje (hasta 40) y hasta 80 nodos;
  - las conexiones apuntan a nodos que existen, nunca al inicio ni al mismo nodo;
  - **ningún ciclo sin espera** (un ciclo con espera sí vale: un recordatorio que se repite).
- **`pasos`:** con grafo los deriva el backend de los mensajes (anchura primero, con la espera acumulada). Los usan el
  tablero, la vista previa y el resumen; el motor recorre el grafo.
- **Motor:** el estado de cada conversación es el nodo que toca (`seguimiento_en_curso.nodo`) y cuándo. Recorre nodos hasta
  una espera, un fin o 30 nodos por minuto. Los mensajes salen con las reglas de §4: horario, tope diario, ventana de 24 h
  y número de salida. Si ella escribe, el flujo se pausa; según `al_responder`, sigue en el mismo nodo (una espera
  vuelve a contar desde ahí), vuelve al inicio o se detiene.

**Rutas:**

- `POST /api/flujos/simular` (todo el equipo), con el cuerpo `{grafo, nombre, producto, cliente, responde, temperatura}` →
  `{pasos: [{nodo, tipo, en_min, mensaje, texto, media_url, omitido, motivo, detalle, flujo}], fin}`. Recorre el flujo
  sin enviar ni guardar, con lo que hay en el lienzo. `responde` decide «¿respondió?» y `temperatura` la condición de
  temperatura.
- `POST /api/secuencias/{id}/probar` (admin), con el cuerpo `{telefono, producto, escala_min}` →
  `{conversation_id, telefono, escala_min}`. **Manda WhatsApp de verdad** al número que escribe la persona, con el flujo
  GUARDADO:
  - cada espera dura `escala_min` minutos (de 0 a 60; por defecto 1; 0 = las reales);
  - corre sin horario, tope ni ventana y aunque la secuencia esté apagada;
  - los envíos se anotan con resultado `prueba` y no cuentan para el tope ni para las cifras;
  - la tarea lleva «🧪 Prueba:» y el nodo de etapa no cambia la etapa;
  - errores: 400 si no es un flujo, el teléfono no sirve o la escala está fuera de rango; 409 si la empresa no tiene
    WhatsApp; 429 tras 10 pruebas en una hora.
- `POST /api/secuencias/{id}/por-modelo` (admin), con el cuerpo `{codigos}` → `{creadas: [codigo], omitidas: {codigo:
  motivo}}`. **Un flujo por modelo**, como los Salesbots por modelo de Kommo: copia el flujo `{id}` (debe tener grafo) a
  una secuencia nueva por cada código, con `alcance: "productos"`, `alcance_valor` = el código y nombre «<base> · <nombre
  del modelo>» (sin el «(migrado)» del base). Copia también `fuera_de_ventana`, `al_responder`, `marcar_perdida` y
  `tarea_al_responder`. Las copias nacen **apagadas** y se editan por separado. Sin `codigos`, todos los modelos activos
  (máximo 300). Omite el código que no existe y el modelo que ya tiene una secuencia propia: no pisa lo que alguien ya
  ajustó. Encendida, la copia gana sobre el flujo general solo para su modelo (prioridad de `SecuenciaPara`).
- `POST /api/secuencias/activar-lote` (admin), con el cuerpo `{ids, activa}` → `{cambiadas}`. Enciende o apaga varias a
  la vez (de 1 a 500 ids). Los ids de otra empresa no se tocan (no cuentan en `cambiadas`).

## 5. Números de WhatsApp

```json
{"id": 2, "nombre": "Avisos 1", "canal": "evolution", "numero": "51999888777", "uso": "salida", "tope_diario": 25,
 "enviados_hoy": 4, "conectado": true, "principal": false}
```

- **`canal`:** `evolution` (WhatsApp normal por QR) o `cloud` (API oficial de Meta; sus credenciales van en el `.env`).
- **`uso`:** `entrada`, `salida` o `ambos`.
- **El principal:** sale del `.env` (`id` 0, `principal: true`). No se borra; solo se le edita `tope_diario`.
- **Rutas:**
  - `GET /api/numeros`;
  - `POST /api/numeros` (admin), con el cuerpo `{nombre, canal, numero, uso, tope_diario}`;
  - `PUT /api/numeros/{id}` (admin);
  - `DELETE /api/numeros/{id}` (admin);
  - `GET /api/numeros/{id}/qr` (admin, solo evolution) → `{qr: "data:image/png;base64,…", conectado}`;
  - `POST /api/numeros/{id}/vincular` (admin), con el cuerpo `{telefono}` → `{codigo}`, el código para vincular con número.
- **Plantillas de la API oficial:** `GET /api/whatsapp-cloud/plantillas` → `[{name, language, status, category, body}]`.
  Sin la API oficial configurada → `[]`.

## 6. Campañas (de dónde llega cada conversación)

- `GET /api/campanas?desde=YYYY-MM-DD&hasta=YYYY-MM-DD` →
  `{"filas": [{clave, ad_id, titular, canal, conversaciones, calientes, pedidos, confirmados, monto}], "total": {…}}`.
  - Las fechas son de Lima y las dos son inclusivas.
  - `clave` es `ad_id` si existe y si no `titular`. «Sin anuncio» agrupa lo orgánico.
  - `monto` suma los pedidos confirmados, preparando, enviados y entregados.
- `GET /api/conversations/{id}/origen` → `{canal, ad_id, titular, ctwa_clid, llego_en}` o `null`.

## 7. Agente (campos nuevos de `/chat` y `/foto`)

- **Lo que manda el bot Go:** `bienvenida_anuncio` (bool), `precio_al_llegar_por_anuncio` (bool), `prospectos`
  (`{"desde_dias": 45, "aviso_dias": 30}` o `null` si está apagado) y `preguntar_presupuesto` (bool).
- **Lo que devuelve el agente:**
  - `memoria.sabemos.coordinar_en`: `YYYY-MM-DD`, la fecha en que se le vuelve a escribir;
  - `memoria.sabemos.coordinar`: `"ahora"` o `"despues"`;
  - `respuestas_rapidas`, como siempre.
