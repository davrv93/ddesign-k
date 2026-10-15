// Piezas de la pantalla Fuentes: editor de una fuente, versiones, subir archivo y «Lo que sabe el robot».
import { $, component$, useSignal, useStore, useVisibleTask$, type QRL } from "@builder.io/qwik";
import {
  borrarFuente,
  cambiarEstadoFuente,
  crearFuente,
  guardarFuente,
  leerConocimiento,
  leerFuente,
  subirFuente,
  versionesFuente,
  type Conocimiento,
  type Fuente,
  type FuenteEditable,
  type FuenteVersion,
  type TipoFuente,
} from "~/lib/api";
import { money, timeAgo, timeLabel } from "~/lib/format";
import { ESTADOS, MECANICAS, PUBLICOS, TIPOS, alcanceTexto, estadoVisible, mdAHtml, mecanicaTexto, vigenciaTexto } from "~/lib/fuentes";
import { Confirmar } from "./confirmar";

export const TipoBadge = component$<{ tipo: TipoFuente }>(({ tipo }) => {
  const t = TIPOS[tipo] ?? { label: tipo, icon: "•", color: "#888" };
  return (
    <span class="pill fu-tipo" style={{ "--col": t.color }}>
      {t.icon} {t.label}
    </span>
  );
});

export const EstadoPill = component$<{ f: Pick<Fuente, "estado" | "programada" | "vigente"> }>(({ f }) => {
  const e = ESTADOS[estadoVisible(f)] ?? { label: f.estado, color: "#888" };
  return (
    <span class="pill" style={{ "--col": e.color }}>
      {e.label}
    </span>
  );
});

const lineas = (s: string) =>
  s
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean);
const etiquetasDe = (s: string) =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

// ---------------------------------------------------------------------------
// Editor de una fuente (cajón lateral). `id` 0 = nueva.

interface EditorProps {
  id: number;
  tipo: TipoFuente;
  admin: boolean;
  onClose$: QRL<() => void>;
  onCambio$: QRL<(id: number) => void>;
  onAsistente$: QRL<(f: Fuente) => void>;
  onToast$: QRL<(text: string, error?: boolean) => void>;
}

export const FuenteEditor = component$<EditorProps>(({ id, tipo, admin, onClose$, onCambio$, onAsistente$, onToast$ }) => {
  const fuente = useSignal<Fuente | null>(null);
  const f = useStore({
    tipo: tipo as TipoFuente,
    titulo: "",
    contenido: "",
    vigente_desde: "",
    vigente_hasta: "",
    etiquetas: "",
    pregunta: "",
    variantes: "",
    respuesta: "",
  });
  const cargando = useSignal(id > 0);
  const vista = useSignal<"escribir" | "previa">("escribir");
  const busy = useSignal(false);
  const error = useSignal("");
  const borrando = useSignal(false);
  const versiones = useSignal<FuenteVersion[] | null>(null);
  const verVersion = useSignal<FuenteVersion | null>(null);
  const cambiado = useSignal(false);

  const llenar = $((x: Fuente) => {
    fuente.value = x;
    f.tipo = x.tipo;
    f.titulo = x.titulo;
    f.contenido = x.contenido ?? "";
    f.vigente_desde = x.vigente_desde ?? "";
    f.vigente_hasta = x.vigente_hasta ?? "";
    f.etiquetas = (x.etiquetas ?? []).join(", ");
    f.pregunta = String(x.datos?.pregunta ?? "");
    f.variantes = ((x.datos?.variantes as string[] | undefined) ?? []).join("\n");
    f.respuesta = String(x.datos?.respuesta ?? "");
    cambiado.value = false;
  });

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    if (id <= 0) return;
    try {
      await llenar(await leerFuente(id));
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      cargando.value = false;
    }
  });

  const cuerpo = $((): FuenteEditable => {
    const previos = fuente.value?.datos ?? {};
    let datos: Record<string, unknown> = {};
    if (f.tipo === "faq") datos = { ...previos, pregunta: f.pregunta.trim(), variantes: lineas(f.variantes), respuesta: f.respuesta.trim() };
    else if (f.tipo === "promo") datos = { ...previos };
    return {
      tipo: f.tipo,
      titulo: f.titulo.trim() || (f.tipo === "faq" ? f.pregunta.trim() : ""),
      contenido: f.contenido,
      vigente_desde: f.vigente_desde,
      vigente_hasta: f.vigente_hasta,
      etiquetas: etiquetasDe(f.etiquetas),
      datos,
    };
  });

  const guardar = $(async (): Promise<Fuente | null> => {
    error.value = "";
    if (f.vigente_desde && f.vigente_hasta && f.vigente_hasta < f.vigente_desde) {
      error.value = "La fecha «hasta» es anterior a «desde».";
      return null;
    }
    busy.value = true;
    try {
      const body = await cuerpo();
      if (!body.titulo) throw new Error("Ponle un título.");
      const x = fuente.value ? await guardarFuente(fuente.value.id, body) : await crearFuente(body);
      const nueva = !fuente.value;
      // Se relee para tener versión, autor y fechas tal como quedaron en la base.
      const leida = x && x.id ? await leerFuente(x.id) : null;
      if (leida) await llenar(leida);
      if (nueva && leida) history.replaceState(null, "", `?f=${leida.id}`);
      onToast$(nueva ? "Fuente creada como borrador" : "Cambios guardados");
      await onCambio$(leida?.id ?? 0);
      return leida;
    } catch (e) {
      error.value = (e as Error).message;
      return null;
    } finally {
      busy.value = false;
    }
  });

  const estado = $(async (nuevo: "activo" | "borrador" | "archivado" | "descartada", guardarAntes = false) => {
    let x = fuente.value;
    if (guardarAntes || !x) x = await guardar();
    if (!x) return;
    busy.value = true;
    error.value = "";
    try {
      const r = await cambiarEstadoFuente(x.id, nuevo);
      await llenar(r && r.id ? r : await leerFuente(x.id));
      const txt = { activo: "Activada: el robot ya la usa", borrador: "Pasó a borrador", archivado: "Archivada", descartada: "Propuesta descartada" };
      onToast$(txt[nuevo]);
      await onCambio$(x.id);
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      busy.value = false;
    }
  });

  const cargarVersiones = $(async () => {
    if (!fuente.value) return;
    try {
      versiones.value = await versionesFuente(fuente.value.id);
    } catch (e) {
      error.value = (e as Error).message;
    }
  });

  const x = fuente.value;
  const ev = x ? estadoVisible(x) : "borrador";
  const soloLectura = !admin;
  const esPropuesta = x?.estado === "propuesta";
  const d = x?.datos ?? {};

  return (
    <>
    <div class="overlay" onClick$={onClose$}>
      <aside class="drawer drawer-lg" onClick$={(e) => e.stopPropagation()}>
        <header class="drawer-head">
          <div>
            <h2>{x ? x.titulo || "Sin título" : `Nueva: ${TIPOS[f.tipo]?.label.toLowerCase() ?? "fuente"}`}</h2>
            <div class="row wrap fu-badges">
              <TipoBadge tipo={f.tipo} />
              {x && <EstadoPill f={x} />}
              {x && <span class="muted small">v{x.version}</span>}
            </div>
          </div>
          <button class="btn btn-ghost" aria-label="Cerrar" onClick$={onClose$}>
            ✕
          </button>
        </header>

        {cargando.value ? (
          <div class="loading">Cargando…</div>
        ) : (
          <>
            {esPropuesta && (
              <div class="drawer-sec">
                <p class="aviso fu-aviso-info small">
                  🤖 El robot propone esta pregunta
                  {d.ocurrencias ? ` (pasó ${d.ocurrencias} ${d.ocurrencias === 1 ? "vez" : "veces"})` : ""}. Revisa la respuesta: no entra al robot
                  hasta que la apruebes.
                </p>
              </div>
            )}
            <section class="drawer-sec">
              <label>
                Título
                <input
                  value={f.titulo}
                  disabled={soloLectura}
                  maxLength={200}
                  placeholder={f.tipo === "faq" ? "Se usa la pregunta si lo dejas vacío" : "Cambios y devoluciones"}
                  onInput$={(_, el) => {
                    f.titulo = el.value;
                    cambiado.value = true;
                  }}
                />
              </label>
              {!x && (
                <label>
                  Tipo
                  <select value={f.tipo} disabled={soloLectura} onChange$={(_, el) => (f.tipo = el.value as TipoFuente)}>
                    {(Object.keys(TIPOS) as TipoFuente[])
                      .filter((k) => k !== "promo")
                      .map((k) => (
                        <option key={k} value={k}>
                          {TIPOS[k].label}
                        </option>
                      ))}
                  </select>
                  <span class="muted small">{TIPOS[f.tipo]?.hint}</span>
                </label>
              )}
            </section>

            {f.tipo === "faq" && (
              <section class="drawer-sec">
                <h3>Pregunta frecuente</h3>
                <label>
                  Pregunta
                  <input
                    value={f.pregunta}
                    disabled={soloLectura}
                    placeholder="¿Hacen factura?"
                    onInput$={(_, el) => {
                      f.pregunta = el.value;
                      cambiado.value = true;
                    }}
                  />
                </label>
                <label>
                  Otras formas de preguntarlo <span class="muted small">(una por línea)</span>
                  <textarea
                    rows={3}
                    value={f.variantes}
                    disabled={soloLectura}
                    placeholder={"dan factura?\nemiten factura con RUC?"}
                    onInput$={(_, el) => {
                      f.variantes = el.value;
                      cambiado.value = true;
                    }}
                  />
                </label>
                <label>
                  Respuesta <span class="muted small">(el robot la dice tal cual)</span>
                  <textarea
                    rows={4}
                    value={f.respuesta}
                    disabled={soloLectura}
                    placeholder="Sí 😊 Emitimos boleta o factura…"
                    onInput$={(_, el) => {
                      f.respuesta = el.value;
                      cambiado.value = true;
                    }}
                  />
                </label>
              </section>
            )}

            {f.tipo === "promo" && x && (
              <section class="drawer-sec">
                <div class="panel-head">
                  <h3>La promo</h3>
                  {admin && (
                    <button class="btn btn-sm" onClick$={() => onAsistente$(x)}>
                      Editar con el asistente
                    </button>
                  )}
                </div>
                <dl class="fu-dl">
                  <dt>Mecánica</dt>
                  <dd>
                    {MECANICAS[d.mecanica ?? "otra"]?.label ?? d.mecanica} · {mecanicaTexto(d)}
                  </dd>
                  <dt>Alcance</dt>
                  <dd>
                    {alcanceTexto(d.alcance)}
                    {d.alcance?.tipo === "prendas" && d.alcance.codigos?.length ? ` (${d.alcance.codigos.join(", ")})` : ""}
                  </dd>
                  {d.condiciones && (
                    <>
                      <dt>Condiciones</dt>
                      <dd>{d.condiciones}</dd>
                    </>
                  )}
                  {d.objetivo && (
                    <>
                      <dt>Objetivo</dt>
                      <dd>
                        {d.objetivo}
                        {d.meta ? ` · meta ${d.meta}` : ""}
                      </dd>
                    </>
                  )}
                  <dt>Público</dt>
                  <dd>{PUBLICOS.find((p) => p.key === d.publico)?.label ?? "Todas las clientas"}</dd>
                  <dt>Embudo</dt>
                  <dd>{d.embudo_id ? `Propio (#${d.embudo_id})` : "El general"}</dd>
                </dl>
                {d.mensaje_apertura && <p class="fu-burbuja">{d.mensaje_apertura}</p>}
              </section>
            )}

            <section class="drawer-sec">
              <div class="panel-head">
                <h3>{f.tipo === "faq" ? "Notas internas" : "Contenido"}</h3>
                <div class="seg">
                  <button class={["seg-btn", vista.value === "escribir" && "active"]} onClick$={() => (vista.value = "escribir")}>
                    Escribir
                  </button>
                  <button class={["seg-btn", vista.value === "previa" && "active"]} onClick$={() => (vista.value = "previa")}>
                    Vista previa
                  </button>
                </div>
              </div>
              {vista.value === "escribir" ? (
                <>
                  <textarea
                    class="fu-md"
                    rows={f.tipo === "faq" ? 4 : 12}
                    value={f.contenido}
                    disabled={soloLectura}
                    placeholder={
                      f.tipo === "guion"
                        ? "## Paso 1 · Saludo\nHola 😊 Soy …\n\n## Paso 2 · Qué busca\n…"
                        : f.tipo === "faq"
                          ? "Solo para el equipo: el robot no lo dice."
                          : "Lo que el robot puede decir, tal cual. Markdown: **negrita**, listas con «- », títulos con «## »."
                    }
                    onInput$={(_, el) => {
                      f.contenido = el.value;
                      cambiado.value = true;
                    }}
                  />
                  {f.tipo === "guion" && <p class="muted small">El robot sigue los pasos por sus encabezados: «## Paso 1», «## Paso 2»…</p>}
                </>
              ) : f.contenido.trim() ? (
                <div class="fu-previa" dangerouslySetInnerHTML={mdAHtml(f.contenido)} />
              ) : (
                <p class="muted small">Sin contenido.</p>
              )}
            </section>

            <section class="drawer-sec">
              <div class="grid2">
                <label>
                  Vigente desde
                  <input
                    type="date"
                    value={f.vigente_desde}
                    disabled={soloLectura}
                    onInput$={(_, el) => {
                      f.vigente_desde = el.value;
                      cambiado.value = true;
                    }}
                  />
                </label>
                <label>
                  Hasta
                  <input
                    type="date"
                    value={f.vigente_hasta}
                    min={f.vigente_desde || undefined}
                    disabled={soloLectura}
                    onInput$={(_, el) => {
                      f.vigente_hasta = el.value;
                      cambiado.value = true;
                    }}
                  />
                </label>
              </div>
              <p class="muted small fu-nota">Sin fechas, vale mientras esté activa. Las fechas son de Lima e incluyen el último día.</p>
              <label>
                Etiquetas <span class="muted small">(separadas por coma)</span>
                <input
                  value={f.etiquetas}
                  disabled={soloLectura}
                  placeholder="verano, clientas que volvieron"
                  onInput$={(_, el) => {
                    f.etiquetas = el.value;
                    cambiado.value = true;
                  }}
                />
              </label>
            </section>

            {x && (
              <section class="drawer-sec">
                <div class="panel-head">
                  <h3>Versiones</h3>
                  <button class="btn btn-sm btn-ghost" onClick$={cargarVersiones}>
                    {versiones.value ? "Actualizar" : "Ver versiones"}
                  </button>
                </div>
                <p class="muted small">
                  Versión {x.version}
                  {x.actualizado_por ? ` · ${x.actualizado_por}` : ""} · {timeAgo(x.actualizado_en)}
                  {x.origen && x.origen !== "manual" ? ` · origen: ${x.origen.replace("archivo:", "archivo ").replace("propuesta_bot", "propuesta del robot")}` : ""}
                </p>
                {versiones.value && versiones.value.length === 0 && <p class="muted small">Todavía no hay versiones anteriores.</p>}
                {versiones.value && versiones.value.length > 0 && (
                  <ul class="fu-versiones">
                    {versiones.value.map((v) => (
                      <li key={v.version}>
                        <span class="grow">
                          <b>v{v.version}</b> {v.titulo}
                          <span class="muted small">
                            {" "}
                            · {v.autor || "—"} · {timeLabel(v.creado_en)}
                          </span>
                        </span>
                        <button class="btn btn-sm btn-ghost" onClick$={() => (verVersion.value = v)}>
                          Ver
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            )}

            {error.value && <p class="form-error pad">{error.value}</p>}

            {admin && (
              <footer class="drawer-foot fu-acciones">
                {x && (
                  <button class="btn btn-danger btn-sm" disabled={busy.value} onClick$={() => (borrando.value = true)}>
                    Borrar
                  </button>
                )}
                <div class="row wrap fu-acciones-der">
                  {esPropuesta ? (
                    <>
                      <button class="btn btn-sm" disabled={busy.value} onClick$={() => estado("descartada")}>
                        Descartar
                      </button>
                      <button class="btn btn-primary" disabled={busy.value} onClick$={() => estado("activo", true)}>
                        Guardar y aprobar
                      </button>
                    </>
                  ) : (
                    <>
                      {x && (ev === "activo" || ev === "programada") && (
                        <button class="btn btn-sm" disabled={busy.value} onClick$={() => estado("borrador")}>
                          Pasar a borrador
                        </button>
                      )}
                      {x && ev !== "archivado" && (
                        <button class="btn btn-sm" disabled={busy.value} onClick$={() => estado("archivado")}>
                          Archivar
                        </button>
                      )}
                      {(!x || ev === "borrador" || ev === "vencido" || ev === "archivado" || ev === "descartada") && (
                        <button class="btn btn-sm" disabled={busy.value} onClick$={() => estado("activo", cambiado.value || !x)}>
                          {x ? "Activar" : "Guardar y activar"}
                        </button>
                      )}
                      <button class="btn btn-primary" disabled={busy.value || (!!x && !cambiado.value)} onClick$={guardar}>
                        {busy.value ? "Guardando…" : "Guardar"}
                      </button>
                    </>
                  )}
                </div>
              </footer>
            )}
            {!admin && (
              <footer class="drawer-foot">
                <span class="muted small">Solo una admin puede cambiar las fuentes.</span>
              </footer>
            )}
          </>
        )}
      </aside>
    </div>

      {verVersion.value && (
        <div class="overlay overlay-center" onClick$={() => (verVersion.value = null)}>
          <div class="modal modal-lg" onClick$={(e) => e.stopPropagation()}>
            <h2>
              Versión {verVersion.value.version}: {verVersion.value.titulo}
            </h2>
            <p class="muted small">
              {verVersion.value.autor || "—"} · {timeLabel(verVersion.value.creado_en)}
            </p>
            {verVersion.value.datos?.pregunta && (
              <div class="fu-version-faq">
                <p>
                  <b>Pregunta:</b> {String(verVersion.value.datos.pregunta)}
                </p>
                <p>
                  <b>Respuesta:</b> {String(verVersion.value.datos.respuesta ?? "")}
                </p>
              </div>
            )}
            {verVersion.value.contenido ? (
              <div class="fu-previa fu-previa-box" dangerouslySetInnerHTML={mdAHtml(verVersion.value.contenido)} />
            ) : (
              <p class="muted small">Sin contenido.</p>
            )}
            <div class="modal-actions">
              <button class="btn btn-ghost" onClick$={() => (verVersion.value = null)}>
                Cerrar
              </button>
              {admin && (
                <button
                  class="btn btn-primary"
                  onClick$={() => {
                    const v = verVersion.value!;
                    f.titulo = v.titulo;
                    f.contenido = v.contenido ?? "";
                    if (f.tipo === "faq" && v.datos) {
                      f.pregunta = String(v.datos.pregunta ?? "");
                      f.variantes = ((v.datos.variantes as string[] | undefined) ?? []).join("\n");
                      f.respuesta = String(v.datos.respuesta ?? "");
                    }
                    cambiado.value = true;
                    verVersion.value = null;
                    onToast$(`Versión ${v.version} cargada en el editor: pulsa «Guardar» para quedártela`);
                  }}
                >
                  Restaurar en el editor
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {borrando.value && x && (
        <Confirmar
          titulo="¿Borrar esta fuente?"
          texto={`Se borra «${x.titulo}» con sus versiones. Si estaba activa, el robot deja de usarla. No se puede deshacer.`}
          confirmar="Borrar"
          peligro
          onCancelar$={() => (borrando.value = false)}
          onConfirmar$={async () => {
            await borrarFuente(x.id);
            borrando.value = false;
            onToast$("Fuente borrada");
            await onCambio$(0);
            await onClose$();
          }}
        />
      )}
    </>
  );
});

// ---------------------------------------------------------------------------
// Subir un archivo: el backend extrae el texto y crea un borrador.

export const SubirModal = component$<{ onClose$: QRL<() => void>; onCreada$: QRL<(f: Fuente) => void> }>(({ onClose$, onCreada$ }) => {
  const archivo = useSignal<HTMLInputElement>();
  const tipo = useSignal<TipoFuente>("material");
  const tituloS = useSignal("");
  const nombre = useSignal("");
  const busy = useSignal(false);
  const error = useSignal("");

  return (
    <div class="overlay overlay-center" onClick$={onClose$}>
      <form
        class="modal"
        preventdefault:submit
        onClick$={(e) => e.stopPropagation()}
        onSubmit$={async () => {
          const file = archivo.value?.files?.[0];
          if (!file) {
            error.value = "Elige un archivo.";
            return;
          }
          busy.value = true;
          error.value = "";
          try {
            const f = await subirFuente(file, tipo.value, tituloS.value.trim() || file.name.replace(/\.[^.]+$/, ""));
            await onCreada$(f);
          } catch (e) {
            error.value = (e as Error).message;
          } finally {
            busy.value = false;
          }
        }}
      >
        <h2>Subir archivo</h2>
        <p class="muted small">PDF, Word (.docx), texto, Markdown o CSV. Se extrae el texto y queda como borrador para que lo revises antes de activarlo.</p>
        <label>
          Archivo
          <input
            ref={archivo}
            type="file"
            accept=".pdf,.docx,.txt,.md,.csv,application/pdf,text/plain,text/markdown,text/csv,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            required
            onChange$={(_, el) => {
              const n = el.files?.[0]?.name ?? "";
              nombre.value = n;
              if (!tituloS.value.trim()) tituloS.value = n.replace(/\.[^.]+$/, "");
            }}
          />
        </label>
        <div class="grid2">
          <label>
            Tipo
            <select value={tipo.value} onChange$={(_, el) => (tipo.value = el.value as TipoFuente)}>
              {(["material", "politica", "guion", "faq", "promo"] as TipoFuente[]).map((k) => (
                <option key={k} value={k}>
                  {TIPOS[k].label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Título
            <input bind:value={tituloS} maxLength={200} placeholder="Tabla de medidas" />
          </label>
        </div>
        {error.value && <p class="form-error">{error.value}</p>}
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost" onClick$={onClose$}>
            Cancelar
          </button>
          <button class="btn btn-primary" disabled={busy.value}>
            {busy.value ? "Subiendo…" : "Subir y revisar"}
          </button>
        </div>
      </form>
    </div>
  );
});

// ---------------------------------------------------------------------------
// «Lo que sabe el robot»: GET /api/conocimiento resumido.

export const ConocimientoModal = component$<{ onClose$: QRL<() => void>; onAbrir$: QRL<(id: number) => void> }>(({ onClose$, onAbrir$ }) => {
  const c = useSignal<Conocimiento | null>(null);
  const error = useSignal("");

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    try {
      c.value = await leerConocimiento();
    } catch (e) {
      error.value = (e as Error).message;
    }
  });

  const k = c.value;
  const promos = k?.promos ?? [];
  const faqs = k?.faqs ?? [];
  const politicas = k?.politicas ?? [];
  const material = k?.material ?? [];
  const vacio = k && !promos.length && !faqs.length && !politicas.length && !material.length && !k.guion;

  return (
    <div class="overlay overlay-center" onClick$={onClose$}>
      <div class="modal modal-lg" role="dialog" aria-modal="true" onClick$={(e) => e.stopPropagation()}>
        <h2>Lo que sabe el robot</h2>
        <p class="muted small">
          Las fuentes vigentes que recibe en cada mensaje, ahora mismo.
          {k && ` Hoy ${k.hoy} · versión ${k.version.slice(0, 8)}.`}
        </p>
        {error.value && <p class="form-error">{error.value}</p>}
        {!k && !error.value && <div class="loading">Cargando…</div>}
        {vacio && <p class="aviso fu-aviso-info">Todavía no hay fuentes vigentes: el robot atiende con lo de siempre (su guion y datos base).</p>}
        {k && !vacio && (
          <div class="fu-sabe">
            <div class="stats fu-sabe-cifras">
              <div class="stat">
                <span>Promos vigentes</span>
                <strong>{promos.length}</strong>
              </div>
              <div class="stat">
                <span>Preguntas</span>
                <strong>{faqs.length}</strong>
              </div>
              <div class="stat">
                <span>Políticas</span>
                <strong>{politicas.length}</strong>
              </div>
              <div class="stat">
                <span>Material</span>
                <strong>{material.length}</strong>
              </div>
            </div>

            {promos.length > 0 && (
              <section>
                <h3>🏷 Promos</h3>
                <ul class="fu-sabe-lista">
                  {promos.map((p) => (
                    <li key={p.id}>
                      <button class="link-plain" onClick$={() => onAbrir$(p.id)}>
                        <b>{p.titulo}</b>
                      </button>
                      <span class="muted small">
                        {" "}
                        · {mecanicaTexto(p)} · {vigenciaTexto(p.vigente_desde, p.vigente_hasta)}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            <section>
              <h3>🗒 Guion</h3>
              {k.guion ? (
                <p>
                  <button class="link-plain" onClick$={() => onAbrir$(k.guion!.id)}>
                    <b>{k.guion.titulo}</b>
                  </button>
                  <span class="muted small"> · {(k.guion.contenido.match(/^##\s/gm) ?? []).length} pasos</span>
                </p>
              ) : (
                <p class="muted small">Sin guion propio: usa el de siempre.</p>
              )}
            </section>
            {faqs.length > 0 && (
              <section>
                <h3>❓ Preguntas frecuentes</h3>
                <ul class="fu-sabe-lista">
                  {faqs.map((q) => (
                    <li key={q.id}>
                      <button class="link-plain" onClick$={() => onAbrir$(q.id)}>
                        <b>{q.pregunta}</b>
                      </button>
                      {(q.variantes?.length ?? 0) > 0 && <span class="muted small"> · +{q.variantes!.length} variantes</span>}
                      <div class="muted small fu-recorte">{q.respuesta}</div>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {politicas.length > 0 && (
              <section>
                <h3>📜 Políticas</h3>
                <ul class="fu-sabe-lista">
                  {politicas.map((p) => (
                    <li key={p.id}>
                      <button class="link-plain" onClick$={() => onAbrir$(p.id)}>
                        <b>{p.titulo}</b>
                      </button>
                      <span class="muted small"> · {p.contenido.length} caracteres</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {material.length > 0 && (
              <section>
                <h3>📎 Material</h3>
                <ul class="fu-sabe-lista">
                  {material.map((p) => (
                    <li key={p.id}>
                      <button class="link-plain" onClick$={() => onAbrir$(p.id)}>
                        <b>{p.titulo}</b>
                      </button>
                      <span class="muted small"> · {p.contenido.length} caracteres</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        )}
        <div class="modal-actions">
          <button class="btn btn-primary" onClick$={onClose$}>
            Cerrar
          </button>
        </div>
      </div>
    </div>
  );
});

// ---------------------------------------------------------------------------
// Tarjetas de resultados de las promos (GET /api/promos/resumen).

export const CifrasPromoFila = component$<{ c: { conversaciones: number; pedidos: number; confirmados: number; pagados: number; monto: number } }>(({ c }) => (
  <div class="fu-cifras">
    <span>
      <b>{c.conversaciones}</b> conversaciones
    </span>
    <span>
      <b>{c.pedidos}</b> pedidos
    </span>
    <span>
      <b>{c.confirmados}</b> confirmados
    </span>
    <span>
      <b>{c.pagados}</b> pagados
    </span>
    <span>
      <b>{money(c.monto)}</b>
    </span>
  </div>
));
