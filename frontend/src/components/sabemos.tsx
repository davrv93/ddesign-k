// «Lo que sabemos» de la clienta en una conversación (GET /api/conversations/{id}/ficha): la temperatura y los ocho
// datos que la tienda quiere que el robot descubra. Lo que falta sale con «—», para que la asesora vea qué preguntar.
// En el teléfono va plegada por defecto (ocupa el alto del chat); en escritorio, abierta.
import { component$, useSignal, useVisibleTask$ } from "@builder.io/qwik";
import type { FichaConversacion } from "~/lib/api";
import { enDias, etapaInfo } from "~/lib/format";

const TEMPERATURA: Record<string, { label: string; clase: string }> = {
  caliente: { label: "🔥 Caliente", clase: "caliente" },
  tibio: { label: "🌤 Tibia", clase: "tibia" },
  frio: { label: "❄️ Fría", clase: "fria" },
};

const unir = (...v: (string | null | undefined)[]) => v.filter((x) => x && x.trim()).join(" · ");

export const LoQueSabemos = component$<{ ficha: FichaConversacion }>(({ ficha }) => {
  const abierta = useSignal(true);

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(() => {
    try {
      abierta.value = !window.matchMedia("(max-width: 760px)").matches;
    } catch {
      /* navegador viejo: abierta */
    }
  });

  const sb = ficha.sabemos;
  const dias = ficha.dias_al_evento;
  const datos: { label: string; valor: string }[] = [
    { label: "Tipo de evento", valor: unir(sb?.ocasion, sb?.para_quien && `para ${sb.para_quien}`) },
    { label: "Fecha del evento", valor: unir(sb?.fecha, enDias(dias), sb?.horario) },
    { label: "Talla", valor: unir(sb?.talla, sb?.estatura && `mide ${sb.estatura}`) },
    { label: "Color de preferencia", valor: sb?.color ?? "" },
    { label: "Estilo", valor: sb?.estilo ?? "" },
    { label: "Presupuesto", valor: sb?.presupuesto ?? "" },
    { label: "Temores o dudas", valor: (ficha.objeciones ?? []).join(", ") },
    { label: "Tiempo disponible", valor: dias == null ? "" : dias < 0 ? "el evento ya pasó" : dias === 0 ? "hoy mismo" : dias === 1 ? "1 día" : `${dias} días` },
  ];
  const conocidos = datos.filter((d) => d.valor).length;
  const temp = TEMPERATURA[ficha.temperatura] ?? null;
  const etapa = ficha.etapa ? etapaInfo(ficha.etapa) : null;
  const envio = unir(sb?.envio, sb?.ciudad);

  return (
    <details class="sabemos" open={abierta.value} onToggle$={(_, el) => (abierta.value = el.open)}>
      <summary>
        <strong>Lo que sabemos</strong>
        {temp && (
          <span class={["temp", temp.clase]} title={ficha.temperatura_motivo || undefined}>
            {temp.label}
          </span>
        )}
        {etapa && etapa.key && (
          <span class="pill" style={{ "--col": etapa.color }}>
            {etapa.label}
          </span>
        )}
        <span class="muted small sabemos-cuenta">
          {conocidos} de {datos.length} datos
        </span>
      </summary>
      {ficha.temperatura_motivo && <p class="muted small sabemos-motivo">{ficha.temperatura_motivo}</p>}
      <dl class="sabemos-datos">
        {datos.map((d) => (
          <div key={d.label} class={!d.valor ? "falta" : undefined}>
            <dt>{d.label}</dt>
            <dd>{d.valor || "—"}</dd>
          </div>
        ))}
        {ficha.producto?.codigo && (
          <div>
            <dt>Prenda en foco</dt>
            <dd>{unir(ficha.producto.codigo, ficha.producto.nombre)}</dd>
          </div>
        )}
        {ficha.pedido?.codigo && (
          <div>
            <dt>Pedido</dt>
            <dd>
              {unir(ficha.pedido.codigo, ficha.pedido.talla && `talla ${ficha.pedido.talla}`)}
              {ficha.pedido.confirmado ? <span class="chip ok">confirmado</span> : <span class="chip warn">por confirmar</span>}
            </dd>
          </div>
        )}
        {envio && (
          <div>
            <dt>Envío</dt>
            <dd>{envio}</dd>
          </div>
        )}
      </dl>
    </details>
  );
});
