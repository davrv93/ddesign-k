/* Figurines de moda de JMD Ventas (portada /jmdventas/ e intro). Sin dependencias: deja window.JMDFiguras.
 * La portada lo carga de forma síncrona antes que todo lo demás; la intro solo lo lee. Así las ilustraciones y el
 * quiosco no dependen de la intro (saltada, con movimiento reducido o con un intro.js viejo en caché).
 */
(function () {
  "use strict";
  // ---------------------------------------------------------------- figuras (alto = 1 de la cabeza a los pies)
  // Figurines de moda de ~9 cabezas en contraposto: el peso en la pierna derecha de la figura (la cadera de ese lado
  // sube y sale, los hombros se inclinan al revés), una mano en la cintura y el otro brazo suelto. Cada look es una
  // lista de trazos {p: puntos, w: grosor relativo, f: relleno suave opcional}; el orden es el orden en que se dibujan.
  // Los usa la intro (constelación en canvas) y la portada (ilustración SVG que se dibuja sola).
  function ovalo(cx, cy, rx, ry, rot, n) {
    var out = [], c = Math.cos(rot), s = Math.sin(rot);
    for (var i = 0; i <= n; i++) {
      var a = Math.PI * 2 * i / n - Math.PI / 2, x = rx * Math.cos(a), y = ry * Math.sin(a);
      out.push([cx + x * c - y * s, cy + x * s + y * c]);
    }
    return out;
  }
  function ondas(x0, y0, x1, y1, n, amp) { // borde con volantes: n ondas de (x0,y0) a (x1,y1)
    var out = [];
    for (var i = 0; i <= n * 6; i++) {
      var u = i / (n * 6);
      out.push([x0 + (x1 - x0) * u, y0 + (y1 - y0) * u + amp * Math.abs(Math.sin(Math.PI * n * u))]);
    }
    return out;
  }
  var CARA = { p: ovalo(.004, .06, .026, .041, -.12, 18), w: .8 };
  var CUELLO = [{ p: [[-.011, .098], [-.012, .12], [-.013, .143]], w: .7 }, { p: [[.016, .097], [.016, .12], [.018, .145]], w: .7 }];
  var HOMBROS = [{ p: [[-.013, .143], [-.04, .147], [-.062, .151], [-.077, .158]], w: .8 }, { p: [[.018, .145], [.045, .151], [.064, .157], [.076, .166]], w: .8 }];
  var BRAZOS = [
    // suelto, con la mano relajada
    { p: [[-.077, .158], [-.089, .185], [-.094, .235], [-.099, .29], [-.106, .35], [-.11, .41], [-.108, .45], [-.1, .472]], w: .75 },
    { p: [[-.064, .205], [-.074, .25], [-.082, .3], [-.09, .36], [-.094, .41], [-.092, .45], [-.1, .472]], w: .75 },
    // mano en la cintura, codo afuera
    { p: [[.076, .166], [.097, .19], [.122, .228], [.144, .26], [.15, .276], [.132, .297], [.1, .314], [.068, .325], [.048, .33]], w: .75 },
    { p: [[.06, .207], [.08, .228], [.103, .252], [.12, .268], [.1, .288], [.074, .303], [.05, .312]], w: .75 }
  ];
  function piernas(hem) { // pierna de apoyo bajo el cuerpo; la libre se abre y apunta el pie
    return [
      // apoyo: muslo, rodilla, pantorrilla, tobillo fino y pie en punta (zapato de tacón)
      { p: [[.046, hem], [.042, .71], [.04, .735], [.045, .785], [.038, .85], [.026, .918], [.021, .944], [.027, .97], [.02, .99], [.006, .998]], w: .75 },
      { p: [[.011, hem], [.012, .71], [.013, .74], [.01, .79], [.011, .86], [.011, .92], [.009, .948], [.007, .975], [.006, .998]], w: .75 },
      // libre: se abre, rodilla hacia dentro, pie en punta hacia afuera
      { p: [[-.05, hem], [-.054, .71], [-.056, .74], [-.066, .79], [-.069, .86], [-.074, .92], [-.077, .946], [-.093, .974], [-.108, .99]], w: .75 },
      { p: [[-.016, hem], [-.024, .71], [-.032, .745], [-.04, .79], [-.05, .86], [-.06, .92], [-.064, .95], [-.08, .981], [-.108, .99]], w: .75 }
    ];
  }
  var PELO = {
    recogido: [ // moño bajo
      { p: [[-.028, .072], [-.033, .045], [-.024, .018], [-.002, .006], [.022, .01], [.036, .028], [.04, .052], [.035, .07]], w: .9 },
      { p: ovalo(.045, .062, .016, .019, .3, 12), w: .8 },
      { p: [[-.02, .02], [.005, .022], [.03, .04]], w: .45 }
    ],
    bob: [ // melena corta con raya al lado
      { p: [[-.033, .088], [-.037, .055], [-.032, .025], [-.015, .008], [.008, .004], [.03, .014], [.042, .04], [.044, .07], [.04, .092]], w: .9 },
      { p: [[-.012, .008], [.006, .03], [.02, .06], [.026, .088]], w: .5 }
    ],
    ondas: [ // pelo largo que cae sobre un hombro
      { p: [[-.03, .075], [-.034, .04], [-.02, .012], [.004, .004], [.028, .014], [.04, .042], [.044, .078], [.052, .11], [.048, .14], [.06, .172], [.054, .205]], w: .9 },
      { p: [[-.031, .075], [-.04, .105], [-.034, .13], [-.042, .158]], w: .7 },
      { p: [[.02, .02], [.032, .06], [.04, .1], [.036, .13]], w: .45 }
    ]
  };
  function look(pelo, ropa, hem) {
    return [CARA].concat(PELO[pelo], CUELLO, HOMBROS, BRAZOS, ropa, hem ? piernas(hem) : []);
  }
  // Piezas de cada vestido con nombre: forman el trazo y, unidas, el relleno suave de la ilustración de la portada.
  var G = {
    faldaI: [[-.04, .326], [-.062, .4], [-.085, .5], [-.11, .62], [-.135, .75], [-.16, .88], [-.185, .975]],
    faldaD: [[.04, .322], [.075, .4], [.1, .5], [.118, .62], [.14, .75], [.168, .87], [.212, .955], [.27, .985]],
    ruedo: [[-.185, .975], [-.12, .99], [-.04, .998], [.05, .997], [.15, .993], [.27, .985]],
    cuerpoI: [[-.064, .205], [-.06, .25], [-.052, .29], [-.04, .326]],
    cuerpoD: [[.06, .207], [.056, .25], [.05, .29], [.04, .322]],
    escote: [[-.04, .147], [-.03, .17], [-.014, .196], [.004, .222], [.004, .222], [.02, .196], [.034, .17], [.046, .152]]
  };
  var L = {
    hombro: [[-.035, .146], [-.06, .15], [-.077, .16], [-.074, .185], [-.064, .205]],
    escote: [[-.062, .151], [-.045, .165], [-.02, .18], [.01, .193], [.035, .2], [.06, .207]],
    ladoI: [[-.064, .205], [-.06, .25], [-.05, .29], [-.042, .326], [-.055, .37], [-.066, .42], [-.067, .48], [-.062, .56], [-.056, .62], [-.052, .665]],
    ladoD: [[.06, .207], [.056, .25], [.048, .29], [.042, .322], [.062, .365], [.082, .41], [.084, .47], [.074, .55], [.062, .62], [.052, .66]],
    ruedo: [[-.052, .665], [0, .663], [.052, .66]]
  };
  var V = {
    escote: [[-.062, .218], [-.045, .207], [-.022, .212], [.002, .232], [.002, .232], [.024, .214], [.046, .209], [.06, .22]],
    faldaI: [[-.04, .324], [-.07, .37], [-.108, .43], [-.1, .44], [-.135, .5], [-.172, .56], [-.165, .57], [-.2, .64], [-.238, .71]],
    faldaD: [[.04, .32], [.075, .365], [.116, .42], [.11, .43], [.146, .49], [.182, .55], [.176, .56], [.202, .625], [.226, .69]],
    ruedo: ondas(-.238, .71, .226, .69, 8, .022)
  };
  function inv(a) { return a.slice().reverse(); }
  var FIGURAS = {
    // Gala larga con escote en V y capa que ondea detrás.
    gala: look("recogido", [
      { p: [[-.077, .158], [-.105, .2], [-.14, .32], [-.18, .48], [-.225, .64], [-.262, .8], [-.288, .92], [-.272, .972], [-.232, .99]], w: .9 },
      { p: [[.076, .166], [.108, .21], [.16, .3], [.198, .45], [.232, .62], [.266, .78], [.302, .9], [.326, .962]], w: .9 },
      { p: [[-.12, .3], [-.168, .55], [-.215, .84]], w: .4 },
      { p: G.escote, w: 1.1 },
      { p: G.cuerpoI, w: 1.1 }, { p: G.cuerpoD, w: 1.1 },
      { p: [[-.04, .326], [0, .332], [.04, .322]], w: .9 },
      { p: G.faldaI, w: 1.15 }, { p: G.faldaD, w: 1.15 }, { p: G.ruedo, w: 1.1 },
      { p: [[.012, .336], [.024, .5], [.034, .7], [.05, .92]], w: .45 },
      { p: [[-.02, .342], [-.045, .55], [-.07, .78], [-.09, .97]], w: .45 }
    ]),
    // Lápiz a la rodilla, de un hombro, con drapeado en diagonal y abertura.
    lapiz: look("bob", [
      { p: L.hombro, w: 1.1 }, { p: L.escote, w: 1.1 },
      { p: L.ladoI, w: 1.15 }, { p: L.ladoD, w: 1.15 }, { p: L.ruedo, w: 1.1 },
      { p: [[.03, .661], [.029, .608]], w: .7 },
      { p: [[-.055, .19], [-.02, .24], [.02, .3], [.06, .36]], w: .5 },
      { p: [[-.05, .25], [-.01, .31], [.035, .38], [.07, .44]], w: .45 }
    ], .664),
    // Corazón con tirantes finos, cintura marcada y falda amplia de tres volantes en movimiento.
    volantes: look("ondas", [
      { p: [[-.042, .149], [-.045, .21]], w: .6 }, { p: [[.042, .153], [.045, .212]], w: .6 },
      { p: V.escote, w: 1.1 },
      { p: [[-.062, .218], [-.057, .26], [-.04, .324]], w: 1.1 }, { p: [[.06, .22], [.054, .26], [.04, .32]], w: 1.1 },
      { p: [[-.04, .324], [0, .33], [.04, .32]], w: .9 },
      { p: V.faldaI, w: 1.15 }, { p: V.faldaD, w: 1.15 },
      { p: ondas(-.108, .43, .116, .42, 5, .013), w: .75 },
      { p: ondas(-.172, .56, .182, .55, 7, .016), w: .75 },
      { p: V.ruedo, w: 1.1 }
    ], .705)
  };
  // Relleno de cada vestido (polígono cerrado con las mismas piezas).
  var RELLENOS = {
    gala: G.escote.concat(G.cuerpoD, G.faldaD.slice(1), inv(G.ruedo).slice(1), inv(G.faldaI).slice(1), inv(G.cuerpoI).slice(1)),
    lapiz: L.hombro.concat(L.ladoI.slice(1), L.ruedo.slice(1), inv(L.ladoD).slice(1), inv(L.escote).slice(1)),
    volantes: V.escote.concat([[.054, .26], [.04, .32]], V.faldaD.slice(1), inv(V.ruedo).slice(1), inv(V.faldaI).slice(1), [[-.057, .26]])
  };
  // Catmull-Rom: el trazo suave de cada línea (un punto repetido deja una esquina nítida).
  function suave(pts, k) {
    var out = [];
    for (var i = 0; i < pts.length - 1; i++) {
      var p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
      for (var j = 0; j < k; j++) {
        var t = j / k, t2 = t * t, t3 = t2 * t;
        out.push([
          .5 * (2 * p1[0] + (-p0[0] + p2[0]) * t + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3),
          .5 * (2 * p1[1] + (-p0[1] + p2[1]) * t + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
        ]);
      }
    }
    out.push(pts[pts.length - 1]);
    return out;
  }
  // Trazo SVG (curvas de Bézier equivalentes a Catmull-Rom) escalado: lo usa la portada.
  function svgPath(pts, esc, ox, oy) {
    var P = function (p) { return [(ox + p[0] * esc).toFixed(1), (oy + p[1] * esc).toFixed(1)]; };
    var d = "M" + P(pts[0]).join(" ");
    for (var i = 0; i < pts.length - 1; i++) {
      var p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
      var c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6], c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
      d += " C" + P(c1).join(" ") + " " + P(c2).join(" ") + " " + P(p2).join(" ");
    }
    return d;
  }
  window.JMDFiguras = { figuras: FIGURAS, rellenos: RELLENOS, svgPath: svgPath, suave: suave, orden: ["gala", "lapiz", "volantes"] };
})();
