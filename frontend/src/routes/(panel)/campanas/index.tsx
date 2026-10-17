import { $, component$, useSignal, useVisibleTask$ } from "@builder.io/qwik";
import type { DocumentHead } from "@builder.io/qwik-city";
import { titulo } from "~/lib/marca";
import { campanas, type CifrasCampana, type FilaCampana } from "~/lib/api";
import { fechaISO } from "~/lib/format";

// Campañas: de qué anuncio llegó cada conversación y cuánto vendió (docs/SEGUIMIENTO_CONTRATO.md §6). Las fechas son de
// Lima e inclusivas; «Sin anuncio» agrupa lo orgánico. El monto suma los pedidos confirmados, en preparación, enviados y
// entregados. Conversión = confirmados / conversaciones.

const soles = (v: number) => `S/ ${(v || 0).toLocaleString("es-PE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const conversion = (c: Partial<CifrasCampana>) => {
  const n = c.conversaciones ?? 0;
  return n > 0 ? `${Math.round(((c.confirmados ?? 0) / n) * 1000) / 10}%` : "—";
};
const nombreFila = (f: FilaCampana) => f.titular || (f.ad_id ? `Anuncio ${f.ad_id}` : f.clave || "Sin anuncio");
const CANAL: Record<string, string> = { facebook: "Facebook", instagram: "Instagram", whatsapp: "WhatsApp", web: "Web" };

const sumar = (filas: FilaCampana[]): CifrasCampana =>
  filas.reduce(
    (t, f) => ({
      conversaciones: t.conversaciones + (f.conversaciones || 0),
      calientes: t.calientes + (f.calientes || 0),
      pedidos: t.pedidos + (f.pedidos || 0),
      confirmados: t.confirmados + (f.confirmados || 0),
      monto: t.monto + (f.monto || 0),
    }),
    { conversaciones: 0, calientes: 0, pedidos: 0, confirmados: 0, monto: 0 },
  );

export default component$(() => {
  const desde = useSignal("");
  const hasta = useSignal("");
  const filas = useSignal<FilaCampana[]>([]);
  const total = useSignal<CifrasCampana | null>(null);
  const cargando = useSignal(true);
  const error = useSignal("");

  const load = $(async () => {
    if (!desde.value || !hasta.value) return;
    if (desde.value > hasta.value) {
      error.value = "La fecha «desde» debe ser anterior a «hasta».";
      return;
    }
    cargando.value = true;
    try {
      const r = await campanas(desde.value, hasta.value);
      filas.value = r.filas;
      total.value = r.total && r.total.conversaciones != null ? { ...sumar(r.filas), ...r.total } : sumar(r.filas);
      error.value = "";
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      cargando.value = false;
    }
  });

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    const hoy = new Date();
    const ini = new Date(hoy.getFullYear(), hoy.getMonth(), hoy.getDate() - 29);
    hasta.value = fechaISO(hoy);
    desde.value = fechaISO(ini);
    await load();
  });

  const t = total.value;

  return (
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Campañas</h1>
          <p class="muted">De qué anuncio llegó cada clienta y cuánto vendió. «Sin anuncio» son las que escribieron por su cuenta.</p>
        </div>
        <form class="head-actions camp-fechas" preventdefault:submit onSubmit$={load}>
          <label>
            Desde
            <input type="date" value={desde.value} max={hasta.value} onChange$={(_, el) => ((desde.value = el.value), load())} />
          </label>
          <label>
            Hasta
            <input type="date" value={hasta.value} min={desde.value} onChange$={(_, el) => ((hasta.value = el.value), load())} />
          </label>
        </form>
      </div>

      {error.value && <p class="form-error">{error.value}</p>}

      {t && filas.value.length > 0 && (
        <div class="stats">
          <div class="stat">
            <span>Conversaciones</span>
            <strong>{t.conversaciones}</strong>
          </div>
          <div class="stat">
            <span>Pedidos confirmados</span>
            <strong>{t.confirmados}</strong>
          </div>
          <div class="stat">
            <span>Monto</span>
            <strong>{soles(t.monto)}</strong>
          </div>
          <div class="stat">
            <span>Conversión</span>
            <strong>{conversion(t)}</strong>
          </div>
        </div>
      )}

      {cargando.value && filas.value.length === 0 ? (
        <div class="loading">Cargando…</div>
      ) : filas.value.length === 0 ? (
        <div class="panel empty">
          <p class="muted">No llegaron conversaciones en esas fechas.</p>
        </div>
      ) : (
        <div class={["tbl-wrap", cargando.value && "cargando"]}>
          <table class="tbl tbl-cards camp-tbl">
            <thead>
              <tr>
                <th>Campaña</th>
                <th class="num">Conversaciones</th>
                <th class="num">Calientes</th>
                <th class="num">Pedidos</th>
                <th class="num">Confirmados</th>
                <th class="num">Monto</th>
                <th class="num">Conversión</th>
              </tr>
            </thead>
            <tbody>
              {filas.value.map((f) => (
                <tr key={f.clave || "organico"}>
                  <td data-label="Campaña" class="tbl-titulo">
                    <strong>{nombreFila(f)}</strong>
                    <span class="muted small block">
                      {[CANAL[f.canal] ?? f.canal, f.ad_id && f.titular ? `ID ${f.ad_id}` : ""].filter(Boolean).join(" · ") ||
                        (f.ad_id ? "" : "Orgánico")}
                    </span>
                  </td>
                  <td data-label="Conversaciones" class="num">
                    {f.conversaciones}
                  </td>
                  <td data-label="Calientes" class="num">
                    {f.calientes}
                  </td>
                  <td data-label="Pedidos" class="num">
                    {f.pedidos}
                  </td>
                  <td data-label="Confirmados" class="num">
                    {f.confirmados}
                  </td>
                  <td data-label="Monto" class="num">
                    {soles(f.monto)}
                  </td>
                  <td data-label="Conversión" class="num">
                    <b>{conversion(f)}</b>
                  </td>
                </tr>
              ))}
            </tbody>
            {t && (
              <tfoot>
                <tr class="tbl-total">
                  <td data-label="Campaña" class="tbl-titulo">
                    <strong>Total</strong>
                  </td>
                  <td data-label="Conversaciones" class="num">
                    {t.conversaciones}
                  </td>
                  <td data-label="Calientes" class="num">
                    {t.calientes}
                  </td>
                  <td data-label="Pedidos" class="num">
                    {t.pedidos}
                  </td>
                  <td data-label="Confirmados" class="num">
                    {t.confirmados}
                  </td>
                  <td data-label="Monto" class="num">
                    {soles(t.monto)}
                  </td>
                  <td data-label="Conversión" class="num">
                    <b>{conversion(t)}</b>
                  </td>
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}
      <p class="muted small">
        El monto suma los pedidos confirmados, en preparación, enviados y entregados. Conversión = pedidos confirmados ÷ conversaciones.
        Fechas en hora de Lima.
      </p>
    </div>
  );
});

export const head: DocumentHead = { title: titulo("Campañas") };
