import { $, component$, useOnWindow, useSignal, useVisibleTask$ } from "@builder.io/qwik";
import type { DocumentHead } from "@builder.io/qwik-city";
import { titulo } from "~/lib/marca";
import { api, equipo, type Miembro, type Tarea } from "~/lib/api";
import { TareaFila, TareaModal, alternarTarea } from "~/components/crm";

// Tareas y recordatorios del equipo: «Mis tareas», «Vencen hoy», «Vencidas», «Todas» y «Hechas».
const VISTAS = [
  { key: "mias", label: "Mis tareas", hint: "Lo que tienes pendiente, lo más urgente primero." },
  { key: "hoy", label: "Vencen hoy", hint: "Pendientes de todo el equipo que vencen hoy." },
  { key: "vencidas", label: "Vencidas", hint: "Pendientes cuya fecha ya pasó." },
  { key: "pendientes", label: "Todas", hint: "Todo lo pendiente del equipo." },
  { key: "hechas", label: "Hechas", hint: "Lo último que se completó." },
] as const;

export default component$(() => {
  const vista = useSignal<string>("mias");
  const responsable = useSignal("");
  const tareas = useSignal<Tarea[]>([]);
  const miembros = useSignal<Miembro[]>([]);
  const cargando = useSignal(true);
  const modal = useSignal<Tarea | "nueva" | null>(null);
  const error = useSignal("");

  const load = $(async () => {
    try {
      const v = vista.value === "mias" ? "pendientes" : vista.value;
      const r = vista.value === "mias" ? "yo" : responsable.value;
      tareas.value = await api<Tarea[]>(`/api/tareas?vista=${v}${r ? `&responsable=${r}` : ""}`);
      error.value = "";
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      cargando.value = false;
    }
  });

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    const v = new URLSearchParams(location.search).get("vista");
    if (v && VISTAS.some((x) => x.key === v)) vista.value = v;
    equipo().then((ms) => (miembros.value = ms));
    await load();
  });
  useOnWindow(
    "kd-change",
    $((e: Event) => {
      if ((e as CustomEvent<string>).detail === "tareas") load();
    }),
  );

  const elegir = $(async (v: string) => {
    vista.value = v;
    history.replaceState(null, "", `?vista=${v}`);
    await load();
  });

  const info = VISTAS.find((v) => v.key === vista.value) ?? VISTAS[0];

  return (
    <div class="page narrow">
      <div class="page-head">
        <div>
          <h1>Tareas</h1>
          <p class="muted">Recordatorios con fecha y responsable: «llamar a Lucía el viernes», «enviar fotos del V35».</p>
        </div>
        <div class="head-actions">
          <button class="btn btn-primary" onClick$={() => (modal.value = "nueva")}>
            + Tarea
          </button>
        </div>
      </div>

      <div class="tabs">
        {VISTAS.map((v) => (
          <button key={v.key} class={["tab", vista.value === v.key && "active"]} onClick$={() => elegir(v.key)}>
            {v.label}
          </button>
        ))}
      </div>

      <div class="filters">
        <span class="muted small">{info.hint}</span>
        {vista.value !== "mias" && (
          <select
            class="push-right auto"
            value={responsable.value}
            aria-label="Responsable"
            onChange$={async (_, el) => {
              responsable.value = el.value;
              await load();
            }}
          >
            <option value="">De todo el equipo</option>
            <option value="yo">Solo mías</option>
            {miembros.value.map((m) => (
              <option key={m.id} value={String(m.id)}>
                {m.name || m.username}
              </option>
            ))}
          </select>
        )}
      </div>

      {error.value && <p class="form-error">{error.value}</p>}
      <section class="panel">
        {cargando.value ? (
          <div class="loading">Cargando…</div>
        ) : tareas.value.length === 0 ? (
          <p class="muted empty-msg">{vista.value === "hechas" ? "Aún no hay tareas hechas." : "Nada pendiente aquí. 🎉"}</p>
        ) : (
          <ul class="tareas">
            {tareas.value.map((t) => (
              <TareaFila
                key={t.id}
                t={t}
                conClienta
                onToggle$={async (x) => {
                  try {
                    await alternarTarea(x);
                  } catch (e) {
                    error.value = (e as Error).message;
                  }
                  await load();
                }}
                onEdit$={(x) => {
                  modal.value = x;
                }}
              />
            ))}
          </ul>
        )}
      </section>

      {modal.value && (
        <TareaModal
          tarea={modal.value === "nueva" ? null : modal.value}
          onClose$={() => (modal.value = null)}
          onSaved$={async () => {
            modal.value = null;
            await load();
          }}
        />
      )}
    </div>
  );
});

export const head: DocumentHead = { title: titulo("Tareas") };
