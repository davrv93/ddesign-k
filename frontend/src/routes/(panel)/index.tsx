import { $, component$, sync$, useComputed$, useOnWindow, useSignal, useStore, useVisibleTask$, type QRL } from "@builder.io/qwik";
import type { DocumentHead } from "@builder.io/qwik-city";
import { api, type Order, type Product, type Stats } from "~/lib/api";
import { customerName, mapsLink, money, phoneLabel, STATUS, timeAgo } from "~/lib/format";

interface Board {
  statuses: string[];
  orders: Order[];
}

export default component$(() => {
  const board = useStore<Board>({ statuses: Object.keys(STATUS), orders: [] });
  const stats = useSignal<Stats | null>(null);
  const products = useSignal<Product[]>([]);
  const loading = useSignal(true);
  const dragging = useSignal<number | null>(null);
  const overCol = useSignal<string | null>(null);
  const selectedId = useSignal<number | null>(null);
  const toast = useSignal<{ text: string; error?: boolean } | null>(null);
  const showNew = useSignal(false);
  const filter = useSignal("");

  const notify = $((text: string, error = false) => {
    toast.value = { text, error };
    setTimeout(() => (toast.value = null), 3500);
  });

  const load = $(async () => {
    try {
      const [b, s] = await Promise.all([api<Board>("/api/orders"), api<Stats>("/api/stats")]);
      board.statuses = b.statuses;
      board.orders = b.orders;
      stats.value = s;
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      loading.value = false;
    }
  });

  const loadProducts = $(async () => {
    products.value = await api<Product[]>("/api/products");
  });

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    await Promise.all([load(), loadProducts()]);
    const id = Number(new URLSearchParams(location.search).get("pedido"));
    if (id) selectedId.value = id;
  });

  useOnWindow(
    "kd-change",
    $((e: Event) => {
      const topic = (e as CustomEvent<string>).detail;
      if (topic === "orders") load();
      if (topic === "products") loadProducts();
    }),
  );

  const move = $(async (id: number, status: string, notifyCustomer = true) => {
    const o = board.orders.find((x) => x.id === id);
    if (!o || o.status === status) return;
    const prev = o.status;
    o.status = status; // optimista
    try {
      await api(`/api/orders/${id}`, { method: "PATCH", json: { status, notify: notifyCustomer } });
      notify(`Pedido #${id} → ${STATUS[status].label}`);
      load();
    } catch (e) {
      o.status = prev;
      notify((e as Error).message, true);
    }
  });

  const visible = useComputed$(() => {
    const q = filter.value.trim().toLowerCase();
    if (!q) return board.orders;
    return board.orders.filter((o) =>
      [String(o.id), o.customer.name, o.customer.phone, o.notes, ...o.items.map((i) => `${i.product_code} ${i.product_name}`)]
        .join(" ")
        .toLowerCase()
        .includes(q),
    );
  });

  const selected = board.orders.find((o) => o.id === selectedId.value) ?? null;

  return (
    <div class="page page-wide">
      <div class="page-head">
        <div>
          <h1>Pedidos</h1>
          <p class="muted">Arrastra las tarjetas entre columnas. El stock se descuenta al confirmar y vuelve al cancelar.</p>
        </div>
        <div class="head-actions">
          <input class="search" type="search" placeholder="Buscar cliente, código, #pedido…" bind:value={filter} />
          <button class="btn btn-primary" onClick$={() => (showNew.value = true)}>
            + Pedido manual
          </button>
        </div>
      </div>

      {stats.value && (
        <div class="stats">
          <div class="stat">
            <span>Consultas abiertas</span>
            <strong>{stats.value.open_inquiries}</strong>
          </div>
          <div class="stat">
            <span>Por confirmar</span>
            <strong>{stats.value.by_status.pendiente ?? 0}</strong>
          </div>
          <div class="stat">
            <span>Por despachar</span>
            <strong>{(stats.value.by_status.confirmado ?? 0) + (stats.value.by_status.preparando ?? 0)}</strong>
          </div>
          <div class="stat">
            <span>Ventas del mes</span>
            <strong>{money(stats.value.sales_month)}</strong>
          </div>
          <div class="stat">
            <span>Tallas con stock ≤ 1</span>
            <strong class={stats.value.low_stock > 0 ? "warn" : ""}>{stats.value.low_stock}</strong>
          </div>
        </div>
      )}

      {loading.value ? (
        <div class="loading">Cargando pedidos…</div>
      ) : (
        <div class="kanban">
          {board.statuses.map((st) => {
            const cards = visible.value.filter((o) => o.status === st);
            const meta = STATUS[st] ?? { label: st, color: "#999", hint: "" };
            return (
              <section
                key={st}
                class={["col", overCol.value === st && "drop-over"]}
                style={{ "--col": meta.color }}
                preventdefault:dragover
                preventdefault:drop
                onDragOver$={() => (overCol.value = st)}
                onDragLeave$={() => (overCol.value = null)}
                onDrop$={() => {
                  overCol.value = null;
                  if (dragging.value != null) move(dragging.value, st);
                  dragging.value = null;
                }}
              >
                <header class="col-head">
                  <span class="col-dot" />
                  <h2>{meta.label}</h2>
                  <span class="count">{cards.length}</span>
                </header>
                <p class="col-hint">{meta.hint}</p>
                <div class="col-body">
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
                      onClick$={() => (selectedId.value = o.id)}
                    >
                      <div class="card-top">
                        <span class="order-id">#{o.id}</span>
                        <span class="muted small">{timeAgo(o.updated_at)}</span>
                      </div>
                      <div class="card-customer">
                        <strong>{customerName(o.customer)}</strong>
                        <span class="muted small">{phoneLabel(o.customer.phone)}</span>
                      </div>
                      {o.items.length > 0 ? (
                        o.items.map((it) => (
                          <div class="card-item" key={it.id}>
                            {it.image && <img src={it.image} alt="" width={40} height={52} loading="lazy" />}
                            <div>
                              <div>
                                <b>{it.product_code}</b> {it.product_name}
                              </div>
                              <div class="muted small">
                                {it.size ? `Talla ${it.size} · ` : ""}×{it.qty}
                              </div>
                            </div>
                          </div>
                        ))
                      ) : o.customer_image ? (
                        <div class="card-item">
                          <img src={o.customer_image} alt="Foto del cliente" width={40} height={52} loading="lazy" />
                          <div class="muted small">{o.notes || "Foto enviada por el cliente"}</div>
                        </div>
                      ) : (
                        <div class="muted small">{o.notes || "Sin productos"}</div>
                      )}
                      <div class="card-foot">
                        {o.total > 0 && <span class="total">{money(o.total)}</span>}
                        {o.customer_image && o.items.length > 0 && (
                          <span class="chip" title="Identificado por IA desde la foto del cliente">
                            📸 IA {Math.round(o.match_confidence * 100)}%
                          </span>
                        )}
                        {o.location_lat != null || o.location_text ? (
                          <span class="chip ok">📍 Ubicación</span>
                        ) : (
                          o.stock_reserved && <span class="chip warn">Sin ubicación</span>
                        )}
                        {o.source === "manual" && <span class="chip">Manual</span>}
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

      {selected && (
        <OrderDrawer
          key={selected.id}
          order={selected}
          onClose$={() => (selectedId.value = null)}
          onMove$={move}
          onSaved$={load}
          onToast$={notify}
        />
      )}

      {showNew.value && (
        <NewOrderModal
          products={products.value}
          onClose$={() => (showNew.value = false)}
          onCreated$={async (id: number) => {
            showNew.value = false;
            notify(`Pedido #${id} registrado`);
            await load();
          }}
        />
      )}

      {toast.value && <div class={["toast", toast.value.error && "toast-error"]}>{toast.value.text}</div>}
    </div>
  );
});

// ---------------------------------------------------------------------------

interface DrawerProps {
  order: Order;
  onClose$: QRL<() => void>;
  onMove$: QRL<(id: number, status: string, notify?: boolean) => Promise<void>>;
  onSaved$: QRL<() => Promise<void>>;
  onToast$: QRL<(text: string, error?: boolean) => void>;
}

const OrderDrawer = component$<DrawerProps>(({ order, onClose$, onMove$, onSaved$, onToast$ }) => {
  const notes = useSignal(order.notes);
  const target = useSignal(order.status);
  const notifyCustomer = useSignal(true);
  const saving = useSignal(false);
  const map = mapsLink(order.location_lat, order.location_lng, order.location_text);

  return (
    <div class="overlay" onClick$={onClose$}>
      <aside class="drawer" onClick$={(e) => e.stopPropagation()}>
        <header class="drawer-head">
          <div>
            <h2>Pedido #{order.id}</h2>
            <span class="pill" style={{ "--col": STATUS[order.status]?.color }}>
              {STATUS[order.status]?.label}
            </span>
            {order.stock_reserved && <span class="chip ok">Stock reservado</span>}
          </div>
          <button class="btn btn-ghost" aria-label="Cerrar" onClick$={onClose$}>
            ✕
          </button>
        </header>

        <section class="drawer-sec">
          <h3>Cliente</h3>
          <p>
            <strong>{customerName(order.customer)}</strong>
            <br />
            {order.customer.phone && (
              <a href={`https://wa.me/${order.customer.phone}`} target="_blank" rel="noreferrer">
                {phoneLabel(order.customer.phone)}
              </a>
            )}
          </p>
          {order.conversation_id > 0 && (
            <a class="btn btn-sm" href={`/conversaciones?c=${order.conversation_id}`}>
              Ver conversación
            </a>
          )}
        </section>

        {order.customer_image && (
          <section class="drawer-sec">
            <h3>Foto enviada por el cliente</h3>
            <a href={order.customer_image} target="_blank" rel="noreferrer">
              <img class="drawer-photo" src={order.customer_image} alt="Foto del cliente" width={320} height={400} />
            </a>
            {order.match_confidence > 0 && <p class="muted small">Coincidencia IA: {Math.round(order.match_confidence * 100)}%</p>}
          </section>
        )}

        <section class="drawer-sec">
          <h3>Productos</h3>
          {order.items.length === 0 && <p class="muted">Sin productos (consulta).</p>}
          {order.items.map((it) => (
            <div class="line" key={it.id}>
              {it.image && <img src={it.image} alt="" width={48} height={64} />}
              <div class="grow">
                <b>{it.product_code}</b> {it.product_name}
                <div class="muted small">
                  {it.size && `Talla ${it.size} · `}
                  {it.qty} × {money(it.unit_price)}
                </div>
              </div>
              <b>{money(it.qty * it.unit_price)}</b>
            </div>
          ))}
          {order.total > 0 && (
            <div class="line total-line">
              <span class="grow">Total</span>
              <b>{money(order.total)}</b>
            </div>
          )}
        </section>

        <section class="drawer-sec">
          <h3>Entrega</h3>
          {map ? (
            <p>
              <a href={map} target="_blank" rel="noreferrer">
                📍 {order.location_text || `${order.location_lat?.toFixed(5)}, ${order.location_lng?.toFixed(5)}`}
              </a>
            </p>
          ) : (
            <p class="muted">El cliente aún no envió su ubicación.</p>
          )}
        </section>

        <section class="drawer-sec">
          <h3>Mover a</h3>
          <div class="row">
            <select bind:value={target}>
              {Object.entries(STATUS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v.label}
                </option>
              ))}
            </select>
            <button
              class="btn btn-primary"
              disabled={target.value === order.status}
              onClick$={() => onMove$(order.id, target.value, notifyCustomer.value)}
            >
              Mover
            </button>
          </div>
          <label class="check">
            <input type="checkbox" bind:checked={notifyCustomer} /> Avisar al cliente por WhatsApp
          </label>
        </section>

        <section class="drawer-sec">
          <h3>Notas internas</h3>
          <textarea rows={3} bind:value={notes} placeholder="Pago por Yape, entrega el sábado…" />
          <button
            class="btn btn-sm"
            disabled={saving.value || notes.value === order.notes}
            onClick$={async () => {
              saving.value = true;
              try {
                await api(`/api/orders/${order.id}`, { method: "PATCH", json: { notes: notes.value } });
                onToast$("Notas guardadas");
                await onSaved$();
              } catch (e) {
                onToast$((e as Error).message, true);
              } finally {
                saving.value = false;
              }
            }}
          >
            Guardar notas
          </button>
        </section>

        <footer class="drawer-foot">
          <span class="muted small">
            Creado {timeAgo(order.created_at)} · {order.source === "manual" ? "manual" : "WhatsApp"}
          </span>
          <button
            class="btn btn-danger btn-sm"
            onClick$={async () => {
              if (!confirm(`¿Eliminar el pedido #${order.id}? Si tenía stock reservado, se devuelve.`)) return;
              try {
                await api(`/api/orders/${order.id}`, { method: "DELETE" });
                onToast$(`Pedido #${order.id} eliminado`);
                await onClose$();
                await onSaved$();
              } catch (e) {
                onToast$((e as Error).message, true);
              }
            }}
          >
            Eliminar
          </button>
        </footer>
      </aside>
    </div>
  );
});

// ---------------------------------------------------------------------------

interface NewOrderProps {
  products: Product[];
  onClose$: QRL<() => void>;
  onCreated$: QRL<(id: number) => Promise<void>>;
}

const NewOrderModal = component$<NewOrderProps>(({ products, onClose$, onCreated$ }) => {
  const form = useStore({ phone: "51", name: "", product_id: "", size: "", qty: 1, status: "confirmado", address: "", notes: "" });
  const error = useSignal("");
  const busy = useSignal(false);
  const product = products.find((p) => String(p.id) === form.product_id);

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
            const o = await api<Order>("/api/orders", {
              method: "POST",
              json: { ...form, product_id: Number(form.product_id) || 0, qty: Number(form.qty) || 1 },
            });
            await onCreated$(o.id);
          } catch (e) {
            error.value = (e as Error).message;
          } finally {
            busy.value = false;
          }
        }}
      >
        <h2>Pedido manual</h2>
        <p class="muted small">Para ventas tomadas por teléfono, Instagram o en tienda.</p>
        <div class="grid2">
          <label>
            Celular (con 51)
            <input value={form.phone} onInput$={(_, el) => (form.phone = el.value)} inputMode="tel" required placeholder="51987654321" />
          </label>
          <label>
            Nombre
            <input value={form.name} onInput$={(_, el) => (form.name = el.value)} placeholder="Nombre del cliente" />
          </label>
        </div>
        <label>
          Producto
          <select
            value={form.product_id}
            onChange$={(_, el) => {
              form.product_id = el.value;
              form.size = "";
            }}
          >
            <option value="">— Sin producto (sólo consulta) —</option>
            {products
              .filter((p) => p.active)
              .map((p) => (
                <option key={p.id} value={String(p.id)}>
                  {`${p.code} · ${p.name} · ${money(p.price)}`}
                </option>
              ))}
          </select>
        </label>
        {product && (
          <div class="grid2">
            <label>
              Talla
              <select value={form.size} onChange$={(_, el) => (form.size = el.value)} required>
                <option value="">Elegir…</option>
                {product.variants.map((v) => (
                  <option key={v.size} value={v.size} disabled={v.stock <= 0}>
                    {`${v.size} (${v.stock} en stock)`}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Cantidad
              <input type="number" min={1} value={form.qty} onInput$={(_, el) => (form.qty = Number(el.value))} />
            </label>
          </div>
        )}
        <label>
          Estado inicial
          <select value={form.status} onChange$={(_, el) => (form.status = el.value)}>
            {Object.entries(STATUS).map(([k, v]) => (
              <option key={k} value={k}>
                {v.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Dirección de entrega
          <input value={form.address} onInput$={(_, el) => (form.address = el.value)} placeholder="Av. …, distrito, referencia" />
        </label>
        <label>
          Notas
          <textarea rows={2} value={form.notes} onInput$={(_, el) => (form.notes = el.value)} />
        </label>
        {error.value && <p class="form-error">{error.value}</p>}
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost" onClick$={onClose$}>
            Cancelar
          </button>
          <button class="btn btn-primary" disabled={busy.value}>
            {busy.value ? "Guardando…" : "Registrar pedido"}
          </button>
        </div>
      </form>
    </div>
  );
});

export const head: DocumentHead = { title: "Pedidos · Baruka Design" };
