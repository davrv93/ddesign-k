// «Probar» del editor de flujos: simular aquí (no envía nada) o enviarlo de verdad a un número de WhatsApp con las
// esperas reducidas. Contrato: docs/SEGUIMIENTO_CONTRATO.md §4b.
import { $, component$, useSignal, useVisibleTask$, type QRL } from "@builder.io/qwik";
import type { Product } from "~/lib/api";
import { esperaTexto } from "~/lib/format";
import { u } from "~/lib/base";
import { NODOS, probarFlujo, simularFlujo, type Grafo, type PasoSimulado } from "~/lib/flujos";

interface Props {
  grafo: Grafo;
  nombre: string;
  secuenciaId: number; // 0 = aún no guardada
  sinGuardar: boolean;
  admin: boolean;
  productos: Product[];
  onCerrar$: QRL<() => void>;
  onRecorrido$: QRL<(ids: string[]) => void>;
}

const CLAVE_TEL = "jmd:telefono-prueba";

export const FlujoProbar = component$<Props>((p) => {
  const pestana = useSignal<"simular" | "enviar">("simular");
  const producto = useSignal("");
  const responde = useSignal(false);
  const temperatura = useSignal("tibia");
  const busy = useSignal(false);
  const error = useSignal("");
  const pasos = useSignal<PasoSimulado[] | null>(null);
  const fin = useSignal("");
  const telefono = useSignal("");
  const escala = useSignal(1);
  const confirmar = useSignal(false);
  const enviada = useSignal<{ conversation_id: number; telefono: string } | null>(null);

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(() => {
    try {
      telefono.value = localStorage.getItem(CLAVE_TEL) ?? "";
    } catch {
      /* sin almacenamiento */
    }
    if (!producto.value) producto.value = p.productos.find((x) => x.active)?.code ?? "";
  });

  const simular = $(async () => {
    busy.value = true;
    error.value = "";
    try {
      const r = await simularFlujo({ grafo: p.grafo, nombre: p.nombre, producto: producto.value, responde: responde.value, temperatura: temperatura.value });
      pasos.value = r.pasos;
      fin.value = r.fin;
      await p.onRecorrido$(r.pasos.map((x) => x.nodo).concat(p.grafo.nodos.filter((n) => n.tipo === "inicio").map((n) => n.id)));
    } catch (e) {
      error.value = (e as Error).message;
      pasos.value = null;
    } finally {
      busy.value = false;
    }
  });

  const enviar = $(async () => {
    confirmar.value = false;
    busy.value = true;
    error.value = "";
    try {
      try {
        localStorage.setItem(CLAVE_TEL, telefono.value);
      } catch {
        /* sin almacenamiento */
      }
      enviada.value = await probarFlujo(p.secuenciaId, { telefono: telefono.value, producto: producto.value, escala_min: escala.value });
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      busy.value = false;
    }
  });

  const mensajes = (pasos.value ?? []).filter((x) => x.tipo === "mensaje" && !x.omitido).length;

  return (
    <div class="overlay overlay-center fl-probar-capa" onClick$={p.onCerrar$}>
      <div class="modal modal-lg fl-probar" role="dialog" aria-modal="true" aria-labelledby="fl-probar-t" onClick$={(e) => e.stopPropagation()}>
        <header class="fl-probar-head">
          <h2 id="fl-probar-t">Probar «{p.nombre || "flujo"}»</h2>
          <button class="btn btn-ghost" aria-label="Cerrar" onClick$={p.onCerrar$}>
            ✕
          </button>
        </header>
        <div class="tabs" role="tablist">
          <button role="tab" aria-selected={pestana.value === "simular"} class={["tab", pestana.value === "simular" && "active"]} onClick$={() => (pestana.value = "simular")}>
            Simular aquí
          </button>
          <button role="tab" aria-selected={pestana.value === "enviar"} class={["tab", pestana.value === "enviar" && "active"]} onClick$={() => (pestana.value = "enviar")}>
            Enviar a WhatsApp
          </button>
        </div>

        <div class="grid2 fl-probar-campos">
          <label>
            Modelo que vio la clienta
            <select value={producto.value} onChange$={(_, el) => (producto.value = el.value)}>
              <option value="">Sin modelo</option>
              {p.productos
                .filter((x) => x.active)
                .map((x) => (
                  <option key={x.id} value={x.code}>
                    {`${x.code} · ${x.name}`}
                  </option>
                ))}
            </select>
          </label>
          {pestana.value === "simular" ? (
            <>
              <label class="check">
                <input type="checkbox" checked={responde.value} onChange$={(_, el) => (responde.value = el.checked)} />
                La clienta responde (para «¿respondió?»)
              </label>
              <label>
                Temperatura de la clienta
                <select value={temperatura.value} onChange$={(_, el) => (temperatura.value = el.value)}>
                  <option value="caliente">caliente</option>
                  <option value="tibia">tibia</option>
                  <option value="fria">fría</option>
                </select>
              </label>
            </>
          ) : (
            <>
              <label>
                Tu número de WhatsApp
                <input type="tel" value={telefono.value} placeholder="51 987 654 321" onInput$={(_, el) => (telefono.value = el.value)} />
              </label>
              <label>
                Esperas en la prueba
                <select value={String(escala.value)} onChange$={(_, el) => (escala.value = Number(el.value))}>
                  <option value="1">1 minuto cada una</option>
                  <option value="5">5 minutos cada una</option>
                  <option value="0">Las reales del flujo</option>
                </select>
              </label>
            </>
          )}
        </div>

        {pestana.value === "simular" ? (
          <>
            <div class="row">
              <button class="btn btn-primary" disabled={busy.value} onClick$={simular}>
                {busy.value ? "Simulando…" : "Simular"}
              </button>
              <span class="muted small">No envía nada. Usa lo que tienes en el lienzo, aunque no esté guardado.</span>
            </div>
            {pasos.value && (
              <div class="fl-chat" aria-live="polite">
                {pasos.value.map((x, i) =>
                  x.tipo === "mensaje" && !x.omitido ? (
                    <div key={`b-${i}-${x.nodo}`} class="fl-burbuja">
                      <span class="fl-hora">{x.en_min ? `+${esperaTexto(x.en_min)}` : "al inicio"}</span>
                      {x.media_url && <img src={x.media_url.startsWith("http") ? x.media_url : u(x.media_url)} alt="" width={160} height={160} />}
                      <p>{x.texto}</p>
                    </div>
                  ) : (
                    <div key={`e-${i}-${x.nodo}`} class={["fl-evento", x.omitido && "omitido"]}>
                      <span aria-hidden="true">{NODOS[x.tipo]?.icono}</span> {NODOS[x.tipo]?.label}
                      {x.detalle && <>: {x.detalle}</>}
                      {x.omitido && <> — no sale: {x.motivo}</>}
                    </div>
                  ),
                )}
                <p class="muted small fl-fin">
                  {mensajes} {mensajes === 1 ? "mensaje" : "mensajes"} · {fin.value}
                </p>
              </div>
            )}
          </>
        ) : !p.admin ? (
          <p class="muted">Solo una admin puede enviar pruebas.</p>
        ) : p.secuenciaId === 0 || p.sinGuardar ? (
          <p class="chip warn">Guarda el flujo antes de enviarlo: la prueba usa la versión guardada.</p>
        ) : enviada.value ? (
          <div class="fl-ok">
            <p>
              ✅ Prueba en marcha hacia <b>+{enviada.value.telefono}</b>. El primer mensaje sale ahora; los siguientes, con {escala.value ? `${escala.value} min` : "las esperas reales"} entre
              cada uno. Si respondes, el flujo se pausa como con una clienta.
            </p>
            <a class="btn btn-sm" href={u(`/conversaciones?c=${enviada.value.conversation_id}`)}>
              Ver la conversación
            </a>
          </div>
        ) : confirmar.value ? (
          <div class="fl-confirma" role="alertdialog" aria-labelledby="fl-conf-t">
            <p id="fl-conf-t">
              <b>Se enviarán mensajes de WhatsApp de verdad a +{telefono.value.replace(/\D/g, "")}.</b> Corre aunque el flujo esté apagado y fuera de horario. ¿Seguro?
            </p>
            <div class="row">
              <button class="btn btn-primary" disabled={busy.value} onClick$={enviar}>
                Sí, enviar la prueba
              </button>
              <button class="btn btn-ghost" onClick$={() => (confirmar.value = false)}>
                Cancelar
              </button>
            </div>
          </div>
        ) : (
          <div class="row">
            <button class="btn btn-primary" disabled={busy.value || telefono.value.replace(/\D/g, "").length < 9} onClick$={() => (confirmar.value = true)}>
              Enviar prueba
            </button>
            <span class="muted small">Escribe tu propio número: la prueba manda los mensajes del flujo a ese WhatsApp.</span>
          </div>
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
