/* =========================================================================
   intro.js — encendido CRT (1,8 s). Saltable; se recuerda en sessionStorage.
   ========================================================================= */
Baruka.register("intro", function (B) {
  var el = document.getElementById("intro");
  if (!el) return;

  var KEY = "baruka_intro_seen";
  var seen = false;
  try { seen = sessionStorage.getItem(KEY) === "1"; } catch (e) {}

  if (B.reduced || seen || typeof gsap === "undefined") {
    el.classList.add("is-done");
    return;
  }

  var logo = el.querySelector(".intro__logo");
  var text = logo.getAttribute("data-logo") || "BARUKA";
  logo.innerHTML = text.split("").map(function (c) {
    return "<span>" + c + "</span>";
  }).join("");
  var spans = B.qsa("span", logo);

  B.lockScroll(true);

  var finished = false;
  function finish() {
    if (finished) return;
    finished = true;
    try { sessionStorage.setItem(KEY, "1"); } catch (e) {}
    el.classList.add("is-done");
    B.lockScroll(false);
    window.removeEventListener("click", skip);
    window.removeEventListener("keydown", skip);
    window.dispatchEvent(new CustomEvent("baruka:intro-done"));
    B.refresh();
  }
  function skip() { tl.progress(1); }

  var tl = gsap.timeline({ onComplete: finish });

  // Línea horizontal que se abre a lo ancho
  tl.fromTo(".intro__beam", { scaleX: 0 }, { scaleX: 1, duration: 0.45, ease: "expo.out" })
    // El tubo se abre en vertical con parpadeo de 2-3 frames
    .set(".intro__flicker", { opacity: 1, transformOrigin: "50% 50%" })
    .fromTo(".intro__flicker", { scaleY: 0.004 }, { scaleY: 1, duration: 0.5, ease: "expo.out" }, "<0.03")
    .to(".intro__flicker", { opacity: 0.12, duration: 0.03, repeat: 2, yoyo: true, ease: "none" })
    .to(".intro__scanlines", { opacity: 0, duration: 0.45 }, "<")
    .to(".intro__flicker", { opacity: 0, duration: 0.35 }, "<0.15")
    // Logotipo: aberración cromática breve + blur -> nítido, letra por letra
    .fromTo(logo,
      { textShadow: "-4px 0 rgba(255,0,0,.8), 4px 0 rgba(0,255,255,.8)" },
      { textShadow: "0 0 rgba(0,0,0,0)", duration: 0.55, ease: "power2.out" }, "-=0.15")
    .fromTo(spans,
      { opacity: 0, filter: "blur(14px)", y: "0.12em" },
      { opacity: 1, filter: "blur(0px)", y: 0, duration: 0.5, stagger: 0.06, ease: "power2.out" }, "<")
    .to(".intro__hint", { opacity: 1, duration: 0.3 }, "-=0.1");

  window.addEventListener("click", skip);
  window.addEventListener("keydown", skip);
});
