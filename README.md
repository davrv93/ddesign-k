# Baruka Design · CRM de pedidos por WhatsApp (kddesign)

CRM para una tienda de ropa que atiende por WhatsApp:

- **Bot con menú**: catálogo y precios, consulta por foto, estado del pedido y derivación a una asesora.
- **Reconocimiento de fotos con IA**: la clienta manda la foto del vestido que vio. Gemini 3.5 Flash-Lite (capa gratuita) la compara con el catálogo; si Gemini falla, responde Gemma. Luego el bot revisa el **stock por talla**.
- **Pedido completo en el chat**: talla → resumen → **SI** (reserva el stock) → **ubicación** (pin de WhatsApp o dirección escrita).
- **Panel web** (Qwik): vinculación del WhatsApp por **QR** o código, **kanban de pedidos** con arrastrar y soltar, conversaciones con respuesta manual, productos/stock y catálogo público.
- **Todo en Docker** con puertos alternos, SQLite para el CRM y el fork [`davrv93/evolution-go`](https://github.com/davrv93/evolution-go) para la mensajería.

```
Cliente WhatsApp ──► evolution-go (whatsmeow) ──webhook──► backend Go ──► SQLite
                          ▲                                   │  └──► Gemini 3.5 Flash-Lite → Gemma (respaldo)
                          └───────── envía respuestas ─────────┘
Navegador ─► nginx del host (HTTPS) ─► 127.0.0.1:18480 frontend (nginx + Qwik estático) ─► /api → backend
```

## Contenedores

| Servicio | Contenedor | Puerto en el host | Qué hace |
|---|---|---|---|
| `frontend` | `kddesign_frontend` | `127.0.0.1:18480` | Panel Qwik estático; enruta `/api` y `/media` al backend |
| `backend` | `kddesign_backend` | — (sólo red interna) | API REST, webhook, bot, IA, SQLite en el volumen `backend_data` |
| `evolution` | `kddesign_evolution` | `127.0.0.1:18481` | Fork evolution-go (compilado desde GitHub) |
| `evolution-db` | `kddesign_evolution_db` | — | Postgres propio de evolution-go (sesión de WhatsApp) |
| `agente` | `kddesign_agente` | `127.0.0.1:18482` | Agente conversacional: embeddings locales + clasificador + RAG + DeepSeek. Ver [`agente/README.md`](agente/README.md) |

Todo usa el proyecto de compose `kddesign`, la red `kddesign_net` y volúmenes propios: **no comparte nada con los contenedores `pjg_*`** ya existentes en el EC2. Los puertos se publican sólo en `127.0.0.1`, así que no quedan expuestos a internet ni chocan con los de otros servicios.

## Despliegue en el EC2

Requisitos: Docker con el plugin `compose`, nginx en el host y certbot.

1. **DNS**: crea un registro `A` `kddesign.pjgfactsalud.com.pe` → IP pública del EC2 (la misma de `boticalima.pjgfactsalud.com.pe`).
2. **Código y variables**:
   ```bash
   git clone <este repo> ~/kddesign && cd ~/kddesign
   ./deploy/deploy.sh          # crea .env con secretos aleatorios, construye y levanta
   nano .env                   # pon GEMINI_API_KEY, WHATSAPP_NUMBER y revisa ADMIN_PASSWORD
   docker compose up -d        # aplica los cambios del .env
   ```
3. **nginx + HTTPS** (sólo agrega un sitio nuevo; no toca los demás):
   ```bash
   CERTBOT_EMAIL=tu@correo.com ./deploy/deploy.sh --nginx
   ```
   o a mano: copia `deploy/nginx-kddesign.conf` a `/etc/nginx/sites-available/kddesign`, enlázalo en `sites-enabled`, `sudo nginx -t && sudo systemctl reload nginx` y `sudo certbot --nginx -d kddesign.pjgfactsalud.com.pe`.

   > Si el nginx del host corre **dentro de un contenedor**, `127.0.0.1:18480` no le llega: conecta ese contenedor a la red `kddesign_net` (`docker network connect kddesign_net <nginx>`) y usa `proxy_pass http://kddesign_frontend:80;`.
4. Entra a `https://kddesign.pjgfactsalud.com.pe`, inicia sesión y ve a **WhatsApp → Generar QR**. Escanéalo desde *Dispositivos vinculados* en el celular de la tienda.

Actualizar: `git pull && docker compose up -d --build`. Respaldo: el volumen `kddesign_backend_data` (`crm.db` + fotos) y `kddesign_evolution_db` (sesión de WhatsApp).

## Flujo del bot

| El cliente escribe… | El bot… |
|---|---|
| `hola`, `menu`, `0` | Saluda por su nombre y muestra el menú 1–4 |
| `1` / “catálogo” | Lista códigos, precios y tallas disponibles + enlace a `/catalogo` |
| `2` o **envía una foto** | IA identifica el modelo → si hay stock, responde con la foto del catálogo, precio y tallas; si está agotado sugiere parecidos; si no lo reconoce, deja la **consulta** en el kanban para una asesora |
| Un código (`V05`) | Igual que la foto, sin IA |
| Talla (`M`, “mediana”, “2 en talla S”) | Arma el resumen del pedido y pide **SI / NO** |
| `SI` | Confirma, **descuenta stock** y pide la ubicación |
| 📍 ubicación o dirección | La guarda en el pedido y cierra la conversación |
| `3` | Estado de sus pedidos |
| `4` | Pausa el bot en ese chat y avisa en el panel |

Texto libre que no encaja lo atiende el **agente conversacional** (`agente/`): clasifica la intención con
embeddings locales, busca en el catálogo y responde con DeepSeek (saludo, repreguntas, estado de ánimo, cambio
de tema, insultos, preguntas de producto). Si el agente no responde, se interpreta con Gemini como antes. Si una asesora responde desde el panel o desde el celular, el bot se pausa en ese chat (se reactiva desde el panel o solo, pasadas las horas configuradas en *Ajustes*).

### Kanban

`Consultas → Por confirmar → Confirmados → En preparación → Enviados → Entregados / Cancelados`.
El stock se reserva al entrar a *Confirmados* (o posteriores) y se devuelve al pasar a *Cancelados*, *Por confirmar* o al eliminar. Al mover una tarjeta se avisa al cliente por WhatsApp (se puede desactivar).

## Catálogo inicial

Al primer arranque se cargan 20 vestidos (`V01`–`V20`) a partir de las fotos enviadas, con nombre, color, descripción y etiquetas visuales para la IA. **Los precios y el stock son de ejemplo**: ajústalos en *Productos y stock*. Al subir la foto de un producto nuevo, “✨ Autocompletar con IA” propone nombre, color y etiquetas.

## IA

- Principal: `gemini-3.5-flash-lite` (≈1,5 s por foto en las pruebas).
- Respaldo: `gemma-4-26b-a4b-it`, luego `gemma-4-31b-it` (más lentos, ≈20 s; se usan sólo si Gemini responde 429/5xx o no responde).
- Todo configurable en `.env` (`GEMINI_MODEL`, `GEMINI_FALLBACK_MODEL`, `MATCH_THRESHOLD`).

Probar el reconocimiento contra el catálogo sin WhatsApp:

```bash
cd backend && GEMINI_API_KEY=... go run ./cmd/aicheck foto1.jpg foto2.jpg
```

## Desarrollo local

```bash
# backend (Go 1.26)
cd backend
ADMIN_PASSWORD=dev JWT_SECRET=dev WEBHOOK_SECRET=dev EVOLUTION_URL=http://localhost:18481 \
EVOLUTION_GLOBAL_API_KEY=... EVOLUTION_INSTANCE_TOKEN=... GEMINI_API_KEY=... go run ./cmd/server
go test ./...

# frontend (Node 22) — proxy de /api y /media a localhost:8080
cd frontend && npm install && npm run dev
```

## API (resumen)

Pública: `POST /api/auth/login`, `GET /api/public/catalog`, `GET /media/...`.
Con token (`Authorization: Bearer`): `/api/whatsapp/{status,connect,qr,pair,logout}`, `/api/products[/{id}[/image]]`, `/api/products/describe`, `/api/orders[/{id}]`, `/api/conversations[/{id}/{messages,send,bot}]`, `/api/settings`, `/api/stats`, `/api/events` (SSE).
Interna: `POST /webhook/evolution/{WEBHOOK_SECRET}` (evolution-go → backend; el nginx del frontend no la expone).
