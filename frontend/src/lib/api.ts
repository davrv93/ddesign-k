// Cliente de la API del backend Go. Sólo se usa en el navegador (el sitio es estático).

const TOKEN_KEY = "kd_token";

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
  const res = await fetch(path, { ...opts, headers, body });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && !path.startsWith("/api/auth")) {
    setToken(null);
    location.href = "/login";
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
