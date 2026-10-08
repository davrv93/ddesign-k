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

/** Ficha técnica (backend/internal/store/ficha.go; el esquema del agente V2). */
export type Fuente = "diners" | "foto" | "ambas" | "tienda";
export interface Dato {
  valor: string;
  fuente: Fuente;
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
