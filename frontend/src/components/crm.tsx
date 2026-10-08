// Piezas del CRM que comparten varias pantallas: etapa, notas internas, tareas y su formulario.
import { $, component$, useOnWindow, useSignal, useStore, useVisibleTask$, type QRL } from "@builder.io/qwik";
import { api, equipo, miSesion, type Miembro, type Nota, type Sesion, type Tarea } from "~/lib/api";
import { u } from "~/lib/base";
import { etapaInfo, isoALocal, localAISO, timeAgo, venceLabel } from "~/lib/format";

export const EtapaPill = component$<{ etapa: string; fijada?: boolean }>(({ etapa, fijada }) => {
  const e = etapaInfo(etapa);
  return (
    <span class="pill" style={{ "--col": e.color }} title={fijada ? "Fijada a mano: el bot no la mueve" : "La mueve el bot"}>
      {e.label}
      {fijada ? " · ✋" : ""}
    </span>
  );
});

// ---------------------------------------------------------------------------
// Notas internas de una clienta o de un pedido (con autor y fecha). Solo las ve el equipo.

export const NotasPanel = component$<{ clientaId?: number; pedidoId?: number; compacto?: boolean }>(({ clientaId, pedidoId, compacto }) => {
  const notas = useSignal<Nota[]>([]);
  const texto = useSignal("");
  const busy = useSignal(false);
  const error = useSignal("");
  const yo = useSignal<Sesion | null>(null);
  const q = pedidoId ? `pedido=${pedidoId}` : `clienta=${clientaId}`;

  const load = $(async () => {
    notas.value = await api<Nota[]>(`/api/notas?${q}`);
  });
  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    miSesion().then((s) => (yo.value = s));
    await load();
  });
  useOnWindow(
    "kd-change",
    $((e: Event) => {
      if ((e as CustomEvent<string>).detail === "clientas") load();
    }),
  );

  const add = $(async () => {
    if (!texto.value.trim()) return;
    busy.value = true;
    error.value = "";
    try {
      await api("/api/notas", { method: "POST", json: { clienta_id: clientaId ?? 0, pedido_id: pedidoId ?? 0, texto: texto.value } });
      texto.value = "";
      await load();
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      busy.value = false;
    }
  });

  return (
    <div class="notas">
      <form class="nota-form" preventdefault:submit onSubmit$={add}>
        <textarea rows={compacto ? 2 : 3} bind:value={texto} placeholder="Nota para el equipo (la clienta no la ve)…" />
        <button class="btn btn-sm btn-primary" disabled={busy.value || !texto.value.trim()}>
          Guardar nota
        </button>
      </form>
      {error.value && <p class="form-error small">{error.value}</p>}
      {notas.value.length === 0 && <p class="muted small">Aún no hay notas.</p>}
      <ul class="nota-list">
        {notas.value.map((n) => (
          <li key={n.id} class="nota">
            <p>{n.texto}</p>
            <span class="muted small">
              {n.autor || "—"} · {timeAgo(n.created_at)}
              {!pedidoId && n.order_id > 0 && (
                <>
                  {" · "}
                  <a href={u(`/pedidos?pedido=${n.order_id}`)}>pedido #{n.order_id}</a>
                </>
              )}
              {yo.value && (yo.value.admin || yo.value.id === n.autor_id) && (
                <button
                  class="link-btn"
                  onClick$={async () => {
                    await api(`/api/notas/${n.id}`, { method: "DELETE" });
                    await load();
                  }}
                >
                  Borrar
                </button>
              )}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
});

// ---------------------------------------------------------------------------
// Tareas

export const TareaFila = component$<{ t: Tarea; conClienta?: boolean; onToggle$: QRL<(t: Tarea) => void>; onEdit$?: QRL<(t: Tarea) => void> }>(
  ({ t, conClienta, onToggle$, onEdit$ }) => {
    const v = venceLabel(t.vence);
    return (
      <li class={["tarea", t.hecha && "hecha"]}>
        <input type="checkbox" class="tarea-check" checked={t.hecha} aria-label="Hecha" onChange$={() => onToggle$(t)} />
        <div class="grow">
          <button class="tarea-titulo" onClick$={() => onEdit$?.(t)} disabled={!onEdit$}>
            {t.titulo}
          </button>
          <div class="tarea-meta small">
            {!t.hecha && <span class={["vence", v.tone]}>{v.text}</span>}
            {t.hecha && t.hecha_at && <span class="muted">Hecha {timeAgo(t.hecha_at)}</span>}
            <span class="muted">· {t.responsable || "Sin responsable"}</span>
            {conClienta && t.customer_id > 0 && (
              <a href={u(`/clientas?c=${t.customer_id}`)} class="tarea-clienta">
                {t.clienta || "Clienta"}
              </a>
            )}
            {t.order_id > 0 && <a href={u(`/pedidos?pedido=${t.order_id}`)}>#{t.order_id}</a>}
          </div>
        </div>
      </li>
    );
  },
);

/** Marca o desmarca una tarea como hecha. */
export const alternarTarea = $(async (t: Tarea) => {
  await api(`/api/tareas/${t.id}`, { method: "PATCH", json: { hecha: !t.hecha } });
});

interface TareaModalProps {
  tarea?: Tarea | null;
  clientaId?: number;
  pedidoId?: number;
  onClose$: QRL<() => void>;
  onSaved$: QRL<() => void>;
}

export const TareaModal = component$<TareaModalProps>(({ tarea, clientaId, pedidoId, onClose$, onSaved$ }) => {
  const f = useStore({
    titulo: tarea?.titulo ?? "",
    vence: isoALocal(tarea?.vence ?? null),
    responsable: tarea ? String(tarea.responsable_id) : "",
  });
  const miembros = useSignal<Miembro[]>([]);
  const error = useSignal("");
  const busy = useSignal(false);

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    const [ms, yo] = await Promise.all([equipo(), miSesion()]);
    miembros.value = ms.filter((m) => m.active);
    if (!tarea && f.responsable === "") f.responsable = String(yo.id);
  });

  const atajo = $((dias: number, hora = 10) => {
    const d = new Date();
    d.setDate(d.getDate() + dias);
    d.setHours(hora, 0, 0, 0);
    f.vence = isoALocal(d.toISOString());
  });

  return (
    <div class="overlay overlay-center" onClick$={onClose$}>
      <form
        class="modal"
        preventdefault:submit
        onClick$={(e) => e.stopPropagation()}
        onSubmit$={async () => {
          busy.value = true;
          error.value = "";
          try {
            const body = {
              titulo: f.titulo,
              vence: localAISO(f.vence),
              sin_vence: !f.vence,
              responsable_id: Number(f.responsable) || 0,
              clienta_id: clientaId ?? 0,
              pedido_id: pedidoId ?? 0,
            };
            if (tarea) await api(`/api/tareas/${tarea.id}`, { method: "PATCH", json: body });
            else await api("/api/tareas", { method: "POST", json: body });
            await onSaved$();
          } catch (e) {
            error.value = (e as Error).message;
          } finally {
            busy.value = false;
          }
        }}
      >
        <h2>{tarea ? "Editar tarea" : "Nueva tarea"}</h2>
        <p class="muted small">{tarea?.clienta ? `Con ${tarea.clienta}` : "Un recordatorio para ti o para alguien del equipo."}</p>
        <label>
          Qué hay que hacer
          <input value={f.titulo} onInput$={(_, el) => (f.titulo = el.value)} required maxLength={300} placeholder="Llamar a Lucía para confirmar la talla" />
        </label>
        <label>
          Vence
          <input type="datetime-local" value={f.vence} onInput$={(_, el) => (f.vence = el.value)} />
        </label>
        <div class="row wrap atajos">
          <button type="button" class="chip-btn" onClick$={() => atajo(0, 18)}>
            Hoy
          </button>
          <button type="button" class="chip-btn" onClick$={() => atajo(1)}>
            Mañana
          </button>
          <button
            type="button"
            class="chip-btn"
            onClick$={() => {
              const dow = new Date().getDay();
              atajo(((5 - dow + 7) % 7) || 7);
            }}
          >
            Viernes
          </button>
          <button type="button" class="chip-btn" onClick$={() => atajo(7)}>
            En una semana
          </button>
          <button type="button" class="chip-btn" onClick$={() => (f.vence = "")}>
            Sin fecha
          </button>
        </div>
        <label>
          Responsable
          <select value={f.responsable} onChange$={(_, el) => (f.responsable = el.value)}>
            <option value="0">Sin responsable</option>
            {miembros.value.map((m) => (
              <option key={m.id} value={String(m.id)}>
                {`${m.name || m.username}${m.role === "admin" ? " (admin)" : ""}`}
              </option>
            ))}
          </select>
        </label>
        {error.value && <p class="form-error">{error.value}</p>}
        <div class="modal-actions">
          {tarea && (
            <button
              type="button"
              class="btn btn-danger btn-sm push-left"
              onClick$={async () => {
                try {
                  await api(`/api/tareas/${tarea.id}`, { method: "DELETE" });
                  await onSaved$();
                } catch (e) {
                  error.value = (e as Error).message;
                }
              }}
            >
              Borrar
            </button>
          )}
          <button type="button" class="btn btn-ghost" onClick$={onClose$}>
            Cancelar
          </button>
          <button class="btn btn-primary" disabled={busy.value || !f.titulo.trim()}>
            {busy.value ? "Guardando…" : "Guardar"}
          </button>
        </div>
      </form>
    </div>
  );
});
