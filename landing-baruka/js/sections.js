/* =========================================================================
   sections.js — historia, categorías y «cómo comprar» (después del hero).
   Animaciones baratas: sólo opacity/transform, una vez o con scrub ligero.
   Sin JS o con movimiento reducido, todo se ve igual, sin animar.
   ========================================================================= */
Baruka.register("sections", function (B) {
  if (B.reduced || typeof ScrollTrigger === "undefined") return;

  // Historia: el titular se «enciende» palabra por palabra al hacer scroll.
  var title = B.qs(".story__title");
  if (title) {
    var html = title.innerHTML.replace(/(<em>.*?<\/em>|[^\s<]+)/g, '<span class="w">$1</span>');
    title.innerHTML = html;
    gsap.fromTo(B.qsa(".w", title), { opacity: 0.12 }, {
      opacity: 1, stagger: 0.08, ease: "none",
      scrollTrigger: { trigger: title, start: "top 80%", end: "bottom 45%", scrub: 0.4 }
    });
  }
  gsap.from(".story__body > *", {
    y: 30, opacity: 0, duration: 0.9, stagger: 0.15, ease: "power2.out",
    scrollTrigger: { trigger: ".story__body", start: "top 85%", once: true }
  });

  // Categorías: las tarjetas suben escalonadas.
  gsap.from(".cat", {
    y: 60, opacity: 0, duration: 0.9, stagger: 0.1, ease: "power3.out",
    scrollTrigger: { trigger: ".cats__grid", start: "top 85%", once: true }
  });

  // Cómo comprar.
  gsap.from(".perk", {
    y: 24, opacity: 0, duration: 0.7, stagger: 0.1, ease: "power2.out",
    scrollTrigger: { trigger: ".perks__list", start: "top 88%", once: true }
  });
});
