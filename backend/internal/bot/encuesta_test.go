package bot

import (
	"testing"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
)

// Voto de encuesta (evolution-go: data.pollVote) → texto, como si la clienta lo hubiera escrito.
func TestVotoDeEncuestaComoTexto(t *testing.T) {
	body := []byte(`{"event":"Message","data":{"Info":{"ID":"A1","Chat":"51999888777@s.whatsapp.net"},
		"Message":{"pollUpdateMessage":{}},"pollVote":{"pollMessageId":"P1","selectedOptions":["Lima"]}}}`)
	in, err := ParseWebhook(body)
	if err != nil || in == nil || in.Text != "Lima" {
		t.Fatalf("voto Lima: in=%+v err=%v", in, err)
	}
	// Menú: «1️⃣ Ver catálogo y precios» llega como «1».
	body = []byte(`{"event":"Message","data":{"Info":{"ID":"A2","Chat":"51999888777@s.whatsapp.net"},
		"Message":{"pollUpdateMessage":{}},"pollVote":{"selectedOptions":["1️⃣ Ver catálogo y precios"]}}}`)
	if in, _ = ParseWebhook(body); in == nil || in.Text != "1" {
		t.Fatalf("voto del menú: %+v", in)
	}
	// Voto retirado: no es un mensaje.
	body = []byte(`{"event":"Message","data":{"Info":{"ID":"A3","Chat":"51999888777@s.whatsapp.net"},
		"Message":{"pollUpdateMessage":{}},"pollVote":{"selectedOptions":[]}}}`)
	if in, _ = ParseWebhook(body); in != nil {
		t.Fatalf("voto retirado debía ser nil: %+v", in)
	}
}

// «Sí, confirmo» de la encuesta de confirmación lo entiende handleConfirm como sí; «Cancelar» como no.
func TestOpcionesDeConfirmacion(t *testing.T) {
	si := normalize("Sí, confirmo")
	if !(isAny(si, "si", "sí", "s", "ok", "dale", "confirmo", "confirmar", "si confirmo") || len(si) > 3 && si[:3] == "si ") {
		t.Fatalf("«Sí, confirmo» normalizado = %q no es sí", si)
	}
	if no := normalize("Cancelar"); !isAny(no, "no", "n", "cancelar", "cancela", "no gracias") {
		t.Fatalf("«Cancelar» normalizado = %q no es no", no)
	}
}

func TestUltimaPregunta(t *testing.T) {
	if q := ultimaPregunta("Excelente 😊 ¿El envío sería para Lima o provincia?"); q != "¿El envío sería para Lima o provincia?" {
		t.Fatalf("pregunta: %q", q)
	}
	if q := ultimaPregunta("Sin pregunta."); q != "Elige una opción 👇" {
		t.Fatalf("sin pregunta: %q", q)
	}
}

// Con encuesta, la pregunta no se repite en el texto (12-10-2026: salía en el texto y otra vez como título de la encuesta).
func TestSinPreguntaDuplicada(t *testing.T) {
	q := "¿Prefiere llamar la atención o verse elegante y discreta?"
	if s := sinPregunta("Perfecto 😊 Tengo colores vibrantes y sobrios. "+q+" 😊", q); s != "Perfecto 😊 Tengo colores vibrantes y sobrios." {
		t.Fatalf("debía quitar la pregunta final: %q", s)
	}
	if s := sinPregunta(q, q); s != "" {
		t.Fatalf("solo la pregunta: debía quedar vacío: %q", s)
	}
	// No está al final: no se corta la frase.
	if s := sinPregunta(q+" Te espero.", q); s != q+" Te espero." {
		t.Fatalf("pregunta en medio: %q", s)
	}
	r := &agente.Reply{RespuestasRapidas: []agente.RespuestaRapida{{Label: "Lima"}, {Label: " "}, {Label: "Provincia"}}}
	if ops := opcionesEncuesta(r); len(ops) != 2 {
		t.Fatalf("opciones: %v", ops)
	}
}
