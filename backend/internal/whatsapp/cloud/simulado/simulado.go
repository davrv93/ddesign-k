// Package simulado es una Graph API de WhatsApp de mentira para las pruebas: valida rutas, token, cuerpos, límites de
// botones y la regla de las 24 h (mensaje libre fuera de ventana → error 131047), y solo acepta plantillas aprobadas.
package simulado

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

type Enviado struct {
	To        string
	Tipo      string // text | image | interactive | template
	Texto     string
	Opciones  []string
	Plantilla string
	Params    []string
	Cuerpo    map[string]any
}

type Servidor struct {
	*httptest.Server
	Token, PhoneNumberID, WABAID string

	mu         sync.Mutex
	enviados   []Enviado
	entrantes  map[string]time.Time // número → último mensaje de la clienta
	plantillas map[string]string    // nombre → estado (APPROVED, PENDING…)
	Ahora      func() time.Time
	errores    []string
}

func New(token, phoneNumberID, wabaID string) *Servidor {
	s := &Servidor{Token: token, PhoneNumberID: phoneNumberID, WABAID: wabaID, entrantes: map[string]time.Time{},
		plantillas: map[string]string{}, Ahora: time.Now}
	s.Server = httptest.NewServer(http.HandlerFunc(s.handler))
	return s
}

// Entrante: la clienta escribió en este momento (abre la ventana de 24 h).
func (s *Servidor) Entrante(numero string, en time.Time) {
	s.mu.Lock()
	s.entrantes[numero] = en
	s.mu.Unlock()
}

// Plantilla registra una plantilla con su estado.
func (s *Servidor) Plantilla(nombre, estado string) {
	s.mu.Lock()
	s.plantillas[nombre] = estado
	s.mu.Unlock()
}

func (s *Servidor) Enviados() []Enviado {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]Enviado(nil), s.enviados...)
}

// Errores: lo que el simulado rechazó por estar mal formado (debe quedar vacío).
func (s *Servidor) Errores() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]string(nil), s.errores...)
}

func (s *Servidor) mal(w http.ResponseWriter, status, code int, msg string) {
	s.mu.Lock()
	if code != 131047 {
		s.errores = append(s.errores, msg)
	}
	s.mu.Unlock()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"error": map[string]any{"message": msg, "code": code, "type": "OAuthException"}})
}

func (s *Servidor) handler(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("Authorization") != "Bearer "+s.Token {
		s.mal(w, 401, 190, "token inválido")
		return
	}
	partes := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
	if len(partes) < 2 || !strings.HasPrefix(partes[0], "v") {
		s.mal(w, 404, 100, "ruta sin versión: "+r.URL.Path)
		return
	}
	switch {
	case r.Method == http.MethodPost && len(partes) == 3 && partes[1] == s.PhoneNumberID && partes[2] == "messages":
		s.mensaje(w, r)
	case r.Method == http.MethodGet && len(partes) == 3 && partes[1] == s.WABAID && partes[2] == "message_templates":
		s.mu.Lock()
		var data []map[string]any
		for n, st := range s.plantillas {
			data = append(data, map[string]any{"name": n, "language": "es", "status": st, "category": "MARKETING",
				"components": []map[string]any{{"type": "BODY", "text": "Hola {{1}}"}}})
		}
		s.mu.Unlock()
		_ = json.NewEncoder(w).Encode(map[string]any{"data": data})
	default:
		s.mal(w, 404, 100, "ruta desconocida: "+r.Method+" "+r.URL.Path)
	}
}

func (s *Servidor) mensaje(w http.ResponseWriter, r *http.Request) {
	data, _ := io.ReadAll(r.Body)
	var m map[string]any
	if err := json.Unmarshal(data, &m); err != nil {
		s.mal(w, 400, 100, "json inválido")
		return
	}
	if m["status"] == "read" {
		_ = json.NewEncoder(w).Encode(map[string]any{"success": true})
		return
	}
	if m["messaging_product"] != "whatsapp" {
		s.mal(w, 400, 100, "falta messaging_product")
		return
	}
	to, _ := m["to"].(string)
	if to == "" || strings.ContainsAny(to, "@+ ") {
		s.mal(w, 400, 100, "to debe ser solo dígitos: "+to)
		return
	}
	tipo, _ := m["type"].(string)
	e := Enviado{To: to, Tipo: tipo, Cuerpo: m}
	if tipo != "template" {
		s.mu.Lock()
		ult, ok := s.entrantes[to]
		ahora := s.Ahora()
		s.mu.Unlock()
		if !ok || ahora.Sub(ult) > 24*time.Hour {
			s.mal(w, 400, 131047, "Re-engagement message: more than 24 hours have passed")
			return
		}
	}
	switch tipo {
	case "text":
		t, _ := m["text"].(map[string]any)
		e.Texto, _ = t["body"].(string)
	case "image":
		t, _ := m["image"].(map[string]any)
		if l, _ := t["link"].(string); l == "" {
			s.mal(w, 400, 100, "imagen sin link")
			return
		}
		e.Texto, _ = t["caption"].(string)
	case "interactive":
		it, _ := m["interactive"].(map[string]any)
		body, _ := it["body"].(map[string]any)
		e.Texto, _ = body["text"].(string)
		act, _ := it["action"].(map[string]any)
		switch it["type"] {
		case "button":
			bs, _ := act["buttons"].([]any)
			if len(bs) == 0 || len(bs) > 3 {
				s.mal(w, 400, 100, fmt.Sprintf("botones: %d (1–3)", len(bs)))
				return
			}
			for _, b := range bs {
				rp, _ := b.(map[string]any)["reply"].(map[string]any)
				t, _ := rp["title"].(string)
				if utf8.RuneCountInString(t) > 20 {
					s.mal(w, 400, 100, "título de botón > 20: "+t)
					return
				}
				e.Opciones = append(e.Opciones, t)
			}
		case "list":
			secs, _ := act["sections"].([]any)
			for _, sc := range secs {
				rows, _ := sc.(map[string]any)["rows"].([]any)
				if len(rows) > 10 {
					s.mal(w, 400, 100, "lista con más de 10 filas")
					return
				}
				for _, rw := range rows {
					t, _ := rw.(map[string]any)["title"].(string)
					if utf8.RuneCountInString(t) > 24 {
						s.mal(w, 400, 100, "fila > 24: "+t)
						return
					}
					e.Opciones = append(e.Opciones, t)
				}
			}
		default:
			s.mal(w, 400, 100, "interactive desconocido")
			return
		}
	case "template":
		t, _ := m["template"].(map[string]any)
		e.Plantilla, _ = t["name"].(string)
		s.mu.Lock()
		st := s.plantillas[e.Plantilla]
		s.mu.Unlock()
		if st != "APPROVED" {
			s.mal(w, 400, 132001, "plantilla inexistente o no aprobada: "+e.Plantilla)
			return
		}
		if cs, ok := t["components"].([]any); ok {
			for _, c := range cs {
				ps, _ := c.(map[string]any)["parameters"].([]any)
				for _, p := range ps {
					v, _ := p.(map[string]any)["text"].(string)
					e.Params = append(e.Params, v)
				}
			}
		}
	default:
		s.mal(w, 400, 100, "tipo desconocido: "+tipo)
		return
	}
	s.mu.Lock()
	s.enviados = append(s.enviados, e)
	n := len(s.enviados)
	s.mu.Unlock()
	_ = json.NewEncoder(w).Encode(map[string]any{"messaging_product": "whatsapp",
		"messages": []map[string]any{{"id": fmt.Sprintf("wamid.SIM%d", n)}}})
}
