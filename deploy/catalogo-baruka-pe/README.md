# Catálogo de baruka.pe → CRM

Extractor del catálogo oficial de la tienda (https://baruka.pe, **Shopify**). Solo lectura: no inicia sesión, no envía
formularios, respeta `robots.txt` (lo comprueba en cada petición), pausa 1,2 s entre peticiones y se identifica como
`BarukaCatalogoBot/1.0`. No escribe en el CRM ni en `agente/seed/`: deja borradores en `salida/` para revisar.

```bash
cd deploy/catalogo-baruka-pe
python3 extraer.py      # 1/3 web → salida/catalogo_baruka_pe.json      (~3 min la primera vez; luego usa la caché)
python3 mapear.py       # 2/3 ↔ V21–V44 del CRM → salida/mapeo.json      (lee el catálogo público vivo; --crm archivo.json para uno local)
python3 fichas.py       # 3/3 → salida/fichas_producto_propuestas.json (V21–V42) + salida/fichas_nuevos_web.json
python3 oficial.py      # 4: baruka.pe como catálogo OFICIAL → salida/catalogo_oficial.json (códigos, precios, tallas, fotos)
python3 agente.py       # 5: fichas en agente/seed/fichas_producto.json y fotos en agente/imagenes/tienda (sips, macOS)
# 6, en el servidor (catalogo_oficial.json + fotos/ junto al script; sin --aplicar solo simula):
python3 cargar.py --destino kddesign --aplicar
python3 cargar.py --destino jmdventas --api http://<ip de jmdventas_backend>:8080 --aplicar
```

**Desde el 12-10-2026 baruka.pe es el catálogo oficial** (lo pidió el usuario: «todo lo de baruka.pe como lo oficial»).
Cargado ese día en los dos CRM: 96 modelos activos (9 conservan su código: V22, V26, V27, V30, V33, V36, V39, V41 y
V42 = Vestido Alma; 87 nuevos V45–V131), 15 desactivados (no borrados). Precio, colores, tallas y descripción = los de la
web. Stock: la web no lo lleva; los nuevos entraron con 10 por talla y la tienda lo corrige en el panel. XS/XL/2XL
«pre orden» = bajo pedido. Respaldos previos en el servidor: `~/respaldos/*-pre-catalogo-20261012-1913.*`.
En jmdventas la columna `color` es VARCHAR(100): ahí la lista de colores se recorta (la completa va en la descripción).

Solo stdlib de Python. El OCR necesita **macOS** con `swiftc` (Xcode CLT); sin eso, `extraer.py --sin-ocr` deja las
tablas de medidas solo como URL.

## De dónde sale cada dato

| Dato | Fuente |
|---|---|
| Nombre, tipo, etiquetas, descripción, precio, precio tachado, colores, tallas, fotos (y qué foto es de qué color) | `/products.json` (API pública de Shopify) |
| Categoría de los productos sin tipo («KERO», «LUZ»…) | `/collections/<c>/products.json` |
| Disponibilidad por talla | `available` de cada variante. **Hoy las 1.463 variantes dicen `true`**: la web no lleva stock real; «Pre orden» va en el nombre de la talla (XS, XL, 2XL) |
| Tela, forro, cierre, cuidados, escote… | Frases de la descripción (`ficha_web`), copiadas sin reescribir. 35 de 96 productos **no tienen descripción** |
| Medidas por talla | **Solo existen como imagen** dentro de la descripción («Consulta tu talla en la tabla inferior»). La app Kiwi Sizing del tema está instalada pero sin tablas. Se leen con Vision (`ocr_vision.swift`) |

## Medidas: cómo se leen y cuándo se confían

1. OCR de la imagen (ampliada ×2 si es pequeña); la tabla se rearma por filas y columnas. Si Vision lee «I1S», se usa
   su otra hipótesis («115») solo si es una cifra limpia.
2. Cada celda vacía se vuelve a leer en su región, dos veces (justa y holgada): vale si coinciden.
3. Validación: celdas completas, rangos (busto 60–160 cm, largo 15–200…), que nada decrezca con la talla, y que busto,
   cintura y cadera coincidan con la tabla **más repetida** de la tienda (85/90/97/106/110 · 65/72/80/90/100 ·
   91/95/107/110/115 en las 51 válidas). Lo que no pasa queda `valida: false` y **no** entra en las fichas.
4. «1.10» o «1.55» (metros) se pasan a cm. `talla_pantalon` es la talla equivalente, no cm.

Las imágenes y el OCR se guardan en `CACHE_DIR` (por defecto `$TMPDIR/catalogo-baruka-pe-cache`), **nunca en el repo**.
Para releer todo desde cero: `rm -rf "$TMPDIR/catalogo-baruka-pe-cache"`.

## Correspondencias a mano

`MANUAL` en `mapear.py`: hoy solo **V42 «Vestido Gala Capa Azul» = Vestido Alma, color Azul Cobalto** (misma lámina de
materiales y la misma foto de maniquí que la web). Si la tienda renombra un modelo, se añade ahí con su evidencia.

## Fichas

Mismo formato que `agente/seed/fichas_producto.json`, más `medidas`, `medidas_origen` y `web`. Reglas:

- Lo que **solo sabe la tienda** (tela, forro, cierre, cuidados): manda la web (`fuente: "baruka.pe"`); lo anterior queda
  en `antes` y, si choca, en `discrepancias`.
- Lo que **se ve en las fotos** (silueta, largo, escote, mangas…): se conserva la ficha actual y la frase web va en
  `baruka_pe`; si parecen decir cosas distintas, a `discrepancias` para revisar.
- Medidas solo de tablas válidas; si no, siguen en `pendiente_tienda` con el enlace a la imagen.
- `ficha_producto.py` todavía no lee `medidas` (`_dato("medidas")` devuelve vacío): usarlas en el bot es otro cambio.
