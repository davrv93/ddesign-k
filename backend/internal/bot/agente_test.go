package bot

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
)

// fakeAgente responde según el mensaje y guarda la última petición.
func fakeAgente(t *testing.T, last *agente.Request) *agente.Client {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req agente.Request
		_ = json.NewDecoder(r.Body).Decode(&req)
		*last = req
		var rep agente.Reply
		switch req.Mensaje {
		case "estoy triste no encuentro vestido":
			rep = agente.Reply{Intencion: "estado_animo", Accion: "responder", Respuesta: "Ánimo, te ayudo a encontrarlo 💖"}
		case "kiero ablar con una persona":
			rep = agente.Reply{Intencion: "asesora", Accion: "asesora"}
		case "cuanto cuesta el vestido esmeralda":
			rep = agente.Reply{Intencion: "consulta_precio", Accion: "codigo", Codigo: "V01"}
		default:
			http.Error(w, "caído", http.StatusBadGateway)
			return
		}
		_ = json.NewEncoder(w).Encode(rep)
	}))
	t.Cleanup(srv.Close)
	return agente.New(srv.URL, 5*time.Second)
}

func TestAgenteAtiendeTextoLibre(t *testing.T) {
	b, _, fe := setup(t, `{"intent":"otro","reply":"respuesta de gemini"}`, false)
	var last agente.Request
	b.Agent = fakeAgente(t, &last)

	handle(b, text("hola"))
	handle(b, text("estoy triste no encuentro vestido"))
	mustContain(t, fe.lastText(), "te ayudo a encontrarlo")
	if last.Cliente != "Ana López" || len(last.Historial) == 0 || last.Historial[0].Rol != "cliente" {
		t.Fatalf("el agente debía recibir cliente e historial: %+v", last)
	}
	for _, h := range last.Historial {
		if h.Texto == "estoy triste no encuentro vestido" {
			t.Fatal("el mensaje actual no debe repetirse en el historial")
		}
	}

	handle(b, text("kiero ablar con una persona"))
	mustContain(t, fe.lastText(), "asesora")
}

func TestAgenteOfreceProducto(t *testing.T) {
	b, _, fe := setup(t, `{}`, false)
	var last agente.Request
	b.Agent = fakeAgente(t, &last)
	handle(b, text("cuanto cuesta el vestido esmeralda"))
	mustContain(t, fe.lastText(), "Vestido Esmeralda")
}

func TestAgenteCaidoVuelveAGemini(t *testing.T) {
	b, _, fe := setup(t, `{"intent":"otro","reply":"respuesta de gemini"}`, false)
	var last agente.Request
	b.Agent = fakeAgente(t, &last)
	handle(b, text("algo que el agente no sabe"))
	mustContain(t, fe.lastText(), "respuesta de gemini")
}
