import { defineConfig } from "vite";
import { qwikVite } from "@builder.io/qwik/optimizer";
import { qwikCity } from "@builder.io/qwik-city/vite";
import tsconfigPaths from "vite-tsconfig-paths";

// En desarrollo, /api y /media van al backend Go local.
const backend = process.env.BACKEND_URL ?? "http://localhost:8080";

export default defineConfig(() => ({
  // "/" o "/baruka/" si el panel va detrás de un alias de nginx (Dockerfile: ARG BASE_PATH).
  base: process.env.BASE_PATH || "/",
  // Marca del producto («Baruka Design» o «JMD Ventas»). El nombre de la empresa lo da el backend en tiempo de
  // ejecución (src/lib/marca.ts).
  define: { __MARCA__: JSON.stringify(process.env.MARCA || "Baruka Design") },
  // Con ruta base y trailingSlash:false, el SSG no genera la página de inicio: «/baruka/» no es «/» y la
  // trata como una ruta con barra de más. Quedaba el index.html de fábrica de nginx («Welcome to nginx!»).
  plugins: [qwikCity({ trailingSlash: (process.env.BASE_PATH || "/") !== "/" }), qwikVite(), tsconfigPaths()],
  server: {
    proxy: {
      "/api": { target: backend, changeOrigin: true },
      "/media": { target: backend, changeOrigin: true },
    },
  },
  preview: {
    headers: { "Cache-Control": "public, max-age=600" },
  },
}));
