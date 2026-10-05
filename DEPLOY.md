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
cd $R/agente   && python3 -m app.regresion --url http://127.0.0.1:18497      # 60 preguntas, 30 conversaciones, entradas raras y aguante; debe decir «REGRESIÓN OK»
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
