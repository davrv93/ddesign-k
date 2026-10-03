package bot

import (
	"context"
	"strings"
	"testing"
	"time"
)

// Dos clientas quieren la última unidad: la segunda no debe verla disponible mientras la
// primera confirma, y al vencer la reserva vuelve a estar libre.
func TestReservaApartaLaTallaMientrasConfirma(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	ctx := context.Background()
	handle(b, text("V01"))
	handle(b, text("S")) // sólo hay 1 en S
	mustContain(t, fe.lastText(), "Resumen de tu pedido", "apartamos")

	p, _ := st.GetProductByCode(ctx, "V01")
	v := p.VariantBySize("S")
	if v.Stock != 1 || v.Reserved != 1 || v.Available() != 0 {
		t.Fatalf("esperaba físico 1, reservado 1, disponible 0: %+v", v)
	}

	// Otra clienta pregunta por la misma talla.
	otra := text("V01")
	otra.Chat, otra.Phone = "51900000000@s.whatsapp.net", "51900000000"
	handle(b, otra)
	mustContain(t, fe.lastText(), "Tallas disponibles: M (2)")
	if strings.Contains(fe.lastText(), "S (") {
		t.Fatal("la talla S está reservada por otra clienta y no debía ofrecerse")
	}
	otraS := text("S")
	otraS.Chat, otraS.Phone = otra.Chat, otra.Phone
	handle(b, otraS)
	mustContain(t, fe.lastText(), "se agotó")

	// La primera cancela: la reserva se libera y S vuelve a estar disponible.
	handle(b, text("no"))
	p, _ = st.GetProductByCode(ctx, "V01")
	if p.VariantBySize("S").Available() != 1 {
		t.Fatalf("al cancelar debía liberarse la reserva: %+v", p.VariantBySize("S"))
	}
}

func TestReservaVencidaSeLibera(t *testing.T) {
	b, st, _ := setup(t, `{}`, false)
	ctx := context.Background()
	handle(b, text("V01"))
	handle(b, text("S"))
	// Forzamos el vencimiento.
	if _, err := st.DB.ExecContext(ctx, `UPDATE stock_reservations SET expires_at=?`, time.Now().UTC().Add(-time.Minute)); err != nil {
		t.Fatal(err)
	}
	p, _ := st.GetProductByCode(ctx, "V01")
	if p.VariantBySize("S").Available() != 1 {
		t.Fatal("una reserva vencida no debe descontar disponibilidad")
	}
	if n, _ := st.PurgeExpiredReservations(ctx); n != 1 {
		t.Fatalf("debía purgar 1 reserva, purgó %d", n)
	}
}

func TestConfirmarDescuentaFisicoYBorraReserva(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	ctx := context.Background()
	handle(b, text("V01"))
	handle(b, text("M"))
	handle(b, text("si"))
	mustContain(t, fe.lastText(), "confirmado")
	p, _ := st.GetProductByCode(ctx, "V01")
	v := p.VariantBySize("M")
	if v.Stock != 1 || v.Reserved != 0 {
		t.Fatalf("esperaba físico 1 y sin reserva: %+v", v)
	}
}
