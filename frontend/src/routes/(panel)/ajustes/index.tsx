import { $, component$, useSignal, useStore, useVisibleTask$ } from "@builder.io/qwik";
import type { DocumentHead } from "@builder.io/qwik-city";
import { titulo } from "~/lib/marca";
import { api, equipo, miSesion, type Miembro, type Sesion } from "~/lib/api";

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
  const yo = useSignal<Sesion | null>(null);
  const error = useSignal("");

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    miSesion().then((u) => (yo.value = u));
    Object.assign(s, await api<Settings>("/api/settings"));
    loaded.value = true;
  });

  const save = $(async (patch: Settings) => {
    try {
      Object.assign(s, await api<Settings>("/api/settings", { method: "PUT", json: patch }));
      error.value = "";
      saved.value = "Guardado";
      setTimeout(() => (saved.value = ""), 2000);
    } catch (e) {
      error.value = (e as Error).message;
    }
  });
  const soloLectura = !yo.value?.admin;

  return (
    <div class="page narrow">
      <div class="page-head">
        <div>
          <h1>Ajustes</h1>
          <p class="muted">Comportamiento del bot de WhatsApp y equipo de la tienda. {saved.value && <b class="ok-text">{saved.value} ✓</b>}</p>
        </div>
      </div>
      {yo.value && (
        <p class="muted small">
          Entraste como <b>{yo.value.name || yo.value.username}</b> · {yo.value.admin ? "admin" : "asesora"}
          {soloLectura && " (los ajustes y el equipo los cambia una admin)"}
        </p>
      )}
      {error.value && <p class="form-error">{error.value}</p>}
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
                disabled={soloLectura}
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
              disabled={soloLectura}
              onChange$={(_, el) => save({ bot_resume_hours: String(Math.max(0, Number(el.value) || 0)) })}
            />
          </label>
        </section>
      )}
      {yo.value && <Equipo yo={yo.value} />}
    </div>
  );
});

// Equipo: personas que entran al panel. Admin: todo. Asesora: atiende clientes, pedidos, notas y tareas, pero no cambia
// ajustes, WhatsApp ni el equipo, no borra pedidos ni productos y no exporta.
const Equipo = component$<{ yo: Sesion }>(({ yo }) => {
  const lista = useSignal<Miembro[]>([]);
  const nuevo = useStore({ username: "", name: "", role: "asesora", password: "" });
  const abierto = useSignal(false);
  const error = useSignal("");
  const ok = useSignal("");

  const load = $(async () => {
    lista.value = await equipo(true);
  });
  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(() => load());

  const cambiar = $(async (m: Miembro, patch: Record<string, unknown>, msg: string) => {
    error.value = "";
    try {
      await api(`/api/equipo/${m.id}`, { method: "PATCH", json: patch });
      ok.value = msg;
      setTimeout(() => (ok.value = ""), 2500);
      await load();
    } catch (e) {
      error.value = (e as Error).message;
      await load();
    }
  });

  return (
    <section class="panel">
      <div class="panel-head">
        <h2>Equipo</h2>
        {yo.admin && (
          <button class="btn btn-sm btn-primary" onClick$={() => (abierto.value = !abierto.value)}>
            {abierto.value ? "Cerrar" : "+ Persona"}
          </button>
        )}
      </div>
      <p class="muted small">Admin: todo. Asesora: atiende clientes, pedidos, notas y tareas; no cambia ajustes, WhatsApp ni el equipo, no borra y no exporta.</p>
      {abierto.value && (
        <form
          class="equipo-form"
          preventdefault:submit
          onSubmit$={async () => {
            error.value = "";
            try {
              await api("/api/equipo", { method: "POST", json: nuevo });
              Object.assign(nuevo, { username: "", name: "", role: "asesora", password: "" });
              abierto.value = false;
              ok.value = "Persona añadida";
              await load();
            } catch (e) {
              error.value = (e as Error).message;
            }
          }}
        >
          <div class="grid2">
            <label>
              Nombre
              <input value={nuevo.name} onInput$={(_, el) => (nuevo.name = el.value)} placeholder="Lucía Pérez" />
            </label>
            <label>
              Usuario
              <input value={nuevo.username} onInput$={(_, el) => (nuevo.username = el.value)} required autoComplete="off" placeholder="lucia" />
            </label>
            <label>
              Clave (mín. 8)
              <input type="password" value={nuevo.password} onInput$={(_, el) => (nuevo.password = el.value)} required minLength={8} autoComplete="new-password" />
            </label>
            <label>
              Rol
              <select value={nuevo.role} onChange$={(_, el) => (nuevo.role = el.value)}>
                <option value="asesora">Asesora</option>
                <option value="admin">Admin</option>
              </select>
            </label>
          </div>
          <button class="btn btn-primary btn-sm">Añadir</button>
        </form>
      )}
      {error.value && <p class="form-error small">{error.value}</p>}
      {ok.value && <p class="ok-text small">{ok.value} ✓</p>}
      <ul class="equipo">
        {lista.value.map((m) => (
          <li key={m.id} class={["miembro", !m.active && "inactivo"]}>
            <span class="avatar sm">{(m.name || m.username).slice(0, 1).toUpperCase()}</span>
            <div class="grow">
              <strong>{m.name || m.username}</strong>
              <span class="muted small block">
                {m.username}
                {m.id === yo.id ? " · tú" : ""}
                {!m.active ? " · desactivada" : ""}
              </span>
            </div>
            {yo.admin ? (
              <>
                <select
                  class="auto"
                  value={m.role === "asesora" ? "asesora" : "admin"}
                  aria-label={`Rol de ${m.username}`}
                  onChange$={(_, el) => cambiar(m, { role: el.value }, "Rol cambiado")}
                >
                  <option value="asesora">Asesora</option>
                  <option value="admin">Admin</option>
                </select>
                <button
                  class="btn btn-sm btn-ghost"
                  onClick$={async () => {
                    const clave = prompt(`Nueva clave para ${m.username} (mínimo 8 caracteres):`);
                    if (clave) await cambiar(m, { password: clave }, "Clave cambiada");
                  }}
                >
                  Clave
                </button>
                {m.id !== yo.id && (
                  <button class="btn btn-sm btn-ghost" onClick$={() => cambiar(m, { active: !m.active }, m.active ? "Desactivada" : "Activada")}>
                    {m.active ? "Desactivar" : "Activar"}
                  </button>
                )}
              </>
            ) : (
              <span class="chip">{m.role === "asesora" ? "Asesora" : "Admin"}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
});

export const head: DocumentHead = { title: titulo("Ajustes") };
