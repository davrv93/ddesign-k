/* =========================================================================
   audio.js — música de fondo (audio/sober.mp3) con botón para silenciar.
   Los navegadores no dejan sonar audio sin un gesto: arranca con el primer
   clic/tecla/toque (el clic que salta el intro sirve). Se recuerda en
   localStorage si la persona la silenció. Se pausa con la pestaña oculta.
   ========================================================================= */
Baruka.register("audio", function (B) {
  var audio = document.getElementById("bg-audio");
  var btn = document.getElementById("sound-toggle");
  if (!audio || !btn) return;

  var KEY = "baruka_audio_muted";
  var VOLUME = 0.35;
  var muted = false;
  try { muted = localStorage.getItem(KEY) === "1"; } catch (e) {}

  var fade = null;
  function fadeTo(target, ms, done) {
    if (fade) cancelAnimationFrame(fade);
    var from = audio.volume, t0 = performance.now();
    (function step(now) {
      var k = B.clamp((now - t0) / ms, 0, 1);
      audio.volume = from + (target - from) * k;   // en iOS es de sólo lectura: el fundido no hace nada y no pasa nada
      if (k < 1) fade = requestAnimationFrame(step);
      else { fade = null; if (done) done(); }
    })(t0);
  }

  function paint(playing) {
    btn.setAttribute("aria-pressed", playing ? "false" : "true");
    btn.setAttribute("aria-label", playing ? "Silenciar música" : "Activar música");
    btn.title = playing ? "Silenciar música" : "Activar música";
    btn.classList.toggle("is-playing", playing);
  }

  function play() {
    audio.volume = 0;
    var p = audio.play();
    if (p && p.then) {
      p.then(function () { paint(true); fadeTo(VOLUME, 1200); })
       .catch(function () { paint(false); });   // sin gesto todavía: el botón queda en «Activar música»
    } else { paint(true); fadeTo(VOLUME, 1200); }
  }
  function pause() {
    paint(false);
    fadeTo(0, 400, function () { audio.pause(); });
  }

  btn.addEventListener("click", function (ev) {
    ev.stopPropagation();                         // no cuenta como «saltar intro» ni como primer gesto
    unlockOff();
    if (audio.paused) { muted = false; play(); }
    else { muted = true; pause(); }
    try { localStorage.setItem(KEY, muted ? "1" : "0"); } catch (e) {}
  });

  /* Primer gesto de la página: si no la silenció antes, empieza la música. */
  var GESTOS = ["pointerdown", "keydown", "touchend"];
  function unlock(ev) {
    if (ev && ev.target && btn.contains(ev.target)) return;   // el botón decide por sí mismo
    unlockOff();
    if (!muted && audio.paused) play();
  }
  function unlockOff() { GESTOS.forEach(function (g) { window.removeEventListener(g, unlock, true); }); }
  GESTOS.forEach(function (g) { window.addEventListener(g, unlock, true); });

  var wasPlaying = false;
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) { wasPlaying = !audio.paused; if (wasPlaying) audio.pause(); }
    else if (wasPlaying && !muted) { audio.play().catch(function () {}); }
  });

  paint(false);
});
