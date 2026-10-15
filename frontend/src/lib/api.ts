// Cliente de la API del backend Go. Sólo se usa en el navegador (el sitio es estático).

import { BASE, u } from "./base";

// Una sesión por panel: /baruka/ conserva su clave de siempre; cada empresa de /jmdventas/<empresa>/ tiene la suya
// (comparten el mismo origen, y un token de una empresa no vale en otra).
const TOKEN_KEY = BASE && BASE !== "/baruka" ? `kd_token:${BASE}` : "kd_token";

export const getToken = (): string | null => {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
};

export const setToken = (t: string | null) => {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* modo privado */
  }
};

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function api<T = unknown>(path: string, opts: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(opts.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let body = opts.body;
  if (opts.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(opts.json);
  }
  const res = await fetch(u(path), { ...opts, headers, body });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && !path.startsWith("/api/auth")) {
    setToken(null);
    location.href = u("/login");
  }
  if (!res.ok) throw new ApiError((data as { error?: string }).error ?? `Error ${res.status}`, res.status);
  return data as T;
}

// ---------------------------------------------------------------------------
// Tipos

export interface Variant {
  id?: number;
  size: string;
  stock: number;
}

export interface Product {
  id: number;
  code: string;
  name: string;
  description: string;
  category: string;
  color: string;
  price: number;
  image: string;
  ai_tags: string;
  active: boolean;
  variants: Variant[];
  ficha?: Ficha | null;
}

/** Ficha técnica (backend/internal/store/ficha.go; el esquema del agente V2).
 *  `OrigenDato` es de dónde salió cada dato de la ficha (no confundir con `Fuente`, el material del robot). */
export type OrigenDato = "diners" | "foto" | "ambas" | "tienda";
export interface Dato {
  valor: string;
  fuente: OrigenDato;
}
export interface Ficha {
  codigo?: string;
  piezas: string[] | null;
  atributos: Record<string, Dato | null> | null;
  detalles: Dato[] | null;
  como_queda: Dato[] | null;
  cuidados: string | null;
  resumen: string;
  discrepancias: string[] | null;
  pendiente_tienda: string[] | null;
}

export interface Customer {
  id: number;
  jid: string;
  phone: string;
  name: string;
}

export interface OrderItem {
  id: number;
  product_id: number | null;
  product_code: string;
  product_name: string;
  image: string;
  size: string;
  qty: number;
  unit_price: number;
}

export interface Order {
  id: number;
  status: string;
  total: number;
  notes: string;
  source: string;
  customer_image: string;
  match_confidence: number;
  location_lat: number | null;
  location_lng: number | null;
  location_text: string;
  stock_reserved: boolean;
  position: number;
  created_at: string;
  updated_at: string;
  customer: Customer;
  conversation_id: number;
  asesora_id: number;
  items: OrderItem[];
}

export interface Conversation {
  id: number;
  state: string;
  bot_paused: boolean;
  unread: number;
  last_message: string;
  last_message_at: string;
  customer: Customer;
}

export interface Message {
  id: number;
  direction: "in" | "out";
  kind: "text" | "image" | "location" | "other";
  body: string;
  media: string;
  author: string;
  status: "" | "pending" | "sent" | "failed";
  created_at: string;
}

export interface Stats {
  by_status: Record<string, number>;
  sales_month: number;
  customers: number;
  low_stock: number;
  open_inquiries: number;
}

// ---------------------------------------------------------------------------
// CRM (backend/internal/api/crm_panel.go)

export interface Sesion {
  id: number;
  username: string;
  name: string;
  role: "admin" | "asesora" | string;
  admin: boolean;
}

export interface Clienta {
  id: number;
  jid: string;
  phone: string;
  name: string;
  email: string;
  ciudad: string;
  etapa: string;
  etapa_fijada: boolean;
  asesora_id: number;
  asesora: string;
  etiquetas: string[];
  pedidos: number;
  total_comprado: number;
  ultima_compra: string | null;
  conversation_id: number;
  ultimo_mensaje: string | null;
  no_leidos: number;
  created_at: string;
}

export interface Nota {
  id: number;
  customer_id: number;
  order_id: number;
  texto: string;
  autor: string;
  autor_id: number;
  created_at: string;
}

export interface Tarea {
  id: number;
  customer_id: number;
  clienta: string;
  order_id: number;
  titulo: string;
  vence: string | null;
  responsable_id: number;
  responsable: string;
  hecha: boolean;
  hecha_at: string | null;
  creada_por: string;
  created_at: string;
}

export interface Miembro {
  id: number;
  username: string;
  name: string;
  role: string;
  active: boolean;
}

export interface Evento {
  tipo: string;
  texto: string;
  autor: string;
  detalle?: string;
  ref_id?: number;
  monto?: number;
  at: string;
}

export interface Inicio {
  ventas_mes: number;
  ventas_mes_anterior: number;
  pedidos_vendidos: number;
  pedidos_mes: number;
  por_estado: Record<string, number>;
  clientas_nuevas: number;
  clientas_total: number;
  embudo: Record<string, number>;
  tareas_vencidas: number;
  tareas_hoy: number;
  mis_vencidas: number;
  mis_hoy: number;
  sin_leer: number;
  stock_bajo: number;
}

let sesionCache: Promise<Sesion> | null = null;

/** Usuario de la sesión y su rol (una vez por carga de página). */
export function miSesion(): Promise<Sesion> {
  if (!sesionCache) {
    sesionCache = api<{ usuario: Sesion }>("/api/me")
      .then((r) => r.usuario)
      .catch((e) => {
        sesionCache = null;
        throw e;
      });
  }
  return sesionCache;
}

let equipoCache: Promise<Miembro[]> | null = null;

/** Personas del equipo (para asignar clientas, pedidos y tareas). */
export function equipo(fresco = false): Promise<Miembro[]> {
  if (!equipoCache || fresco) {
    equipoCache = api<Miembro[]>("/api/equipo").catch((e) => {
      equipoCache = null;
      throw e;
    });
  }
  return equipoCache;
}

/** Descarga un archivo de la API con la sesión (los enlaces normales no llevan el token). */
export async function descargar(path: string, nombre: string) {
  const res = await fetch(u(path), { headers: { Authorization: `Bearer ${getToken() ?? ""}` } });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    throw new ApiError((d as { error?: string }).error ?? `Error ${res.status}`, res.status);
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = nombre;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// ---------------------------------------------------------------------------
// Fuentes, promos, embudos y conocimiento del robot (docs/FUENTES_CONTRATO.md)

export type TipoFuente = "promo" | "faq" | "guion" | "politica" | "material";
export type EstadoFuente = "borrador" | "activo" | "vencido" | "archivado" | "propuesta" | "descartada";
export type Mecanica = "porcentaje" | "monto" | "precio_especial" | "dos_por" | "envio_gratis" | "regalo" | "otra";
export type Publico = "todas" | "primera_vez" | "que_compraron";

export interface AlcancePromo {
  tipo: "todo" | "categoria" | "coleccion" | "prendas";
  categoria: string;
  coleccion: string;
  codigos: string[];
}

/** `datos` de una promo. */
export interface PromoDatos {
  mecanica: Mecanica;
  valor: number;
  alcance: AlcancePromo;
  condiciones: string;
  mensaje_apertura: string;
  respuesta_promo: string;
  respuesta_fuera: string;
  objetivo: string;
  meta: number;
  publico: Publico;
  embudo_id: number;
}

/** `datos` de una pregunta frecuente (`ocurrencias` solo en las propuestas del robot). */
export interface FaqDatos {
  pregunta: string;
  variantes: string[];
  respuesta: string;
  ocurrencias?: number;
}

/** Material con el que atiende el robot. En la lista, `contenido` puede venir vacío y el texto recortado en `resumen`. */
export interface Fuente {
  id: number;
  tipo: TipoFuente;
  titulo: string;
  contenido: string;
  resumen?: string;
  estado: EstadoFuente;
  vigente_desde: string;
  vigente_hasta: string;
  vigente: boolean;
  programada: boolean;
  etiquetas: string[] | null;
  datos: Partial<PromoDatos> & Partial<FaqDatos> & Record<string, unknown>;
  origen: string;
  archivo: string;
  version: number;
  actualizado_por: string;
  actualizado_en: string;
  creado_en: string;
}

/** Campos que se mandan al crear o guardar. */
export interface FuenteEditable {
  tipo: TipoFuente;
  titulo: string;
  contenido: string;
  vigente_desde: string;
  vigente_hasta: string;
  etiquetas: string[];
  datos: Record<string, unknown>;
}

export interface FuenteVersion {
  version: number;
  titulo: string;
  contenido: string;
  datos: Fuente["datos"];
  autor: string;
  creado_en: string;
}

export interface CifrasPromo {
  conversaciones: number;
  pedidos: number;
  confirmados: number;
  pagados: number;
  monto: number;
}

export interface PromoResumen {
  id: number;
  titulo: string;
  estado: EstadoFuente;
  vigente: boolean;
  programada: boolean;
  vigente_desde: string;
  vigente_hasta: string;
  dias_restantes: number | null;
  embudo_id: number;
  cifras: CifrasPromo;
  avisos: string[] | null;
}

export interface Embudo {
  id: number;
  nombre: string;
  etapas: string[];
  promo_id: number;
  general: boolean;
  kommo_pipeline_id: number;
  creado_en: string;
}

export type PedidoEmbudo = Order & { etapa: string };

export interface Conocimiento {
  empresa: string;
  version: string;
  hoy: string;
  promos: ({ id: number; titulo: string; contenido: string; vigente_desde: string; vigente_hasta: string } & Partial<PromoDatos>)[] | null;
  faqs: { id: number; pregunta: string; variantes: string[] | null; respuesta: string }[] | null;
  politicas: { id: number; titulo: string; contenido: string }[] | null;
  guion: { id: number; titulo: string; contenido: string } | null;
  material: { id: number; titulo: string; contenido: string }[] | null;
}

export interface FuenteUsada {
  fuente_id: number;
  titulo: string;
  tipo: TipoFuente;
  creado_en: string;
}

const qs = (p: Record<string, string | undefined>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(p)) if (v) s.set(k, v);
  const t = s.toString();
  return t ? `?${t}` : "";
};

export const listarFuentes = (f: { tipo?: string; estado?: string; q?: string } = {}) => api<Fuente[] | null>(`/api/fuentes${qs(f)}`).then((r) => r ?? []);
export const leerFuente = (id: number) => api<Fuente>(`/api/fuentes/${id}`);
export const crearFuente = (body: FuenteEditable) => api<Fuente>("/api/fuentes", { method: "POST", json: body });
export const guardarFuente = (id: number, body: FuenteEditable) => api<Fuente>(`/api/fuentes/${id}`, { method: "PUT", json: body });
export const cambiarEstadoFuente = (id: number, estado: "activo" | "borrador" | "archivado" | "descartada") =>
  api<Fuente>(`/api/fuentes/${id}/estado`, { method: "POST", json: { estado } });
export const borrarFuente = (id: number) => api(`/api/fuentes/${id}`, { method: "DELETE" });
export const versionesFuente = (id: number) => api<FuenteVersion[] | null>(`/api/fuentes/${id}/versiones`).then((r) => r ?? []);

/** Sube un archivo (pdf, docx, txt, md, csv): el backend extrae el texto y crea un borrador. */
export const subirFuente = (archivo: File, tipo: TipoFuente, titulo: string) => {
  const fd = new FormData();
  fd.append("archivo", archivo);
  fd.append("tipo", tipo);
  fd.append("titulo", titulo);
  return api<Fuente>("/api/fuentes/subir", { method: "POST", body: fd });
};

export const leerConocimiento = () => api<Conocimiento>("/api/conocimiento");
export const resumenPromos = () => api<PromoResumen[] | null>("/api/promos/resumen").then((r) => r ?? []);
export const fuentesDeConversacion = (id: number) => api<FuenteUsada[] | null>(`/api/conversations/${id}/fuentes`).then((r) => r ?? []);

export const listarEmbudos = () => api<Embudo[] | null>("/api/embudos").then((r) => r ?? []);
export const crearEmbudo = (body: { nombre: string; etapas: string[]; promo_id: number }) => api<Embudo>("/api/embudos", { method: "POST", json: body });
export const guardarEmbudo = (id: number, body: { nombre: string; etapas: string[] }) => api<Embudo>(`/api/embudos/${id}`, { method: "PUT", json: body });
export const borrarEmbudo = (id: number) => api(`/api/embudos/${id}`, { method: "DELETE" });
export const pedidosDeEmbudo = (id: number) =>
  api<{ embudo: Embudo; pedidos: PedidoEmbudo[] | null }>(`/api/embudos/${id}/pedidos`).then((r) => ({ embudo: r.embudo, pedidos: r.pedidos ?? [] }));
export const moverEnEmbudo = (id: number, orderId: number, etapa: string) =>
  api(`/api/embudos/${id}/pedidos/${orderId}`, { method: "PUT", json: { etapa } });
export const sacarDeEmbudo = (id: number, orderId: number) => api(`/api/embudos/${id}/pedidos/${orderId}`, { method: "DELETE" });

// ---------------------------------------------------------------------------
// Clienta caliente: avisos del robot (GET /api/alertas) y lo que sabe de cada conversación (GET …/ficha).

/** Lo que el robot descubrió de la clienta cuando saltó el aviso. Cadenas vacías = no lo sabe. */
export interface ResumenAlerta {
  evento: string;
  fecha: string;
  dias: number | null;
  horario: string;
  talla: string;
  color: string;
  estilo: string;
  presupuesto: string;
  dudas: string[] | null;
  prenda: string;
  prenda_nombre: string;
}

export interface Alerta {
  id: number;
  tipo: "caliente" | string;
  conversation_id: number;
  customer_id: number;
  nombre: string;
  telefono: string;
  motivo: string;
  resumen: ResumenAlerta | null;
  creado_en: string;
  atendida_por: string;
  atendida_en: string | null;
  whatsapp_enviado: boolean;
}

export type Temperatura = "caliente" | "tibio" | "frio";

export interface FichaConversacion {
  temperatura: Temperatura | "";
  temperatura_motivo: string;
  dias_al_evento: number | null;
  etapa: string;
  sabemos: {
    nombre: string;
    ocasion: string;
    fecha: string;
    fecha_iso: string;
    horario: string;
    talla: string;
    estatura: string;
    color: string;
    estilo: string;
    presupuesto: string;
    envio: string;
    ciudad: string;
    para_quien: string;
  } | null;
  objeciones: string[] | null;
  producto: { codigo: string; nombre: string } | null;
  pedido: { codigo: string; talla: string; confirmado: boolean } | null;
}

export const listarAlertas = (estado: "pendiente" | "todas" = "pendiente") =>
  api<Alerta[] | null>(`/api/alertas${qs({ estado })}`).then((r) => r ?? []);
export const marcarAlertaAtendida = (id: number) => api<Alerta>(`/api/alertas/${id}/atendida`, { method: "POST" });
export const fichaConversacion = (id: number) => api<FichaConversacion>(`/api/conversations/${id}/ficha`);
