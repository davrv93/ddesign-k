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
| `agente/` | `kddesign_agente` | Clasificador de intención, RAG, búsqueda por foto y redacción con el LLM. La imagen pesa 2,5 GB y en RAM ocupa unos 1,3 GiB |
| — | `kddesign_evolution` + `kddesign_evolution_db` | Mensajería de WhatsApp (whatsmeow). La sesión vive en el volumen `kddesign_evolution_db` |

### Cómo reparte el bot Go los mensajes

En `step()` de `backend/internal/bot/bot.go`:

- `menu`/`0`, los números `1`–`4`, un código de producto (`V25`) y los estados del pedido
  (talla/confirmación/ubicación) van a los **flujos fijos de Go**.
- **Todo lo demás va al agente**, incluidos el saludo y frases como «cómo hago mi pedido». Esto solo
  aplica cuando hay agente (`AGENT_URL`). Sin agente, las palabras sueltas vuelven a los flujos fijos.
- Si el agente no responde, el backend recurre a Gemini (`internal/ai`).

### El agente

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
- **Talla y pedido:** con una prenda en foco (`producto_en_foco`), una talla («el Kabanova rojo en L», «L»)
  devuelve `accion: "pedido"` con `codigo` y `talla`. El bot Go (`orderWithSize`) arma el pedido y va
  directo al resumen, sin volver a preguntar la talla. En la web salen botones de talla con su precio
  («L (S/ 330)») y luego «Sí, confirmar / Cambiar talla».
- **«Pásame la foto del Irla»** (`pide_foto_de`) manda la foto de esa prenda aunque ya la haya visto. No
  es la intención `foto` del clasificador, que significa que la clienta va a mandar una foto.
- El agente lee el catálogo vivo **antes** de empezar a atender; antes, durante unos 11 s tras cada
  reinicio, no conocía las prendas por nombre.
- **Datos de la tienda** que el LLM puede contar: `agente/seed/tienda.md` y `agente/seed/sucursales.json`.
  Lo que no figura ahí, como formas de pago o costo de envío, lo deriva a una asesora (*4*).
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
| Borde | `~/landing`: contenedor `landing_web`, que además sirve la landing de consultoría en `/` |

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
| Sucursales (Centro de Lima, Miraflores, Gamarra) | **Demostración, con direcciones inventadas.** El bot las menciona. Pendiente: datos reales o quitarlas |
| Precios de `agente/seed/precios_catalogo100.json` | Inventados. Con `CATALOGO100=0` no se usan |
| Vestido Irla (V35) | Diners lo registra en «palo rosa», pero en su foto es negro. Se copió tal cual |
| 9 pedidos y 14 conversaciones | Del 30-09 y 01-10, migrados del servidor anterior |

## Trampas conocidas

- **`sed -i` sobre `~/landing/nginx.conf` no llega al contenedor.** Es un bind mount de un solo archivo:
  `sed -i` crea un inodo nuevo y `landing_web` sigue leyendo el viejo. Edita en sitio o reinicia el
  contenedor (ver `DEPLOY.md` §5).
- **Un solo agente.** Dos copias (2 × 1,3 GiB) no caben. `/demo-design` y WhatsApp comparten `kddesign_agente`.
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
