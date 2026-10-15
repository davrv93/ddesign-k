import { $, component$, useOnWindow, useSignal, useVisibleTask$ } from "@builder.io/qwik";
import type { DocumentHead } from "@builder.io/qwik-city";
import { titulo } from "~/lib/marca";
import { cambiarEstadoFuente, leerFuente, listarFuentes, miSesion, resumenPromos, type Fuente, type PromoResumen, type TipoFuente } from "~/lib/api";
import { timeAgo } from "~/lib/format";
import { ESTADOS, TIPOS, estadoVisible, sinMarkdown, vigenciaTexto, type EstadoVisible } from "~/lib/fuentes";
import { CifrasPromoFila, ConocimientoModal, EstadoPill, FuenteEditor, SubirModal, TipoBadge } from "~/components/fuentes";
import { PromoAsistente } from "~/components/promo-asistente";

// Fuentes: todo el material con el que atiende el robot (promos, preguntas frecuentes, guion, políticas y material).
// El editor va por ?f=<id> (o ?f=nueva&tipo=faq) y el asistente de promos por ?asistente=promo: el sitio es estático.
const PESTANAS = [
  { key: "todas", label: "Todas", tipos: null },
  { key: "promo", label: "Promos", tipos: ["promo"] },
  { key: "faq", label: "Preguntas frecuentes", tipos: ["faq"] },
  { key: "guion", label: "Guion y políticas", tipos: ["guion", "politica"] },
  { key: "material", label: "Material", tipos: ["material"] },
  { key: "propuestas", label: "Propuestas", tipos: null },
] as const;

type Abierta = { id: number; tipo: TipoFuente } | null;

export default component$(() => {
  const pestana = useSignal<string>("todas");
  const fuentes = useSignal<Fuente[]>([]);
  const promos = useSignal<PromoResumen[]>([]);
  const cargando = useSignal(true);
  const error = useSignal("");
  const q = useSignal("");
  const estado = useSignal("");
  const admin = useSignal(false);
  const abierta = useSignal<Abierta>(null);
  const asistente = useSignal<{ fuente: Fuente | null } | null>(null);
  const subir = useSignal(false);
  const sabe = useSignal(false);
  const toast = useSignal<{ text: string; error?: boolean } | null>(null);
  const espera = useSignal(0);

  const notify = $((text: string, err = false) => {
    toast.value = { text, error: err };
    setTimeout(() => (toast.value = null), 3500);
  });

  const load = $(async () => {
    try {
      fuentes.value = await listarFuentes({ q: q.value.trim() });
      error.value = "";
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      cargando.value = false;
    }
    resumenPromos()
      .then((r) => (promos.value = r))
      .catch(() => (promos.value = []));
  });

  /** Deja la URL al día: pestaña + lo que esté abierto (para volver con el botón atrás o compartir el enlace). */
  const url = $((extra = "") => {
    const p = new URLSearchParams();
    if (pestana.value !== "todas") p.set("tab", pestana.value);
    const s = [p.toString(), extra].filter(Boolean).join("&");
    history.replaceState(null, "", s ? `?${s}` : location.pathname);
  });

  const abrir = $(async (id: number, tipo: TipoFuente = "material") => {
    sabe.value = false;
    abierta.value = { id, tipo };
    await url(id > 0 ? `f=${id}` : `f=nueva&tipo=${tipo}`);
  });

  const abrirAsistente = $(async (f: Fuente | null) => {
    abierta.value = null;
    asistente.value = { fuente: f };
    await url(f ? `asistente=promo&f=${f.id}` : "asistente=promo");
  });

  const cerrar = $(async () => {
    abierta.value = null;
    asistente.value = null;
    await url();
  });

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    miSesion()
      .then((s) => (admin.value = s.admin))
      .catch(() => {});
    const p = new URLSearchParams(location.search);
    const tab = p.get("tab");
    if (tab && PESTANAS.some((x) => x.key === tab)) pestana.value = tab;
    await load();
    const f = p.get("f");
    const tipo = (p.get("tipo") ?? "") as TipoFuente;
    if (p.get("asistente") === "promo" || (f === "nueva" && tipo === "promo")) {
      let fu: Fuente | null = null;
      if (f && Number(f) > 0) fu = await leerFuente(Number(f)).catch(() => null);
      await abrirAsistente(fu);
    } else if (f === "nueva") {
      await abrir(0, TIPOS[tipo] ? tipo : "material");
    } else if (Number(f) > 0) {
      await abrir(Number(f));
    }
  });

  useOnWindow(
    "kd-change",
    $((e: Event) => {
      if ((e as CustomEvent<string>).detail === "fuentes") load();
    }),
  );

  const elegir = $(async (k: string) => {
    pestana.value = k;
    await url();
  });

  const buscar = $((v: string) => {
    q.value = v;
    clearTimeout(espera.value);
    espera.value = window.setTimeout(() => load(), 300);
  });

  const propuestas = fuentes.value.filter((f) => f.estado === "propuesta");
  const tab = PESTANAS.find((p) => p.key === pestana.value) ?? PESTANAS[0];
  const lista = fuentes.value.filter((f) => {
    if (tab.key === "propuestas") return f.estado === "propuesta";
    if (tab.tipos && !(tab.tipos as readonly string[]).includes(f.tipo)) return false;
    const ev = estadoVisible(f);
    if (estado.value) return ev === estado.value || (estado.value === "activo" && ev === "programada");
    return f.estado !== "propuesta" && f.estado !== "descartada";
  });
  const promosVisibles = promos.value.filter((p) => p.estado === "activo" || p.estado === "borrador");

  return (
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Fuentes</h1>
          <p class="muted">Lo que el robot sabe y puede decir: promos, preguntas frecuentes, guion, políticas y material de la tienda.</p>
        </div>
        <div class="head-actions">
          <button class="btn" onClick$={() => (sabe.value = true)}>
            🤖 Lo que sabe el robot
          </button>
          {admin.value && (
            <>
              <button class="btn" onClick$={() => (subir.value = true)}>
                ⬆ Subir archivo
              </button>
              <button
                class="btn"
                onClick$={() => {
                  const t = tab.tipos?.[0];
                  return t === "promo" ? abrirAsistente(null) : abrir(0, (t as TipoFuente) ?? "faq");
                }}
              >
                + Nueva
              </button>
              <button class="btn btn-primary" onClick$={() => abrirAsistente(null)}>
                🏷 Diseñar promo
              </button>
            </>
          )}
        </div>
      </div>

      <div class="tabs">
        {PESTANAS.map((p) => (
          <button key={p.key} class={["tab", pestana.value === p.key && "active"]} onClick$={() => elegir(p.key)}>
            {p.label}
            {p.key === "propuestas" && propuestas.length > 0 && <span class="badge fu-badge">{propuestas.length}</span>}
          </button>
        ))}
      </div>

      {tab.key !== "propuestas" && (
        <div class="filters">
          <input
            class="search grow"
            type="search"
            placeholder="Buscar en títulos y contenido…"
            value={q.value}
            onInput$={(_, el) => buscar(el.value)}
            aria-label="Buscar"
          />
          <select class="auto" value={estado.value} aria-label="Estado" onChange$={(_, el) => (estado.value = el.value)}>
            <option value="">Todos los estados</option>
            {(Object.keys(ESTADOS) as EstadoVisible[])
              .filter((k) => k !== "propuesta")
              .map((k) => (
                <option key={k} value={k}>
                  {ESTADOS[k].label}
                </option>
              ))}
          </select>
        </div>
      )}

      {tab.key === "promo" && promosVisibles.length > 0 && (
        <div class="fu-promos">
          {promosVisibles.map((p) => (
            <button key={p.id} class="fu-promo" onClick$={() => abrir(p.id)}>
              <div class="fu-promo-top">
                <strong>{p.titulo}</strong>
                <EstadoPill f={p} />
              </div>
              <div class="muted small">
                {vigenciaTexto(p.vigente_desde, p.vigente_hasta)}
                {p.vigente && p.dias_restantes != null && ` · quedan ${p.dias_restantes} ${p.dias_restantes === 1 ? "día" : "días"}`}
                {p.embudo_id > 0 && " · embudo propio"}
              </div>
              <CifrasPromoFila c={p.cifras} />
              {(p.avisos ?? []).length > 0 && (
                <div class="fu-avisos">
                  {p.avisos!.map((a) => (
                    <span key={a} class="chip warn">
                      ⚠ {a}
                    </span>
                  ))}
                </div>
              )}
            </button>
          ))}
        </div>
      )}

      {error.value && <p class="form-error">{error.value}</p>}

      {cargando.value ? (
        <div class="loading">Cargando…</div>
      ) : tab.key === "propuestas" ? (
        <section>
          <p class="muted small">
            Preguntas que el robot no supo contestar bien y que se repitieron. Revísalas: ninguna entra al robot sin que alguien la apruebe.
          </p>
          {lista.length === 0 && (
            <div class="panel empty">
              <p class="muted">No hay propuestas pendientes. 🎉</p>
            </div>
          )}
          <div class="fu-lista">
            {lista.map((f) => (
              <article key={f.id} class="fu-card fu-propuesta">
                <div class="fu-card-top">
                  <TipoBadge tipo={f.tipo} />
                  {f.datos?.ocurrencias ? (
                    <span class="chip">
                      {f.datos.ocurrencias} {f.datos.ocurrencias === 1 ? "vez" : "veces"}
                    </span>
                  ) : null}
                  <span class="muted small push-right">{timeAgo(f.creado_en)}</span>
                </div>
                <h3>{String(f.datos?.pregunta || f.titulo)}</h3>
                {(f.datos?.variantes ?? []).length > 0 && (
                  <div class="tags">
                    {(f.datos.variantes as string[]).slice(0, 5).map((v) => (
                      <span key={v} class="tag">
                        «{v}»
                      </span>
                    ))}
                  </div>
                )}
                {f.datos?.respuesta && (
                  <p class="fu-sugerida">
                    <span class="muted small">Respuesta sugerida</span>
                    {String(f.datos.respuesta)}
                  </p>
                )}
                {admin.value && (
                  <div class="row wrap fu-card-acc">
                    <button
                      class="btn btn-sm"
                      onClick$={async () => {
                        try {
                          await cambiarEstadoFuente(f.id, "descartada");
                          notify("Propuesta descartada: no se volverá a proponer");
                          await load();
                        } catch (e) {
                          notify((e as Error).message, true);
                        }
                      }}
                    >
                      Descartar
                    </button>
                    <button class="btn btn-sm btn-primary" onClick$={() => abrir(f.id)}>
                      Editar y aprobar
                    </button>
                  </div>
                )}
              </article>
            ))}
          </div>
        </section>
      ) : lista.length === 0 ? (
        <div class="panel empty">
          <p class="muted">
            {fuentes.value.length === 0
              ? "Todavía no hay fuentes. Empieza por una pregunta frecuente o diseña tu primera promo."
              : "Nada coincide con el filtro."}
          </p>
          {admin.value && fuentes.value.length === 0 && (
            <div class="row wrap fu-vacio-acc">
              <button class="btn" onClick$={() => abrir(0, "faq")}>
                + Pregunta frecuente
              </button>
              <button class="btn btn-primary" onClick$={() => abrirAsistente(null)}>
                🏷 Diseñar promo
              </button>
            </div>
          )}
        </div>
      ) : (
        <div class="fu-lista">
          {lista.map((f) => (
            <button key={f.id} class="fu-card" onClick$={() => abrir(f.id)}>
              <div class="fu-card-top">
                <TipoBadge tipo={f.tipo} />
                <EstadoPill f={f} />
              </div>
              <h3>{f.titulo || "Sin título"}</h3>
              <p class="muted small fu-recorte">{f.tipo === "faq" && f.datos?.pregunta ? String(f.datos.respuesta ?? "") : sinMarkdown(f.resumen ?? f.contenido ?? "")}</p>
              {(f.etiquetas ?? []).length > 0 && (
                <div class="tags">
                  {f.etiquetas!.map((t) => (
                    <span key={t} class="tag">
                      {t}
                    </span>
                  ))}
                </div>
              )}
              <div class="fu-card-meta small muted">
                <span>📅 {vigenciaTexto(f.vigente_desde, f.vigente_hasta)}</span>
                <span>v{f.version}</span>
                <span>
                  {f.actualizado_por || "—"} · {timeAgo(f.actualizado_en)}
                </span>
              </div>
            </button>
          ))}
        </div>
      )}

      {abierta.value && (
        <FuenteEditor
          key={`${abierta.value.id}-${abierta.value.tipo}`}
          id={abierta.value.id}
          tipo={abierta.value.tipo}
          admin={admin.value}
          onClose$={cerrar}
          onCambio$={async () => {
            await load();
          }}
          onAsistente$={(f) => abrirAsistente(f)}
          onToast$={notify}
        />
      )}

      {asistente.value && (
        <PromoAsistente
          fuente={asistente.value.fuente}
          onClose$={cerrar}
          onToast$={notify}
          onListo$={async (id) => {
            asistente.value = null;
            await load();
            pestana.value = "promo";
            await abrir(id);
          }}
        />
      )}

      {subir.value && (
        <SubirModal
          onClose$={() => (subir.value = false)}
          onCreada$={async (f) => {
            subir.value = false;
            notify("Archivo leído: revisa el texto y actívalo cuando esté listo");
            await load();
            await abrir(f.id, f.tipo);
          }}
        />
      )}

      {sabe.value && <ConocimientoModal onClose$={() => (sabe.value = false)} onAbrir$={(id) => abrir(id)} />}

      {toast.value && <div class={["toast", toast.value.error && "toast-error"]}>{toast.value.text}</div>}
    </div>
  );
});

export const head: DocumentHead = { title: titulo("Fuentes") };
