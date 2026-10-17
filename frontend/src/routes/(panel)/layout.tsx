import { $, component$, Slot, useSignal, useVisibleTask$ } from "@builder.io/qwik";
import { Link, useLocation } from "@builder.io/qwik-city";
import { api, getToken, setToken, type Conversation } from "~/lib/api";
import { BASE, u } from "~/lib/base";
import { LEMA, MARCA, cargarEmpresa, empresaGuardada, iniciales } from "~/lib/marca";
import { AlertasCalientes } from "~/components/alertas";

const NAV = [
  { href: "/", label: "Inicio", icon: "◎" },
  { href: "/pedidos", label: "Pedidos", icon: "▦" },
  { href: "/clientes", label: "Clientes", icon: "☺" },
  { href: "/tareas", label: "Tareas", icon: "☑" },
  { href: "/fuentes", label: "Fuentes", icon: "📚" },
  { href: "/seguimiento", label: "Seguimiento", icon: "↻" },
  { href: "/conversaciones", label: "Conversaciones", icon: "✉" },
  { href: "/campanas", label: "Campañas", icon: "📣" },
  { href: "/productos", label: "Productos y stock", icon: "👗" },
  { href: "/whatsapp", label: "WhatsApp", icon: "☎" },
  { href: "/ajustes", label: "Ajustes", icon: "⚙" },
];

export default component$(() => {
  const ready = useSignal(false);
  const loc = useLocation();
  const wa = useSignal<{ available?: boolean; logged_in?: boolean; name?: string; habilitado?: boolean } | null>(null);
  const unread = useSignal(0);
  const navOpen = useSignal(false);
  const empresa = useSignal("");
  // Clientas calientes sin atender (las cuenta AlertasCalientes) y el gatillo para reabrir su modal.
  const calientes = useSignal(0);
  const verCalientes = useSignal(0);

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(({ cleanup }) => {
    const token = getToken();
    if (!token) {
      location.href = u("/login");
      return;
    }
    ready.value = true;
    empresa.value = empresaGuardada();
    cargarEmpresa().then((n) => (empresa.value = n));

    // Avisos en vivo del backend: cada página escucha "kd-change" y recarga lo suyo.
    const es = new EventSource(u(`/api/events?token=${encodeURIComponent(token)}`));
    es.addEventListener("change", (ev) =>
      window.dispatchEvent(new CustomEvent("kd-change", { detail: (ev as MessageEvent).data })),
    );

    const refresh = async () => {
      try {
        wa.value = await api("/api/whatsapp/status");
      } catch {
        /* sin conexión */
      }
      try {
        const convs = await api<Conversation[]>("/api/conversations");
        unread.value = convs.reduce((n, c) => n + (c.unread > 0 ? 1 : 0), 0);
      } catch {
        /* sin conexión */
      }
    };
    refresh();
    const onChange = (e: Event) => {
      const topic = (e as CustomEvent<string>).detail;
      if (topic === "conversations" || topic === "whatsapp") refresh();
    };
    window.addEventListener("kd-change", onChange);
    const iv = setInterval(refresh, 30000);
    cleanup(() => {
      es.close();
      window.removeEventListener("kd-change", onChange);
      clearInterval(iv);
    });
  });

  const logout = $(() => {
    setToken(null);
    location.href = u("/login");
  });

  const path = loc.url.pathname.slice(BASE.length) || "/";
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href.replace(/\/$/, "")));

  return (
    <div class={["shell", navOpen.value && "nav-open"]}>
      <aside class="sidebar">
        <div class="brand">
          <span class="brand-mark">{iniciales(empresa.value || MARCA)}</span>
          <div>
            <strong>{empresa.value || " "}</strong>
            <small>{LEMA}</small>
          </div>
        </div>
        <nav>
          {NAV.map((n) => (
            <Link key={n.href} href={u(n.href)} class={["nav-item", active(n.href) && "active"]} onClick$={() => (navOpen.value = false)}>
              <span class="nav-icon">{n.icon}</span>
              {n.label}
              {n.href.startsWith("/conversaciones") && calientes.value > 0 && (
                <span class="badge badge-caliente" title={`${calientes.value} clienta(s) caliente(s) sin atender`}>
                  🔥 {calientes.value}
                </span>
              )}
              {n.href.startsWith("/conversaciones") && unread.value > 0 && <span class="badge">{unread.value}</span>}
            </Link>
          ))}
          <a class="nav-item" href={u("/catalogo")} target="_blank">
            <span class="nav-icon">↗</span>Catálogo público
          </a>
        </nav>
        <div class="sidebar-foot">
          {calientes.value > 0 && (
            <button
              type="button"
              class="caliente-pill"
              onClick$={() => {
                navOpen.value = false;
                verCalientes.value++;
              }}
            >
              🔥 {calientes.value === 1 ? "1 clienta caliente" : `${calientes.value} clientas calientes`} sin atender
            </button>
          )}
          <Link href={u("/whatsapp")} class={["wa-pill", wa.value?.logged_in ? "ok" : "off"]}>
            <span class="dot" />
            {wa.value == null
              ? "Revisando WhatsApp…"
              : wa.value.habilitado === false
                ? "WhatsApp aún no conectado"
                : !wa.value.available
                ? "WhatsApp no disponible"
                : wa.value.logged_in
                  ? `Conectado${wa.value.name ? ` · ${wa.value.name}` : ""}`
                  : "WhatsApp sin vincular"}
          </Link>
          <button class="btn btn-ghost btn-sm" onClick$={logout}>
            Cerrar sesión
          </button>
        </div>
      </aside>
      <div class="main">
        <header class="topbar">
          <button class="btn btn-ghost burger" aria-label="Menú" onClick$={() => (navOpen.value = !navOpen.value)}>
            ☰
          </button>
          <span class="topbar-title">{empresa.value}</span>
          {calientes.value > 0 && (
            <button
              type="button"
              class="caliente-pill topbar-caliente"
              aria-label={`${calientes.value} clienta(s) caliente(s) sin atender`}
              onClick$={() => verCalientes.value++}
            >
              🔥 {calientes.value}
            </button>
          )}
        </header>
        {ready.value ? <Slot /> : <div class="loading">Cargando…</div>}
      </div>
      {ready.value && <AlertasCalientes cuenta={calientes} abrir={verCalientes} />}
      <div class="scrim" onClick$={() => (navOpen.value = false)} />
    </div>
  );
});
