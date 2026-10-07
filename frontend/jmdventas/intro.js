/* Intro de JMD Ventas (portada /jmdventas/ y login de cada empresa).
 *
 * 1. Apertura editorial en blanco y negro: cabecera didona enorme que entra con tracking amplio y cortes diagonales.
 * 2. Fondo azul profundo → púrpura con partículas en constelación (puntos unidos por líneas finas, destellos y rayos
 *    de luz). Las constelaciones dibujan tres vestidos: gala larga con capa, lápiz de un hombro y falda amplia con
 *    volantes (trazados en canvas, sin imágenes).
 * 3. «Inteligencia Artificial a tu servicio · Consultoría DIGITAL» con el logo, y fundido a la página.
 *
 * Unos 8 s, en cada carga (también con F5), con «Saltar» y Escape; con prefers-reduced-motion no se muestra. No
 * bloquea: la página de abajo carga mientras tanto. Todo lo mueve un único reloj en JS: ?intro=1 la fuerza (aun con
 * movimiento reducido), ?intro=0 la omite (pruebas) y ?intro_t=3.4 congela ese instante (capturas).
 *
 * Los figurines vienen de figuras.js (window.JMDFiguras), que la página carga antes que este archivo.
 */
(function () {
  "use strict";
  // Figurines: figuras.js (window.JMDFiguras), que se carga antes. Sin él no hay intro, y la página sigue igual.
  var FIG = window.JMDFiguras;
  if (!FIG) return;
  var FIGURAS = FIG.figuras, suave = FIG.suave;

  var qs = new URLSearchParams(location.search);
  var forzar = qs.get("intro") === "1" || qs.has("intro_t");
  var congelado = qs.has("intro_t") ? parseFloat(qs.get("intro_t")) || 0 : null;
  function marcar() { /* ya no se recuerda: la intro sale en cada carga */ }
  var reducido = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (qs.get("intro") === "0" || (!forzar && reducido)) return;
  if (document.getElementById("jmd-intro")) return;

  var yo = document.currentScript && document.currentScript.src;
  var base = yo ? yo.replace(/[^/]*$/, "") : "/jmdventas/_jmd/";

  // ---------------------------------------------------------------- tiempos (s)
  var FIN_APERTURA = 2.25, INICIO_CIELO = 1.55, INICIO_TEXTO = 5.85, INICIO_SALIDA = 7.45, FIN = 8.1;

  // ---------------------------------------------------------------- estilos y DOM
  if (!document.querySelector('link[data-jmd-intro]')) {
    var lf = document.createElement("link");
    lf.rel = "stylesheet"; lf.setAttribute("data-jmd-intro", "");
    lf.href = "https://fonts.googleapis.com/css2?family=Bodoni+Moda:ital,opsz,wght@0,6..96,400;0,6..96,700;0,6..96,900;1,6..96,500&family=Inter:wght@500;600&display=swap";
    document.head.appendChild(lf);
  }
  var css = [
    "#jmd-intro{position:fixed;inset:0;z-index:2147483000;overflow:hidden;background:#05051a;color:#fff;-webkit-font-smoothing:antialiased}",
    "#jmd-intro canvas{position:absolute;inset:0;width:100%;height:100%;display:block}",
    "#jmd-intro .ji-ap{position:absolute;inset:0;background:#f7f5f0;display:flex;flex-direction:column;align-items:center;justify-content:center;overflow:hidden}",
    "#jmd-intro .ji-cut{position:absolute;inset:0;background:#0b0b0d}",
    "#jmd-intro .ji-cap{position:relative;font:600 11px/1 Inter,system-ui,sans-serif;letter-spacing:.42em;text-transform:uppercase;color:#fff;mix-blend-mode:difference;margin-bottom:2.2vh}",
    "#jmd-intro .ji-mast{position:relative;display:flex;font-family:'Bodoni Moda','Didot','Bodoni 72','Bodoni MT',serif;font-weight:900;font-size:min(46vw,46vh);line-height:.8;color:#fff;mix-blend-mode:difference;font-optical-sizing:auto}",
    "#jmd-intro .ji-mast span{display:inline-block;will-change:transform,opacity}",
    "#jmd-intro .ji-rule{position:relative;width:min(70vw,620px);height:2px;background:#fff;mix-blend-mode:difference;margin:3.2vh 0 2.4vh;transform-origin:left center}",
    "#jmd-intro .ji-sub{position:relative;font:400 clamp(13px,2.4vw,22px)/1 'Bodoni Moda',Didot,serif;letter-spacing:.6em;text-transform:uppercase;color:#fff;mix-blend-mode:difference;padding-left:.6em}",
    "#jmd-intro .ji-fin{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:0 24px;pointer-events:none;background:radial-gradient(ellipse min(80vw,760px) min(46vh,380px) at 50% 52%,rgba(6,6,30,.78),rgba(6,6,30,.45) 55%,rgba(6,6,30,0) 100%)}",
    "#jmd-intro .ji-logo{width:clamp(68px,10vw,96px);height:clamp(68px,10vw,96px);border-radius:50%;background:#fff;box-shadow:0 0 0 1px rgba(255,255,255,.35),0 0 44px 6px rgba(120,120,255,.45);margin-bottom:22px;object-fit:cover}",
    "#jmd-intro .ji-l1{font:italic 500 clamp(27px,5.4vw,58px)/1.08 'Bodoni Moda',Didot,serif;letter-spacing:-.01em;max-width:13em;text-shadow:0 2px 30px rgba(60,30,140,.6)}",
    "#jmd-intro .ji-sep{width:44px;height:1px;background:rgba(255,255,255,.55);margin:20px 0 16px}",
    "#jmd-intro .ji-l2{font:400 clamp(17px,2.4vw,24px)/1 'Bodoni Moda',Didot,serif;letter-spacing:.02em;color:#e9e6ff}",
    "#jmd-intro .ji-l2 b{font:600 .78em/1 Inter,system-ui,sans-serif;letter-spacing:.42em;margin-left:.5em;color:#fff}",
    "#jmd-intro .ji-skip{position:absolute;right:max(16px,env(safe-area-inset-right));bottom:max(18px,env(safe-area-inset-bottom));z-index:3;appearance:none;border:1px solid rgba(255,255,255,.45);background:rgba(10,8,30,.35);color:#fff;font:600 13px/1 Inter,system-ui,sans-serif;letter-spacing:.08em;padding:11px 16px;border-radius:999px;cursor:pointer;-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px);mix-blend-mode:normal}",
    "#jmd-intro .ji-skip:hover{background:rgba(255,255,255,.14)}",
    "#jmd-intro .ji-skip:focus-visible{outline:2px solid #fff;outline-offset:2px}"
  ].join("\n");
  var st = document.createElement("style");
  st.textContent = css;
  document.head.appendChild(st);

  var root = document.createElement("div");
  root.id = "jmd-intro";
  root.setAttribute("role", "dialog");
  root.setAttribute("aria-label", "Presentación de JMD Ventas");
  root.innerHTML =
    '<canvas aria-hidden="true"></canvas>' +
    '<div class="ji-ap" aria-hidden="true"><div class="ji-cut ji-c1"></div><div class="ji-cut ji-c2"></div>' +
    '<div class="ji-cap">Consultoría Digital presenta</div>' +
    '<div class="ji-mast"><span>J</span><span>M</span><span>D</span></div>' +
    '<div class="ji-rule"></div><div class="ji-sub">Ventas</div></div>' +
    '<div class="ji-fin"><img class="ji-logo" alt="Consultoría Digital" src="' + base + 'consultoria-digital.jpg">' +
    '<div class="ji-l1">Inteligencia Artificial a tu servicio</div><div class="ji-sep"></div>' +
    '<div class="ji-l2">Consultoría <b>DIGITAL</b></div></div>' +
    '<button type="button" class="ji-skip">Saltar</button>';
  var prevOverflow = document.documentElement.style.overflow;
  document.documentElement.style.overflow = "hidden";
  (document.body || document.documentElement).appendChild(root);

  var cv = root.querySelector("canvas"), ctx = cv.getContext("2d");
  var ap = root.querySelector(".ji-ap"), c1 = root.querySelector(".ji-c1"), c2 = root.querySelector(".ji-c2");
  var cap = root.querySelector(".ji-cap"), mast = root.querySelector(".ji-mast"), letras = mast.querySelectorAll("span");
  var regla = root.querySelector(".ji-rule"), sub = root.querySelector(".ji-sub"), fin = root.querySelector(".ji-fin");
  var logo = root.querySelector(".ji-logo"), skip = root.querySelector(".ji-skip");

  // ---------------------------------------------------------------- utilidades
  function cl(x) { return x < 0 ? 0 : x > 1 ? 1 : x; }
  function ease(x) { x = cl(x); return 1 - Math.pow(1 - x, 3); }
  function easeIO(x) { x = cl(x); return x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }
  function seg(t, a, b) { return cl((t - a) / (b - a)); }
  var rnd = (function (s) { return function () { s = (s * 16807) % 2147483647; return (s - 1) / 2147483646; }; })(20261007);


  // ---------------------------------------------------------------- lienzo
  var W = 0, H = 0, DPR = 1, particulas = [], figuras = [], movil = false, glow;
  function hacerGlow() {
    var g = document.createElement("canvas"); g.width = g.height = 64;
    var x = g.getContext("2d"), r = x.createRadialGradient(32, 32, 0, 32, 32, 32);
    r.addColorStop(0, "rgba(255,255,255,1)"); r.addColorStop(.18, "rgba(225,220,255,.85)");
    r.addColorStop(.45, "rgba(150,130,255,.28)"); r.addColorStop(1, "rgba(120,90,255,0)");
    x.fillStyle = r; x.fillRect(0, 0, 64, 64);
    return g;
  }
  function medir() {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    W = root.clientWidth; H = root.clientHeight;
    cv.width = Math.round(W * DPR); cv.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    movil = W < 760;
    var n = Math.max(36, Math.min(movil ? 70 : 130, Math.round(W * H / 11000)));
    particulas = [];
    for (var i = 0; i < n; i++) {
      particulas.push({ x: rnd() * W, y: rnd() * H, vx: (rnd() - .5) * 9, vy: (rnd() - .5) * 9, r: .6 + rnd() * 1.3, f: rnd() * 6.28 });
    }
    // Figuras: tres en fila en escritorio; en el teléfono, una tras otra al centro.
    var alto = movil ? Math.min(H * .8, W * 1.42) : Math.min(H * .8, W * .42);
    var top = (H - alto) * (movil ? .42 : .45);
    figuras = window.JMDFiguras.orden.map(function (nombre, i) {
      var trazos = FIGURAS[nombre];
      var cx = movil ? W / 2 : W * (.22 + .28 * i);
      var lineas = trazos.map(function (tz) {
        var pts = suave(tz.p, 7).map(function (p) { return [cx + p[0] * alto, top + p[1] * alto]; });
        var largo = 0, acum = [0];
        for (var j = 1; j < pts.length; j++) { largo += Math.hypot(pts[j][0] - pts[j - 1][0], pts[j][1] - pts[j - 1][1]); acum.push(largo); }
        return { pts: pts, acum: acum, largo: largo, w: tz.w };
      });
      var total = lineas.reduce(function (s, l) { return s + l.largo; }, 0);
      var paso = alto * (movil ? .036 : .03), nodos = [], off = 0;
      lineas.forEach(function (l) {
        for (var s = 0; s <= l.largo; s += paso * (l.w < .9 ? 1.7 : 1)) {
          var k = 1; while (k < l.acum.length - 1 && l.acum[k] < s) k++;
          var a = l.acum[k - 1], b = l.acum[k], u = b > a ? (s - a) / (b - a) : 0, p = l.pts[k - 1], q = l.pts[k];
          nodos.push({ x: p[0] + (q[0] - p[0]) * u, y: p[1] + (q[1] - p[1]) * u, s: off + s, f: rnd() * 6.28, big: l.w > 1 && rnd() < .16 });
        }
        off += l.largo;
      });
      return { lineas: lineas, total: total, nodos: nodos, cx: cx, top: top, alto: alto };
    });
  }
  // Cuándo se dibuja cada vestido (inicio, duración del trazo) y cuánto se ve (alfa) en el instante t.
  function plan(i, t) {
    if (!movil) {
      var a0 = 2.35 + i * .5;
      return { p: easeIO(seg(t, a0, a0 + 1.75)), a: ease(seg(t, a0 - .1, a0 + .3)) * (1 - .72 * ease(seg(t, INICIO_TEXTO - .2, INICIO_TEXTO + .5))) };
    }
    var m0 = 2.3 + i * 1.18, ultimo = i === figuras.length - 1;
    var sale = ultimo ? .72 * ease(seg(t, INICIO_TEXTO - .2, INICIO_TEXTO + .5)) : ease(seg(t, m0 + 1.0, m0 + 1.25));
    return { p: easeIO(seg(t, m0, m0 + 0.95)), a: ease(seg(t, m0 - .1, m0 + .2)) * (1 - sale) };
  }

  function dibujarCielo(t, dt) {
    var g = ctx.createRadialGradient(W * .28, H * .18, 0, W * .5, H * .55, Math.max(W, H) * .95);
    g.addColorStop(0, "#1b2a8c"); g.addColorStop(.38, "#141660"); g.addColorStop(.72, "#0c0a36"); g.addColorStop(1, "#05041a");
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1; ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    var g2 = ctx.createRadialGradient(W * .85, H * .92, 0, W * .85, H * .92, Math.max(W, H) * .6);
    g2.addColorStop(0, "rgba(118,32,176,.46)"); g2.addColorStop(1, "rgba(118,32,176,0)");
    ctx.fillStyle = g2; ctx.fillRect(0, 0, W, H);
    var g3 = ctx.createRadialGradient(W * .95, H * .02, 0, W * .95, H * .02, Math.max(W, H) * .45);
    g3.addColorStop(0, "rgba(90,40,160,.3)"); g3.addColorStop(1, "rgba(90,40,160,0)");
    ctx.fillStyle = g3; ctx.fillRect(0, 0, W, H);

    ctx.globalCompositeOperation = "lighter";
    // Rayos de luz: haces finos que bajan desde arriba y oscilan despacio.
    for (var r = 0; r < 4; r++) {
      var ang = -1.05 + r * .2 + Math.sin(t * .35 + r * 1.7) * .05, ox = W * (.08 + r * .07), oy = -H * .05;
      var len = Math.hypot(W, H) * 1.2, ex = ox + Math.cos(-ang) * len, ey = oy + Math.sin(-ang) * len;
      var ancho = (movil ? 30 : 54) * (1 - r * .18);
      [[1, .018], [.45, .026], [.12, .05]].forEach(function (ps) {
        var lg = ctx.createLinearGradient(ox, oy, ex, ey);
        lg.addColorStop(0, "rgba(170,175,255," + (ps[1] * (1 - r * .15)).toFixed(3) + ")");
        lg.addColorStop(.7, "rgba(170,175,255,0)");
        ctx.strokeStyle = lg; ctx.lineWidth = ancho * ps[0];
        ctx.beginPath(); ctx.moveTo(ox, oy); ctx.lineTo(ex, ey); ctx.stroke();
      });
    }
    // Partículas en constelación.
    var D = Math.min(W, H) * (movil ? .22 : .16), D2 = D * D;
    for (var i = 0; i < particulas.length; i++) {
      var p = particulas[i];
      p.x += p.vx * dt; p.y += p.vy * dt;
      if (p.x < -20) p.x = W + 20; if (p.x > W + 20) p.x = -20; if (p.y < -20) p.y = H + 20; if (p.y > H + 20) p.y = -20;
    }
    ctx.lineWidth = .6;
    for (i = 0; i < particulas.length; i++) {
      for (var j = i + 1; j < particulas.length; j++) {
        var a = particulas[i], b = particulas[j], dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
        if (d2 < D2) {
          ctx.strokeStyle = "rgba(170,175,255," + ((1 - d2 / D2) * .32).toFixed(3) + ")";
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        }
      }
    }
    for (i = 0; i < particulas.length; i++) {
      p = particulas[i];
      var tw = .55 + .45 * Math.sin(t * 2.2 + p.f), s = p.r * 7 * (.75 + .25 * tw);
      ctx.globalAlpha = .55 + .45 * tw;
      ctx.drawImage(glow, p.x - s / 2, p.y - s / 2, s, s);
    }
    ctx.globalAlpha = 1;
    // Estrellas fugaces cada ~1,3 s.
    for (var k = 0; k < 3; k++) {
      var per = 1.3, fase = (t + k * .45) % (per * 3) / per;
      if (fase > 1) continue;
      var sx = W * (.55 + .4 * ((k * 37) % 10) / 10) - fase * W * .35, sy = H * (.06 + .1 * k) + fase * H * .22;
      var sl = ctx.createLinearGradient(sx, sy, sx + 110, sy - 70);
      sl.addColorStop(0, "rgba(255,255,255," + (.75 * (1 - fase)) + ")"); sl.addColorStop(1, "rgba(255,255,255,0)");
      ctx.strokeStyle = sl; ctx.lineWidth = 1.3;
      ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(sx + 110, sy - 70); ctx.stroke();
    }
  }

  function dibujarVestido(fg, pl, t) {
    if (pl.a <= .01) return;
    var hasta = pl.p * fg.total, off = 0;
    ctx.lineJoin = ctx.lineCap = "round";
    // Silueta: un trazo nítido y un halo.
    for (var pass = 0; pass < 2; pass++) {
      off = 0;
      fg.lineas.forEach(function (l) {
        var resto = hasta - off;
        off += l.largo;
        if (resto <= 0) return;
        ctx.beginPath(); ctx.moveTo(l.pts[0][0], l.pts[0][1]);
        for (var i = 1; i < l.pts.length; i++) {
          if (l.acum[i] > resto) {
            var u = (resto - l.acum[i - 1]) / (l.acum[i] - l.acum[i - 1]), p = l.pts[i - 1], q = l.pts[i];
            ctx.lineTo(p[0] + (q[0] - p[0]) * u, p[1] + (q[1] - p[1]) * u);
            break;
          }
          ctx.lineTo(l.pts[i][0], l.pts[i][1]);
        }
        if (pass === 0) { ctx.strokeStyle = "rgba(150,130,255," + (.16 * pl.a) + ")"; ctx.lineWidth = 7 * l.w; }
        else { ctx.strokeStyle = "rgba(236,232,255," + (.8 * pl.a) + ")"; ctx.lineWidth = 1.15 * l.w; }
        ctx.stroke();
      });
    }
    // Estrellas de la constelación y uniones finas entre vecinas (el «tejido»).
    var vis = fg.nodos.filter(function (n) { return n.s <= hasta; });
    ctx.lineWidth = .5;
    for (var i = 0; i < vis.length; i += 3) {
      var a = vis[i], b = vis[(i + 7) % vis.length];
      if (!b || Math.hypot(a.x - b.x, a.y - b.y) > fg.alto * .2) continue;
      ctx.strokeStyle = "rgba(190,185,255," + (.18 * pl.a) + ")";
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
    vis.forEach(function (n) {
      var tw = .6 + .4 * Math.sin(t * 3 + n.f), s = (n.big ? 13 : 7) * (.8 + .3 * tw);
      ctx.globalAlpha = pl.a * (.6 + .4 * tw);
      ctx.drawImage(glow, n.x - s / 2, n.y - s / 2, s, s);
      if (n.big) { // destello en cruz
        ctx.strokeStyle = "rgba(255,255,255," + (.5 * pl.a * tw) + ")"; ctx.lineWidth = .7;
        ctx.beginPath(); ctx.moveTo(n.x - s, n.y); ctx.lineTo(n.x + s, n.y); ctx.moveTo(n.x, n.y - s); ctx.lineTo(n.x, n.y + s); ctx.stroke();
      }
    });
    ctx.globalAlpha = 1;
    // La punta del trazo: una estrella que avanza mientras dibuja.
    if (pl.p > 0 && pl.p < 1) {
      off = 0;
      for (var k = 0; k < fg.lineas.length; k++) {
        var l = fg.lineas[k];
        if (hasta <= off + l.largo) {
          var r = hasta - off, m = 1; while (m < l.acum.length - 1 && l.acum[m] < r) m++;
          var pt = l.pts[m], sz = 26;
          ctx.globalAlpha = pl.a; ctx.drawImage(glow, pt[0] - sz / 2, pt[1] - sz / 2, sz, sz); ctx.globalAlpha = 1;
          break;
        }
        off += l.largo;
      }
    }
  }

  // ---------------------------------------------------------------- un fotograma
  function pintar(t, dt) {
    // 1. Apertura editorial
    if (t < FIN_APERTURA + .05) {
      ap.style.display = "";
      ap.style.opacity = String(1 - ease(seg(t, FIN_APERTURA - .4, FIN_APERTURA)));
      var tr = 1 - ease(seg(t, .05, 1.35));
      mast.style.letterSpacing = (.02 + .55 * tr).toFixed(3) + "em";
      for (var i = 0; i < letras.length; i++) {
        var k = ease(seg(t, .08 + i * .13, .95 + i * .13));
        letras[i].style.opacity = String(k);
        letras[i].style.transform = "translateY(" + ((1 - k) * 22).toFixed(1) + "%)";
      }
      cap.style.opacity = String(ease(seg(t, .15, .7)));
      regla.style.transform = "scaleX(" + ease(seg(t, .55, 1.15)).toFixed(3) + ")";
      sub.style.opacity = String(ease(seg(t, .85, 1.35)));
      sub.style.letterSpacing = (.55 + .6 * (1 - ease(seg(t, .85, 1.6)))).toFixed(3) + "em";
      // Cortes editoriales: dos planos negros en diagonal que se cruzan.
      var x1 = ease(seg(t, 1.05, 1.5)) * 66, x2 = ease(seg(t, 1.32, 1.8)) * 62;
      c1.style.clipPath = "polygon(0 0," + x1 + "% 0," + (x1 - 14) + "% 100%,0 100%)";
      c2.style.clipPath = "polygon(100% 0,100% 100%," + (100 - x2) + "% 100%," + (100 - x2 + 12) + "% 0)";
    } else {
      ap.style.display = "none";
    }
    // 2. Cielo y vestidos
    if (t >= INICIO_CIELO) {
      dibujarCielo(t, dt);
      for (var v = 0; v < figuras.length; v++) dibujarVestido(figuras[v], plan(v, t), t);
    }
    // 3. Texto y logo
    var kf = ease(seg(t, INICIO_TEXTO, INICIO_TEXTO + .8));
    fin.style.opacity = String(kf);
    fin.style.transform = "translateY(" + ((1 - kf) * 14).toFixed(1) + "px)";
    logo.style.transform = "scale(" + (.86 + .14 * ease(seg(t, INICIO_TEXTO, INICIO_TEXTO + 1))).toFixed(3) + ")";
    // Salida
    root.style.opacity = String(1 - ease(seg(t, INICIO_SALIDA, FIN)));
  }

  // ---------------------------------------------------------------- reloj
  var t0 = null, ultimo = 0, vivo = true, salida = null;
  function cerrar(rapido) {
    if (!vivo) return;
    marcar();
    if (rapido && salida === null) { salida = performance.now(); return; }
    vivo = false;
    window.removeEventListener("resize", medir);
    document.removeEventListener("keydown", tecla);
    document.documentElement.style.overflow = prevOverflow;
    root.remove(); st.remove();
  }
  function tecla(e) { if (e.key === "Escape") cerrar(true); }
  function cuadro(now) {
    if (!vivo) return;
    if (t0 === null) t0 = now;
    var t = congelado !== null ? congelado : (now - t0) / 1000;
    var dt = Math.min(.05, (now - (ultimo || now)) / 1000); ultimo = now;
    pintar(t, congelado !== null ? 0 : dt);
    if (salida !== null) { // «Saltar»: fundido corto
      var k = (now - salida) / 380;
      root.style.opacity = String(Math.max(0, 1 - k));
      if (k >= 1) return cerrar(false);
    } else if (congelado === null && t >= FIN) return cerrar(false);
    requestAnimationFrame(cuadro);
  }

  glow = hacerGlow();
  medir();
  window.addEventListener("resize", medir);
  document.addEventListener("keydown", tecla);
  skip.addEventListener("click", function () { cerrar(true); });
  pintar(0, 0);
  // Espera un momento a la tipografía didona (máx. 0,8 s) para no arrancar con la de reserva.
  var arranque = function () { requestAnimationFrame(cuadro); };
  if (document.fonts && document.fonts.load) {
    Promise.race([
      Promise.all([document.fonts.load("900 100px 'Bodoni Moda'"), document.fonts.load("italic 500 40px 'Bodoni Moda'")]),
      new Promise(function (r) { setTimeout(r, 800); })
    ]).then(arranque, arranque);
  } else arranque();
  if (congelado === null) marcar();
})();
