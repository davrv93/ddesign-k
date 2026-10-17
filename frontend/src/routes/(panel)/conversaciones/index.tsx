import { $, component$, sync$, useOnWindow, useSignal, useVisibleTask$ } from "@builder.io/qwik";
import type { DocumentHead } from "@builder.io/qwik-city";
import { titulo } from "~/lib/marca";
import {
  api,
  detenerSeguimiento,
  fichaConversacion,
  origenConversacion,
  seguimientoConversacion,
  fuentesDeConversacion,
  miSesion,
  type Conversation,
  type FichaConversacion,
  type FuenteUsada,
  type Message,
  type Order,
  type OrigenConversacion,
  type SeguimientoConversacion,
} from "~/lib/api";
import { LoQueSabemos } from "~/components/sabemos";
import { Confirmar } from "~/components/confirmar";
import { cuandoTexto, customerName, money, phoneLabel, STATUS, timeAgo, timeLabel, waText } from "~/lib/format";
import { u } from "~/lib/base";

interface Thread {
  conversation: Conversation;
  messages: Message[];
  orders: Order[];
  // Enlace al lead en Kommo CRM (solo si la integración está activa y la conversación ya se sincronizó).
  kommo_url?: string;
}

// Conversación que espera a una asesora (la clienta la pidió y el bot se pausó): GET /api/derivadas.
interface Derivada {
  conversation_id: number;
  customer_id: number;
  nombre: string;
  phone: string;
  asesora_id: number;
  asesora: string;
  desde: string;
  ultimo_mensaje: string;
}

const STATE_LABEL: Record<string, string> = {
  esperando_foto: "Esperando foto",
  esperando_talla: "Eligiendo talla",
  esperando_confirmacion: "Confirmando pedido",
  esperando_ubicacion: "Esperando ubicación",
  asesora: "Pidió asesora",
};

export default component$(() => {
  const convs = useSignal<Conversation[]>([]);
  const current = useSignal<number | null>(null);
  const thread = useSignal<Thread | null>(null);
  // Eliminar la conversación: solo admin (el backend también lo exige), con el diálogo del panel.
  const esAdmin = useSignal(false);
  const borrando = useSignal(false);
  const draft = useSignal("");
  const sending = useSignal(false);
  const error = useSignal("");
  const filter = useSignal("");
  const scroller = useSignal<HTMLElement>();
  // Cola de derivadas: quién pidió hablar con una asesora y desde cuándo. El bot sigue en pausa hasta que alguien le
  // responde o lo reactiva.
  const derivadas = useSignal<Derivada[]>([]);
  const soloDerivadas = useSignal(false);
  // Fuentes con las que respondió el robot en esta conversación (promos, preguntas frecuentes…).
  const usadas = useSignal<FuenteUsada[]>([]);
  // Lo que el robot sabe de la clienta (temperatura y los ocho datos que la tienda quiere descubrir).
  const ficha = useSignal<FichaConversacion | null>(null);
  // Secuencia de seguimiento en curso (paso, próximo envío) y de qué anuncio llegó la clienta.
  const seguimiento = useSignal<SeguimientoConversacion | null>(null);
  const origen = useSignal<OrigenConversacion | null>(null);
  const deteniendo = useSignal(false);

  const loadList = $(async () => {
    convs.value = await api<Conversation[]>("/api/conversations");
    try {
      derivadas.value = await api<Derivada[]>("/api/derivadas");
    } catch {
      derivadas.value = [];
    }
  });

  const loadSeguimiento = $(async () => {
    const id = current.value;
    if (id == null) return;
    try {
      const s = await seguimientoConversacion(id);
      if (current.value === id) seguimiento.value = s && s.secuencia_id ? s : null;
    } catch {
      if (current.value === id) seguimiento.value = null;
    }
  });

  const loadThread = $(async () => {
    if (current.value == null) return;
    const id = current.value;
    thread.value = await api<Thread>(`/api/conversations/${id}/messages`);
    fuentesDeConversacion(id)
      .then((fs) => {
        if (current.value !== id) return;
        // Una vez por fuente, la más reciente primero.
        const vistas = new Set<number>();
        usadas.value = [...fs]
          .sort((a, b) => b.creado_en.localeCompare(a.creado_en))
          .filter((f) => !vistas.has(f.fuente_id) && vistas.add(f.fuente_id));
      })
      .catch(() => (usadas.value = []));
    fichaConversacion(id)
      .then((f) => {
        if (current.value === id) ficha.value = f;
      })
      .catch(() => {
        if (current.value === id) ficha.value = null;
      });
    loadSeguimiento();
    origenConversacion(id)
      .then((o) => {
        if (current.value === id) origen.value = o && (o.ad_id || o.titular) ? o : null;
      })
      .catch(() => {
        if (current.value === id) origen.value = null;
      });
    setTimeout(() => scroller.value?.scrollTo({ top: scroller.value.scrollHeight }), 30);
  });

  const open = $(async (id: number) => {
    current.value = id;
    thread.value = null;
    usadas.value = [];
    ficha.value = null;
    seguimiento.value = null;
    origen.value = null;
    history.replaceState(null, "", `?c=${id}`);
    await loadThread();
  });

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    miSesion()
      .then((s) => (esAdmin.value = s.admin))
      .catch(() => {});
    await loadList();
    const c = Number(new URLSearchParams(location.search).get("c"));
    if (c) await open(c);
  });

  // El aviso de clienta caliente pide abrir una conversación estando ya en esta página.
  useOnWindow(
    "kd-abrir-conversacion",
    $(async (e: Event) => {
      const id = Number((e as CustomEvent<number>).detail);
      if (id) await open(id);
    }),
  );

  useOnWindow(
    "kd-change",
    $(async (e: Event) => {
      const topic = (e as CustomEvent<string>).detail;
      if (topic === "conversations") {
        await loadList();
        await loadThread();
      }
      if (topic === "orders") await loadThread();
      if (topic === "tareas") await loadList();
      if (topic === "seguimiento") await loadSeguimiento();
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

  const detener = $(async () => {
    if (current.value == null) return;
    deteniendo.value = true;
    error.value = "";
    try {
      await detenerSeguimiento(current.value);
      await loadSeguimiento();
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      deteniendo.value = false;
    }
  });

  const toggleBot = $(async (paused: boolean) => {
    if (current.value == null) return;
    await api(`/api/conversations/${current.value}/bot`, { method: "POST", json: { paused } });
    await loadThread();
    await loadList();
  });

  const q = filter.value.trim().toLowerCase();
  const enCola = new Map(derivadas.value.map((d) => [d.conversation_id, d]));
  let list = q
    ? convs.value.filter((c) => `${c.customer.name} ${c.customer.phone} ${c.last_message}`.toLowerCase().includes(q))
    : convs.value;
  if (soloDerivadas.value) list = list.filter((c) => enCola.has(c.id));
  const conv = thread.value?.conversation;
  const derivada = conv ? enCola.get(conv.id) : undefined;

  return (
    <div class={["page page-wide inbox", current.value != null && "has-thread"]}>
      <section class="inbox-list">
        <div class="inbox-list-head">
          <h1>Conversaciones</h1>
          <input class="search" type="search" placeholder="Buscar…" bind:value={filter} />
        </div>
        {derivadas.value.length > 0 && (
          <button
            class={["derivadas-strip", soloDerivadas.value && "active"]}
            onClick$={() => (soloDerivadas.value = !soloDerivadas.value)}
            title="Clientas que pidieron hablar con una asesora; el bot está en pausa en esos chats"
          >
            🙋‍♀️ {derivadas.value.length === 1 ? "1 clienta espera" : `${derivadas.value.length} clientas esperan`} una asesora
            <span class="muted small"> · {soloDerivadas.value ? "ver todas" : "ver solo estas"}</span>
          </button>
        )}
        {soloDerivadas.value && list.length === 0 && <p class="muted pad">Nadie espera una asesora ahora mismo.</p>}
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
                {enCola.has(c.id) && <span class="chip warn">🙋‍♀️ Pidió asesora</span>}
                {c.bot_paused && !enCola.has(c.id) && <span class="chip">Atendida por asesora</span>}
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
              <a class="btn btn-sm btn-ghost" href={u(`/clientes?c=${conv.customer.id}`)} title="Ficha del cliente: datos, notas, tareas e historial">
                Ficha
              </a>
              {thread.value!.kommo_url && (
                <a class="btn btn-sm btn-ghost" href={thread.value!.kommo_url} target="_blank" rel="noreferrer" title="Abrir el lead de esta conversación en Kommo CRM">
                  Ver en Kommo ↗
                </a>
              )}
              {esAdmin.value && (
                <button class="btn btn-sm btn-ghost btn-borrar" onClick$={() => (borrando.value = true)} title="Eliminar esta conversación y sus mensajes">
                  Eliminar conversación
                </button>
              )}
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

            {derivada && (
              <div class="derivada-aviso">
                🙋‍♀️ <b>Pidió hablar con una asesora</b> {timeAgo(derivada.desde)}
                {derivada.asesora ? ` · asignada a ${derivada.asesora}` : " · sin asesora asignada"}. El bot está en pausa en este chat:
                respóndele aquí abajo o pulsa «Reactivar bot» para que siga el guion.
              </div>
            )}
            {thread.value!.orders.filter((o) => o.status !== "cancelado").length > 0 && (
              <div class="thread-orders">
                {thread.value!.orders
                  .filter((o) => o.status !== "cancelado")
                  .slice(0, 4)
                  .map((o) => (
                    <a key={o.id} href={u(`/pedidos?pedido=${o.id}`)} class="pill" style={{ "--col": STATUS[o.status]?.color }}>
                      #{o.id} {STATUS[o.status]?.label}
                      {o.total > 0 && ` · ${money(o.total)}`}
                    </a>
                  ))}
              </div>
            )}

            {usadas.value.length > 0 && (
              <div class="thread-fuentes">
                {usadas.value.slice(0, 6).map((f) => (
                  <a key={f.fuente_id} class="chip fu-usada" href={u(`/fuentes?f=${f.fuente_id}`)} title={`Usada ${timeAgo(f.creado_en)}`}>
                    {f.tipo === "promo" ? "🏷" : "📚"} Respondió con: {f.titulo}
                  </a>
                ))}
              </div>
            )}

            {(seguimiento.value || origen.value) && (
              <div class="thread-seg">
                {origen.value && (
                  <span class="chip fu-usada" title={origen.value.llego_en ? `Llegó ${timeAgo(origen.value.llego_en)}` : undefined}>
                    📣 Llegó por anuncio: {origen.value.titular || origen.value.ad_id}
                  </span>
                )}
                {seguimiento.value && (
                  <span class={["seg-estado", seguimiento.value.estado]}>
                    ↻{" "}
                    {seguimiento.value.estado === "activo" || seguimiento.value.estado === "pausado" ? (
                      <>
                        <a href={u(`/seguimiento?s=${seguimiento.value.secuencia_id}`)} title={seguimiento.value.nombre}>
                          Seguimiento
                        </a>
                        : paso {seguimiento.value.paso} de {seguimiento.value.total}
                        {seguimiento.value.estado === "pausado"
                          ? " · en pausa"
                          : seguimiento.value.proximo_en
                            ? ` · próximo ${cuandoTexto(seguimiento.value.proximo_en)}`
                            : ""}
                        <button type="button" class="link-btn" disabled={deteniendo.value} onClick$={detener}>
                          {deteniendo.value ? "Deteniendo…" : "Detener"}
                        </button>
                      </>
                    ) : (
                      <span class="muted">
                        Seguimiento {seguimiento.value.estado === "terminado" ? "terminado" : "detenido"} ({seguimiento.value.nombre})
                      </span>
                    )}
                  </span>
                )}
              </div>
            )}

            {ficha.value && <LoQueSabemos ficha={ficha.value} />}

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
      {borrando.value && conv && (
        <Confirmar
          titulo="¿Eliminar esta conversación?"
          texto={`Se borran la conversación con ${customerName(conv.customer)} y sus ${thread.value?.messages.length ?? 0} mensajes. Su ficha de cliente y sus pedidos se conservan, y en su historial queda quién la eliminó. No se puede deshacer.`}
          confirmar="Eliminar conversación"
          peligro
          onCancelar$={() => (borrando.value = false)}
          onConfirmar$={async () => {
            await api(`/api/conversations/${conv.id}`, { method: "DELETE" });
            borrando.value = false;
            current.value = null;
            thread.value = null;
            await loadList();
          }}
        />
      )}
    </div>
  );
});

export const head: DocumentHead = { title: titulo("Conversaciones") };
