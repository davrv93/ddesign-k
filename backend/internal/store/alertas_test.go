package store

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
)

// Alertas de clienta caliente: una por conversación y semana, atender queda a nombre de quien la atiende, y nada de una
// empresa se ve ni se toca desde la otra (SQLite y, con JMD_TEST_MYSQL_DSN, MariaDB).
func TestAlertasYAislamiento(t *testing.T) {
	for nombre, base := range backends(t) {
		t.Run(nombre, func(t *testing.T) {
			ctx := ConAutor(context.Background(), "Rosa")
			a := nuevaEmpresa(t, base, "aa-"+nombre+sufijo(), "Uno", 5)
			b := nuevaEmpresa(t, base, "ab-"+nombre+sufijo(), "Dos", 5)
			al := &Alerta{ConversationID: a.conv.ID, Motivo: "evento en 9 días", Resumen: json.RawMessage(`{"evento":"boda"}`)}
			if err := a.st.CrearAlerta(ctx, al); err != nil {
				t.Fatal(err)
			}
			if al.ID == 0 || al.CustomerID != a.cust.ID || al.Tipo != "caliente" {
				t.Fatalf("creada: %+v", al)
			}
			if err := a.st.CrearAlerta(ctx, &Alerta{ConversationID: a.conv.ID}); !errors.Is(err, AlertaRepetida) {
				t.Fatalf("la segunda en la semana debía ser repetida: %v", err)
			}
			if err := b.st.CrearAlerta(ctx, &Alerta{ConversationID: a.conv.ID}); !errors.Is(err, ErrNotFound) {
				t.Fatalf("con la conversación de otra empresa: %v", err)
			}
			if as, _ := b.st.ListarAlertas(ctx, false); len(as) != 0 {
				t.Fatalf("la otra empresa no ve alertas ajenas: %d", len(as))
			}
			if err := b.st.AtenderAlerta(ctx, al.ID); !errors.Is(err, ErrNotFound) {
				t.Fatalf("la otra empresa no atiende alertas ajenas: %v", err)
			}
			if err := b.st.MarcarAlertaEnviada(ctx, al.ID); err != nil {
				t.Fatal(err)
			}
			as, err := a.st.ListarAlertas(ctx, true)
			if err != nil || len(as) != 1 || as[0].Nombre != "Ana de Uno" || as[0].Telefono != "51900000001" || as[0].WhatsappEnviado ||
				string(as[0].Resumen) != `{"evento":"boda"}` || as[0].AtendidaEn != nil {
				t.Fatalf("pendientes: %+v %v", as, err)
			}
			if err := a.st.MarcarAlertaEnviada(ctx, al.ID); err != nil {
				t.Fatal(err)
			}
			if err := a.st.AtenderAlerta(ctx, al.ID); err != nil {
				t.Fatal(err)
			}
			if as, _ := a.st.ListarAlertas(ctx, true); len(as) != 0 {
				t.Fatal("atendida: ya no es pendiente")
			}
			as, _ = a.st.ListarAlertas(ctx, false)
			if len(as) != 1 || as[0].AtendidaPor != "Rosa" || as[0].AtendidaEn == nil || !as[0].WhatsappEnviado {
				t.Fatalf("todas: %+v", as[0])
			}
		})
	}
}
