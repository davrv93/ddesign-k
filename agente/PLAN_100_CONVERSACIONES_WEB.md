# Plan: 100 conversaciones en modo web, con la traza completa, y mejoras

Objetivo: correr 100 conversaciones completas contra el agente en canal `web` y V2 activo, guardar **todo** lo que pasa en cada
turno en un log, encontrar dónde falla y corregirlo con evidencia. Hoy las capturas de WhatsApp y del chat nos enseñan
fallos de uno en uno (rojo → cierre, talla XL inventada, bitcoin → «¿qué modelo?»); esto los encuentra en lote.

## Lo que ya existe y se reutiliza

`app/conversaciones.py` (04-10): corpus determinista (semilla 2026), clienta simulada por LLM que reacciona al bot (28 personas),
guiones fijos con los errores ya vistos, etapa/memoria/estado viajando entre turnos como el bot Go, reglas deterministas por turno
y por conversación, juez LLM, tope de gasto e informe antes/después.

## Lo que le falta (y se construye)

1. **Solo el 25 % de las simuladas va en canal `web`**: las 100 irán todas en `web`.
2. **No guarda la traza V2**: el JSONL actual no trae `v2` (etapas con ms, degradaciones, RAG, reranker, `fuera_de_giro`, temas,
   `motivo_v1`, plan y no_habla). Se guarda tal cual vuelve del agente, sin recortar.
3. **No hay log legible**: además del JSONL se genera una transcripción por conversación (cliente / bot / acción / fotos / traza
   resumida) para leerlas de corrido.
4. **El corpus de 100 es nuevo**: el actual es de 200 y mezcla canales; no hay `pruebas_conv/corpus.jsonl` en esta máquina.

## Fases

**F0 · Entorno de prueba (sin tocar producción ni a nadie)**
- Agente local con el código actual (`feat/agente-v2`), `AGENT_VERSION=v2`, `V2_MODO=activo`, canal `web`.
- **Sin gastar el saldo del bot**: la clave de OpenRouter es la misma de producción y hoy le quedan **US$ 0,82** (de US$ 3;
  vence el 09-10). Por eso las 100 corren con `usar_llm=false` (el agente ejecuta toda su lógica de código y arma el texto con
  su respaldo, costo 0) y la **clienta simulada es un modelo local** (Ollama `qwen3:1.7b` en este Mac (sin razonamiento)), no DeepSeek. Es lo
  que más fallos nos ha dado: color, talla, cierre, fuera de giro, etapas, memoria.
- Solo **10 conversaciones** (las más representativas) corren además con el LLM de pago real, con tope de US$ 0,25, para
  ver lo que el modelo escribe de verdad (como la talla XL inventada). Nunca se baja el saldo de US$ 0,50.
- CRM apagado (`CRM_EVENT_URL` vacío): el canal `web` avisa a Kommo y no debemos crear leads falsos. Sin salida a WhatsApp.
- Catálogo y stock de producción en solo lectura, como en la regresión.

**F1 · Corpus de 100** (semilla fija, `canal=web` en todas)
- 20 guiones con los fallos conocidos: color sin stock, talla que no hay, fuera de giro (cripto, empleo, reclamo), «eres un bot»,
  pedir persona, XL, fecha como objeción, foto, cita, pedido completo, cambio de prenda, mensajes partidos.
- 80 simuladas: 28 personas × variantes (fría, apurada, regatea, queja, jerga, faltas, audios, anuncio, cambia de idea…), 4–12 turnos.
- 30 reservadas que **no se miran para corregir**: solo miden antes y después. Las 70 de desarrollo sí se leen.

**F2 · Corrida y log completo**
- `agente/pruebas_conv/` (ya fuera de git: el repo es público): `log_web_<fecha>.jsonl` (un registro por turno con mensaje,
  respuesta, acción, fotos, etapa, memoria, `v2` íntegro, ms, costo) y `transcripciones/<id>.md`.
- Tope de gasto con corte automático. Reanudable por id.

**F3 · Análisis (dónde falla)**
- Reglas deterministas por turno y por conversación + juez LLM, agrupado por categoría y gravedad.
- Métricas de V2: % de turnos en que habló, motivos de no hablar, degradaciones por tipo, latencia p50/p95 por etapa,
  derivaciones a la dueña, aclaraciones, reranker (elige/duda/fuera de tiempo), costo por conversación.
- Tabla final «dónde falla»: categoría, frecuencia, ejemplo con id de conversación, causa probable (V1, V2, catálogo, LLM).

**F4 · Mejoras**
- Orden: gravedad × frecuencia. Prioridad a lo que le cuesta una venta o dice algo falso (precio, talla, color, stock).
- Cada arreglo lleva su caso en la regresión (`data/regresion_conversaciones.jsonl`) y se explica en el commit. Las pruebas que
  cubren defectos reales no se borran.
- Lo que sea de datos (fotos o fichas mal puestas, como la del V24) se reporta aparte: no se arregla con código.

**F5 · Verificación y entrega**
- Regresión completa en V1, V2 sombra y V2 activo + pruebas unitarias.
- Reservadas antes/después con el mismo juez.
- Commit local con el informe. **Push y despliegue solo con tu orden.**

## Costos y límites

- Las 100 sin LLM de pago: **US$ 0**. Las 10 con DeepSeek: ≈ US$ 0,15–0,25, con corte automático en US$ 0,25 y comprobando
  `limit_remaining` antes y después. La clave no se imprime.
- Sin juez LLM de pago: el análisis usa las reglas deterministas del runner y las nuevas que salgan de los hallazgos.
- Tiempo: F0–F2 unos 40–60 min (el modelo local es el cuello); F3–F5 depende de cuántos hallazgos haya.

## Decisiones que necesito de ti

1. **Saldo**: con US$ 0,82 el plan anterior (US$ 5) no cabe. ¿Te sirve el plan «100 gratis + 10 con DeepSeek», o prefieres
   recargar la clave / darme otra para correr las 100 completas con el LLM de pago?
2. **Dónde correr**: propongo el agente **local** (no contamina el CRM ni a producción). La alternativa, `proyectopostventa.site`,
   crearía leads falsos en Kommo y gastaría el mismo saldo.
3. **Alcance de las mejoras**: ¿arreglo todo lo que salga con evidencia clara y te traigo lo ambiguo, o me detengo después del
   informe para que elijas?
