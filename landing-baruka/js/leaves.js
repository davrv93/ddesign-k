/* =========================================================================
   leaves.js — hojas azules en caída, capa global tras el hero.
   Dos canvas: una capa detrás del contenido y otra por delante.
   El scroll empuja (viento). Máx. 36 partículas en escritorio, 14 en móvil.

   Rendimiento: cada hoja se dibuja UNA vez como sprite (color × profundidad;
   la capa trasera con el desenfoque ya horneado) y en cada cuadro sólo se
   copia con drawImage. Antes se aplicaba ctx.filter = blur() por cuadro y se
   trazaban curvas Bézier por hoja: era lo que más pesaba de la página.
   ========================================================================= */
Baruka.register("leaves", function (B) {
  if (B.reduced) return;

  var back = document.getElementById("leaves-back");
  var front = document.getElementById("leaves-front");
  if (!back || !front) return;

  var COLORS = ["#1F3FBF", "#A9C4F5"];
  var SCALES = [0.55, 1, 1.7];
  var BASE = 16;              // tamaño de referencia del sprite (px CSS, escala 1)
  var dpr = 1;                // hojas suaves: no ganan nada a 2x y cuestan 4 veces más
  var W = 0, H = 0;

  function sizeCanvas(c) {
    c.width = Math.round(W * dpr);
    c.height = Math.round(H * dpr);
    c.style.width = W + "px";
    c.style.height = H + "px";
  }
  function onResize() {
    W = window.innerWidth; H = window.innerHeight;
    sizeCanvas(back); sizeCanvas(front);
  }
  onResize();

  /* Sprite de una hoja: [capa][color][profundidad] -> canvas pequeño. */
  function makeSprite(color, scale, blur) {
    var s = BASE * scale;
    var pad = Math.ceil(blur * 2 + 2);
    var w = Math.ceil(s * 1.6 + pad * 2), h = Math.ceil(s * 2 + pad * 2);
    var c = document.createElement("canvas");
    c.width = w; c.height = h;
    var g = c.getContext("2d");
    if (blur) g.filter = "blur(" + blur + "px)";
    g.translate(w / 2, h / 2);
    g.fillStyle = color;
    g.beginPath();
    g.moveTo(0, -s);
    g.bezierCurveTo(s * 0.72, -s * 0.3, s * 0.72, s * 0.3, 0, s);
    g.bezierCurveTo(-s * 0.72, s * 0.3, -s * 0.72, -s * 0.3, 0, -s);
    g.fill();
    return c;
  }
  var sprites = [0, 1].map(function (layer) {
    return COLORS.map(function (color) {
      return SCALES.map(function (scale) { return makeSprite(color, scale, layer === 0 ? 2 : 0); });
    });
  });

  function makeParticle() {
    var depth = Math.floor(Math.random() * 3);
    return {
      x: Math.random() * W,
      y: Math.random() * H,
      size: (0.45 + Math.random() * 0.55),          // escala sobre el sprite
      depth: depth,
      base: (14 + Math.random() * 16) * (0.55 + depth * 0.5),
      rot: Math.random() * Math.PI * 2,
      rv: (Math.random() - 0.5) * 1.6,
      phase: Math.random() * Math.PI * 2,
      sway: 0.4 + Math.random() * 0.8,
      swayAmp: 10 + Math.random() * 26,
      color: Math.random() < 0.35 ? 1 : 0,
      alpha: 0.16 + depth * 0.12 + Math.random() * 0.1
    };
  }

  var total = B.desktop.matches ? 36 : 14;
  var backCount = Math.round(total * 0.55);
  var lists = [
    { ctx: back.getContext("2d"), items: [], layer: 0 },
    { ctx: front.getContext("2d"), items: [], layer: 1 }
  ];
  for (var i = 0; i < total; i++) {
    lists[i < backCount ? 0 : 1].items.push(makeParticle());
  }

  var last = performance.now();
  var lastScrollY = window.scrollY;
  var raf = null;

  function frame(now) {
    var dt = Math.min((now - last) / 1000, 0.05);
    last = now;

    var scrollY = window.scrollY;
    var scVel = B.clamp((scrollY - lastScrollY) / (dt || 0.016) / 1400, -1.2, 1.2);
    lastScrollY = scrollY;
    B.scrollVelocity = scVel;

    var t = now / 1000;
    for (var l = 0; l < lists.length; l++) {
      var layer = lists[l];
      var ctx = layer.ctx;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, W * dpr, H * dpr);
      for (var k = 0; k < layer.items.length; k++) {
        var p = layer.items[k];
        var wind = Math.abs(scVel) * (60 + p.depth * 90) * (scVel < 0 ? -0.35 : 1);
        p.y += (p.base + wind) * dt;
        p.rot += p.rv * dt;
        var img = sprites[layer.layer][p.color][p.depth];
        var hh = img.height * p.size;
        if (p.y > H + hh) { p.y = -hh; p.x = Math.random() * W; }
        else if (p.y < -hh * 2) { p.y = H + hh; }

        var x = p.x + Math.sin(t * p.sway + p.phase) * p.swayAmp;
        var c = Math.cos(p.rot), s = Math.sin(p.rot), sq = Math.cos(p.rot * 0.7); // giro 3D simulado
        var sc = p.size * dpr;
        ctx.setTransform(c * sc * sq, s * sc * sq, -s * sc, c * sc, x * dpr, p.y * dpr);
        ctx.globalAlpha = p.alpha;
        ctx.drawImage(img, -img.width / 2, -img.height / 2);
      }
      ctx.globalAlpha = 1;
    }
    raf = document.hidden ? null : requestAnimationFrame(frame);
  }

  function start() { if (!raf) { last = performance.now(); lastScrollY = window.scrollY; raf = requestAnimationFrame(frame); } }
  function stop() { if (raf) cancelAnimationFrame(raf); raf = null; }

  document.addEventListener("visibilitychange", function () { document.hidden ? stop() : start(); });
  window.addEventListener("resize", onResize, { passive: true });
  start();
});
