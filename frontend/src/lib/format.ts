export const STATUS: Record<string, { label: string; color: string; hint: string }> = {
  consulta: { label: "Consultas", color: "#8b5cf6", hint: "Fotos y preguntas sin pedido" },
  pendiente: { label: "Por confirmar", color: "#f59e0b", hint: "Esperan el SI del cliente" },
  confirmado: { label: "Confirmados", color: "#10b981", hint: "Stock reservado" },
  preparando: { label: "En preparación", color: "#0ea5e9", hint: "Empaque / ajustes" },
  enviado: { label: "Enviados", color: "#6366f1", hint: "En reparto" },
  entregado: { label: "Entregados", color: "#64748b", hint: "Venta cerrada" },
  cancelado: { label: "Cancelados", color: "#ef4444", hint: "Stock devuelto" },
};

export const money = (v: number, currency = "S/") => `${currency} ${v.toFixed(2)}`;

export const customerName = (c: { name: string; phone: string }) => c.name || (c.phone ? `+${c.phone}` : "Cliente");

export const phoneLabel = (p: string) => (p ? `+${p}` : "");

export function timeAgo(iso: string): string {
  const d = new Date(iso);
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 60) return "ahora";
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`;
  if (s < 86400) return `hace ${Math.floor(s / 3600)} h`;
  if (s < 86400 * 7) return `hace ${Math.floor(s / 86400)} d`;
  return d.toLocaleDateString("es-PE", { day: "2-digit", month: "short" });
}

export const timeLabel = (iso: string) =>
  new Date(iso).toLocaleString("es-PE", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

export const mapsLink = (lat: number | null, lng: number | null, text: string) =>
  lat != null && lng != null
    ? `https://www.google.com/maps?q=${lat},${lng}`
    : text
      ? `https://www.google.com/maps/search/${encodeURIComponent(text)}`
      : "";

// Convierte *negrita* de WhatsApp a texto plano con marcas simples para mostrar en el panel.
export const waText = (s: string) => s.replace(/\*([^*\n]+)\*/g, "$1");

// Etapas del embudo (backend: store.Etapas). «perdida» solo la pone una persona.
export const ETAPAS: { key: string; label: string; color: string }[] = [
  { key: "prospeccion", label: "Prospección", color: "#8b5cf6" },
  { key: "seguimiento", label: "Seguimiento", color: "#0ea5e9" },
  { key: "cierre", label: "Cierre", color: "#f59e0b" },
  { key: "venta_confirmada", label: "Venta confirmada", color: "#10b981" },
  { key: "perdida", label: "Perdida", color: "#94a3b8" },
];

export const etapaInfo = (k: string) => ETAPAS.find((e) => e.key === k) ?? { key: "", label: "Sin etapa", color: "#a8a29e" };

export const dateLabel = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleDateString("es-PE", { day: "2-digit", month: "short", year: "numeric" }) : "—";

/** «hoy 17:00», «mañana», «vie 9 oct», «hace 2 d» para el vencimiento de una tarea. */
export function venceLabel(iso: string | null): { text: string; tone: "" | "warn" | "danger" } {
  if (!iso) return { text: "Sin fecha", tone: "" };
  const d = new Date(iso);
  const hoy = new Date();
  const ini = new Date(hoy.getFullYear(), hoy.getMonth(), hoy.getDate());
  const dias = Math.floor((new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() - ini.getTime()) / 86400000);
  const hora = d.toLocaleTimeString("es-PE", { hour: "2-digit", minute: "2-digit" });
  const conHora = hora !== "00:00" && hora !== "12:00 a. m." ? ` ${hora}` : "";
  if (dias < 0) return { text: dias === -1 ? "Venció ayer" : `Venció hace ${-dias} d`, tone: "danger" };
  if (dias === 0) return { text: `Hoy${conHora}`, tone: "warn" };
  if (dias === 1) return { text: `Mañana${conHora}`, tone: "" };
  return { text: d.toLocaleDateString("es-PE", { weekday: "short", day: "numeric", month: "short" }) + conHora, tone: "" };
}

/** Valor de un <input type="datetime-local"> (hora local) a ISO, y al revés. */
export const localAISO = (v: string) => (v ? new Date(v).toISOString() : null);
export function isoALocal(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

export const iniciales2 = (nombre: string) =>
  nombre
    .replace("+", "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase() || "?";
