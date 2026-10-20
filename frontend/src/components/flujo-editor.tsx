// Editor visual de flujos de seguimiento (20-10-2026): lienzo con zoom (rueda, botones, pellizco), desplazamiento
// (arrastrar el fondo), nodos que se mueven y se conectan arrastrando desde su salida hasta otro nodo, deshacer y un
// inspector para editar el nodo elegido. El grafo vive en `estado.g` (lo guarda el editor de secuencias); cada cambio
// avisa con onCambio$. Contrato del grafo: docs/SEGUIMIENTO_CONTRATO.md §4b.
import { $, component$, useSignal, useStore, useVisibleTask$, type QRL } from "@builder.io/qwik";
import "~/flujos.css";
import { subirImagenPaso, type Fuente, type PlantillaCloud, type Product, type TipoPaso } from "~/lib/api";
import { esperaTexto } from "~/lib/format";
import { u } from "~/lib/base";
import {
  ANCHO_NODO,
  CONDICIONES,
  ETAPAS_FLUJO,
  NODOS,
  Y_PUERTO,
  ZOOM_MAX,
  ZOOM_MIN,
  borrarNodo,
  conectar,
  curva,
  duplicarNodo,
  encuadrar,
  nodoNuevo,
  ordenar,
  resumenNodo,
  salidasDe,
  yPuerto,
  type Condicion,
  type Grafo,
  type Nodo,
  type Salida,
  type TipoNodo,
} from "~/lib/flujos";

export interface EstadoFlujo {
  g: Grafo;
  sel: string; // id del nodo elegido ("" = ninguno)
  selArista: number; // índice de la conexión elegida (-1 = ninguna)
  resaltados: string[]; // nodos que recorrió la última simulación
  aviso: string;
}

interface Props {
  estado: EstadoFlujo;
  soloLectura: boolean;
  onCambio$: QRL<() => void>;
  productos: Product[];
  promos: Fuente[];
  plantillas: PlantillaCloud[];
  usaPlantillas: boolean;
  secuencias: { id: number; nombre: string; esFlujo: boolean }[];
  secuenciaId: number;
}

const TIPOS_MSG: Record<TipoPaso, string> = {
  emoji: "Emoji",
  texto: "Texto",
  beneficio: "Beneficio del modelo",
  testimonio: "Testimonio del modelo",
  tip: "Tip del modelo",
  oferta: "Oferta (promo)",
};
const AGREGAR: TipoNodo[] = ["esperar", "mensaje", "condicion", "tarea", "etapa", "ir_a", "fin"];
const MAX_HISTORIA = 50;

export const FlujoEditor = component$<Props>((props) => {
  const { estado } = props;
  const lienzo = useSignal<HTMLDivElement>();
  const historia = useStore<{ pila: string[] }>({ pila: [] });
  const tmp = useStore<{ linea: string }>({ linea: "" });
  const tam = useStore({ w: 900, h: 520 });

  const guardarHistoria = $(() => {
    historia.pila.push(JSON.stringify(estado.g));
    if (historia.pila.length > MAX_HISTORIA) historia.pila.splice(0, historia.pila.length - MAX_HISTORIA);
  });

  const cambio = $(async () => {
    estado.resaltados = [];
    await props.onCambio$();
  });

  const zoomA = $((z: number, cx?: number, cy?: number) => {
    const v = estado.g.vista ?? (estado.g.vista = { x: 40, y: 40, zoom: 1 });
    const nz = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
    const px = cx ?? tam.w / 2;
    const py = cy ?? tam.h / 2;
    v.x = px - ((px - v.x) * nz) / v.zoom;
    v.y = py - ((py - v.y) * nz) / v.zoom;
    v.zoom = nz;
  });

  const ajustar = $(() => {
    estado.g.vista = encuadrar(estado.g, tam.w, tam.h);
  });

  const agregar = $(async (tipo: TipoNodo) => {
    if (props.soloLectura) return;
    await guardarHistoria();
    const v = estado.g.vista ?? { x: 40, y: 40, zoom: 1 };
    const sel = estado.g.nodos.find((n) => n.id === estado.sel);
    const x = sel ? sel.x + 290 : (tam.w / 2 - v.x) / v.zoom - ANCHO_NODO / 2;
    const y = sel ? sel.y : (tam.h / 2 - v.y) / v.zoom - 40;
    const n = nodoNuevo(tipo, x, y);
    estado.g.nodos.push(n);
    // Tras el nodo elegido: el nuevo va en su salida libre; si ya estaba conectada, se inserta en medio
    // (elegido → nuevo → el que seguía), que es lo que se busca al «agregar un paso después de este».
    if (sel) {
      const salidas = salidasDe(sel.tipo);
      const libre = salidas.find((s) => !estado.g.aristas.some((a) => a.desde === sel.id && a.salida === s));
      if (libre) conectar(estado.g, sel.id, libre, n.id);
      else if (salidas.length) {
        const viejo = estado.g.aristas.find((a) => a.desde === sel.id && a.salida === salidas[0])?.hasta;
        conectar(estado.g, sel.id, salidas[0], n.id);
        const sal = salidasDe(n.tipo)[0];
        if (viejo && sal) conectar(estado.g, n.id, sal, viejo);
        // lo que seguía se corre a la derecha para que no quede debajo
        const corre = (id: string, visto = new Set<string>()) => {
          if (visto.has(id)) return;
          visto.add(id);
          const m = estado.g.nodos.find((x) => x.id === id);
          if (m) m.x += 290;
          estado.g.aristas.filter((a) => a.desde === id).forEach((a) => corre(a.hasta, visto));
        };
        if (viejo) corre(viejo);
      }
    }
    estado.sel = n.id;
    estado.selArista = -1;
    await cambio();
  });

  const borrarSeleccion = $(async () => {
    if (props.soloLectura) return;
    if (estado.selArista >= 0) {
      await guardarHistoria();
      estado.g.aristas.splice(estado.selArista, 1);
      estado.selArista = -1;
      await cambio();
      return;
    }
    const n = estado.g.nodos.find((x) => x.id === estado.sel);
    if (!n || n.tipo === "inicio") return;
    await guardarHistoria();
    borrarNodo(estado.g, n.id);
    estado.sel = "";
    await cambio();
  });

  const deshacer = $(async () => {
    const prev = historia.pila.pop();
    if (!prev) return;
    const vista = estado.g.vista;
    estado.g = { ...JSON.parse(prev), vista };
    estado.sel = "";
    estado.selArista = -1;
    await cambio();
  });

  const ordenarTodo = $(async () => {
    if (props.soloLectura) return;
    await guardarHistoria();
    ordenar(estado.g);
    estado.g.vista = encuadrar(estado.g, tam.w, tam.h);
    await cambio();
  });

  // Ratón y teclado: todo lo que necesita seguir al puntero va aquí (cerraduras de JS normales, no QRL por evento).
  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(({ cleanup }) => {
    const el = lienzo.value;
    if (!el) return;
    const medir = () => {
      const r = el.getBoundingClientRect();
      tam.w = r.width;
      tam.h = r.height;
    };
    medir();
    const ro = new ResizeObserver(medir);
    ro.observe(el);
    if (!estado.g.vista || estado.g.vista.zoom <= 0) estado.g.vista = encuadrar(estado.g, tam.w, tam.h);

    type Modo =
      | { tipo: "pan"; x: number; y: number; vx: number; vy: number }
      | { tipo: "nodo"; id: string; x: number; y: number; nx: number; ny: number; movido: boolean }
      | { tipo: "conectar"; desde: string; salida: Salida; x1: number; y1: number };
    let modo: Modo | null = null;

    const mundo = (cx: number, cy: number) => {
      const r = el.getBoundingClientRect();
      const v = estado.g.vista!;
      return { x: (cx - r.left - v.x) / v.zoom, y: (cy - r.top - v.y) / v.zoom };
    };

    const abajo = (e: PointerEvent) => {
      if (e.button !== 0) return;
      const t = e.target as HTMLElement;
      if (t.closest("button, input, textarea, select, a, .fl-arista-hit")) return;
      const puerto = t.closest<HTMLElement>("[data-salida]");
      if (puerto && !props.soloLectura) {
        const id = puerto.dataset.salida!;
        const salida = puerto.dataset.nombre as Salida;
        const n = estado.g.nodos.find((x) => x.id === id);
        if (!n) return;
        modo = { tipo: "conectar", desde: id, salida, x1: n.x + ANCHO_NODO, y1: n.y + yPuerto(salida) };
        el.setPointerCapture(e.pointerId);
        e.preventDefault();
        return;
      }
      const nodoEl = t.closest<HTMLElement>("[data-nodo]");
      if (nodoEl) {
        const id = nodoEl.dataset.nodo!;
        const n = estado.g.nodos.find((x) => x.id === id);
        estado.sel = id;
        estado.selArista = -1;
        if (n && !props.soloLectura) {
          modo = { tipo: "nodo", id, x: e.clientX, y: e.clientY, nx: n.x, ny: n.y, movido: false };
          el.setPointerCapture(e.pointerId);
        }
        return;
      }
      estado.sel = "";
      estado.selArista = -1;
      const v = estado.g.vista!;
      modo = { tipo: "pan", x: e.clientX, y: e.clientY, vx: v.x, vy: v.y };
      el.setPointerCapture(e.pointerId);
      el.classList.add("moviendo");
    };

    const mover = (e: PointerEvent) => {
      if (!modo) return;
      const v = estado.g.vista!;
      if (modo.tipo === "pan") {
        v.x = modo.vx + (e.clientX - modo.x);
        v.y = modo.vy + (e.clientY - modo.y);
      } else if (modo.tipo === "nodo") {
        const n = estado.g.nodos.find((x) => x.id === (modo as { id: string }).id);
        if (!n) return;
        const dx = (e.clientX - modo.x) / v.zoom;
        const dy = (e.clientY - modo.y) / v.zoom;
        if (!modo.movido && Math.abs(dx) + Math.abs(dy) < 3) return;
        if (!modo.movido) {
          historia.pila.push(JSON.stringify(estado.g));
          if (historia.pila.length > MAX_HISTORIA) historia.pila.shift();
        }
        modo.movido = true;
        n.x = modo.nx + dx;
        n.y = modo.ny + dy;
      } else {
        const p = mundo(e.clientX, e.clientY);
        tmp.linea = curva(modo.x1, modo.y1, p.x, p.y);
      }
    };

    const arriba = (e: PointerEvent) => {
      if (!modo) return;
      const m = modo;
      modo = null;
      el.classList.remove("moviendo");
      try {
        el.releasePointerCapture(e.pointerId);
      } catch {
        /* ya liberado */
      }
      if (m.tipo === "nodo" && m.movido) {
        const n = estado.g.nodos.find((x) => x.id === m.id);
        if (n) {
          n.x = Math.round(n.x / 10) * 10;
          n.y = Math.round(n.y / 10) * 10;
        }
        props.onCambio$();
      }
      if (m.tipo === "conectar") {
        tmp.linea = "";
        const bajo = document.elementFromPoint(e.clientX, e.clientY) as HTMLElement | null;
        const destino = bajo?.closest<HTMLElement>("[data-nodo]")?.dataset.nodo;
        if (destino) {
          historia.pila.push(JSON.stringify(estado.g));
          const motivo = conectar(estado.g, m.desde, m.salida, destino);
          if (motivo) {
            historia.pila.pop();
            estado.aviso = motivo;
            setTimeout(() => (estado.aviso = ""), 2500);
          } else {
            estado.resaltados = [];
            props.onCambio$();
          }
        }
      }
    };

    const rueda = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const v = estado.g.vista!;
      const f = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015));
      const nz = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, v.zoom * f));
      const px = e.clientX - r.left;
      const py = e.clientY - r.top;
      v.x = px - ((px - v.x) * nz) / v.zoom;
      v.y = py - ((py - v.y) * nz) / v.zoom;
      v.zoom = nz;
    };

    const tecla = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t && (t.closest("input, textarea, select, [contenteditable]") || !el.isConnected)) return;
      if ((e.key === "Delete" || e.key === "Backspace") && (estado.sel || estado.selArista >= 0)) {
        e.preventDefault();
        borrarSeleccion();
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        deshacer();
      } else if (e.key === "+" || e.key === "=") {
        zoomA((estado.g.vista?.zoom ?? 1) * 1.2);
      } else if (e.key === "-") {
        zoomA((estado.g.vista?.zoom ?? 1) / 1.2);
      } else if (e.key === "0") {
        ajustar();
      }
    };

    el.addEventListener("pointerdown", abajo);
    el.addEventListener("pointermove", mover);
    el.addEventListener("pointerup", arriba);
    el.addEventListener("pointercancel", arriba);
    el.addEventListener("wheel", rueda, { passive: false });
    window.addEventListener("keydown", tecla);
    cleanup(() => {
      ro.disconnect();
      el.removeEventListener("pointerdown", abajo);
      el.removeEventListener("pointermove", mover);
      el.removeEventListener("pointerup", arriba);
      el.removeEventListener("pointercancel", arriba);
      el.removeEventListener("wheel", rueda);
      window.removeEventListener("keydown", tecla);
    });
  });

  const g = estado.g;
  const v = g.vista ?? { x: 40, y: 40, zoom: 1 };
  const porId = new Map(g.nodos.map((n) => [n.id, n]));
  const nombres = {
    secuencias: Object.fromEntries(props.secuencias.map((s) => [s.id, s.nombre])),
    promos: Object.fromEntries(props.promos.map((p) => [p.id, p.titulo])),
  };
  const resaltados = new Set(estado.resaltados);
  const sel = porId.get(estado.sel);
  const paso = 20 * v.zoom;

  return (
    <div class="fl-editor">
      <div class="fl-barra" role="toolbar" aria-label="Herramientas del flujo">
        {!props.soloLectura && (
          <div class="fl-grupo">
            <span class="muted small">Agregar:</span>
            {AGREGAR.map((t) => (
              <button key={t} type="button" class={["btn btn-sm fl-agregar", `fl-t-${t}`]} title={NODOS[t].hint} onClick$={() => agregar(t)}>
                <span aria-hidden="true">{NODOS[t].icono}</span> {NODOS[t].label}
              </button>
            ))}
          </div>
        )}
        <div class="fl-grupo">
          <button type="button" class="btn btn-sm btn-ghost" title="Alejar (−)" aria-label="Alejar" onClick$={() => zoomA(v.zoom / 1.2)}>
            −
          </button>
          <button type="button" class="btn btn-sm btn-ghost fl-zoom" title="Encuadrar todo (0)" onClick$={ajustar}>
            {Math.round(v.zoom * 100)} %
          </button>
          <button type="button" class="btn btn-sm btn-ghost" title="Acercar (+)" aria-label="Acercar" onClick$={() => zoomA(v.zoom * 1.2)}>
            +
          </button>
          {!props.soloLectura && (
            <>
              <button type="button" class="btn btn-sm btn-ghost" title="Ordenar el flujo en columnas" onClick$={ordenarTodo}>
                Ordenar
              </button>
              <button type="button" class="btn btn-sm btn-ghost" disabled={!historia.pila.length} title="Deshacer (Ctrl+Z)" onClick$={deshacer}>
                ↶ Deshacer
              </button>
              <button
                type="button"
                class="btn btn-sm btn-ghost btn-borrar"
                disabled={!(estado.selArista >= 0 || (sel && sel.tipo !== "inicio"))}
                title="Borrar lo elegido (Supr)"
                onClick$={borrarSeleccion}
              >
                Borrar
              </button>
            </>
          )}
        </div>
      </div>

      <div class="fl-cuerpo">
        <div
          class="fl-lienzo"
          ref={lienzo}
          tabIndex={0}
          aria-label="Lienzo del flujo: arrastra el fondo para moverte, la rueda para acercar, y desde el punto de salida de un paso hasta otro para conectarlos"
          style={{ backgroundSize: `${paso}px ${paso}px`, backgroundPosition: `${v.x}px ${v.y}px` }}
        >
          <div class="fl-mundo" style={{ transform: `translate(${v.x}px, ${v.y}px) scale(${v.zoom})` }}>
            <svg class="fl-aristas" width="1" height="1" aria-hidden="true">
              {g.aristas.map((a, i) => {
                const d = porId.get(a.desde);
                const h = porId.get(a.hasta);
                if (!d || !h) return null;
                const path = curva(d.x + ANCHO_NODO, d.y + yPuerto(a.salida), h.x, h.y + Y_PUERTO);
                const elegida = estado.selArista === i;
                const recorrida = resaltados.has(a.desde) && resaltados.has(a.hasta);
                return (
                  <g key={`${a.desde}-${a.salida}-${a.hasta}`}>
                    <path d={path} class={["fl-arista", `fl-s-${a.salida}`, elegida && "elegida", recorrida && "recorrida"]} />
                    <path
                      d={path}
                      class="fl-arista-hit"
                      onClick$={() => {
                        estado.selArista = i;
                        estado.sel = "";
                      }}
                    />
                  </g>
                );
              })}
              {tmp.linea && <path d={tmp.linea} class="fl-arista fl-temporal" />}
            </svg>

            {g.nodos.map((n) => (
              <div
                key={n.id}
                data-nodo={n.id}
                class={["fl-nodo", `fl-t-${n.tipo}`, estado.sel === n.id && "elegido", resaltados.has(n.id) && "recorrido"]}
                style={{ left: `${n.x}px`, top: `${n.y}px`, width: `${ANCHO_NODO}px` }}
              >
                <div class="fl-nodo-cab">
                  <span class="fl-icono" aria-hidden="true">
                    {NODOS[n.tipo].icono}
                  </span>
                  <strong>{NODOS[n.tipo].label}</strong>
                </div>
                <div class="fl-nodo-cuerpo">{resumenNodo(n, nombres, esperaTexto)}</div>
                {n.media_url && n.tipo === "mensaje" && <img class="fl-mini" src={u(n.media_url)} alt="" width={36} height={36} />}
                {n.tipo !== "inicio" && <span class="fl-puerto fl-entrada" style={{ top: `${Y_PUERTO - 7}px` }} aria-hidden="true" />}
                {salidasDe(n.tipo).map((s) => (
                  <span
                    key={s}
                    class={["fl-puerto fl-salida", `fl-s-${s}`]}
                    data-salida={n.id}
                    data-nombre={s}
                    style={{ top: `${yPuerto(s) - 7}px` }}
                    title={s === "siguiente" ? "Arrastra hasta el siguiente paso" : `Arrastra hasta el paso de «${s === "si" ? "sí" : "no"}»`}
                  >
                    {s !== "siguiente" && <em>{s === "si" ? "sí" : "no"}</em>}
                  </span>
                ))}
              </div>
            ))}
          </div>
          {estado.aviso && (
            <p class="fl-aviso" role="status">
              {estado.aviso}
            </p>
          )}
          <p class="fl-ayuda muted small">Rueda: zoom · arrastra el fondo para moverte · arrastra desde ● para conectar · Supr borra</p>
        </div>

        <aside class="fl-inspector" aria-label="Paso elegido">
          {estado.selArista >= 0 && g.aristas[estado.selArista] ? (
            <div class="fl-ins">
              <h4>Conexión</h4>
              <p class="small">
                De «{NODOS[porId.get(g.aristas[estado.selArista].desde)?.tipo ?? "inicio"].label}» a «
                {NODOS[porId.get(g.aristas[estado.selArista].hasta)?.tipo ?? "fin"].label}»
                {g.aristas[estado.selArista].salida !== "siguiente" && ` (salida «${g.aristas[estado.selArista].salida === "si" ? "sí" : "no"}»)`}.
              </p>
              {!props.soloLectura && (
                <button type="button" class="btn btn-sm btn-danger" onClick$={borrarSeleccion}>
                  Quitar conexión
                </button>
              )}
            </div>
          ) : sel ? (
            <Inspector
              key={sel.id}
              nodo={sel}
              soloLectura={props.soloLectura}
              productos={props.productos}
              promos={props.promos}
              plantillas={props.plantillas}
              usaPlantillas={props.usaPlantillas}
              secuencias={props.secuencias.filter((s) => s.id !== props.secuenciaId)}
              onAntes$={guardarHistoria}
              onCambio$={cambio}
              onDuplicar$={async () => {
                await guardarHistoria();
                const c = duplicarNodo(estado.g, sel.id);
                if (c) estado.sel = c.id;
                await cambio();
              }}
              onBorrar$={borrarSeleccion}
            />
          ) : (
            <div class="fl-ins">
              <h4>Cómo se arma</h4>
              <ol class="small fl-guia">
                <li>Elige un paso y usa «Agregar»: el nuevo se conecta detrás.</li>
                <li>Arrastra desde el punto ● de la derecha de un paso hasta otro para conectarlos.</li>
                <li>La condición tiene dos salidas: «sí» y «no».</li>
                <li>«Ir a otro flujo» conecta este flujo con otro guardado.</li>
                <li>Guarda y usa «Probar»: simula aquí o envíalo a tu WhatsApp.</li>
              </ol>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
});

// ---------------------------------------------------------------------------

interface InsProps {
  nodo: Nodo;
  soloLectura: boolean;
  productos: Product[];
  promos: Fuente[];
  plantillas: PlantillaCloud[];
  usaPlantillas: boolean;
  secuencias: { id: number; nombre: string; esFlujo: boolean }[];
  onAntes$: QRL<() => void>;
  onCambio$: QRL<() => void>;
  onDuplicar$: QRL<() => void>;
  onBorrar$: QRL<() => void>;
}

type Unidad = "min" | "h" | "d";
const FACTOR: Record<Unidad, number> = { min: 1, h: 60, d: 1440 };

const Inspector = component$<InsProps>((p) => {
  const n = p.nodo;
  const tocado = useSignal(false);
  const subiendo = useSignal(false);
  const error = useSignal("");
  const ro = p.soloLectura;
  const antes = $(async () => {
    if (!tocado.value) {
      tocado.value = true;
      await p.onAntes$();
    }
  });
  const unidad: Unidad = (n.minutos ?? 0) % 1440 === 0 && (n.minutos ?? 0) > 0 ? "d" : (n.minutos ?? 0) % 60 === 0 && (n.minutos ?? 0) > 0 ? "h" : "min";
  const cantidad = (n.minutos ?? 0) / FACTOR[unidad];
  const categorias = [...new Set(p.productos.map((x) => x.category).filter(Boolean))].sort();

  return (
    <div class="fl-ins" onFocusin$={antes}>
      <h4>
        <span aria-hidden="true">{NODOS[n.tipo].icono}</span> {NODOS[n.tipo].label}
      </h4>
      <p class="muted small">{NODOS[n.tipo].hint}</p>

      {n.tipo === "esperar" && (
        <div class="row">
          <input
            class="num"
            type="number"
            min={1}
            value={cantidad}
            disabled={ro}
            aria-label="Cuánto esperar"
            onInput$={(_, el) => {
              n.minutos = Math.max(1, Math.round(Number(el.value) || 1)) * FACTOR[unidad];
              p.onCambio$();
            }}
          />
          <select
            value={unidad}
            disabled={ro}
            aria-label="Unidad"
            onChange$={(_, el) => {
              n.minutos = Math.max(1, Math.round(cantidad || 1)) * FACTOR[el.value as Unidad];
              p.onCambio$();
            }}
          >
            <option value="min">minutos</option>
            <option value="h">horas</option>
            <option value="d">días</option>
          </select>
        </div>
      )}

      {n.tipo === "mensaje" && (
        <>
          <label>
            Qué se envía
            <select
              value={n.mensaje}
              disabled={ro}
              onChange$={(_, el) => {
                n.mensaje = el.value as TipoPaso;
                p.onCambio$();
              }}
            >
              {(Object.keys(TIPOS_MSG) as TipoPaso[]).map((t) => (
                <option key={t} value={t}>
                  {TIPOS_MSG[t]}
                </option>
              ))}
            </select>
          </label>
          <label>
            {n.mensaje === "emoji" || n.mensaje === "texto" ? "Mensaje" : "Introducción (opcional)"}
            <textarea
              rows={5}
              value={n.texto ?? ""}
              disabled={ro}
              placeholder={n.mensaje === "emoji" ? "🥰" : "Hola {nombre}, ¿qué te pareció el {modelo}?"}
              onInput$={(_, el) => {
                n.texto = el.value;
                p.onCambio$();
              }}
            />
          </label>
          <p class="muted small">
            Usa {"{nombre}"} y {"{modelo}"}
            {n.mensaje !== "emoji" && n.mensaje !== "texto" && n.mensaje !== "oferta" && <>; {"{contenido}"} dice dónde va el contenido del modelo</>}.
          </p>
          {n.mensaje === "oferta" && (
            <label>
              Promo
              <select
                value={String(n.promo_id ?? 0)}
                disabled={ro}
                onChange$={(_, el) => {
                  n.promo_id = Number(el.value) || 0;
                  p.onCambio$();
                }}
              >
                <option value="0">Elige una promo…</option>
                {p.promos.map((x) => (
                  <option key={x.id} value={String(x.id)}>
                    {`${x.titulo}${x.vigente ? "" : " (no vigente)"}`}
                  </option>
                ))}
              </select>
            </label>
          )}
          {(n.mensaje === "emoji" || n.mensaje === "texto") && (
            <div class="fl-imagen">
              {n.media_url ? (
                <div class="row">
                  <img src={u(n.media_url)} alt="Imagen del mensaje" width={56} height={56} />
                  {!ro && (
                    <button
                      type="button"
                      class="btn btn-sm btn-ghost"
                      onClick$={async () => {
                        await antes();
                        n.media_url = "";
                        p.onCambio$();
                      }}
                    >
                      Quitar imagen
                    </button>
                  )}
                </div>
              ) : (
                !ro && (
                  <label class="btn btn-sm">
                    {subiendo.value ? "Subiendo…" : "Subir imagen o sticker"}
                    <input
                      type="file"
                      accept="image/*"
                      class="sr-only"
                      onChange$={async (_, el) => {
                        const f = el.files?.[0];
                        if (!f) return;
                        subiendo.value = true;
                        error.value = "";
                        try {
                          await antes();
                          n.media_url = (await subirImagenPaso(f)).media_url;
                          p.onCambio$();
                        } catch (e) {
                          error.value = (e as Error).message;
                        } finally {
                          subiendo.value = false;
                          el.value = "";
                        }
                      }}
                    />
                  </label>
                )
              )}
            </div>
          )}
          {p.usaPlantillas && (
            <label>
              Plantilla de la API oficial (fuera de las 24 h)
              <select
                value={n.plantilla ?? ""}
                disabled={ro}
                onChange$={(_, el) => {
                  n.plantilla = el.value;
                  p.onCambio$();
                }}
              >
                <option value="">Sin plantilla</option>
                {p.plantillas.map((x) => (
                  <option key={x.name} value={x.name}>
                    {x.name}
                  </option>
                ))}
              </select>
            </label>
          )}
        </>
      )}

      {n.tipo === "condicion" && (
        <>
          <label>
            Pregunta
            <select
              value={n.condicion}
              disabled={ro}
              onChange$={(_, el) => {
                n.condicion = el.value as Condicion;
                n.valor = "";
                p.onCambio$();
              }}
            >
              {(Object.keys(CONDICIONES) as Condicion[]).map((c) => (
                <option key={c} value={c}>
                  {CONDICIONES[c]}
                </option>
              ))}
            </select>
          </label>
          {n.condicion !== "respondio" && (
            <label>
              Valor
              <select
                value={n.valor ?? ""}
                disabled={ro}
                onChange$={(_, el) => {
                  n.valor = el.value;
                  p.onCambio$();
                }}
              >
                <option value="">Elige…</option>
                {(n.condicion === "temperatura"
                  ? ["caliente", "tibia", "fria"]
                  : n.condicion === "tiene_contenido"
                    ? ["beneficio", "testimonio", "tip"]
                    : categorias
                ).map((x) => (
                  <option key={x} value={x}>
                    {x === "fria" ? "fría" : x}
                  </option>
                ))}
              </select>
            </label>
          )}
          <p class="muted small">
            {n.condicion === "respondio"
              ? "«Sí» si escribió después del último mensaje del flujo. Al escribir, el flujo se pausa; sigue (según «Al responder») cuando vuelve a callarse."
              : "Arrastra la salida «sí» y la salida «no» a los pasos que correspondan."}
          </p>
        </>
      )}

      {n.tipo === "tarea" && (
        <label>
          Título de la tarea
          <input
            value={n.titulo ?? ""}
            disabled={ro}
            onInput$={(_, el) => {
              n.titulo = el.value;
              p.onCambio$();
            }}
          />
          <span class="muted small">Usa {"{nombre}"} y {"{modelo}"}. Vence al crearse, para que el equipo la vea hoy.</span>
        </label>
      )}

      {n.tipo === "etapa" && (
        <label>
          Etapa del embudo
          <select
            value={n.etapa}
            disabled={ro}
            onChange$={(_, el) => {
              n.etapa = el.value;
              p.onCambio$();
            }}
          >
            {Object.entries(ETAPAS_FLUJO).map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
        </label>
      )}

      {n.tipo === "ir_a" && (
        <label>
          Flujo destino
          <select
            value={String(n.destino ?? 0)}
            disabled={ro}
            onChange$={(_, el) => {
              n.destino = Number(el.value) || 0;
              p.onCambio$();
            }}
          >
            <option value="0">Elige un flujo…</option>
            {p.secuencias.map((s) => (
              <option key={s.id} value={String(s.id)} disabled={!s.esFlujo}>
                {`${s.nombre}${s.esFlujo ? "" : " (lista de pasos: ábrela y guárdala como flujo)"}`}
              </option>
            ))}
          </select>
          <span class="muted small">El flujo destino arranca desde su inicio. Si está apagado, este paso se salta (salvo en «Probar»).</span>
        </label>
      )}

      {n.tipo !== "inicio" && (
        <label>
          Nota (no se envía)
          <input
            value={n.nota ?? ""}
            disabled={ro}
            placeholder="Para el equipo"
            onInput$={(_, el) => {
              n.nota = el.value;
              p.onCambio$();
            }}
          />
        </label>
      )}
      {error.value && <p class="form-error small">{error.value}</p>}
      {!ro && n.tipo !== "inicio" && (
        <div class="row wrap fl-ins-acc">
          <button type="button" class="btn btn-sm btn-ghost" onClick$={p.onDuplicar$}>
            Duplicar
          </button>
          <button type="button" class="btn btn-sm btn-danger" onClick$={p.onBorrar$}>
            Borrar paso
          </button>
        </div>
      )}
    </div>
  );
});
