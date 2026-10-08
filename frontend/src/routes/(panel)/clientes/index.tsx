import { $, component$, useOnWindow, useSignal, useStore, useVisibleTask$, type QRL } from "@builder.io/qwik";
import type { DocumentHead } from "@builder.io/qwik-city";
import { titulo } from "~/lib/marca";
import {
  api,
  descargar,
  equipo,
  miSesion,
  type Clienta,
  type Evento,
  type Miembro,
  type Nota,
  type Order,
  type Sesion,
  type Tarea,
} from "~/lib/api";
import { u } from "~/lib/base";
import { ETAPAS, STATUS, customerName, dateLabel, iniciales2, money, phoneLabel, timeAgo, timeLabel, waText } from "~/lib/format";
import { EtapaPill, NotasPanel, TareaFila, TareaModal, alternarTarea } from "~/components/crm";

interface Filtros {
  q: string;
  etapa: string;
  etiqueta: string;
  asesora: string;
  compra: string;
}

const qs = (f: Filtros) =>
  Object.entries(f)
    .filter(([, v]) => v)
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
    .join("&");

export default component$(() => {
  const sel = useSignal<number | null>(null);
  const lista = useSignal<Clienta[]>([]);
  const cargando = useSignal(true);
  const etiquetas = useSignal<{ etiqueta: string; clientas: number }[]>([]);
  const miembros = useSignal<Miembro[]>([]);
  const yo = useSignal<Sesion | null>(null);
  const f = useStore<Filtros>({ q: "", etapa: "", etiqueta: "", asesora: "", compra: "" });
  const nueva = useSignal(false);
  const toast = useSignal<{ text: string; error?: boolean } | null>(null);
  const timer = useSignal<number>(0);

  const notify = $((text: string, error = false) => {
    toast.value = { text, error };
    setTimeout(() => (toast.value = null), 3500);
  });

  const load = $(async () => {
    try {
      lista.value = await api<Clienta[]>(`/api/clientas?${qs(f)}`);
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      cargando.value = false;
    }
  });

  const abrir = $((id: number | null) => {
    sel.value = id;
    const p = new URLSearchParams(location.search);
    if (id) p.set("c", String(id));
    else p.delete("c");
    history.pushState(null, "", `${location.pathname}${p.toString() ? `?${p}` : ""}`);
    window.scrollTo({ top: 0 });
  });

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async ({ cleanup }) => {
    const p = new URLSearchParams(location.search);
    f.etapa = p.get("etapa") ?? "";
    f.etiqueta = p.get("etiqueta") ?? "";
    sel.value = Number(p.get("c")) || null;
    const onPop = () => (sel.value = Number(new URLSearchParams(location.search).get("c")) || null);
    window.addEventListener("popstate", onPop);
    cleanup(() => window.removeEventListener("popstate", onPop));
    miSesion().then((s) => (yo.value = s));
    equipo().then((ms) => (miembros.value = ms));
    api<{ etiqueta: string; clientas: number }[]>("/api/etiquetas").then((e) => (etiquetas.value = e));
    await load();
  });

  useOnWindow(
    "kd-change",
    $((e: Event) => {
      const t = (e as CustomEvent<string>).detail;
      if (t === "clientas" || t === "orders" || t === "conversations") load();
    }),
  );

  const filtrar = $((k: keyof Filtros, v: string) => {
    f[k] = v;
    clearTimeout(timer.value);
    timer.value = window.setTimeout(() => load(), k === "q" ? 250 : 0);
  });

  if (sel.value) {
    return (
      <>
        <Ficha
          key={sel.value}
          id={sel.value}
          miembros={miembros.value}
          etiquetas={etiquetas.value.map((e) => e.etiqueta)}
          onBack$={async () => {
            await abrir(null);
            await load();
          }}
          onToast$={notify}
        />
        {toast.value && <div class={["toast", toast.value.error && "toast-error"]}>{toast.value.text}</div>}
      </>
    );
  }

  const hayFiltros = !!(f.q || f.etapa || f.etiqueta || f.asesora || f.compra);

  return (
    <div class="page page-wide">
      <div class="page-head">
        <div>
          <h1>Clientes</h1>
          <p class="muted">Todos los que escribieron o compraron, con su etapa, su asesora y lo que llevan comprado.</p>
        </div>
        <div class="head-actions">
          <input class="search" type="search" placeholder="Nombre, teléfono, correo, ciudad…" value={f.q} onInput$={(_, el) => filtrar("q", el.value)} />
          {yo.value?.admin && (
            <button
              class="btn"
              onClick$={async () => {
                try {
                  await descargar("/api/export/clientas.csv", `clientes-${new Date().toISOString().slice(0, 10)}.csv`);
                } catch (e) {
                  notify((e as Error).message, true);
                }
              }}
            >
              ⬇ CSV
            </button>
          )}
          <button class="btn btn-primary" onClick$={() => (nueva.value = true)}>
            + Cliente
          </button>
        </div>
      </div>

      <div class="filters">
        <select value={f.etapa} onChange$={(_, el) => filtrar("etapa", el.value)} aria-label="Etapa">
          <option value="">Todas las etapas</option>
          {ETAPAS.map((e) => (
            <option key={e.key} value={e.key}>
              {e.label}
            </option>
          ))}
          <option value="sin">Sin etapa</option>
        </select>
        <select value={f.etiqueta} onChange$={(_, el) => filtrar("etiqueta", el.value)} aria-label="Etiqueta">
          <option value="">Todas las etiquetas</option>
          {etiquetas.value.map((e) => (
            <option key={e.etiqueta} value={e.etiqueta}>
              {`${e.etiqueta} (${e.clientas})`}
            </option>
          ))}
        </select>
        <select value={f.asesora} onChange$={(_, el) => filtrar("asesora", el.value)} aria-label="Asesora">
          <option value="">Cualquier asesora</option>
          <option value="yo">Mis clientes</option>
          <option value="sin">Sin asignar</option>
          {miembros.value.map((m) => (
            <option key={m.id} value={String(m.id)}>
              {m.name || m.username}
            </option>
          ))}
        </select>
        <select value={f.compra} onChange$={(_, el) => filtrar("compra", el.value)} aria-label="Compras">
          <option value="">Con o sin compra</option>
          <option value="con">Ya compraron</option>
          <option value="sin">Aún no compran</option>
        </select>
        {hayFiltros && (
          <button
            class="btn btn-ghost btn-sm"
            onClick$={() => {
              Object.assign(f, { q: "", etapa: "", etiqueta: "", asesora: "", compra: "" });
              load();
            }}
          >
            Limpiar
          </button>
        )}
        <span class="muted small push-right">{lista.value.length} clientes</span>
      </div>

      {cargando.value ? (
        <div class="loading">Cargando clientes…</div>
      ) : lista.value.length === 0 ? (
        <div class="panel empty">
          <p class="muted">{hayFiltros ? "Ningún cliente coincide con los filtros." : "Aún no hay clientes. Aparecen solos cuando alguien escribe al WhatsApp, o puedes registrarlos a mano."}</p>
        </div>
      ) : (
        <div class="tbl-wrap">
          <table class="tbl">
            <thead>
              <tr>
                <th>Cliente</th>
                <th>Etapa</th>
                <th>Etiquetas</th>
                <th>Asesora</th>
                <th class="num">Pedidos</th>
                <th class="num">Total comprado</th>
                <th>Última compra</th>
                <th>Último mensaje</th>
              </tr>
            </thead>
            <tbody>
              {lista.value.map((c) => (
                <tr key={c.id} onClick$={() => abrir(c.id)} tabIndex={0} onKeyDown$={(e) => e.key === "Enter" && abrir(c.id)}>
                  <td data-label="Cliente">
                    <span class="who">
                      <span class="avatar sm">{iniciales2(customerName(c))}</span>
                      <span>
                        <strong>{customerName(c)}</strong>
                        <span class="muted small block">
                          {phoneLabel(c.phone)}
                          {c.ciudad ? ` · ${c.ciudad}` : ""}
                        </span>
                      </span>
                      {c.no_leidos > 0 && <span class="badge">{c.no_leidos}</span>}
                    </span>
                  </td>
                  <td data-label="Etapa">
                    <EtapaPill etapa={c.etapa} />
                  </td>
                  <td data-label="Etiquetas">
                    <span class="tags">
                      {c.etiquetas.map((e) => (
                        <span key={e} class="tag">
                          {e}
                        </span>
                      ))}
                    </span>
                  </td>
                  <td data-label="Asesora">{c.asesora || <span class="muted">—</span>}</td>
                  <td data-label="Pedidos" class="num">
                    {c.pedidos}
                  </td>
                  <td data-label="Total" class="num">
                    {c.total_comprado > 0 ? money(c.total_comprado) : <span class="muted">—</span>}
                  </td>
                  <td data-label="Última compra">{c.ultima_compra ? dateLabel(c.ultima_compra) : <span class="muted">—</span>}</td>
                  <td data-label="Último mensaje" class="muted">
                    {c.ultimo_mensaje ? timeAgo(c.ultimo_mensaje) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {nueva.value && (
        <NuevaClienta
          onClose$={() => (nueva.value = false)}
          onCreated$={async (id: number) => {
            nueva.value = false;
            await abrir(id);
          }}
        />
      )}
      {toast.value && <div class={["toast", toast.value.error && "toast-error"]}>{toast.value.text}</div>}
    </div>
  );
});

// ---------------------------------------------------------------------------
// Ficha del cliente

interface FichaData {
  clienta: Clienta;
  pedidos: Order[];
  notas: Nota[];
  tareas: Tarea[];
}

const ICONO: Record<string, string> = {
  pedido: "🧾",
  estado: "🔄",
  etapa: "🎯",
  asignacion: "👤",
  edicion: "✏️",
  nota: "📝",
  tarea: "☑️",
  tarea_hecha: "✅",
};

interface FichaProps {
  id: number;
  miembros: Miembro[];
  etiquetas: string[];
  onBack$: QRL<() => void>;
  onToast$: QRL<(text: string, error?: boolean) => void>;
}

const Ficha = component$<FichaProps>(({ id, miembros, etiquetas, onBack$, onToast$ }) => {
  const d = useSignal<FichaData | null>(null);
  const actividad = useSignal<Evento[]>([]);
  const tab = useSignal<"actividad" | "notas" | "tareas" | "pedidos">("actividad");
  const form = useStore({ name: "", phone: "", email: "", ciudad: "" });
  const nuevaEtiqueta = useSignal("");
  const tareaModal = useSignal<Tarea | "nueva" | null>(null);
  const error = useSignal("");

  const load = $(async () => {
    try {
      const [f, a] = await Promise.all([api<FichaData>(`/api/clientas/${id}`), api<Evento[]>(`/api/clientas/${id}/actividad?limit=150`)]);
      d.value = f;
      actividad.value = a;
      form.name = f.clienta.name;
      form.phone = f.clienta.phone;
      form.email = f.clienta.email;
      form.ciudad = f.clienta.ciudad;
    } catch (e) {
      error.value = (e as Error).message;
    }
  });

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(() => load());
  useOnWindow(
    "kd-change",
    $((e: Event) => {
      const t = (e as CustomEvent<string>).detail;
      if (t === "clientas" || t === "tareas" || t === "orders" || t === "conversations") load();
    }),
  );

  const patch = $(async (body: Record<string, unknown>, ok?: string) => {
    try {
      await api(`/api/clientas/${id}`, { method: "PATCH", json: body });
      if (ok) await onToast$(ok);
      await load();
    } catch (e) {
      await onToast$((e as Error).message, true);
    }
  });

  if (error.value) {
    return (
      <div class="page">
        <button class="btn btn-ghost btn-sm" onClick$={onBack$}>
          ← Clientes
        </button>
        <p class="form-error">{error.value}</p>
      </div>
    );
  }
  if (!d.value) return <div class="loading">Cargando ficha…</div>;
  const c = d.value.clienta;
  const cambiado = form.name !== c.name || form.phone !== c.phone || form.email !== c.email || form.ciudad !== c.ciudad;
  const pendientes = d.value.tareas.filter((t) => !t.hecha).length;
  const etapaSel = !c.etapa_fijada ? "auto" : c.etapa || "sin";

  // Línea de tiempo agrupada por día.
  const dias: { dia: string; items: Evento[] }[] = [];
  for (const ev of actividad.value) {
    const dia = new Date(ev.at).toLocaleDateString("es-PE", { weekday: "long", day: "numeric", month: "long" });
    if (!dias.length || dias[dias.length - 1].dia !== dia) dias.push({ dia, items: [] });
    dias[dias.length - 1].items.push(ev);
  }

  return (
    <div class="page ficha-clienta">
      <button class="btn btn-ghost btn-sm back-link" onClick$={onBack$}>
        ← Clientes
      </button>
      <header class="fc-head">
        <span class="avatar lg">{iniciales2(customerName(c))}</span>
        <div class="grow">
          <h1>{customerName(c)}</h1>
          <p class="muted fc-sub">
            {c.phone && (
              <a href={`https://wa.me/${c.phone}`} target="_blank" rel="noreferrer">
                {phoneLabel(c.phone)}
              </a>
            )}
            {c.email && <span> · {c.email}</span>}
            {c.ciudad && <span> · {c.ciudad}</span>}
          </p>
          <div class="fc-badges">
            <EtapaPill etapa={c.etapa} fijada={c.etapa_fijada} />
            {c.etiquetas.map((e) => (
              <span key={e} class="tag">
                {e}
              </span>
            ))}
          </div>
        </div>
        <div class="head-actions fc-actions">
          {c.conversation_id > 0 && c.ultimo_mensaje && (
            <a class="btn btn-sm" href={u(`/conversaciones?c=${c.conversation_id}`)}>
              💬 Conversación
            </a>
          )}
          <button class="btn btn-sm btn-primary" onClick$={() => (tareaModal.value = "nueva")}>
            + Tarea
          </button>
        </div>
      </header>

      <div class="stats fc-kpis">
        <div class="stat">
          <span>Total comprado</span>
          <strong>{money(c.total_comprado)}</strong>
        </div>
        <div class="stat">
          <span>Pedidos</span>
          <strong>{c.pedidos}</strong>
        </div>
        <div class="stat">
          <span>Última compra</span>
          <strong class="sm">{c.ultima_compra ? dateLabel(c.ultima_compra) : "—"}</strong>
        </div>
        <div class="stat">
          <span>Cliente desde</span>
          <strong class="sm">{dateLabel(c.created_at)}</strong>
        </div>
      </div>

      <div class="fc-grid">
        <aside class="fc-side">
          <section class="panel">
            <h2>Embudo y asignación</h2>
            <label>
              Etapa
              <select
                value={etapaSel}
                onChange$={(_, el) => {
                  const v = el.value;
                  if (v === "auto") patch({ etapa_automatica: true }, "El bot vuelve a mover la etapa");
                  else patch({ etapa: v === "sin" ? "" : v }, "Etapa actualizada");
                }}
              >
                <option value="auto">{`Automática · la mueve el bot (${ETAPAS.find((e) => e.key === c.etapa)?.label ?? "sin etapa"})`}</option>
                {ETAPAS.map((e) => (
                  <option key={e.key} value={e.key}>
                    {`✋ ${e.label}`}
                  </option>
                ))}
                <option value="sin">✋ Sin etapa</option>
              </select>
            </label>
            <label>
              Asesora a cargo
              <select value={String(c.asesora_id)} onChange$={(_, el) => patch({ asesora_id: Number(el.value) }, "Asignación guardada")}>
                <option value="0">Sin asignar</option>
                {miembros
                  .filter((m) => m.active || m.id === c.asesora_id)
                  .map((m) => (
                    <option key={m.id} value={String(m.id)}>
                      {m.name || m.username}
                    </option>
                  ))}
              </select>
            </label>
            <div class="lbl">Etiquetas</div>
            <div class="tags edit">
              {c.etiquetas.map((e) => (
                <span key={e} class="tag">
                  {e}
                  <button
                    class="tag-x"
                    aria-label={`Quitar ${e}`}
                    onClick$={() => patch({ etiquetas: c.etiquetas.filter((x) => x !== e) })}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            <form
              class="row"
              preventdefault:submit
              onSubmit$={async () => {
                const v = nuevaEtiqueta.value.trim();
                if (!v) return;
                nuevaEtiqueta.value = "";
                await patch({ etiquetas: [...c.etiquetas, v] });
              }}
            >
              <input {...({ list: "etiquetas-conocidas" } as Record<string, string>)} bind:value={nuevaEtiqueta} placeholder="vip, novia, mayorista…" maxLength={40} />
              <datalist id="etiquetas-conocidas">
                {etiquetas.map((e) => (
                  <option key={e} value={e} />
                ))}
              </datalist>
              <button class="btn btn-sm" disabled={!nuevaEtiqueta.value.trim()}>
                Añadir
              </button>
            </form>
          </section>

          <section class="panel">
            <h2>Datos</h2>
            <form
              preventdefault:submit
              onSubmit$={() => patch({ name: form.name, phone: form.phone, email: form.email, ciudad: form.ciudad }, "Datos guardados")}
            >
              <label>
                Nombre
                <input value={form.name} onInput$={(_, el) => (form.name = el.value)} maxLength={200} />
              </label>
              <label>
                Teléfono
                <input value={form.phone} onInput$={(_, el) => (form.phone = el.value)} inputMode="tel" />
              </label>
              <label>
                Correo
                <input type="email" value={form.email} onInput$={(_, el) => (form.email = el.value)} />
              </label>
              <label>
                Ciudad
                <input value={form.ciudad} onInput$={(_, el) => (form.ciudad = el.value)} maxLength={120} />
              </label>
              <button class="btn btn-sm btn-primary" disabled={!cambiado}>
                Guardar datos
              </button>
            </form>
          </section>
        </aside>

        <section class="panel fc-main">
          <div class="tabs">
            <button class={["tab", tab.value === "actividad" && "active"]} onClick$={() => (tab.value = "actividad")}>
              Actividad
            </button>
            <button class={["tab", tab.value === "tareas" && "active"]} onClick$={() => (tab.value = "tareas")}>
              Tareas{pendientes > 0 ? ` (${pendientes})` : ""}
            </button>
            <button class={["tab", tab.value === "notas" && "active"]} onClick$={() => (tab.value = "notas")}>
              Notas{d.value.notas.length > 0 ? ` (${d.value.notas.length})` : ""}
            </button>
            <button class={["tab", tab.value === "pedidos" && "active"]} onClick$={() => (tab.value = "pedidos")}>
              Pedidos ({d.value.pedidos.length})
            </button>
          </div>

          {tab.value === "actividad" && (
            <div class="timeline">
              {dias.length === 0 && <p class="muted">Sin actividad todavía.</p>}
              {dias.map((g) => (
                <div key={g.dia} class="tl-day">
                  <h3>{g.dia}</h3>
                  <ul>
                    {g.items.map((ev, i) => (
                      <li key={i} class={["tl-item", `tl-${ev.tipo}`, ev.tipo === "mensaje" && `tl-${ev.detalle}`]}>
                        <span class="tl-icon" aria-hidden="true">
                          {ev.tipo === "mensaje" ? (ev.detalle === "in" ? "💬" : ev.autor === "asesora" ? "🙋‍♀️" : "🤖") : ICONO[ev.tipo] ?? "•"}
                        </span>
                        <div class="grow">
                          <p class="tl-text">
                            {ev.tipo === "pedido" && ev.ref_id ? (
                              <a href={u(`/pedidos?pedido=${ev.ref_id}`)}>
                                {ev.texto}
                                {ev.monto ? ` · ${money(ev.monto)}` : ""}
                              </a>
                            ) : ev.tipo === "mensaje" ? (
                              waText(ev.texto)
                            ) : (
                              ev.texto
                            )}
                          </p>
                          <span class="muted small">
                            {new Date(ev.at).toLocaleTimeString("es-PE", { hour: "2-digit", minute: "2-digit" })}
                            {ev.autor && ev.tipo !== "mensaje" ? ` · ${ev.autor === "bot" ? "Bot" : ev.autor}` : ""}
                            {ev.tipo === "mensaje" ? ` · ${ev.detalle === "in" ? "Cliente" : ev.autor === "asesora" ? "Asesora" : "Bot"}` : ""}
                          </span>
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}

          {tab.value === "tareas" && (
            <div>
              <button class="btn btn-sm" onClick$={() => (tareaModal.value = "nueva")}>
                + Tarea para {c.name || "este cliente"}
              </button>
              {d.value.tareas.length === 0 ? (
                <p class="muted small">Sin tareas. Por ejemplo: «Llamar el viernes para confirmar la talla».</p>
              ) : (
                <ul class="tareas">
                  {d.value.tareas.map((t) => (
                    <TareaFila
                      key={t.id}
                      t={t}
                      onToggle$={async (x) => {
                        await alternarTarea(x);
                        await load();
                      }}
                      onEdit$={(x) => {
                        tareaModal.value = x;
                      }}
                    />
                  ))}
                </ul>
              )}
            </div>
          )}

          {tab.value === "notas" && <NotasPanel clientaId={c.id} />}

          {tab.value === "pedidos" && (
            <div>
              {d.value.pedidos.length === 0 && <p class="muted small">Aún no tiene pedidos.</p>}
              <ul class="mini-list">
                {d.value.pedidos.map((o) => (
                  <li key={o.id}>
                    <a class="mini-row" href={u(`/pedidos?pedido=${o.id}`)}>
                      <span class="order-id">#{o.id}</span>
                      <span class="grow">
                        {o.items.map((it) => `${it.product_code} ${it.product_name}${it.size ? ` (${it.size})` : ""}`).join(", ") || o.notes || "Consulta"}
                        <span class="muted small block">{timeLabel(o.created_at)}</span>
                      </span>
                      <span class="pill" style={{ "--col": STATUS[o.status]?.color }}>
                        {STATUS[o.status]?.label ?? o.status}
                      </span>
                      {o.total > 0 && <b>{money(o.total)}</b>}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      </div>

      {tareaModal.value && (
        <TareaModal
          tarea={tareaModal.value === "nueva" ? null : tareaModal.value}
          clientaId={c.id}
          onClose$={() => (tareaModal.value = null)}
          onSaved$={async () => {
            tareaModal.value = null;
            tab.value = "tareas";
            await load();
          }}
        />
      )}
    </div>
  );
});

// ---------------------------------------------------------------------------

const NuevaClienta = component$<{ onClose$: QRL<() => void>; onCreated$: QRL<(id: number) => void> }>(({ onClose$, onCreated$ }) => {
  const f = useStore({ name: "", phone: "51", email: "", ciudad: "" });
  const error = useSignal("");
  const busy = useSignal(false);
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
            const c = await api<Clienta>("/api/clientas", { method: "POST", json: f });
            await onCreated$(c.id);
          } catch (e) {
            error.value = (e as Error).message;
          } finally {
            busy.value = false;
          }
        }}
      >
        <h2>Nuevo cliente</h2>
        <p class="muted small">Para contactos que llegaron por teléfono, Instagram o a la tienda. Si el número ya existe, se abre su ficha.</p>
        <div class="grid2">
          <label>
            Nombre
            <input value={f.name} onInput$={(_, el) => (f.name = el.value)} required />
          </label>
          <label>
            Celular (con 51)
            <input value={f.phone} onInput$={(_, el) => (f.phone = el.value)} inputMode="tel" required placeholder="51987654321" />
          </label>
          <label>
            Correo
            <input type="email" value={f.email} onInput$={(_, el) => (f.email = el.value)} />
          </label>
          <label>
            Ciudad
            <input value={f.ciudad} onInput$={(_, el) => (f.ciudad = el.value)} />
          </label>
        </div>
        {error.value && <p class="form-error">{error.value}</p>}
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost" onClick$={onClose$}>
            Cancelar
          </button>
          <button class="btn btn-primary" disabled={busy.value}>
            {busy.value ? "Guardando…" : "Registrar"}
          </button>
        </div>
      </form>
    </div>
  );
});

export const head: DocumentHead = { title: titulo("Clientes") };
