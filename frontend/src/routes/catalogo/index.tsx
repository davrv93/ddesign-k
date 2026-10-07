import { component$, useSignal, useVisibleTask$ } from "@builder.io/qwik";
import type { DocumentHead } from "@builder.io/qwik-city";
import { money } from "~/lib/format";
import { u } from "~/lib/base";
import { MARCA, iniciales, titulo } from "~/lib/marca";

interface PubProduct {
  code: string;
  name: string;
  description: string;
  category: string;
  color: string;
  price: number;
  image: string;
  sizes: string[];
}

interface Catalog {
  business: string;
  currency: string;
  whatsapp: string;
  products: PubProduct[];
}

// Catálogo público: el bot comparte este enlace cuando la clienta pide ver modelos.
export default component$(() => {
  const data = useSignal<Catalog | null>(null);
  const cat = useSignal("todos");

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    const res = await fetch(u("/api/public/catalog"));
    data.value = await res.json();
  });

  const d = data.value;
  const cats = d ? ["todos", ...new Set(d.products.map((p) => p.category).filter(Boolean))] : [];
  const list = d ? d.products.filter((p) => cat.value === "todos" || p.category === cat.value) : [];

  return (
    <main class="catalog">
      <header class="catalog-head">
        <span class="brand-mark">{iniciales(d?.business ?? MARCA)}</span>
        <h1>{d?.business ?? " "}</h1>
        <p>Pide por WhatsApp enviando el código del modelo o su foto.</p>
        <div class="chips">
          {cats.map((c) => (
            <button key={c} class={["chip-btn", cat.value === c && "active"]} onClick$={() => (cat.value = c)}>
              {c}
            </button>
          ))}
        </div>
      </header>
      {!d && <p class="loading">Cargando catálogo…</p>}
      <div class="catalog-grid">
        {list.map((p) => (
          <article key={p.code} class={["cat-card", p.sizes.length === 0 && "out"]}>
            <div class="cat-img">
              {p.image && <img src={u(p.image)} alt={p.name} width={300} height={400} loading="lazy" />}
              {p.sizes.length === 0 && <span class="sold-out">Agotado</span>}
            </div>
            <div class="cat-body">
              <div class="product-top">
                <b>{p.code}</b>
                <span class="price">{money(p.price, d?.currency)}</span>
              </div>
              <h2>{p.name}</h2>
              <p class="muted small">{p.description}</p>
              {p.sizes.length > 0 && <p class="small">Tallas: {p.sizes.join(" · ")}</p>}
              {d?.whatsapp && p.sizes.length > 0 && (
                <a
                  class="btn btn-primary btn-block"
                  href={`https://wa.me/${d.whatsapp}?text=${encodeURIComponent(`Hola, me interesa el modelo ${p.code}`)}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  Pedir {p.code} por WhatsApp
                </a>
              )}
            </div>
          </article>
        ))}
      </div>
    </main>
  );
});

export const head: DocumentHead = {
  title: titulo("Catálogo"),
  meta: [{ name: "description", content: "Vestidos y moda femenina. Pide por WhatsApp." }],
};
