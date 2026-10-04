/* =========================================================================
   gallery.js — scroll vertical que se vuelve horizontal (escritorio).
   La tarjeta centrada crece; las demás se desaturan. En móvil: swipe nativo.
   ========================================================================= */
Baruka.register("gallery", function (B) {
  var section = B.qs(".gallery");
  var viewport = B.qs(".gallery__viewport");
  var track = B.qs(".gallery__track");
  if (!section || !viewport || !track) return;

  var cards = B.qsa(".card", track);

  // Segunda foto al tocar en pantallas táctiles.
  if ("ontouchstart" in window) {
    cards.forEach(function (card) {
      card.addEventListener("click", function () {
        cards.forEach(function (c) { if (c !== card) c.classList.remove("is-touch"); });
        card.classList.toggle("is-touch");
      });
    });
  }

  if (B.reduced || typeof ScrollTrigger === "undefined") return;

  /* Tarjeta centrada sin medir las 13 tarjetas en cada cuadro: se guarda una vez la posición
     de cada centro RELATIVA al carril (las dos medidas en el mismo instante, así el transform se
     cancela) y en cada cuadro sólo se lee dónde está el carril. */
  var rel = [], current = -1;
  function measure() {
    var t = track.getBoundingClientRect().left;
    rel = cards.map(function (card) { var r = card.getBoundingClientRect(); return r.left - t + r.width / 2; });
  }
  function highlight() {
    if (!rel.length) measure();
    var left = track.getBoundingClientRect().left;
    var mid = window.innerWidth / 2;
    var best = 0, bestDist = Infinity;
    for (var k = 0; k < rel.length; k++) {
      var d = Math.abs(left + rel[k] - mid);
      if (d < bestDist) { bestDist = d; best = k; }
    }
    if (best === current) return;
    current = best;
    cards.forEach(function (card, k) {
      card.classList.toggle("is-active", k === best);
      card.classList.toggle("is-dim", k !== best);
    });
  }

  var mm = gsap.matchMedia();
  mm.add("(prefers-reduced-motion: no-preference) and (min-width: 901px)", function () {
    function distance() { return Math.max(0, track.scrollWidth - viewport.clientWidth); }
    var tween = gsap.to(track, {
      x: function () { return -distance(); },
      ease: "none",
      onUpdate: highlight,          // el carril llega tarde (scrub): se mide cuando de verdad se mueve
      scrollTrigger: {
        trigger: section,
        start: "top top",
        end: function () { return "+=" + distance(); },
        pin: true,
        scrub: 0.4,
        anticipatePin: 1,
        invalidateOnRefresh: true,
        onRefresh: function () { measure(); current = -1; highlight(); }
      }
    });
    highlight();
    return function () { tween.scrollTrigger && tween.scrollTrigger.kill(); tween.kill(); };
  });
});
