import { $, component$, useSignal, useVisibleTask$ } from "@builder.io/qwik";
import type { DocumentHead } from "@builder.io/qwik-city";
import { api, getToken, setToken } from "~/lib/api";
import { u } from "~/lib/base";

export default component$(() => {
  const user = useSignal("admin");
  const pass = useSignal("");
  const error = useSignal("");
  const busy = useSignal(false);

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(() => {
    if (getToken()) location.href = u("/");
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

  return (
    <main class="login">
      <form class="login-card" preventdefault:submit onSubmit$={submit}>
        <div class="brand brand-lg">
          <span class="brand-mark">BD</span>
          <div>
            <strong>Baruka Design</strong>
            <small>CRM de pedidos por WhatsApp</small>
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
});

export const head: DocumentHead = { title: "Ingresar · Baruka Design" };
