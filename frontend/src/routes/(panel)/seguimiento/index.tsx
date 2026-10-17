import { $, component$, useOnWindow, useSignal, useVisibleTask$ } from "@builder.io/qwik";
import type { DocumentHead } from "@builder.io/qwik-city";
import { titulo } from "~/lib/marca";
import { u } from "~/lib/base";
import { activarSecuencia, listarSecuencias, miSesion, tableroSecuencia, type FilaTablero, type Secuencia } from "~/lib/api";
import { Confirmar } from "~/components/confirmar";
import { SecuenciaEditor, alcanceTexto, resumenPasos } from "~/components/seguimiento";
import { ResumenContenidoTabla } from "~/components/contenido";

// Seguimiento: secuencias de mensajes para las clientas que vieron un modelo y dejaron de responder
// (docs/SEGUIMIENTO_CONTRATO.md §4), y qué modelos tienen contenido para ellas (§3). Todo el equipo mira; solo admin edita.
// El editor va por ?s=<id> (o ?s=nueva) y la pestaña por ?tab=contenido: el sitio es estático.
type Pestana = "secuencias" | "tablero" | "contenido";
type Abierta = { secuencia: Secuencia | null } | null;

export default component$(() => {
  const pestana = useSignal<Pestana>("secuencias");
  const lista = useSignal<Secuencia[]>([]);
  const cargando = useSignal(true);
  const error = useSignal("");
  const admin = useSignal(false);
  const abierta = useSignal<Abierta>(null);
  const activando = useSignal<Secuencia | null>(null);
  const toast = useSignal<{ text: string; error?: boolean } | null>(null);

  const notify = $((text: string, err = false) => {
    toast.value = { text, error: err };
    setTimeout(() => (toast.value = null), 3500);
  });

  const load = $(async () => {
    try {
      lista.value = await listarSecuencias();
      error.value = "";
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      cargando.value = false;
    }
  });

  const url = $(() => {
    const p = new URLSearchParams();
    if (pestana.value !== "secuencias") p.set("tab", pestana.value);
    if (abierta.value) p.set("s", abierta.value.secuencia ? String(abierta.value.secuencia.id) : "nueva");
    const s = p.toString();
    history.replaceState(null, "", s ? `?${s}` : location.pathname);
  });

  const abrir = $(async (s: Secuencia | null) => {
    abierta.value = { secuencia: s };
    await url();
  });

  const cerrar = $(async () => {
    abierta.value = null;
    await url();
  });

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    miSesion()
      .then((s) => (admin.value = s.admin))
      .catch(() => {});
    const p = new URLSearchParams(location.search);
    if (p.get("tab") === "contenido" || p.get("tab") === "tablero") pestana.value = p.get("tab") as Pestana;
    await load();
    const sid = p.get("s");
    if (sid === "nueva") await abrir(null);
    else if (Number(sid) > 0) {
      const s = lista.value.find((x) => x.id === Number(sid));
      if (s) await abrir(s);
    }
  });

  useOnWindow(
    "kd-change",
    $((e: Event) => {
      if ((e as CustomEvent<string>).detail === "seguimiento") load();
    }),
  );

  const cambiarActiva = $(async (s: Secuencia, activa: boolean) => {
    try {
      await activarSecuencia(s.id, activa);
      await load();
      await notify(activa ? `«${s.nombre}» activada` : `«${s.nombre}» apagada`);
    } catch (e) {
      await notify((e as Error).message, true);
      await load();
    }
  });

  const activas = lista.value.filter((s) => s.activa).length;

  return (
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Seguimiento</h1>
          <p class="muted">Mensajes que el robot manda, paso a paso, a la clienta que vio un modelo y dejó de responder.</p>
        </div>
        <div class="head-actions">
          {admin.value && pestana.value === "secuencias" && (
            <button class="btn btn-primary" onClick$={() => abrir(null)}>
              + Secuencia
            </button>
          )}
        </div>
      </div>

      <div class="tabs" role="tablist">
        <button
          role="tab"
          aria-selected={pestana.value === "secuencias"}
          class={["tab", pestana.value === "secuencias" && "active"]}
          onClick$={async () => {
            pestana.value = "secuencias";
            await url();
          }}
        >
          Secuencias
          {activas > 0 && <span class="badge fu-badge">{activas}</span>}
        </button>
        <button
          role="tab"
          aria-selected={pestana.value === "tablero"}
          class={["tab", pestana.value === "tablero" && "active"]}
          onClick$={async () => {
            pestana.value = "tablero";
            await url();
          }}
        >
          Tablero
        </button>
        <button
          role="tab"
          aria-selected={pestana.value === "contenido"}
          class={["tab", pestana.value === "contenido" && "active"]}
          onClick$={async () => {
            pestana.value = "contenido";
            await url();
          }}
        >
          Contenido por modelo
        </button>
      </div>

      {pestana.value === "tablero" ? (
        <TableroSeguimiento secuencias={lista.value} />
      ) : pestana.value === "contenido" ? (
        <ResumenContenidoTabla />
      ) : (
        <>
          <div class="ficha-box box-warn seg-aviso" role="note">
            <strong>⚠ Activar una secuencia escribe a clientas reales.</strong>
            <ul>
              <li>Nunca manda mensajes masivos: solo a conversaciones que se quedaron calladas después de ver un modelo.</li>
              <li>
                Se detiene sola si la clienta responde, si una asesora le escribe, si el bot está en pausa o si ya confirmó su pedido.
              </li>
              <li>
                Solo sale en el <a href={u("/ajustes")}>horario de seguimiento</a> y respeta el tope diario de cada{" "}
                <a href={u("/whatsapp")}>número de WhatsApp</a>.
              </li>
            </ul>
          </div>

          {error.value && <p class="form-error">{error.value}</p>}

          {cargando.value ? (
            <div class="loading">Cargando…</div>
          ) : lista.value.length === 0 ? (
            <div class="panel empty">
              <p class="muted">Todavía no hay secuencias.</p>
              {admin.value && (
                <button class="btn btn-primary" onClick$={() => abrir(null)}>
                  Crear la primera
                </button>
              )}
            </div>
          ) : (
            <div class="seg-lista">
              {lista.value.map((s) => {
                const c = s.cifras;
                return (
                  <article key={s.id} class={["panel seg-card", s.activa && "activa"]}>
                    <div class="seg-card-top">
                      <div class="grow">
                        <h2>
                          <button class="link-plain" onClick$={() => abrir(s)}>
                            {s.nombre || "Sin nombre"}
                          </button>
                        </h2>
                        <p class="muted small">
                          {alcanceTexto(s)} · {(s.pasos ?? []).length} {(s.pasos ?? []).length === 1 ? "paso" : "pasos"}
                        </p>
                      </div>
                      <label class="seg-switch">
                        <span class={["small", s.activa ? "ok-text" : "muted"]}>{s.activa ? "Activa" : "Apagada"}</span>
                        <input
                          type="checkbox"
                          class="switch"
                          aria-label={`${s.activa ? "Apagar" : "Activar"} ${s.nombre}`}
                          checked={s.activa}
                          disabled={!admin.value}
                          onChange$={(_, el) => {
                            if (el.checked) {
                              el.checked = false;
                              activando.value = s;
                            } else cambiarActiva(s, false);
                          }}
                        />
                      </label>
                    </div>
                    <p class="seg-resumen">{resumenPasos(s.pasos) || "Sin pasos"}</p>
                    {c && (
                      <div class="fu-cifras">
                        <span>
                          <b>{c.en_curso}</b> en curso
                        </span>
                        <span>
                          <b>{c.enviados_hoy}</b> enviados hoy
                        </span>
                        <span>
                          <b>{c.respondieron}</b> respondieron
                        </span>
                        <span>
                          <b>{c.terminados}</b> terminados
                        </span>
                      </div>
                    )}
                    <div class="row wrap seg-card-acc">
                      <button class="btn btn-sm" onClick$={() => abrir(s)}>
                        {admin.value ? "Editar y vista previa" : "Ver y vista previa"}
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </>
      )}

      {abierta.value && (
        <SecuenciaEditor
          key={abierta.value.secuencia?.id ?? "nueva"}
          secuencia={abierta.value.secuencia}
          admin={admin.value}
          onClose$={cerrar}
          onSaved$={async (s) => {
            await load();
            if (s) {
              await notify("Secuencia guardada");
              await abrir(lista.value.find((x) => x.id === s.id) ?? s);
            } else {
              await notify("Secuencia borrada");
              await cerrar();
            }
          }}
        />
      )}

      {activando.value && (
        <Confirmar
          titulo={`¿Activar «${activando.value.nombre}»?`}
          texto={`Desde ahora el robot escribirá a clientas reales que vieron un modelo y dejaron de responder: ${resumenPasos(activando.value.pasos)}. No envía mensajes masivos y se detiene si la clienta responde.`}
          confirmar="Activar"
          onCancelar$={() => (activando.value = null)}
          onConfirmar$={async () => {
            const s = activando.value!;
            await activarSecuencia(s.id, true);
            activando.value = null;
            await load();
            await notify(`«${s.nombre}» activada`);
          }}
        />
      )}

      {toast.value && (
        <div class={["toast", toast.value.error && "toast-error"]} role="status">
          {toast.value.text}
        </div>
      )}
    </div>
  );
});

export const head: DocumentHead = { title: titulo("Seguimiento") };

const TIPO_PASO_TXT: Record<string, string> = {
  emoji: "Emoji o sticker",
  texto: "Mensaje",
  beneficio: "Beneficios",
  testimonio: "Testimonio",
  tip: "Tip",
  oferta: "Oferta",
};

const cuando = (iso: string | null) => {
  if (!iso) return "";
  const d = new Date(iso);
  const hoy = new Date();
  const hora = d.toLocaleTimeString("es-PE", { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === hoy.toDateString() ? `hoy ${hora}` : `${d.toLocaleDateString("es-PE", { day: "numeric", month: "short" })} ${hora}`;
};

/** Tablero de una secuencia: una columna por paso (como las etapas SEGUI-1, SEGUI-2… de Kommo), más «Respondió»,
 *  «Terminó sin respuesta» y «Detenido». Cada tarjeta abre la conversación. */
export const TableroSeguimiento = component$<{ secuencias: Secuencia[] }>(({ secuencias }) => {
  const elegida = useSignal<number>(0);
  const filas = useSignal<FilaTablero[]>([]);
  const cargando = useSignal(false);
  const error = useSignal("");

  const cargar = $(async () => {
    const id = elegida.value || secuencias.find((s) => s.activa)?.id || secuencias[0]?.id || 0;
    elegida.value = id;
    if (!id) return;
    cargando.value = true;
    try {
      filas.value = await tableroSecuencia(id);
      error.value = "";
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      cargando.value = false;
    }
  });

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(({ track }) => {
    track(() => secuencias.length);
    cargar();
  });

  useOnWindow(
    "kd-change",
    $((e: Event) => {
      if ((e as CustomEvent<string>).detail === "seguimiento") cargar();
    }),
  );

  const sec = secuencias.find((s) => s.id === elegida.value);
  const pasos = sec?.pasos ?? [];
  const columnas = [
    ...pasos.map((p, i) => ({ clave: `paso-${i + 1}`, titulo: `${i + 1}. ${TIPO_PASO_TXT[p.tipo] ?? p.tipo}`, nota: "le toca" })),
    { clave: "respondio", titulo: "Respondió", nota: "atender" },
    { clave: "terminado", titulo: "Terminó sin respuesta", nota: "" },
    { clave: "detenido", titulo: "Detenido", nota: "una persona tomó la conversación" },
  ];

  if (secuencias.length === 0) return <p class="muted pad">Todavía no hay secuencias.</p>;

  return (
    <div class="seg-tablero-wrap">
      <div class="row seg-tablero-barra">
        <label>
          Secuencia{" "}
          <select
            value={String(elegida.value)}
            onChange$={async (_, el) => {
              elegida.value = Number(el.value);
              await cargar();
            }}
          >
            {secuencias.map((s) => (
              <option key={s.id} value={String(s.id)}>
                {s.nombre + (s.activa ? "" : " (apagada)")}
              </option>
            ))}
          </select>
        </label>
        <span class="muted small">Últimos 14 días</span>
      </div>
      {error.value && <p class="form-error">{error.value}</p>}
      {cargando.value && filas.value.length === 0 ? (
        <div class="loading">Cargando…</div>
      ) : (
        <div class="seg-tablero" role="list">
          {columnas.map((c) => {
            const items = filas.value.filter((f) => f.columna === c.clave);
            return (
              <section key={c.clave} class={["seg-col", c.clave === "respondio" && "seg-col-ok"]} role="listitem" aria-label={c.titulo}>
                <header>
                  <strong>{c.titulo}</strong>
                  <span class="badge">{items.length}</span>
                </header>
                {items.length === 0 && <p class="muted small">—</p>}
                {items.map((f) => (
                  <a key={f.conversation_id} class="seg-card" href={u(`/conversaciones/?c=${f.conversation_id}`)}>
                    <strong>{f.nombre || f.telefono || "Clienta"}</strong>
                    {f.producto_nombre && <span class="small">{f.producto_nombre}</span>}
                    {c.clave.startsWith("paso-") && f.proximo_en && <span class="muted small">sale {cuando(f.proximo_en)}</span>}
                    {!c.clave.startsWith("paso-") && <span class="muted small">{cuando(f.actualizado_en)}</span>}
                  </a>
                ))}
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
});

