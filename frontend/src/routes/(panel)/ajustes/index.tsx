import { $, component$, useSignal, useStore, useVisibleTask$ } from "@builder.io/qwik";
import type { DocumentHead } from "@builder.io/qwik-city";
import { api, type AgentMetricas, type AgentVersionStats } from "~/lib/api";

type Settings = Record<string, string>;

const TOGGLES = [
  { key: "bot_enabled", label: "Bot activo", hint: "Si lo apagas, los mensajes se guardan pero nadie responde automáticamente." },
  { key: "notify_status_changes", label: "Avisar cambios de estado", hint: "Al mover un pedido (preparando, enviado, entregado…) el cliente recibe un mensaje." },
  { key: "pause_on_manual_reply", label: "Pausar el bot cuando respondo desde el celular", hint: "Si escribes al cliente desde el teléfono, el bot deja de responder en ese chat." },
];

const VERSIONES = [
  { v: "v1", label: "V1" },
  { v: "v2", label: "V2" },
  { v: "ab", label: "A/B" },
];
const MODOS = [
  { v: "sombra", label: "Sombra" },
  { v: "activo", label: "Activo" },
];

const pct = (x?: number) => (x == null ? "—" : `${Math.round(x * 100)} %`);
const ms = (x?: number) => (x == null || x === 0 ? "—" : `${x} ms`);

export default component$(() => {
  const s = useStore<Settings>({});
  const loaded = useSignal(false);
  const saved = useSignal("");
  const error = useSignal("");
  const metricas = useSignal<AgentMetricas | null>(null);
  const percent = useSignal(0);

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    Object.assign(s, await api<Settings>("/api/settings"));
    percent.value = Number(s.agent_v2_percent) || 0;
    loaded.value = true;
    metricas.value = await api<AgentMetricas>("/api/agent/metricas").catch(() => ({ disponible: false }));
  });

  const save = $(async (patch: Settings) => {
    error.value = "";
    try {
      Object.assign(s, await api<Settings>("/api/settings", { method: "PUT", json: patch }));
      percent.value = Number(s.agent_v2_percent) || 0;
      saved.value = "Guardado";
      setTimeout(() => (saved.value = ""), 2000);
    } catch (e) {
      error.value = (e as Error).message;
    }
  });

  const refreshMetricas = $(async () => {
    metricas.value = await api<AgentMetricas>("/api/agent/metricas").catch(() => ({ disponible: false }));
  });

  const v = s.agent_version || "v1";
  const versiones = metricas.value?.versiones ?? {};
  const filas = (["v1", "v2"] as const).filter((k) => versiones[k]);

  return (
    <div class="page narrow">
      <div class="page-head">
        <div>
          <h1>Ajustes</h1>
          <p class="muted">Comportamiento del bot de WhatsApp. {saved.value && <b class="ok-text">{saved.value} ✓</b>}</p>
        </div>
      </div>
      {error.value && <p class="form-error">{error.value}</p>}
      {loaded.value && (
        <>
          <section class="panel">
            {TOGGLES.map((t) => (
              <label key={t.key} class="setting">
                <div class="grow">
                  <strong>{t.label}</strong>
                  <p class="muted small">{t.hint}</p>
                </div>
                <input
                  type="checkbox"
                  class="switch"
                  checked={s[t.key] === "true"}
                  onChange$={(_, el) => save({ [t.key]: el.checked ? "true" : "false" })}
                />
              </label>
            ))}
            <label class="setting">
              <div class="grow">
                <strong>Reactivar el bot tras (horas)</strong>
                <p class="muted small">Un chat atendido por una asesora vuelve al bot pasado este tiempo. 0 = nunca.</p>
              </div>
              <input
                type="number"
                min="0"
                class="num"
                value={s.bot_resume_hours}
                onChange$={(_, el) => save({ bot_resume_hours: String(Math.max(0, Number(el.value) || 0)) })}
              />
            </label>
          </section>

          <section class="panel">
            <h2>Versión del agente</h2>
            <p class="muted small">
              Vale para WhatsApp. El chat de prueba (<code>/demo-design</code>) tiene su propio selector. Cada clienta ve siempre la
              misma versión mientras dure la prueba.
            </p>

            <div class="setting">
              <div class="grow">
                <strong>¿Quién contesta?</strong>
                <p class="muted small">
                  <b>V1</b>: el agente de siempre. <b>V2</b>: el agente nuevo para todas. <b>A/B</b>: reparte un porcentaje de clientas
                  a V2 (por su número) y el resto queda en V1.
                </p>
              </div>
              <div class="seg" role="group" aria-label="Versión del agente">
                {VERSIONES.map((o) => (
                  <button key={o.v} type="button" class={v === o.v && "on"} aria-pressed={v === o.v} onClick$={() => save({ agent_version: o.v })}>
                    {o.label}
                  </button>
                ))}
              </div>
            </div>

            {v === "ab" && (
              <div class="setting">
                <div class="grow">
                  <strong>Clientas que van a V2</strong>
                  <p class="muted small">Subir el porcentaje no cambia de versión a quien ya estaba en V2.</p>
                </div>
                <div class="ab-range">
                  <input
                    type="range"
                    min="0"
                    max="100"
                    step="5"
                    value={percent.value}
                    aria-label="Porcentaje de clientas en V2"
                    onInput$={(_, el) => (percent.value = Number(el.value))}
                    onChange$={(_, el) => save({ agent_v2_percent: el.value })}
                  />
                  <output>{percent.value} %</output>
                </div>
              </div>
            )}

            <div class="setting">
              <div class="grow">
                <strong>Modo de V2</strong>
                <p class="muted small">
                  <b>Sombra</b>: V2 decide y se mide, pero la clienta recibe siempre el texto de V1. <b>Activo</b>: V2 contesta cuando su
                  plan coincide con lo que hizo V1 y su texto pasa el control de calidad; si no, contesta V1.
                </p>
              </div>
              <div class="seg" role="group" aria-label="Modo de V2">
                {MODOS.map((o) => (
                  <button
                    key={o.v}
                    type="button"
                    class={(s.agent_v2_modo || "sombra") === o.v && "on"}
                    aria-pressed={(s.agent_v2_modo || "sombra") === o.v}
                    onClick$={() => save({ agent_v2_modo: o.v })}
                  >
                    {o.label}
                  </button>
                ))}
              </div>
            </div>

            <label class="setting">
              <div class="grow">
                <strong>Números de prueba (siempre V2)</strong>
                <p class="muted small">Separados por coma, con o sin el +51. Van a V2 aunque arriba esté V1. Sirve para probar con tu propio teléfono.</p>
              </div>
              <textarea
                rows={2}
                placeholder="51987654321, 51912345678"
                value={s.agent_v2_phones}
                onChange$={(_, el) => save({ agent_v2_phones: el.value })}
              />
            </label>
          </section>

          <section class="panel">
            <div class="row between">
              <h2>V1 contra V2</h2>
              <button class="btn btn-sm btn-ghost" onClick$={refreshMetricas}>
                Actualizar
              </button>
            </div>
            {!metricas.value?.disponible ? (
              <p class="muted small">Sin datos: el agente no responde o todavía no atendió mensajes desde que se reinició.</p>
            ) : filas.length === 0 ? (
              <p class="muted small">Aún no hay turnos. Las cifras se reinician con el agente.</p>
            ) : (
              <>
                <table class="ab-stats">
                  <thead>
                    <tr>
                      <th></th>
                      {filas.map((k) => (
                        <th key={k}>{k.toUpperCase()}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(
                      [
                        ["Turnos", (x: AgentVersionStats) => String(x.turnos)],
                        ["Latencia p50", (x: AgentVersionStats) => ms(x.p50_ms)],
                        ["Latencia p95", (x: AgentVersionStats) => ms(x.p95_ms)],
                        ["Acuerdo de V2 con V1 (texto libre)", (x: AgentVersionStats) => (x.comparables ? pct(x.acuerdo_v1) : "—")],
                        ["V2 habló (modo activo)", (x: AgentVersionStats) => (x.activo ? pct(x.habla_v2) : "—")],
                        ["Borradores que pasan el control", (x: AgentVersionStats) => (x.activo ? pct(x.calidad_ok) : "—")],
                      ] as [string, (x: AgentVersionStats) => string][]
                    ).map(([etiqueta, f]) => (
                      <tr key={etiqueta}>
                        <td>{etiqueta}</td>
                        {filas.map((k) => (
                          <td key={k}>{f(versiones[k])}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
                {versiones.v2?.por_que_no_habla && Object.keys(versiones.v2.por_que_no_habla).length > 0 && (
                  <p class="muted small">
                    Por qué V2 no habló:{" "}
                    {Object.entries(versiones.v2.por_que_no_habla)
                      .map(([k, n]) => `${k} (${n})`)
                      .join(" · ")}
                  </p>
                )}
              </>
            )}
          </section>
        </>
      )}
    </div>
  );
});

export const head: DocumentHead = { title: "Ajustes · Baruka Design" };
