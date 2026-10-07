import { $, component$, noSerialize, useOnWindow, useSignal, useStore, useVisibleTask$, type NoSerialize, type QRL } from "@builder.io/qwik";
import type { DocumentHead } from "@builder.io/qwik-city";
import { titulo } from "~/lib/marca";
import { api, getToken, type Product } from "~/lib/api";
import { money } from "~/lib/format";
import { u } from "~/lib/base";

const emptyProduct = (): Product => ({
  id: 0,
  code: "",
  name: "",
  description: "",
  category: "vestido midi",
  color: "",
  price: 0,
  image: "",
  ai_tags: "",
  active: true,
  variants: [
    { size: "S", stock: 0 },
    { size: "M", stock: 0 },
    { size: "L", stock: 0 },
  ],
});

async function upload(path: string, file: File) {
  const fd = new FormData();
  fd.append("image", file);
  const res = await fetch(u(path), { method: "POST", body: fd, headers: { Authorization: `Bearer ${getToken()}` } });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error ?? `Error ${res.status}`);
  return data;
}

export default component$(() => {
  const products = useSignal<Product[]>([]);
  const editing = useSignal<Product | null>(null);
  const filter = useSignal("");
  const onlyLow = useSignal(false);

  const load = $(async () => {
    products.value = await api<Product[]>("/api/products");
  });

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(() => load());
  useOnWindow(
    "kd-change",
    $((e: Event) => {
      if ((e as CustomEvent<string>).detail === "products") load();
    }),
  );

  const q = filter.value.trim().toLowerCase();
  const list = products.value.filter(
    (p) =>
      (!q || `${p.code} ${p.name} ${p.color} ${p.category}`.toLowerCase().includes(q)) &&
      (!onlyLow.value || p.variants.some((v) => v.stock <= 1)),
  );

  return (
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Productos y stock</h1>
          <p class="muted">
            El bot usa la foto y las etiquetas de cada producto para reconocer lo que envía el cliente. Los códigos (V01, V02…) sirven para pedir
            por chat.
          </p>
        </div>
        <div class="head-actions">
          <input class="search" type="search" placeholder="Buscar…" bind:value={filter} />
          <label class="check">
            <input type="checkbox" bind:checked={onlyLow} /> Stock bajo
          </label>
          <button class="btn btn-primary" onClick$={() => (editing.value = emptyProduct())}>
            + Producto
          </button>
        </div>
      </div>

      <div class="product-grid">
        {list.map((p) => {
          const total = p.variants.reduce((n, v) => n + v.stock, 0);
          return (
            <article key={p.id} class={["product", !p.active && "inactive"]} onClick$={() => (editing.value = structuredClone(p))}>
              <div class="product-img">
                {p.image ? <img src={u(p.image)} alt={p.name} width={240} height={320} loading="lazy" /> : <span class="muted">Sin foto</span>}
                {total === 0 && <span class="sold-out">Agotado</span>}
                {!p.active && <span class="sold-out off">Oculto</span>}
              </div>
              <div class="product-body">
                <div class="product-top">
                  <b>{p.code}</b>
                  <span class="price">{money(p.price)}</span>
                </div>
                <div class="product-name">{p.name}</div>
                <div class="sizes">
                  {p.variants.map((v) => (
                    <span key={v.size} class={["size", v.stock === 0 ? "zero" : v.stock <= 1 ? "low" : ""]}>
                      {v.size} <b>{v.stock}</b>
                    </span>
                  ))}
                </div>
              </div>
            </article>
          );
        })}
      </div>

      {editing.value && (
        <ProductForm
          key={editing.value.id || "new"}
          product={editing.value}
          onClose$={() => (editing.value = null)}
          onSaved$={async () => {
            editing.value = null;
            await load();
          }}
        />
      )}
    </div>
  );
});

// ---------------------------------------------------------------------------

interface FormProps {
  product: Product;
  onClose$: QRL<() => void>;
  onSaved$: QRL<() => Promise<void>>;
}

const ProductForm = component$<FormProps>(({ product, onClose$, onSaved$ }) => {
  const p = useStore<Product>(structuredClone(product), { deep: true });
  const file = useSignal<NoSerialize<File>>();
  const preview = useSignal(u(product.image));
  const busy = useSignal("");
  const error = useSignal("");

  const describe = $(async () => {
    if (!file.value) return;
    busy.value = "Analizando la foto con IA…";
    error.value = "";
    try {
      const d = await upload("/api/products/describe", file.value);
      if (!p.name) p.name = d.name;
      if (!p.description) p.description = d.description;
      p.color = d.color || p.color;
      p.category = d.category || p.category;
      p.ai_tags = d.tags || p.ai_tags;
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      busy.value = "";
    }
  });

  return (
    <div class="overlay overlay-center" onClick$={onClose$}>
      <form
        class="modal modal-lg"
        preventdefault:submit
        onClick$={(e) => e.stopPropagation()}
        onSubmit$={async () => {
          busy.value = "Guardando…";
          error.value = "";
          try {
            const body = { ...p, price: Number(p.price) || 0, variants: p.variants.map((v) => ({ size: v.size, stock: Number(v.stock) || 0 })) };
            const saved = await api<Product>(p.id ? `/api/products/${p.id}` : "/api/products", { method: p.id ? "PUT" : "POST", json: body });
            if (file.value) await upload(`/api/products/${saved.id}/image${p.ai_tags ? "" : "?describe=1"}`, file.value);
            await onSaved$();
          } catch (e) {
            error.value = (e as Error).message;
          } finally {
            busy.value = "";
          }
        }}
      >
        <h2>{p.id ? `Editar ${p.code}` : "Nuevo producto"}</h2>
        <div class="form-cols">
          <div class="photo-col">
            <div class="photo-box">{preview.value ? <img src={preview.value} alt="" width={220} height={290} /> : <span class="muted">Sin foto</span>}</div>
            <label class="btn btn-sm btn-block">
              {preview.value ? "Cambiar foto" : "Subir foto"}
              <input
                type="file"
                accept="image/*"
                hidden
                onChange$={(_, el) => {
                  const f = el.files?.[0];
                  if (!f) return;
                  file.value = noSerialize(f);
                  preview.value = URL.createObjectURL(f);
                }}
              />
            </label>
            <button type="button" class="btn btn-sm btn-block" disabled={!file.value || !!busy.value} onClick$={describe}>
              ✨ Autocompletar con IA
            </button>
          </div>
          <div class="grow">
            <div class="grid2">
              <label>
                Código
                <input value={p.code} onInput$={(_, el) => (p.code = el.value)} required placeholder="V21" />
              </label>
              <label>
                Precio (S/)
                <input type="number" step="0.01" min="0" value={p.price} onInput$={(_, el) => (p.price = Number(el.value))} required />
              </label>
            </div>
            <label>
              Nombre
              <input value={p.name} onInput$={(_, el) => (p.name = el.value)} required />
            </label>
            <div class="grid2">
              <label>
                Tipo
                <select value={p.category} onChange$={(_, el) => (p.category = el.value)}>
                  {[...new Set(["vestido corto", "vestido midi", "vestido largo", "blusa", "pantalón", "conjunto", "otro", p.category])]
                    .filter(Boolean)
                    .map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                Color
                <input value={p.color} onInput$={(_, el) => (p.color = el.value)} />
              </label>
            </div>
            <label>
              Descripción (la ve el cliente)
              <textarea rows={2} value={p.description} onInput$={(_, el) => (p.description = el.value)} />
            </label>
            <label>
              Etiquetas para la IA (corte, escote, mangas, tela…)
              <textarea rows={2} value={p.ai_tags} onInput$={(_, el) => (p.ai_tags = el.value)} />
            </label>

            <fieldset class="variants">
              <legend>Tallas y stock</legend>
              {p.variants.map((v, i) => (
                <div class="variant" key={i}>
                  <input class="size-in" value={v.size} onInput$={(_, el) => (p.variants[i].size = el.value.toUpperCase())} placeholder="Talla" />
                  <input type="number" min="0" value={v.stock} onInput$={(_, el) => (p.variants[i].stock = Number(el.value))} />
                  <button type="button" class="btn btn-ghost btn-sm" aria-label="Quitar talla" onClick$={() => p.variants.splice(i, 1)}>
                    ✕
                  </button>
                </div>
              ))}
              <button type="button" class="btn btn-sm" onClick$={() => p.variants.push({ size: "", stock: 0 })}>
                + Talla
              </button>
            </fieldset>
            <label class="check">
              <input type="checkbox" checked={p.active} onChange$={(_, el) => (p.active = el.checked)} /> Visible en el catálogo y para el bot
            </label>
          </div>
        </div>
        {error.value && <p class="form-error">{error.value}</p>}
        <div class="modal-actions">
          {p.id > 0 && (
            <button
              type="button"
              class="btn btn-danger btn-sm push-left"
              onClick$={async () => {
                if (!confirm(`¿Eliminar ${p.code}? Los pedidos existentes conservan el nombre del producto.`)) return;
                await api(`/api/products/${p.id}`, { method: "DELETE" });
                await onSaved$();
              }}
            >
              Eliminar
            </button>
          )}
          {busy.value && <span class="muted small">{busy.value}</span>}
          <button type="button" class="btn btn-ghost" onClick$={onClose$}>
            Cancelar
          </button>
          <button class="btn btn-primary" disabled={!!busy.value}>
            Guardar
          </button>
        </div>
      </form>
    </div>
  );
});

export const head: DocumentHead = { title: titulo("Productos") };
