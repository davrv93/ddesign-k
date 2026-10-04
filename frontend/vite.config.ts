import { defineConfig } from "vite";
import { qwikVite } from "@builder.io/qwik/optimizer";
import { qwikCity } from "@builder.io/qwik-city/vite";
import tsconfigPaths from "vite-tsconfig-paths";

// En desarrollo, /api y /media van al backend Go local.
const backend = process.env.BACKEND_URL ?? "http://localhost:8080";

export default defineConfig(() => ({
  // "/" o "/baruka/" si el panel va detrás de un alias de nginx (Dockerfile: ARG BASE_PATH).
  base: process.env.BASE_PATH || "/",
  plugins: [qwikCity({ trailingSlash: false }), qwikVite(), tsconfigPaths()],
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
