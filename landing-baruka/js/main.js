/* =========================================================================
   main.js — orquesta el arranque. Todos los módulos ya se registraron.
   ========================================================================= */
(function () {
  function boot() { if (window.Baruka) window.Baruka.start(); }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
