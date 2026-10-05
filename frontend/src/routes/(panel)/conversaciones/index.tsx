import { $, component$, sync$, useOnWindow, useSignal, useVisibleTask$ } from "@builder.io/qwik";
import type { DocumentHead } from "@builder.io/qwik-city";
import { api, type Conversation, type Message, type Order } from "~/lib/api";
import { customerName, money, phoneLabel, STATUS, timeAgo, timeLabel, waText } from "~/lib/format";
import { u } from "~/lib/base";

interface Thread {
  conversation: Conversation;
  messages: Message[];
  orders: Order[];
  // Enlace al lead en Kommo CRM (solo si la integración está activa y la conversación ya se sincronizó).
  kommo_url?: string;
}

const STATE_LABEL: Record<string, string> = {
  esperando_foto: "Esperando foto",
  esperando_talla: "Eligiendo talla",
  esperando_confirmacion: "Confirmando pedido",
  esperando_ubicacion: "Esperando ubicación",
  asesora: "Pidió asesora",
};

// Quién habló en el último turno (agent_last): solo se muestra cuando intervino V2.
const AGENT_LAST: Record<string, string> = { v2: "V2", "v2→v1": "V2→V1", "v2 (sombra)": "V2 sombra" };
const AGENT_LAST_HINT: Record<string, string> = {
  v2: "El último mensaje lo escribió V2",
  "v2→v1": "V2 miró el turno pero habló V1 (su plan no coincidía o no pasó el control de calidad)",
  "v2 (sombra)": "V2 corrió en sombra: la clienta recibió el texto de V1",
};

export default component$(() => {
  const convs = useSignal<Conversation[]>([]);
  const current = useSignal<number | null>(null);
  const thread = useSignal<Thread | null>(null);
  const draft = useSignal("");
  const sending = useSignal(false);
  const error = useSignal("");
  const filter = useSignal("");
  const scroller = useSignal<HTMLElement>();

  const loadList = $(async () => {
    convs.value = await api<Conversation[]>("/api/conversations");
  });

  const loadThread = $(async () => {
    if (current.value == null) return;
    thread.value = await api<Thread>(`/api/conversations/${current.value}/messages`);
    setTimeout(() => scroller.value?.scrollTo({ top: scroller.value.scrollHeight }), 30);
  });

  const open = $(async (id: number) => {
    current.value = id;
    thread.value = null;
    history.replaceState(null, "", `?c=${id}`);
    await loadThread();
  });

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    await loadList();
    const c = Number(new URLSearchParams(location.search).get("c"));
    if (c) await open(c);
  });

  useOnWindow(
    "kd-change",
    $(async (e: Event) => {
      const topic = (e as CustomEvent<string>).detail;
      if (topic === "conversations") {
        await loadList();
        await loadThread();
      }
      if (topic === "orders") await loadThread();
    }),
  );

  const send = $(async () => {
    const text = draft.value.trim();
    if (!text || current.value == null) return;
    sending.value = true;
    error.value = "";
    try {
      await api(`/api/conversations/${current.value}/send`, { method: "POST", json: { text } });
      draft.value = "";
      await loadThread();
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      sending.value = false;
    }
  });

  const toggleBot = $(async (paused: boolean) => {
    if (current.value == null) return;
    await api(`/api/conversations/${current.value}/bot`, { method: "POST", json: { paused } });
    await loadThread();
    await loadList();
  });

  // Fija la versión del agente SOLO para esta conversación: "" = lo que digan los Ajustes.
  const setAgentVersion = $(async (version: string) => {
    if (current.value == null) return;
    await api(`/api/conversations/${current.value}/agent-version`, { method: "POST", json: { version } });
    await loadThread();
    await loadList();
  });

  const q = filter.value.trim().toLowerCase();
  const list = q
    ? convs.value.filter((c) => `${c.customer.name} ${c.customer.phone} ${c.last_message}`.toLowerCase().includes(q))
    : convs.value;
  const conv = thread.value?.conversation;

  return (
    <div class={["page page-wide inbox", current.value != null && "has-thread"]}>
      <section class="inbox-list">
        <div class="inbox-list-head">
          <h1>Conversaciones</h1>
          <input class="search" type="search" placeholder="Buscar…" bind:value={filter} />
        </div>
        {list.length === 0 && <p class="muted pad">Aún no hay conversaciones. Cuando un cliente escriba al WhatsApp vinculado aparecerá aquí.</p>}
        {list.map((c) => (
          <button key={c.id} class={["conv", current.value === c.id && "active"]} onClick$={() => open(c.id)}>
            <span class="avatar">{customerName(c.customer).replace("+", "").slice(0, 1).toUpperCase()}</span>
            <span class="conv-body">
              <span class="conv-top">
                <strong>{customerName(c.customer)}</strong>
                <span class="muted small">{timeAgo(c.last_message_at)}</span>
              </span>
              <span class="conv-last">
                {c.bot_paused && <span class="chip warn">Asesora</span>}
                {c.agent_last && AGENT_LAST[c.agent_last] && (
                  <span class="chip v2" title={AGENT_LAST_HINT[c.agent_last]}>
                    {AGENT_LAST[c.agent_last]}
                  </span>
                )}
                {!c.bot_paused && STATE_LABEL[c.state] && <span class="chip">{STATE_LABEL[c.state]}</span>}
                {waText(c.last_message)}
              </span>
            </span>
            {c.unread > 0 && <span class="badge">{c.unread}</span>}
          </button>
        ))}
      </section>

      <section class="thread">
        {current.value == null ? (
          <div class="thread-empty muted">Elige una conversación</div>
        ) : !conv ? (
          <div class="loading">Cargando…</div>
        ) : (
          <>
            <header class="thread-head">
              <button class="btn btn-ghost back" onClick$={() => (current.value = null)}>
                ←
              </button>
              <div class="grow">
                <strong>{customerName(conv.customer)}</strong>
                <div class="muted small">
                  {conv.customer.phone && (
                    <a href={`https://wa.me/${conv.customer.phone}`} target="_blank" rel="noreferrer">
                      {phoneLabel(conv.customer.phone)}
                    </a>
                  )}
                  {STATE_LABEL[conv.state] && ` · ${STATE_LABEL[conv.state]}`}
                </div>
              </div>
              {thread.value!.kommo_url && (
                <a class="btn btn-sm btn-ghost" href={thread.value!.kommo_url} target="_blank" rel="noreferrer" title="Abrir el lead de esta conversación en Kommo CRM">
                  Ver en Kommo ↗
                </a>
              )}
              <div class="agent-ver" title="Versión del agente solo para esta conversación. «Auto» sigue los Ajustes.">
                <span class="muted small">Agente</span>
                <div class="seg" role="group" aria-label="Versión del agente en esta conversación">
                  {[
                    { v: "", label: "Auto" },
                    { v: "v1", label: "V1" },
                    { v: "v2", label: "V2" },
                  ].map((o) => (
                    <button
                      key={o.v}
                      type="button"
                      class={(conv.agent_version ?? "") === o.v && "on"}
                      aria-pressed={(conv.agent_version ?? "") === o.v}
                      onClick$={() => setAgentVersion(o.v)}
                    >
                      {o.label}
                    </button>
                  ))}
                </div>
              </div>
              {conv.bot_paused ? (
                <button class="btn btn-sm btn-primary" onClick$={() => toggleBot(false)} title="El bot vuelve a responder desde el menú">
                  🤖 Reactivar bot
                </button>
              ) : (
                <button class="btn btn-sm" onClick$={() => toggleBot(true)} title="El bot deja de responder en este chat">
                  ✋ Atender yo
                </button>
              )}
            </header>

            {thread.value!.orders.filter((o) => o.status !== "cancelado").length > 0 && (
              <div class="thread-orders">
                {thread.value!.orders
                  .filter((o) => o.status !== "cancelado")
                  .slice(0, 4)
                  .map((o) => (
                    <a key={o.id} href={u(`/?pedido=${o.id}`)} class="pill" style={{ "--col": STATUS[o.status]?.color }}>
                      #{o.id} {STATUS[o.status]?.label}
                      {o.total > 0 && ` · ${money(o.total)}`}
                    </a>
                  ))}
              </div>
            )}

            <div class="messages" ref={scroller}>
              {thread.value!.messages.map((m) => (
                <div key={m.id} class={["msg", m.direction, m.author]}>
                  {m.kind === "image" && m.media && (
                    <a href={u(m.media)} target="_blank" rel="noreferrer">
                      <img src={u(m.media)} alt="Imagen" width={220} height={280} loading="lazy" />
                    </a>
                  )}
                  {m.kind === "location" ? (
                    <a href={`https://www.google.com/maps?q=${m.body.split(" ")[0]}`} target="_blank" rel="noreferrer">
                      📍 Ver ubicación {m.body.split(" ").slice(1).join(" ")}
                    </a>
                  ) : (
                    m.body && <p>{waText(m.body)}</p>
                  )}
                  <span class="msg-meta">
                    {m.direction === "out" && (m.author === "bot" ? "🤖 Bot · " : "🙋‍♀️ Asesora · ")}
                    {timeLabel(m.created_at)}
                    {m.status === "pending" && " · ⏳"}
                    {m.status === "failed" && <b class="failed"> · ⚠ No enviado</b>}
                  </span>
                </div>
              ))}
            </div>

            <form class="composer" preventdefault:submit onSubmit$={send}>
              <textarea
                rows={2}
                placeholder={conv.bot_paused ? "Escribe tu respuesta…" : "Al escribir, el bot se pausa en este chat…"}
                bind:value={draft}
                onKeyDown$={sync$((e: KeyboardEvent, el: HTMLTextAreaElement) => {
                  // Enter envía, Shift+Enter hace salto de línea.
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    el.form?.requestSubmit();
                  }
                })}
              />
              <button class="btn btn-primary" disabled={sending.value || !draft.value.trim()}>
                Enviar
              </button>
            </form>
            {error.value && <p class="form-error pad">{error.value}</p>}
          </>
        )}
      </section>
    </div>
  );
});

export const head: DocumentHead = { title: "Conversaciones · Baruka Design" };
