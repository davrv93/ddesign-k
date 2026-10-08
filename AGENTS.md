# AGENTS.md — kddesign (Baruka Design)

Lee primero [`CLAUDE.md`](CLAUDE.md): dónde corre el sistema, qué datos son reales y cuáles de
demostración, y las trampas conocidas. Para desplegar, sigue [`DEPLOY.md`](DEPLOY.md) **al pie de la
letra**; nunca inventes comandos de despliegue.

## Repo

- Remoto: `https://github.com/davrv93/ddesign-k.git`. La única rama publicada es
  `claude/gifted-rubin-oc1iqo` (no hay `main`).
- La copia local vive dentro del superproyecto de PjgFactSalud
  (`~/Downloads/PjgFactSalud_completo/ddesign-k`), pero es **su propio repo git**: haz commit y push
  **desde `ddesign-k/`**. Un push **no despliega**; aquí no hay CD.
- El servidor tiene una copia sin `.git`: lo que manda es la copia local que subes por rsync.

## Estructura

```
backend/            Go 1.26 · API, webhook de evolution, bot (internal/bot/bot.go), SQLite (internal/store)
  internal/agente/  cliente HTTP del agente        internal/ai/   Gemini (respaldo y «describe» de fotos)
  internal/seed/    catálogo y sucursales iniciales (solo el primer arranque)
  internal/kommo/   CRM Kommo: cliente API v4, embudo y campos, Sincronizador; simulado/ = Kommo falso para pruebas
  cmd/kommo-seed/   embudo + campos, leads demo, --limpiar, --desde-base [--dry-run] (DEPLOY.md §11)
  cmd/jmd/          JMD Ventas: alta de empresas, `empresas`, migrar-sqlite (DEPLOY.md §12)
  internal/store/   SQLite (una tienda) o MariaDB (multiempresa); todo filtrado por tenant_id (CLAUDE.md, «JMD Ventas»)
                    crm.go y equipo.go: clientas, notas, tareas, actividad, roles (CLAUDE.md, «CRM básico»)
frontend/           Qwik City estático + nginx · src/lib/api.ts (cliente) · src/lib/base.ts (ruta base)
agente/             Python 3.12 · FastAPI · app/main.py (conversación, prompts, motores, fotos sugeridas)
  app/entrenar.py   entrena clasificadores e índice RAG (en el build)   app/imagen.py  índice de fotos
  data/             datasets; intenciones_tienda.csv (entrena) · prueba_chat.csv (solo mide)
  seed/             tienda.md y sucursales.json (lo que el LLM puede contar de la tienda)
  imagenes/tienda/  fotos de los productos para la búsqueda por foto (vNN.jpg, vNN_2.jpg…)
deploy/             catalogo-diners/ (copia el catálogo real de Diners al CRM) · deploy.sh y edge-kddesign.conf
                    son del esquema anterior (nginx en host, kddesign.duckdns.org): no los uses
docker-compose.yml  5 servicios: frontend, backend, agente, evolution, evolution-db
```

## Comandos

```bash
# Backend
cd backend && go vet ./... && go test ./...
# Frontend (siempre con la ruta base de producción; incluye tsc)
cd frontend && BASE_PATH=/baruka/ npm run build && rm -rf dist
# Agente: no corre en local sin sus modelos (se hornean en la imagen). En local, solo sintaxis:
cd agente && python3 -m py_compile app/*.py
# Probar el agente desplegado sin WhatsApp:
curl -s -X POST https://proyectopostventa.site/demo-design/chat -H 'Content-Type: application/json' \
  -d '{"mensaje":"tienen blazer?","cliente":"Ana","motor":"deepseek"}'
```

## Reglas al cambiar código

- **Frontend:** toda ruta absoluta del sitio (`/api/…`, `/media/…`, `/login`, enlaces del menú, `src`
  de imágenes del backend) pasa por `u()` de `src/lib/base.ts`. Sin eso, la pantalla funciona en local y
  rompe bajo `/baruka/`.
- **Bot Go:** cuando hay agente, las frases van a él; las reglas por palabra suelta (`hasAny`) solo
  aplican sin agente. Si agregas un atajo, acompáñalo de su prueba en `internal/bot/agente_test.go`.
- **Agente, intenciones:** los ejemplos nuevos van en `data/intenciones_tienda.csv`, en **tríos**
  (correcta, informal, con errores). Las frases con coma van entre comillas. No copies frases de
  `prueba_chat.csv`, porque esa prueba mide lo que el modelo no vio. Un cambio aquí re-entrena la imagen
  (~4 min). Revisa la cifra en el log del build (`DEPLOY.md` §3.1).
- **Agente, etapas comerciales:** las transiciones se cambian en `app/etapas.py` y cada regla nueva lleva su
  caso en `app/prueba_etapas.py` (`python3 -m app.prueba_etapas`, sin dependencias). Los ejemplos del
  clasificador comercial van en `data/comercial.csv`; `data/prueba_comercial.csv` no entra al entrenamiento.
  En el bot Go, las pruebas del flujo por etapas están en `internal/bot/etapas_test.go`.
- **Agente, memoria y hilo:** la ficha de la conversación vive en `app/memoria.py` y se prueba con
  `python3 -m app.prueba_memoria` (sin dependencias; también corre en el build). Una pregunta nueva del bot lleva
  su clave en `PREGUNTAS` (texto que se le da al LLM) **y** en `DETECTOR` (cómo se reconoce en una respuesta): la
  prueba comprueba que cada pregunta se reconozca a sí misma; si no, nunca quedaría pendiente y se repetiría. Un
  dato nuevo va en `CAMPOS`, en `extraer` con su caso y, si se pregunta, en `ORDEN` de su etapa. No vuelvas a
  decidir nada mirando «lo último que dijo el bot» en el historial: usa `pendiente`. En el bot Go, la memoria es
  JSON opaco (`internal/bot/memoria.go`): solo se tocan `pendiente`, `sabemos.talla` y `producto`, siempre
  partiendo de `memoriaActual` (editar un `convContext{}` nuevo la reemplazaría por una vacía); sus pruebas están
  en `internal/bot/memoria_test.go`.
- **Agente, método de venta** (indagar → temperatura → una opción → precio + probárselo → cita): la temperatura y la
  cita son reglas de `app/memoria.py` (`temperatura`, `fecha_iso`, `hora_en`, `validar_cita`, `leer_cita`) y se prueban en
  `prueba_memoria.py` con una fecha «hoy» fija; no dejes que el LLM las decida. Los textos de la cita los arma
  `app/venta.py` (`cita_pide`, `cita_invalida`, `cita_ok`) con `seed/venta.json` → `showroom`; no inventes otros datos. El
  horario del showroom vive en `memoria.py` (`ABRE`, `CIERRA`, `REFRIGERIO`), en `tienda.md` y en `venta.json`: si cambia,
  cámbialo en los tres. En Go, la cita llega al tablero por `registrarCita` (`internal/bot/memoria.go`), con su prueba
  `TestCitaQuedaEnElTablero`.
- **Agente, prueba con conversaciones** (`app/conversaciones.py`, solo biblioteca estándar, corre en el host): 200
  conversaciones completas (reales anonimizadas, guiones del cliente y clientas simuladas por una LLM que reaccionan al bot)
  contra un agente de pruebas; reglas deterministas + Jev como juez. Córrelo antes de desplegar un cambio de conversación:
  `python3 -m app.conversaciones correr --conjunto reservada --salida pruebas_conv/X.jsonl` e `informe … --comparar`. El
  corpus y los resultados van en `agente/pruebas_conv/` (**fuera de git**: hay chats reales). Las 50 `reservada` no se
  miran para corregir. Cuesta ~US$ 0,10 por 50 conversaciones con `deepseek-v4-flash`; **la clave de OpenRouter es la de
  producción y tiene tope**: mira el saldo (`--tope`) antes de correr. Detalle en [`agente/README.md`](agente/README.md).
- **Agente, prueba de regresión** (`app/regresion.py`, **sin costo**): 60 preguntas (`data/regresion_preguntas.csv`) y 30
  conversaciones con afirmaciones por turno (`data/regresion_conversaciones.jsonl`) contra un agente de pruebas con
  `usar_llm: false`, más entradas raras (nunca 500) y aguante. Córrela antes de desplegar cualquier cambio de conversación:
  `python3 -m app.regresion --url http://127.0.0.1:18497`. Un fallo nuevo visto en WhatsApp se añade como caso (anonimizado:
  el repo es público). Los casos **no** se usan para entrenar (`--solapes`) ni se borran. Lo que depende del texto del LLM se
  prueba con `"llm": "…"` en el turno (campo `respuesta_llm`, solo con `RESPUESTA_LLM_PRUEBA=1`). Sin LLM contesta
  `respaldo_codigo` (`main.py`): si añades un dato que el bot puede dar (un servicio, un horario), dalo también ahí.
- **Agente, Jev:** las 20 intenciones que ve Jev están descritas en `INTENCIONES` de `app/jev.py`; si se añade
  una intención a `comercial.csv`, va también ahí. Se mide con `python -m app.evaluar_jev --local …` dentro
  del contenedor (llamadas de pago, ~US$ 0,004). Jev nunca decide la etapa: solo entrega intención y confianza
  a `etapas.decidir`.
- **Agente, prompts:** ningún dato de la tienda se inventa en el prompt. Lo que el bot puede afirmar
  está en `seed/tienda.md`, las fichas (precio y tallas) y la línea `AHORA:` (stock).
- **Agente, categorías:** las prendas que se filtran por nombre están en `RE_CATEGORIA` de `main.py`
  (conjunto, enterizo, blazer, falda, jeans, pantalón, polo, blusa, vestido). Si la tienda suma otra, va
  ahí y en `RE_ROPA`.
- **Códigos de producto:** `V` + 2 dígitos (`V21`). Es el formato que reconocen a la vez el bot Go
  (`reCode`) y el agente (`datos.RE_CODIGO`). Otro prefijo exige cambiar ambos.
- **Capa de Juicio:** la decisión de enviar/derivar/callar vive en `backend/internal/juicio` (pura, con sus
  pruebas) y se aplica en `bot/juicio.go` (`JUICIO_MODO`: `sombra` registra, `activo` aplica). El agente solo
  propone. Ánimo y urgencia van por reglas en `agente/app/animo.py`. No metas política de negocio en el
  prompt ni condicionales sueltos en `bot.go`: van en `juicio`.
- **CRM Kommo** (`internal/kommo`, detalle en `CLAUDE.md`): los dos canales pasan por el mismo `Sincronizador`. En el
  bot Go, un paso nuevo que merezca nota para la asesora usa `b.hito(ctx, conv, "…")`, y si toca un pedido,
  `b.crmPedido(ctx, id)`; **nunca** llames a Kommo desde el flujo (todo va en cola, en segundo plano). Un dato nuevo de la
  memoria que deba verse en Kommo va en `kommo.Campos` (se crea solo) y en `Sincronizador.campos`. Las pruebas van contra
  `kommo/simulado`, que falla con cualquier petición mal formada o por encima de 7/s; la de «Kommo caído no cambia las
  respuestas» (`TestKommoCaidoNoCambiaLasRespuestas`) no se toca. El chat web avisa desde `agente/app/crm.py`
  (`python3 -m app.prueba_crm`). El token de Kommo es un secreto como los demás.
- **Seguimiento:** el recordatorio de silencio es un job de fondo apagado por defecto
  (`seguimiento_habilitado`); activarlo escribe a clientas reales, consúltalo antes.
- **Secretos:** nunca los imprimas ni los subas. `.env` está en `.gitignore`.
- **WhatsApp:** no mandes mensajes de prueba a números reales sin consultar antes.
