/* =========================================================================
   hero.js — pin de 200 vh: las letras se separan y la foto aparece entre ellas.
   ========================================================================= */
Baruka.register("hero", function (B) {
  if (B.reduced || typeof ScrollTrigger === "undefined") return;

  var mm = gsap.matchMedia();

  function build(endPct, spread, fadeTitle) {
    return function () {
      var tl = gsap.timeline({
        scrollTrigger: {
          trigger: ".hero",
          start: "top top",
          end: "+=" + endPct + "%",
          pin: ".hero__pin",
          scrub: 0.4,
          anticipatePin: 1,
          invalidateOnRefresh: true
        }
      });
      tl.fromTo(".hero__media",
          { clipPath: "inset(0% 50% 0% 50%)" },
          { clipPath: "inset(0% 0% 0% 0%)", ease: "none", duration: 1 }, 0)
        .fromTo(".hero__half--l", { x: 0 }, { x: "-" + spread + "vw", ease: "none", duration: 1 }, 0)
        .fromTo(".hero__half--r", { x: 0 }, { x: spread + "vw", ease: "none", duration: 1 }, 0)
        .to(".hero__title", { color: "#F5F1EA", ease: "none", duration: 0.7 }, 0.15)
        .to(".hero__sub", { y: "-28vh", opacity: 0, color: "#F5F1EA", ease: "none", duration: 0.8 }, 0)
        .to(".hero__meta", { opacity: 0, ease: "none", duration: 0.4 }, 0);
      if (fadeTitle) tl.to(".hero__title", { opacity: 0.12, ease: "none", duration: 0.5 }, 0.6);
    };
  }

  mm.add("(prefers-reduced-motion: no-preference) and (min-width: 901px)", build(130, 22, true));
  mm.add("(prefers-reduced-motion: no-preference) and (max-width: 900px)", build(70, 34, false));

  window.addEventListener("baruka:intro-done", B.refresh);
});
