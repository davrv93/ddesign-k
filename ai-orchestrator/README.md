# ai-orchestrator

Gateway de IA **compatible con OpenAI**, escrito en Rust, que elige el modelo por ti y no
se cae cuando un proveedor gratuito te devuelve 429. Trae una **UI de gestión** y
muestra **cuánta cuota te queda** en cada proveedor y modelo.

Tu aplicación habla con una sola API:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:4000/v1", api_key="mi-clave-local")

r = client.chat.completions.create(
    model="auto",                       # o "coding-deep", "reasoning", "laguna"…
    messages=[{"role": "user", "content": "Revisa este código…"}],
)
print(r.choices[0].message.content)
print(r.model_extra["orchestrator"])    # agente, modelo, proveedor, intentos
```

Todo lo demás ocurre detrás.

```
                  TU APLICACIÓN  (cliente OpenAI: model = auto | agente | modelo)
                        │
                        ▼
       ┌──────────────────────────────────────────┐
       │ ai-orchestrator  :4000                   │   UI de gestión en /ui/
       │                                          │
       │  Nivel 0  reglas (sin LLM): ¿imagen?     │
       │           ¿audio? ¿PDF? ¿código? ¿tools? │
       │  Nivel 1  juez LLM con salida JSON,      │
       │           solo si las reglas dudan       │
       │  Nivel 2  agente → cadena de modelos     │
       │           filtrada por capacidades y     │
       │           reordenada por la telemetría   │
       │                                          │
       │  Fallback · reintentos · cooldown ·      │
       │  circuit breaker · cuotas · telemetría   │
       └──────┬──────────────┬──────────────┬─────┘
              ▼              ▼              ▼
         OpenRouter     OpenCode Zen    Gemini (AI Studio)   … cualquier endpoint OpenAI
```

## Arranque

Necesitas Docker **o** Podman.

```bash
cd ai-orchestrator
cp .env.example .env          # pon ADMIN_TOKEN y las claves que tengas

docker compose up -d --build  # o: podman compose up -d --build
```

Sin compose, igual:

```bash
podman build -t ai-orchestrator .
podman run -d --name ai-orchestrator -p 127.0.0.1:4000:4000 \
  --env-file .env -v aio_data:/data ai-orchestrator
```

Abre **http://localhost:4000/ui/** y entra con `ADMIN_TOKEN`. Si lo dejaste vacío, se
generó uno: `docker exec ai-orchestrator cat /data/admin_token` (o `podman exec …`).

Sin contenedores: `cargo run --release` (lee `config.yaml` y guarda en `./data`).

## Desde la terminal: `jmd`, Claude Code y OpenCode

`jmd` es el CLI del gateway. Va dentro de la imagen y también se descarga desde la UI
(pestaña **Terminal**).

```bash
# Linux/WSL: el binario sale del propio gateway
mkdir -p ~/.local/bin && curl -fsSL http://localhost:4000/download/jmd -o ~/.local/bin/jmd && chmod +x ~/.local/bin/jmd
# macOS/Windows: cargo install --git <este repo> ai-orchestrator --bin jmd

jmd login --url http://localhost:4000 --token <ADMIN_TOKEN>
jmd status              # gateway, tokens, Claude Code, OpenCode, RTK y caveman
jmd setup claude        # Claude Code → gateway (y ofrece el hook de RTK)
jmd setup opencode      # proveedor «jmd» en opencode.json (--project para el del proyecto)
```

| Comando | |
|---|---|
| `jmd` | Modo interactivo: los comandos de abajo y, si no es un comando, chat con streaming (`/model`, `/style`, `/rate`, `/clear`) |
| `jmd models` · `providers` · `provider <n> [test\|balance]` · `quotas` | Estado, cuotas y saldo |
| `jmd stats` · `requests` | Uso, éxito, latencia, calidad y ahorro por estilo y cliente (+ `rtk gain`) |
| `jmd route "…"` · `chat "…"` | Ruta sin llamar · pregunta |
| `jmd style off\|lite\|full\|ultra [--compress on]` | Ahorro de tokens para todos los clientes |
| `jmd reset <modelo>` · `ui` | Quita cooldowns · abre la UI |

Todos aceptan `--json`.

**Claude Code** habla la API de Anthropic. El gateway expone `POST /v1/messages` (y
`/v1/messages/count_tokens`) y la traduce a OpenAI, con streaming, tools, imágenes y PDF.
`jmd setup claude` escribe `ANTHROPIC_BASE_URL` y `ANTHROPIC_AUTH_TOKEN` en
`~/.claude/settings.json`. Los nombres de modelo que pide Claude Code se mapean a agentes en
`compat.model_aliases`: `claude-*opus*` va a `coding-deep`, `claude-*sonnet*` a `coding` y
`claude-*haiku*` a `cheap` (editables en la UI). Si `ANTHROPIC_BASE_URL` está definida en la
terminal, gana a settings.json, y `jmd status` lo avisa.

**Ahorro de tokens:**

- **Estilo** (`savings.style`) es una instrucción de respuesta corta a la manera de
  [caveman](https://github.com/JuliusBrussee/caveman), aplicada en el gateway: sirve para
  cualquier agente. También se elige por petición (`X-JMD-Style`).
- **Compresión de salidas de herramientas** (`savings.compress_tool_output`) quita ANSI y líneas
  repetidas y recorta por el medio, conservando el final, donde suele estar el error. Es la idea de
  [RTK](https://github.com/rtk-ai/rtk), pero en el servidor.
- RTK propiamente dicho actúa en el equipo de cada persona, y `jmd setup claude` instala su hook.
  Si usas el plugin caveman, deja el estilo en `off` para no recortar dos veces.

`jmd stats` compara los tokens de salida por estilo y por cliente (`claude-code`,
`opencode`, `jmd`…).

## La UI

| Pestaña | Para qué |
|---|---|
| **Panel** | Cuotas por proveedor y por modelo, y estado de cada modelo (disponible, cooldown, circuito abierto, sin cuota) con éxito, latencia y calidad. Se refresca cada 10 s. «Reiniciar» cierra el circuito y quita los cooldowns |
| **Proveedores** | Alta, edición y baja de proveedores, con plantillas (OpenRouter, OpenCode Zen, Gemini, DeepSeek, Groq, OpenAI, Ollama). Incluye la clave, las cabeceras y la cuota. «Probar y ver modelos» llama a `GET /models` del proveedor y marca qué modelos configurados **no existen** |
| **Modelos** | Cada modelo es un grupo con uno o varios deployments (el mismo modelo en varios proveedores), con sus capacidades, su contexto, su costo y su cuota propia |
| **Agentes** | Perfiles con su cadena de modelos ordenable, su prioridad (calidad, velocidad o costo), sus parámetros por defecto y su system prompt |
| **Router y fiabilidad** | Umbral de las reglas y modelos del juez, una tabla de qué hacer con cada tipo de error y los pesos del aprendizaje |
| **Terminal** | Instalar `jmd`, conectar Claude Code y OpenCode, alias de modelos y ahorro de tokens |
| **Probar** | Chat de prueba que muestra la ruta elegida, los intentos y el modelo que respondió. Se puede valorar la respuesta |
| **Peticiones** | Últimos intentos con su resultado, latencia y tokens |
| **YAML** | Toda la configuración de una vez. Se puede descargar |

Cada cambio se valida (por ejemplo, no deja borrar un proveedor que usa un modelo), se
guarda en `data/config.yaml` y **se aplica sin reiniciar**.

## Cuotas: lo que queda

El formato OpenAI no tiene un endpoint estándar de cuota, así que el panel combina tres
fuentes:

1. **Cabeceras del proveedor.** De cada respuesta se leen `x-ratelimit-{limit,remaining,reset}-{requests,tokens}`
   (OpenAI, Groq, OpenRouter y otros). Si el proveedor dice «0 restantes hasta las 14:32»,
   ese modelo se salta hasta esa hora.
2. **Saldo del proveedor**, si tiene endpoint:
   - `openrouter`: `GET /key` devuelve el límite, lo gastado y lo restante.
   - `deepseek`: `/user/balance`.
   - `json`: cualquier URL, con punteros JSON al restante y al límite.

   Se consulta cada 5 minutos o con el botón del panel. Con el saldo agotado, el proveedor se salta.
3. **Presupuestos locales** por proveedor y por modelo: `requests_per_day`,
   `requests_per_minute` y `tokens_per_day`. Sirven para las capas gratuitas que no avisan
   hasta el 429. Cada uno se reinicia a su hora: `reset_utc_offset_hours: -8` para AI
   Studio. Lo gastado sale de la telemetría, así que sobrevive a los reinicios. Los 429 no
   cuentan.

**Un modelo sin cuota se salta antes de llamarlo**: no se gasta una petición en recibir el 429.

## Perfiles (`model`)

| `model` | Qué hace |
|---|---|
| `auto` | El router decide: reglas → juez si dudan → agente |
| `general`, `cheap`, `coding`, `coding-fast`, `coding-deep`, `reasoning`, `multimodal`, `vision`, `document`, `research` | Ese agente (su cadena, filtrada y reordenada) |
| `laguna`, `gemini-flash`, … | Ese modelo, sin cadena |

Pistas opcionales en el cuerpo (`"orchestrator": {"agent": "coding", "priority": "speed",
"judge": false}`) o en cabeceras (`X-Orchestrator-Agent`, `X-Orchestrator-Priority`).

Cada respuesta trae el campo `orchestrator` (request_id, agente, modelo, proveedor,
ruta, cadena e intentos) y las cabeceras `x-orchestrator-*`.

## Fiabilidad

Por cada modelo de la cadena:

```
Laguna ── 429 ── backoff (respeta Retry-After) ── 429 ── 429 ── cooldown 30 s
   │
   └─► Nemotron ── 200 OK
```

La tabla `reliability.policy` (editable en la UI) decide, por tipo de error:

| Error | Reintentos | Fallback | Cooldown | Breaker |
|---|---|---|---|---|
| `rate_limit` (429) | 2 | sí | 30 s | sí |
| `quota` (402, cuota diaria) | 0 | sí | 1 h | sí |
| `timeout` (408/504) | 1 | sí | — | sí |
| `server_error` (5xx) | 2 | sí | — | sí |
| `unavailable` (modelo caído) | 0 | sí | 60 s | sí |
| `auth` (401/403) | 0 | sí | 10 min | no |
| `not_found` (404) | 0 | sí | 1 h | no |
| `context` / `unsupported` | 0 | sí | — | no |
| `bad_request` (400) | 0 | **no**: va al cliente | — | no |

- **Circuit breaker:** con 5 fallos en 60 s el modelo queda fuera 30 s. Después pasa **una**
  petición de prueba: si responde, se cierra; si falla, vuelve a abrirse.
- **Deployments:** dentro de un modelo se prueba primero el proveedor 1 y luego el 2. Un
  proveedor que falla se enfría solo para ese modelo.
- Si toda la cadena está en cooldown o con el circuito abierto, se prueba igualmente el
  modelo que vuelve antes: es mejor un intento que un error seguro. Si nada responde: `429`
  con `Retry-After` cuando el problema es la cuota, o `503` en otro caso.
- En **streaming** se hace fallback mientras no haya llegado el primer byte. Después, el
  stream pasa tal cual.

## Aprendizaje

Cada intento queda en `data/telemetry.db` (SQLite):

```json
{"task": "coding", "bucket": "text|large|tools=1|r=high", "model": "laguna",
 "latency": 4.2, "ok": true, "quality": 0.91}
```

El scorer reordena la cadena de cada agente con esta fórmula:

```
w_calidad·calidad + w_éxito·éxito + w_velocidad·velocidad + w_costo·costo
```

Los pesos dependen de la prioridad. Usa las estadísticas del **subtipo** de tarea
(«coding con contexto grande y tools») cuando hay suficientes muestras, y si no, las del
agente entero. Sin datos se respeta el orden de la config. Con `exploration: 0.05`, de vez
en cuando prueba primero un modelo con pocos datos.

La calidad automática es una cota: penaliza las respuestas vacías, cortadas o con JSON
inválido. La calidad fina llega por:

```bash
curl -X POST localhost:4000/v1/feedback -H 'Content-Type: application/json' \
  -d '{"request_id": "…", "quality": 0.9}'
```

La pestaña **Probar** también la envía con los botones Buena, Regular y Mala.

## API

| Ruta | |
|---|---|
| `POST /v1/chat/completions` | OpenAI, con streaming |
| `POST /v1/messages` · `/v1/messages/count_tokens` | Anthropic (Claude Code), con streaming y tools |
| `GET /v1/models` | `auto`, los agentes y los modelos |
| `POST /v1/route` | Dry-run: qué agente y qué cadena tocarían, sin llamar a nadie |
| `POST /v1/feedback` | `{request_id, quality}` |
| `GET /health` | |
| `/admin/api/*` | Lo que usa la UI (con `Authorization: Bearer $ADMIN_TOKEN`) |

`/v1` pide `Authorization: Bearer <una de GATEWAY_API_KEYS>`. Si esa variable está vacía,
no pide nada: déjalo así solo en local.

## Configuración

`config.yaml` es la **semilla**. Se copia a `data/config.yaml` en el primer arranque y desde
ahí manda la UI. Para volver a la semilla, borra `data/config.yaml`.

Las claves pueden ir en variables de entorno (`api_key_env`) o guardarse desde la UI
(`api_key`, en `data/config.yaml`, con permisos 600). La UI y la API las muestran tapadas.

> **Antes de usarlo:** los identificadores de los modelos gratuitos de la semilla (Laguna,
> Muse Spark, Nemotron 3, Inkling…) **no se pudieron verificar** al escribirla y cambian a
> menudo. Abre **Proveedores → Probar y ver modelos**: marca en rojo los que no existen y
> lista los reales para corregirlos en **Modelos**.

## Desarrollo

```bash
cargo test                 # 64 pruebas: unitarias y de punta a punta contra un proveedor falso
cargo clippy --all-targets
cargo run                  # http://localhost:4000/ui/
```

```
src/
  main.rs         arranque, sondeo de saldos, apagado limpio
  api.rs          /v1 (OpenAI), /admin/api, UI embebida
  engine.rs       router (niveles 0-1-2), fallback manager, llamadas a los proveedores, juez
  classifier.rs   nivel 0: rasgos de la petición y reglas
  reliability.rs  clasificación de errores, backoff, circuit breaker, cooldowns
  quotas.rs       cabeceras x-ratelimit, saldo, presupuestos locales
  scorer.rs       ranking aprendido y calidad automática
  telemetry.rs    SQLite
  config.rs       esquema, validación, claves tapadas, alias de modelos
  anthropic.rs    traducción Anthropic ⇄ OpenAI (peticiones, respuestas, SSE)
  savings.rs      estilo de respuesta y compresión de salidas de herramientas
  bin/jmd/        el CLI: comandos, modo interactivo, setup de Claude Code/OpenCode/RTK
ui/               index.html · app.js · style.css (sin dependencias; van dentro del binario)
tests/gateway.rs  gateway real contra un proveedor OpenAI falso (429, 400, 503, SSE, /models, /key)
```
