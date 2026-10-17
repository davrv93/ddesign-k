// Contenido de seguimiento de cada modelo (docs/SEGUIMIENTO_CONTRATO.md §3): beneficio, testimonio y tip. Lo usan los pasos
// de las secuencias del mismo nombre. «web» = lo trajo el script de la tienda en línea; «manual» = lo escribió la tienda
// aquí (el script nunca lo pisa). Va como pestaña en Productos y como tabla de resumen en /seguimiento.
import { $, component$, useOnWindow, useSignal, useStore, useVisibleTask$ } from "@builder.io/qwik";
import {
  contenidoProducto,
  guardarContenido,
  miSesion,
  resumenContenido,
  type ClaveContenido,
  type ContenidoProducto,
  type ResumenContenido,
} from "~/lib/api";
import { u } from "~/lib/base";
import { timeAgo } from "~/lib/format";

export const CLAVES: { clave: ClaveContenido; label: string; hint: string; conMedia: boolean }[] = [
  { clave: "beneficio", label: "Beneficio", hint: "Por qué este modelo le conviene: lo que resuelve, cómo la hace sentir.", conMedia: false },
  { clave: "testimonio", label: "Testimonio", hint: "Lo que dijo una clienta real. Puede llevar una foto.", conMedia: true },
  { clave: "tip", label: "Tip", hint: "Cómo combinarlo: cartera, zapatos, accesorios.", conMedia: false },
];

const ORIGEN: Record<string, string> = { web: "de la web", manual: "escrito por la tienda" };

const esImagen = (url: string) => /\.(png|jpe?g|webp|gif|avif)(\?.*)?$/i.test(url) || url.startsWith("data:image/") || url.startsWith("/media/");

interface Pieza {
  texto: string;
  media_url: string;
  origen: string;
  actualizado_en: string;
  guardado: string; // texto|media tal como está en el servidor, para saber si cambió
}

export const ContenidoSeguimiento = component$<{ productId: number }>(({ productId }) => {
  const piezas = useStore<Record<ClaveContenido, Pieza>>(
    {
      beneficio: { texto: "", media_url: "", origen: "", actualizado_en: "", guardado: "|" },
      testimonio: { texto: "", media_url: "", origen: "", actualizado_en: "", guardado: "|" },
      tip: { texto: "", media_url: "", origen: "", actualizado_en: "", guardado: "|" },
    },
    { deep: true },
  );
  const cargando = useSignal(true);
  const admin = useSignal(false);
  const error = useSignal("");
  const ok = useSignal("");
  const busy = useSignal<ClaveContenido | "">("");

  const poner = $((lista: ContenidoProducto[]) => {
    for (const c of CLAVES) {
      const x = lista.find((l) => l.clave === c.clave);
      piezas[c.clave] = {
        texto: x?.texto ?? "",
        media_url: x?.media_url ?? "",
        origen: x?.origen ?? "",
        actualizado_en: x?.actualizado_en ?? "",
        guardado: `${x?.texto ?? ""}|${x?.media_url ?? ""}`,
      };
    }
  });

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    miSesion()
      .then((s) => (admin.value = s.admin))
      .catch(() => {});
    try {
      await poner(await contenidoProducto(productId));
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      cargando.value = false;
    }
  });

  const guardar = $(async (clave: ClaveContenido) => {
    const p = piezas[clave];
    busy.value = clave;
    error.value = "";
    try {
      await guardarContenido(productId, clave, { texto: p.texto.trim(), media_url: p.media_url.trim() });
      await poner(await contenidoProducto(productId));
      const borrado = !p.texto.trim() && !p.media_url.trim();
      ok.value = `${CLAVES.find((c) => c.clave === clave)?.label}: ${borrado ? "quitado" : "guardado"}`;
      setTimeout(() => (ok.value = ""), 2500);
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      busy.value = "";
    }
  });

  if (cargando.value) return <div class="loading">Cargando…</div>;

  return (
    <div class="contenido-seg">
      <p class="muted small">
        Lo usan los pasos «beneficio», «testimonio» y «tip» de las <a href={u("/seguimiento")}>secuencias de seguimiento</a>. Si un
        modelo no tiene una pieza, ese paso se salta para él.
      </p>
      {error.value && <p class="form-error small">{error.value}</p>}
      {ok.value && <p class="ok-text small">{ok.value} ✓</p>}
      {CLAVES.map((c) => {
        const p = piezas[c.clave];
        const cambiado = `${p.texto}|${p.media_url}` !== p.guardado;
        const id = `cont-${productId}-${c.clave}`;
        return (
          <fieldset key={c.clave} class="contenido-pieza">
            <legend>
              {c.label}
              {p.origen ? <span class={["chip", p.origen === "manual" && "ok"]}>{ORIGEN[p.origen] ?? p.origen}</span> : <span class="chip">vacío</span>}
            </legend>
            <p class="muted small">
              {c.hint}
              {p.actualizado_en && ` · actualizado ${timeAgo(p.actualizado_en)}`}
            </p>
            <label for={`${id}-texto`} class="sr-only">
              Texto del {c.label.toLowerCase()}
            </label>
            <textarea
              id={`${id}-texto`}
              rows={3}
              value={p.texto}
              disabled={!admin.value}
              placeholder={c.clave === "testimonio" ? "«Me lo puse para el matrimonio de mi prima y todas me preguntaron…»" : ""}
              onInput$={(_, el) => (piezas[c.clave].texto = el.value)}
            />
            {c.conMedia && (
              <div class="contenido-media">
                <label>
                  Foto o video (enlace)
                  <input
                    type="url"
                    inputMode="url"
                    placeholder="https://…"
                    value={p.media_url}
                    disabled={!admin.value}
                    onInput$={(_, el) => (piezas[c.clave].media_url = el.value)}
                  />
                </label>
                {p.media_url && esImagen(p.media_url) && <img src={u(p.media_url)} alt={`Testimonio del modelo`} width={120} height={160} loading="lazy" />}
                {p.media_url && !esImagen(p.media_url) && (
                  <a class="small" href={u(p.media_url)} target="_blank" rel="noreferrer">
                    Abrir enlace ↗
                  </a>
                )}
              </div>
            )}
            {admin.value && (
              <div class="row wrap">
                <button type="button" class="btn btn-sm btn-primary" disabled={!cambiado || busy.value === c.clave} onClick$={() => guardar(c.clave)}>
                  {busy.value === c.clave ? "Guardando…" : "Guardar"}
                </button>
                {p.origen && (
                  <button
                    type="button"
                    class="btn btn-sm btn-ghost btn-borrar"
                    disabled={busy.value === c.clave}
                    onClick$={async () => {
                      piezas[c.clave].texto = "";
                      piezas[c.clave].media_url = "";
                      await guardar(c.clave);
                    }}
                  >
                    Quitar
                  </button>
                )}
                {p.origen === "web" && <span class="muted small">Si lo editas, queda como escrito por la tienda y la web ya no lo cambia.</span>}
              </div>
            )}
          </fieldset>
        );
      })}
    </div>
  );
});

// ---------------------------------------------------------------------------
// Resumen: qué modelos tienen cada pieza (GET /api/contenido/resumen).

export const ResumenContenidoTabla = component$(() => {
  const filas = useSignal<ResumenContenido[]>([]);
  const cargando = useSignal(true);
  const error = useSignal("");
  const q = useSignal("");
  const soloIncompletos = useSignal(false);

  const load = $(async () => {
    try {
      filas.value = await resumenContenido();
      error.value = "";
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      cargando.value = false;
    }
  });
  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(() => load());
  useOnWindow(
    "kd-change",
    $((e: Event) => {
      const t = (e as CustomEvent<string>).detail;
      if (t === "products" || t === "seguimiento") load();
    }),
  );

  const busca = q.value.trim().toLowerCase();
  const lista = filas.value.filter(
    (f) => (!busca || `${f.codigo} ${f.nombre}`.toLowerCase().includes(busca)) && (!soloIncompletos.value || !(f.beneficio && f.testimonio && f.tip)),
  );
  const cuenta = (k: ClaveContenido) => filas.value.filter((f) => f[k]).length;

  return (
    <section>
      <div class="filters">
        <input class="search grow" type="search" placeholder="Buscar modelo…" aria-label="Buscar modelo" bind:value={q} />
        <label class="check">
          <input type="checkbox" bind:checked={soloIncompletos} /> Solo los que les falta algo
        </label>
      </div>
      {error.value && <p class="form-error">{error.value}</p>}
      {!cargando.value && filas.value.length > 0 && (
        <p class="muted small">
          De {filas.value.length} modelos: {cuenta("beneficio")} con beneficio · {cuenta("testimonio")} con testimonio · {cuenta("tip")} con tip.
          Toca un modelo para completarlo.
        </p>
      )}
      {cargando.value ? (
        <div class="loading">Cargando…</div>
      ) : lista.length === 0 ? (
        <div class="panel empty">
          <p class="muted">{filas.value.length === 0 ? "Aún no hay modelos." : "Ningún modelo coincide."}</p>
        </div>
      ) : (
        <div class="tbl-wrap">
          <table class="tbl tbl-cards">
            <thead>
              <tr>
                <th>Modelo</th>
                {CLAVES.map((c) => (
                  <th key={c.clave} class="center">
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {lista.map((f) => (
                <tr key={f.product_id}>
                  <td data-label="Modelo" class="tbl-titulo">
                    <a href={u(`/productos?p=${f.product_id}&tab=seguimiento`)}>
                      <b>{f.codigo}</b> {f.nombre}
                    </a>
                  </td>
                  {CLAVES.map((c) => (
                    <td key={c.clave} data-label={c.label} class="center">
                      {f[c.clave] ? (
                        <span class="ok-text" aria-label="Sí">
                          ✓
                        </span>
                      ) : (
                        <span class="muted" aria-label="Falta">
                          —
                        </span>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
});
