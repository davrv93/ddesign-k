# Contrato — Fuentes, promos y embudos (JMD Ventas ↔ panel ↔ agente)

Plan: `PjgFactSalud_completo/PLAN_TRABAJO_FUENTES_Y_PROMOS_JMDVENTAS.md` (15-10-2026). Este documento fija las formas JSON
que comparten el backend Go (`backend/`), el panel Qwik (`frontend/`) y el agente Python (`ddesign-k-v2/agente`).

## 1. Fuente

Todas las rutas van bajo `/<empresa>/api/…` (multiempresa). `p` = cualquier usuario con sesión; `a` = solo admin.

```jsonc
{
  "id": 12,
  "tipo": "promo",                 // promo | faq | guion | politica | material
  "titulo": "Promo pelotera",
  "contenido": "markdown libre",   // lo que el robot puede decir; en faq, notas internas
  "estado": "activo",              // borrador | activo | vencido | archivado | propuesta | descartada
  "vigente_desde": "2026-10-15",   // "" = sin fecha (fecha de Lima, inclusive)
  "vigente_hasta": "2026-10-31",   // "" = sin fecha (inclusive)
  "vigente": true,                 // calculado: activo y hoy dentro de las fechas
  "programada": false,             // calculado: activo pero vigente_desde todavía no llega
  "etiquetas": ["verano"],
  "datos": { },                    // estructura según el tipo (abajo)
  "origen": "manual",              // manual | archivo:<nombre> | propuesta_bot
  "archivo": "",                   // ruta del original subido (solo informativa)
  "version": 3,
  "actualizado_por": "Ana",
  "actualizado_en": "2026-10-15T14:00:00Z",
  "creado_en": "2026-10-14T10:00:00Z"
}
```

`datos` de una **promo**:

```jsonc
{
  "mecanica": "porcentaje",        // porcentaje | monto | precio_especial | dos_por | envio_gratis | regalo | otra
  "valor": 20,                     // % · soles de descuento · precio final · precio por 2 unidades · (sin uso)
  "alcance": {"tipo": "categoria", "categoria": "vestido", "coleccion": "", "codigos": []},
                                   // tipo: todo | categoria (vestido, blusa, enterizo, pantalon, conjunto, blazer, falda)
                                   //       | coleccion (handle de baruka.pe: vestidos, vestido-cortos, enterizos…) | prendas
  "condiciones": "Solo pago por Yape. Hasta agotar stock.",
  "mensaje_apertura": "¡Esta semana los vestidos tienen 20% de descuento! 🎉",
  "respuesta_promo": "Sí 😊 Esta semana tenemos …",     // respuesta a «¿tienen promo?»
  "respuesta_fuera": "Esa prenda no entra en la promo, …", // la prenda pedida no entra
  "objetivo": "Vender 30 vestidos",
  "meta": 30,
  "publico": "todas",              // todas | primera_vez | que_compraron
  "embudo_id": 0                   // 0 = el embudo general
}
```

`datos` de una **faq**: `{"pregunta": "¿Hacen factura?", "variantes": ["dan factura?"], "respuesta": "Sí, …", "ocurrencias": 4}`
(`ocurrencias` solo en las propuestas del robot).

`guion`, `politica`, `material`: `datos` vacío; todo va en `contenido` (markdown). El guion usa encabezados `## Paso N`.

### Rutas

| Método y ruta | Rol | Qué hace |
|---|---|---|
| `GET /api/fuentes?tipo=&estado=&q=` | p | Lista (sin `contenido` largo: se recorta a 300 caracteres en `resumen`) |
| `GET /api/fuentes/{id}` | p | Una fuente completa |
| `POST /api/fuentes` | a | Crea (estado por defecto `borrador`). Cuerpo: los campos editables |
| `PUT /api/fuentes/{id}` | a | Guarda; si cambió algo sube `version` y guarda la anterior |
| `POST /api/fuentes/{id}/estado` | a | `{"estado": "activo" \| "borrador" \| "archivado" \| "descartada"}` (aprobar una propuesta = `activo`) |
| `DELETE /api/fuentes/{id}` | a | Borra |
| `GET /api/fuentes/{id}/versiones` | p | `[{version, titulo, contenido, datos, autor, creado_en}]`, la más nueva primero |
| `POST /api/fuentes/subir` | a | multipart: `archivo`, `tipo`, `titulo` → crea un borrador con el texto extraído (pdf, docx, txt, md, csv) |
| `GET /api/conocimiento` | p | Lo que recibe el robot ahora mismo (§3) — vista previa |
| `GET /api/promos/resumen` | p | `[{id, titulo, estado, vigente, programada, vigente_desde, vigente_hasta, dias_restantes, embudo_id, cifras: {conversaciones, pedidos, confirmados, pagados, monto}, avisos: ["Vence en 2 días"]}]` |
| `GET /api/conversations/{id}/fuentes` | p | `[{fuente_id, titulo, tipo, creado_en}]`: con qué fuentes respondió el robot en esa conversación |

Errores: `{"error": "mensaje"}` con 400/403/404/409/500, como el resto de la API.

## 2. Embudos

El embudo **general** es el tablero de pedidos de siempre (estados `consulta → … → cancelado`); no se guarda y tiene `id: 0`.
Un embudo propio tiene etapas libres y **no cambia el estado del pedido** (el stock sigue dependiendo de `status`): la
etapa del embudo va aparte.

```jsonc
{"id": 3, "nombre": "Promo pelotera", "etapas": ["Interesada", "Cotizada", "Separada", "Pagada"], "promo_id": 12,
 "general": false, "kommo_pipeline_id": 0, "creado_en": "…"}
```

| Método y ruta | Rol | Qué hace |
|---|---|---|
| `GET /api/embudos` | p | `[general, …propios]` |
| `POST /api/embudos` | a | `{nombre, etapas, promo_id}` → crea (y el espejo en Kommo si está encendido) |
| `PUT /api/embudos/{id}` | a | Cambia nombre o etapas |
| `DELETE /api/embudos/{id}` | a | Borra un embudo propio (sus pedidos vuelven solo al general) |
| `GET /api/embudos/{id}/pedidos` | p | `{"embudo": {…}, "pedidos": [{…Order de /api/orders, "etapa": "Cotizada"}]}` |
| `PUT /api/embudos/{id}/pedidos/{orderId}` | p | `{"etapa": "Separada"}`: mueve (o mete) el pedido en ese embudo |
| `DELETE /api/embudos/{id}/pedidos/{orderId}` | p | Lo saca del embudo |

Un pedido entra solo al embudo de una promo cuando nace de una conversación en la que el robot ofreció esa promo; queda en
la primera etapa hasta que alguien lo mueva.

## 3. Conocimiento (lo que recibe el agente)

El bot manda `conocimiento` en **cada** `POST /chat` y `POST /foto` al agente (como hoy `colores_grupos`). Solo lleva
fuentes **vigentes**. `version` es un hash del contenido: el agente cachea lo derivado (índice de FAQ) por
`(empresa, version)`. Sin `conocimiento` (o vacío), el agente se comporta como siempre (seed).

```jsonc
"conocimiento": {
  "empresa": "baruka",
  "version": "9f2c1a0b7d3e4f51",
  "hoy": "2026-10-15",                       // fecha de Lima
  "promos": [{"id": 12, "titulo": "…", "contenido": "…", "vigente_desde": "…", "vigente_hasta": "…", …datos de la promo}],
  "faqs": [{"id": 3, "pregunta": "…", "variantes": ["…"], "respuesta": "…"}],
  "politicas": [{"id": 4, "titulo": "…", "contenido": "…"}],
  "guion": {"id": 5, "titulo": "…", "contenido": "## Paso 1 …"},   // null si la tienda no cargó uno
  "material": [{"id": 6, "titulo": "…", "contenido": "…"}]
}
```

La respuesta del agente suma `"fuentes_usadas": [12, 3]` (ids de las fuentes en las que se apoyó en ese turno). El bot
guarda cada uso (conversación + fuente) y, si una es promo, la conversación queda asociada a esa promo.

### Precio de promo (lo calcula el código del agente, nunca el LLM)

| mecanica | precio final de una prenda de precio P |
|---|---|
| porcentaje | `round(P × (1 − valor/100), 2)` |
| monto | `max(P − valor, 0)` |
| precio_especial | `valor` |
| dos_por | `P` la unidad; `valor` por dos unidades |
| envio_gratis | `P`; el envío cuesta 0 |
| regalo, otra | `P` (beneficio en `condiciones`) |
