"""Quality gate, generación con control y fallback de código (spec §12, §17, §18).

- ReglasCalidad valida un borrador contra el plan y contra los datos reales: precios del catálogo, prenda nombrada,
  ninguna otra prenda, telas que la ficha respalda, sin promesas inventadas, una sola pregunta (la del plan), sin
  volver a saludar y sin salirse del español.
- PlantillaGeneracion redacta desde el plan con texto fijo de código (sin modelo). Tiene variantes: la 0 es completa y
  la 1 es corta, para la regeneración.
- `componer` arma el mensaje como V1: una apertura (que puede escribir un modelo) y, aparte, la pregunta que eligió el
  código. Con foto, el bot Go manda la pregunta DESPUÉS de la foto; por eso va en su propio párrafo.
- Si ninguna variante pasa, sale FALLBACK: el texto seguro del spec, nunca un borrador que no pasó la validación."""
from __future__ import annotations

import re
import unicodedata
from typing import Callable

PRECIO_RE = re.compile(r"S/\s?(\d+(?:[.,]\d{1,2})?)")
CODIGO_RE = re.compile(r"\bV\d{2}\b")
PROMESA_RE = re.compile(
    r"(entrega (en|para|el) |llega (en|el) |\d+\s*d[ií]as|env[ií]o gratis|gratis|descuento|\d+\s?%|"
    r"[uú]ltimas? unidades?|oferta)", re.I)
# Caracteres que no son español: Qwen y otros modelos pequeños a veces cambian de idioma a media frase.
AJENO_RE = re.compile(r"[Ѐ-ӿ֐-ۿ฀-๿぀-ヿ㐀-鿿가-힯]")
SALUDO_RE = re.compile(r"^\W*(hola|holi|buen[oa]s?\s+(d[ií]as?|tardes|noches)|soy\s+\w+|bienvenid[ao])\b", re.I)
TELAS = ("sat[eé]n", "seda", "algod[oó]n", "lino", "gasa", "crep[eé]", "licra", "lycra", "poli[eé]ster", "terciopelo",
         "encaje", "tul", "chiffon", "organza", "jersey", "lana", "cuero", "denim", "mezclilla", "viscosa", "spandex")
TELA_RE = re.compile(r"\b(" + "|".join(TELAS) + r")\b", re.I)
REPETIDA_RE = re.compile(r"\b(\w{3,})\s+\1\b", re.I)                           # «Vestido VESTIDO», «muy muy»
NOMBRE_BOT_RE = re.compile(r"\bros(?:e)?mary\b", re.I)          # la asesora no se nombra ni llama así a la clienta
TALLA_RE = re.compile(r"\btalla\s+\*?(XXL|XL|XS|S|M|L)\*?(?![A-Za-z])", re.I)
NUMERO_RE = re.compile(r"(?<![\w/.])\d{1,4}(?![\w])")
# Adjetivos de opinión y palabras de enlace que un redactor puede usar sin que sean un dato de la prenda. Todo lo demás que
# afirme la apertura (tela, corte, color, detalles, estilo concreto) tiene que estar en la ficha o en lo que dijo la clienta.
LIBRES = frozenset("""
ideal perfecto perfecta elegante sofisticado sofisticada clasico clasica moderno moderna versatil favorecedor favorecedora
comodo comoda fresco fresca especial lindo linda bonito bonita hermoso hermosa precioso preciosa atractivo atractiva
juvenil formal casual sencillo sencilla estilo ocasion ocasiones evento eventos momento momentos look prenda modelo
recomiendo recomendamos proximo proxima corte diseno detalle detalles
porque tiene tienes tienen sobre cuando donde ademas tambien siempre cualquier ambos ambas queda quedara quedaria luce
lucir lucira combina combinar combinara resalta resaltar aporta aportar da dar seguro segura muy mucho mucha mismo
tus tuyo tuya para esta este esto esos esas otra otro otros otras hacia desde entre hasta cuanto cuantos mejor
""".split())
FALLBACK = "Déjame confirmarlo con una asesora para darte información exacta 😊"
MAX_CHARS = 420
MAX_PALABRAS_APERTURA = 32


def _plano(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower()) if unicodedata.category(c) != "Mn")


def _sin_asteriscos(t: str) -> str:
    return (t or "").replace("*", "").replace("_", "")


class ReglasCalidad:
    nombre = "reglas"

    def __init__(self, precios: Callable[[], set[int]], nombres: Callable[[str], str | None] | None = None,
                 todos_los_nombres: Callable[[], dict[str, str]] | None = None,
                 ficha_texto: Callable[[str], str] | None = None):
        self.precios = precios
        self.nombres = nombres or (lambda c: None)
        self.todos = todos_los_nombres or (lambda: {})
        self.ficha_texto = ficha_texto or (lambda c: "")

    def evaluar(self, borrador: str, plan, contexto: dict | None = None) -> dict:
        b = (borrador or "").strip()
        errores: list[str] = []
        if not b:
            errores.append("borrador vacío")
        if len(b) > MAX_CHARS:
            errores.append(f"demasiado largo ({len(b)} > {MAX_CHARS})")
        if AJENO_RE.search(b):
            errores.append("caracteres que no son español")
        precios = self.precios()
        for m in PRECIO_RE.findall(b):
            if int(round(float(m.replace(",", ".")))) not in precios:
                errores.append(f"precio no verificado: S/ {m}")
        codigos = set(CODIGO_RE.findall(b))
        plano_b = _plano(_sin_asteriscos(b))
        if plan.producto:
            if codigos - {plan.producto}:
                errores.append("nombra otra prenda")
            nombre = self.nombres(plan.producto) or ""
            if plan.accion == "recomendar" and plan.producto not in codigos and _plano(nombre) not in plano_b:
                errores.append("no nombra la prenda que va en la foto")
            for cod, otro in self.todos().items():
                if cod != plan.producto and otro and len(otro) > 3 and _plano(otro) in plano_b and _plano(otro) not in _plano(nombre):
                    errores.append(f"nombra otra prenda: {otro}")
                    break
            respaldo = _plano(self.ficha_texto(plan.producto))
            for tela in {m.lower() for m in TELA_RE.findall(b)}:
                if _plano(tela) not in respaldo:
                    errores.append(f"tela que la ficha no respalda: {tela}")
        if PROMESA_RE.search(b):
            errores.append("promesa o descuento no respaldado")
        preguntas = (plan.pregunta or {}).get("texto") or ""
        if b.count("?") > 2:
            errores.append("más de una pregunta")
        if plan.accion == "preguntar" and "?" not in b:
            errores.append("el plan pide preguntar y no hay pregunta")
        if preguntas:
            if _plano(_sin_asteriscos(preguntas)).strip(" ¿?") not in plano_b:
                errores.append("no hace la pregunta que eligió el código")
            elif b.count("?") > 1:
                errores.append("más de una pregunta")
        elif plan.accion == "recomendar" and "?" in b:
            errores.append("pregunta algo que el plan no pide")
        turnos = ((contexto or {}).get("conversation") or {}).get("turns_total", 0)
        if turnos and SALUDO_RE.search(b):
            errores.append("vuelve a saludar")
        if REPETIDA_RE.search(_sin_asteriscos(b)):
            errores.append("palabra repetida")
        if NOMBRE_BOT_RE.search(b):
            errores.append("nombra a la asesora (o llama así a la clienta)")
        apertura = b.replace(preguntas, "").strip() if preguntas else b
        if len(re.findall(r"\w+", apertura)) > MAX_PALABRAS_APERTURA:
            errores.append(f"apertura demasiado larga (más de {MAX_PALABRAS_APERTURA} palabras)")
        errores += self._inventos(b, plan, contexto, preguntas)
        if plan.accion == "recomendar" and plan.producto:
            errores += self._sin_fundamento(apertura, plan, contexto, preguntas)
        return {"passed": not errores, "score": round(max(0.0, 1.0 - 0.25 * len(errores)), 2), "errors": errores}


    def _inventos(self, b: str, plan, contexto: dict | None, pregunta: str) -> list[str]:
        """Tallas y números que nadie dijo: salen de lo que escribió la clienta, de los hechos del plan o de la pregunta."""
        conv = (contexto or {}).get("conversation") or {}
        dichos = " ".join([conv.get("last_user_message") or "", " ".join(t.get("texto", "") for t in conv.get("recent_turns") or []),
                           " ".join(plan.hechos or []), pregunta, self.ficha_texto(plan.producto) if plan.producto else ""])
        out: list[str] = []
        for t in {m.upper() for m in TALLA_RE.findall(b)}:
            if not re.search(rf"\b{t}\b", dichos.upper()):
                out.append(f"talla que nadie dijo: {t}")
        sin_codigos = CODIGO_RE.sub(" ", b)
        for n in sorted(set(NUMERO_RE.findall(sin_codigos))):
            if not re.search(rf"(?<!\d){n}(?!\d)", dichos) and not re.search(rf"S/\s?{n}\b", b):
                out.append(f"dato que nadie dijo: {n}")
        return out


    def _sin_fundamento(self, apertura: str, plan, contexto: dict | None, pregunta: str) -> list[str]:
        """Las palabras de contenido de la apertura (5 letras o más) tienen que salir de la ficha de la prenda, de lo que
        dijo la clienta o de las pocas palabras libres de opinión. Un modelo pequeño inventa estilos, épocas y detalles
        que suenan bien y no están en la ficha («clásico ochentero», «detalles exclusivos»)."""
        conv = (contexto or {}).get("conversation") or {}
        corpus = _plano(" ".join([self.ficha_texto(plan.producto), self.nombres(plan.producto) or "", conv.get("last_user_message") or "",
                                  " ".join(t.get("texto", "") for t in conv.get("recent_turns") or []),
                                  " ".join(plan.hechos or []), pregunta]))
        propias = {w[:5] for w in re.findall(r"[a-z]{5,}", corpus)}          # prefijo de 5: «vestido» ≈ «vestidos»
        sobran = []
        propio = re.sub(r"te recomiendo (el|la|los|las)\s+\*[^*]+\*\.?", "", apertura, flags=re.I)   # lo pone el código
        for w in re.findall(r"[a-z]{5,}", _plano(_sin_asteriscos(propio))):
            if w in LIBRES or w[:5] in propias or w[:5] in {x[:5] for x in LIBRES}:
                continue
            if w not in sobran:
                sobran.append(w)
        return [f"afirma algo que no está en la ficha: {w}" for w in sobran[:3]]


def componer(apertura: str, plan: dict) -> str:
    """Apertura + (en párrafo aparte) la pregunta que eligió el código. Como V1: una sola pregunta, y es la del código."""
    apertura = (apertura or "").strip()
    pregunta = ((plan.get("pregunta") or {}).get("texto") or "").strip()
    return "\n\n".join(p for p in (apertura, pregunta) if p)


class PlantillaGeneracion:
    """Texto desde el plan con plantillas fijas. La variante solo cambia el aviso de «sin stock»; lo demás es igual."""
    nombre = "plantilla"

    def __init__(self, nombres: Callable[[str], str | None] | None = None):
        self.nombres = nombres or (lambda c: None)

    def redactar(self, plan: dict, variante: int = 0, contexto: dict | None = None) -> str:
        accion, prod = plan.get("accion"), plan.get("producto")
        pregunta = (plan.get("pregunta") or {}).get("texto") or ""
        if accion == "recomendar" and prod:
            nombre = self.nombres(prod) or prod
            return componer(f"Te recomiendo el *{nombre}*.", plan)
        if accion == "preguntar":
            if pregunta:
                return componer("", plan)                 # solo la pregunta del código
            return ("Por ahora no tengo esa prenda disponible. ¿Te interesaría ver otra opción?" if variante == 0
                    else "¿Te interesaría ver otra opción?")
        if accion in ("pedir_asesora", "derivar"):
            return "Te paso con una asesora para ayudarte mejor."
        return "Déjame confirmarlo con una asesora para darte la información exacta."
