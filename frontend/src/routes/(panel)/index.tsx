import { $, component$, useOnWindow, useSignal, useVisibleTask$ } from "@builder.io/qwik";
import type { DocumentHead } from "@builder.io/qwik-city";
import { titulo } from "~/lib/marca";
import { api, miSesion, type Clienta, type Inicio, type Sesion, type Tarea } from "~/lib/api";
import { u } from "~/lib/base";
import { ETAPAS, STATUS, customerName, iniciales2, money, timeAgo } from "~/lib/format";
import { EtapaPill, TareaFila, TareaModal, alternarTarea } from "~/components/crm";

// Panel de inicio: cómo va el mes, el embudo, los pedidos por estado y lo que toca hacer hoy.
export default component$(() => {
  const data = useSignal<{ inicio: Inicio; moneda: string } | null>(null);
  const tareas = useSignal<Tarea[]>([]);
  const recientes = useSignal<Clienta[]>([]);
  const yo = useSignal<Sesion | null>(null);
  const nueva = useSignal(false);
  const error = useSignal("");

  const load = $(async () => {
    try {
      const [d, vencidas, hoy, cs] = await Promise.all([
        api<{ inicio: Inicio; moneda: string }>("/api/inicio"),
        api<Tarea[]>("/api/tareas?vista=vencidas&responsable=yo&limit=8"),
        api<Tarea[]>("/api/tareas?vista=hoy&responsable=yo&limit=8"),
        api<Clienta[]>("/api/clientas?limit=6"),
      ]);
      data.value = d;
      tareas.value = [...vencidas, ...hoy].slice(0, 8);
      recientes.value = cs;
    } catch (e) {
      error.value = (e as Error).message;
    }
  });

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    // Enlaces de antes al tablero (/?pedido=N): el tablero ahora está en /pedidos.
    const pedido = new URLSearchParams(location.search).get("pedido");
    if (pedido) {
      location.replace(u(`/pedidos?pedido=${encodeURIComponent(pedido)}`));
      return;
    }
    miSesion().then((s) => (yo.value = s));
    await load();
  });

  useOnWindow(
    "kd-change",
    $((e: Event) => {
      const t = (e as CustomEvent<string>).detail;
      if (t === "orders" || t === "tareas" || t === "clientas" || t === "conversations") load();
    }),
  );

  const d = data.value?.inicio;
  const mon = data.value?.moneda ?? "S/";
  const mes = new Date().toLocaleDateString("es-PE", { month: "long", year: "numeric" });

  // Embudo: cuántas llegaron al menos a cada etapa (las perdidas cuentan en la base, no avanzan).
  const embudo = ETAPAS.filter((e) => e.key !== "perdida");
  const conEtapa = d ? ETAPAS.reduce((n, e) => n + (d.embudo[e.key] ?? 0), 0) : 0;
  const llegaron = (i: number) => (d ? embudo.slice(i).reduce((n, e) => n + (d.embudo[e.key] ?? 0), 0) : 0);
  const pct = (n: number, base: number) => (base > 0 ? Math.round((n / base) * 100) : 0);
  const variacion = d && d.ventas_mes_anterior > 0 ? Math.round(((d.ventas_mes - d.ventas_mes_anterior) / d.ventas_mes_anterior) * 100) : null;
  const estados = Object.entries(STATUS);
  const maxEstado = d ? Math.max(1, ...estados.map(([k]) => d.por_estado[k] ?? 0)) : 1;

  return (
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Inicio</h1>
          <p class="muted">
            {yo.value ? `Hola, ${yo.value.name || yo.value.username}. ` : ""}Así va {mes}.
          </p>
        </div>
        <div class="head-actions">
          <button class="btn" onClick$={() => (nueva.value = true)}>
            + Tarea
          </button>
          <a class="btn btn-primary" href={u("/pedidos")}>
            Ver pedidos
          </a>
        </div>
      </div>

      {error.value && <p class="form-error">{error.value}</p>}
      {!d ? (
        <div class="loading">Cargando…</div>
      ) : (
        <>
          <div class="stats kpis">
            <div class="stat">
              <span>Ventas del mes</span>
              <strong>{money(d.ventas_mes, mon)}</strong>
              {variacion != null && (
                <small class={variacion >= 0 ? "ok-text" : "warn"}>
                  {variacion >= 0 ? "▲" : "▼"} {Math.abs(variacion)}% vs. mes anterior
                </small>
              )}
            </div>
            <div class="stat">
              <span>Pedidos vendidos</span>
              <strong>{d.pedidos_vendidos}</strong>
              <small class="muted">{d.pedidos_mes} pedidos en el mes</small>
            </div>
            <div class="stat">
              <span>Ticket medio</span>
              <strong>{money(d.pedidos_vendidos > 0 ? d.ventas_mes / d.pedidos_vendidos : 0, mon)}</strong>
            </div>
            <div class="stat">
              <span>Clientas nuevas</span>
              <strong>{d.clientas_nuevas}</strong>
              <small class="muted">de {d.clientas_total} en total</small>
            </div>
            <a class="stat stat-link" href={u("/tareas?vista=vencidas")}>
              <span>Tareas vencidas</span>
              <strong class={d.tareas_vencidas > 0 ? "danger-text" : ""}>{d.tareas_vencidas}</strong>
              <small class="muted">{d.tareas_hoy} vencen hoy</small>
            </a>
          </div>

          <div class="dash-grid">
            <section class="panel">
              <div class="panel-head">
                <h2>Embudo de ventas</h2>
                <a class="small" href={u("/clientas")}>
                  Ver clientas →
                </a>
              </div>
              {conEtapa === 0 ? (
                <p class="muted small">Todavía no hay clientas con etapa. El bot las pone al conversar, o se fijan a mano en la ficha.</p>
              ) : (
                <ul class="funnel">
                  {embudo.map((e, i) => {
                    const n = llegaron(i);
                    return (
                      <li key={e.key}>
                        <a href={u(`/clientas?etapa=${e.key}`)} class="funnel-row">
                          <span class="funnel-label">{e.label}</span>
                          <span class="funnel-bar">
                            <span style={{ width: `${Math.max(pct(n, conEtapa), 2)}%`, background: e.color }} />
                          </span>
                          <span class="funnel-num">
                            <b>{d.embudo[e.key] ?? 0}</b>
                            <span class="muted small"> · {pct(n, conEtapa)}% llegó</span>
                          </span>
                        </a>
                      </li>
                    );
                  })}
                </ul>
              )}
              <p class="muted small funnel-foot">
                Conversión a venta: <b>{pct(d.embudo.venta_confirmada ?? 0, conEtapa)}%</b> · Perdidas: {d.embudo.perdida ?? 0} · Sin etapa:{" "}
                {d.embudo[""] ?? 0}
              </p>
            </section>

            <section class="panel">
              <div class="panel-head">
                <h2>Pedidos por estado</h2>
                <a class="small" href={u("/pedidos")}>
                  Tablero →
                </a>
              </div>
              <ul class="bars">
                {estados.map(([k, v]) => (
                  <li key={k}>
                    <span class="bars-label">{v.label}</span>
                    <span class="bars-bar">
                      <span style={{ width: `${((d.por_estado[k] ?? 0) / maxEstado) * 100}%`, background: v.color }} />
                    </span>
                    <b class="bars-num">{d.por_estado[k] ?? 0}</b>
                  </li>
                ))}
              </ul>
              <p class="muted small">Abiertos, más los entregados y cancelados de este mes.</p>
            </section>

            <section class="panel">
              <div class="panel-head">
                <h2>Mis tareas para hoy</h2>
                <a class="small" href={u("/tareas")}>
                  Todas →
                </a>
              </div>
              {d.mis_vencidas > 0 && (
                <p class="aviso aviso-danger small">
                  Tienes {d.mis_vencidas} tarea{d.mis_vencidas === 1 ? "" : "s"} vencida{d.mis_vencidas === 1 ? "" : "s"}.
                </p>
              )}
              {tareas.value.length === 0 ? (
                <p class="muted small">Nada pendiente para hoy. 🎉</p>
              ) : (
                <ul class="tareas">
                  {tareas.value.map((t) => (
                    <TareaFila
                      key={t.id}
                      t={t}
                      conClienta
                      onToggle$={async (x) => {
                        await alternarTarea(x);
                        await load();
                      }}
                    />
                  ))}
                </ul>
              )}
            </section>

            <section class="panel">
              <div class="panel-head">
                <h2>Clientas recientes</h2>
                <a class="small" href={u("/conversaciones")}>
                  {d.sin_leer > 0 ? `${d.sin_leer} chats sin leer →` : "Conversaciones →"}
                </a>
              </div>
              <ul class="mini-list">
                {recientes.value.map((c) => (
                  <li key={c.id}>
                    <a href={u(`/clientas?c=${c.id}`)} class="mini-row">
                      <span class="avatar sm">{iniciales2(customerName(c))}</span>
                      <span class="grow">
                        <strong>{customerName(c)}</strong>
                        <span class="muted small"> · {c.ultimo_mensaje ? timeAgo(c.ultimo_mensaje) : timeAgo(c.created_at)}</span>
                      </span>
                      <EtapaPill etapa={c.etapa} />
                    </a>
                  </li>
                ))}
                {recientes.value.length === 0 && <li class="muted small">Aún no hay clientas.</li>}
              </ul>
              {d.stock_bajo > 0 && (
                <p class="muted small">
                  <a href={u("/productos")}>{d.stock_bajo} tallas con stock ≤ 1</a>
                </p>
              )}
            </section>
          </div>
        </>
      )}

      {nueva.value && (
        <TareaModal
          onClose$={() => (nueva.value = false)}
          onSaved$={async () => {
            nueva.value = false;
            await load();
          }}
        />
      )}
    </div>
  );
});

export const head: DocumentHead = { title: titulo("Inicio") };
