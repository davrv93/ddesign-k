// Embudos propios (por promo) en el tablero de pedidos. El general es el tablero de siempre y no pasa por aquí:
// la etapa de un embudo propio va aparte y NO cambia el estado del pedido (el stock sigue dependiendo del estado).
import { $, component$, sync$, useOnWindow, useSignal, useStore, useVisibleTask$, type QRL } from "@builder.io/qwik";
import { moverEnEmbudo, pedidosDeEmbudo, sacarDeEmbudo, type Embudo, type PedidoEmbudo } from "~/lib/api";
import { u } from "~/lib/base";
import { customerName, money, phoneLabel, STATUS, timeAgo } from "~/lib/format";

// Colores de las etapas, en orden (las etapas son texto libre).
const COLORES = ["#8b5cf6", "#0ea5e9", "#f59e0b", "#10b981", "#6366f1", "#ec4899", "#64748b", "#ef4444"];

interface BoardProps {
  id: number;
  filtro: string;
  onAbrir$: QRL<(orderId: number) => void>;
  onToast$: QRL<(text: string, error?: boolean) => void>;
}

export const EmbudoBoard = component$<BoardProps>(({ id, filtro, onAbrir$, onToast$ }) => {
  const st = useStore<{ embudo: Embudo | null; pedidos: PedidoEmbudo[] }>({ embudo: null, pedidos: [] });
  const cargando = useSignal(true);
  const dragging = useSignal<number | null>(null);
  const overCol = useSignal<string | null>(null);
  const menu = useSignal<number | null>(null);

  const load = $(async () => {
    try {
      const r = await pedidosDeEmbudo(id);
      st.embudo = r.embudo;
      st.pedidos = r.pedidos;
    } catch (e) {
      onToast$((e as Error).message, true);
    } finally {
      cargando.value = false;
    }
  });

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    await load();
  });
  useOnWindow(
    "kd-change",
    $((e: Event) => {
      const t = (e as CustomEvent<string>).detail;
      if (t === "orders" || t === "embudos") load();
    }),
  );

  const mover = $(async (orderId: number, etapa: string) => {
    const o = st.pedidos.find((x) => x.id === orderId);
    if (!o || o.etapa === etapa) return;
    const prev = o.etapa;
    o.etapa = etapa; // optimista
    try {
      await moverEnEmbudo(id, orderId, etapa);
      onToast$(`Pedido #${orderId} → ${etapa}`);
    } catch (e) {
      o.etapa = prev;
      onToast$((e as Error).message, true);
    }
  });

  const sacar = $(async (orderId: number) => {
    menu.value = null;
    try {
      await sacarDeEmbudo(id, orderId);
      st.pedidos = st.pedidos.filter((x) => x.id !== orderId);
      onToast$(`Pedido #${orderId} fuera del embudo (sigue en el general)`);
    } catch (e) {
      onToast$((e as Error).message, true);
    }
  });

  if (cargando.value) return <div class="loading">Cargando embudo…</div>;
  const etapas = st.embudo?.etapas ?? [];
  const q = filtro.trim().toLowerCase();
  const visibles = q
    ? st.pedidos.filter((o) =>
        [String(o.id), o.customer.name, o.customer.phone, o.notes, ...o.items.map((i) => `${i.product_code} ${i.product_name}`)]
          .join(" ")
          .toLowerCase()
          .includes(q),
      )
    : st.pedidos;
  // Un pedido con una etapa que ya no existe (se renombró) se muestra en la primera.
  const etapaDe = (o: PedidoEmbudo) => (etapas.includes(o.etapa) ? o.etapa : etapas[0]);

  return (
    <>
      <p class="muted small emb-nota">
        Embudo <b>{st.embudo?.nombre}</b>: las etapas son solo de este tablero; el estado del pedido (y el stock) se cambia en el general.
      </p>
      {etapas.length === 0 ? (
        <p class="muted">Este embudo no tiene etapas.</p>
      ) : (
        <div class="kanban">
          {etapas.map((et, i) => {
            const cards = visibles.filter((o) => etapaDe(o) === et);
            return (
              <section
                key={et}
                class={["col", overCol.value === et && "drop-over"]}
                style={{ "--col": COLORES[i % COLORES.length] }}
                preventdefault:dragover
                preventdefault:drop
                onDragOver$={() => (overCol.value = et)}
                onDragLeave$={() => (overCol.value = null)}
                onDrop$={() => {
                  overCol.value = null;
                  if (dragging.value != null) mover(dragging.value, et);
                  dragging.value = null;
                }}
              >
                <header class="col-head">
                  <span class="col-dot" />
                  <h2>{et}</h2>
                  <span class="count">{cards.length}</span>
                </header>
                <div class="col-body emb-body">
                  {cards.map((o) => (
                    <article
                      key={o.id}
                      class={["card", dragging.value === o.id && "dragging"]}
                      draggable
                      data-id={o.id}
                      onDragStart$={[
                        sync$((e: DragEvent, el: HTMLElement) => {
                          e.dataTransfer?.setData("text/plain", el.dataset.id ?? "");
                          if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
                        }),
                        $(() => (dragging.value = o.id)),
                      ]}
                      onDragEnd$={() => (dragging.value = null)}
                      onClick$={() => onAbrir$(o.id)}
                    >
                      <div class="card-top">
                        <span class="order-id">#{o.id}</span>
                        <span class="muted small">{timeAgo(o.updated_at)}</span>
                        <button
                          class="emb-menu-btn"
                          aria-label="Opciones del pedido"
                          aria-expanded={menu.value === o.id}
                          onClick$={(e) => {
                            e.stopPropagation();
                            menu.value = menu.value === o.id ? null : o.id;
                          }}
                        >
                          ⋯
                        </button>
                      </div>
                      {menu.value === o.id && (
                        <div class="emb-menu" onClick$={(e) => e.stopPropagation()}>
                          {etapas
                            .filter((x) => x !== etapaDe(o))
                            .map((x) => (
                              <button
                                key={x}
                                onClick$={async () => {
                                  menu.value = null;
                                  await mover(o.id, x);
                                }}
                              >
                                Mover a {x}
                              </button>
                            ))}
                          <button class="emb-sacar" onClick$={() => sacar(o.id)}>
                            Sacar del embudo
                          </button>
                        </div>
                      )}
                      <div class="card-customer">
                        <strong>{customerName(o.customer)}</strong>
                        <span class="muted small">{phoneLabel(o.customer.phone)}</span>
                      </div>
                      {o.items.slice(0, 2).map((it) => (
                        <div class="card-item" key={it.id}>
                          {it.image && <img src={u(it.image)} alt="" width={40} height={52} loading="lazy" />}
                          <div>
                            <b>{it.product_code}</b> {it.product_name}
                            <div class="muted small">
                              {it.size ? `Talla ${it.size} · ` : ""}×{it.qty}
                            </div>
                          </div>
                        </div>
                      ))}
                      {o.items.length === 0 && <div class="muted small">{o.notes || "Sin productos"}</div>}
                      <div class="card-foot">
                        {o.total > 0 && <span class="total">{money(o.total)}</span>}
                        <span class="pill" style={{ "--col": STATUS[o.status]?.color }}>
                          {STATUS[o.status]?.label ?? o.status}
                        </span>
                      </div>
                    </article>
                  ))}
                  {cards.length === 0 && <div class="col-empty">Sin pedidos</div>}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </>
  );
});

// ---------------------------------------------------------------------------
// «Añadir a embudo» desde la ficha de un pedido del tablero general.

export const AnadirAEmbudo = component$<{ orderId: number; embudos: Embudo[]; onToast$: QRL<(text: string, error?: boolean) => void> }>(
  ({ orderId, embudos, onToast$ }) => {
    const sel = useSignal(String(embudos[0]?.id ?? ""));
    const etapa = useSignal(embudos[0]?.etapas?.[0] ?? "");
    const busy = useSignal(false);
    const e = embudos.find((x) => String(x.id) === sel.value);

    return (
      <div class="row wrap emb-anadir">
        <select
          class="auto"
          aria-label="Embudo"
          value={sel.value}
          onChange$={(_, el) => {
            sel.value = el.value;
            etapa.value = embudos.find((x) => String(x.id) === el.value)?.etapas?.[0] ?? "";
          }}
        >
          {embudos.map((x) => (
            <option key={x.id} value={String(x.id)}>
              {x.nombre}
            </option>
          ))}
        </select>
        <select class="auto" aria-label="Etapa" value={etapa.value} onChange$={(_, el) => (etapa.value = el.value)}>
          {(e?.etapas ?? []).map((x) => (
            <option key={x} value={x}>
              {x}
            </option>
          ))}
        </select>
        <button
          class="btn btn-sm"
          disabled={busy.value || !e || !etapa.value}
          onClick$={async () => {
            if (!e) return;
            busy.value = true;
            try {
              await moverEnEmbudo(e.id, orderId, etapa.value);
              onToast$(`Pedido #${orderId} en «${e.nombre}» · ${etapa.value}`);
            } catch (err) {
              onToast$((err as Error).message, true);
            } finally {
              busy.value = false;
            }
          }}
        >
          Añadir a embudo
        </button>
      </div>
    );
  },
);
