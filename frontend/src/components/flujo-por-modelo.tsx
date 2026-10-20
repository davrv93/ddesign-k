// «Un flujo por modelo»: copia el flujo abierto a cada modelo elegido, como los Salesbots por modelo de Kommo. Las copias
// nacen apagadas y se editan por separado; encendida, la de un modelo gana sobre el flujo general solo para ese modelo.
// Contrato: docs/SEGUIMIENTO_CONTRATO.md §4b.
import { $, component$, useSignal, useStore, type QRL } from "@builder.io/qwik";
import type { Product } from "~/lib/api";
import { flujoPorModelo } from "~/lib/flujos";

interface Props {
  baseId: number;
  baseNombre: string;
  productos: Product[];
  propios: Record<string, string>; // código → nombre del flujo que ya tiene
  onCerrar$: QRL<() => void>;
  onHecho$: QRL<(creadas: number) => void>;
}

const sinTilde = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

export const FlujoPorModelo = component$<Props>((p) => {
  const libres = p.productos.filter((x) => x.active && !p.propios[x.code.toUpperCase()]);
  const sel = useStore<Record<string, boolean>>(Object.fromEntries(libres.map((x) => [x.code, true])));
  const busca = useSignal("");
  const busy = useSignal(false);
  const error = useSignal("");
  const hecho = useSignal<{
    creadas: string[];
    omitidas: Record<string, string>;
  } | null>(null);

  // Filtrar dentro de cada QRL: la que se arma en el render queda con la búsqueda de entonces.
  const filtrar = (texto: string) => {
    const q = sinTilde(texto.trim());
    return p.productos.filter((x) => x.active && (!q || sinTilde(`${x.code} ${x.name} ${x.category ?? ""}`).includes(q)));
  };
  const q = busca.value.trim();
  const visibles = filtrar(busca.value);
  const elegidos = libres.filter((x) => sel[x.code]).map((x) => x.code);

  const marcar = $((v: boolean) => {
    for (const x of filtrar(busca.value)) if (!p.propios[x.code.toUpperCase()]) sel[x.code] = v;
  });

  const crear = $(async () => {
    busy.value = true;
    error.value = "";
    try {
      hecho.value = await flujoPorModelo(
        p.baseId,
        libres.filter((x) => sel[x.code]).map((x) => x.code),
      );
      await p.onHecho$(hecho.value.creadas.length);
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      busy.value = false;
    }
  });

  return (
    <div class="overlay overlay-center fl-probar-capa" onClick$={p.onCerrar$}>
      <div class="modal modal-lg fl-probar fl-pm" role="dialog" aria-modal="true" aria-labelledby="fl-pm-t" onClick$={(e) => e.stopPropagation()}>
        <header class="fl-probar-head">
          <h2 id="fl-pm-t">Un flujo por modelo</h2>
          <button class="btn btn-ghost" aria-label="Cerrar" onClick$={p.onCerrar$}>
            ✕
          </button>
        </header>
        {hecho.value ? (
          <div class="fl-ok">
            <p>
              ✅ {hecho.value.creadas.length === 1 ? "Se creó 1 flujo" : `Se crearon ${hecho.value.creadas.length} flujos`}, uno por modelo, todos{" "}
              <b>apagados</b>. Edita el que quieras ajustar y enciéndelos desde la lista (puedes encender varios a la vez).
            </p>
            {Object.keys(hecho.value.omitidas).length > 0 && (
              <p class="muted small">
                No se crearon:{" "}
                {Object.entries(hecho.value.omitidas)
                  .map(([c, m]) => `${c} (${m})`)
                  .join(", ")}
                .
              </p>
            )}
            <button class="btn btn-primary" onClick$={p.onCerrar$}>
              Listo
            </button>
          </div>
        ) : (
          <>
            <p class="muted small">
              Copia «{p.baseNombre}» a cada modelo elegido. Cada copia se edita por separado (textos, fotos, esperas) y nace <b>apagada</b>. Encendida, la de un
              modelo gana sobre este flujo general solo para ese modelo.
            </p>
            <div class="row wrap fl-pm-barra">
              <input
                type="search"
                placeholder="Buscar modelo, código o categoría"
                value={busca.value}
                onInput$={(_, el) => (busca.value = el.value)}
                aria-label="Buscar modelo"
              />
              <button class="btn btn-sm" onClick$={() => marcar(true)}>
                Marcar {q ? "los que se ven" : "todos"}
              </button>
              <button class="btn btn-sm btn-ghost" onClick$={() => marcar(false)}>
                Ninguno
              </button>
            </div>
            <ul class="fl-pm-lista">
              {visibles.map((x) => {
                const propio = p.propios[x.code.toUpperCase()];
                return (
                  <li key={x.code}>
                    <label class={["check", propio && "muted"]}>
                      <input type="checkbox" disabled={!!propio} checked={!propio && !!sel[x.code]} onChange$={(_, el) => (sel[x.code] = el.checked)} />
                      <span>
                        <b>{x.code}</b> · {x.name}
                        {propio && <span class="chip fl-pm-chip">ya tiene «{propio}»</span>}
                      </span>
                    </label>
                  </li>
                );
              })}
              {visibles.length === 0 && <li class="muted small">Ningún modelo coincide.</li>}
            </ul>
            <div class="row">
              <button class="btn btn-primary" disabled={busy.value || elegidos.length === 0} onClick$={crear}>
                {busy.value ? "Creando…" : `Crear ${elegidos.length} ${elegidos.length === 1 ? "flujo" : "flujos"}`}
              </button>
              <span class="muted small">No envía nada: quedan apagados.</span>
            </div>
          </>
        )}
        {error.value && (
          <p class="form-error" role="alert">
            {error.value}
          </p>
        )}
      </div>
    </div>
  );
});
