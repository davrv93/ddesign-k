import { $, component$, useSignal, useStore, useVisibleTask$ } from "@builder.io/qwik";
import type { DocumentHead } from "@builder.io/qwik-city";
import { titulo } from "~/lib/marca";
import { api } from "~/lib/api";

type Settings = Record<string, string>;

const TOGGLES = [
  { key: "bot_enabled", label: "Bot activo", hint: "Si lo apagas, los mensajes se guardan pero nadie responde automáticamente." },
  { key: "notify_status_changes", label: "Avisar cambios de estado", hint: "Al mover un pedido (preparando, enviado, entregado…) el cliente recibe un mensaje." },
  { key: "pause_on_manual_reply", label: "Pausar el bot cuando respondo desde el celular", hint: "Si escribes al cliente desde el teléfono, el bot deja de responder en ese chat." },
];

export default component$(() => {
  const s = useStore<Settings>({});
  const loaded = useSignal(false);
  const saved = useSignal("");

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    Object.assign(s, await api<Settings>("/api/settings"));
    loaded.value = true;
  });

  const save = $(async (patch: Settings) => {
    Object.assign(s, await api<Settings>("/api/settings", { method: "PUT", json: patch }));
    saved.value = "Guardado";
    setTimeout(() => (saved.value = ""), 2000);
  });

  return (
    <div class="page narrow">
      <div class="page-head">
        <div>
          <h1>Ajustes</h1>
          <p class="muted">Comportamiento del bot de WhatsApp. {saved.value && <b class="ok-text">{saved.value} ✓</b>}</p>
        </div>
      </div>
      {loaded.value && (
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
      )}
    </div>
  );
});

export const head: DocumentHead = { title: titulo("Ajustes") };
