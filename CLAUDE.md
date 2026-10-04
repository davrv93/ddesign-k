# Contexto para agentes — kddesign (Baruka Design · CRM y bot de WhatsApp)

Este archivo reúne lo que el código no dice por sí solo: dónde corre el sistema, qué datos son reales
y cuáles de demostración, y las trampas que ya costaron tiempo. Para desplegar, sigue
[`DEPLOY.md`](DEPLOY.md) **al pie de la letra**. Para la orientación rápida (estructura, comandos,
reglas), lee [`AGENTS.md`](AGENTS.md).

## Qué es

CRM para **Baruka Design** (BARUKA DESIGN SAC), tienda de ropa de mujer en Perú que vende por
WhatsApp. Es la demo que se le enseña al cliente. Tiene tres piezas que conviven:

1. **Bot de WhatsApp.** La clienta escribe o manda una foto. El bot conversa, identifica la prenda, revisa
   el stock por talla y arma el pedido: código → talla → resumen → *SI* → ubicación.
2. **Panel web.** Kanban de pedidos, conversaciones con respuesta manual, productos y stock, vinculación
   de WhatsApp por QR y catálogo público.
3. **Chat web de prueba del agente** (`/demo-design`). Es el mismo agente que contesta en WhatsApp, con un
   panel de análisis: intención, fichas del RAG y motor usado.

## Arquitectura

```
Cliente WhatsApp ─► kddesign_evolution (fork davrv93/evolution-go) ─webhook─► kddesign_backend (Go, SQLite)
                                                                               │  texto libre y fotos
                                                                               ▼
                                                                   kddesign_agente (Python, FastAPI)
                                                                   embeddings ONNX + clasificador + RAG
                                                                   + LLM: motor «actual» (Gemini) o «deepseek»
Navegador ─► landing_web (nginx del borde, HTTPS) ─┬─ /baruka/      ─► kddesign_frontend (Qwik estático + nginx) ─► /api → backend
                                                   └─ /demo-design/ ─► kddesign_agente (UI de prueba)
```

| Carpeta | Servicio | Qué hace |
|---|---|---|
| `backend/` | `kddesign_backend` | API REST, webhook de evolution, flujo del bot (`internal/bot/bot.go`), SQLite en el volumen `kddesign_backend_data` |
| `frontend/` | `kddesign_frontend` | Panel Qwik estático. Se compila con `BASE_PATH` (hoy `/baruka/`) |
| `agente/` | `kddesign_agente` | Clasificadores (intención y comercial), máquina de etapas, RAG, búsqueda por foto y redacción con el LLM. En RAM ocupa unos 1,13 GiB |
| — | `kddesign_evolution` + `kddesign_evolution_db` | Mensajería de WhatsApp (whatsmeow). La sesión vive en el volumen `kddesign_evolution_db` |

### Cómo reparte el bot Go los mensajes

En `step()` de `backend/internal/bot/bot.go`:

- `menu`/`0`, los números `1`–`4`, un código de producto (`V25`) y los estados del pedido
  (talla/confirmación/ubicación) van a los **flujos fijos de Go**.
- **Todo lo demás va al agente**, incluidos el saludo y frases como «cómo hago mi pedido». Esto solo
  aplica cuando hay agente (`AGENT_URL`). Sin agente, las palabras sueltas vuelven a los flujos fijos.
- **El código dentro de una frase es una pregunta**, no un pedido: «¿de qué tela es el V42?» va al agente.
  Solo el código suelto («V42», «el V42») arranca el pedido.
- **En talla y confirmación el bot ya no se atasca.** Lo que no es una talla ni un sí/no va al agente con la
  etapa y el pedido en curso (`askAgent` → `closingReply`). Si la clienta sigue en el cierre se le recuerda el
  paso; si dudó o pidió otros modelos, `pauseDraft` libera la reserva y devuelve el pedido a consulta.
- **Después del *SI*** el estado es `esperando_pago` (`handlePayment`): el agente lleva Lima o provincia →
  total → datos de pago; **la foto que llega ahí es el comprobante** (`handleVoucher`, queda anotado en el
  pedido) y luego se pide la dirección. Sin agente sigue el flujo anterior (ubicación y una asesora cobra).
- **Memoria de la conversación** (`internal/bot/memoria.go`): la ficha que devuelve el agente se guarda en
  `convContext.Memoria` y viaja en cada `askAgent`/`agentPhoto`, como la etapa. Los reinicios del flujo
  (`setState(..., convContext{})`) la conservan, como `Anuncio`, pero sueltan la pregunta pendiente. Los estados del
  pedido fijan la suya (`esperando_talla` → `talla`, `esperando_confirmacion` → `confirmar`, `esperando_pago` →
  `lima_o_provincia`, `esperando_ubicacion` → `direccion`). Go manda además `perfil` (tallas y prendas de los
  pedidos confirmados anteriores) para la clienta que vuelve.
- Si el agente no responde, el backend recurre a Gemini (`internal/ai`).

### El agente

- **Tres etapas comerciales** (04-10-2026): prospección → seguimiento → cierre → venta confirmada. Las decide
  `agente/app/etapas.py` con reglas, no el LLM: interés no es compra, el «sí» depende de la pregunta pendiente
  (memoria), y con confianza < 0,60 la etapa no cambia. La etapa viaja en cada petición (`etapa`) y el
  bot Go la guarda en el contexto de la conversación. Detalle, umbrales y registro `[CLASSIFIER]` en
  [`agente/README.md`](agente/README.md).
- **Embeddings:** `Xenova/multilingual-e5-small` cuantizado (antes jina). El agente ocupa ~1,2 GiB.
- **Jev** (TypeSafe, por OpenRouter con la misma clave): en `cascada`, cuando el clasificador local duda
  (< 0,80), decide la intención con toda la conversación; con `JEV_VERIFICAR=1` quita de la respuesta del LLM
  lo que afirme de la prenda sin estar en su ficha. **Propone; la etapa la siguen decidiendo las reglas.**
  Cuesta ~0,3 s más por mensaje. Medido: cascada 98,5 % en la prueba comercial; verificación 9/9.
- **SetFit** (`agente/app/setfit.py`): ajuste contrastivo de e5 en una etapa del build con PyTorch. **No ganó**
  (97,0 / 95,6 % frente a 98,5 / 97,1 % del e5 sin ajustar) y en producción va apagado (`KD_SETFIT_PASOS=0`).
  Forzado suma ~300 MB de RAM. Detalle y cifras en [`agente/README.md`](agente/README.md).
- **El primer mensaje siempre es prospección** (`primer_mensaje` en `etapas.py`), salvo compra explícita.
- **Memoria y hilo** (`agente/app/memoria.py`, 04-10-2026): una ficha por conversación (`sabemos`: ocasión,
  día/noche, fecha, talla, estatura, color, presupuesto, envío, ciudad; `pendiente`: la pregunta que el bot dejó
  abierta; `preguntado`, `mostrados`, `producto`, `objeciones`, `llego_por`). Llega y vuelve en cada petición
  (`memoria`); si no llega, se reconstruye del historial. El mensaje nuevo se lee **primero** contra la pendiente;
  los datos se extraen con reglas (y Jev propone en la misma llamada de la cascada); **la siguiente pregunta la
  elige el código** (`memoria.siguiente`) y el LLM la recibe hecha en `SIGUIENTE PREGUNTA`. Lo ya preguntado o
  sabido no se repite (`quitar_repetidas` lo borra si el LLM insiste). Reemplazó a `esperando_cual`,
  `RE_PIDE_CONFIRMAR`, `talla_conocida` por historial y `_ultimos_del_bot` como fuente de verdad. Detalle en
  [`agente/README.md`](agente/README.md) («Memoria y hilo»).
- **Vestido del anuncio:** `KD_PRODUCTO_DEMO=V42`, pero **solo para quien llega por un anuncio de clic a
  WhatsApp** (el bot Go lo detecta en `contextInfo.externalAdReply` y lo recuerda en la conversación; si el
  título del anuncio nombra otra prenda, manda esa). Sin anuncio, «¿tienen este vestido?» → el bot pregunta
  cuál (foto o nombre). En el chat de prueba lo simula el botón «Desde anuncio». Material y lámina en `agente/seed/producto_demo.json`.
- **Pago y envíos:** costos en `agente/seed/venta.json` (el total lo suma el código). Yape y titular en
  `agente/seed/pago.md`, **fuera de git a propósito** (el repo es público); solo se dan con pedido confirmado.
- **Motor** (`MOTOR`): `actual` usa `LLM_*` (Gemini Flash-Lite, capa gratuita) y responde los saludos con
  frases del dataset. `deepseek` usa DeepSeek por OpenRouter (`OPENROUTER_API_KEY`, **de pago**), con más
  historial, los datos de la tienda y un tono más humano. El bot de WhatsApp usa el motor por defecto. La UI
  de prueba puede cambiarlo en cada mensaje. **Producción: `deepseek`.**
- **Fuera del rubro:** historia, cálculos, política y similares los rechaza con «no es mi giro», en ambos
  motores y también sin LLM (`FUERA_DE_GIRO`).
- **Fotos sugeridas** (`quiere_opciones`): solo se envían si la clienta pide opciones o abre una búsqueda
  nueva. No se envían si pregunta dónde queda la tienda, cómo pagar o por la talla del modelo que ya vio.
- **Productos por nombre** (`nombrados`): «el conjunto Xela» equivale a escribir `V32`. Si nombra una
  prenda, solo se habla de esa; **nunca se sustituye por otra**. Si ya la vio o está agotada, el bot
  pregunta «¿Quieres ver otras opciones?» (`OFERTA`): en WhatsApp con «Responde *SI*», en la web con un
  botón (`canal: "web"`). Con un «sí» (`acepta_oferta`), `otras_opciones` enseña prendas que no vio, con
  stock y primero de la misma categoría.
- **Catálogo sin prenda** («muéstrame tu catálogo», «quiero ver los modelos», o «1» en la web): no se
  mandan fotos al azar. El agente dice qué tipos de prenda hay con stock (`categorias_con_stock`) y pregunta
  cuál quiere ver; en la web salen como botones (`categorias`). Con la prenda dicha («quiero ver
  conjuntos») sí sale la vitrina con fotos. En la web, `1`–`4` se traducen a su opción (`MENU_WEB`), porque
  ahí no existe el menú numérico del bot Go.
- **Tarjetas con botones de talla (web):** cada sugerencia trae `titulo` y `tallas`; la UI pinta un botón
  por talla (las agotadas, tachadas) y al pulsarlo envía «Talla M del V24», que arma el pedido. `pie` sigue
  siendo el texto de WhatsApp.
- **El LLM no ve los pies de foto enteros** (`_hist_llm`): los imitaba y escribía «V24 · … Tallas: L, M, S
  👉 Escribe V24» como mensajes, duplicando las fotos. En el historial van resumidos y, por si acaso,
  `_sin_pies` quita de la respuesta los párrafos que parezcan un pie.
- **Talla y pedido:** decir la talla ya no arma el pedido. Solo **en cierre** (dijo que quiere comprarlo, o
  pulsó el botón de talla de la tarjeta web), con una prenda en foco (`producto_en_foco`), la talla
  devuelve `accion: "pedido"` con `codigo` y `talla`. El bot Go (`orderWithSize`) arma el pedido y va
  directo al resumen, sin volver a preguntar la talla. En la web salen botones de talla con su precio
  («L (S/ 330)») y luego «Sí, confirmar / Cambiar talla».
- **«Pásame la foto del Irla»** (`pide_foto_de`) manda la foto de esa prenda aunque ya la haya visto. No
  es la intención `foto` del clasificador, que significa que la clienta va a mandar una foto.
- El agente lee el catálogo vivo **antes** de empezar a atender; antes, durante unos 11 s tras cada
  reinicio, no conocía las prendas por nombre.
- **Datos de la tienda** que el LLM puede contar: `agente/seed/tienda.md`, `venta.json` y `pago.md`.
  Lo que no figura ahí lo deriva a una asesora (*4*).
- **Clasificador:** `agente/data/intenciones_tienda.csv` (frases en tríos: correcta, informal y con
  errores) más los datasets de BOT.zip. Se mide con `agente/data/prueba_chat.csv`, que **no** entra al
  entrenamiento. El 03-10-2026 la prueba con mensajes reales pasó de 38 % a 97 %, y un set totalmente
  nuevo dio 81 %. La validación cruzada (≈90 %) mide frases de plantilla y no refleja el chat real.
- **`CATALOGO100=0`** (producción) deja fuera el catálogo ficticio de 100 modelos de BOT.zip: el agente
  solo ofrece y reconoce por foto las prendas de la tienda.

## Producción

| | |
|---|---|
| Servidor | EC2 **3.132.249.89**, usuario `ubuntu`, llave `~/Downloads/cur5.pem`. Ubuntu 26.04, 2 vCPU, 3,8 GiB de RAM y 2 GiB de swap (`/swapfile`) |
| Dominio | `proyectopostventa.site` (DNS en DonWeb). Certificado Let's Encrypt, renovado por webroot |
| Panel | `https://proyectopostventa.site/baruka/` (catálogo público en `/baruka/catalogo`) |
| Chat de prueba | `https://proyectopostventa.site/demo-design/` |
| Página de demo | `https://proyectopostventa.site/demo/` — un enlace con tres tarjetas: landing, panel con el QR y chat (`demo-hub/`) |
| Código | `~/kddesign` (copiado con rsync, **sin `.git`**). `docker-compose.override.yml` existe **solo en el servidor** |
| Borde | `~/landing`: contenedor `landing_web`, que además sirve la landing de Consultoría Digital en `/` y la de Mennova en `/mennova/` (`DEPLOY.md` §5.2) |

El panel va **detrás de un alias** (`/baruka/`), no en un subdominio. Por eso el frontend se compila con
`KD_BASE_PATH=/baruka/`, y `PUBLIC_URL` vale `https://proyectopostventa.site/baruka`.

**Servidor anterior:** 3.130.244.177 (`cur4.pem`, el EC2 de producción de PjgFactSalud). Ahí quedan los
contenedores `kddesign_*` **detenidos**, con sus volúmenes y su `.env`, y `kddesign.duckdns.org` ya no
responde. **No los arranques**: serían dos bots con el mismo número de WhatsApp. Los datos se migraron el
03-10-2026.

## Datos: qué es real y qué es de demostración

| Dato | Estado |
|---|---|
| Productos **V21–V41** | **Reales.** Precio, tallas S/M/L, stock, descripción y fotos copiados de `baruka.dinersclubmall.pe` el 03-10-2026 (19 productos; Kabanova y Azra, que traen dos colores, se separan en uno por color). Scripts en `deploy/catalogo-diners/` |
| Política de cambios y devoluciones | **Real** (de la ficha de Diners), en `tienda.md` |
| Productos V01–V20 | Ejemplo, **inactivos**. No se borran porque los pedidos de prueba #4, #5 y #7 los referencian |
| Sucursales (Centro de Lima, Miraflores, Gamarra) | **Demostración, con direcciones inventadas.** Apagadas con `KD_SUCURSALES=0`: el bot ya no las menciona |
| Showroom, horario, envíos | **Reales** (los dio la tienda el 04-10-2026): Juan Ayllón 459, Santa Anita; L–D 9–19 h, solo con cita; Lima S/ 15, provincia S/ 20. En `tienda.md` y `venta.json` |
| Vestido **V42** «Vestido Gala Capa Azul» | Foto y lámina de materiales **reales** (las mandó la tienda). **Nombre, precio (S/ 260) y stock provisionales**: falta que Baruka los confirme |
| Precios de `agente/seed/precios_catalogo100.json` | Inventados. Con `CATALOGO100=0` no se usan |
| Vestido Irla (V35) | Diners lo registra en «palo rosa», pero en su foto es negro. Se copió tal cual |
| 9 pedidos y 14 conversaciones | Del 30-09 y 01-10, migrados del servidor anterior |

## Trampas conocidas

- **`sed -i` sobre `~/landing/nginx.conf` no llega al contenedor.** Es un bind mount de un solo archivo:
  `sed -i` crea un inodo nuevo y `landing_web` sigue leyendo el viejo. Edita en sitio o reinicia el
  contenedor (ver `DEPLOY.md` §5).
- **Un solo agente.** Dos copias (2 × 1,1 GiB) no caben. `/demo-design` y WhatsApp comparten `kddesign_agente`.
- **Docker no llega a `127.0.0.1` del host.** Los puertos `127.0.0.1:1848x` sirven para diagnosticar por
  SSH. El borde llega a los contenedores **por nombre** en la red `landing_default`.
- **Qwik con ruta base** deja el sitio en `dist/baruka/`. El `Dockerfile` lo sube a la raíz porque el
  alias quita el prefijo. Cualquier ruta absoluta nueva en el frontend debe pasar por `u()` de
  `src/lib/base.ts`, o romperá bajo `/baruka/`.
- **Con ruta base, Qwik necesita `trailingSlash: true`.** Con `false`, el SSG trata «/baruka/» como una ruta
  con barra de más y no genera la página de inicio: quedaba el `index.html` de fábrica de nginx y, tras el
  login, salía «Welcome to nginx!» con un 200. `vite.config.ts` lo activa solo cuando `BASE_PATH` no es «/».
- **Tocar el entrenamiento del agente re-entrena la imagen.** Si cambian `agente/data/`, `datos.py`,
  `modelo.py` o `entrenar.py`, el entrenamiento se rehace (≈4 min). Si cambian `imagen.py` o `imagenes/`,
  se reindexan las fotos. Si solo cambia `main.py`, el build tarda segundos.
- **El catálogo vivo** lo lee el agente del backend cada 5 min (`CATALOG_URL`). Justo al arrancar usa
  `catalogo_seed.json` hasta la primera lectura.
- **No hay respaldo automático** de los volúmenes `kddesign_backend_data` y `kddesign_evolution_db`. El
  respaldo manual está en `DEPLOY.md` §7.
- **El README** todavía habla de `kddesign.pjgfactsalud.com.pe` y de un nginx en el host. Eso describe el
  esquema original; el vigente es el de este archivo.

## Reglas

1. **No despliegues sin que el usuario lo pida.** Aun así, una orden de desplegar ya es la autorización:
   no vuelvas a preguntar.
2. **Nunca imprimas secretos** (`.env`). Comprueba si existen con `awk -F= '{print $1, (length($2)>0?"set":"EMPTY")}' .env`.
3. **No mandes mensajes de WhatsApp de prueba** a números reales sin consultarlo antes. Para probar el
   agente, usa `/demo-design/chat` o la UI de prueba.
4. **No inventes datos de la tienda** en los prompts (pagos, envíos, direcciones). Si no están en
   `tienda.md`, el bot debe derivar a una asesora.
5. **No borres pruebas** de `prueba_chat.csv` para mejorar la cifra. Si falla, se arregla el
   entrenamiento.
