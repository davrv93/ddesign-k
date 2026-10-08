# LMS para programación agéntica — prompt y contexto

Este documento tiene tres partes:

1. **El prompt.** La versión lista para pegar, con el contrato ya dentro, está en
   [`LMS_PROMPT_COMPLETO.txt`](LMS_PROMPT_COMPLETO.txt). Pégala en una sesión nueva (Claude Code u
   otro agente) dentro de un repo vacío para el LMS.
2. **El contrato LMS ⇄ `jmd` ⇄ gateway.** Lo que las dos partes deben respetar para trabajar en
   paralelo sin pisarse. Va también dentro del prompt.
3. **Qué se hace del lado de `jmd` y del gateway** (este repo), para que sepas qué esperar.

---

## 1. Prompt

````text
Eres el ingeniero principal de un LMS (plataforma de cursos) para enseñar PROGRAMACIÓN AGÉNTICA
(«vibecoding»): los alumnos programan con agentes de IA (Claude Code, OpenCode y nuestro CLI `jmd`)
y la plataforma es donde ven las clases, los materiales, el código de cada sesión, hacen preguntas,
entregan tareas y reciben su nota. Construye el proyecto completo en este repositorio, por fases,
con pruebas, y documenta cada decisión en docs/DECISIONES.md.

## Quién lo usa
- Instructor (yo): crea cursos, módulos y sesiones; publica grabaciones de Zoom, PPT por objetivo
  de aprendizaje, repos de código por sesión; crea tareas con rúbrica; califica; responde preguntas.
- Alumno: entra con su cuenta (SSO), ve sus cursos y materiales, ve el código de cada sesión,
  pregunta, entrega tareas (repo git + commit), ve su nota, y hace todo eso también desde la
  terminal con `jmd` (nuestro CLI en Rust) y desde su agente (Claude Code/OpenCode) vía MCP.
- Admin: gestiona usuarios, cohortes y cuotas de IA.

## Stack (decidido; si algo no encaja, propón el cambio en DECISIONES.md antes de hacerlo)
- App: Next.js (App Router) + TypeScript estricto + Tailwind + shadcn/ui. Server Actions solo para
  la UI; toda la funcionalidad también expuesta como API REST versionada (/api/v1, OpenAPI 3.1
  generado desde el código con zod) porque la consumen `jmd` y el servidor MCP.
- Base de datos: PostgreSQL 16 + Drizzle ORM + migraciones versionadas.
- Identidad: Keycloak (OIDC) como servidor de autorización. NO implementes OAuth a mano.
  Federación con Google, GitHub y Microsoft como proveedores de identidad de Keycloak.
  La web usa Auth.js (next-auth v5) con el proveedor Keycloak.
- Archivos: Garage (S3 compatible, autoalojado) con el SDK de AWS v3 (forcePathStyle, region
  "garage"). Subidas y descargas con URLs prefirmadas de corta vida; nunca archivos públicos.
- Conversión de PPT/PPTX/DOCX a PDF para verlos en el navegador: Gotenberg (LibreOffice headless).
- Visor de código: Shiki para lectura (resaltado en servidor) y Monaco para edición y diffs.
- Git: lectura de cualquier repo HTTPS (GitHub, GitLab, Forgejo/Gitea) clonando en el servidor con
  `git` (clon parcial: --filter=blob:none) en una caché; API del proveedor solo para extras.
- Compilar y previsualizar:
  * Web (HTML/CSS/JS/TS/React/Vite): en el navegador con Sandpack (open source). No uses StackBlitz
    WebContainers sin revisar su licencia comercial.
  * Lenguajes compilados (Python, Rust, Go, Java, C/C++, Node…): un servicio «runner» propio en
    contenedores efímeros con gVisor (runsc), sin red, CPU/memoria/tiempo limitados, sistema de
    archivos de solo lectura salvo /work. Cola de trabajos (pg-boss sobre Postgres) y resultados
    en streaming (SSE).
  * Preview de proyectos web de un repo: build en el runner → artefacto estático en Garage →
    servido en preview.<dominio>/<id> con cabeceras CSP estrictas y en otro origen.
- Video: las sesiones de Zoom se publican por ENLACE (share link + código). Zoom no permite
  incrustar su visor: abre en pestaña nueva. Fase posterior: importar la grabación (webhook
  recording.completed + app Server-to-Server OAuth de Zoom) a Garage y servirla con un reproductor
  propio (HLS) con marcas de tiempo enlazables.
- Todo levanta con `docker compose up` (y debe funcionar igual con `podman compose`): postgres,
  keycloak (con el realm importado desde un JSON versionado), garage, gotenberg, app, runner.
- Pruebas: Vitest (unidad), Playwright (punta a punta, incluido el login), pruebas de contrato
  de la API contra el OpenAPI.

## Módulos y criterios de aceptación
1. Identidad y roles
   - Login web con SSO (Google/GitHub/Microsoft o cuenta local de Keycloak). Roles del realm:
     admin, instructor, student; grupos = cohortes.
   - Cliente público `jmd-cli` con Authorization Code + PKCE (S256) y redirección loopback
     http://127.0.0.1:{puerto}/callback con cualquier puerto (RFC 8252), y Device Authorization
     Grant (RFC 8628) para terminales sin navegador (SSH, Codespaces, WSL). Ver el CONTRATO.
   - Refresh tokens con rotación; cierre de sesión que revoca.
2. Cursos, módulos, sesiones y objetivos
   - Curso → módulos → sesiones. Cada sesión tiene objetivos de aprendizaje; cada objetivo puede
     tener materiales adjuntos (PPT/PDF/enlaces/notas en Markdown).
   - Publicación programada y borradores. Orden por arrastrar y soltar.
3. Materiales
   - Subida directa a Garage con URL prefirmada; PPT/PPTX se convierte a PDF con Gotenberg y se ve
     en un visor de diapositivas en el navegador (PDF.js), con descarga del original.
   - Cada material ligado a uno o varios objetivos. Búsqueda de texto completo (Postgres FTS).
4. Grabaciones
   - Enlace de Zoom + código (cifrado en reposo) + duración + resumen + capítulos con marcas de
     tiempo. Las preguntas de los alumnos pueden citar un minuto concreto.
5. Código de la sesión
   - Cada sesión enlaza un repo y una referencia (rama, tag o commit). Árbol de archivos, visor con
     resaltado, historial de commits de la sesión y diff entre dos sesiones o dos commits.
   - Botón «Abrir en terminal»: muestra el comando `jmd lesson open <id>` (ver CONTRATO).
   - Botón «Ejecutar»: Sandpack si es web, runner si no.
6. Preguntas
   - Hilos por sesión y por objetivo, con Markdown, bloques de código resaltados, mención a una
     línea de código del repo o a un minuto de la grabación. Marcar como resuelta. Votos.
7. Tareas y calificación
   - Tarea con enunciado, objetivos evaluados, fecha límite, rúbrica (criterios × niveles × puntos)
     y pruebas automáticas opcionales (comando que corre en el runner contra el repo entregado).
   - Entrega = URL del repo + commit SHA (inmutable) + notas; también archivos sueltos.
   - Autocalificación con el runner + calificación manual con la rúbrica + comentarios en líneas
     de código. Nota final, historial y exportación CSV.
   - Las entregas también llegan desde `jmd submit` (ver CONTRATO).
8. Servidor MCP del LMS (para Claude Code/OpenCode)
   - Expuesto en /mcp (HTTP transmisible), autenticado con el mismo token OIDC.
   - Herramientas: list_courses, get_lesson, get_objectives, get_materials (texto extraído),
     get_assignment, submit_assignment, list_my_grades, ask_question. Solo lectura salvo submit y ask.
9. Panel
   - Alumno: próximas entregas, progreso por objetivo, notas. Instructor: entregas pendientes,
     preguntas sin responder, actividad. Admin: usuarios, cohortes, cuotas de IA por cohorte.

## Seguridad (no negociable)
- Autorización en el servidor en cada endpoint (RBAC + pertenencia al curso); pruebas que lo cubran.
- URLs prefirmadas de ≤ 10 min; claves de objeto no adivinables; el bucket nunca es público.
- El runner y las previews son código no confiable: aislamiento gVisor, sin red, límites, otro
  origen para las previews, CSP, sin cookies de la app en ese origen.
- Secretos solo en variables de entorno; nada de secretos en el repo ni en logs.
- Validación con zod en toda entrada; límites de tamaño; rate limiting en login y en la API.

## Fases (entrega cada una funcionando, con pruebas y una nota en docs/PROGRESO.md)
0. Infraestructura: compose con postgres, keycloak (realm exportado en infra/keycloak/realm.json
   con clientes lms-web, jmd-cli y ai-gateway, roles y un usuario de prueba por rol), garage
   (bucket y clave creados por un script de arranque), gotenberg. README con el arranque.
1. Identidad + cursos/módulos/sesiones/objetivos + API /api/v1/me y /courses.
2. Materiales (subida, conversión PPT, visor) + grabaciones.
3. Código de la sesión (git, árbol, visor, diffs) + Sandpack.
4. Preguntas.
5. Tareas, entregas, rúbricas, runner y calificación.
6. Servidor MCP + endpoints del CONTRATO completos + pruebas de contrato.
Antes de cada fase, escribe el plan en PROGRESO.md; al terminarla, lo que quedó hecho y lo que no.

## Reglas de trabajo
- No inventes APIs de terceros: si no estás seguro de una opción de Keycloak, Garage, Gotenberg,
  Sandpack o gVisor, consulta su documentación y cita la versión.
- Cada endpoint del CONTRATO lleva su prueba de contrato. Si necesitas cambiar el contrato, NO lo
  cambies tú solo: anótalo en docs/CONTRATO_CAMBIOS.md y avisa, porque `jmd` lo implementa en paralelo.
- Commits pequeños y descriptivos. No subas .env.
- Interfaz en español; código e identificadores en inglés.

## CONTRATO (copia literal en docs/CONTRATO.md; es la fuente de verdad compartida con `jmd`)
<pega aquí la sección 2 de este documento>
````

---

## 2. Contrato LMS ⇄ `jmd` ⇄ gateway (v1)

### 2.1 Identidad (Keycloak, realm `lms`)

| Cosa | Valor |
|---|---|
| Issuer | `https://auth.<dominio>/realms/lms` (descubrimiento en `/.well-known/openid-configuration`) |
| Cliente CLI | `jmd-cli`, **público**, sin secreto |
| Flujo principal | Authorization Code + PKCE `S256` |
| Redirección | `http://127.0.0.1:{puerto}/callback`, con cualquier puerto (loopback, RFC 8252). `jmd` abre un puerto libre, lanza el navegador y espera el `code` |
| Flujo sin navegador | Device Authorization Grant (`jmd login --device`): muestra la URL y el código |
| Scopes | `openid profile email offline_access lms:read lms:submit ai:use` |
| Audiencias del access token | `lms-api` (el LMS) y `ai-gateway` (el gateway de IA). Un *audience mapper* en Keycloak añade las dos |
| Claims usados | `sub`, `email`, `name`, `realm_access.roles` (`admin` · `instructor` · `student`), `groups` (cohortes, p. ej. `/cohorte-2026-1`) |
| Vida | access token 15 min · refresh 30 días con rotación · revocación en `jmd logout` |
| Guardado en el cliente | llavero del sistema (macOS Keychain, Windows Credential Manager, Secret Service); si no hay llavero, `~/.config/jmd/tokens.json` con permisos 600 |

### 2.2 API del LMS para `jmd` y para MCP (`/api/v1`, Bearer = access token)

Errores con el mismo formato en todos los endpoints:
`{"error": {"code": "not_found", "message": "…"}}` y el código HTTP que corresponda.

| Método y ruta | Respuesta (lo mínimo que `jmd` usa) |
|---|---|
| `GET /api/v1/me` | `{id, email, name, roles: [...], cohorts: [...]}` |
| `GET /api/v1/courses` | `[{id, slug, title, role}]` |
| `GET /api/v1/courses/{id}` | `{id, title, modules: [{id, title, lessons: [{id, title, starts_at, published}]}]}` |
| `GET /api/v1/lessons/{id}` | `{id, title, objectives: [{id, title}], recording: {url, passcode?} \| null, repo: {url, ref} \| null, materials: [{id, title, kind, objective_ids}]}` |
| `GET /api/v1/materials/{id}/download` | `{url, expires_at, filename}`: URL prefirmada (Garage) |
| `GET /api/v1/assignments?course={id}` | `[{id, title, due_at, status}]` |
| `GET /api/v1/assignments/{id}` | `{id, title, description_md, due_at, rubric: [...], autograde: bool}` |
| `POST /api/v1/assignments/{id}/submissions` | Cuerpo `{repo_url, commit_sha, notes?}` → `201 {id, status: "queued"}` |
| `GET /api/v1/submissions/{id}` | `{id, status: queued\|running\|graded\|failed, score?, max_score?, feedback_md?, checks: [...]}` |
| `GET /api/v1/grades?course={id}` | `[{assignment_id, title, score, max_score, graded_at}]` |
| `POST /api/v1/lessons/{id}/questions` | Cuerpo `{body_md, code_ref?: {path, line}, video_ts?: seconds}` → `201 {id, url}` |

### 2.3 Comandos de `jmd` que dependen del LMS (los implementa este repo)

| Comando | Hace |
|---|---|
| `jmd login --sso [--issuer URL]` / `--device` | OIDC según 2.1; guarda los tokens; `jmd whoami` muestra `/me` |
| `jmd logout` | Revoca el refresh token y lo borra |
| `jmd courses` · `jmd course <id>` | Lista cursos y su temario |
| `jmd lesson <id>` | Objetivos, materiales, grabación, repo |
| `jmd lesson open <id>` | `git clone` del repo en la ref de la sesión y descarga de los materiales a `./materiales/` |
| `jmd materials pull <lesson>` | Solo los materiales |
| `jmd assignments` · `jmd assignment <id>` | Tareas y enunciado |
| `jmd submit <assignment>` | Envía el `origin` y el `HEAD` del repo actual (pide confirmación si hay cambios sin commit o sin push) y sigue el estado hasta la nota |
| `jmd grades` | Notas |
| `jmd ask <lesson> "…"` | Publica una pregunta; con `--line archivo:N` cita código |
| `jmd setup claude\|opencode` | Además de lo actual, registra el servidor MCP del LMS en el agente |

### 2.4 Gateway de IA con la misma cuenta

- El gateway acepta, además de las claves estáticas, **access tokens del realm** con audiencia
  `ai-gateway`. Los valida con el JWKS del issuer (con caché) y comprueba `exp`, `iss` y `aud`.
- Cuotas por usuario y por cohorte (peticiones/día y tokens/día) configurables en su UI. La
  telemetría registra `sub` y la cohorte, y no guarda el email.
- `jmd chat`, Claude Code y OpenCode usan el token del alumno. `jmd` lo refresca antes de que
  venza, y para Claude Code se usa `apiKeyHelper`, un comando que devuelve un token fresco.

---

## 3. Qué hago yo en paralelo (este repo)

1. `jmd login --sso` / `--device`, `logout`, `whoami` y guardado en el llavero, probado contra un
   Keycloak de prueba en Docker.
2. Validación de JWT en el gateway, con cuotas por usuario y cohorte y una pestaña en la UI.
3. Los comandos de 2.3, contra un LMS falso que implementa el contrato, para no esperar al real.
4. `jmd setup` registra el MCP del LMS y configura `apiKeyHelper` en Claude Code.

Si el agente del LMS necesita cambiar algo del contrato, debe anotarlo en
`docs/CONTRATO_CAMBIOS.md` de su repo; yo lo adapto aquí.
