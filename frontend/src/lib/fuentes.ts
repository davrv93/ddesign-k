// Etiquetas, colores y cálculos de las fuentes del robot (promos, preguntas frecuentes, guion, políticas y material).
import type { AlcancePromo, Fuente, Mecanica, PromoDatos, TipoFuente } from "./api";

export const TIPOS: Record<TipoFuente, { label: string; corto: string; icon: string; color: string; hint: string }> = {
  promo: { label: "Promo", corto: "Promo", icon: "🏷", color: "#c2334a", hint: "Descuento o beneficio con fechas." },
  faq: { label: "Pregunta frecuente", corto: "Pregunta", icon: "❓", color: "#0ea5e9", hint: "Una pregunta, sus variantes y la respuesta tal cual." },
  guion: { label: "Guion", corto: "Guion", icon: "🗒", color: "#8b5cf6", hint: "El orden de la conversación, con encabezados «## Paso N»." },
  politica: { label: "Política", corto: "Política", icon: "📜", color: "#6366f1", hint: "Cambios, envíos, pagos: lo que la tienda permite." },
  material: { label: "Material", corto: "Material", icon: "📎", color: "#64748b", hint: "Texto suelto: catálogos, tablas, mensajes pegados." },
};

/** Estado que se muestra: «activo» se parte en activo / programada según las fechas. */
export type EstadoVisible = "borrador" | "activo" | "programada" | "vencido" | "archivado" | "propuesta" | "descartada";

export const ESTADOS: Record<EstadoVisible, { label: string; color: string }> = {
  borrador: { label: "Borrador", color: "#a8a29e" },
  activo: { label: "Activo", color: "#0f9d6b" },
  programada: { label: "Programada", color: "#0ea5e9" },
  vencido: { label: "Vencido", color: "#c77700" },
  archivado: { label: "Archivado", color: "#64748b" },
  propuesta: { label: "Propuesta", color: "#8b5cf6" },
  descartada: { label: "Descartada", color: "#94a3b8" },
};

export function estadoVisible(f: Pick<Fuente, "estado" | "programada" | "vigente">): EstadoVisible {
  if (f.estado === "activo" && f.programada) return "programada";
  return f.estado as EstadoVisible;
}

export const MECANICAS: Record<Mecanica, { label: string; valor: string }> = {
  porcentaje: { label: "Porcentaje de descuento", valor: "% de descuento" },
  monto: { label: "Monto de descuento", valor: "Soles de descuento" },
  precio_especial: { label: "Precio especial", valor: "Precio final (S/)" },
  dos_por: { label: "Dos por…", valor: "Precio por 2 unidades (S/)" },
  envio_gratis: { label: "Envío gratis", valor: "" },
  regalo: { label: "Regalo", valor: "" },
  otra: { label: "Otra", valor: "" },
};

export const PUBLICOS = [
  { key: "todas", label: "Todas las clientas" },
  { key: "primera_vez", label: "Las que escriben por primera vez" },
  { key: "que_compraron", label: "Las que ya compraron" },
] as const;

export const CATEGORIAS = ["vestido", "blusa", "enterizo", "pantalon", "conjunto", "blazer", "falda"];
export const COLECCIONES = ["vestidos", "vestido-cortos", "enterizos", "blusas", "pantalon", "conjuntos", "sastre", "blazer"];

export const usaValor = (m: Mecanica) => MECANICAS[m]?.valor !== "";

/** Precio de promo de una prenda de precio P (misma tabla que el agente; aquí solo es para la vista previa). */
export function precioPromo(p: number, m: Mecanica, valor: number): { precio: number; nota: string } {
  switch (m) {
    case "porcentaje":
      return { precio: Math.round(p * (1 - valor / 100) * 100) / 100, nota: `${valor}% de descuento` };
    case "monto":
      return { precio: Math.max(p - valor, 0), nota: `S/ ${valor} menos` };
    case "precio_especial":
      return { precio: valor, nota: "Precio especial" };
    case "dos_por":
      return { precio: p, nota: `2 por S/ ${valor.toFixed(2)}` };
    case "envio_gratis":
      return { precio: p, nota: "Envío gratis" };
    default:
      return { precio: p, nota: "Beneficio según condiciones" };
  }
}

/** «20% de descuento en vestidos»: la promo en una línea. */
export function mecanicaTexto(d: Partial<PromoDatos>): string {
  const v = Number(d.valor) || 0;
  const m = d.mecanica ?? "otra";
  const que =
    m === "porcentaje"
      ? `${v}% de descuento`
      : m === "monto"
        ? `S/ ${v} de descuento`
        : m === "precio_especial"
          ? `precio especial S/ ${v}`
          : m === "dos_por"
            ? `2 por S/ ${v}`
            : m === "envio_gratis"
              ? "envío gratis"
              : m === "regalo"
                ? "regalo"
                : "promo";
  return `${que} ${alcanceTexto(d.alcance)}`.trim();
}

// Plural de cada categoría del contrato, para los textos («S/ 30 de descuento en blusas», no «en blusa»).
const PLURAL_CATEGORIA: Record<string, string> = {
  vestido: "vestidos", blusa: "blusas", enterizo: "enterizos", pantalon: "pantalones", conjunto: "conjuntos", blazer: "blazers", falda: "faldas",
};

/** El texto de una fuente sin marcas de markdown, para las tarjetas de la lista. */
export function sinMarkdown(md: string): string {
  return (md || "").replace(/^#{1,6}\s+/gm, "").replace(/\*\*|__|`/g, "").replace(/^\s*[-*]\s+/gm, "• ").replace(/\s+/g, " ").trim();
}

export function alcanceTexto(a?: Partial<AlcancePromo>): string {
  if (!a || !a.tipo || a.tipo === "todo") return "en toda la tienda";
  if (a.tipo === "categoria") return a.categoria ? `en ${PLURAL_CATEGORIA[a.categoria] ?? a.categoria + "s"}` : "en una categoría";
  if (a.tipo === "coleccion") return `en la colección ${a.coleccion || "—"}`;
  const n = a.codigos?.length ?? 0;
  return n === 1 ? "en 1 prenda" : `en ${n} prendas`;
}

export const sinTildes = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

/** Días entre dos fechas AAAA-MM-DD, ambas incluidas (null si falta alguna). */
export function diasVigencia(desde: string, hasta: string): number | null {
  if (!desde || !hasta) return null;
  const a = Date.parse(`${desde}T00:00:00Z`);
  const b = Date.parse(`${hasta}T00:00:00Z`);
  if (isNaN(a) || isNaN(b)) return null;
  return Math.round((b - a) / 86400000) + 1;
}

/** «15 oct → 31 oct», «desde 15 oct», «sin fecha». */
export function vigenciaTexto(desde: string, hasta: string): string {
  const f = (d: string) => new Date(`${d}T12:00:00`).toLocaleDateString("es-PE", { day: "numeric", month: "short" });
  if (desde && hasta) return `${f(desde)} → ${f(hasta)}`;
  if (desde) return `desde ${f(desde)}`;
  if (hasta) return `hasta ${f(hasta)}`;
  return "sin fecha";
}

/** Fecha de hoy en Lima (AAAA-MM-DD), para valores por defecto. */
export function hoyLima(diasMas = 0): string {
  const d = new Date(Date.now() + diasMas * 86400000);
  return d.toLocaleDateString("en-CA", { timeZone: "America/Lima" });
}

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const enLinea = (s: string) =>
  esc(s)
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<strong>$2</strong>")
    .replace(/(^|[\s(])_([^_\n]+)_/g, "$1<em>$2</em>")
    .replace(/`([^`\n]+)`/g, "<code>$1</code>");

/** Markdown mínimo a HTML para la vista previa (escapa todo antes: no pasa HTML del texto). */
export function mdAHtml(md: string): string {
  const out: string[] = [];
  let lista: "ul" | "ol" | null = null;
  let parrafo: string[] = [];
  const cerrarParrafo = () => {
    if (parrafo.length) out.push(`<p>${parrafo.join("<br>")}</p>`);
    parrafo = [];
  };
  const cerrarLista = () => {
    if (lista) out.push(`</${lista}>`);
    lista = null;
  };
  for (const linea of (md || "").replace(/\r/g, "").split("\n")) {
    const t = linea.trim();
    const h = /^(#{1,4})\s+(.*)$/.exec(t);
    const ul = /^[-*•]\s+(.*)$/.exec(t);
    const ol = /^\d+[.)]\s+(.*)$/.exec(t);
    if (!t) {
      cerrarParrafo();
      cerrarLista();
    } else if (h) {
      cerrarParrafo();
      cerrarLista();
      const n = Math.min(h[1].length + 2, 6);
      out.push(`<h${n}>${enLinea(h[2])}</h${n}>`);
    } else if (ul || ol) {
      cerrarParrafo();
      const tipo = ul ? "ul" : "ol";
      if (lista !== tipo) {
        cerrarLista();
        out.push(`<${tipo}>`);
        lista = tipo;
      }
      out.push(`<li>${enLinea((ul ?? ol)![1])}</li>`);
    } else {
      cerrarLista();
      parrafo.push(enLinea(t));
    }
  }
  cerrarParrafo();
  cerrarLista();
  return out.join("");
}

/** Texto en markdown de una promo armada con el asistente (lo que el robot puede decir). */
export function contenidoPromo(titulo: string, d: PromoDatos, desde: string, hasta: string): string {
  const l = [`# ${titulo}`, "", `**Promo:** ${mecanicaTexto(d)}.`];
  if (desde || hasta) l.push(`**Vigencia:** ${vigenciaTexto(desde, hasta)}.`);
  if (d.alcance.tipo === "prendas" && d.alcance.codigos.length) l.push(`**Prendas:** ${d.alcance.codigos.join(", ")}.`);
  if (d.condiciones.trim()) l.push(`**Condiciones:** ${d.condiciones.trim()}`);
  return l.join("\n");
}
