# Diseño del chat estilo WhatsApp

El chat de prueba del agente (`https://proyectopostventa.site/demo-design/`) imita una conversación de WhatsApp en
el móvil. Este documento describe su diseño: estructura, colores, componentes, ritmo de los mensajes, adaptación a
cada pantalla y accesibilidad. También explica cómo se corresponde con lo que la clienta ve en el WhatsApp real.

Todo vive en un solo archivo, [`agente/app/ui.html`](agente/app/ui.html): HTML, CSS y JavaScript sin dependencias
ni build. Lo sirve el agente (`GET /`), así que cambiarlo solo pide reconstruir `kddesign_agente`, que tarda
segundos.

Arquitectura general del sistema: [`ARQUITECTURA.md`](ARQUITECTURA.md).

---

## 1. Principios

1. **Que parezca WhatsApp, no una demo.** La clienta (o el cliente al que se le enseña) debe reconocer la app al
   primer vistazo: cabecera verde, fondo con garabatos, globos con cola, hora y checks, barra de escritura con
   micrófono.
2. **Los controles de la demo se esconden.** Motor, «Desde anuncio», respuestas rápidas y análisis viven en el menú
   ⋮, como cualquier opción de la app. La cabecera sigue siendo la de un chat normal.
3. **Mismo contrato que el canal real.** La página llama a `POST /chat` y `POST /foto` con los mismos campos que
   usa el backend Go para WhatsApp. Lo que se prueba aquí es lo que contesta el bot de verdad.
4. **El ritmo de una persona.** Cada párrafo sale como un mensaje, con «escribiendo…» entre uno y otro. La pregunta
   final va después de la foto.
5. **Nada que la demo no haga.** Voz, llamada y videollamada están dibujadas, pero al tocarlas sale un aviso
   breve que dice que no existen en la demo.

---

## 2. Estructura

```
┌──────────────────────────────────────────┐
│ ←  (BD)  Baruka Design          📹 📞 ⋮  │  cabecera  · «en línea» / «escribiendo…»
│          en línea                        │
├──────────────────────────────────────────┤
│                 [ HOY ]                  │  chip de día
│   ✨ Demo del asistente de Baruka …      │  aviso amarillo
│                                          │
│ ┌──────────────────┐                     │  globo del bot (blanco, cola a la izquierda)
│ │ ¡Hola! ¿Qué estás│                     │
│ │ buscando?   10:21│                     │
│ └──────────────────┘                     │
│                    ┌───────────────────┐ │  globo de la clienta (verde claro, cola a la derecha)
│                    │ Un vestido  10:21✓✓│ │
│                    └───────────────────┘ │
│ ┌──────────────────┐                     │  tarjeta: foto 4:5 + título + botones de talla
│ │     [ foto ]     │                     │
│ │ *V35* Irla       │                     │
│ │ Elige tu talla 👇│                     │
│ └──────────────────┘                     │
│ [  S  ][  M  ][ L̶ ]                      │  botones de respuesta colgados del globo
├──────────────────────────────────────────┤
│ (Hola, quisiera…) (Es para un matri…) →  │  respuestas rápidas (deslizables, opcionales)
│ ┌────────────────────────────┐  ┌──┐     │
│ │ 😊 Escribe un mensaje  📎 📷│  │🎤│     │  barra de escritura · 🎤 se vuelve ➤ al escribir
│ └────────────────────────────┘  └──┘     │
└──────────────────────────────────────────┘
```

Capas superpuestas, por orden de `z-index`:

1. cuerpo: fondo fijo y mensajes que corren por encima;
2. cabecera (3);
3. velo (7) y cajón de análisis (8);
4. menú ⋮ (9);
5. visor de fotos (12);
6. aviso breve o *toast* (14).

---

## 3. Colores

Todos los colores son variables de `:root`. El tema oscuro se activa solo si el sistema va en oscuro
(`prefers-color-scheme`), igual que la app. Los valores copian los de WhatsApp.

| Variable | Uso | Claro | Oscuro |
|---|---|---|---|
| `--cab` | Cabecera | `#008069` | `#202c33` |
| `--franja` | Franja verde del escritorio | `#00a884` | `#0a1014` |
| `--fuera` | Fondo alrededor de la ventana | `#d9dbd5` | `#0a1014` |
| `--wall` | Fondo del chat | `#efeae2` | `#0b141a` |
| `--garabato` | Color del patrón de fondo | `rgba(90,66,30,.115)` | `rgba(255,255,255,.055)` |
| `--in` | Globo del bot y botones de respuesta | `#ffffff` | `#202c33` |
| `--out` | Globo de la clienta | `#d9fdd3` | `#005c4b` |
| `--tinta` | Texto | `#111b21` | `#e9edef` |
| `--muted` / `--meta` | Texto secundario / hora | `#667781` / `#5b6b74` | `#8696a0` / `#a9b6bd` |
| `--tick` / `--tick-gris` | Checks leído / enviado | `#53bdeb` / `#8696a0` | igual |
| `--enlace` | Texto de los botones de respuesta | `#0277ab` | `#53bdeb` |
| `--verde` | Botón de enviar, barras del análisis | `#00a27e` | `#00a884` |
| `--aviso` | Aviso del sistema («Demo del asistente…») | `#ffeecd` | `#182229` (texto `#ffd279`) |
| `--pill` | Campo de texto y bandeja de emojis | `#ffffff` | `#2a3942` |

Además:

- `theme-color` pinta la barra del navegador móvil del mismo color que la cabecera (`#008069` o `#202c33`).
- El avatar «BD» va en granate `#8a3b55`, el color de la marca, para que no parezca un contacto genérico.
- El favicon es el globo de WhatsApp en verde.

### Patrón de fondo

El fondo es un SVG propio en `--patron`, con tesela de 288 × 288 que se pinta a 236 px. Usa motivos de una tienda
de ropa en trazo fino: blusa, percha, bolsa de compras, regalo, diamante, corazón, cámara, flor, estrella, luna,
nube, nota musical y frasco. Se aplica como máscara (`mask`) sobre el color `--garabato`. Así, un solo dibujo sirve
para los dos temas cambiando solo el color. Sin soporte de `mask-image`, el fondo queda liso.

---

## 4. Tipografía y medidas

- **Fuente:** la del sistema (`-apple-system`, Segoe UI, Roboto…) y emojis de color del sistema, como la app.
- **Tamaños:**

| Elemento | Tamaño |
|---|---|
| Texto base | 15/20 px |
| Nombre del contacto | 16,5 px, peso 600 |
| Estado bajo el nombre | 13 px |
| Hora dentro del globo | 11 px |
| Chips y avisos | 12,5 px |
| Campo de texto | 16 px (evita que iOS haga zoom al enfocar) |

- **Medidas:**

| Elemento | Medida |
|---|---|
| Cabecera | 60 px más el área segura superior |
| Botones de icono | 40 × 40 px |
| Campo de texto y botón redondo | 46 px de alto |
| Botones de respuesta | Mínimo 40 px de alto |
| Opciones del menú | Mínimo 44 px de alto |
| Radio de los globos | 7,5 px |
| Ancho máximo de un globo | `min(84 %, 400px)` |
| Ancho máximo de una tarjeta con foto | `min(80 %, 290px)` |

---

## 5. Componentes

### 5.1 Cabecera

- Contiene: flecha atrás (decorativa), avatar, nombre y estado, videollamada, llamada y ⋮.
- El estado cambia de «en línea» a «escribiendo…» mientras el bot prepara cada mensaje. Usa `aria-live="polite"`
  para que lo anuncie un lector de pantalla.
- Por debajo de 350 px de ancho, los iconos se estrechan y desaparece la cámara de la barra de escritura.

### 5.2 Menú ⋮

Es la puerta a los controles de la demo:

| Opción | Tipo | Efecto |
|---|---|---|
| Nuevo chat | acción | Borra historial, etapa, memoria y abre otra sesión del CRM (`web-<16 hex>`) |
| Desde anuncio | casilla | Simula la llegada por el anuncio de clic a WhatsApp (vestido V42) |
| Motor: Actual / DeepSeek | radio | Elige el redactor; se recuerda en el navegador |
| Respuestas rápidas | casilla | Muestra u oculta la fila de mensajes de ejemplo; se recuerda |
| Análisis | acción | Abre el cajón de análisis |

El menú aparece con una animación de 0,13 s desde la esquina superior derecha, como el de Android. Se cierra al
tocar fuera, con Escape o con Tab.

### 5.3 Globos de mensaje

- **Cola.** Solo el primer globo de cada tanda la lleva. La dibuja un `clip-path` de 8 × 13 px, y la esquina de ese
  lado queda recta. Los globos seguidos del mismo autor van a 2 px; al cambiar de autor, a 9 px.
- **Hora y checks.** Van abajo a la derecha, dentro del globo y con posición absoluta. Al final del texto se añade
  un hueco invisible del mismo ancho (`.esp`). Si la hora no cabe en la última línea, el hueco abre otra, y la
  hora nunca pisa el texto. Es la misma técnica de WhatsApp.
- **Checks.**
  - Los mensajes de la clienta salen con ✓✓ gris y pasan a azul a los 350 ms, cuando el bot «lo leyó».
  - El bot no lleva checks, como un contacto en la app.
- **Formato de WhatsApp.** `*negrita*` y `_cursiva_` se convierten en `<b>` y `<i>` después de escapar el HTML.
  Así, el mismo texto que el agente escribe para WhatsApp se ve bien aquí.
- **Entrada.** Cada globo sube 4 px con un fundido de 0,18 s.

### 5.4 Cita de anuncio

Con «Desde anuncio» activo, el primer mensaje de la clienta lleva arriba una cita como la que WhatsApp pone al
llegar desde un anuncio de clic a WhatsApp:

- barra verde a la izquierda;
- «Anuncio» en verde;
- debajo, «Baruka Design · clic a WhatsApp».

### 5.5 Mensajes con foto

- **Foto del bot.** Va arriba, recortada a 4:5 y anclada arriba para que se vea el torso de la prenda. Debajo va
  el pie, en el mismo globo.
- **Foto de la clienta.** Respeta su proporción, con un máximo de 340 px de alto.
- **Foto sin pie.** Lleva un degradado oscuro abajo, y la hora y los checks van en blanco encima de la imagen, como
  en la app.
- **Visor.** Al tocar una foto se abre a pantalla completa sobre negro al 95 %, con flecha para volver. Se cierra
  tocando, con Escape o con la flecha, y el foco vuelve a la foto.
- **Precios.** En el pie no se parten en dos líneas («S/ 330.00» va con `nowrap`).

### 5.6 Botones de respuesta

Copian los botones de respuesta rápida de WhatsApp Business:

- filas del color del globo, colgadas debajo del mensaje;
- texto azul centrado;
- 3 px de separación entre filas.

| Uso | Texto del botón | Mensaje que envía |
|---|---|---|
| Tallas de una tarjeta | `S` `M` `L` (agotadas, tachadas y desactivadas) | «Talla M del V35» |
| Tallas con precio | «L (S/ 330)» | «Talla L del V35» |
| Tipos de prenda (catálogo sin prenda) | «Vestidos» | «Quiero ver vestidos» |
| Confirmar pedido | **Sí, confirmar** · Cambiar talla | «Sí, confirmo» / «Cambiar talla» |
| Ofrecer otras opciones | **Sí, muéstrame** · No, gracias | «Sí, quiero ver otras opciones» / «No, gracias» |
| Botones libres del agente | el texto tal cual | el mismo texto |

- **Columnas.**
  - Textos de hasta 4 caracteres (tallas): hasta 4 columnas.
  - Dos opciones de hasta 16 caracteres, o tres o más de hasta 12: 2 columnas.
  - En cualquier otro caso: 1 columna.
  - Si en 2 columnas algún texto no cabe en una línea, se pasa a 1 columna después de pintarlos.
  - En 2 columnas, un botón impar al final ocupa todo el ancho.
- **Icono.** Los botones de texto largo llevan el icono de «responder» de WhatsApp. El botón afirmativo va en
  negrita.
- **Al usarlos.**
  - Los botones de respuesta desaparecen y el mensaje que envían aparece como escrito por la clienta.
  - Los de talla de una tarjeta siguen activos mientras la tarjeta esté en pantalla.

### 5.7 «Escribiendo…»

Mientras el bot prepara un mensaje, aparece un globo del bot con tres puntos. Los puntos saltan en secuencia: ciclo
de 1,2 s y 0,15 s de desfase entre puntos. Al mismo tiempo, la cabecera dice «escribiendo…».

### 5.8 Respuestas rápidas de ejemplo

Fila deslizable encima de la barra de escritura, con nueve frases típicas de una clienta:

- «Hola, quisiera saber si todavía tienen este vestido»
- «Es para un matrimonio»
- «¿Cuánto cuesta?»
- «¿Cómo es el material?»
- «Soy talla M»
- «Sí, me interesa»
- «Lo voy a pensar»
- «Quiero comprarlo»
- «¿Dónde quedan?»

Son píldoras blancas de 34 px con sombra suave. La rueda del ratón las desliza de lado. Existen para probar rápido
el embudo y se apagan desde el menú.

### 5.9 Barra de escritura

- **Campo.** Píldora de 46 px con emoji, campo, clip (adjuntar foto) y cámara (`capture="environment"`, abre la
  cámara trasera en el móvil).
- **Botón redondo verde.**
  - Con el campo vacío muestra el micrófono; al pulsarlo sale un aviso de que la voz no está en la demo.
  - Al escribir, el micrófono se cambia por el avión de enviar y desaparece la cámara, como en la app.
- **Fotos.**
  - Se reducen en el navegador a 1024 px y JPEG al 85 % antes de enviarlas.
  - El texto escrito en el campo viaja como pie de la foto.
  - También se puede arrastrar una foto sobre el chat.
- **Emojis.** Bandeja corta de 32 emojis del rubro (👗 👠 💃 💍 🛍️ 💳 🚚 📍…) en 8 columnas. Escribe en la
  posición del cursor.

### 5.10 Cajón de análisis

Muestra cómo entendió el bot el último mensaje. Es la parte de la demo que no existe en WhatsApp.

- **En el móvil:** hoja inferior al 84 % de alto, con velo oscuro y asa. Sube en 0,26 s con una curva suave.
- **En pantallas de 1000 px o más:** panel fijo de 400 px a la derecha, sin velo. La ventana del chat se corre a
  la izquierda y se puede seguir chateando.
- **Contenido:**
  - ajustes activos (motor, desde anuncio);
  - etapa de la venta en cuatro casillas: hechas en verde tenue, la actual en verde sólido;
  - intención y confianza;
  - opinión de Jev;
  - bitácora de decisiones;
  - temperatura;
  - qué esperaba el bot y si la clienta lo respondió;
  - datos extraídos de este mensaje (marcados «(Jev)» si los aportó Jev);
  - lo que ya se sabe de ella;
  - cita;
  - fichas del RAG con su fuente;
  - ejemplos del dataset con su similitud;
  - para fotos, los umbrales y las prendas más parecidas con barra de similitud.

### 5.11 Avisos breves

Un *toast* oscuro sobre la barra de escritura, de 2,6 s. Se usa para lo que la demo no tiene (voz, llamadas),
para confirmar un cambio del menú y para un archivo que no es imagen.

---

## 6. Ritmo de la conversación

El agente devuelve un texto con párrafos separados por línea en blanco, más fotos y botones. La página lo
convierte en una secuencia como la de una vendedora:

1. **El mensaje de la clienta** aparece al instante, con ✓✓ gris. A los 350 ms los checks se vuelven azules y
   empieza «escribiendo…».
2. **Cada párrafo es un mensaje.** Antes de cada párrafo, a partir del segundo, hay una pausa proporcional al
   largo: 25 ms por carácter, entre 0,6 y 2,2 s.
3. **Las fotos van después del texto**, con 0,7 s de «escribiendo…» antes de cada una.
4. **La pregunta final va después de la foto.** Si hay fotos y el último párrafo es una pregunta, se guarda y se
   envía al final: «te recomiendo este» → foto → «¿qué talla usas?». Antes, la pregunta quedaba encima de la foto
   y la clienta contestaba a la foto.
5. **Los botones** cuelgan del último mensaje del bot. Si ese mensaje es una tarjeta con foto, van en un bloque
   aparte debajo.
6. **Mientras el bot responde,** el botón de enviar queda desactivado y se ignoran los toques en botones, para no
   cruzar dos turnos.

Si falla la red o el agente, el bot contesta en su propia voz:

- texto: «Uy, se me cortó la conexión 😅 ¿Me lo repites?»;
- foto: «Uy, no pude ver bien la foto 😅 ¿Me la mandas otra vez?».

La página guarda en memoria los últimos 20 mensajes del historial, la etapa, la memoria y la sesión, y los devuelve
en cada petición. Solo el motor elegido y la preferencia de respuestas rápidas se guardan en `localStorage`,
siempre dentro de `try/catch`.

---

## 7. Adaptación a cada pantalla

| Pantalla | Diseño |
|---|---|
| **Escritorio** (más de 560 px de ancho y alto) | Ventana centrada de 500 px de ancho (máx. 960 px de alto, 12 px de radio y sombra) sobre la franja verde superior y el fondo gris de WhatsApp Web |
| **Móvil** (≤ 560 px de ancho o de alto) | Pantalla completa, sin radio ni sombra, como la app |
| **Muy estrecho** (≤ 350 px) | Iconos de cabecera más estrechos; sin botón de cámara |
| **Ancho** (≥ 1000 px y > 560 px de alto) | El análisis pasa a panel lateral de 400 px y el chat se corre a la izquierda |

Detalles para el móvil:

- `100dvh` en vez de `100vh`, para que la barra de direcciones no tape la barra de escritura.
- `viewport-fit=cover` y `env(safe-area-inset-*)` en cabecera, barra de escritura, menú y visor, para la muesca y
  la barra de inicio del iPhone.
- `interactive-widget=resizes-content`: al abrir el teclado, el chat se encoge en vez de quedar tapado.
- `overscroll-behavior: contain` en la lista de mensajes y en el cajón, para que deslizar no arrastre la página.
- El resalte al pasar el ratón solo existe donde hay ratón (`@media (hover: hover)`). En el teléfono se quedaría
  pegado tras tocar.

---

## 8. Accesibilidad

- **Roles y nombres:**
  - la lista de mensajes es `role="log"`;
  - el menú es `role="menu"`, con `menuitem`, `menuitemcheckbox` y `menuitemradio`, y `aria-checked`;
  - el visor es `role="dialog"` con `aria-modal`;
  - el aviso breve es `role="status"`.
- **Botones solo con icono:** todos llevan `aria-label`. Los que no funcionan en la demo lo dicen en su nombre
  («Llamada (no disponible en esta demo)»).
- **Elementos con nombre propio:**
  - los checks se leen como «Leído»;
  - los puntos, como «Escribiendo»;
  - una talla agotada, como «Talla L, agotada».
- **Teclado:**
  - Tab marca la sesión como de teclado; el aro de foco del campo solo aparece entonces.
  - El menú se recorre con ↑ ↓, Inicio y Fin.
  - Escape cierra, en este orden: visor, menú, emojis, cajón.
  - Al cerrar, el foco vuelve a donde estaba.
- **Foco visible:** contorno de 2 px en `--foco` (blanco sobre la cabecera y el visor).
- **Movimiento reducido:** con `prefers-reduced-motion` se quitan todas las animaciones y transiciones.

---

## 9. Correspondencia con el WhatsApp real

La página imita la app, pero el canal real tiene sus límites: evolution-go envía texto e imágenes con pie, sin
botones interactivos. El agente devuelve las dos cosas y cada canal pinta la suya.

| Elemento | Chat web (`canal: "web"`) | WhatsApp real (backend Go) |
|---|---|---|
| Párrafos | Un globo por párrafo, con pausa y «escribiendo…» | Un mensaje por párrafo (`sendAgentText`), en orden, por la cola de salida del chat |
| Pregunta final con foto | Después de la foto | Después de la foto (misma regla en `sendAgentText`) |
| Prenda sugerida | Tarjeta: foto + `titulo` + «Elige tu talla 👇» + botones de talla | Imagen con `pie`: «*V35* Irla — S/ … / Tallas: S, M / 👉 Escribe *V35* para pedirlo» |
| Elegir talla | Botón «M» | Escribir la talla |
| Confirmar pedido | Botones «Sí, confirmar» / «Cambiar talla» | «Responde *SI* para confirmar o *NO* para cancelar» |
| Otras opciones | Botones «Sí, muéstrame» / «No, gracias» | «¿Quieres ver otras opciones? Responde *SI*» |
| Catálogo sin prenda | Botón por tipo de prenda | La lista de tipos en el texto |
| Menú numérico (`1`–`4`) | Se traduce a su opción en el agente (`MENU_WEB`) | Lo resuelve el bot Go |
| Negrita y cursiva | `*…*` y `_…_` convertidos a HTML | Nativas de WhatsApp |
| Imagen que no se puede enviar | — | Se envía solo el pie como texto (`outbox.go`) |
| Llegada por anuncio | Casilla «Desde anuncio» del menú | `contextInfo.externalAdReply` del mensaje |

La cola de salida (`backend/internal/bot/outbox.go`) tiene un trabajador por chat que entrega en orden. Así, el
bot sigue leyendo mensajes aunque evolution-go esté reconectando: solo se atrasa la entrega.

**El WhatsApp real no simula las pausas ni «escribiendo…».** Los mensajes salen tan rápido como los entrega
evolution-go. Las pausas de la web son solo de la demo.

---

## 10. Cómo cambiarlo sin romperlo

- **Un solo archivo.** No se añaden librerías ni hojas externas: la página tiene que cargar al instante y
  funcionar bajo `/demo-design/` (todas las rutas son relativas: `chat`, `foto`, `health`).
- **Colores, solo por variable.** Un color nuevo se define en `:root` y en el bloque oscuro. No se escriben colores
  sueltos en los componentes. La excepción es la etapa actual del análisis, en `#008069` fijo.
- **El contrato manda.** Un campo nuevo en la respuesta del agente debe pintarse en la web y tener su equivalente
  en texto para WhatsApp. La web no puede prometer algo que el canal real no da.
- **Verificar en tres tamaños y dos temas:**
  - 393 px de ancho (teléfono);
  - una ventana de escritorio;
  - más de 1000 px con el análisis abierto;
  - en claro y en oscuro.
  Prueba también una conversación con foto, botones de talla y confirmación.
- **Despliegue:** reconstruir y recrear `kddesign_agente` (`DEPLOY.md`). Si no cambian los datos de entrenamiento,
  el build tarda segundos.
