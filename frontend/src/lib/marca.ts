// Marca del producto y nombre de la empresa.
//
// MARCA se fija al compilar (variable MARCA → vite.config.ts → __MARCA__): «Baruka Design» en el panel de una
// tienda, «JMD Ventas» en el multiempresa. El nombre de la EMPRESA no se compila: lo da el backend en
// /api/public/info según la ruta (/jmdventas/<empresa>/), así una sola compilación sirve a todas.
import { BASE, u } from "./base";

declare const __MARCA__: string;

export const MARCA: string = typeof __MARCA__ !== "undefined" && __MARCA__ ? __MARCA__ : "Baruka Design";

/** Segunda línea bajo el nombre de la empresa. */
export const LEMA = MARCA === "Baruka Design" ? "CRM WhatsApp" : MARCA;

export const titulo = (pagina: string) => `${pagina} · ${MARCA}`;

/** Intro animada (frontend/jmdventas/): solo en JMD Ventas; la sirve su nginx en /jmdventas/_jmd/. Primero los
 *  figurines (figuras.js) y luego la intro, en ese orden. ?v= evita mezclar versiones en la caché del navegador. */
const V_INTRO = "20261007d";
export const INTRO_URLS = MARCA === "JMD Ventas" ? [`/jmdventas/_jmd/figuras.js?v=${V_INTRO}`, `/jmdventas/_jmd/intro.js?v=${V_INTRO}`] : [];

export const iniciales = (nombre: string) =>
  nombre
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase() || "CRM";

const CLAVE = `kd_empresa:${BASE}`;

/** Último nombre conocido de la empresa (para pintar sin esperar a la red). */
export const empresaGuardada = (): string => {
  try {
    return localStorage.getItem(CLAVE) || MARCA;
  } catch {
    return MARCA;
  }
};

/** Nombre de la empresa según el backend. */
export async function cargarEmpresa(): Promise<string> {
  try {
    const r = await fetch(u("/api/public/info"));
    const d = (await r.json()) as { business?: string };
    if (d.business) {
      try {
        localStorage.setItem(CLAVE, d.business);
      } catch {
        /* modo privado */
      }
      return d.business;
    }
  } catch {
    /* sin conexión */
  }
  return empresaGuardada();
}
