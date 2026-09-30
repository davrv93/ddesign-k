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
