package bot

import (
	"context"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// TestFollowupRecordatorio: el seguimiento manda un recordatorio, lo cuenta y calla al llegar al tope.
func TestFollowupRecordatorio(t *testing.T) {
	b, st, fe := setup(t, `{"intent":"otro","reply":"hola"}`, false)
	ctx := context.Background()
	_, conv, err := st.UpsertCustomer(ctx, "51911111111@s.whatsapp.net", "51911111111", "Ana")
	if err != nil || conv == nil {
		t.Fatalf("cliente: %v", err)
	}
	// La última (y única) entrada de la clienta fue hace 25 h.
	old := time.Now().UTC().Add(-25 * time.Hour)
	if err := st.AddMessage(ctx, &store.Message{ConversationID: conv.ID, Direction: "in", Kind: "text", Body: "hola", Author: "cliente", CreatedAt: old, WAID: "OLD"}); err != nil {
		t.Fatal(err)
	}

	cand, err := st.ConversationsToFollowUp(ctx, 24*time.Hour, 2)
	if err != nil || len(cand) != 1 || cand[0].ID != conv.ID {
		t.Fatalf("candidatas: %v (%d)", err, len(cand))
	}

	n := len(fe.sent)
	b.Followup(ctx, cand[0])
	b.Drain(5 * time.Second)
	if len(fe.sent) != n+1 {
		t.Fatalf("debía salir un recordatorio, salieron %d", len(fe.sent)-n)
	}
	if got := fe.lastText(); got == "" {
		t.Fatal("recordatorio vacío")
	}
	if c := st.FollowupCount(ctx, conv.ID); c != 1 {
		t.Fatalf("contador de recordatorios = %d, quería 1", c)
	}

	// Segundo recordatorio: todavía se permite.
	b.Followup(ctx, conv)
	b.Drain(5 * time.Second)
	if c := st.FollowupCount(ctx, conv.ID); c != 2 {
		t.Fatalf("contador = %d, quería 2", c)
	}

	// Tercero: la Capa de Juicio calla (dos sin respuesta) y ya no es candidata.
	n = len(fe.sent)
	b.Followup(ctx, conv)
	b.Drain(5 * time.Second)
	if len(fe.sent) != n {
		t.Fatal("no debía salir un tercer recordatorio")
	}
	if cand, _ := st.ConversationsToFollowUp(ctx, 24*time.Hour, 2); len(cand) != 0 {
		t.Fatalf("ya no debía ser candidata: %d", len(cand))
	}
}

// TestFollowupSeReiniciaAlVolver: si la clienta escribe, el contador vuelve a cero.
func TestFollowupSeReiniciaAlVolver(t *testing.T) {
	_, st, _ := setup(t, `{"intent":"otro","reply":"hola"}`, false)
	ctx := context.Background()
	_, conv, _ := st.UpsertCustomer(ctx, "51922222222@s.whatsapp.net", "51922222222", "Beto")
	if err := st.MarkFollowUp(ctx, conv.ID); err != nil {
		t.Fatal(err)
	}
	if err := st.AddMessage(ctx, &store.Message{ConversationID: conv.ID, Direction: "in", Kind: "text", Body: "hola", Author: "cliente", WAID: "NEW"}); err != nil {
		t.Fatal(err)
	}
	if c := st.FollowupCount(ctx, conv.ID); c != 0 {
		t.Fatalf("el contador debía reiniciarse, quedó en %d", c)
	}
}
