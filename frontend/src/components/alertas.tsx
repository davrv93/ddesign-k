// Aviso de «clienta caliente» en todo el panel. Cuando el robot detecta una clienta con evento cercano, ganas de comprar,
// una cita o prisa, el backend crea una alerta (GET /api/alertas) y avisa por SSE con el tema «alertas». Aquí se abre un
// modal con lo que el robot descubrió de ella. «Después» lo cierra solo en esta pestaña (sessionStorage); «Ya la atiendo»
// la marca atendida para todo el equipo. Sin sonido, a propósito. El ajuste `alerta_caliente_panel` = "false" lo apaga.
import { $, component$, useSignal, useVisibleTask$, type QRL, type Signal } from "@builder.io/qwik";
import { api, listarAlertas, marcarAlertaAtendida, type Alerta } from "~/lib/api";
import { BASE, u } from "~/lib/base";
import { enDias, telefonoLegible, timeAgo } from "~/lib/format";

// Una clave por panel: cada empresa de /jmdventas/<empresa>/ comparte el origen.
const CLAVE_DESPUES = `kd_alertas_despues:${BASE || "/"}`;

const leerDespues = (): number[] => {
  try {
    const v = JSON.parse(sessionStorage.getItem(CLAVE_DESPUES) || "[]");
    return Array.isArray(v) ? v.filter((n) => typeof n === "number") : [];
  } catch {
    return [];
  }
};

const guardarDespues = (ids: number[]) => {
  try {
    sessionStorage.setItem(CLAVE_DESPUES, JSON.stringify(ids.slice(-200)));
  } catch {
    /* modo privado: el aviso volverá a salir al recargar */
  }
};

// Las que puede mostrar el modal: todas las pendientes si se abrió desde el contador; si no, sin las de «después».
const visiblesDe = (pend: Alerta[], desp: number[], todas: boolean) => (todas ? pend : pend.filter((a) => !desp.includes(a.id)));

/** Los datos del resumen que se conocen, en el orden en que los lee la asesora. */
export function hechosDeAlerta(a: Alerta): { label: string; valor: string }[] {
  const r = a.resumen;
  if (!r) return [];
  const fecha = [r.fecha, enDias(r.dias)].filter(Boolean).join(" · ");
  const prenda = [r.prenda, r.prenda_nombre].filter(Boolean).join(" · ");
  return [
    { label: "Evento", valor: r.evento },
    { label: "Fecha", valor: fecha },
    { label: "Horario", valor: r.horario },
    { label: "Talla", valor: r.talla },
    { label: "Color", valor: r.color },
    { label: "Estilo", valor: r.estilo },
    { label: "Presupuesto", valor: r.presupuesto },
    { label: "Dudas", valor: (r.dudas ?? []).join(", ") },
    { label: "Prenda", valor: prenda },
  ].filter((h) => h.valor);
}

interface Props {
  /** Alertas pendientes (para el contador del menú). */
  cuenta: Signal<number>;
  /** Cada vez que sube, el modal se abre con todas las pendientes, también las dejadas para «después». */
  abrir: Signal<number>;
}

export const AlertasCalientes = component$<Props>(({ cuenta, abrir }) => {
  const pendientes = useSignal<Alerta[]>([]);
  const despues = useSignal<number[]>([]);
  const activo = useSignal(true); // ajuste alerta_caliente_panel
  const forzado = useSignal(false); // abierto desde el contador: muestra también las de «después»
  const abierto = useSignal(false);
  const actualId = useSignal<number | null>(null);
  const ocupado = useSignal(false);
  const error = useSignal("");


  const cargar = $(async () => {
    try {
      const lista = await listarAlertas("pendiente");
      // La más nueva primero.
      pendientes.value = [...lista].sort((a, b) => b.creado_en.localeCompare(a.creado_en));
      cuenta.value = pendientes.value.length;
    } catch {
      return; /* sin conexión: se reintenta con el siguiente aviso */
    }
    if (pendientes.value.length === 0) {
      abierto.value = false;
      return;
    }
    try {
      const s = await api<Record<string, string>>("/api/settings");
      activo.value = s.alerta_caliente_panel !== "false";
    } catch {
      /* se queda con el último valor */
    }
    const vis = visiblesDe(pendientes.value, despues.value, forzado.value);
    if (vis.length === 0) {
      abierto.value = false;
      return;
    }
    // Si llega una nueva mientras se lee otra, se queda en la que estaba; el contador «+N más» la anuncia.
    if (!abierto.value || !vis.some((a) => a.id === actualId.value)) actualId.value = vis[0].id;
    if (activo.value || forzado.value) abierto.value = true;
  });

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(({ cleanup }) => {
    despues.value = leerDespues();
    cargar();
    const onChange = (e: Event) => {
      const topic = (e as CustomEvent<string>).detail;
      if (topic === "alertas") cargar();
    };
    window.addEventListener("kd-change", onChange);
    // Respaldo por si el SSE se cortó un rato.
    const iv = setInterval(cargar, 60000);
    cleanup(() => {
      window.removeEventListener("kd-change", onChange);
      clearInterval(iv);
    });
  });

  // Abrir desde el contador del menú o de la barra superior.
  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(({ track }) => {
    const n = track(() => abrir.value);
    if (n > 0) {
      forzado.value = true;
      actualId.value = null;
      cargar();
    }
  });

  const cerrar = $(() => {
    abierto.value = false;
    forzado.value = false;
    error.value = "";
  });

  // «Después» (o Escape): estas alertas no vuelven a abrir el modal en esta pestaña. Siguen en el contador.
  const dejarParaDespues = $(() => {
    const ids = new Set([...despues.value, ...visiblesDe(pendientes.value, despues.value, forzado.value).map((a) => a.id)]);
    despues.value = [...ids];
    guardarDespues(despues.value);
    cerrar();
  });

  const atender = $(async (a: Alerta) => {
    ocupado.value = true;
    error.value = "";
    try {
      await marcarAlertaAtendida(a.id);
      pendientes.value = pendientes.value.filter((x) => x.id !== a.id);
      cuenta.value = pendientes.value.length;
      const resto = visiblesDe(pendientes.value, despues.value, forzado.value);
      if (resto.length === 0) cerrar();
      else actualId.value = resto[0].id;
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      ocupado.value = false;
    }
  });

  const abrirConversacion = $(async (a: Alerta) => {
    // Mirarla no es atenderla: queda pendiente, pero no vuelve a saltar en esta pestaña. Las otras pendientes de ahora
    // tampoco: tapaban la conversación recién abierta (prueba local, 15-10-2026). Siguen en el contador «🔥 N» y una
    // alerta NUEVA sí vuelve a abrir la ventana.
    despues.value = [...new Set([...despues.value, a.id, ...pendientes.value.map((x) => x.id)])];
    guardarDespues(despues.value);
    cerrar();
    const enConversaciones = location.pathname.slice(BASE.length).startsWith("/conversaciones");
    if (enConversaciones) {
      // Ya estamos en la página: la abre ella misma (navegar a la misma ruta no vuelve a leer ?c=).
      window.dispatchEvent(new CustomEvent("kd-abrir-conversacion", { detail: a.conversation_id }));
    } else {
      // Carga completa: la navegación SPA de Qwik perdía el «?c=» (prueba local, 15-10-2026), como hacen pedidos y clientes.
      location.assign(u(`/conversaciones/?c=${a.conversation_id}`));
    }
  });

  const lista = visiblesDe(pendientes.value, despues.value, forzado.value);
  const idx = Math.max(
    0,
    lista.findIndex((a) => a.id === actualId.value),
  );
  const alerta = lista[idx];
  if (!abierto.value || !alerta) return null;

  return (
    <ModalCaliente
      // Sin key, el «1 de N» quedaba con el total viejo tras «Ya la atiendo» (los props calculados no se refrescaban).
      key={`${alerta.id}-${idx}-${lista.length}`}
      alerta={alerta}
      indice={idx}
      total={lista.length}
      ocupado={ocupado.value}
      error={error.value}
      onIr$={(i: number) => (actualId.value = lista[(i + lista.length) % lista.length].id)}
      onAbrir$={abrirConversacion}
      onAtender$={atender}
      onDespues$={dejarParaDespues}
    />
  );
});

interface ModalProps {
  alerta: Alerta;
  indice: number;
  total: number;
  ocupado: boolean;
  error: string;
  onIr$: QRL<(i: number) => void>;
  onAbrir$: QRL<(a: Alerta) => void>;
  onAtender$: QRL<(a: Alerta) => void>;
  onDespues$: QRL<() => void>;
}

const ModalCaliente = component$<ModalProps>((p) => {
  const caja = useSignal<HTMLElement>();
  const principal = useSignal<HTMLButtonElement>();

  // Foco dentro del modal mientras está abierto (Tab y Shift+Tab dan la vuelta) y Escape = «Después».
  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(({ cleanup }) => {
    const antes = document.activeElement as HTMLElement | null;
    principal.value?.focus();
    const tecla = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        p.onDespues$();
        return;
      }
      if (e.key !== "Tab" || !caja.value) return;
      const focos = [...caja.value.querySelectorAll<HTMLElement>("a[href], button:not([disabled])")];
      if (focos.length === 0) return;
      const primero = focos[0];
      const ultimo = focos[focos.length - 1];
      const dentro = caja.value.contains(document.activeElement);
      if (e.shiftKey && (document.activeElement === primero || !dentro)) {
        e.preventDefault();
        ultimo.focus();
      } else if (!e.shiftKey && (document.activeElement === ultimo || !dentro)) {
        e.preventDefault();
        primero.focus();
      }
    };
    window.addEventListener("keydown", tecla);
    cleanup(() => {
      window.removeEventListener("keydown", tecla);
      antes?.focus?.();
    });
  });

  const a = p.alerta;
  const hechos = hechosDeAlerta(a);
  const mas = p.total - 1;

  return (
    <div class="overlay overlay-center" onClick$={() => !p.ocupado && p.onDespues$()}>
      <div
        ref={caja}
        class="modal modal-caliente"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="caliente-titulo"
        aria-describedby="caliente-motivo"
        onClick$={(e) => e.stopPropagation()}
      >
        <div class="caliente-top">
          <h2 id="caliente-titulo">🔥 Clienta caliente</h2>
          {mas > 0 && (
            <div class="caliente-nav" role="group" aria-label="Otras clientas calientes">
              {p.indice > 0 && (
                <button type="button" class="btn btn-sm btn-ghost" onClick$={() => p.onIr$(p.indice - 1)} aria-label="Anterior">
                  ‹
                </button>
              )}
              <span class="muted small">
                {p.indice + 1} de {p.total}
              </span>
              <button type="button" class="btn btn-sm" onClick$={() => p.onIr$(p.indice + 1)} title="Ver la siguiente">
                {p.indice === 0 ? `+${mas} más ›` : "›"}
              </button>
            </div>
          )}
        </div>

        <div class="caliente-quien">
          <span class="avatar">{(a.nombre || "?").replace("+", "").slice(0, 1).toUpperCase()}</span>
          <div class="grow">
            <strong>{a.nombre || "Clienta sin nombre"}</strong>
            <div class="muted small">
              {a.telefono && (
                <a href={`https://wa.me/${a.telefono.replace(/\D/g, "")}`} target="_blank" rel="noreferrer">
                  {telefonoLegible(a.telefono)}
                </a>
              )}
              {a.telefono && " · "}
              {timeAgo(a.creado_en)}
              {a.whatsapp_enviado && " · aviso enviado por WhatsApp"}
            </div>
          </div>
        </div>

        <p id="caliente-motivo" class="caliente-motivo">
          {a.motivo || "El robot la marcó como caliente."}
        </p>

        {hechos.length > 0 ? (
          <dl class="caliente-hechos">
            {hechos.map((h) => (
              <div key={h.label}>
                <dt>{h.label}</dt>
                <dd>{h.valor}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p class="muted small">El robot aún no tiene más datos de ella.</p>
        )}

        {p.error && <p class="form-error small">{p.error}</p>}

        <div class="modal-actions caliente-acciones">
          <button type="button" class="btn btn-ghost push-left" disabled={p.ocupado} onClick$={p.onDespues$}>
            Después
          </button>
          <button type="button" class="btn" disabled={p.ocupado} onClick$={() => p.onAtender$(a)}>
            {p.ocupado ? "Un momento…" : "Ya la atiendo"}
          </button>
          <button type="button" ref={principal} class="btn btn-primary" disabled={p.ocupado} onClick$={() => p.onAbrir$(a)}>
            Abrir conversación
          </button>
        </div>
      </div>
    </div>
  );
});
