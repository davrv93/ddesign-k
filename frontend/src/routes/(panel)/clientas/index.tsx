import { component$, useVisibleTask$ } from "@builder.io/qwik";
import type { DocumentHead } from "@builder.io/qwik-city";
import { u } from "~/lib/base";
import { titulo } from "~/lib/marca";

// La pantalla pasó a /clientes: los enlaces viejos (/clientas?c=12) llevan a la nueva con los mismos parámetros.
export default component$(() => {
  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(() => {
    location.replace(u("/clientes/") + location.search);
  });
  return <div class="loading">Abriendo clientes…</div>;
});

export const head: DocumentHead = { title: titulo("Clientes") };
