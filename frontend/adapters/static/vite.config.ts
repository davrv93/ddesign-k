import { staticAdapter } from "@builder.io/qwik-city/adapters/static/vite";
import { extendConfig } from "@builder.io/qwik-city/vite";
import baseConfig from "../../vite.config";

// Genera el sitio estático (dist/) que sirve el contenedor nginx del frontend.
export default extendConfig(baseConfig, () => ({
  build: {
    ssr: true,
    rollupOptions: { input: ["@qwik-city-plan"] },
  },
  plugins: [staticAdapter({ origin: process.env.PUBLIC_URL ?? "https://kddesign.pjgfactsalud.com.pe" })],
}));
