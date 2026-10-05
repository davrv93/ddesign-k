// Package juicio es la Capa de Juicio: decide qué hacer con la propuesta del agente,
// no qué responder. El LLM propone; esta capa decide si se envía, si se pide aprobación
// a una persona, si se deriva a un humano o si no se responde todavía.
//
// Sus reglas son políticas de negocio explícitas con prioridad, no llamadas a ningún
// modelo: no presionar, no descontar sin criterio, derivar quejas y negociaciones, y
// callar cuando el cliente ya no responde. Es pura (sin estado ni E/S) para poder
// probarla sola.
//
// Fase 1 usa solo las señales que ya existen (etapa, intención, confianza, acción
// propuesta y la historia de intervención humana). Los campos Sentimiento, Urgencia,
// Conversion e IntentosSinRespuesta son ganchos que llenan las fases siguientes; con sus
// valores neutros la decisión es la de hoy.
package juicio

// Riesgo de ejecutar la propuesta del agente sin revisión humana.
type Riesgo int

const (
	RiesgoBajo Riesgo = iota
	RiesgoMedio
	RiesgoAlto
)

func (r Riesgo) String() string {
	switch r {
	case RiesgoAlto:
		return "alto"
	case RiesgoMedio:
		return "medio"
	default:
		return "bajo"
	}
}

// Accion es lo que la Capa de Juicio decide hacer con la propuesta.
type Accion string

const (
	// Enviar: la propuesta del agente sale tal cual.
	Enviar Accion = "enviar"
	// Sugerir: la propuesta no sale; queda para que una persona la apruebe o la edite.
	Sugerir Accion = "sugerir"
	// Derivar: el chat pasa a un humano ahora.
	Derivar Accion = "derivar"
	// Callar: no se responde (silencio estratégico); el bot espera.
	Callar Accion = "callar"
)

// Entrada es todo lo que la Capa de Juicio mira. Solo Etapa, Intent, Confianza, Accion
// y YaIntervinoHumano son obligatorios; el resto puede quedar en su valor neutro.
type Entrada struct {
	Etapa     string  // prospeccion | seguimiento | cierre | venta_confirmada
	Intent    string  // intención del clasificador comercial
	Confianza float64 // 0..1
	// Accion propuesta por el agente: responder | catalogo | foto | pedido_estado |
	// asesora | codigo | pedido.
	Accion string

	// Fase 2 (emoción y urgencia). Sentimiento neutro = 0.
	Sentimiento float64 // -1 (muy negativo) .. +1 (muy positivo)
	Urgencia    float64 // 0..1
	// Fase 3 (silencio y fatiga).
	IntentosSinRespuesta int
	SegundosDesdeUltimo  int
	// Fase 4 (clienta que vuelve y probabilidad de cierre).
	Reincidente bool
	Conversion  float64 // 0..1; <= 0 = desconocida
	// Si una persona ya tomó el chat antes, la decisión humana manda.
	YaIntervinoHumano bool
}

// Salida es la decisión, con el motivo (para el registro de auditoría).
type Salida struct {
	Accion Accion
	Riesgo Riesgo
	Motivo string
}

// umbralConfianza: por debajo, una acción de riesgo no se envía sin revisión humana.
const umbralConfianza = 0.60

// intentsRiesgoAlto: la ejecución involucra dinero, stock, una promesa o una queja.
var intentsRiesgoAlto = map[string]bool{
	"intencion_compra":     true,
	"confirmacion_compra":  true,
	"cancelacion":          true,
	"consulta_pago":        true,
	"objecion_precio":      true,
	"objecion":             true,
	"censura":              true,
}

// intentsRiesgoMedio: hablan de stock o de una promesa concreta, sin cerrar la venta.
var intentsRiesgoMedio = map[string]bool{
	"consulta_disponibilidad": true,
	"consulta_talla":          true,
	"consulta_delivery":       true,
	"consulta_producto":       true,
}

// RiesgoDe clasifica el riesgo de ejecutar una propuesta. La acción manda sobre la
// intención: armar un pedido o mostrar un código toca stock y dinero.
func RiesgoDe(accion, intent string) Riesgo {
	switch accion {
	case "pedido":
		return RiesgoAlto
	case "codigo":
		return RiesgoAlto
	case "asesora":
		return RiesgoAlto
	case "foto", "pedido_estado":
		return RiesgoMedio
	}
	if intentsRiesgoAlto[intent] {
		return RiesgoAlto
	}
	if intentsRiesgoMedio[intent] {
		return RiesgoMedio
	}
	return RiesgoBajo
}

// Decidir aplica las políticas por prioridad: la primera que dispara decide.
func Decidir(e Entrada) Salida {
	riesgo := RiesgoDe(e.Accion, e.Intent)

	// 1. Derivar a humano: la clienta lo pide, hay una queja, o una negociación
	//    (precio) que el bot no debe cerrar solo.
	if e.Accion == "asesora" {
		return Salida{Derivar, riesgo, "la clienta pidió una asesora"}
	}
	switch e.Intent {
	case "censura":
		return Salida{Derivar, riesgo, "queja o mensaje agresivo: lo atiende una persona"}
	case "objecion_precio":
		return Salida{Derivar, riesgo, "negociación de precio: no se descuenta sin criterio"}
	}

	// 2. Sentimiento muy negativo: aunque no sea queja, cambia de manos.
	if e.Sentimiento <= -0.5 && e.Etapa != "venta_confirmada" {
		return Salida{Derivar, riesgo, "sentimiento negativo: mejor una persona"}
	}

	// 3. Riesgo alto con poca confianza: se propone, no se envía solo.
	if riesgo == RiesgoAlto && e.Confianza < umbralConfianza {
		return Salida{Sugerir, riesgo, "acción de riesgo con confianza baja: requiere revisión"}
	}

	// 4. Silencio estratégico: dos recordatorios ya sin respuesta, no se insiste más.
	if e.IntentosSinRespuesta >= 2 {
		return Salida{Callar, riesgo, "ya se recordó dos veces sin respuesta: no insistir"}
	}

	// 5. Probabilidad de cierre muy baja: no empujar (nunca sobre una venta cerrada).
	//    Conversion <= 0 significa «desconocida»: no se evalúa.
	if e.Conversion > 0 && e.Conversion < 0.15 && e.Etapa != "venta_confirmada" && e.Etapa != "cierre" {
		return Salida{Callar, riesgo, "probabilidad de cierre muy baja: no insistir"}
	}

	return Salida{Enviar, riesgo, "bajo riesgo y confianza suficiente"}
}

func clamp01(v float64) float64 {
	if v < 0 {
		return 0
	}
	if v > 1 {
		return 1
	}
	return v
}

// EstimarConversion es una probabilidad de cierre heurística y transparente (0..1). Es la base que un
// modelo sustituirá cuando haya suficientes decisiones registradas; hasta entonces, suma señales claras:
// la etapa, la reincidencia, la urgencia, el ánimo y la intención. No promete precisión: ordena.
func EstimarConversion(e Entrada) float64 {
	base := map[string]float64{
		"prospeccion":      0.15,
		"seguimiento":      0.35,
		"cierre":           0.65,
		"venta_confirmada": 0.95,
	}[e.Etapa]
	if base == 0 {
		base = 0.20
	}
	if e.Reincidente {
		base += 0.10
	}
	base += 0.10 * clamp01(e.Urgencia)
	if e.Sentimiento < 0 {
		base += 0.15 * e.Sentimiento // el ánimo negativo resta
	}
	switch e.Intent {
	case "consulta_pago", "intencion_compra", "confirmacion_compra":
		base += 0.10
	case "objecion", "objecion_precio", "cancelacion":
		base -= 0.15
	}
	return clamp01(base)
}
