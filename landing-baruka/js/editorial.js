/* =========================================================================
   editorial.js — bloque tipo revista: parallax inverso y contadores discretos.
   ========================================================================= */
Baruka.register("editorial", function (B) {
  var section = B.qs(".editorial");
  if (!section) return;

  var counters = B.qsa(".stat__num", section);

  function renderCounter(el, value) {
    el.textContent = (el.dataset.prefix || "") + Math.round(value) + (el.dataset.suffix || "");
  }

  if (B.reduced || typeof ScrollTrigger === "undefined") {
    counters.forEach(function (el) { renderCounter(el, parseFloat(el.dataset.count || "0")); });
    return;
  }

  counters.forEach(function (el) {
    var end = parseFloat(el.dataset.count || "0");
    var obj = { v: 0 };
    renderCounter(el, 0);
    gsap.to(obj, {
      v: end, duration: 1.6, ease: "power2.out",
      onUpdate: function () { renderCounter(el, obj.v); },
      scrollTrigger: { trigger: el, start: "top 88%", once: true }
    });
  });

  gsap.fromTo(".editorial__media--a",
    { y: "6vh" }, { y: "-10vh", ease: "none",
      scrollTrigger: { trigger: section, start: "top bottom", end: "bottom top", scrub: true } });
  gsap.fromTo(".editorial__media--b",
    { y: "-6vh" }, { y: "10vh", ease: "none",
      scrollTrigger: { trigger: section, start: "top bottom", end: "bottom top", scrub: true } });

  gsap.fromTo(".editorial__quote",
    { opacity: 0, y: 40 }, { opacity: 1, y: 0, duration: 1, ease: "power2.out",
      scrollTrigger: { trigger: ".editorial__quote", start: "top 85%", once: true } });
});
