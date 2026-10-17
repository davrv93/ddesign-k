// Números de WhatsApp de la tienda (docs/SEGUIMIENTO_CONTRATO.md §5): el principal (del .env) y los que se añaden para
// recibir, para sacar el seguimiento o para las dos cosas. Cada uno tiene un tope de mensajes iniciados al día.
// Admin: añade, edita, borra y conecta (QR o código). El resto del equipo solo mira.
import { $, component$, useOnWindow, useSignal, useStore, useVisibleTask$, type QRL } from "@builder.io/qwik";
import {
  borrarNumero,
  crearNumero,
  guardarNumero,
  listarNumeros,
  miSesion,
  qrNumero,
  vincularNumero,
  type NumeroEditable,
  type NumeroWhatsApp,
} from "~/lib/api";
import { telefonoLegible } from "~/lib/format";
import { Confirmar } from "~/components/confirmar";

export const CANALES: Record<string, string> = {
  evolution: "WhatsApp por QR",
  cloud: "API oficial de Meta",
};

export const USOS: Record<string, { label: string; hint: string }> = {
  entrada: { label: "Recibir", hint: "Atiende a las clientas que escriben" },
  salida: { label: "Seguimiento", hint: "Solo saca mensajes de seguimiento" },
  ambos: { label: "Recibir y seguimiento", hint: "Atiende y también saca seguimiento" },
};

const TOPE_SUGERIDO = 25;

export const NumerosWhatsApp = component$(() => {
  const lista = useSignal<NumeroWhatsApp[]>([]);
  const cargado = useSignal(false);
  const admin = useSignal(false);
  const error = useSignal("");
  const editando = useSignal<NumeroWhatsApp | "nuevo" | null>(null);
  const borrando = useSignal<NumeroWhatsApp | null>(null);
  const conectando = useSignal<NumeroWhatsApp | null>(null);

  const load = $(async () => {
    try {
      lista.value = await listarNumeros();
      error.value = "";
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      cargado.value = true;
    }
  });

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(() => {
    miSesion()
      .then((s) => (admin.value = s.admin))
      .catch(() => {});
    load();
  });

  useOnWindow(
    "kd-change",
    $((e: Event) => {
      const t = (e as CustomEvent<string>).detail;
      if (t === "whatsapp" || t === "numeros" || t === "seguimiento") load();
    }),
  );

  return (
    <section class="panel">
      <div class="panel-head">
        <h2>Números de WhatsApp</h2>
        {admin.value && (
          <button class="btn btn-sm btn-primary" onClick$={() => (editando.value = "nuevo")}>
            + Número
          </button>
        )}
      </div>
      <p class="muted small">
        Puedes sumar números para recibir clientas o para sacar los mensajes de seguimiento sin cargar el número principal. Pasar de
        ~25 mensajes iniciados al día por número puede hacer que WhatsApp lo restrinja: lo que pase del tope espera al día
        siguiente.
      </p>
      {error.value && <p class="form-error small">{error.value}</p>}
      {cargado.value && lista.value.length === 0 && !error.value && <p class="muted small">Todavía no hay números configurados.</p>}
      <ul class="numeros">
        {lista.value.map((n) => {
          const pct = n.tope_diario > 0 ? Math.min(100, Math.round((n.enviados_hoy / n.tope_diario) * 100)) : 0;
          return (
            <li key={`${n.id}-${n.numero}`} class="numero">
              <span class={["numero-dot", n.conectado ? "ok" : "off"]} title={n.conectado ? "Conectado" : "Sin conectar"} />
              <div class="grow">
                <strong>{n.nombre || telefonoLegible(n.numero) || "Sin nombre"}</strong>
                {n.principal && <span class="chip">Principal</span>}
                <span class="muted small block">
                  {telefonoLegible(n.numero) || "sin número"} · {CANALES[n.canal] ?? n.canal} · {USOS[n.uso]?.label ?? n.uso} ·{" "}
                  {n.conectado ? "conectado" : "sin conectar"}
                </span>
                <div class="numero-tope" title={`${n.enviados_hoy} de ${n.tope_diario} mensajes iniciados hoy`}>
                  <span class="numero-barra">
                    <span class={pct >= 100 ? "lleno" : pct >= 80 ? "casi" : ""} style={{ width: `${Math.max(pct, 2)}%` }} />
                  </span>
                  <span class="muted small">
                    Hoy {n.enviados_hoy} de {n.tope_diario || "—"}
                  </span>
                </div>
              </div>
              {admin.value && (
                <div class="numero-acc">
                  {n.canal === "evolution" && !n.principal && !n.conectado && (
                    <button class="btn btn-sm btn-primary" onClick$={() => (conectando.value = n)}>
                      Conectar
                    </button>
                  )}
                  <button class="btn btn-sm btn-ghost" onClick$={() => (editando.value = n)}>
                    {n.principal ? "Tope" : "Editar"}
                  </button>
                  {!n.principal && (
                    <button class="btn btn-sm btn-ghost btn-borrar" onClick$={() => (borrando.value = n)}>
                      Quitar
                    </button>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {editando.value && (
        <NumeroForm
          numero={editando.value === "nuevo" ? null : editando.value}
          onClose$={() => (editando.value = null)}
          onSaved$={async () => {
            editando.value = null;
            await load();
          }}
        />
      )}
      {borrando.value && (
        <Confirmar
          titulo="¿Quitar este número?"
          texto={`${borrando.value.nombre || telefonoLegible(borrando.value.numero)} deja de recibir y de sacar mensajes desde este panel. Las conversaciones que ya tuvo se conservan.`}
          confirmar="Quitar número"
          peligro
          onCancelar$={() => (borrando.value = null)}
          onConfirmar$={async () => {
            await borrarNumero(borrando.value!.id);
            borrando.value = null;
            await load();
          }}
        />
      )}
      {conectando.value && (
        <ConectarNumero
          numero={conectando.value}
          onClose$={async () => {
            conectando.value = null;
            await load();
          }}
        />
      )}
    </section>
  );
});

// ---------------------------------------------------------------------------

const NumeroForm = component$<{ numero: NumeroWhatsApp | null; onClose$: QRL<() => void>; onSaved$: QRL<() => Promise<void>> }>(
  ({ numero, onClose$, onSaved$ }) => {
    const f = useStore<NumeroEditable>({
      nombre: numero?.nombre ?? "",
      canal: numero?.canal ?? "evolution",
      numero: numero?.numero ?? "",
      uso: numero?.uso ?? "salida",
      tope_diario: numero?.tope_diario ?? TOPE_SUGERIDO,
    });
    const busy = useSignal(false);
    const error = useSignal("");
    const soloTope = !!numero?.principal;

    return (
      <div class="overlay overlay-center" onClick$={onClose$}>
        <form
          class="modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="numero-titulo"
          preventdefault:submit
          onClick$={(e) => e.stopPropagation()}
          onSubmit$={async () => {
            error.value = "";
            const tope = Math.round(Number(f.tope_diario));
            if (!Number.isFinite(tope) || tope < 1) {
              error.value = "El tope debe ser al menos 1 mensaje al día.";
              return;
            }
            const tel = f.numero.replace(/\D/g, "");
            if (!soloTope && !/^\d{8,15}$/.test(tel)) {
              error.value = "Escribe el número con código de país, solo dígitos (por ejemplo 51987654321).";
              return;
            }
            busy.value = true;
            try {
              if (soloTope) await guardarNumero(numero!.id, { tope_diario: tope });
              else {
                const body: NumeroEditable = { ...f, numero: tel, nombre: f.nombre.trim(), tope_diario: tope };
                if (numero) await guardarNumero(numero.id, body);
                else await crearNumero(body);
              }
              await onSaved$();
            } catch (e) {
              error.value = (e as Error).message;
            } finally {
              busy.value = false;
            }
          }}
        >
          <h2 id="numero-titulo">{numero ? (soloTope ? "Tope del número principal" : "Editar número") : "Nuevo número"}</h2>
          {soloTope ? (
            <p class="muted small">El número principal se configura en el servidor; aquí solo cambias cuántos mensajes inicia al día.</p>
          ) : (
            <>
              <div class="grid2">
                <label>
                  Nombre
                  <input value={f.nombre} placeholder="Avisos 1" onInput$={(_, el) => (f.nombre = el.value)} />
                </label>
                <label>
                  Número
                  <input
                    type="tel"
                    inputMode="numeric"
                    autoComplete="off"
                    placeholder="51987654321"
                    value={f.numero}
                    required
                    onInput$={(_, el) => (f.numero = el.value)}
                  />
                </label>
                <label>
                  Canal
                  <select value={f.canal} onChange$={(_, el) => (f.canal = el.value as NumeroEditable["canal"])}>
                    {Object.entries(CANALES).map(([k, v]) => (
                      <option key={k} value={k}>
                        {v}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Uso
                  <select value={f.uso} onChange$={(_, el) => (f.uso = el.value as NumeroEditable["uso"])}>
                    {Object.entries(USOS).map(([k, v]) => (
                      <option key={k} value={k}>
                        {v.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <p class="muted small">
                {USOS[f.uso]?.hint}.{" "}
                {f.canal === "cloud"
                  ? "Las credenciales de la API oficial van en la configuración del servidor."
                  : "Después de guardarlo, pulsa «Conectar» para escanear el QR."}
              </p>
            </>
          )}
          <label>
            Tope de mensajes iniciados al día
            <input
              type="number"
              min="1"
              step="1"
              class="num"
              value={f.tope_diario}
              aria-describedby="numero-tope-hint"
              onInput$={(_, el) => (f.tope_diario = Number(el.value))}
            />
          </label>
          <p class="muted small" id="numero-tope-hint">
            Sugerido: {TOPE_SUGERIDO}. Pasar de ~25 al día por número puede hacer que WhatsApp lo restrinja.
          </p>
          {error.value && (
            <p class="form-error" role="alert">
              {error.value}
            </p>
          )}
          <div class="modal-actions">
            <button type="button" class="btn btn-ghost" onClick$={onClose$}>
              Cancelar
            </button>
            <button class="btn btn-primary" disabled={busy.value}>
              {busy.value ? "Guardando…" : "Guardar"}
            </button>
          </div>
        </form>
      </div>
    );
  },
);

// ---------------------------------------------------------------------------
// Conectar un número de WhatsApp normal: QR (se consulta cada 3 s mientras la ventana está abierta, hasta que conecta) o
// código para vincular con el número.

const ConectarNumero = component$<{ numero: NumeroWhatsApp; onClose$: QRL<() => void> }>(({ numero, onClose$ }) => {
  const modo = useSignal<"qr" | "codigo">("qr");
  const qr = useSignal("");
  const conectado = useSignal(false);
  const error = useSignal("");
  const telefono = useSignal(numero.numero || "51");
  const codigo = useSignal("");
  const busy = useSignal(false);

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(({ cleanup }) => {
    let vivo = true;
    const tick = async () => {
      if (!vivo || conectado.value) return;
      try {
        const r = await qrNumero(numero.id);
        if (!vivo) return;
        conectado.value = !!r.conectado;
        if (modo.value === "qr") qr.value = r.qr || "";
        error.value = "";
      } catch (e) {
        if (vivo) error.value = (e as Error).message;
      }
      if (conectado.value) clearInterval(iv);
    };
    tick();
    const iv = setInterval(tick, 3000);
    const tecla = (e: KeyboardEvent) => e.key === "Escape" && onClose$();
    window.addEventListener("keydown", tecla);
    cleanup(() => {
      vivo = false;
      clearInterval(iv);
      window.removeEventListener("keydown", tecla);
    });
  });

  const pedirCodigo = $(async () => {
    const tel = telefono.value.replace(/\D/g, "");
    if (!/^\d{8,15}$/.test(tel)) {
      error.value = "Escribe el número con código de país, solo dígitos.";
      return;
    }
    busy.value = true;
    error.value = "";
    try {
      codigo.value = (await vincularNumero(numero.id, tel)).codigo;
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      busy.value = false;
    }
  });

  return (
    <div class="overlay overlay-center" onClick$={onClose$}>
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="conectar-titulo" onClick$={(e) => e.stopPropagation()}>
        <h2 id="conectar-titulo">Conectar {numero.nombre || telefonoLegible(numero.numero)}</h2>
        {conectado.value ? (
          <div class="state state-ok">
            <span class="big-dot" />
            <div>
              <strong>¡Conectado!</strong>
              <p class="muted small">Ya puede usarse para {USOS[numero.uso]?.label.toLowerCase() ?? "enviar"}.</p>
            </div>
          </div>
        ) : (
          <>
            <div class="seg" role="tablist" aria-label="Forma de conectar">
              <button type="button" role="tab" aria-selected={modo.value === "qr"} class={["seg-btn", modo.value === "qr" && "active"]} onClick$={() => (modo.value = "qr")}>
                Escanear QR
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={modo.value === "codigo"}
                class={["seg-btn", modo.value === "codigo" && "active"]}
                onClick$={() => (modo.value = "codigo")}
              >
                Vincular con número
              </button>
            </div>
            {modo.value === "qr" ? (
              <>
                <ol class="steps small">
                  <li>Abre WhatsApp en el celular de ese número.</li>
                  <li>
                    <b>⋮ Más opciones</b> → <b>Dispositivos vinculados</b> → <b>Vincular un dispositivo</b>.
                  </li>
                  <li>Apunta la cámara a este código.</li>
                </ol>
                <div class="qr-box">
                  {qr.value ? <img src={qr.value} alt="Código QR de WhatsApp" width={264} height={264} /> : <span class="muted">Generando QR…</span>}
                </div>
                <p class="muted small center">El QR se renueva solo. Esta ventana detecta la conexión.</p>
              </>
            ) : (
              <>
                <p class="muted small">Si no puedes escanear, pide un código de 8 caracteres y escríbelo en el celular.</p>
                <form class="row" preventdefault:submit onSubmit$={pedirCodigo}>
                  <input aria-label="Número a vincular" inputMode="tel" placeholder="51987654321" bind:value={telefono} />
                  <button class="btn" disabled={busy.value}>
                    Pedir código
                  </button>
                </form>
                {codigo.value && (
                  <>
                    <p class="pair-code" aria-live="polite">
                      {codigo.value}
                    </p>
                    <ol class="steps small">
                      <li>
                        En WhatsApp: <b>Dispositivos vinculados</b> → <b>Vincular un dispositivo</b>.
                      </li>
                      <li>
                        Toca <b>Vincular con el número de teléfono</b> e ingresa el código.
                      </li>
                    </ol>
                  </>
                )}
              </>
            )}
          </>
        )}
        {error.value && <p class="form-error small">{error.value}</p>}
        <div class="modal-actions">
          <button type="button" class="btn btn-primary" onClick$={onClose$}>
            {conectado.value ? "Listo" : "Cerrar"}
          </button>
        </div>
      </div>
    </div>
  );
});
