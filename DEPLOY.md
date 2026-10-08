# DEPLOY — kddesign en el EC2 de proyectopostventa.site

Guía oficial para desplegar. **Síguela al pie de la letra; no improvises comandos.** El contexto (qué es
cada pieza y por qué está así) está en [`CLAUDE.md`](CLAUDE.md).

## 0. Entorno

```bash
K=~/Downloads/cur5.pem
H=ubuntu@3.132.249.89
R=~/Downloads/PjgFactSalud_completo/ddesign-k   # copia local (fuente de verdad)
```

| En el servidor | Qué es |
|---|---|
| `~/kddesign/` | Código (rsync, **sin `.git`**), `.env` (600) y `docker-compose.override.yml` (**solo existe aquí**) |
| `~/landing/` | Borde: `nginx.conf` (bind mount en `landing_web`), `docker-compose.yml` y la landing en `site/` |
| `/etc/letsencrypt/live/proyectopostventa.site/` | Certificado; lo renueva `certbot.timer` por webroot `/var/www/certbot` |

Proyecto compose `kddesign`, red `kddesign_net`. Los contenedores `kddesign_frontend` y `kddesign_agente`
también están en `landing_default`, la red del borde, por el override.

### Variables que el `.env` del servidor debe tener (además de los secretos)

```
PUBLIC_URL=https://proyectopostventa.site/baruka
KD_BASE_PATH=/baruka/
AGENT_URL=http://agente:8000
MOTOR=deepseek
KD_CATALOGO100=0
KD_PRODUCTO_DEMO=V42     # vestido del anuncio: «este vestido» es ese (vacío = sin producto por defecto)
KD_SUCURSALES=0          # Baruka no tiene sucursales con stock: un showroom con cita
KD_JEV_MODO=cascada      # Jev decide la intención cuando el clasificador local duda (off | sombra | cascada)
KD_JEV_VERIFICAR=1       # Jev quita de la respuesta del LLM lo que invente de la prenda
KD_SETFIT_PASOS=0        # SetFit no ganó al e5 sin ajustar (agente/README.md): no gastar 20 min de build en él
```

Jev usa la misma `OPENROUTER_API_KEY`. Comprobación: `curl -s $B/demo-design/health` → `"jev": {"modo":
"cascada", "verificar": true, "configurado": true}`.

Además, `agente/seed/pago.md` (Yape y titular) **no está en git**: viaja con el rsync del §2 desde la copia
local. Si falta en el servidor, el bot deriva el pago a una asesora. Compruébalo sin mostrarlo:
`ssh -i $K $H 'test -s ~/kddesign/agente/seed/pago.md && echo pago.md OK'`.

Para comprobar si existen sin mostrar sus valores:

```bash
ssh -i $K $H 'cd ~/kddesign && awk -F= "{print \$1, (length(\$2)>0?\"set\":\"EMPTY\")}" .env'
```

## 1. Antes de subir — validar en local

```bash
cd $R/backend  && go vet ./... && go test ./...                 # bot, reservas, agente simulado
cd $R/frontend && BASE_PATH=/baruka/ npm run build && rm -rf dist # tsc + build con la ruta base real
cd $R/agente   && python3 -m py_compile app/*.py && python3 -m app.prueba_etapas && python3 -m app.prueba_memoria && python3 -m app.prueba_crm   # sintaxis, etapas, memoria y aviso al CRM
# Si cambió algo de la conversación (agente/app, agente/data o internal/bot): la prueba de regresión, que no cuesta nada.
# Necesita el agente de pruebas en 127.0.0.1:18497 (cómo levantarlo: agente/README.md, «Prueba de regresión»).
cd $R/agente   && python3 -m app.regresion --url http://127.0.0.1:18497      # 62 preguntas, 32 conversaciones, entradas raras y aguante; debe decir «REGRESIÓN OK»
```

## 2. Subir el código

Sube **todo el árbol**, pero nunca el `.env`, el override, `.git` ni los artefactos:

```bash
rsync -az --exclude .git --exclude .env --exclude docker-compose.override.yml \
  --exclude node_modules --exclude dist --exclude __pycache__ \
  -e "ssh -i $K" $R/ $H:kddesign/
```

## 3. Compilar y levantar — **un servicio a la vez**

Dos builds en paralelo agotan los 3,8 GiB. Compila uno, levántalo y sigue con el siguiente:

```bash
ssh -i $K $H 'cd ~/kddesign && docker compose build <servicio> && docker compose up -d <servicio>'
```

| Cambiaste… | Servicio | Tiempo | Ojo |
|---|---|---|---|
| `backend/` | `backend` | ~1 min | Las migraciones de SQLite son `CREATE TABLE IF NOT EXISTS` al arrancar |
| `frontend/` | `frontend` | ~2 min | `KD_BASE_PATH` llega como build arg. Sin él, el panel se compila para `/` y rompe bajo `/baruka/` |
| `agente/app/main.py`, `seed/` | `agente` | segundos | — |
| `agente/data/`, `datos.py`, `modelo.py`, `entrenar.py` | `agente` | ~4 min | Re-entrena. Revisa el log (§3.1) |
| `agente/imagenes/`, `imagen.py` | `agente` | ~1 min | Reindexa las fotos. Revisa `top1` en el log |
| — (casi nunca) | `evolution` | ~3 min | Compila el fork desde GitHub. Recrearlo corta WhatsApp unos segundos |

### 3.1 Agente: comprobar el entrenamiento en el log del build

```bash
ssh -i $K $H 'cd ~/kddesign && docker compose build --progress plain agente 2>&1 \
  | grep -E "comparación|elegido|intención  prueba|comercial  prueba|etapas|✗|top1|setfit:"'
```

`intención  prueba con mensajes reales` debe quedar en **≥ 0,95** (`data/prueba_chat.csv`), `comercial
prueba independiente` en **≥ 0,95** (`data/prueba_comercial.csv`), `top1` de fotos en **≥ 0,95** y `etapas
máquina de estados` en **26/26** (si falla un caso, el build se detiene solo). `clasificador elegido` dice
si quedó `base` o `setfit` (con `KD_SETFIT_PASOS=0` solo hay `base`). Si baja, no levantes la
imagen nueva: arregla los datos.

**La cifra que vale es la del build del servidor.** El modelo de embeddings está cuantizado y da números
algo distintos en ARM (Mac) y en x86 (EC2): el 04-10-2026 los mismos datos dieron 0,97 en el Mac y 0,94 en
el servidor. Se reforzó `intenciones_tienda.csv` y quedó en 0,985 (65/66). Mientras el build no pase, la
imagen nueva no se levanta: el contenedor en marcha sigue con la anterior hasta el `up -d`.

**SetFit en el EC2:** la primera vez instala PyTorch (~200 MB) en la etapa `setfit`, que queda en caché. Con
`KD_SETFIT_PASOS` > 0 el ajuste corre en cada cambio de `data/` y tarda 15–25 min con 2 vCPU y ~1,5 GB de
RAM. No lo lances en horario de venta.

## 4. Verificar (siempre, después de cada deploy)

```bash
B=https://proyectopostventa.site
for p in / /baruka/ /baruka/login /baruka/catalogo /baruka/api/public/catalog /demo-design/ /demo-design/health; do
  curl -s -o /dev/null -m15 -w "$p %{http_code}\n" $B$p; done          # todo 200
# Un 200 no basta: el 03-10 /baruka/ devolvía 200 con el «Welcome to nginx!» de fábrica.
curl -s $B/baruka/ | grep -o "<title[^>]*>[^<]*"                       # …Pedidos · Baruka Design
curl -s $B/demo-design/health | python3 -m json.tool | grep -E '"motor"|configurado|"fichas"'
ssh -i $K $H 'docker ps --format "{{.Names}} {{.Status}}"; free -m | sed -n 2,3p'
```

Para probar el agente sin WhatsApp (no envía nada a nadie):

```bash
curl -s -X POST $B/demo-design/chat -H 'Content-Type: application/json' \
  -d '{"mensaje":"cuánto cuesta el vestido Kendall?","cliente":"Ana"}' | python3 -m json.tool | head -20
```

Esperado: `motor: deepseek` y precio **S/ 320**.

Etapas comerciales (la etapa la guarda quien llama; aquí se manda a mano):

```bash
c() { curl -s -X POST $B/demo-design/chat -H 'Content-Type: application/json' -d "$1" \
  | python3 -c "import json,sys; j=json.load(sys.stdin); print(j['comercial']['etapa_anterior'],'→',j['etapa'],'|',j['comercial']['intent'],'|',j['accion'])"; }
c '{"mensaje":"Sí, me interesa","etapa":"prospeccion"}'     # prospeccion → seguimiento | interesado | responder
c '{"mensaje":"soy talla M","etapa":"seguimiento"}'         # seguimiento → seguimiento | consulta_talla | responder (NO pedido)
c '{"mensaje":"quiero comprarlo","etapa":"seguimiento"}'    # seguimiento → cierre | intencion_compra
``` Para probar WhatsApp de verdad, pide permiso antes
(no se mandan mensajes de prueba a números reales sin consultar).

## 5. nginx del borde (`~/landing/nginx.conf`)

`landing_web` monta **un solo archivo**. **Nunca uses `sed -i`**: crea un inodo nuevo y el contenedor
sigue leyendo el viejo. Edita en sitio (por ejemplo con Python, `open(p, "w")`) y sigue este orden:

```bash
ssh -i $K $H 'cd ~/landing && cp nginx.conf nginx.conf.bak-$(date +%Y%m%d%H%M)'
# … editar en sitio …
# validar con un contenedor desechable que monta el archivo NUEVO:
ssh -i $K $H 'cd ~/landing && docker run --rm -v $PWD/nginx.conf:/etc/nginx/conf.d/default.conf:ro \
  -v /etc/letsencrypt:/etc/letsencrypt:ro --network landing_default nginx:1.29-alpine nginx -t'
ssh -i $K $H 'docker exec landing_web nginx -s reload'
# si `docker exec landing_web nginx -t` sigue mostrando el error viejo → docker restart landing_web
```

Rutas actuales: `/` es la landing estática; `/baruka/` va a `kddesign_frontend` (sin el prefijo), con
`/baruka/api/events` sin buffer porque es SSE; `/demo-design/` va a `kddesign_agente`. Las tres usan
`resolver 127.0.0.11` y una variable, para que nginx arranque aunque un contenedor esté caído.

## 5.1 Página de demo (`/demo/`)

`demo-hub/index.html` es un único archivo estático: tres tarjetas que abren la landing
(`/landing-baruka/`), el panel con el QR (`/baruka/whatsapp`) y el chat (`/demo-design/`). Vive en
`~/landing/site/demo/`, que el borde ya sirve con su `location /`; no toca `nginx.conf`.

```bash
rsync -az -e "ssh -i $K" $R/demo-hub/index.html "${H}:landing/site/demo/index.html"   # ${H}: zsh se come «$H:l»
curl -s -o /dev/null -w "%{http_code}\n" https://proyectopostventa.site/demo/         # 200
```

Los puntos «En línea» consultan `/baruka/api/public/info` y `/demo-design/health`. Las fotos salen de
`/landing-baruka/img/`: si se quita esa landing, la primera tarjeta se queda sin imagen.

## 5.2 Landings estáticas del borde (`~/landing/site/`)

Además del panel y el chat, el borde sirve páginas estáticas de un solo archivo. Sus fuentes **no** están
en este repositorio, sino en la carpeta padre (`PjgFactSalud_completo/`, repositorio local sin remoto):

| Ruta | Fuente | Qué es |
|---|---|---|
| `/` | `consultoria-digital-landing/` | Landing de Consultoría Digital. Se genera con `python3 consultoria-digital-landing/generar.py` a partir de `cuerpo.html` y `propio.css`; no se edita `index.html` a mano |
| `/consultoria-digital/` | la misma | Copia de vista previa |
| `/mennova/` | `landingmenova/editorial/` | Landing editorial de Mennova Solutions |

```bash
P=~/Downloads/PjgFactSalud_completo
rsync -az -e "ssh -i $K" $P/consultoria-digital-landing/index.html "${H}:landing/site/index.html"
rsync -az -e "ssh -i $K" $P/consultoria-digital-landing/img/logo.jpg "${H}:landing/site/img/logo.jpg"
rsync -az -e "ssh -i $K" $P/landingmenova/editorial/index.html "${H}:landing/site/mennova/index.html"
```

**Respaldo de la raíz anterior** (sitio Qwik, sustituido el 04-10-2026): `~/landing/respaldos/raiz-consultoria-qwik-20261004-1834.tgz`
(47 archivos: `index.html`, `build/`, `assets/` y demás). `build/` y `assets/` siguen en `site/`, así que para
volver basta con restaurar el `index.html`:

```bash
ssh -i $K $H 'cd ~/landing && tar xzf respaldos/raiz-consultoria-qwik-20261004-1834.tgz -C site index.html'
```

## 6. WhatsApp

Para vincular o volver a vincular: entra a `https://proyectopostventa.site/baruka/whatsapp`, pulsa
**Generar QR** y escanéalo desde el celular de la tienda en *Dispositivos vinculados*. La sesión vive en
el volumen `kddesign_evolution_db`: recrear contenedores no la pierde, pero `docker compose down -v` sí.

**Nunca arranques** los `kddesign_*` del servidor anterior (3.130.244.177): serían dos bots con el mismo
número.

## 7. Respaldo manual (no hay automático)

```bash
F=$(date +%Y%m%d-%H%M)
ssh -i $K $H "mkdir -p ~/respaldos \
  && docker run --rm -v kddesign_backend_data:/d:ro -v ~/respaldos:/o alpine tar czf /o/kddesign_backend_data-$F.tgz -C /d . \
  && docker exec kddesign_evolution_db pg_dumpall -U evogo | gzip > ~/respaldos/kddesign_evolution-$F.sql.gz \
  && ls -la ~/respaldos"
```

- `backend_data` (`crm.db` y las fotos de `/media`) se copia como archivos. Es SQLite con WAL y tiene
  poco tráfico, así que en caliente el riesgo es bajo. Para una copia exacta, antes
  `docker compose stop backend`.
- La sesión de WhatsApp (Postgres de evolution) va con `pg_dumpall`, **no** copiando el volumen en
  caliente, que podría quedar inconsistente. Para restaurarla:
  `zcat …sql.gz | docker exec -i kddesign_evolution_db psql -U evogo -d postgres`.

Haz el respaldo **antes** de tocar datos (catálogo, migraciones) o de recrear `evolution-db`. Probado el
03-10-2026: `crm.db` y fotos, 10 MB; sesión, 7,5 KB.

## 8. Catálogo de la tienda (`deploy/catalogo-diners/`)

Copia el catálogo público de `baruka.dinersclubmall.pe` (precios, tallas, stock y fotos) al CRM:

```bash
cd $R/deploy/catalogo-diners
python3 extraer.py                    # 1. lee /catalogo y cada ficha → productos.json
mkdir -p fotos && python3 armar.py    # 2. un producto por color (V21…) → catalogo.json + fotos/
rsync -az -e "ssh -i $K" cargar_diners.py catalogo.json fotos $H:diners-carga/
ssh -i $K $H 'cd ~/diners-carga && python3 -u cargar_diners.py'   # 3. respaldo §7 ANTES: borra V21…V74 y crea
```

Después, copia las fotos al índice del agente: `fotos/vNN*.jpg` reducidas a 720 px van a
`agente/imagenes/tienda/` (`v21.jpg`, `v21_2.jpg`…). Vuelve a compilar el agente (§3) y comprueba `top1`.
Si cambia el número de productos, revisa el rango de códigos en `cargar_diners.py`.

## 8.1 Vestido de la demo comercial (`deploy/producto-demo/`)

El vestido del anuncio (**V42**) se crea o actualiza con un guion idempotente que usa la API del panel y
lee `agente/seed/producto_demo.json` (nombre, precio, tallas, descripción) y `agente/imagenes/tienda/v42.jpg`:

```bash
ssh -i $K $H 'cd ~/kddesign && API=http://127.0.0.1:18480 ENV=.env AGENTE_DIR=agente python3 deploy/producto-demo/cargar_demo.py'
```

Va **después** de levantar el backend y **antes** de reiniciar el agente (o espera 5 min: el agente relee el
catálogo solo). El nombre, el precio (S/ 260) y el stock son provisionales: los confirma la tienda. Para
cambiar de vestido: edita `producto_demo.json`, vuelve a correr el guion y pon su código en `KD_PRODUCTO_DEMO`.

## 9. Certificado

`certbot.timer` renueva por webroot (`/var/www/certbot`, que el borde sirve en
`/.well-known/acme-challenge/`). El gancho `/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh`
recarga `landing_web`. Para probarlo: `sudo certbot renew --dry-run --no-random-sleep-on-renew`.

## 10. Volver atrás

- **Código:** sube la versión anterior desde la copia local (§2) y vuelve a compilar el servicio.
- **nginx:** restaura `nginx.conf.bak-*` (editando en sitio), valida (§5) y recarga.
- **Datos del CRM:** `docker compose stop backend`, vacía el volumen y restaura el `.tgz` de §7 con
  `tar xzf` desde un contenedor `alpine` montando `kddesign_backend_data`. Después `up -d backend`.
- **Sesión de WhatsApp:** restaura el `.sql.gz` de §7 (comando en §7) o vuelve a escanear el QR (§6).
- **Kommo:** `KOMMO_ENABLED=0` + `docker compose up -d backend` (§11.5).

## 11. Kommo CRM (`backend/internal/kommo`)

Lleva al CRM Kommo (kommo.com, API v4) lo que capturan los dos canales: **un contacto por clienta, un lead por
conversación** en el embudo «Baruka · Ventas por WhatsApp», con sus campos, etiquetas y una nota por hito. Qué se
sincroniza y cuándo: [`CLAUDE.md`](CLAUDE.md), «CRM Kommo». **Apagado por defecto** (`KOMMO_ENABLED=0`): sin las
variables no se llama a Kommo y el bot se comporta igual que antes.

### 11.1 Crear la integración privada y el token (una vez, en Kommo)

Lo hace un usuario **administrador** de la cuenta (solo un administrador puede generar el token y crear embudos y
campos; el token trabaja con sus permisos).

1. Entra a `https://<subdominio>.kommo.com` → **Ajustes** → **Integraciones** → **Crear integración** (arriba a la
   derecha) → integración **privada**. Nombre: «kddesign». **No** hace falta Redirect URL ni el webhook de revocación.
2. Acceso: marca el acceso a los datos del **CRM** (leads, contactos, embudos, campos y notas). Chats, archivos y
   notificaciones no hacen falta. Guarda.
3. En la integración, pestaña **Llaves y alcances** → **Generar token de larga duración** → elige la caducidad (de 1
   día a 5 años; recomendado 1 año, con un recordatorio para renovarlo) → **copia el token ya**: Kommo no vuelve a
   mostrarlo.
4. Para revocarlo: pestaña **Autorización** → **Revocar acceso**. Un token revocado o caducado hace que cada envío
   termine en 401 (`[KOMMO] … HTTP 401` en el log del backend); el bot sigue contestando igual.

### 11.2 Variables en el `.env` del servidor

| Variable | Valor |
|---|---|
| `KOMMO_ENABLED` | `0` al desplegar; `1` cuando el seed haya pasado (§11.4) |
| `KOMMO_SUBDOMAIN` | El subdominio: `baruka` de `https://baruka.kommo.com` |
| `KOMMO_TOKEN` | El token de larga duración. **Nunca** lo imprimas ni lo pegues en un chat |
| `KOMMO_PIPELINE_NAME` | `Baruka · Ventas por WhatsApp` (se busca por nombre; si no existe, se crea) |
| `KOMMO_SYNC_TRANSCRIPT` | `0` = solo hitos; `1` = además cada turno como nota (sin datos de pago ni números de 9+ cifras) |
| `CRM_EVENT_SECRET` | `openssl rand -hex 24`. Lo comparten backend y agente para los turnos del chat web |

Para añadirlas sin que el token pase por la pantalla ni por el historial (`read -rs` lo pide sin mostrarlo):

```bash
read -rs KT && ssh -i $K $H "cd ~/kddesign && cp .env .env.bak-\$(date +%Y%m%d%H%M) && cat >> .env" <<EOF
KOMMO_ENABLED=0
KOMMO_SUBDOMAIN=baruka
KOMMO_TOKEN=$KT
KOMMO_PIPELINE_NAME="Baruka · Ventas por WhatsApp"
KOMMO_SYNC_TRANSCRIPT=0
CRM_EVENT_SECRET=$(openssl rand -hex 24)
EOF
unset KT
ssh -i $K $H 'cd ~/kddesign && awk -F= "/^(KOMMO|CRM_EVENT)/{print \$1, (length(\$2)>0?\"set\":\"EMPTY\")}" .env'
```

**`docker compose config` imprime el `.env` entero** (también el token): en el servidor usa `docker compose config -q`.

### 11.3 Desplegar

Primero se despliega con `KOMMO_ENABLED=0` y se comprueba que nada cambió; la sincronización se enciende en el §11.4.

```bash
# 0. Respaldo (§7). La tabla nueva kommo_vinculos es CREATE TABLE IF NOT EXISTS, pero se respalda igual.
# 1. Subir (§2) y compilar de uno en uno (§3):
ssh -i $K $H 'cd ~/kddesign && docker compose build backend && docker compose up -d backend'     # ~1 min (incluye kommo-seed)
ssh -i $K $H 'cd ~/kddesign && docker compose build agente && docker compose up -d agente'       # segundos: main.py, crm.py, ui.html
ssh -i $K $H 'cd ~/kddesign && docker compose build frontend && docker compose up -d frontend'   # ~2 min: bloqueo de /api/internal y «Ver en Kommo»
# 2. Verificar (§4) y además:
curl -s -o /dev/null -w "%{http_code}\n" -X POST $B/baruka/api/internal/crm/evento     # 404: la ruta interna no se ve desde fuera
curl -s $B/demo-design/health | python3 -c "import json,sys; h=json.load(sys.stdin); print(h['crm'], h['envios'])"   # True {'lima': 15, 'provincia': 20}
ssh -i $K $H 'docker logs kddesign_backend 2>&1 | grep -i kommo | tail -3'              # nada mientras KOMMO_ENABLED=0
```

### 11.4 Seed: embudo, campos, demo y volcado

`kommo-seed` va dentro de la imagen del backend y lee las mismas variables (no mira `KOMMO_ENABLED`: correrlo ya es
pedirlo). Va a 4 peticiones/s para dejarle sitio al backend dentro del límite de la cuenta (7/s).

```bash
KS='cd ~/kddesign && docker compose exec -T backend kommo-seed'
ssh -i $K $H "$KS --verificar"                          # crea o verifica el embudo, sus estados y los 13 campos; imprime los ids
ssh -i $K $H "$KS --demo"                               # 24 leads de demostración (etiqueta «demo», contactos «DEMO · …», +51 900 000 0xx)
ssh -i $K $H "$KS --desde-base --dry-run | head -80"    # qué subiría de la SQLite, sin llamar a Kommo (teléfonos enmascarados)
ssh -i $K $H "$KS --desde-base --limite 5"              # primero las 5 conversaciones más recientes
ssh -i $K $H "$KS --desde-base"                         # todo lo capturado (idempotente: repetirlo no duplica)
```

**Renombrar leads con el formato viejo de nombre** (antes del 05-10-2026 salían «Chat web wa:450 · WhatsApp» o «Chat web
web-prue · chat web»). Solo cambia los que conservan el nombre automático exacto; uno renombrado a mano no se toca:

```bash
KS='cd ~/kddesign && docker compose exec -T -e KOMMO_SYNC_TRANSCRIPT=0 backend kommo-seed'
ssh -i $K $H "$KS --renombrar --dry-run"      # lista los leads del chat web que cambiarían (lee Kommo, no escribe)
ssh -i $K $H "$KS --desde-base --renombrar"   # WhatsApp (al reaplicar) y chat web
```

`KOMMO_SYNC_TRANSCRIPT=0` va a propósito: con la transcripción activa, cada corrida de `--desde-base` vuelve a mandarla
como nota.

- La demo usa prendas, nombres y precios **reales** del catálogo (`CATALOG_URL`, por defecto el público de producción,
  solo lectura). Las clientas son inventadas y obvias («Ana Demo», «Hilda Prueba»…), con teléfonos `+51 900 000 0xx`.
- `--limpiar` cierra como «Venta perdida» los leads con la etiqueta `demo` y olvida sus vínculos locales. **La API v4
  de Kommo no tiene método para borrar leads ni contactos**: para eliminarlos del todo, en Kommo filtra la lista de
  leads por la etiqueta `demo`, selecciona todos → **Eliminar** (y lo mismo con los contactos «DEMO · …»).

Cuando el seed haya pasado, se enciende la sincronización en vivo (editando en sitio, sin imprimir nada):

```bash
ssh -i $K $H 'cd ~/kddesign && python3 -c "import re;p=\".env\";s=open(p).read();open(p,\"w\").write(re.sub(r\"(?m)^KOMMO_ENABLED=.*$\",\"KOMMO_ENABLED=1\",s))" \
  && docker compose up -d backend && sleep 3 && docker logs kddesign_backend 2>&1 | grep -i kommo | tail -2'
# → «kommo: sincronización activa con https://baruka.kommo.com (embudo …)». Escribe en /demo-design/ y mira el lead en Kommo.
```

`up -d` recrea el contenedor si cambió el `.env`; `restart` **no** vuelve a leer el `env_file`.

### 11.5 Diagnóstico y volver atrás

- Errores: `docker logs kddesign_backend 2>&1 | grep KOMMO`. Cada petición se reintenta hasta 5 veces ante 429/5xx (espera
  exponencial o la de `Retry-After`); si no sale, se registra y se sigue. El turno siguiente de esa conversación lleva el
  estado completo, así que el lead se pone al día solo; lo que se pierde es la nota de ese turno.
- Los ids del embudo y los campos quedan en el ajuste `kommo_esquema` de la SQLite (solo para consultar; se rehacen al
  arrancar). Si alguien renombra un campo en Kommo, se crea otro con el nombre original.
- **Apagar:** `KOMMO_ENABLED=0` + `docker compose up -d backend`. El chat web deja de avisar con `CRM_EVENT_SECRET` vacío
  + `docker compose up -d agente` (con el secreto puesto y Kommo apagado, el backend acepta el aviso y no hace nada).

## 12. JMD Ventas — multiempresa sobre MariaDB (`/jmdventas/<empresa>/`, proyecto `jmdventas`)

El mismo CRM, para varias empresas, en `https://proyectopostventa.site/jmdventas/<empresa>/` (Baruka:
`/jmdventas/baruka/`). Es un **proyecto aparte** de `kddesign`: `/baruka/` sigue con su SQLite, su bot y su WhatsApp.
Contexto y diseño en [`CLAUDE.md`](CLAUDE.md), «JMD Ventas».

| En el servidor | Qué es |
|---|---|
| `~/jmdventas/` | Copia (rsync, sin `.git`) de `backend/`, `frontend/` y `deploy/` de la rama `feat/jmdventas-multitenant` |
| `~/jmdventas/deploy/jmdventas/.env` (600) | `JWT_SECRET`, `WEBHOOK_SECRET`, `DB_PASSWORD`, `GEMINI_API_KEY` |
| `~/jmdventas/deploy/jmdventas/mariadb.env` (600) | `MARIADB_PASSWORD` (= `DB_PASSWORD`) y `MARIADB_ROOT_PASSWORD` |
| Contenedores | `jmdventas_mariadb`, `jmdventas_backend`, `jmdventas_frontend` (servicios `jmd-*`) |
| Volúmenes | `jmdventas_mariadb_data` (la base) y `jmdventas_backend_data` (fotos: `/data/tenants/<slug>/media`) |
| Red | `jmdventas_net`; backend y panel también en `landing_default` (el borde y el agente) |

**Desde el 07-10-2026 todo corre con Podman** (rootful): `sudo podman ps`. Las imágenes se compilan con Docker y se
cargan en Podman. Las imágenes se llaman `localhost/jmdventas/*` a propósito: con otro nombre, `podman load` las deja
como `docker.io/…` y el contenedor sigue con la vieja.

### 12.1 Desplegar un cambio

```bash
rsync -az --exclude .git --exclude .env --exclude '*.env' --exclude node_modules --exclude dist \
  -e "ssh -i $K" $R/backend $R/frontend $R/deploy "${H}:jmdventas/"
ssh -i $K $H 'cd ~/jmdventas/deploy/jmdventas && docker compose build jmd-backend \
  && docker save localhost/jmdventas/backend:local | sudo podman load'
# podman-compose --force-recreate NO recrea si el panel depende del backend: se quitan los dos y se levantan.
ssh -i $K $H 'cd ~/jmdventas/deploy/jmdventas && sudo podman rm -f --depend jmdventas_backend \
  && sudo podman-compose -p jmdventas up -d --no-build && sudo podman ps --format "{{.Names}} {{.ImageID}} {{.Status}}" | grep jmd'
# Panel: igual con jmd-frontend (compila con BASE_PATH=/jmdventas/XEMPRESAX/, ~1 min) y `podman rm -f jmdventas_frontend`.
```

Verificar:

```bash
for p in /jmdventas/baruka/ /jmdventas/baruka/login/ /jmdventas/baruka/api/public/catalog /baruka/ /demo-design/health; do
  curl -s -o /dev/null -w "$p %{http_code}\n" https://proyectopostventa.site$p; done          # todo 200
curl -s https://proyectopostventa.site/jmdventas/baruka/login/ | grep -c XEMPRESAX                  # 0
```

### 12.2 Alta de una empresa (sin tocar código)

```bash
# La clave va por entorno (nunca en la línea de órdenes). Idempotente: repetirla actualiza nombre y clave.
read -rs JMD_CLAVE && export JMD_CLAVE && ssh -i $K $H "export JMD_CLAVE='$JMD_CLAVE'; \
  sudo --preserve-env=JMD_CLAVE podman exec -e JMD_CLAVE jmdventas_backend \
  jmd alta --slug otra-tienda --nombre 'Otra Tienda' --whatsapp 51900000000 --usuario admin"; unset JMD_CLAVE
ssh -i $K $H 'sudo podman exec jmdventas_backend jmd empresas'     # empresas y conteos por tabla
```

Queda en `https://proyectopostventa.site/jmdventas/otra-tienda/` (panel, `login/`, `catalogo/`). El slug: minúsculas,
cifras y guiones, 2–40, y no una ruta reservada (`api`, `media`, `build`, `login`, `catalogo`…). La empresa nueva
empieza vacía: productos desde el panel. Sin WhatsApp (ver §12.4).

Tarjeta en la portada `/jmdventas/` (§12.6): `--orden N` al crearla (menor primero) y, después,
`jmd orden --empresa X --orden N`. Color del monograma y logo: `jmd marca --empresa X --color '#6d1f45' --logo https://…`
(vacíos = color derivado del slug y monograma). Empresas al 07-10-2026: Modas LILI (`modaslili`, orden 1, catálogo
vacío) y Baruka Design (`baruka`, orden 2). La clave de Modas LILI se generó en el servidor y está en
`~/jmdventas/deploy/jmdventas/credenciales-modaslili.txt` (600; `credenciales-*.txt` está en `.gitignore`).

### 12.3 Migración de la SQLite de `/baruka/` (hecha el 07-10-2026)

Sobre una **copia** (el respaldo del §7), nunca sobre la base viva. Repetible: borra lo que la empresa tenga en las
tablas de negocio (no sus usuarios) y lo vuelve a cargar en una transacción; conserva los ids (los números de pedido).

```bash
ssh -i $K $H 'T=$(mktemp -d) && tar xzf ~/respaldos/kddesign_backend_data-<fecha>.tgz -C $T ./crm.db ./crm.db-wal ./crm.db-shm \
  && sudo podman exec -u 0 jmdventas_backend mkdir -p /data/migracion \
  && for f in $T/crm.db*; do sudo podman cp $f jmdventas_backend:/data/migracion/; done \
  && sudo podman exec -u 0 jmdventas_backend jmd migrar-sqlite --origen /data/migracion/crm.db --empresa baruka \
  && sudo podman exec -u 0 jmdventas_backend rm -rf /data/migracion; rm -rf $T'
# Fotos: media/ del respaldo → jmdventas_backend:/data/tenants/baruka/media (podman cp + chown -R app).
```

Imprime los conteos origen/destino y falla si no cuadran. La SQLite de producción trae columnas de otra rama
(`agent_version`, `agent_last`, un `tenant_id` de texto): las comunes se copian y las demás se avisan. Los usuarios de
su tabla `users` (bcrypt) se copian por nombre. Los ids son globales: la migración con ids es para la primera empresa
de una base; si un id ya es de otra empresa, falla sin escribir nada.

### 12.6 Portada e intro (`frontend/jmdventas/`)

`index.html` es la portada, con estética de revista: cabecera didona «JMD Ventas», titular «Elige tu tienda», un
figurín de línea fina que se dibuja solo (SVG, `stroke-dashoffset`) y cada tienda como una portada («Nº 01», su nombre
como cabecera, monograma, productos activos y los botones). A la izquierda (en el teléfono, una franja arriba) un
quiosco de cabeceras **inventadas** que rotan (SILUETA, ATELIER, LUMIÈRE, MAISON, COUTURE, PASARELA): nunca nombres
de revistas reales. Las tiendas salen de `GET /jmdventas/api/empresas` (solo slug, nombre, orden, color, logo y
productos activos).

`intro.js` es la intro animada: apertura editorial en blanco y negro; constelaciones sobre azul profundo y púrpura
que dibujan tres figurines de moda de ~9 cabezas en contraposto (gala con capa, lápiz de un hombro, falda con
volantes); y «Inteligencia Artificial a tu servicio · Consultoría DIGITAL» con `consultoria-digital.jpg`. Sale en
**cada carga** (también con F5) de la portada y del login de cada empresa, con «Saltar» y Escape; con
`prefers-reduced-motion` no aparece. No bloquea: la página carga debajo. Los figurines viven en `intro.js`
(`window.JMDFiguras`) y la portada los reutiliza en SVG. Todo va dentro de la imagen del panel (`COPY jmdventas/` →
`/usr/share/nginx/jmd/`, servido en `/jmdventas/_jmd/`): se despliega horneando `jmd-frontend` (§12.1).

Para revisar: `/jmdventas/?intro=1` la fuerza (aun con movimiento reducido), `?intro=0` la omite y
`?intro_t=4.2` congela ese segundo (así se hacen las capturas).

**Login de cada tienda** (`/jmdventas/<slug>/login/`): lookbook con sus fotos, logo, nombre, formulario, dirección y
horario, leídos de `GET /<slug>/api/public/empresa`. Se fijan con `jmd marca` (color y logo), `jmd datos` (dirección y
horario) y `jmd galeria` (fotos; sin galería salen las de su catálogo). **Las fotos con personas no van a git**: viven en
`~/jmdventas/deploy/jmdventas/empresas/<slug>/` del servidor (volumen del panel, servido en
`/jmdventas/_jmd/empresas/<slug>/`); en local, en `assets-empresas/` (ignorada). Los logos sí van en
`frontend/jmdventas/logos/`.

El color de cada portada es el de la empresa (`jmd marca`); Baruka va en su vino de marca:
`ssh -i $K $H "sudo podman exec jmdventas_backend jmd marca --empresa baruka --color '#6d1f45'"`.

### 12.7 Fichas técnicas (`products.ficha`)

Cada producto tiene su ficha técnica (esquema de `agente/seed/fichas_producto.json`, rama `feat/agente-v2`): atributos
con su fuente, detalles, cómo queda, cuidados, resumen, contradicciones y pendientes. El panel la muestra y la edita en
la pestaña «Ficha técnica» del producto; lo que se cambia a mano queda con fuente «tienda». La columna se crea sola al
arrancar el backend (`ALTER TABLE … ADD COLUMN IF NOT EXISTS`).

Despliegue completo (fichas + portada + intro). **Todavía no se ha hecho**: requiere orden expresa del usuario.

```bash
F2=~/Downloads/PjgFactSalud_completo/ddesign-k-v2/agente/seed/fichas_producto.json   # 22 fichas, V21–V42
# 0. Respaldo de la base y de las imágenes actuales (para volver atrás)
ssh -i $K $H 'F=$(date +%Y%m%d-%H%M); RP=$(sudo grep "^MARIADB_ROOT_PASSWORD=" ~/jmdventas/deploy/jmdventas/mariadb.env | cut -d= -f2-); \
  sudo podman exec -e MYSQL_PWD="$RP" jmdventas_mariadb mariadb-dump -uroot --single-transaction jmdventas | gzip > ~/respaldos/jmdventas-$F.sql.gz \
  && sudo podman tag localhost/jmdventas/backend:local localhost/jmdventas/backend:previo \
  && sudo podman tag localhost/jmdventas/frontend:local localhost/jmdventas/frontend:previo && ls -la ~/respaldos/jmdventas-$F.sql.gz'
# 1. Código
rsync -az --exclude .git --exclude .env --exclude '*.env' --exclude node_modules --exclude dist \
  -e "ssh -i $K" $R/backend $R/frontend $R/deploy "${H}:jmdventas/"
# 2. Hornear (uno tras otro) y cargar en Podman
ssh -i $K $H 'cd ~/jmdventas/deploy/jmdventas && docker compose build jmd-backend && docker compose build jmd-frontend \
  && docker save localhost/jmdventas/backend:local localhost/jmdventas/frontend:local | sudo podman load'
# 3. Recrear backend y panel (MariaDB no se toca; la columna ficha se crea al arrancar)
ssh -i $K $H 'cd ~/jmdventas/deploy/jmdventas && sudo podman rm -f --depend jmdventas_backend \
  && sudo podman-compose -p jmdventas up -d --no-build && sudo podman ps --format "{{.Names}} {{.ImageID}} {{.Status}}" | grep jmd'
# 4. Cargar las fichas en Baruka (repetible; OJO: reemplaza también lo editado a mano en esos códigos)
scp -i $K $F2 "${H}:/tmp/fichas_producto.json"
ssh -i $K $H 'sudo podman cp /tmp/fichas_producto.json jmdventas_backend:/tmp/fichas.json \
  && sudo podman exec jmdventas_backend jmd fichas --empresa baruka --archivo /tmp/fichas.json && rm -f /tmp/fichas_producto.json'
# 5. Verificar
for p in /jmdventas/ /jmdventas/_jmd/intro.js /jmdventas/_jmd/consultoria-digital.jpg /jmdventas/api/empresas \
  /jmdventas/baruka/login/ /jmdventas/modaslili/login/ /baruka/ /demo-design/health; do
  curl -s -o /dev/null -w "$p %{http_code}\n" https://proyectopostventa.site$p; done                 # todo 200
```

`jmd fichas` imprime por código «10/13 datos · N pendientes · N contradicciones» y avisa de los códigos sin producto.

### 12.8 CRM básico (clientas, notas, tareas, equipo, inicio y CSV; rama `feat/jmdventas-crm`)

Detalle y brechas en [`docs/CRM_BASICO.md`](docs/CRM_BASICO.md). **Todavía no se ha desplegado**: requiere orden expresa.

- **Migración:** no hay paso a mano. Al arrancar, el backend crea `notas`, `tareas`, `actividad` y `cliente_etiquetas` y
  añade columnas con valor por defecto (`customers`: `email`, `ciudad`, `etapa`, `etapa_fijada`, `asesora_id`; `orders`:
  `asesora_id`) con `ADD COLUMN IF NOT EXISTS`. Copia una vez a `customers.etapa` la etapa que el bot ya guardó en la
  conversación. Probado sobre una MariaDB con el esquema anterior (dos arranques seguidos, sin pérdida).
- **Pasos:** los del §12.7 (0 respaldo con `mariadb-dump` y etiqueta `:previo`; 1 rsync; 2 hornear `jmd-backend` y luego
  `jmd-frontend`, uno tras otro; 3 recrear backend y panel), sin el paso 4 de fichas. Verificar además, con sesión:
  `GET /jmdventas/baruka/api/inicio`, `/api/clientas` y `/api/me` → 200.
- **Roles:** todos los usuarios que existen hoy son `admin` (el alta por consola crea admin), así que nada cambia para
  ellos. Las asesoras se crean en el panel: *Ajustes → Equipo*.
- **El tablero de pedidos pasa a `/pedidos`**; `/` es el panel de inicio y `/?pedido=N` redirige.
- **Volver atrás:** imágenes `:previo` (§12.5). Las tablas y columnas nuevas pueden quedarse: el backend anterior no las
  lee.

### 12.4 WhatsApp

Apagado en este stack (`EVOLUTION_URL=off`, sin `WHATSAPP_TENANT`): el panel lo muestra «no disponible», no se envía ni
se recibe nada y el webhook da 404. El número sigue en `kddesign_backend` (`/baruka/`). **No levantes otra evolution**
(dos bots con el mismo número). El corte, cuando se decida: apuntar el webhook de `kddesign_evolution` al backend nuevo
(`WHATSAPP_TENANT=baruka`, `EVOLUTION_URL` a la evolution de kddesign, misma red) y migrar otra vez lo que haya
entrado mientras tanto.

### 12.5 Volver atrás

- **Quitar `/jmdventas/` del borde:** restaurar `~/landing/nginx.conf.bak-pre-jmdventas-20261007-2119` **en sitio**
  (`python3 -c 'd=open("/home/ubuntu/landing/nginx.conf.bak-pre-jmdventas-20261007-2119").read(); f=open("/home/ubuntu/landing/nginx.conf","r+"); f.write(d); f.truncate()'`),
  `sudo podman exec landing_web nginx -t && sudo podman exec landing_web nginx -s reload`.
- **Apagar el stack:** `cd ~/jmdventas/deploy/jmdventas && sudo podman-compose -p jmdventas down` (sin `-v` conserva
  la base y las fotos). No toca `kddesign`, `keto` ni `landing`.
- **Datos de `/baruka/`:** la migración solo leyó una copia. Respaldo previo: `~/respaldos/kddesign_backend_data-pre-jmdventas-20261007-2119.tgz`.
- **Fichas, portada e intro (§12.6–12.7):** volver a las imágenes del paso 0 y recrear:
  `sudo podman tag localhost/jmdventas/backend:previo localhost/jmdventas/backend:local && sudo podman tag
  localhost/jmdventas/frontend:previo localhost/jmdventas/frontend:local && sudo podman rm -f --depend jmdventas_backend
  && sudo podman-compose -p jmdventas up -d --no-build`. La columna `products.ficha` puede quedarse (el backend anterior
  no la lee). Para quitar solo las fichas: `UPDATE products SET ficha='' WHERE tenant_id=<id de baruka>`; para todo,
  restaurar `~/respaldos/jmdventas-<fecha>.sql.gz` con `zcat … | sudo podman exec -i -e MYSQL_PWD=… jmdventas_mariadb mariadb -uroot jmdventas`.
- **Modas LILI:** `DELETE FROM tenants WHERE slug='modaslili'` (en cascada) y borrar su archivo de credenciales.
