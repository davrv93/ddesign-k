/* =========================================================================
   core.js — estado compartido, utilidades y motor de scroll.
   Carga primero. Expone window.Baruka para el resto de los módulos.
   ========================================================================= */
(function () {
  "use strict";

  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var desktop = window.matchMedia("(min-width: 901px)");

  var Baruka = {
    /* --- Configuración editable (único lugar para teléfono y catálogo) --- */
    config: {
      whatsapp: "51999999999",        // sólo dígitos, con código de país
      whatsappText: "Hola Baruka Design, quiero ver la colección",
      catalogUrl: "https://proyectopostventa.site/baruka/catalogo"
    },

    reduced: reduced,
    desktop: desktop,
    modules: [],
    scrollVelocity: 0,

    register: function (name, init) {
      this.modules.push({ name: name, init: init });
    },

    /* --- Utilidades numéricas --- */
    clamp: function (v, min, max) { return v < min ? min : v > max ? max : v; },
    lerp: function (a, b, t) { return a + (b - a) * t; },
    qs: function (sel, ctx) { return (ctx || document).querySelector(sel); },
    qsa: function (sel, ctx) { return Array.prototype.slice.call((ctx || document).querySelectorAll(sel)); },

    /* --- Interpola dos colores #rrggbb --- */
    mixHex: function (a, b, t) {
      function p(h) { return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)]; }
      var A = p(a), B = p(b), o = "#";
      for (var i = 0; i < 3; i++) {
        var v = Math.round(A[i] + (B[i] - A[i]) * t).toString(16);
        o += v.length < 2 ? "0" + v : v;
      }
      return o;
    },

    /* --- Marcador 3:4 si falta una foto --- */
    placeholder: function (label) {
      var svg = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 300 400'>" +
        "<rect width='300' height='400' fill='#ddd6c9'/>" +
        "<rect x='16' y='16' width='268' height='368' fill='none' stroke='#0b0b0c' stroke-opacity='.12'/>" +
        "<text x='150' y='196' fill='#0b0b0c' fill-opacity='.55' font-family='Georgia,serif'" +
        " font-size='17' font-style='italic' text-anchor='middle'>" + String(label).slice(0, 28) + "</text>" +
        "<text x='150' y='220' fill='#0b0b0c' fill-opacity='.35' font-family='Georgia,serif'" +
        " font-size='10' letter-spacing='2' text-anchor='middle'>SIN FOTO — 3:4</text></svg>";
      return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
    },

    /* --- Arranque, invocado por main.js --- */
    start: function () {
      this.applyStatic();
      this.initScroll();
      var self = this;
      this.modules.forEach(function (m) {
        try { m.init(self); }
        catch (err) { console.warn("[baruka] módulo " + m.name + " falló:", err); }
      });
      this.refresh();
      if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(function () { self.refresh(); });
      }
      window.addEventListener("resize", function () { self.refresh(); }, { passive: true });
    },

    /* Enlaces de contacto, año y fotos de marcador */
    applyStatic: function () {
      var self = this;
      var wa = "https://wa.me/" + this.config.whatsapp + "?text=" + encodeURIComponent(this.config.whatsappText);
      this.qsa("[data-whatsapp]").forEach(function (a) { a.href = wa; });
      this.qsa("[data-catalog]").forEach(function (a) { a.href = self.config.catalogUrl; });
      this.qsa("[data-year]").forEach(function (el) { el.textContent = new Date().getFullYear(); });

      this.qsa("img").forEach(function (img) {
        if (img.dataset.ph) return;
        var fallback = function () {
          if (img.dataset.ph) return;
          img.dataset.ph = "1";
          img.src = self.placeholder(img.alt || "Baruka Design");
        };
        img.addEventListener("error", fallback, { once: true });
        if (img.complete && img.naturalWidth === 0) fallback();
      });
    },

    /* Scroll suave con Lenis + GSAP unificados */
    initScroll: function () {
      if (typeof gsap === "undefined") return;
      if (typeof ScrollTrigger !== "undefined") gsap.registerPlugin(ScrollTrigger);

      if (this.reduced || typeof Lenis === "undefined") return;
      // lerp alto = el scroll sigue al dedo/rueda casi al instante (antes duration 1.1 s: se sentía pesado).
      var lenis = new Lenis({ lerp: 0.16, smoothWheel: true, touchMultiplier: 1.2 });
      this.lenis = lenis;
      lenis.on("scroll", function (e) {
        if (typeof ScrollTrigger !== "undefined") ScrollTrigger.update();
        Baruka.scrollVelocity = e.velocity || 0;
      });
      gsap.ticker.add(function (t) { lenis.raf(t * 1000); });
      gsap.ticker.lagSmoothing(0);
    },

    refresh: function () {
      if (typeof ScrollTrigger !== "undefined") ScrollTrigger.refresh();
    },

    lockScroll: function (on) {
      document.documentElement.classList.toggle("intro-lock", on);
      if (this.lenis) { on ? this.lenis.stop() : this.lenis.start(); }
    }
  };

  window.Baruka = Baruka;
})();
