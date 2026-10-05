"""V2 del agente (spec «Baruka AI Agent V2», fases 1–2).

Convive con V1 sin cambiarla: `AGENT_VERSION=v1` (por defecto) es el agente de siempre. `AGENT_VERSION=v2` pasa por
`AgentV2`, que hoy envuelve el flujo V1 y le añade trazabilidad. Las piezas nuevas (interfaces de decisión y de
generación, contexto estructurado, plan validado) son puras y no llaman a ningún modelo ni a ninguna API.

Regla: los modelos proponen; el código decide.
"""
