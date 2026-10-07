// Ficha técnica de una prenda: se ve completa (cada dato con su fuente, «No figura» en lo que falta, medidas por
// talla pendientes, contradicciones y lo que falta confirmar) y se edita con todos sus campos. Al guardar, el
// backend deja con fuente «tienda» lo que se cambió a mano.
import { $, component$, useSignal, useStore, type QRL } from "@builder.io/qwik";
import { api, type Dato, type Ficha, type Fuente } from "~/lib/api";

export const ATRIBUTOS: [string, string][] = [
  ["silueta", "Silueta"],
  ["largo", "Largo"],
  ["escote", "Escote"],
  ["mangas", "Mangas"],
  ["cintura", "Cintura"],
  ["espalda", "Espalda"],
  ["cierre", "Cierre"],
  ["tela", "Tela"],
  ["forro", "Forro"],
  ["transparencias", "Transparencias"],
  ["estampado", "Estampado"],
  ["color_visto", "Color (en la foto)"],
];

const FUENTE: Record<Fuente, string> = { diners: "Tienda", foto: "Foto", ambas: "Tienda y foto", tienda: "Tienda · a mano" };

/** Datos completos de 13 (12 atributos + cuidados). */
export const datosFicha = (f?: Ficha | null): [number, number] => {
  if (!f) return [0, 13];
  let n = ATRIBUTOS.filter(([k]) => (f.atributos?.[k]?.valor ?? "").trim() !== "").length;
  if ((f.cuidados ?? "").trim()) n++;
  return [n, 13];
};

const vacia = (): Ficha => ({
  piezas: [],
  atributos: {},
  detalles: [],
  como_queda: [],
  cuidados: null,
  resumen: "",
  discrepancias: [],
  pendiente_tienda: [],
});

const clonar = (f?: Ficha | null): Ficha => {
  const c = structuredClone(f ?? vacia());
  c.atributos = c.atributos ?? {};
  for (const [k] of ATRIBUTOS) c.atributos[k] = c.atributos[k] ?? { valor: "", fuente: "tienda" };
  for (const k of ["piezas", "detalles", "como_queda", "discrepancias", "pendiente_tienda"] as const) (c as any)[k] = c[k] ?? [];
  c.cuidados = c.cuidados ?? "";
  return c;
};

const Fuente_ = ({ f }: { f?: Fuente | null }) => (f ? <span class={["fuente", `fuente-${f}`]}>{FUENTE[f] ?? f}</span> : null);

interface Props {
  productId: number;
  sizes: string[];
  ficha?: Ficha | null;
  onSaved$: QRL<(f: Ficha) => void>;
}

export const FichaTecnica = component$<Props>(({ productId, sizes, ficha, onSaved$ }) => {
  const editing = useSignal(false);
  const actual = useSignal<Ficha | null>(ficha ?? null);
  const f = useStore<{ v: Ficha }>({ v: clonar(ficha) }, { deep: true });
  const busy = useSignal(false);
  const error = useSignal("");

  const guardar = $(async () => {
    busy.value = true;
    error.value = "";
    try {
      const v = f.v;
      const body = {
        ...v,
        cuidados: (v.cuidados ?? "").trim() || null,
        atributos: Object.fromEntries(ATRIBUTOS.map(([k]) => [k, (v.atributos?.[k]?.valor ?? "").trim() ? v.atributos![k] : null])),
      };
      const r = await api<{ ficha: Ficha }>(`/api/products/${productId}/ficha`, { method: "PUT", json: body });
      actual.value = r.ficha;
      f.v = clonar(r.ficha);
      editing.value = false;
      await onSaved$(r.ficha);
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      busy.value = false;
    }
  });

  const fa = actual.value;
  const [n, total] = datosFicha(fa);

  if (!editing.value) {
    return (
      <div class="ficha">
        <div class="ficha-head">
          <div class="ficha-meter" title={`${n} de ${total} datos`}>
            <span style={{ width: `${(n / total) * 100}%` }} />
          </div>
          <span class="muted small">
            {n}/{total} datos
          </span>
          <button type="button" class="btn btn-sm push-right" onClick$={() => (editing.value = true)}>
            ✎ Editar ficha
          </button>
        </div>
        {!fa && <p class="muted">Este producto aún no tiene ficha técnica. Puedes crearla con «Editar ficha».</p>}
        {fa?.resumen && <p class="ficha-resumen">{fa.resumen}</p>}
        {(fa?.piezas?.length ?? 0) > 0 && (
          <p class="ficha-piezas">
            <span class="muted small">Piezas</span> {fa!.piezas!.join(" + ")}
          </p>
        )}

        <h3 class="ficha-h">Atributos</h3>
        <dl class="ficha-attrs">
          {ATRIBUTOS.map(([k, label]) => {
            const d = fa?.atributos?.[k];
            return (
              <div key={k} class="ficha-attr">
                <dt>{label}</dt>
                <dd>
                  {d?.valor ? (
                    <>
                      <span>{d.valor}</span> <Fuente_ f={d.fuente} />
                    </>
                  ) : (
                    <span class="no-figura">No figura</span>
                  )}
                </dd>
              </div>
            );
          })}
        </dl>

        <div class="ficha-cols">
          <Lista titulo="Detalles" datos={fa?.detalles} />
          <Lista titulo="Cómo queda" datos={fa?.como_queda} />
        </div>
        <h3 class="ficha-h">Cuidados</h3>
        <p>{fa?.cuidados ? fa.cuidados : <span class="no-figura">No figura</span>}</p>

        <h3 class="ficha-h">Medidas por talla</h3>
        <div class="ficha-medidas">
          <table>
            <thead>
              <tr>
                <th>Talla</th>
                <th>Busto</th>
                <th>Cintura</th>
                <th>Cadera</th>
                <th>Largo</th>
              </tr>
            </thead>
            <tbody>
              {(sizes.length ? sizes : ["—"]).map((s) => (
                <tr key={s}>
                  <th>{s}</th>
                  {[0, 1, 2, 3].map((i) => (
                    <td key={i} class="pendiente">
                      Pendiente
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <p class="muted small">Las medidas las confirma la tienda; el bot no las da mientras falten.</p>
        </div>

        {(fa?.discrepancias?.length ?? 0) > 0 && (
          <div class="ficha-box box-warn">
            <b>Contradicciones entre el texto y la foto</b>
            <ul>
              {fa!.discrepancias!.map((d, i) => (
                <li key={i}>{d}</li>
              ))}
            </ul>
          </div>
        )}
        {(fa?.pendiente_tienda?.length ?? 0) > 0 && (
          <div class="ficha-box box-info">
            <b>Falta confirmar con la tienda</b>
            <ul>
              {fa!.pendiente_tienda!.map((d, i) => (
                <li key={i}>{d}</li>
              ))}
            </ul>
          </div>
        )}
        <p class="muted small ficha-leyenda">
          Fuente: <Fuente_ f="diners" /> texto de la tienda · <Fuente_ f="foto" /> lo que se ve en la foto · <Fuente_ f="ambas" /> los dos ·{" "}
          <Fuente_ f="tienda" /> escrito en el panel.
        </p>
      </div>
    );
  }

  const v = f.v;
  return (
    <div class="ficha ficha-edit">
      <p class="muted small">
        Lo que cambies o agregues queda con fuente «Tienda · a mano». Deja vacío lo que no se sepa: se mostrará como «No figura».
      </p>
      <label>
        Resumen
        <textarea rows={3} value={v.resumen} onInput$={(_, el) => (f.v.resumen = el.value)} />
      </label>
      <label>
        Piezas (separadas por coma)
        <input
          value={(v.piezas ?? []).join(", ")}
          onChange$={(_, el) =>
            (f.v.piezas = el.value
              .split(",")
              .map((x) => x.trim())
              .filter(Boolean))
          }
          placeholder="vestido"
        />
      </label>
      <h3 class="ficha-h">Atributos</h3>
      <div class="ficha-attrs-edit">
        {ATRIBUTOS.map(([k, label]) => {
          const orig = fa?.atributos?.[k];
          const val = v.atributos?.[k]?.valor ?? "";
          const cambia = val.trim() !== (orig?.valor ?? "").trim();
          return (
            <label key={k}>
              <span class="lbl">
                {label} {val.trim() ? <Fuente_ f={cambia ? "tienda" : orig?.fuente} /> : <span class="no-figura small">No figura</span>}
              </span>
              <input value={val} placeholder="No figura" onInput$={(_, el) => (f.v.atributos![k] = { valor: el.value, fuente: "tienda" })} />
            </label>
          );
        })}
      </div>
      <ListaEdit titulo="Detalles" items={v.detalles!} />
      <ListaEdit titulo="Cómo queda" items={v.como_queda!} />
      <label>
        Cuidados
        <textarea rows={2} value={v.cuidados ?? ""} placeholder="No figura" onInput$={(_, el) => (f.v.cuidados = el.value)} />
      </label>
      <TextosEdit titulo="Contradicciones entre el texto y la foto" items={v.discrepancias!} />
      <TextosEdit titulo="Falta confirmar con la tienda" items={v.pendiente_tienda!} />
      {error.value && <p class="form-error">{error.value}</p>}
      <div class="modal-actions">
        <button
          type="button"
          class="btn btn-ghost"
          onClick$={() => {
            f.v = clonar(actual.value);
            editing.value = false;
          }}
        >
          Cancelar
        </button>
        <button type="button" class="btn btn-primary" disabled={busy.value} onClick$={guardar}>
          {busy.value ? "Guardando…" : "Guardar ficha"}
        </button>
      </div>
    </div>
  );
});

const Lista = ({ titulo, datos }: { titulo: string; datos?: Dato[] | null }) => (
  <div>
    <h3 class="ficha-h">{titulo}</h3>
    {datos?.length ? (
      <ul class="ficha-list">
        {datos.map((d, i) => (
          <li key={i}>
            {d.valor} <Fuente_ f={d.fuente} />
          </li>
        ))}
      </ul>
    ) : (
      <p class="no-figura">No figura</p>
    )}
  </div>
);

const ListaEdit = component$<{ titulo: string; items: Dato[] }>(({ titulo, items }) => (
  <fieldset class="ficha-fs">
    <legend>{titulo}</legend>
    {items.map((d, i) => (
      <div class="ficha-row" key={i}>
        <input value={d.valor} onInput$={(_, el) => (items[i] = { valor: el.value, fuente: "tienda" })} />
        <Fuente_ f={d.fuente} />
        <button type="button" class="btn btn-ghost btn-sm" aria-label="Quitar" onClick$={() => items.splice(i, 1)}>
          ✕
        </button>
      </div>
    ))}
    <button type="button" class="btn btn-sm" onClick$={() => items.push({ valor: "", fuente: "tienda" })}>
      + Agregar
    </button>
  </fieldset>
));

const TextosEdit = component$<{ titulo: string; items: string[] }>(({ titulo, items }) => (
  <fieldset class="ficha-fs">
    <legend>{titulo}</legend>
    {items.map((d, i) => (
      <div class="ficha-row" key={i}>
        <input value={d} onInput$={(_, el) => (items[i] = el.value)} />
        <button type="button" class="btn btn-ghost btn-sm" aria-label="Quitar" onClick$={() => items.splice(i, 1)}>
          ✕
        </button>
      </div>
    ))}
    <button type="button" class="btn btn-sm" onClick$={() => items.push("")}>
      + Agregar
    </button>
  </fieldset>
));
