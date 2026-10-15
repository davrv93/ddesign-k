package bot

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// El agente recibe las fuentes vigentes de la tienda en cada turno (y nada si no hay) y el bot guarda con cuáles
// respondió (docs/FUENTES_CONTRATO.md §3, 15-10-2026).
func TestConocimientoViajaYUsoSeAnota(t *testing.T) {
	b, st, _ := setup(t, `{}`, false)
	ctx := context.Background()
	var last agente.Request
	var usar []int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		last = agente.Request{}
		_ = json.NewDecoder(r.Body).Decode(&last)
		_ = json.NewEncoder(w).Encode(agente.Reply{Accion: "responder", Respuesta: "¡Hola! Esta semana hay promo 🎉", FuentesUsadas: usar})
	}))
	t.Cleanup(srv.Close)
	b.Agent = agente.New(srv.URL, 0)

	handle(b, text("hola"))
	if last.Conocimiento != nil {
		t.Fatalf("sin fuentes no se manda conocimiento: %v", last.Conocimiento)
	}

	datos, _ := json.Marshal(store.PromoDatos{Mecanica: "porcentaje", Valor: 20, Alcance: store.PromoAlcance{Tipo: "todo"}})
	f := &store.Fuente{Tipo: "promo", Titulo: "Pelotera", Datos: datos}
	if err := st.CrearFuente(ctx, f); err != nil {
		t.Fatal(err)
	}
	if _, err := st.CambiarEstadoFuente(ctx, f.ID, "activo"); err != nil {
		t.Fatal(err)
	}
	b.conoc.exp = b.conoc.exp.AddDate(-1, 0, 0) // sin esperar los 20 s de la caché
	usar = []int64{f.ID, 999999}
	handle(b, text("tienen promo?"))
	c, _ := last.Conocimiento.(map[string]any)
	promos, _ := c["promos"].([]any)
	if len(promos) != 1 || c["version"] == "" {
		t.Fatalf("el agente debía recibir la promo vigente: %v", last.Conocimiento)
	}
	cvs, err := st.ListConversations(ctx, 10)
	if err != nil || len(cvs) == 0 {
		t.Fatalf("conversación: %v", err)
	}
	us, err := st.FuentesDeConversacion(ctx, cvs[0].ID)
	if err != nil || len(us) != 1 || us[0].FuenteID != f.ID {
		t.Fatalf("uso anotado (sin el id que no existe): %+v %v", us, err)
	}
	if pid, _, _ := st.PromoDeConversacion(ctx, cvs[0].ID); pid != f.ID {
		t.Fatalf("promo de la conversación: %d", pid)
	}
}
