package api

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// Eliminar una conversación: solo admin, solo de su empresa; se van la conversación y sus mensajes, quedan el
// cliente y sus pedidos, y su línea de tiempo dice quién la eliminó.
func TestEliminarConversacion(t *testing.T) {
	h, st, _ := servidorMulti(t)
	ctx := context.Background()
	sfx := strings.ReplaceAll(time.Now().Format("150405.000000"), ".", "")
	a := prepararEmpresa(t, h, st, "ca"+sfx, "Alfa")
	b := prepararEmpresa(t, h, st, "cb"+sfx, "Beta")
	conv := func(e *empresaHTTP) (*store.Store, int64) {
		tn, err := st.TenantBySlug(ctx, e.slug)
		if err != nil {
			t.Fatal(err)
		}
		ts := st.ForTenant(tn.ID)
		cv, err := ts.ConversationByCustomer(ctx, e.clienta)
		if err != nil {
			t.Fatal(err)
		}
		for _, txt := range []string{"hola", "¿tienen el V21?"} {
			if err := ts.AddMessage(ctx, &store.Message{ConversationID: cv.ID, Direction: "in", Body: txt}); err != nil {
				t.Fatal(err)
			}
		}
		return ts, cv.ID
	}
	sa, ca := conv(a)
	sb, cb := conv(b)
	A, B := "/"+a.slug+"/api/conversations/", "/"+b.slug+"/api/conversations/"

	// La asesora no puede.
	if w := llamar(h, "DELETE", A+itoa(ca), a.asesora, ""); w.Code != 403 {
		t.Fatalf("la asesora eliminó una conversación: %d", w.Code)
	}
	// Con el id de la otra empresa: no existe (y la de B queda intacta).
	if w := llamar(h, "DELETE", A+itoa(cb), a.admin, ""); w.Code != 404 {
		t.Fatalf("A eliminó la conversación de B: %d", w.Code)
	}
	// El token de una empresa no abre la otra.
	if w := llamar(h, "DELETE", B+itoa(cb), a.admin, ""); w.Code != 401 {
		t.Fatalf("token de A en B: %d", w.Code)
	}
	if ms, _ := sb.ListMessages(ctx, cb, 50); len(ms) != 2 {
		t.Fatalf("B perdió mensajes: %d", len(ms))
	}

	// La admin de A sí.
	if w := llamar(h, "DELETE", A+itoa(ca), a.admin, ""); w.Code != 200 {
		t.Fatalf("eliminar: %d %s", w.Code, w.Body)
	}
	if _, err := sa.GetConversation(ctx, ca); err == nil {
		t.Fatal("la conversación sigue")
	}
	if ms, _ := sa.ListMessages(ctx, ca, 50); len(ms) != 0 {
		t.Fatalf("quedaron %d mensajes", len(ms))
	}
	if _, err := sa.GetClienta(ctx, a.clienta); err != nil {
		t.Fatalf("se perdió el cliente: %v", err)
	}
	if o, err := sa.GetOrder(ctx, a.pedido); err != nil || o.CustomerID != a.clienta {
		t.Fatalf("se perdió el pedido: %v", err)
	}
	evs, err := sa.Actividad(ctx, a.clienta, 50)
	if err != nil {
		t.Fatal(err)
	}
	visto := false
	for _, e := range evs {
		if strings.Contains(e.Texto, "Conversación eliminada por") && strings.Contains(e.Texto, "2 mensajes") {
			visto = true
		}
	}
	if !visto {
		t.Fatalf("la línea de tiempo no registra la eliminación: %+v", evs)
	}
	// Segunda vez: ya no existe.
	if w := llamar(h, "DELETE", A+itoa(ca), a.admin, ""); w.Code != 404 {
		t.Fatalf("eliminar dos veces: %d", w.Code)
	}
	// B sigue igual.
	if _, err := sb.GetConversation(ctx, cb); err != nil {
		t.Fatalf("la conversación de B desapareció: %v", err)
	}
}
