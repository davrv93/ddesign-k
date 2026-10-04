/* =========================================================================
   morph.js — pin de 400 vh: una figura pasa por 4 looks.
   WebGL (desplazamiento tipo tela/agua) en escritorio; fundido con clip-path
   en móvil o si no hay WebGL. El fondo hace morph al tono de cada prenda.

   Rendimiento: el shader sólo se dibuja cuando algo cambia (scroll) o mientras
   dura una transición entre dos looks; con un look quieto no gasta GPU.
   Resolución tope 1,25x y ruido de 3 octavas (antes 2x, 4 octavas, 60 fps
   continuos aunque nadie se moviera).
   ========================================================================= */
Baruka.register("morph", function (B) {
  if (B.reduced || typeof ScrollTrigger === "undefined") return;

  var LOOKS = [
    { bg: "#E7DFD3", dark: false },
    { bg: "#6E2E3C", dark: true },
    { bg: "#2C3A5E", dark: true },
    { bg: "#14141A", dark: true }
  ];
  var section = B.qs(".morph");
  var stage = B.qs(".morph__stage");
  var fallbackImgs = B.qsa(".morph__look");
  var labels = B.qsa(".morph__label");
  if (!section || !stage) return;

  var VERT = "attribute vec2 aPos; varying vec2 vUv;" +
    "void main(){ vUv = aPos * 0.5 + 0.5; gl_Position = vec4(aPos, 0.0, 1.0); }";
  var FRAG =
    "precision mediump float; varying vec2 vUv;" +
    "uniform sampler2D uTexA, uTexB; uniform float uProgress, uTime, uCanvasAsp; uniform float uImgAsp;" +
    "float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7))) * 43758.5453); }" +
    "float noise(vec2 p){ vec2 i=floor(p), f=fract(p); float a=hash(i), b=hash(i+vec2(1.,0.)), c=hash(i+vec2(0.,1.)), d=hash(i+vec2(1.,1.)); vec2 u=f*f*(3.-2.*f); return mix(a,b,u.x)+(c-a)*u.y*(1.-u.x)+(d-b)*u.x*u.y; }" +
    "float fbm(vec2 p){ float v=0.0, a=0.5; for(int i=0;i<3;i++){ v+=a*noise(p); p*=2.03; a*=0.5; } return v; }" +
    "vec2 cover(vec2 uv){ float r=uCanvasAsp/uImgAsp; if(r>1.0) uv.y=(uv.y-0.5)*r+0.5; else uv.x=(uv.x-0.5)/r+0.5; return uv; }" +
    "void main(){ vec2 uv=vUv; float n=fbm(cover(uv)*3.5 + vec2(uTime*0.03,uTime*0.02));" +
    " float d=(n-0.5)*0.14*sin(uProgress*3.14159);" +
    " vec4 a=texture2D(uTexA, cover(uv+vec2(d*0.5,d*0.4)));" +
    " vec4 b=texture2D(uTexB, cover(uv-vec2(d*0.5,d*0.4)));" +
    " float m=smoothstep(0.0,1.0,uProgress); vec3 col=mix(a.rgb,b.rgb,m);" +
    " col*=0.96+0.04*n; gl_FragColor=vec4(col,1.0); }";

  function initGL() {
    var canvas = B.qs(".morph__gl");
    if (!canvas) return null;
    var gl = canvas.getContext("webgl", { antialias: false, alpha: true, premultipliedAlpha: false });
    if (!gl) return null;
    function sh(type, src) {
      var s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) return null;
      return s;
    }
    var vs = sh(gl.VERTEX_SHADER, VERT), fs = sh(gl.FRAGMENT_SHADER, FRAG);
    if (!vs || !fs) return null;
    var prog = gl.createProgram();
    gl.attachShader(prog, vs); gl.attachShader(prog, fs); gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return null;
    gl.useProgram(prog);

    var buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    var loc = gl.getAttribLocation(prog, "aPos");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

    var tex = [];
    var loaded = 0;
    for (var i = 0; i < fallbackImgs.length; i++) {
      (function (idx) {
        var t = gl.createTexture();
        tex[idx] = t;
        gl.bindTexture(gl.TEXTURE_2D, t);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        function upload() {
          gl.bindTexture(gl.TEXTURE_2D, t);
          gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, fallbackImgs[idx]);
          loaded++;
        }
        if (fallbackImgs[idx].complete && fallbackImgs[idx].naturalWidth) upload();
        else fallbackImgs[idx].addEventListener("load", upload, { once: true });
      })(i);
    }

    var U = {
      progress: gl.getUniformLocation(prog, "uProgress"),
      time: gl.getUniformLocation(prog, "uTime"),
      canvasAsp: gl.getUniformLocation(prog, "uCanvasAsp"),
      imgAsp: gl.getUniformLocation(prog, "uImgAsp"),
      a: gl.getUniformLocation(prog, "uTexA"),
      b: gl.getUniformLocation(prog, "uTexB")
    };

    function resize() {
      var dpr = Math.min(window.devicePixelRatio || 1, 1.25);
      var w = Math.max(1, Math.round(canvas.clientWidth * dpr));
      var h = Math.max(1, Math.round(canvas.clientHeight * dpr));
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w; canvas.height = h; gl.viewport(0, 0, w, h);
      }
    }

    var state = { a: 0, b: 1, progress: 0, time: 0, visible: false, dirty: true };

    function render() {
      state.raf = null;
      resize();
      if (loaded < fallbackImgs.length) { state.raf = requestAnimationFrame(render); return; }
      if (stage && !stage.classList.contains("has-gl")) stage.classList.add("has-gl");
      // En plena transición la tela «respira» (uTime); con un look quieto, un solo cuadro basta.
      var transitioning = state.progress > 0.002 && state.progress < 0.998;
      if (!state.dirty && !transitioning) return;
      state.dirty = false;
      if (transitioning) state.time += 0.016;
      gl.uniform1f(U.time, state.time);
      gl.uniform1f(U.progress, state.progress);
      gl.uniform1f(U.canvasAsp, canvas.width / canvas.height);
      gl.uniform1f(U.imgAsp, fallbackImgs[state.a].naturalWidth / fallbackImgs[state.a].naturalHeight || 0.75);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, tex[state.a]); gl.uniform1i(U.a, 0);
      gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, tex[state.b]); gl.uniform1i(U.b, 1);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      if (transitioning && state.visible && !document.hidden) state.raf = requestAnimationFrame(render);
    }
    state.kick = function () {
      state.dirty = true;
      if (state.visible && !document.hidden && !state.raf) state.raf = requestAnimationFrame(render);
    };
    state.start = function () { state.visible = true; state.kick(); };
    state.stop = function () { state.visible = false; if (state.raf) cancelAnimationFrame(state.raf); state.raf = null; };
    return state;
  }

  /* Aplica progress p (0..1) a fondo, etiquetas y shader/fundido. */
  function apply(p) {
    var seg = p * 3;
    var i = B.clamp(Math.floor(seg), 0, 2);
    var frac = B.clamp(seg - i, 0, 1);

    section.style.background = B.mixHex(LOOKS[i].bg, LOOKS[i + 1].bg, frac);
    section.classList.toggle("is-dark", frac < 0.5 ? LOOKS[i].dark : LOOKS[i + 1].dark);

    labels.forEach(function (el, k) {
      var dist = Math.abs(k - seg);
      var op = B.clamp(1 - dist * 1.5, 0, 1);
      var dir = (k % 2 === 0) ? 1 : -1;
      el.style.opacity = op;
      el.style.transform = "translate3d(" + (dir * (8 - op * 8) * 3) + "vw, -50%, 0)";
      el.classList.toggle("is-active", op > 0.6);
    });

    if (B.glState) {
      B.glState.a = i; B.glState.b = i + 1; B.glState.progress = frac;
      B.glState.kick();
    } else {
      fallbackImgs.forEach(function (img, k) {
        if (k === i) img.style.opacity = 1;
        else if (k === i + 1) img.style.opacity = frac;
        else img.style.opacity = 0;
        img.classList.toggle("is-active", k === i);
      });
    }
  }

  function setup(useGL) {
    var glState = null;
    if (useGL) { glState = initGL(); B.glState = glState; }

    var io = null;
    if (glState) {
      io = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) { e.isIntersecting ? glState.start() : glState.stop(); });
      }, { rootMargin: "200px" });
      io.observe(stage);
      document.addEventListener("visibilitychange", function () {
        document.hidden ? glState.stop() : (io && glState.start());
      });
    }

    var st = ScrollTrigger.create({
      trigger: ".morph",
      start: "top top",
      end: "+=" + (useGL ? 260 : 180) + "%",
      pin: ".morph__pin",
      scrub: 0.4,
      anticipatePin: 1,
      invalidateOnRefresh: true,
      onUpdate: function (self) { apply(self.progress); }
    });
    apply(0);

    return function cleanup() {
      st.kill();
      if (glState) glState.stop();
      if (io) io.disconnect();
      B.glState = null;
      stage.classList.remove("has-gl");
    };
  }

  var mm = gsap.matchMedia();
  mm.add("(prefers-reduced-motion: no-preference) and (min-width: 901px)", function () { return setup(true); });
  mm.add("(prefers-reduced-motion: no-preference) and (max-width: 900px)", function () { return setup(false); });
});
