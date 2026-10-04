// Ruta base del panel: "/" con dominio propio, o "/baruka/" detrás de un alias del nginx de otro sitio.
// La fija BASE_PATH al compilar (vite.config.ts → base).
export const BASE = import.meta.env.BASE_URL.replace(/\/$/, "");

/** Antepone la ruta base a una ruta absoluta del sitio ("/api/…", "/media/…", "/login").
 *  Deja igual las URL completas, blob: y data:. */
export const u = (p?: string | null): string => (p && p.startsWith("/") && !p.startsWith("//") ? BASE + p : (p ?? ""));
