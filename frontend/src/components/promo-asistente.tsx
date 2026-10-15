// Asistente «Diseñar promo»: cinco pantallas cortas (objetivo, mecánica, vigencia, cómo la cuenta el bot y embudo).
// Crea la fuente en borrador (o la guarda si ya existía), el embudo propio si se pide, y ofrece activarla.
import { $, component$, useSignal, useStore, useVisibleTask$, type QRL } from "@builder.io/qwik";
import {
  api,
  cambiarEstadoFuente,
  crearEmbudo,
  crearFuente,
  guardarEmbudo,
  guardarFuente,
  listarEmbudos,
  type AlcancePromo,
  type Fuente,
  type FuenteEditable,
  type Mecanica,
  type Product,
  type PromoDatos,
  type Publico,
} from "~/lib/api";
import { u } from "~/lib/base";
import { money } from "~/lib/format";
import {
  CATEGORIAS,
  COLECCIONES,
  MECANICAS,
  PUBLICOS,
  alcanceTexto,
  contenidoPromo,
  diasVigencia,
  hoyLima,
  mecanicaTexto,
  precioPromo,
  sinTildes,
  usaValor,
  vigenciaTexto,
} from "~/lib/fuentes";

const PASOS = ["Objetivo", "Mecánica", "Vigencia", "Cómo la cuenta", "Embudo"];
const ETAPAS_SUGERIDAS = ["Interesada", "Cotizada", "Separada", "Pagada"];

// Colección de baruka.pe → palabra de la categoría del producto (solo para elegir la prenda de ejemplo).
const COLECCION_A_CATEGORIA: Record<string, string> = {
  vestidos: "vestido",
  "vestido-cortos": "vestido corto",
  enterizos: "enterizo",
  blusas: "blusa",
  pantalon: "pantalon",
  conjuntos: "conjunto",
  sastre: "blazer",
  blazer: "blazer",
};

/** Una prenda de ejemplo que entra en la promo, para la vista previa. */
function prendaDeEjemplo(ps: Product[], a: AlcancePromo): Product | null {
  const activos = ps.filter((p) => p.active && p.price > 0);
  if (a.tipo === "prendas") return activos.find((p) => a.codigos.includes(p.code)) ?? ps.find((p) => a.codigos.includes(p.code)) ?? null;
  const clave = a.tipo === "categoria" ? a.categoria : a.tipo === "coleccion" ? (COLECCION_A_CATEGORIA[a.coleccion] ?? "") : "";
  if (clave) {
    const k = sinTildes(clave);
    // Sin una prenda de esa categoría no se muestra otra (un vestido de ejemplo para una promo de blusas confunde).
    return activos.find((x) => sinTildes(`${x.category} ${x.name}`).includes(k)) ?? null;
  }
  return activos[0] ?? null;
}

interface Estado {
  titulo: string;
  objetivo: string;
  meta: string;
  publico: Publico;
  mecanica: Mecanica;
  valor: string;
  alcanceTipo: AlcancePromo["tipo"];
  categoria: string;
  coleccion: string;
  codigos: string[];
  condiciones: string;
  desde: string;
  hasta: string;
  mensaje_apertura: string;
  respuesta_promo: string;
  respuesta_fuera: string;
  embudo: "" | "general" | "propio";
  embudo_id: number;
  etapas: string[];
}

const alcanceDe = (w: Estado): AlcancePromo => ({
  tipo: w.alcanceTipo,
  categoria: w.alcanceTipo === "categoria" ? w.categoria : "",
  coleccion: w.alcanceTipo === "coleccion" ? w.coleccion : "",
  codigos: w.alcanceTipo === "prendas" ? [...w.codigos] : [],
});

const datosDe = (w: Estado): PromoDatos => ({
  mecanica: w.mecanica,
  valor: usaValor(w.mecanica) ? Number(w.valor) || 0 : 0,
  alcance: alcanceDe(w),
  condiciones: w.condiciones.trim(),
  mensaje_apertura: w.mensaje_apertura.trim(),
  respuesta_promo: w.respuesta_promo.trim(),
  respuesta_fuera: w.respuesta_fuera.trim(),
  objetivo: w.objetivo.trim(),
  meta: Number(w.meta) || 0,
  publico: w.publico,
  embudo_id: w.embudo === "propio" ? w.embudo_id : 0,
});

/** Lo que falta para pasar del paso n ("" si nada). */
function faltaEn(w: Estado, n: number): string {
  if (n === 1 && !w.titulo.trim()) return "Ponle un nombre a la promo.";
  if (n === 2) {
    const v = Number(w.valor);
    if (usaValor(w.mecanica) && !(v > 0)) return `Falta el valor: ${MECANICAS[w.mecanica].valor.toLowerCase()}.`;
    if (w.mecanica === "porcentaje" && v >= 100) return "El porcentaje tiene que ser menor que 100.";
    if (w.alcanceTipo === "prendas" && w.codigos.length === 0) return "Elige al menos una prenda.";
  }
  if (n === 3 && w.desde && w.hasta && w.hasta < w.desde) return "La fecha «hasta» es anterior a «desde».";
  if (n === 5 && !w.embudo) return "Elige un embudo.";
  if (n === 5 && w.embudo === "propio" && w.etapas.filter((e) => e.trim()).length < 2) return "El embudo necesita al menos dos etapas.";
  return "";
}

interface Props {
  fuente?: Fuente | null;
  onClose$: QRL<() => void>;
  onListo$: QRL<(id: number) => void>;
  onToast$: QRL<(text: string, error?: boolean) => void>;
}

export const PromoAsistente = component$<Props>(({ fuente, onClose$, onListo$, onToast$ }) => {
  const d = fuente?.datos ?? {};
  const w = useStore<Estado>({
    titulo: fuente?.titulo ?? "",
    objetivo: String(d.objetivo ?? ""),
    meta: d.meta ? String(d.meta) : "",
    publico: d.publico ?? "todas",
    mecanica: d.mecanica ?? "porcentaje",
    valor: d.valor ? String(d.valor) : "",
    alcanceTipo: d.alcance?.tipo ?? "todo",
    categoria: d.alcance?.categoria || "vestido",
    coleccion: d.alcance?.coleccion || "vestidos",
    codigos: [...(d.alcance?.codigos ?? [])],
    condiciones: String(d.condiciones ?? ""),
    desde: fuente ? (fuente.vigente_desde ?? "") : hoyLima(),
    hasta: fuente ? (fuente.vigente_hasta ?? "") : hoyLima(13),
    mensaje_apertura: String(d.mensaje_apertura ?? ""),
    respuesta_promo: String(d.respuesta_promo ?? ""),
    respuesta_fuera: String(d.respuesta_fuera ?? ""),
    embudo: d.embudo_id ? "propio" : "",
    embudo_id: Number(d.embudo_id ?? 0),
    etapas: [...ETAPAS_SUGERIDAS],
  });
  const paso = useSignal(1);
  const productos = useSignal<Product[]>([]);
  const buscar = useSignal("");
  const error = useSignal("");
  const busy = useSignal(false);
  const listo = useSignal<number>(0); // id de la promo guardada: pantalla final

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(async () => {
    api<Product[]>("/api/products")
      .then((ps) => (productos.value = ps ?? []))
      .catch(() => {});
    if (w.embudo_id) {
      try {
        const e = (await listarEmbudos()).find((x) => x.id === w.embudo_id);
        if (e?.etapas?.length) w.etapas = [...e.etapas];
      } catch {
        /* sin embudos: quedan las sugeridas */
      }
    }
  });

  const sugerir = $(() => {
    const dd = datosDe(w);
    const que = mecanicaTexto(dd);
    const hasta = w.hasta ? ` hasta el ${new Date(`${w.hasta}T12:00:00`).toLocaleDateString("es-PE", { day: "numeric", month: "long" })}` : "";
    const cond = dd.condiciones ? ` ${dd.condiciones}` : "";
    if (!w.mensaje_apertura.trim()) w.mensaje_apertura = `¡${w.titulo.trim() || "Tenemos promo"}! Esta semana hay ${que} 🎉`;
    if (!w.respuesta_promo.trim()) w.respuesta_promo = `Sí 😊 Tenemos ${que}${hasta}.${cond} ¿Te muestro las prendas?`;
    if (!w.respuesta_fuera.trim())
      w.respuesta_fuera = `Esa prenda no entra en la promo 😊 La promo es ${alcanceTexto(dd.alcance)}. ¿Quieres que te muestre las que sí entran?`;
  });

  const siguiente = $(() => {
    const e = faltaEn(w, paso.value);
    error.value = e;
    if (e) return;
    if (paso.value === 3) sugerir();
    paso.value = Math.min(paso.value + 1, 5);
  });

  const terminar = $(async () => {
    for (let n = 1; n <= 5; n++) {
      const e = faltaEn(w, n);
      if (e) {
        paso.value = n;
        error.value = e;
        return;
      }
    }
    error.value = "";
    busy.value = true;
    try {
      const dd = datosDe(w);
      const titulo = w.titulo.trim();
      // El contenido lo arma el asistente; si alguien lo reescribió a mano en el editor, se respeta.
      const previo = fuente?.contenido ?? "";
      const autogenerado = !previo.trim() || previo.startsWith(`# ${fuente?.titulo ?? ""}\n\n**Promo:**`);
      const cuerpo = (embudoId: number): FuenteEditable => ({
        tipo: "promo",
        titulo,
        contenido: autogenerado ? contenidoPromo(titulo, dd, w.desde, w.hasta) : previo,
        vigente_desde: w.desde,
        vigente_hasta: w.hasta,
        etiquetas: fuente?.etiquetas ?? [],
        datos: { ...(fuente?.datos ?? {}), ...dd, embudo_id: embudoId } as unknown as Record<string, unknown>,
      });
      const etapas = w.etapas.map((e) => e.trim()).filter(Boolean);

      let id = fuente?.id ?? 0;
      let embudoId = w.embudo === "propio" ? w.embudo_id : 0;
      if (id) await guardarFuente(id, cuerpo(embudoId));
      else id = (await crearFuente(cuerpo(0))).id;

      if (w.embudo === "propio") {
        if (embudoId) {
          await guardarEmbudo(embudoId, { nombre: titulo, etapas });
        } else {
          embudoId = (await crearEmbudo({ nombre: titulo, etapas, promo_id: id })).id;
          w.embudo_id = embudoId;
          await guardarFuente(id, cuerpo(embudoId));
        }
      }
      listo.value = id;
      onToast$(fuente ? "Promo guardada" : "Promo creada como borrador");
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      busy.value = false;
    }
  });

  const ejemplo = prendaDeEjemplo(productos.value, alcanceDe(w));
  const pp = ejemplo ? precioPromo(ejemplo.price, w.mecanica, Number(w.valor) || 0) : null;
  const dias = diasVigencia(w.desde, w.hasta);
  const q = sinTildes(buscar.value.trim());
  const visibles = productos.value.filter((p) => p.active && (!q || sinTildes(`${p.code} ${p.name} ${p.category}`).includes(q))).slice(0, 60);
  const yaActiva = fuente?.estado === "activo";

  return (
    <div class="overlay overlay-center" onClick$={() => !busy.value && onClose$()}>
      <div class="modal modal-lg wiz" role="dialog" aria-modal="true" aria-labelledby="wiz-titulo" onClick$={(e) => e.stopPropagation()}>
        <div class="wiz-head">
          <h2 id="wiz-titulo">{fuente ? "Editar promo" : "Diseñar promo"}</h2>
          <button class="btn btn-ghost" aria-label="Cerrar" disabled={busy.value} onClick$={onClose$}>
            ✕
          </button>
        </div>

        {listo.value > 0 ? (
          <div class="wiz-fin">
            <p class="wiz-ok">✓</p>
            <h3>{fuente ? "Promo guardada" : "La promo quedó en borrador"}</h3>
            <p class="muted">
              {yaActiva
                ? "Ya estaba activa: el robot usa los cambios en el próximo mensaje."
                : w.desde && w.desde > hoyLima()
                  ? `Si la activas ahora, queda programada y el robot empieza a ofrecerla el ${vigenciaTexto(w.desde, "").replace("desde ", "")}.`
                  : "El robot todavía no la ofrece. Actívala cuando esté lista."}
              {w.embudo === "propio" && " Su embudo ya aparece en el tablero de pedidos."}
            </p>
            <div class="modal-actions wiz-fin-acc">
              {!yaActiva && (
                <button class="btn" onClick$={() => onListo$(listo.value)}>
                  Dejar en borrador
                </button>
              )}
              {!yaActiva ? (
                <button
                  class="btn btn-primary"
                  disabled={busy.value}
                  onClick$={async () => {
                    busy.value = true;
                    try {
                      await cambiarEstadoFuente(listo.value, "activo");
                      onToast$("Promo activada");
                      await onListo$(listo.value);
                    } catch (e) {
                      error.value = (e as Error).message;
                      busy.value = false;
                    }
                  }}
                >
                  Activar ahora
                </button>
              ) : (
                <button class="btn btn-primary" onClick$={() => onListo$(listo.value)}>
                  Listo
                </button>
              )}
            </div>
            {error.value && <p class="form-error">{error.value}</p>}
          </div>
        ) : (
          <>
            <ol class="wiz-pasos" aria-label="Pasos">
              {PASOS.map((p, i) => (
                <li key={p} class={[paso.value === i + 1 && "actual", paso.value > i + 1 && "hecho"]}>
                  <button
                    type="button"
                    disabled={i + 1 > paso.value}
                    onClick$={() => {
                      error.value = "";
                      paso.value = i + 1;
                    }}
                  >
                    <span class="wiz-num">{paso.value > i + 1 ? "✓" : i + 1}</span>
                    <span class="wiz-lbl">{p}</span>
                  </button>
                </li>
              ))}
            </ol>
            <p class="muted small wiz-de">
              Paso {paso.value} de {PASOS.length}
            </p>

            {paso.value === 1 && (
              <div class="wiz-cuerpo">
                <h3>¿Qué quieres lograr y con quién?</h3>
                <label>
                  Nombre de la promo
                  <input value={w.titulo} onInput$={(_, el) => (w.titulo = el.value)} maxLength={120} placeholder="Promo pelotera" />
                </label>
                <label>
                  Objetivo
                  <input value={w.objetivo} onInput$={(_, el) => (w.objetivo = el.value)} placeholder="Vender 30 vestidos antes de fin de mes" />
                </label>
                <div class="grid2">
                  <label>
                    Meta (número)
                    <input type="number" min={0} value={w.meta} onInput$={(_, el) => (w.meta = el.value)} placeholder="30" />
                  </label>
                  <label>
                    Público
                    <select value={w.publico} onChange$={(_, el) => (w.publico = el.value as Publico)}>
                      {PUBLICOS.map((p) => (
                        <option key={p.key} value={p.key}>
                          {p.label}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <p class="muted small">El público solo cambia qué dice el robot cuando una clienta escribe: no se envía nada a nadie.</p>
              </div>
            )}

            {paso.value === 2 && (
              <div class="wiz-cuerpo">
                <h3>Mecánica y alcance</h3>
                <div class="grid2">
                  <label>
                    Tipo de promo
                    <select value={w.mecanica} onChange$={(_, el) => (w.mecanica = el.value as Mecanica)}>
                      {(Object.keys(MECANICAS) as Mecanica[]).map((k) => (
                        <option key={k} value={k}>
                          {MECANICAS[k].label}
                        </option>
                      ))}
                    </select>
                  </label>
                  {usaValor(w.mecanica) ? (
                    <label>
                      {MECANICAS[w.mecanica].valor}
                      <input type="number" min={0} step="0.01" value={w.valor} onInput$={(_, el) => (w.valor = el.value)} />
                    </label>
                  ) : (
                    <p class="muted small wiz-sin-valor">Sin valor: el beneficio se explica en las condiciones.</p>
                  )}
                </div>
                <div class="lbl">¿A qué prendas aplica?</div>
                <div class="seg wiz-seg">
                  {(
                    [
                      ["todo", "Toda la tienda"],
                      ["categoria", "Una categoría"],
                      ["coleccion", "Una colección"],
                      ["prendas", "Prendas elegidas"],
                    ] as const
                  ).map(([k, l]) => (
                    <button key={k} type="button" class={["seg-btn", w.alcanceTipo === k && "active"]} onClick$={() => (w.alcanceTipo = k)}>
                      {l}
                    </button>
                  ))}
                </div>
                {w.alcanceTipo === "categoria" && (
                  <label>
                    Categoría
                    <select value={w.categoria} onChange$={(_, el) => (w.categoria = el.value)}>
                      {CATEGORIAS.map((c) => (
                        <option key={c} value={c}>
                          {c === "pantalon" ? "pantalón" : c}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {w.alcanceTipo === "coleccion" && (
                  <label>
                    Colección de la web
                    <select value={w.coleccion} onChange$={(_, el) => (w.coleccion = el.value)}>
                      {COLECCIONES.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {w.alcanceTipo === "prendas" && (
                  <div class="wiz-prendas">
                    <div class="row">
                      <input class="search grow" type="search" placeholder="Buscar código o nombre…" bind:value={buscar} />
                      <span class="chip">{w.codigos.length} elegidas</span>
                    </div>
                    {w.codigos.length > 0 && (
                      <div class="tags wiz-elegidas">
                        {w.codigos.map((c) => (
                          <span key={c} class="tag">
                            {c}
                            <button type="button" class="tag-x" aria-label={`Quitar ${c}`} onClick$={() => (w.codigos = w.codigos.filter((x) => x !== c))}>
                              ×
                            </button>
                          </span>
                        ))}
                      </div>
                    )}
                    <div class="wiz-grid">
                      {visibles.map((p) => {
                        const sel = w.codigos.includes(p.code);
                        return (
                          <button
                            key={p.id}
                            type="button"
                            class={["wiz-prenda", sel && "sel"]}
                            aria-pressed={sel}
                            onClick$={() => (w.codigos = sel ? w.codigos.filter((x) => x !== p.code) : [...w.codigos, p.code])}
                          >
                            {p.image ? <img src={u(p.image)} alt="" width={64} height={84} loading="lazy" /> : <span class="wiz-sinfoto">👗</span>}
                            <span class="wiz-prenda-txt">
                              <b>{p.code}</b> {p.name}
                              <span class="muted small">{money(p.price)}</span>
                            </span>
                            {sel && <span class="wiz-check">✓</span>}
                          </button>
                        );
                      })}
                      {visibles.length === 0 && <p class="muted small">No hay prendas que coincidan.</p>}
                    </div>
                  </div>
                )}
                <label>
                  Condiciones
                  <textarea rows={2} value={w.condiciones} onInput$={(_, el) => (w.condiciones = el.value)} placeholder="Solo pago por Yape. Hasta agotar stock." />
                </label>
              </div>
            )}

            {paso.value === 3 && (
              <div class="wiz-cuerpo">
                <h3>¿Cuándo vale?</h3>
                <div class="grid2">
                  <label>
                    Desde
                    <input type="date" value={w.desde} onInput$={(_, el) => (w.desde = el.value)} />
                  </label>
                  <label>
                    Hasta
                    <input type="date" value={w.hasta} min={w.desde || undefined} onInput$={(_, el) => (w.hasta = el.value)} />
                  </label>
                </div>
                <div class="row wrap atajos">
                  {[7, 14, 30].map((n) => (
                    <button
                      key={n}
                      type="button"
                      class="chip-btn"
                      onClick$={() => {
                        if (!w.desde) w.desde = hoyLima();
                        const dd = new Date(`${w.desde}T12:00:00Z`);
                        dd.setUTCDate(dd.getUTCDate() + n - 1);
                        w.hasta = dd.toISOString().slice(0, 10);
                      }}
                    >
                      {n} días
                    </button>
                  ))}
                  <button
                    type="button"
                    class="chip-btn"
                    onClick$={() => {
                      w.desde = "";
                      w.hasta = "";
                    }}
                  >
                    Sin fecha
                  </button>
                </div>
                <p class="wiz-dias">
                  {dias != null ? (
                    dias > 0 ? (
                      <>
                        <b>{dias}</b> {dias === 1 ? "día" : "días"} · {vigenciaTexto(w.desde, w.hasta)}
                      </>
                    ) : (
                      <span class="danger-text">Las fechas están al revés.</span>
                    )
                  ) : (
                    <span class="muted">{w.desde || w.hasta ? vigenciaTexto(w.desde, w.hasta) : "Sin fecha: vale mientras esté activa."}</span>
                  )}
                </p>
                <p class="muted small">Las fechas son de Lima e incluyen el último día. En el inicio del CRM aparece «Vence en N días» cuando se acerque.</p>
              </div>
            )}

            {paso.value === 4 && (
              <div class="wiz-cuerpo wiz-4">
                <div>
                  <div class="panel-head">
                    <h3>Cómo la cuenta el bot</h3>
                    <button type="button" class="btn btn-sm btn-ghost" onClick$={sugerir}>
                      Sugerir textos
                    </button>
                  </div>
                  <label>
                    Mensaje de apertura <span class="muted small">(la primera vez que la menciona)</span>
                    <textarea rows={2} value={w.mensaje_apertura} onInput$={(_, el) => (w.mensaje_apertura = el.value)} />
                  </label>
                  <label>
                    Respuesta a «¿tienen promo?»
                    <textarea rows={3} value={w.respuesta_promo} onInput$={(_, el) => (w.respuesta_promo = el.value)} />
                  </label>
                  <label>
                    Si la prenda que pide no entra en la promo
                    <textarea rows={3} value={w.respuesta_fuera} onInput$={(_, el) => (w.respuesta_fuera = el.value)} />
                  </label>
                </div>
                <aside class="wiz-previa" aria-label="Vista previa">
                  <div class="muted small">Vista previa</div>
                  {ejemplo && pp ? (
                    <div class="wiz-ejemplo">
                      {ejemplo.image && <img src={u(ejemplo.image)} alt="" width={72} height={96} />}
                      <div>
                        <b>
                          {ejemplo.name}
                        </b>
                        <div class="wiz-precio">
                          {pp.precio !== ejemplo.price && <s class="muted">{money(ejemplo.price)}</s>} <b>{money(pp.precio)}</b>
                        </div>
                        <span class="chip ok">{pp.nota}</span>
                      </div>
                    </div>
                  ) : (
                    <p class="muted small">Sin prendas para el ejemplo.</p>
                  )}
                  {w.mensaje_apertura.trim() && <p class="fu-burbuja">{w.mensaje_apertura}</p>}
                  <p class="fu-burbuja in">¿tienen alguna promo?</p>
                  <p class="fu-burbuja">{w.respuesta_promo.trim() || "…"}</p>
                  <p class="muted small">El precio lo calcula el robot con la misma regla; aquí es solo un ejemplo.</p>
                </aside>
              </div>
            )}

            {paso.value === 5 && (
              <div class="wiz-cuerpo">
                <h3>¿Quieres un embudo propio para esta promo o usar el que ya tienes?</h3>
                <div class="wiz-opciones">
                  <button type="button" class={["wiz-opcion", w.embudo === "general" && "sel"]} onClick$={() => (w.embudo = "general")}>
                    <b>Usar el general</b>
                    <span class="muted small">Los pedidos siguen en el tablero de siempre (Consultas → Entregados).</span>
                  </button>
                  <button type="button" class={["wiz-opcion", w.embudo === "propio" && "sel"]} onClick$={() => (w.embudo = "propio")}>
                    <b>Embudo propio</b>
                    <span class="muted small">Un tablero aparte para seguir solo los pedidos de esta promo. El estado del pedido no cambia.</span>
                  </button>
                </div>
                {w.embudo === "propio" && (
                  <div class="wiz-etapas">
                    <div class="lbl">Etapas</div>
                    {w.etapas.map((e, i) => (
                      <div key={i} class="row">
                        <span class="wiz-num">{i + 1}</span>
                        <input
                          value={e}
                          maxLength={40}
                          aria-label={`Etapa ${i + 1}`}
                          onInput$={(_, el) => (w.etapas = w.etapas.map((x, j) => (j === i ? el.value : x)))}
                        />
                        <button
                          type="button"
                          class="btn btn-ghost btn-sm"
                          aria-label="Subir"
                          disabled={i === 0}
                          onClick$={() => {
                            const a = [...w.etapas];
                            [a[i - 1], a[i]] = [a[i], a[i - 1]];
                            w.etapas = a;
                          }}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          class="btn btn-ghost btn-sm"
                          aria-label="Quitar"
                          disabled={w.etapas.length <= 2}
                          onClick$={() => (w.etapas = w.etapas.filter((_, j) => j !== i))}
                        >
                          ✕
                        </button>
                      </div>
                    ))}
                    {w.etapas.length < 8 && (
                      <button type="button" class="btn btn-sm" onClick$={() => (w.etapas = [...w.etapas, ""])}>
                        + Etapa
                      </button>
                    )}
                    <p class="muted small">Un pedido entra solo en la primera etapa cuando nace de una conversación en la que el robot ofreció esta promo.</p>
                  </div>
                )}
              </div>
            )}

            {error.value && <p class="form-error">{error.value}</p>}
            <div class="modal-actions">
              {paso.value > 1 && (
                <button
                  type="button"
                  class="btn btn-ghost push-left"
                  disabled={busy.value}
                  onClick$={() => {
                    error.value = "";
                    paso.value--;
                  }}
                >
                  ← Atrás
                </button>
              )}
              <button type="button" class="btn btn-ghost" disabled={busy.value} onClick$={onClose$}>
                Cancelar
              </button>
              {paso.value < 5 ? (
                <button type="button" class="btn btn-primary" onClick$={siguiente}>
                  Siguiente →
                </button>
              ) : (
                <button type="button" class="btn btn-primary" disabled={busy.value} onClick$={terminar}>
                  {busy.value ? "Guardando…" : fuente ? "Guardar promo" : "Crear promo"}
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
});
