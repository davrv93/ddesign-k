package kommo

import (
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
)

// EventoWeb es lo que el agente (chat web de /demo-design) avisa al final de cada turno por
// POST /api/internal/crm/evento. El chat web no pasa por el backend: sin esto no llegaría al CRM.
type EventoWeb struct {
	Canal        string          `json:"canal"`        // «web»
	Conversacion string          `json:"conversacion"` // id de la sesión del navegador («Nuevo chat» abre otra)
	Nombre       string          `json:"nombre"`
	Etapa        string          `json:"etapa"`
	Memoria      json.RawMessage `json:"memoria"`
	Intencion    string          `json:"intencion"` // intención comercial del turno
	Accion       string          `json:"accion"`    // responder | pedido | codigo | asesora…
	Codigo       string          `json:"codigo"`
	Talla        string          `json:"talla"`
	Sugerencias  []string        `json:"sugerencias"` // prendas cuya foto se mostró
	DesdeAnuncio bool            `json:"desde_anuncio"`
	Anuncio      string          `json:"anuncio"`
	EnvioCosto   float64         `json:"envio_costo"`
	Mensaje      string          `json:"mensaje"`
	Respuesta    string          `json:"respuesta"`
	Foto         *struct {
		Codigo    string  `json:"codigo"`
		Caso      string  `json:"caso"`
		Similitud float64 `json:"similitud"`
	} `json:"foto"`
}

var reSesion = regexp.MustCompile(`^[A-Za-z0-9_-]{4,64}$`)

// Evento convierte el aviso del chat web en un evento del sincronizador (la misma lógica que WhatsApp).
func (w EventoWeb) Evento() (Evento, error) {
	if w.Canal != "web" {
		return Evento{}, errors.New("canal debe ser «web»")
	}
	if !reSesion.MatchString(w.Conversacion) {
		return Evento{}, errors.New("conversacion inválida (4–64 letras, números, - o _)")
	}
	ev := Evento{Canal: "web", Clave: "web:" + w.Conversacion, Nombre: strings.TrimSpace(w.Nombre), Etapa: w.Etapa,
		Memoria: w.Memoria, Intencion: w.Intencion, Anuncio: w.DesdeAnuncio, AnuncioTitulo: w.Anuncio,
		EnvioCosto: w.EnvioCosto, Mostrados: w.Sugerencias}
	if string(ev.Memoria) == "null" {
		ev.Memoria = nil
	}
	if reCodigo.MatchString(w.Codigo) && (w.Accion == "pedido" || w.Accion == "codigo") {
		ev.Producto = strings.ToUpper(w.Codigo)
	}
	switch w.Accion {
	case "pedido":
		if w.Talla != "" && ev.Producto != "" {
			ev.Talla = strings.ToUpper(w.Talla)
			ev.Hitos = append(ev.Hitos, fmt.Sprintf("🛍️ Eligió talla %s del %s", ev.Talla, ev.Producto))
		}
	case "asesora":
		ev.Hitos = append(ev.Hitos, "🙋‍♀️ Pidió hablar con una asesora")
	}
	if f := w.Foto; f != nil {
		ev.Hitos = append(ev.Hitos, fmt.Sprintf("📷 Mandó una foto: %s (%s, similitud %.2f)", firstNonEmpty(f.Codigo, "sin coincidencia"), f.Caso, f.Similitud))
	}
	if m := strings.TrimSpace(w.Mensaje); m != "" || w.Foto != nil {
		if w.Foto != nil {
			m = strings.TrimSpace("[foto] " + m)
		}
		ev.Turno = append(ev.Turno, Linea{Rol: "cliente", Texto: m})
	}
	if r := strings.TrimSpace(w.Respuesta); r != "" {
		ev.Turno = append(ev.Turno, Linea{Rol: "bot", Texto: r})
	}
	return ev, nil
}
