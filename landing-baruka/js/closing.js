/* =========================================================================
   closing.js — "Encuéntranos" se rellena de color con el scroll.
   ========================================================================= */
Baruka.register("closing", function (B) {
  var title = B.qs(".closing__title");
  if (!title) return;
  if (B.reduced || typeof ScrollTrigger === "undefined") return;

  gsap.fromTo(title,
    { backgroundSize: "0% 100%" },
    {
      backgroundSize: "100% 100%",
      ease: "none",
      scrollTrigger: { trigger: ".closing", start: "top 82%", end: "bottom 62%", scrub: true }
    });
});
