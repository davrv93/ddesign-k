// Secuencias de seguimiento (docs/SEGUIMIENTO_CONTRATO.md §4): editor de pasos, resumen en una línea y vista previa con
// un modelo real. Lo usa la página /seguimiento. Leer es para todo el equipo; crear, editar y borrar, solo admin.
import { $, component$, useSignal, useStore, useVisibleTask$, type QRL } from "@builder.io/qwik";
import {
  api,
  borrarSecuencia,
  crearSecuencia,
  guardarSecuencia,
  listarFuentes,
  plantillasCloud,
  vistaPreviaSecuencia,
  type AlcanceSecuencia,
  type AlResponder,
  type Fuente,
  type FueraDeVentana,
  type PasoSecuencia,
  type PasoVistaPrevia,
  type PlantillaCloud,
  type Product,
  type Secuencia,
  type SecuenciaEditable,
  type TipoPaso,
  subirImagenPaso,
} from "~/lib/api";
import { esperaTexto } from "~/lib/format";
import { u } from "~/lib/base";
import { Confirmar } from "~/components/confirmar";

export const MAX_PASOS = 12;
export const ESPERA_MAX = 20160; // 14 días

export const TIPOS_PASO: Record<TipoPaso, { label: string; hint: string }> = {
  emoji: { label: "Emoji", hint: "Un emoji suelto para reabrir la charla (🥰, 👀…)." },
  texto: { label: "Texto", hint: "Un mensaje que escribes tú." },
  beneficio: { label: "Beneficio", hint: "El beneficio del modelo que vio. Si el modelo no lo tiene, el paso se salta." },
  testimonio: { label: "Testimonio", hint: "El testimonio del modelo que vio (texto o foto). Si no tiene, se salta." },
  tip: { label: "Tip", hint: "Cómo combinarlo. Si el modelo no tiene tip, se salta." },
  oferta: { label: "Oferta", hint: "La promo elegida. Si no está vigente o no cubre el modelo, se salta." },
};

export const FUERA_DE_VENTANA: Record<FueraDeVentana, string> = {
  saltar: "No enviar ese paso",
  enviar: "Enviar igual (solo WhatsApp por QR)",
  plantilla: "Usar una plantilla aprobada (solo API oficial)",
  numero_secundario: "Enviar desde un número de seguimiento",
};

export const AL_RESPONDER: Record<AlResponder, string> = {
  continuar: "Seguir con el siguiente paso si vuelve a callarse",
  reiniciar: "Volver al paso 1 si vuelve a callarse",
  detener: "Detener el seguimiento",
};

const ALCANCES: Record<AlcanceSecuencia, string> = {
  todas: "Todos los modelos",
  categoria: "Una categoría",
  productos: "Modelos elegidos",
};

/** «1 h → emoji · 5 h → beneficio · 3 días → oferta». */
export const resumenPasos = (pasos: PasoSecuencia[] | null | undefined) =>
  (pasos ?? []).map((p) => `${esperaTexto(p.espera_min)} → ${p.tipo}`).join(" · ");

export const alcanceTexto = (s: Pick<Secuencia, "alcance" | "alcance_valor">) =>
  s.alcance === "categoria"
    ? `Categoría: ${s.alcance_valor || "—"}`
    : s.alcance === "productos"
      ? `Modelos: ${s.alcance_valor.split(",").filter(Boolean).join(", ") || "—"}`
      : "Todos los modelos";

type Unidad = "min" | "h" | "d";
const FACTOR: Record<Unidad, number> = { min: 1, h: 60, d: 1440 };
const UNIDADES: Record<Unidad, string> = { min: "minutos", h: "horas", d: "días" };

interface PasoUI extends PasoSecuencia {
  cantidad: number;
  unidad: Unidad;
}

const aUI = (p: PasoSecuencia): PasoUI => {
  const m = Math.max(1, Math.round(p.espera_min || 60));
  const unidad: Unidad = m % 1440 === 0 ? "d" : m % 60 === 0 ? "h" : "min";
  return { ...p, texto: p.texto ?? "", plantilla: p.plantilla ?? "", promo_id: p.promo_id ?? 0, media_url: p.media_url ?? "",
    cantidad: m / FACTOR[unidad], unidad };
};

const pasoNuevo = (): PasoUI => ({ espera_min: 300, tipo: "texto", texto: "", promo_id: 0, plantilla: "", media_url: "", cantidad: 5, unidad: "h" });

interface FormSecuencia {
  nombre: string;
  alcance: AlcanceSecuencia;
  alcance_valor: string;
  fuera_de_ventana: FueraDeVentana;
  al_responder: AlResponder;
  marcar_perdida: boolean;
  pasos: PasoUI[];
}

/** Lo que falta para poder guardar (las mismas reglas que el backend), o "". */
function validar(f: FormSecuencia): string {
  if (!f.nombre.trim()) return "Ponle un nombre a la secuencia.";
  if (f.pasos.length === 0) return "Añade al menos un paso.";
  if (f.pasos.length > MAX_PASOS) return `Como máximo ${MAX_PASOS} pasos.`;
  if (f.alcance === "categoria" && !f.alcance_valor.trim()) return "Elige la categoría.";
  if (f.alcance === "productos" && !f.alcance_valor.trim()) return "Elige al menos un modelo.";
  for (let i = 0; i < f.pasos.length; i++) {
    const p = f.pasos[i];
    if (!(p.espera_min >= 1 && p.espera_min <= ESPERA_MAX)) return `Paso ${i + 1}: la espera va de 1 minuto a 14 días.`;
    if ((p.tipo === "emoji" || p.tipo === "texto") && !p.texto.trim() && !p.media_url)
      return `Paso ${i + 1}: escribe el mensaje o sube una imagen.`;
    if (p.tipo === "oferta" && !p.promo_id) return `Paso ${i + 1}: elige la promo de la oferta.`;
  }
  return "";
}

const aEditable = (f: FormSecuencia): SecuenciaEditable => ({
  nombre: f.nombre.trim(),
  alcance: f.alcance,
  alcance_valor: f.alcance === "todas" ? "" : f.alcance_valor.trim(),
  fuera_de_ventana: f.fuera_de_ventana,
  al_responder: f.al_responder,
  marcar_perdida: f.marcar_perdida,
  pasos: f.pasos.map((p) => ({
    espera_min: p.espera_min,
    tipo: p.tipo,
    texto: p.texto.trim(),
    promo_id: p.tipo === "oferta" ? Number(p.promo_id) || 0 : 0,
    plantilla: f.fuera_de_ventana === "plantilla" ? p.plantilla : "",
    media_url: p.tipo === "emoji" || p.tipo === "texto" ? p.media_url || "" : "",
  })),
});

// ---------------------------------------------------------------------------

interface EditorProps {
  secuencia: Secuencia | null;
  admin: boolean;
  onClose$: QRL<() => void>;
  onSaved$: QRL<(s: Secuencia | null) => Promise<void>>;
}

export const SecuenciaEditor = component$<EditorProps>(({ secuencia, admin, onClose$, onSaved$ }) => {
  const x = secuencia;
  const f = useStore<FormSecuencia>(
    {
      nombre: x?.nombre ?? "",
      alcance: x?.alcance ?? "todas",
      alcance_valor: x?.alcance_valor ?? "",
      fuera_de_ventana: x?.fuera_de_ventana ?? "saltar",
      al_responder: x?.al_responder ?? "continuar",
      marcar_perdida: x?.marcar_perdida ?? false,
      pasos: x ? (x.pasos ?? []).map(aUI) : [pasoNuevo()],
    },
    { deep: true },
  );

  const productos = useSignal<Product[]>([]);
  const promos = useSignal<Fuente[]>([]);
  const plantillas = useSignal<PlantillaCloud[]>([]);
  const busy = useSignal(false);
  const error = useSignal("");
  const cambiado = useSignal(false);
  const borrando = useSignal(false);
  const buscaModelo = useSignal("");
  const soloLectura = !admin;

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(({ cleanup }) => {
    api<Product[]>("/api/products")
      .then((r) => (productos.value = r ?? []))
      .catch(() => {});
    listarFuentes({ tipo: "promo" })
      .then((r) => (promos.value = r.filter((p) => p.estado !== "descartada" && p.estado !== "propuesta")))
      .catch(() => {});
    plantillasCloud()
      .then((r) => (plantillas.value = r))
      .catch(() => (plantillas.value = []));
    const tecla = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !borrando.value) onClose$();
    };
    window.addEventListener("keydown", tecla);
    cleanup(() => window.removeEventListener("keydown", tecla));
  });

  const tocar = $(() => {
    cambiado.value = true;
    error.value = "";
  });

  const guardar = $(async () => {
    const falta = validar(f);
    if (falta) {
      error.value = falta;
      return;
    }
    busy.value = true;
    error.value = "";
    try {
      const body = aEditable(f);
      const s = x ? await guardarSecuencia(x.id, body) : await crearSecuencia(body);
      cambiado.value = false;
      await onSaved$(s);
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      busy.value = false;
    }
  });

  const categorias = [...new Set([...productos.value.map((p) => p.category).filter(Boolean), f.alcance === "categoria" ? f.alcance_valor : ""])]
    .filter(Boolean)
    .sort();
  const elegidos = new Set(f.alcance_valor.split(",").map((c) => c.trim()).filter(Boolean));
  const qm = buscaModelo.value.trim().toLowerCase();
  const modelos = productos.value.filter((p) => elegidos.has(p.code) || (p.active && (!qm || `${p.code} ${p.name}`.toLowerCase().includes(qm))));
  const resumen = resumenPasos(f.pasos);

  return (
    <>
      <div class="overlay" onClick$={onClose$}>
        <aside class="drawer drawer-lg" role="dialog" aria-modal="true" aria-labelledby="sec-titulo" onClick$={(e) => e.stopPropagation()}>
          <header class="drawer-head">
            <div>
              <h2 id="sec-titulo">{x ? x.nombre || "Sin nombre" : "Nueva secuencia"}</h2>
              <div class="row wrap fu-badges">
                {x && (
                  <span class="pill" style={{ "--col": x.activa ? "var(--ok)" : "var(--muted)" }}>
                    {x.activa ? "Activa" : "Apagada"}
                  </span>
                )}
                {resumen && <span class="muted small">{resumen}</span>}
              </div>
            </div>
            <button class="btn btn-ghost" aria-label="Cerrar" onClick$={onClose$}>
              ✕
            </button>
          </header>

          <section class="drawer-sec">
            <label>
              Nombre
              <input
                value={f.nombre}
                disabled={soloLectura}
                placeholder="Baruka · vestidos"
                onInput$={(_, el) => {
                  f.nombre = el.value;
                  tocar();
                }}
              />
            </label>
            <label>
              ¿A qué modelos aplica?
              <select
                value={f.alcance}
                disabled={soloLectura}
                onChange$={(_, el) => {
                  f.alcance = el.value as AlcanceSecuencia;
                  f.alcance_valor = "";
                  tocar();
                }}
              >
                {(Object.keys(ALCANCES) as AlcanceSecuencia[]).map((k) => (
                  <option key={k} value={k}>
                    {ALCANCES[k]}
                  </option>
                ))}
              </select>
            </label>
            {f.alcance === "categoria" && (
              <label>
                Categoría
                <select
                  value={f.alcance_valor}
                  disabled={soloLectura}
                  onChange$={(_, el) => {
                    f.alcance_valor = el.value;
                    tocar();
                  }}
                >
                  <option value="">Elige una categoría…</option>
                  {categorias.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {f.alcance === "productos" && (
              <fieldset class="seg-modelos">
                <legend>Modelos ({elegidos.size})</legend>
                <input
                  class="search"
                  type="search"
                  placeholder="Buscar modelo…"
                  aria-label="Buscar modelo"
                  value={buscaModelo.value}
                  onInput$={(_, el) => (buscaModelo.value = el.value)}
                />
                <div class="chips">
                  {modelos.slice(0, 60).map((p) => {
                    const sel = elegidos.has(p.code);
                    return (
                      <button
                        key={p.id}
                        type="button"
                        class={["chip-btn", sel && "active"]}
                        aria-pressed={sel}
                        disabled={soloLectura}
                        title={p.name}
                        onClick$={() => {
                          const s = new Set(f.alcance_valor.split(",").map((c) => c.trim()).filter(Boolean));
                          if (sel) s.delete(p.code);
                          else s.add(p.code);
                          f.alcance_valor = [...s].join(",");
                          tocar();
                        }}
                      >
                        {p.code} · {p.name}
                      </button>
                    );
                  })}
                </div>
              </fieldset>
            )}
            <p class="muted small">Si un modelo entra en varias secuencias activas, gana la más específica: modelos elegidos, luego categoría, luego todos.</p>
          </section>

          <section class="drawer-sec">
            <div class="panel-head">
              <h3>
                Pasos ({f.pasos.length} de {MAX_PASOS})
              </h3>
            </div>
            <p class="muted small">La espera cuenta desde el paso anterior; la del primero, desde que la clienta dejó de responder.</p>
            <ol class="pasos">
              {f.pasos.map((p, i) => {
                const promo = promos.value.find((x) => x.id === Number(p.promo_id));
                return (
                  <li key={i} class="paso">
                    <div class="paso-top">
                      <span class="paso-num" aria-hidden="true">
                        {i + 1}
                      </span>
                      <strong class="grow">
                        Paso {i + 1} · {esperaTexto(p.espera_min)} → {TIPOS_PASO[p.tipo]?.label.toLowerCase() ?? p.tipo}
                      </strong>
                      {!soloLectura && (
                        <span class="paso-acc">
                          <button
                            type="button"
                            class="btn btn-ghost btn-sm"
                            aria-label={`Subir el paso ${i + 1}`}
                            disabled={i === 0}
                            onClick$={() => {
                              const [m] = f.pasos.splice(i, 1);
                              f.pasos.splice(i - 1, 0, m);
                              tocar();
                            }}
                          >
                            ↑
                          </button>
                          <button
                            type="button"
                            class="btn btn-ghost btn-sm"
                            aria-label={`Bajar el paso ${i + 1}`}
                            disabled={i === f.pasos.length - 1}
                            onClick$={() => {
                              const [m] = f.pasos.splice(i, 1);
                              f.pasos.splice(i + 1, 0, m);
                              tocar();
                            }}
                          >
                            ↓
                          </button>
                          <button
                            type="button"
                            class="btn btn-ghost btn-sm btn-borrar"
                            aria-label={`Quitar el paso ${i + 1}`}
                            onClick$={() => {
                              f.pasos.splice(i, 1);
                              tocar();
                            }}
                          >
                            ✕
                          </button>
                        </span>
                      )}
                    </div>
                    <div class="paso-campos">
                      <div class="paso-espera">
                        <label>
                          Espera
                          <input
                            type="number"
                            min="1"
                            step="1"
                            class="num"
                            value={p.cantidad}
                            disabled={soloLectura}
                            onInput$={(_, el) => {
                              const n = Math.max(0, Number(el.value) || 0);
                              f.pasos[i].cantidad = n;
                              f.pasos[i].espera_min = Math.round(n * FACTOR[f.pasos[i].unidad]);
                              tocar();
                            }}
                          />
                        </label>
                        <label>
                          <span class="sr-only">Unidad de la espera del paso {i + 1}</span>
                          <select
                            value={p.unidad}
                            disabled={soloLectura}
                            onChange$={(_, el) => {
                              f.pasos[i].unidad = el.value as Unidad;
                              f.pasos[i].espera_min = Math.round(f.pasos[i].cantidad * FACTOR[f.pasos[i].unidad]);
                              tocar();
                            }}
                          >
                            {(Object.keys(UNIDADES) as Unidad[]).map((k) => (
                              <option key={k} value={k}>
                                {UNIDADES[k]}
                              </option>
                            ))}
                          </select>
                        </label>
                      </div>
                      <label>
                        Tipo
                        <select
                          value={p.tipo}
                          disabled={soloLectura}
                          onChange$={(_, el) => {
                            f.pasos[i].tipo = el.value as TipoPaso;
                            tocar();
                          }}
                        >
                          {(Object.keys(TIPOS_PASO) as TipoPaso[]).map((k) => (
                            <option key={k} value={k}>
                              {TIPOS_PASO[k].label}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                    {(p.espera_min < 1 || p.espera_min > ESPERA_MAX) && <p class="form-error small">La espera va de 1 minuto a 14 días.</p>}
                    <p class="muted small">{TIPOS_PASO[p.tipo]?.hint}</p>
                    {p.tipo === "oferta" && (
                      <label>
                        Promo
                        <select
                          value={String(p.promo_id || "")}
                          disabled={soloLectura}
                          onChange$={(_, el) => {
                            f.pasos[i].promo_id = Number(el.value) || 0;
                            tocar();
                          }}
                        >
                          <option value="">Elige una promo…</option>
                          {promos.value.map((pr) => (
                            <option key={pr.id} value={String(pr.id)}>
                              {`${pr.titulo}${pr.vigente ? "" : " (no vigente)"}`}
                            </option>
                          ))}
                        </select>
                        {promos.value.length === 0 && (
                          <span class="muted small block">
                            No hay promos. Créalas en <a href={u("/fuentes?tab=promo")}>Fuentes → Promos</a>.
                          </span>
                        )}
                        {promo && !promo.vigente && <span class="chip warn">Esta promo no está vigente: el paso se saltará</span>}
                      </label>
                    )}
                    <label>
                      {p.tipo === "emoji" ? "Emoji" : p.tipo === "texto" ? "Mensaje" : "Introducción (opcional)"}
                      {p.tipo === "emoji" ? (
                        <input
                          value={p.texto}
                          maxLength={16}
                          placeholder="🥰"
                          disabled={soloLectura}
                          onInput$={(_, el) => {
                            f.pasos[i].texto = el.value;
                            tocar();
                          }}
                        />
                      ) : (
                        <textarea
                          rows={2}
                          value={p.texto}
                          disabled={soloLectura}
                          placeholder={p.tipo === "texto" ? "¿Pudiste ver el modelo? Si quieres te cuento más 😊" : "Por ejemplo: Te cuento algo de este modelo 👇"}
                          onInput$={(_, el) => {
                            f.pasos[i].texto = el.value;
                            tocar();
                          }}
                        />
                      )}
                    </label>
                    {(p.tipo === "emoji" || p.tipo === "texto") && (
                      <div class="paso-imagen">
                        <span class="small">Imagen o sticker (opcional)</span>
                        {p.media_url ? (
                          <div class="row">
                            <img src={u(p.media_url)} alt="Imagen del paso" width={96} height={96} style={{ objectFit: "contain", borderRadius: "8px" }} />
                            {!soloLectura && (
                              <button
                                type="button"
                                class="btn btn-sm btn-ghost"
                                onClick$={() => {
                                  f.pasos[i].media_url = "";
                                  tocar();
                                }}
                              >
                                Quitar imagen
                              </button>
                            )}
                          </div>
                        ) : (
                          !soloLectura && (
                            <input
                              type="file"
                              accept="image/png,image/jpeg,image/webp,image/gif"
                              aria-label={`Imagen del paso ${i + 1}`}
                              onChange$={async (_, el) => {
                                const archivo = el.files?.[0];
                                if (!archivo) return;
                                try {
                                  const r = await subirImagenPaso(archivo);
                                  f.pasos[i].media_url = r.media_url;
                                  tocar();
                                } catch (e) {
                                  error.value = (e as Error).message;
                                }
                              }}
                            />
                          )
                        )}
                      </div>
                    )}
                    {f.fuera_de_ventana === "plantilla" && (
                      <label>
                        Plantilla fuera de las 24 h
                        {plantillas.value.length > 0 ? (
                          <select
                            value={p.plantilla}
                            disabled={soloLectura}
                            onChange$={(_, el) => {
                              f.pasos[i].plantilla = el.value;
                              tocar();
                            }}
                          >
                            <option value="">Sin plantilla (el paso se salta fuera de la ventana)</option>
                            {plantillas.value.map((t) => (
                              <option key={`${t.name}-${t.language}`} value={t.name}>
                                {`${t.name} · ${t.language}${t.status && t.status !== "APPROVED" ? ` (${t.status.toLowerCase()})` : ""}`}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <span class="muted small block">
                            No hay plantillas: solo existen con la API oficial de WhatsApp configurada. Sin ella, el paso fuera de la ventana se salta.
                          </span>
                        )}
                      </label>
                    )}
                  </li>
                );
              })}
            </ol>
            {!soloLectura && (
              <button
                type="button"
                class="btn btn-sm"
                disabled={f.pasos.length >= MAX_PASOS}
                onClick$={() => {
                  f.pasos.push(pasoNuevo());
                  tocar();
                }}
              >
                + Paso
              </button>
            )}
          </section>

          <section class="drawer-sec">
            <h3>Reglas</h3>
            <label>
              Si pasaron más de 24 h desde su último mensaje
              <select
                value={f.fuera_de_ventana}
                disabled={soloLectura}
                onChange$={(_, el) => {
                  f.fuera_de_ventana = el.value as FueraDeVentana;
                  tocar();
                }}
              >
                {(Object.keys(FUERA_DE_VENTANA) as FueraDeVentana[]).map((k) => (
                  <option key={k} value={k}>
                    {FUERA_DE_VENTANA[k]}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Si la clienta responde
              <select
                value={f.al_responder}
                disabled={soloLectura}
                onChange$={(_, el) => {
                  f.al_responder = el.value as AlResponder;
                  tocar();
                }}
              >
                {(Object.keys(AL_RESPONDER) as AlResponder[]).map((k) => (
                  <option key={k} value={k}>
                    {AL_RESPONDER[k]}
                  </option>
                ))}
              </select>
            </label>
            <label class="check">
              <input
                type="checkbox"
                checked={f.marcar_perdida}
                disabled={soloLectura}
                onChange$={(_, el) => {
                  f.marcar_perdida = el.checked;
                  tocar();
                }}
              />{" "}
              Al terminar sin respuesta, pasar la clienta a «Perdida»
            </label>
          </section>

          {x && <VistaPrevia secuenciaId={x.id} productos={productos.value} cambiado={cambiado.value} />}

          {error.value && (
            <p class="form-error pad" role="alert">
              {error.value}
            </p>
          )}

          {admin ? (
            <footer class="drawer-foot fu-acciones">
              {x ? (
                <button class="btn btn-danger btn-sm" disabled={busy.value} onClick$={() => (borrando.value = true)}>
                  Borrar
                </button>
              ) : (
                <span class="muted small">Se crea apagada.</span>
              )}
              <div class="row wrap fu-acciones-der">
                <button class="btn btn-ghost" onClick$={onClose$}>
                  Cancelar
                </button>
                <button class="btn btn-primary" disabled={busy.value || (!!x && !cambiado.value)} onClick$={guardar}>
                  {busy.value ? "Guardando…" : "Guardar"}
                </button>
              </div>
            </footer>
          ) : (
            <footer class="drawer-foot">
              <span class="muted small">Solo una admin puede cambiar las secuencias.</span>
            </footer>
          )}
        </aside>
      </div>
      {borrando.value && x && (
        <Confirmar
          titulo="¿Borrar esta secuencia?"
          texto={`«${x.nombre}» deja de enviarse. Las conversaciones que la seguían se detienen. No se puede deshacer.`}
          confirmar="Borrar secuencia"
          peligro
          onCancelar$={() => (borrando.value = false)}
          onConfirmar$={async () => {
            await borrarSecuencia(x.id);
            borrando.value = false;
            await onSaved$(null);
          }}
        />
      )}
    </>
  );
});

// ---------------------------------------------------------------------------
// Vista previa: lo que recibiría una clienta que vio un modelo concreto, paso a paso, como burbujas de WhatsApp. Los
// pasos que se saltarían salen en gris con el motivo.

const VistaPrevia = component$<{ secuenciaId: number; productos: Product[]; cambiado: boolean }>(({ secuenciaId, productos, cambiado }) => {
  const codigo = useSignal("");
  const pasos = useSignal<PasoVistaPrevia[] | null>(null);
  const busy = useSignal(false);
  const error = useSignal("");

  const ver = $(async () => {
    if (!codigo.value) return;
    busy.value = true;
    error.value = "";
    try {
      pasos.value = await vistaPreviaSecuencia(secuenciaId, codigo.value);
    } catch (e) {
      error.value = (e as Error).message;
      pasos.value = null;
    } finally {
      busy.value = false;
    }
  });

  const activos = productos.filter((p) => p.active);

  return (
    <section class="drawer-sec">
      <h3>Vista previa</h3>
      <p class="muted small">
        Elige un modelo para ver qué recibiría una clienta que lo vio y dejó de responder.
        {cambiado && " Muestra la versión guardada: guarda antes para ver tus cambios."}
      </p>
      <form class="row" preventdefault:submit onSubmit$={ver}>
        <select
          class="grow"
          aria-label="Modelo para la vista previa"
          value={codigo.value}
          onChange$={(_, el) => {
            codigo.value = el.value;
            ver();
          }}
        >
          <option value="">Elige un modelo…</option>
          {activos.map((p) => (
            <option key={p.id} value={p.code}>
              {`${p.code} · ${p.name}`}
            </option>
          ))}
        </select>
        <button class="btn btn-sm" disabled={!codigo.value || busy.value}>
          {busy.value ? "…" : "Ver"}
        </button>
      </form>
      {error.value && <p class="form-error small">{error.value}</p>}
      {pasos.value && pasos.value.length === 0 && <p class="muted small">Esta secuencia no tiene pasos.</p>}
      {pasos.value && pasos.value.length > 0 && (
        <ol class="previa-pasos">
          {pasos.value.map((p) => (
            <li key={p.paso} class={["previa-paso", p.omitido && "omitido"]}>
              <span class="muted small">
                Paso {p.paso} · tras {esperaTexto(p.espera_min)} · {TIPOS_PASO[p.tipo as TipoPaso]?.label.toLowerCase() ?? p.tipo}
              </span>
              {p.omitido ? (
                <p class="previa-motivo small">Se salta: {p.motivo || "sin contenido para este modelo"}</p>
              ) : (
                <div class="fu-burbuja previa-burbuja">
                  {p.media_url && <img src={u(p.media_url)} alt="" width={200} height={260} loading="lazy" />}
                  {p.texto && <span>{p.texto}</span>}
                </div>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
});
