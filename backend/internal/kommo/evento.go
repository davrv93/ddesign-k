package kommo

import (
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
	"time"
)

// Evento es lo que un canal (bot de WhatsApp o chat web) sabe de una conversación al terminar un turno. Lleva el
// estado completo, no solo el cambio: si se pierde un evento, el siguiente deja el lead al día igual.
type Evento struct {
	Canal          string // whatsapp | web
	Clave          string // «wa:<conversation_id>» o «web:<sesión>»: una clave, un lead
	ConversationID int64  // id de la conversación en la SQLite (solo WhatsApp): enlace al panel
	// Sesion es el inicio (unix) de la sesión. Si el lead ya está cerrado (ganado o perdido) y la clienta vuelve en
	// una sesión nueva, es otra venta: se abre otro lead para el mismo contacto.
	Sesion   int64
	Nombre   string
	Telefono string // solo dígitos, con 51; vacío en el chat web
	Etapa    string // prospeccion | seguimiento | cierre | venta_confirmada (la del agente)
	Memoria  json.RawMessage
	// Intencion es la intención comercial del turno (consulta_material, objecion_precio…): da el hito legible.
	Intencion     string
	Producto      string   // prenda en foco si la memoria no la trae (p. ej. el pedido del chat web)
	Talla         string   // talla elegida (chat web: «Talla M del V35»)
	Mostrados     []string // prendas cuya foto se envió en este turno
	Pedido        *Pedido
	Anuncio       bool
	AnuncioTitulo string
	EnvioCosto    float64 // costo del envío si quien llama lo sabe; si no, se pregunta a Opciones.Envios
	Pagado        bool    // llegó el comprobante de pago
	Hitos         []string
	Turno         []Linea // último intercambio, para la transcripción (si está activada)
	Demo          bool    // dato de demostración (seed): lleva la etiqueta «demo»
	Cuando        time.Time
}

// Pedido es el pedido de la conversación en el tablero de kddesign.
type Pedido struct {
	ID       int64
	Estado   string // consulta | pendiente | confirmado | preparando | enviado | entregado | cancelado
	Total    float64
	Codigo   string
	Nombre   string
	Talla    string
	Cantidad int
}

// Linea es un mensaje de la transcripción.
type Linea struct {
	Rol   string // cliente | bot | asesora
	Texto string
}

// memoria es lo que se lee de la ficha del agente (agente/app/memoria.py). El resto se ignora.
type memoria struct {
	Producto    string         `json:"producto"`
	Temperatura string         `json:"temperatura"`
	Motivo      string         `json:"temperatura_motivo"`
	LlegoPor    string         `json:"llego_por"`
	Sabemos     map[string]any `json:"sabemos"`
}

func leerMemoria(raw json.RawMessage) memoria {
	var m memoria
	if len(raw) > 0 {
		_ = json.Unmarshal(raw, &m)
	}
	return m
}

func (m memoria) s(k string) string {
	switch v := m.Sabemos[k].(type) {
	case string:
		return strings.TrimSpace(v)
	case float64:
		return fmt.Sprint(v)
	}
	return ""
}

var confirmados = map[string]bool{"confirmado": true, "preparando": true, "enviado": true, "entregado": true}

// Lima: Perú no tiene horario de verano.
var lima = time.FixedZone("America/Lima", -5*3600)

var (
	tempNombre    = map[string]string{"frio": "Fría", "tibio": "Tibia", "caliente": "Caliente"}
	horarioNombre = map[string]string{"dia": "Día", "noche": "Noche"}
	diasCortos    = [...]string{"dom", "lun", "mar", "mié", "jue", "vie", "sáb"}
	mesesCortos   = [...]string{"ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"}
	reCodigo      = regexp.MustCompile(`(?i)\bV\d{2}\b`)
)

// Cuando legible: «vie 9-oct 17:00».
func cuandoCorto(t time.Time) string {
	t = t.In(lima)
	return fmt.Sprintf("%s %d-%s %s", diasCortos[t.Weekday()], t.Day(), mesesCortos[t.Month()-1], t.Format("15:04"))
}

func parseCita(s string) (time.Time, bool) {
	t, err := time.ParseInLocation("2006-01-02T15:04", s, lima)
	return t, err == nil
}

// parseFecha: solo fechas completas; «2026-10» (mes sin día) no se puede poner en un campo de fecha.
func parseFecha(s string) (time.Time, bool) {
	t, err := time.ParseInLocation("2006-01-02", s, lima)
	if err != nil {
		return time.Time{}, false
	}
	return t.Add(12 * time.Hour), true // mediodía de Lima: ningún huso lo corre de día
}

// Intenciones comerciales que merecen una nota, en palabras de la asesora. %s = « del V35» o "".
var hitoIntencion = map[string]string{
	"consulta_material":       "Preguntó por la tela%s",
	"consulta_precio":         "Preguntó el precio%s",
	"consulta_talla":          "Preguntó por tallas o medidas%s",
	"consulta_color":          "Preguntó por colores%s",
	"consulta_disponibilidad": "Preguntó si hay stock%s",
	"consulta_ubicacion":      "Preguntó por el showroom o quiere probárselo",
	"consulta_delivery":       "Preguntó por el envío",
	"consulta_pago":           "Preguntó cómo pagar",
	"objecion_precio":         "Le pareció caro o pidió descuento%s",
	"objecion":                "Dudó: lo va a pensar o teme que no le quede",
	"comparacion":             "Pidió ver otras opciones",
	"intencion_compra":        "Dijo que quiere comprarlo%s",
	"cancelacion":             "Dijo que ya no lo quiere",
}

// Datos de pago: nunca van a Kommo, ni en la transcripción (el repo y el CRM no son el lugar).
var rePago = regexp.MustCompile(`(?i)datos para el pago|yape|plin|\bbcp\b|interbank|bbva|scotiabank|\bcci\b|n[uú]mero de cuenta|titular`)
var reDigitos = regexp.MustCompile(`\d[\d \-]{7,}\d`) // 9 cifras o más: teléfonos y cuentas

// Limpiar quita de un mensaje lo que no debe salir de kddesign: los datos de pago enteros y los números largos
// (cuentas, teléfonos) se tapan.
func Limpiar(texto string) string {
	if rePago.MatchString(texto) {
		return "[datos de pago omitidos]"
	}
	return reDigitos.ReplaceAllString(texto, "•••")
}
