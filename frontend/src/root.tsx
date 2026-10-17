import { component$ } from "@builder.io/qwik";
import { QwikCityProvider, RouterOutlet } from "@builder.io/qwik-city";
import { RouterHead } from "./components/router-head";
import "./global.css";
import "./crm.css";

export default component$(() => {
  return (
    <QwikCityProvider>
      <head>
        <meta charset="utf-8" />
        <RouterHead />
        {/* Tras un despliegue, una pestaña abierta antes pide archivos de la versión anterior que ya no existen y la página se
            queda en «Cargando…» (17-10-2026). Si falla la carga de un archivo de código, se recarga una vez (como mucho cada
            30 s) y toma la versión nueva. */}
        <script
          dangerouslySetInnerHTML={`(function(){var K="kd_recarga";function r(){try{var t=+sessionStorage.getItem(K)||0;if(Date.now()-t<30000)return;sessionStorage.setItem(K,String(Date.now()))}catch(e){}location.reload()}document.addEventListener("qerror",r);window.addEventListener("unhandledrejection",function(e){var m=String(e&&e.reason&&(e.reason.message||e.reason)||"");if(/dynamically imported module|Importing a module script failed|error loading dynamically/i.test(m))r()});window.addEventListener("error",function(e){var t=e&&e.target;if(t&&t.tagName==="SCRIPT"&&/\/build\/q-/.test(t.src||""))r()},true)})();`}
        />
      </head>
      <body lang="es">
        <RouterOutlet />
      </body>
    </QwikCityProvider>
  );
});
