import { $, component$, useOnWindow, useSignal, useVisibleTask$ } from "@builder.io/qwik";
import type { DocumentHead } from "@builder.io/qwik-city";
import { titulo } from "~/lib/marca";
import { api } from "~/lib/api";

interface Status {
  available: boolean;
  connected?: boolean;
  logged_in?: boolean;
  name?: string;
  instance?: string;
  error?: string;
  // false = WhatsApp apagado a propósito para esta empresa (JMD Ventas, mientras el bot siga en su panel anterior).
  habilitado?: boolean;
  empresa?: string;
}

interface QR {
  qrcode?: string;
  code?: string;
  pending?: boolean;
  error?: string;
  passkeyStage?: string;
  passkeyOpenUrl?: string;
  passkeyCode?: string;
}

export default component$(() => {
  const status = useSignal<Status | null>(null);
  const qr = useSignal<QR | null>(null);
  const linking = useSignal(false);
  const phone = useSignal("51");
  const pairCode = useSignal("");
  const error = useSignal("");
  const busy = useSignal(false);

  const loadStatus = $(async () => {
    status.value = await api<Status>("/api/whatsapp/status");
    if (status.value.logged_in) {
      linking.value = false;
      qr.value = null;
      pairCode.value = "";
    }
  });

  // Mientras se vincula, el QR rota cada ~20 s: se consulta cada 3 s hasta que haya sesión.
  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(({ track, cleanup }) => {
    track(() => linking.value);
    loadStatus();
    if (!linking.value) return;
    const tick = async () => {
      await loadStatus();
      if (linking.value && !pairCode.value) qr.value = await api<QR>("/api/whatsapp/qr");
    };
    tick();
    const iv = setInterval(tick, 3000);
    const stop = setTimeout(() => (linking.value = false), 3 * 60 * 1000);
    cleanup(() => {
      clearInterval(iv);
      clearTimeout(stop);
    });
  });

  useOnWindow(
    "kd-change",
    $((e: Event) => {
      if ((e as CustomEvent<string>).detail === "whatsapp") loadStatus();
    }),
  );

  const startQR = $(async () => {
    busy.value = true;
    error.value = "";
    pairCode.value = "";
    try {
      await api("/api/whatsapp/connect", { method: "POST" });
      linking.value = true;
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      busy.value = false;
    }
  });

  const startPair = $(async () => {
    busy.value = true;
    error.value = "";
    try {
      const r = await api<{ code: string }>("/api/whatsapp/pair", { method: "POST", json: { phone: phone.value } });
      pairCode.value = r.code;
      qr.value = null;
      linking.value = true;
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      busy.value = false;
    }
  });

  const logout = $(async () => {
    if (!confirm("¿Desvincular este WhatsApp? El bot dejará de responder hasta que vuelvas a escanear el QR.")) return;
    busy.value = true;
    try {
      await api("/api/whatsapp/logout", { method: "POST" });
      await loadStatus();
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      busy.value = false;
    }
  });

  const st = status.value;

  return (
    <div class="page narrow">
      <div class="page-head">
        <div>
          <h1>WhatsApp</h1>
          <p class="muted">Vincula el celular de la tienda para que el bot atienda los mensajes.</p>
        </div>
      </div>

      <section class="panel">
        {st == null ? (
          <p class="muted">Consultando estado…</p>
        ) : !st.available && st.habilitado === false ? (
          <div class="state state-off">
            <span class="big-dot" />
            <div>
              <strong>WhatsApp todavía no está conectado a este panel</strong>
              <p class="small">
                Si {st.empresa || "tu tienda"} ya atiende con un bot de WhatsApp, sigue funcionando como hasta ahora desde su panel actual. Te
                avisaremos cuando lo traslademos aquí; entonces podrás vincular el número y ver las conversaciones en este panel.
              </p>
            </div>
          </div>
        ) : !st.available ? (
          <div class="state state-off">
            <span class="big-dot" />
            <div>
              <strong>WhatsApp no responde en este momento</strong>
              <p class="small">Vuelve a intentarlo en unos minutos. Si sigue igual, avisa al equipo de soporte.</p>
            </div>
          </div>
        ) : st.logged_in ? (
          <div class="state state-ok">
            <span class="big-dot" />
            <div class="grow">
              <strong>Conectado{st.name ? ` como ${st.name}` : ""}</strong>
              <p class="muted small">
                Instancia <code>{st.instance}</code> · {st.connected ? "en línea" : "reconectando…"}
              </p>
            </div>
            <button class="btn btn-danger btn-sm" disabled={busy.value} onClick$={logout}>
              Desvincular
            </button>
          </div>
        ) : (
          <div class="state state-off">
            <span class="big-dot" />
            <div>
              <strong>Sin vincular</strong>
              <p class="muted small">Escanea el QR con el WhatsApp de la tienda.</p>
            </div>
          </div>
        )}
      </section>

      {st?.available && !st.logged_in && (
        <div class="link-grid">
          <section class="panel">
            <h2>Escanear código QR</h2>
            <ol class="steps">
              <li>Abre WhatsApp en el celular de la tienda.</li>
              <li>
                Toca <b>⋮ Más opciones</b> → <b>Dispositivos vinculados</b> → <b>Vincular un dispositivo</b>.
              </li>
              <li>Apunta la cámara a este código.</li>
            </ol>
            <div class="qr-box">
              {qr.value?.qrcode ? (
                <img src={qr.value.qrcode} alt="Código QR de WhatsApp" width={264} height={264} />
              ) : qr.value?.passkeyStage ? (
                <div class="small">
                  <p>
                    Esta cuenta pide una <b>passkey</b> para vincular. Abre WhatsApp Web y sigue las instrucciones.
                  </p>
                  {qr.value.passkeyOpenUrl && (
                    <a class="btn btn-primary" href={qr.value.passkeyOpenUrl} target="_blank" rel="noreferrer">
                      Abrir WhatsApp Web
                    </a>
                  )}
                  {qr.value.passkeyCode && <p class="pair-code">{qr.value.passkeyCode}</p>}
                </div>
              ) : linking.value ? (
                <span class="muted">Generando QR…</span>
              ) : (
                <button class="btn btn-primary" disabled={busy.value} onClick$={startQR}>
                  Generar QR
                </button>
              )}
            </div>
            {linking.value && !pairCode.value && <p class="muted small center">El QR se renueva solo. Esta página detecta la vinculación automáticamente.</p>}
          </section>

          <section class="panel">
            <h2>O vincular con número</h2>
            <p class="muted small">Si no puedes escanear, recibe un código de 8 caracteres.</p>
            <form class="row" preventdefault:submit onSubmit$={startPair}>
              <input bind:value={phone} inputMode="tel" placeholder="51987654321" />
              <button class="btn" disabled={busy.value}>
                Pedir código
              </button>
            </form>
            {pairCode.value && (
              <>
                <p class="pair-code">{pairCode.value}</p>
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
          </section>
        </div>
      )}

      {error.value && <p class="form-error">{error.value}</p>}

      <section class="panel muted small">
        <h2>¿Qué hace el bot?</h2>
        <ul class="bullets">
          <li>Saluda y muestra el menú: catálogo, consulta por foto, estado del pedido y asesora.</li>
          <li>Cuando un cliente envía una foto, la IA (Gemini Flash-Lite, con Gemma de respaldo) la compara con tus productos y revisa el stock por talla.</li>
          <li>Pide talla, muestra el resumen y, si confirma con <b>SI</b>, reserva el stock y le pide su ubicación.</li>
          <li>Si respondes desde el panel o desde el celular, el bot se pausa en ese chat.</li>
        </ul>
      </section>
    </div>
  );
});

export const head: DocumentHead = { title: titulo("WhatsApp") };
