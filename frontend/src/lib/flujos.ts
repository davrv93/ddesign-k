// Flujos visuales de seguimiento (docs/SEGUIMIENTO_CONTRATO.md §4b): tipos, operaciones puras sobre el grafo (las usan el
// lienzo y la barra del editor) y las llamadas a la API. Va aparte de lib/api.ts para no chocar con otra línea de trabajo.
import { api, type PasoSecuencia, type Secuencia, type SecuenciaEditable, type TipoPaso } from "~/lib/api";

export type TipoNodo = "inicio" | "esperar" | "mensaje" | "condicion" | "tarea" | "etapa" | "ir_a" | "fin";
export type Salida = "siguiente" | "si" | "no";
export type Condicion = "respondio" | "temperatura" | "categoria" | "tiene_contenido";

// «modelo» (foto del modelo en foco) existe solo en los flujos; TipoPaso de lib/api.ts no se toca (otra línea de trabajo).
export type TipoMensaje = TipoPaso | "modelo";

export interface Nodo {
  id: string;
  tipo: TipoNodo;
  x: number;
  y: number;
  minutos?: number;
  mensaje?: TipoMensaje;
  texto?: string;
  media_url?: string;
  promo_id?: number;
  plantilla?: string;
  condicion?: Condicion;
  valor?: string;
  titulo?: string;
  etapa?: string;
  destino?: number;
  nota?: string;
}

export interface Arista {
  desde: string;
  salida: Salida;
  hasta: string;
}

export interface Vista {
  x: number;
  y: number;
  zoom: number;
}

export interface Grafo {
  nodos: Nodo[];
  aristas: Arista[];
  vista?: Vista;
}

export type SecuenciaConGrafo = Secuencia & { grafo?: Grafo | null };
export type EditableConGrafo = SecuenciaEditable & { grafo?: Grafo };

// Medidas del nodo en el lienzo (coordenadas del mundo): los puertos se calculan con ellas, sin medir el DOM.
export const ANCHO_NODO = 230;
export const Y_PUERTO = 24; // entrada y salida «siguiente» / «sí»
export const Y_PUERTO_NO = 58; // salida «no» de la condición
export const ZOOM_MIN = 0.25;
export const ZOOM_MAX = 2;

export const NODOS: Record<TipoNodo, { label: string; icono: string; hint: string }> = {
  inicio: { label: "Inicio", icono: "▶", hint: "Arranca cuando la clienta deja de responder con un modelo en foco." },
  esperar: { label: "Esperar", icono: "⏱", hint: "Deja pasar un tiempo antes del siguiente paso." },
  mensaje: { label: "Mensaje", icono: "💬", hint: "Envía un mensaje por WhatsApp (texto, emoji, beneficio, testimonio, tip u oferta)." },
  condicion: { label: "Condición", icono: "◇", hint: "Pregunta algo y sigue por «sí» o por «no»." },
  tarea: { label: "Tarea", icono: "☑", hint: "Crea una tarea para el equipo." },
  etapa: { label: "Etapa", icono: "▦", hint: "Mueve a la clienta a una etapa del embudo." },
  ir_a: { label: "Ir a otro flujo", icono: "↪", hint: "Conecta con otro flujo y sigue en él." },
  fin: { label: "Fin", icono: "■", hint: "Termina el flujo." },
};

export const CONDICIONES: Record<Condicion, string> = {
  respondio: "¿Respondió desde el último mensaje?",
  temperatura: "¿La clienta está…?",
  categoria: "¿El modelo es de la categoría…?",
  tiene_contenido: "¿El modelo tiene…?",
};

export const ETAPAS_FLUJO: Record<string, string> = {
  prospeccion: "Prospección",
  seguimiento: "Seguimiento",
  cierre: "Cierre",
  venta_confirmada: "Venta confirmada",
  mas_adelante: "Más adelante",
  perdida: "Perdida",
};

export const salidasDe = (t: TipoNodo): Salida[] => (t === "condicion" ? ["si", "no"] : t === "fin" ? [] : ["siguiente"]);
export const yPuerto = (s: Salida) => (s === "no" ? Y_PUERTO_NO : Y_PUERTO);

let contador = 0;
export const nuevoId = (tipo: TipoNodo) => `${tipo}-${Date.now().toString(36)}${(contador++).toString(36)}`;

export function nodoNuevo(tipo: TipoNodo, x: number, y: number): Nodo {
  const n: Nodo = { id: nuevoId(tipo), tipo, x: Math.round(x / 10) * 10, y: Math.round(y / 10) * 10 };
  if (tipo === "esperar") n.minutos = 60;
  if (tipo === "mensaje") Object.assign(n, { mensaje: "texto", texto: "", media_url: "", promo_id: 0, plantilla: "" });
  if (tipo === "condicion") Object.assign(n, { condicion: "respondio", valor: "" });
  if (tipo === "tarea") n.titulo = "Atender a {nombre}";
  if (tipo === "etapa") n.etapa = "perdida";
  if (tipo === "ir_a") n.destino = 0;
  return n;
}

/** La lista de pasos de siempre como flujo (igual que store.GrafoDesdePasos). */
export function grafoDesdePasos(pasos: PasoSecuencia[]): Grafo {
  const g: Grafo = { nodos: [{ id: "inicio", tipo: "inicio", x: 40, y: 160 }], aristas: [], vista: { x: 40, y: 40, zoom: 0.8 } };
  let prev = "inicio";
  let x = 320;
  pasos.forEach((p, i) => {
    const e = `espera-${i + 1}`;
    const m = `mensaje-${i + 1}`;
    g.nodos.push({ id: e, tipo: "esperar", x, y: 160, minutos: p.espera_min });
    g.nodos.push({
      id: m,
      tipo: "mensaje",
      x: x + 280,
      y: 160,
      mensaje: p.tipo,
      texto: p.texto ?? "",
      media_url: p.media_url ?? "",
      promo_id: p.promo_id ?? 0,
      plantilla: p.plantilla ?? "",
    });
    g.aristas.push({ desde: prev, salida: "siguiente", hasta: e }, { desde: e, salida: "siguiente", hasta: m });
    prev = m;
    x += 560;
  });
  g.nodos.push({ id: "fin", tipo: "fin", x, y: 160 });
  g.aristas.push({ desde: prev, salida: "siguiente", hasta: "fin" });
  return g;
}

export const grafoInicial = (): Grafo => ({
  nodos: [
    { id: "inicio", tipo: "inicio", x: 40, y: 160 },
    { id: "espera-1", tipo: "esperar", x: 320, y: 160, minutos: 60 },
    { id: "mensaje-1", tipo: "mensaje", x: 600, y: 160, mensaje: "emoji", texto: "🥰", media_url: "", promo_id: 0, plantilla: "" },
    { id: "fin", tipo: "fin", x: 880, y: 160 },
  ],
  aristas: [
    { desde: "inicio", salida: "siguiente", hasta: "espera-1" },
    { desde: "espera-1", salida: "siguiente", hasta: "mensaje-1" },
    { desde: "mensaje-1", salida: "siguiente", hasta: "fin" },
  ],
  vista: { x: 40, y: 40, zoom: 0.8 },
});

/** Conecta (o reconecta) la salida `salida` de `desde` con `hasta`. Devuelve "" o el motivo por el que no se puede. */
export function conectar(g: Grafo, desde: string, salida: Salida, hasta: string): string {
  if (desde === hasta) return "Un paso no se conecta consigo mismo.";
  const h = g.nodos.find((n) => n.id === hasta);
  if (!h) return "";
  if (h.tipo === "inicio") return "Nada puede llevar al inicio.";
  g.aristas = g.aristas.filter((a) => !(a.desde === desde && a.salida === salida));
  g.aristas.push({ desde, salida, hasta });
  return "";
}

export function borrarNodo(g: Grafo, id: string) {
  g.nodos = g.nodos.filter((n) => n.id !== id);
  g.aristas = g.aristas.filter((a) => a.desde !== id && a.hasta !== id);
}

export function duplicarNodo(g: Grafo, id: string): Nodo | null {
  const n = g.nodos.find((x) => x.id === id);
  if (!n || n.tipo === "inicio") return null;
  const c: Nodo = { ...JSON.parse(JSON.stringify(n)), id: nuevoId(n.tipo), x: n.x + 30, y: n.y + 110 };
  g.nodos.push(c);
  return c;
}

/** Ordena el flujo en columnas desde el inicio (anchura primero; «sí» arriba, «no» abajo). */
export function ordenar(g: Grafo) {
  const col = new Map<string, number>();
  const ini = g.nodos.find((n) => n.tipo === "inicio")?.id;
  if (!ini) return;
  const cola: string[] = [ini];
  col.set(ini, 0);
  while (cola.length) {
    const id = cola.shift()!;
    const sal = g.aristas.filter((a) => a.desde === id).sort((a, b) => (a.salida === "no" ? 1 : 0) - (b.salida === "no" ? 1 : 0));
    for (const a of sal) {
      if (!col.has(a.hasta)) {
        col.set(a.hasta, (col.get(id) ?? 0) + 1);
        cola.push(a.hasta);
      }
    }
  }
  let suelto = Math.max(0, ...col.values()) + 1;
  for (const n of g.nodos) if (!col.has(n.id)) col.set(n.id, suelto++);
  const filas = new Map<number, number>();
  for (const n of [...g.nodos].sort((a, b) => (col.get(a.id)! - col.get(b.id)!) || a.y - b.y)) {
    const c = col.get(n.id)!;
    const f = filas.get(c) ?? 0;
    filas.set(c, f + 1);
    n.x = 40 + c * 290;
    n.y = 60 + f * 150;
  }
}

/** Vista que encuadra todo el flujo en un lienzo de ancho×alto. */
export function encuadrar(g: Grafo, ancho: number, alto: number): Vista {
  if (!g.nodos.length) return { x: 40, y: 40, zoom: 1 };
  const xs = g.nodos.map((n) => n.x);
  const ys = g.nodos.map((n) => n.y);
  const minX = Math.min(...xs) - 40;
  const minY = Math.min(...ys) - 40;
  const maxX = Math.max(...xs) + ANCHO_NODO + 40;
  const maxY = Math.max(...ys) + 130;
  const zoom = Math.max(ZOOM_MIN, Math.min(1.1, ancho / (maxX - minX), alto / (maxY - minY)));
  return { zoom, x: (ancho - (maxX - minX) * zoom) / 2 - minX * zoom, y: (alto - (maxY - minY) * zoom) / 2 - minY * zoom };
}

/** Curva de una conexión: de la salida (derecha del nodo) a la entrada (izquierda). */
export function curva(x1: number, y1: number, x2: number, y2: number): string {
  const dx = Math.max(60, Math.abs(x2 - x1) / 2);
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

/** Lo que falta para guardar (las reglas principales del backend), o "". */
export function validarGrafo(g: Grafo): string {
  const inicios = g.nodos.filter((n) => n.tipo === "inicio");
  if (inicios.length !== 1) return "El flujo necesita exactamente un inicio.";
  if (!g.aristas.some((a) => a.desde === inicios[0].id)) return "Conecta el inicio con el primer paso.";
  const mensajes = g.nodos.filter((n) => n.tipo === "mensaje");
  if (!mensajes.length) return "Añade al menos un mensaje.";
  for (const n of g.nodos) {
    const nom = `«${NODOS[n.tipo].label}»`;
    if (n.tipo === "esperar" && !((n.minutos ?? 0) >= 1 && (n.minutos ?? 0) <= 20160)) return `${nom}: la espera va de 1 minuto a 14 días.`;
    if (n.tipo === "mensaje") {
      if ((n.mensaje === "emoji" || n.mensaje === "texto") && !(n.texto ?? "").trim() && !n.media_url) return `${nom}: escribe el mensaje o sube una imagen.`;
      if (n.mensaje === "oferta" && !n.promo_id) return `${nom}: elige la promo de la oferta.`;
    }
    if (n.tipo === "condicion" && n.condicion !== "respondio" && !(n.valor ?? "").trim()) return `${nom}: elige el valor de la condición.`;
    if (n.tipo === "tarea" && !(n.titulo ?? "").trim()) return `${nom}: escribe el título de la tarea.`;
    if (n.tipo === "ir_a" && !n.destino) return `${nom}: elige a qué flujo pasa.`;
  }
  return "";
}

/** Una línea para la tarjeta del nodo. */
export function resumenNodo(n: Nodo, nombres: { secuencias: Record<number, string>; promos: Record<number, string> }, espera: (m: number) => string): string {
  switch (n.tipo) {
    case "inicio":
      return "Cuando deja de responder";
    case "esperar":
      return espera(n.minutos ?? 0);
    case "mensaje": {
      const t = (n.texto ?? "").trim();
      const tipo: Record<string, string> = {
        emoji: "Emoji",
        texto: "Texto",
        modelo: "Foto del modelo",
        beneficio: "Beneficio del modelo",
        testimonio: "Testimonio",
        tip: "Tip",
        oferta: "Oferta",
      };
      const base = tipo[n.mensaje ?? "texto"] ?? "Mensaje";
      if (n.mensaje === "oferta") return `${base}: ${nombres.promos[n.promo_id ?? 0] ?? "elige la promo"}`;
      return t ? `${base}: ${t.length > 70 ? t.slice(0, 70) + "…" : t}` : n.media_url ? `${base} con imagen` : base;
    }
    case "condicion":
      return n.condicion === "respondio" ? CONDICIONES.respondio : `${CONDICIONES[n.condicion ?? "respondio"]} ${n.valor ?? ""}`.trim();
    case "tarea":
      return n.titulo ?? "";
    case "etapa":
      return `A «${ETAPAS_FLUJO[n.etapa ?? ""] ?? n.etapa}»`;
    case "ir_a":
      return n.destino ? `Al flujo «${nombres.secuencias[n.destino] ?? "#" + n.destino}»` : "Elige el flujo";
    case "fin":
      return "Termina el seguimiento";
  }
}

// ---------------------------------------------------------------------------
// API

export interface PasoSimulado {
  nodo: string;
  tipo: TipoNodo;
  en_min: number;
  mensaje?: string;
  texto?: string;
  media_url?: string;
  omitido?: boolean;
  motivo?: string;
  detalle?: string;
  flujo?: string;
}

export const simularFlujo = (body: { grafo: Grafo; nombre: string; producto: string; responde: boolean; temperatura: string }) =>
  api<{ pasos: PasoSimulado[]; fin: string }>("/api/flujos/simular", { method: "POST", json: body });

export const probarFlujo = (id: number, body: { telefono: string; producto: string; escala_min: number }) =>
  api<{ conversation_id: number; telefono: string; escala_min: number }>(`/api/secuencias/${id}/probar`, { method: "POST", json: body });

// Un flujo por modelo (como los Salesbots de Kommo): copias del flujo base con alcance en cada modelo, apagadas.
// Sin códigos, todos los modelos activos. Los modelos que ya tienen su flujo se omiten.
export const flujoPorModelo = (id: number, codigos: string[]) =>
  api<{ creadas: string[]; omitidas: Record<string, string> }>(`/api/secuencias/${id}/por-modelo`, { method: "POST", json: { codigos } });

export const activarLote = (ids: number[], activa: boolean) =>
  api<{ cambiadas: number }>("/api/secuencias/activar-lote", { method: "POST", json: { ids, activa } });

// Modelos que ya tienen un flujo propio (alcance «productos»): código → nombre del flujo.
export const flujosPropios = (secuencias: { id: number; nombre: string; alcance: string; alcance_valor?: string | null }[], salvo = 0) => {
  const m: Record<string, string> = {};
  for (const q of secuencias) {
    if (q.alcance !== "productos" || q.id === salvo) continue;
    for (const c of (q.alcance_valor ?? "").split(",")) if (c.trim()) m[c.trim().toUpperCase()] = q.nombre;
  }
  return m;
};
