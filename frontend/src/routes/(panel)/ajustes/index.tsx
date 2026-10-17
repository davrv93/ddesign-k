import { $, component$, useSignal, useStore, useVisibleTask$, type QRL } from "@builder.io/qwik";
import type { DocumentHead } from "@builder.io/qwik-city";
import { titulo } from "~/lib/marca";
import { u } from "~/lib/base";
import { api, equipo, miSesion, type Miembro, type Sesion } from "~/lib/api";

type Settings = Record<string, string>;

const TOGGLES = [
  { key: "bot_enabled", label: "Bot activo", hint: "Si lo apagas, los mensajes se guardan pero nadie responde automáticamente." },
  { key: "notify_status_changes", label: "Avisar cambios de estado", hint: "Al mover un pedido (preparando, enviado, entregado…) el cliente recibe un mensaje." },
  { key: "pause_on_manual_reply", label: "Pausar el bot cuando respondo desde el celular", hint: "Si escribes al cliente desde el teléfono, el bot deja de responder en ese chat." },
  {
    key: "precios_solo_si_pregunta",
    label: "Precios solo si la clienta los pregunta",
    hint: "El robot no dice precios por su cuenta (ni en el texto ni en las fotos o el catálogo): los dice cuando la clienta pregunta «¿cuánto cuesta?». En el resumen del pedido y el pago, el total va siempre. Las promos se mencionan sin cifras.",
  },
];

export default component$(() => {
  const s = useStore<Settings>({});
  const loaded = useSignal(false);
  const saved = useSignal("");
  const yo = useSignal<Sesion | null>(null);
  const error = useSignal("");

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    miSesion().then((u) => (yo.value = u));
    Object.assign(s, await api<Settings>("/api/settings"));
    loaded.value = true;
  });

  const save = $(async (patch: Settings) => {
    try {
      Object.assign(s, await api<Settings>("/api/settings", { method: "PUT", json: patch }));
      error.value = "";
      saved.value = "Guardado";
      setTimeout(() => (saved.value = ""), 2000);
    } catch (e) {
      error.value = (e as Error).message;
    }
  });
  const soloLectura = !yo.value?.admin;

  return (
    <div class="page narrow">
      <div class="page-head">
        <div>
          <h1>Ajustes</h1>
          <p class="muted">Comportamiento del bot de WhatsApp y equipo de la tienda. {saved.value && <b class="ok-text">{saved.value} ✓</b>}</p>
        </div>
      </div>
      {yo.value && (
        <p class="muted small">
          Entraste como <b>{yo.value.name || yo.value.username}</b> · {yo.value.admin ? "admin" : "asesora"}
          {soloLectura && " (los ajustes y el equipo los cambia una admin)"}
        </p>
      )}
      {error.value && <p class="form-error">{error.value}</p>}
      {loaded.value && (
        <section class="panel">
          {TOGGLES.map((t) => (
            <label key={t.key} class="setting">
              <div class="grow">
                <strong>{t.label}</strong>
                <p class="muted small">{t.hint}</p>
              </div>
              <input
                type="checkbox"
                class="switch"
                checked={s[t.key] === "true"}
                disabled={soloLectura}
                onChange$={(_, el) => save({ [t.key]: el.checked ? "true" : "false" })}
              />
            </label>
          ))}
          <label class="setting">
            <div class="grow">
              <strong>Reactivar el bot tras (horas)</strong>
              <p class="muted small">Un chat atendido por una asesora vuelve al bot pasado este tiempo. 0 = nunca.</p>
            </div>
            <input
              type="number"
              min="0"
              class="num"
              value={s.bot_resume_hours}
              disabled={soloLectura}
              onChange$={(_, el) => save({ bot_resume_hours: String(Math.max(0, Number(el.value) || 0)) })}
            />
          </label>
        </section>
      )}
      {loaded.value && <ClientaCaliente s={s} soloLectura={soloLectura} save={save} />}
      {loaded.value && <BienvenidaAnuncio s={s} soloLectura={soloLectura} save={save} />}
      {loaded.value && <ProspectosFuturo s={s} soloLectura={soloLectura} save={save} />}
      {loaded.value && <HorarioSeguimiento s={s} soloLectura={soloLectura} save={save} />}
      {yo.value && <Colores yo={yo.value} />}
      {yo.value && <Equipo yo={yo.value} />}
    </div>
  );
});

// Clienta caliente: cuando el robot detecta una clienta con evento cercano, ganas de comprar, una cita o prisa, el
// backend avisa por WhatsApp al número de `alerta_caliente_whatsapp` ("" = no se envía) y, si `alerta_caliente_panel`
// no es "false", el panel abre un modal (components/alertas.tsx).
const TELEFONO_VALIDO = /^\d{8,15}$/;
const soloDigitos = (v: string) => v.replace(/[\s()+.-]/g, "");

const ClientaCaliente = component$<{ s: Settings; soloLectura: boolean; save: QRL<(patch: Settings) => Promise<void>> }>(
  ({ s, soloLectura, save }) => {
    const tel = useSignal(s.alerta_caliente_whatsapp ?? "");
    const error = useSignal("");
    // Último valor enviado: el blur y el «Guardar» llegan juntos al pulsar el botón; se guarda una vez.
    const enviado = useSignal(s.alerta_caliente_whatsapp ?? "");

    const guardarTel = $(async () => {
      const v = soloDigitos(tel.value);
      if (v !== "" && !TELEFONO_VALIDO.test(v)) {
        error.value = "Escribe solo números, con el código de país: entre 8 y 15 dígitos (por ejemplo 51987654321).";
        return;
      }
      error.value = "";
      tel.value = v;
      if (v === enviado.value) return;
      enviado.value = v;
      await save({ alerta_caliente_whatsapp: v });
    });

    return (
      <section class="panel">
        <div class="panel-head">
          <h2>🔥 Clienta caliente</h2>
        </div>
        <p class="muted small">
          El robot avisa cuando una clienta está lista para comprar: tiene un evento en los próximos 14 días, dice que lo quiere,
          pide una cita para probarse o tiene prisa. El aviso trae lo que el robot descubrió de ella.
        </p>
        <label class="setting">
          <div class="grow">
            <strong>Avisarme en el panel</strong>
            <p class="muted small">Abre una ventana con sus datos en el panel de quien esté conectada, para atenderla al momento.</p>
          </div>
          <input
            type="checkbox"
            class="switch"
            checked={s.alerta_caliente_panel !== "false"}
            disabled={soloLectura}
            onChange$={(_, el) => save({ alerta_caliente_panel: el.checked ? "true" : "false" })}
          />
        </label>
        <div class="setting setting-tel">
          <div class="grow">
            <label for="alerta-tel">
              <strong>WhatsApp que recibe el aviso</strong>
            </label>
            <p class="muted small" id="alerta-tel-hint">
              Número con código de país, por ejemplo 51987654321. Vacío = no se envía.
            </p>
            {error.value && (
              <p class="form-error small" role="alert">
                {error.value}
              </p>
            )}
          </div>
          <form class="tel-form" preventdefault:submit onSubmit$={guardarTel}>
            <input
              id="alerta-tel"
              type="tel"
              inputMode="numeric"
              autoComplete="off"
              placeholder="51987654321"
              maxLength={20}
              aria-describedby="alerta-tel-hint"
              aria-invalid={error.value ? "true" : undefined}
              value={tel.value}
              disabled={soloLectura}
              onInput$={(_, el) => {
                tel.value = el.value;
                error.value = "";
              }}
              onBlur$={guardarTel}
            />
            {!soloLectura && <button class="btn btn-sm btn-primary">Guardar</button>}
          </form>
        </div>
      </section>
    );
  },
);

type SeccionProps = { s: Settings; soloLectura: boolean; save: QRL<(patch: Settings) => Promise<void>> };

/** Interruptor de un ajuste "true"/"false" con su explicación. */
const Interruptor = component$<SeccionProps & { clave: string; label: string; hint: string; porDefecto?: boolean }>(
  ({ s, soloLectura, save, clave, label, hint, porDefecto }) => (
    <label class="setting">
      <div class="grow">
        <strong>{label}</strong>
        <p class="muted small">{hint}</p>
      </div>
      <input
        type="checkbox"
        class="switch"
        checked={porDefecto ? s[clave] !== "false" : s[clave] === "true"}
        disabled={soloLectura}
        onChange$={(_, el) => save({ [clave]: el.checked ? "true" : "false" })}
      />
    </label>
  ),
);

// Bienvenida por anuncio (SEGUIMIENTO_CONTRATO §1): quien llega por un anuncio de Meta recibe presentación, foto del modelo,
// garantía y «Ver colores / Ver tallas». La garantía no es un ajuste: es una fuente de tipo política con «garantía» en el
// título o las etiquetas.
const BienvenidaAnuncio = component$<SeccionProps>((props) => (
  <section class="panel">
    <div class="panel-head">
      <h2>📣 Bienvenida por anuncio</h2>
    </div>
    <Interruptor
      {...props}
      clave="bienvenida_anuncio"
      label="Dar la bienvenida a quien llega por un anuncio"
      hint="El robot se presenta, manda la foto del modelo del anuncio, la garantía y los botones «Ver colores» y «Ver tallas»."
    />
    <Interruptor
      {...props}
      clave="precio_al_llegar_por_anuncio"
      label="Mostrar el precio en esa foto"
      hint="La foto de bienvenida lleva el precio aunque «Precios solo si la clienta los pregunta» esté encendido."
    />
    <p class="muted small">
      El texto de la garantía sale de una fuente de tipo <b>política</b> cuyo título o etiquetas digan «garantía». Créala o
      revísala en <a href={u("/fuentes?tab=guion")}>Fuentes → Guion y políticas</a>. Sin esa fuente, la bienvenida va sin garantía.
    </p>
  </section>
));

const DIAS_MIN = 1;
const DIAS_MAX = 365;

/** Campo numérico de días (1–365) que se guarda al salir si es válido. */
const CampoDias = component$<SeccionProps & { clave: string; label: string; hint: string; porDefecto: string }>(
  ({ s, soloLectura, save, clave, label, hint, porDefecto }) => {
    const error = useSignal("");
    const id = `dias-${clave}`;
    return (
      <div class="setting">
        <div class="grow">
          <label for={id}>
            <strong>{label}</strong>
          </label>
          <p class="muted small" id={`${id}-hint`}>
            {hint}
          </p>
          {error.value && (
            <p class="form-error small" role="alert">
              {error.value}
            </p>
          )}
        </div>
        <input
          id={id}
          type="number"
          min={DIAS_MIN}
          max={DIAS_MAX}
          step="1"
          class="num"
          inputMode="numeric"
          aria-describedby={`${id}-hint`}
          aria-invalid={error.value ? "true" : undefined}
          value={s[clave] || porDefecto}
          disabled={soloLectura}
          onChange$={(_, el) => {
            const n = Number(el.value);
            if (!Number.isInteger(n) || n < DIAS_MIN || n > DIAS_MAX) {
              error.value = `Escribe un número de días entre ${DIAS_MIN} y ${DIAS_MAX}.`;
              return;
            }
            error.value = "";
            if (String(n) !== s[clave]) save({ [clave]: String(n) });
          }}
        />
      </div>
    );
  },
);

// Prospectos a futuro y presupuesto (§1): con un evento lejano el robot pregunta «¿ahora o coordinamos en <mes>?» y, si
// elige más adelante, la clienta pasa a la etapa «Más adelante» con una tarea para volver a escribirle.
const ProspectosFuturo = component$<SeccionProps>((props) => {
  const { s } = props;
  const desde = s.prospecto_desde_dias || "45";
  const aviso = s.prospecto_aviso_dias || "30";
  return (
    <section class="panel">
      <div class="panel-head">
        <h2>🗓 Prospectos a futuro</h2>
      </div>
      <Interruptor
        {...props}
        clave="prospectos_habilitado"
        label="Preguntar si compra ahora o más adelante"
        hint={`Si el evento de la clienta está a ${desde} días o más, el robot le pregunta si lo ve ahora o lo coordinan más cerca de la fecha. Si elige después, pasa a la etapa «Más adelante» y queda una tarea para escribirle ${aviso} días antes.`}
      />
      <CampoDias
        {...props}
        clave="prospecto_desde_dias"
        porDefecto="45"
        label="Evento lejano desde (días)"
        hint="Desde cuántos días antes del evento se le hace la pregunta."
      />
      <CampoDias
        {...props}
        clave="prospecto_aviso_dias"
        porDefecto="30"
        label="Volver a escribirle (días antes del evento)"
        hint="La tarea de seguimiento vence ese día."
      />
      <div class="panel-head fila-sub">
        <h2>💰 Presupuesto</h2>
      </div>
      <Interruptor
        {...props}
        clave="preguntar_presupuesto"
        label="Preguntar el presupuesto"
        hint="Cuando ya sabe la ocasión, el robot pregunta qué rango de precio tiene en mente para recomendar mejor."
      />
    </section>
  );
});

const HORA = /^([01]\d|2[0-3]):[0-5]\d$/;

// Horario de seguimiento (§1): hora de Lima en la que pueden salir los pasos de las secuencias ("HH:MM-HH:MM").
const HorarioSeguimiento = component$<SeccionProps>(({ s, soloLectura, save }) => {
  const [ini0, fin0] = (s.seguimiento_horario || "08:00-21:00").split("-");
  const ini = useSignal(HORA.test(ini0 ?? "") ? ini0 : "08:00");
  const fin = useSignal(HORA.test(fin0 ?? "") ? fin0 : "21:00");
  const error = useSignal("");

  const guardar = $(async () => {
    if (!HORA.test(ini.value) || !HORA.test(fin.value)) {
      error.value = "Elige la hora de inicio y la de fin.";
      return;
    }
    if (ini.value >= fin.value) {
      error.value = "La hora de inicio debe ser antes que la de fin.";
      return;
    }
    error.value = "";
    const v = `${ini.value}-${fin.value}`;
    if (v !== s.seguimiento_horario) await save({ seguimiento_horario: v });
  });

  return (
    <section class="panel">
      <div class="panel-head">
        <h2>⏰ Horario de seguimiento</h2>
        <a class="small" href={u("/seguimiento")}>
          Ver secuencias →
        </a>
      </div>
      <p class="muted small">
        Los mensajes de seguimiento solo salen en este horario (hora de Lima). Lo que toque fuera de él espera a la mañana
        siguiente; no se pierde.
      </p>
      <div class="horario">
        <label>
          Desde
          <input type="time" value={ini.value} disabled={soloLectura} onChange$={(_, el) => ((ini.value = el.value), guardar())} />
        </label>
        <label>
          Hasta
          <input type="time" value={fin.value} disabled={soloLectura} onChange$={(_, el) => ((fin.value = el.value), guardar())} />
        </label>
      </div>
      {error.value && (
        <p class="form-error small" role="alert">
          {error.value}
        </p>
      )}
    </section>
  );
});

// Colores: a qué grupo va cada color de los productos para el guion de noche del bot («¿Prefieres llamar un poco más la
// atención, o buscas verte elegante, sofisticada y más discreta?»). Con la respuesta, el bot ofrece prendas en colores
// vibrantes o sobrios. La propuesta viene puesta; la admin la cambia aquí y el bot la usa en el siguiente mensaje.
interface ColorTienda {
  color: string;
  clave: string;
  grupo: "" | "sobrio" | "vibrante";
  productos: number;
  propio: boolean;
}

const GRUPOS: { valor: ColorTienda["grupo"]; label: string; clase: string }[] = [
  { valor: "sobrio", label: "Sobrio", clase: "sobrio" },
  { valor: "vibrante", label: "Vibrante", clase: "vibrante" },
  { valor: "", label: "Sin grupo", clase: "ninguno" },
];

const Colores = component$<{ yo: Sesion }>(({ yo }) => {
  const lista = useSignal<ColorTienda[]>([]);
  const error = useSignal("");
  const ok = useSignal("");
  const cargado = useSignal(false);

  const load = $(async () => {
    lista.value = await api<ColorTienda[]>("/api/colores");
    cargado.value = true;
  });
  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(() => load());

  const fijar = $(async (c: ColorTienda, grupo: ColorTienda["grupo"]) => {
    error.value = "";
    try {
      lista.value = await api<ColorTienda[]>("/api/colores", { method: "PUT", json: { grupos: { [c.clave]: grupo } } });
      ok.value = `${c.color}: ${grupo === "" ? "sin grupo" : grupo}`;
      setTimeout(() => (ok.value = ""), 2500);
    } catch (e) {
      error.value = (e as Error).message;
    }
  });

  const n = (g: ColorTienda["grupo"]) => lista.value.filter((c) => c.grupo === g).length;

  return (
    <section class="panel">
      <div class="panel-head">
        <h2>Colores</h2>
        <span class="muted small">
          {n("sobrio")} sobrios · {n("vibrante")} vibrantes · {n("")} sin grupo
        </span>
      </div>
      <p class="muted small">
        En los eventos de noche el bot pregunta «¿prefieres llamar un poco más la atención, o verte elegante y discreta?». Con la
        respuesta ofrece prendas en colores <b>vibrantes</b> o <b>sobrios</b>, según lo que marques aquí. Son los colores que
        aparecen en tus productos; la propuesta ya viene puesta y puedes cambiarla cuando quieras.
      </p>
      {error.value && <p class="form-error small">{error.value}</p>}
      {ok.value && <p class="ok-text small">{ok.value} ✓</p>}
      {cargado.value && lista.value.length === 0 && <p class="muted small">Tus productos aún no tienen colores.</p>}
      <div class="colores-lista">
        {lista.value.map((c) => (
          <div key={c.clave} class="color-fila">
            <div>
              <strong>{c.color}</strong>
              <span class="muted small">
                {" "}
                · {c.productos === 1 ? "1 producto" : `${c.productos} productos`}
                {c.propio ? " · fijado por la tienda" : ""}
              </span>
            </div>
            <div class="grupo" role="group" aria-label={`Grupo de ${c.color}`}>
              {GRUPOS.map((g) => (
                <button
                  key={g.valor}
                  type="button"
                  class={[c.grupo === g.valor && "active", c.grupo === g.valor && g.clase]}
                  disabled={!yo.admin || c.grupo === g.valor}
                  onClick$={() => fijar(c, g.valor)}
                >
                  {g.label}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
});

// Equipo: personas que entran al panel. Admin: todo. Asesora: atiende clientes, pedidos, notas y tareas, pero no cambia
// ajustes, WhatsApp ni el equipo, no borra pedidos ni productos y no exporta.
const Equipo = component$<{ yo: Sesion }>(({ yo }) => {
  const lista = useSignal<Miembro[]>([]);
  const nuevo = useStore({ username: "", name: "", role: "asesora", password: "" });
  const abierto = useSignal(false);
  const error = useSignal("");
  const ok = useSignal("");

  const load = $(async () => {
    lista.value = await equipo(true);
  });
  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(() => load());

  const cambiar = $(async (m: Miembro, patch: Record<string, unknown>, msg: string) => {
    error.value = "";
    try {
      await api(`/api/equipo/${m.id}`, { method: "PATCH", json: patch });
      ok.value = msg;
      setTimeout(() => (ok.value = ""), 2500);
      await load();
    } catch (e) {
      error.value = (e as Error).message;
      await load();
    }
  });

  return (
    <section class="panel">
      <div class="panel-head">
        <h2>Equipo</h2>
        {yo.admin && (
          <button class="btn btn-sm btn-primary" onClick$={() => (abierto.value = !abierto.value)}>
            {abierto.value ? "Cerrar" : "+ Persona"}
          </button>
        )}
      </div>
      <p class="muted small">Admin: todo. Asesora: atiende clientes, pedidos, notas y tareas; no cambia ajustes, WhatsApp ni el equipo, no borra y no exporta.</p>
      {abierto.value && (
        <form
          class="equipo-form"
          preventdefault:submit
          onSubmit$={async () => {
            error.value = "";
            try {
              await api("/api/equipo", { method: "POST", json: nuevo });
              Object.assign(nuevo, { username: "", name: "", role: "asesora", password: "" });
              abierto.value = false;
              ok.value = "Persona añadida";
              await load();
            } catch (e) {
              error.value = (e as Error).message;
            }
          }}
        >
          <div class="grid2">
            <label>
              Nombre
              <input value={nuevo.name} onInput$={(_, el) => (nuevo.name = el.value)} placeholder="Lucía Pérez" />
            </label>
            <label>
              Usuario
              <input value={nuevo.username} onInput$={(_, el) => (nuevo.username = el.value)} required autoComplete="off" placeholder="lucia" />
            </label>
            <label>
              Clave (mín. 8)
              <input type="password" value={nuevo.password} onInput$={(_, el) => (nuevo.password = el.value)} required minLength={8} autoComplete="new-password" />
            </label>
            <label>
              Rol
              <select value={nuevo.role} onChange$={(_, el) => (nuevo.role = el.value)}>
                <option value="asesora">Asesora</option>
                <option value="admin">Admin</option>
              </select>
            </label>
          </div>
          <button class="btn btn-primary btn-sm">Añadir</button>
        </form>
      )}
      {error.value && <p class="form-error small">{error.value}</p>}
      {ok.value && <p class="ok-text small">{ok.value} ✓</p>}
      <ul class="equipo">
        {lista.value.map((m) => (
          <li key={m.id} class={["miembro", !m.active && "inactivo"]}>
            <span class="avatar sm">{(m.name || m.username).slice(0, 1).toUpperCase()}</span>
            <div class="grow">
              <strong>{m.name || m.username}</strong>
              <span class="muted small block">
                {m.username}
                {m.id === yo.id ? " · tú" : ""}
                {!m.active ? " · desactivada" : ""}
              </span>
            </div>
            {yo.admin ? (
              <>
                <select
                  class="auto"
                  value={m.role === "asesora" ? "asesora" : "admin"}
                  aria-label={`Rol de ${m.username}`}
                  onChange$={(_, el) => cambiar(m, { role: el.value }, "Rol cambiado")}
                >
                  <option value="asesora">Asesora</option>
                  <option value="admin">Admin</option>
                </select>
                <button
                  class="btn btn-sm btn-ghost"
                  onClick$={async () => {
                    const clave = prompt(`Nueva clave para ${m.username} (mínimo 8 caracteres):`);
                    if (clave) await cambiar(m, { password: clave }, "Clave cambiada");
                  }}
                >
                  Clave
                </button>
                {m.id !== yo.id && (
                  <button class="btn btn-sm btn-ghost" onClick$={() => cambiar(m, { active: !m.active }, m.active ? "Desactivada" : "Activada")}>
                    {m.active ? "Desactivar" : "Activar"}
                  </button>
                )}
              </>
            ) : (
              <span class="chip">{m.role === "asesora" ? "Asesora" : "Admin"}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
});

export const head: DocumentHead = { title: titulo("Ajustes") };
