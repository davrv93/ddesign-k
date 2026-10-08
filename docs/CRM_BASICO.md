# JMD Ventas como CRM: lo básico frente a lo que hay

Auditoría del 07-10-2026 sobre la rama `feat/jmdventas-multitenant` (commit `f214807`), y lo que se implementó
en `feat/jmdventas-crm`. Mide el panel (Qwik, `frontend/`) y la API (Go, `backend/`) contra las ocho funciones que
cualquier CRM trae de serie.

## Punto de partida

El panel nació como tablero de pedidos de un bot de WhatsApp, no como CRM. Lo que ya tenía:

- **Pedidos**: kanban de 7 estados con arrastrar y soltar, reserva y devolución de stock, pedido manual, una nota libre
  por pedido (`orders.notes`) y 5 indicadores encima del tablero (`/api/stats`).
- **Conversaciones**: bandeja con búsqueda, hilo, respuesta manual, pausar o reactivar el bot y los pedidos de la
  clienta como pastillas.
- **Productos y stock**, **ficha técnica**, **catálogo público**, **ajustes del bot**, **WhatsApp por QR** y
  **Kommo** (opcional, en segundo plano).
- **Multiempresa**: `tenant_id` en todas las tablas y un `Store` ligado a la empresa, con pruebas de aislamiento.

## Brechas

| # | Función básica | Antes | Brecha |
|---|---|---|---|
| 1 | **Clientas y contactos** | Tabla `customers` con jid, teléfono y nombre. Sin pantalla: la clienta solo existía dentro de un pedido o de una conversación. | No había lista, búsqueda, filtros, ficha, edición, total comprado ni última compra. Sin correo ni ciudad. Sin etiquetas. |
| 2 | **Notas internas** | Un único texto libre por pedido (`orders.notes`), que se sobrescribe. | Nada en la clienta. Sin autor ni fecha: no es un registro, es un campo. |
| 3 | **Tareas y recordatorios** | Nada. | Sin tareas, fechas de vencimiento ni responsable; sin «Mis tareas» ni «Vencen hoy». |
| 4 | **Embudo** | La etapa comercial (prospección → seguimiento → cierre → venta confirmada) vive en el JSON `conversations.context` y la decide el agente. Solo se ve en Kommo. | Invisible en el panel y no editable a mano. Se pierde al reactivar el bot (el contexto se vacía). |
| 5 | **Historial de actividad** | Mensajes y pedidos, cada uno en su pantalla. | Sin línea de tiempo por clienta; los cambios de estado y de etapa no quedan registrados en ninguna parte. |
| 6 | **Usuarios y roles** | Tabla `users` con columna `role` (siempre `admin`), alta solo por consola (`jmd alta`). | Sin pantalla de equipo, sin rol asesora, sin permisos (cualquiera borra pedidos o cambia ajustes), sin asignar clienta o pedido a una persona. `/api/me` no decía quién eras. |
| 7 | **Panel de inicio** | 5 cifras encima del kanban: consultas, por confirmar, por despachar, ventas del mes, stock bajo. | Sin clientas nuevas, conversión por etapa, tareas vencidas ni pedidos por estado de un vistazo. La portada era el kanban. |
| 8 | **Exportar CSV** | Nada. | Ni clientas ni pedidos. |

## Lo implementado (MVP)

Todo por empresa: cada consulta lleva `tenant_id=?` y cada recurso nuevo tiene su prueba de aislamiento entre dos
empresas (`internal/store/crm_test.go` en SQLite y MariaDB, `internal/api/crm_panel_test.go` por HTTP).

1. **Clientas** (`/clientas`): lista con búsqueda (nombre, teléfono, correo, ciudad), filtros por etapa, etiqueta,
   asesora y «con compra / sin compra», con pedidos, total comprado y última compra. Ficha (`/clientas?c=<id>`) con
   datos editables (nombre, teléfono, correo, ciudad), etiquetas, asesora, etapa, indicadores, pedidos, enlace a la
   conversación, notas, tareas y línea de tiempo.
2. **Notas internas** (tabla `notas`): en la clienta y en el pedido, con autor y fecha; las borra su autora o una
   admin. La nota libre de siempre del pedido (`orders.notes`) se conserva.
3. **Tareas** (tabla `tareas`, pantalla `/tareas`): título, vencimiento, responsable, clienta o pedido opcional;
   pestañas «Mis tareas», «Vencen hoy», «Vencidas», «Todas» y «Hechas». Se crean también desde la ficha.
4. **Embudo**: columna `customers.etapa`. El bot la sigue moviendo (al guardar el contexto de la conversación) salvo
   que una persona la haya fijado a mano (`etapa_fijada`); «Automática» le devuelve el control al bot. Etapas:
   prospección, seguimiento, cierre, venta confirmada y perdida.
5. **Línea de tiempo** (`GET /api/clientas/{id}/actividad`): mensajes, pedidos creados, cambios de estado del pedido,
   cambios de etapa, asignaciones, notas y tareas, en orden. Los cambios se registran en la tabla `actividad` desde el
   `store` (también los que hace el bot), así que no dependen de qué pantalla los provoque.
6. **Usuarios y roles** (`Ajustes → Equipo`): admin y asesora. La asesora atiende (clientas, notas, tareas, pedidos,
   conversaciones) pero no cambia ajustes ni WhatsApp, no gestiona el equipo, no borra pedidos ni productos y no
   exporta. El rol se lee de la base en cada petición: un cambio vale al instante. Asignación de clienta y de pedido
   a una persona del equipo. No se puede quitar la última admin ni desactivarse a una misma.
7. **Panel de inicio** (`/`): ventas del mes, pedidos del mes, clientas nuevas, ticket medio, pedidos por estado,
   embudo con conversión por etapa, tareas vencidas y de hoy, y las clientas sin atender. El kanban pasó a
   `/pedidos` (el enlace viejo `/?pedido=N` redirige).
8. **CSV**: `GET /api/export/clientas.csv` y `/api/export/pedidos.csv` (solo admin), UTF-8 con BOM para Excel y
   celdas que empiezan por `= + - @` neutralizadas.

## Migración

Sin pasos a mano: al arrancar, el backend crea las tablas `notas`, `tareas`, `actividad` y `cliente_etiquetas` y
añade a `customers` (`email`, `ciudad`, `etapa`, `etapa_fijada`, `asesora_id`) y a `orders`
(`asesora_id`) las columnas que falten (`ALTER TABLE … ADD COLUMN IF NOT EXISTS` en MariaDB, `pragma_table_info` en
SQLite), igual que se hizo con `products.ficha`. Todo con valor por defecto: los datos existentes no cambian. La etapa
de las clientas que ya conversaron se rellena una vez desde `conversations.context`.

## Pendiente (fuera de este MVP)

- Importar clientas desde CSV.
- Recordatorios que avisen solos (correo o WhatsApp): hoy la tarea se ve en el panel, no notifica.
- Campos personalizados por empresa y segmentos guardados.
- Ver solo «mis clientas» como restricción para la asesora (hoy ve todas y filtra por asesora).
- Fusionar clientas duplicadas (el mismo número por dos canales).
- Que Kommo reciba las notas, tareas y etapas manuales (hoy solo sale lo del bot).
