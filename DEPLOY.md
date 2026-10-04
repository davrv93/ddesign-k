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
```

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
cd $R/agente   && python3 -m py_compile app/*.py && python3 -m app.prueba_etapas   # sintaxis + máquina de etapas (23/23)
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
  | grep -E "intención  prueba|comercial  prueba|etapas|✗|top1"'
```

`intención  prueba con mensajes reales` debe quedar en **≥ 0,95** (`data/prueba_chat.csv`), `comercial
prueba independiente` en **≥ 0,95** (`data/prueba_comercial.csv`), `top1` de fotos en **≥ 0,95** y `etapas
máquina de estados` en **23/23** (si falla un caso, el build se detiene solo). Si baja, no levantes la
imagen nueva: arregla los datos.

**La cifra que vale es la del build del servidor.** El modelo de embeddings está cuantizado y da números
algo distintos en ARM (Mac) y en x86 (EC2): el 04-10-2026 los mismos datos dieron 0,97 en el Mac y 0,94 en
el servidor. Se reforzó `intenciones_tienda.csv` y quedó en 0,985 (65/66). Mientras el build no pase, la
imagen nueva no se levanta: el contenedor en marcha sigue con la anterior hasta el `up -d`.

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
