import { $, component$, useSignal, useStyles$, useVisibleTask$ } from "@builder.io/qwik";
import type { DocumentHead } from "@builder.io/qwik-city";
import { api, getToken, setToken } from "~/lib/api";
import { u } from "~/lib/base";
import { LEMA, MARCA, cargarEmpresa, empresaGuardada, iniciales, titulo } from "~/lib/marca";
import estilos from "./login.css?inline";

/** Datos públicos de la tienda para su login (GET /<slug>/api/public/empresa). */
interface EmpresaLogin {
  nombre: string;
  color: string;
  logo: string;
  direccion: string;
  horario: string;
  galeria: string[];
  fotos_catalogo: string[];
}

// Las rutas del producto (/jmdventas/_jmd/…) van tal cual; las de la tienda (/media/…), con su ruta base.
const ruta = (p: string) => (p.startsWith("/jmdventas/") || p.startsWith("https://") ? p : u(p));

// El login editorial es de JMD Ventas; el panel de una sola tienda (/baruka/) conserva el de siempre.
const EDITORIAL = MARCA === "JMD Ventas";

export default component$(() => {
  useStyles$(estilos);
  const user = useSignal("admin");
  const pass = useSignal("");
  const error = useSignal("");
  const busy = useSignal(false);
  const empresa = useSignal("");
  const datos = useSignal<EmpresaLogin | null>(null);

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    if (getToken()) {
      location.href = u("/");
      return;
    }
    // Sin la intro animada: el login de cada tienda va directo (la intro queda solo en la portada /jmdventas/).
    empresa.value = empresaGuardada();
    if (EDITORIAL) {
      try {
        const r = await fetch(u("/api/public/empresa"));
        if (r.ok) {
          datos.value = (await r.json()) as EmpresaLogin;
          empresa.value = datos.value.nombre;
          return;
        }
      } catch {
        /* sin conexión: queda el nombre guardado */
      }
    }
    empresa.value = await cargarEmpresa();
  });

  const submit = $(async () => {
    busy.value = true;
    error.value = "";
    try {
      const r = await api<{ token: string }>("/api/auth/login", { method: "POST", json: { user: user.value, password: pass.value } });
      setToken(r.token);
      location.href = u("/");
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      busy.value = false;
    }
  });

  if (!EDITORIAL) {
    return (
      <main class="login">
        <form class="login-card" preventdefault:submit onSubmit$={submit}>
          <div class="brand brand-lg">
            <span class="brand-mark">{iniciales(empresa.value || MARCA)}</span>
            <div>
              <strong>{empresa.value || " "}</strong>
              <small>{LEMA === "CRM WhatsApp" ? "CRM de pedidos por WhatsApp" : LEMA}</small>
            </div>
          </div>
          <label>
            Usuario
            <input bind:value={user} autoComplete="username" required />
          </label>
          <label>
            Contraseña
            <input type="password" bind:value={pass} autoComplete="current-password" required autoFocus />
          </label>
          {error.value && <p class="form-error">{error.value}</p>}
          <button class="btn btn-primary btn-block" disabled={busy.value}>
            {busy.value ? "Ingresando…" : "Ingresar"}
          </button>
        </form>
      </main>
    );
  }

  // Lookbook: las fotos de la tienda (o de su catálogo), repartidas en tres columnas y repetidas para el bucle.
  const d = datos.value;
  const fotos = d ? (d.galeria?.length ? d.galeria : d.fotos_catalogo || []).map(ruta) : [];
  const base = fotos.length ? Array.from({ length: Math.max(fotos.length, 9) }, (_, i) => fotos[i % fotos.length]) : [];
  const cols = [0, 1, 2].map((c) => base.filter((_, i) => i % 3 === c));
  const color = d && /^#[0-9a-fA-F]{6}$/.test(d.color) ? d.color : "#6d1f45";
  const nombre = d?.nombre || empresa.value;
  const placa = !!d?.logo && !d.logo.endsWith(".png"); // un PNG transparente va sin placa

  return (
    <main class="lg" style={{ "--acento": color }}>
      <section class="lg-galeria" aria-hidden="true">
        <div class="lg-cols">
          {cols.map((col, ci) => (
            <div class="lg-col" key={ci} style={{ animationDuration: `${54 + ci * 14}s` }}>
              {[...col, ...col].map((src, i) => (
                <img key={i} src={src} alt="" width={300} height={400} loading={i < 4 ? "eager" : "lazy"} style={{ animationDelay: `${(i % 6) * 0.12}s` }} />
              ))}
            </div>
          ))}
        </div>
        <div class="lg-velo" />
        <div class="lg-leyenda">
          <span>Colección</span>
          <b>{nombre || " "}</b>
        </div>
      </section>

      <section class="lg-panel">
        <div class="lg-marca">
          {d?.logo ? (
            <img class={["lg-logo", placa && "placa"]} src={ruta(d.logo)} alt={nombre} width={260} height={92} />
          ) : (
            <span class="lg-mono">{iniciales(nombre || MARCA)}</span>
          )}
        </div>
        <h1 class="lg-nombre">{nombre || " "}</h1>
        <p class="lg-lema">Panel de la tienda · JMD Ventas</p>

        <form class="lg-form" preventdefault:submit onSubmit$={submit}>
          <label>
            Usuario
            <input bind:value={user} autoComplete="username" required />
          </label>
          <label>
            Contraseña
            <input type="password" bind:value={pass} autoComplete="current-password" required autoFocus />
          </label>
          {error.value && <p class="lg-error">{error.value}</p>}
          <button class="lg-boton" disabled={busy.value}>
            {busy.value ? "Ingresando…" : "Ingresar"}
          </button>
        </form>

        {(d?.direccion || d?.horario) && (
          <ul class="lg-datos">
            {d?.direccion && (
              <li>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
                  <path d="M12 21s-7-5.6-7-11a7 7 0 0 1 14 0c0 5.4-7 11-7 11z" />
                  <circle cx="12" cy="10" r="2.6" />
                </svg>
                <span>{d.direccion}</span>
              </li>
            )}
            {d?.horario && (
              <li>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
                  <circle cx="12" cy="12" r="8.5" />
                  <path d="M12 7.5V12l3 2" />
                </svg>
                <span>{d.horario}</span>
              </li>
            )}
          </ul>
        )}
        <p class="lg-pie">JMD Ventas · por Consultoría Digital</p>
      </section>
    </main>
  );
});

export const head: DocumentHead = { title: titulo("Ingresar") };
