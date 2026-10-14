package store

import (
	"context"
	"fmt"
	"testing"
	"time"
)

// La derivación a asesora queda en el CRM: actividad de la clienta, UNA tarea pendiente (no dos por la misma clienta)
// asignada a su asesora, y la conversación en la cola de derivadas mientras el bot siga en pausa. Por empresa.
func TestDerivacionEnElCRM(t *testing.T) {
	for nombre, base := range backends(t) {
		t.Run(nombre, func(t *testing.T) {
			ctx := context.Background()
			sfx := fmt.Sprintf("%d", time.Now().UnixNano()%1_000_000)
			a := nuevaEmpresa(t, base, "deriva-"+sfx, "Alfa", 5)
			b := nuevaEmpresa(t, base, "derivb-"+sfx, "Beta", 5)
			lucia, err := a.st.CrearMiembro(ctx, "lucia", "clave-larga-1", "Lucía", RolAsesora)
			if err != nil {
				t.Fatal(err)
			}
			if err := a.st.AsignarClienta(ConAutor(ctx, "admin"), a.cust.ID, lucia.ID); err != nil {
				t.Fatal(err)
			}
			if err := a.st.SetConversationState(ctx, a.conv.ID, "asesora", "{}"); err != nil {
				t.Fatal(err)
			}
			if err := a.st.SetBotPaused(ctx, a.conv.ID, true); err != nil {
				t.Fatal(err)
			}
			id, err := a.st.Derivar(ctx, a.conv, "")
			if err != nil || id == 0 {
				t.Fatalf("derivar: id=%d err=%v", id, err)
			}
			if otra, err := a.st.Derivar(ctx, a.conv, "reclamo"); err != nil || otra != 0 {
				t.Fatalf("segunda derivación con tarea pendiente: id=%d err=%v (no debe abrir otra)", otra, err)
			}
			tareas, err := a.st.ListTareas(ctx, FiltroTareas{CustomerID: a.cust.ID})
			if err != nil || len(tareas) != 1 {
				t.Fatalf("tareas de la clienta: %d %v", len(tareas), err)
			}
			if tareas[0].Titulo != TituloDerivacion || tareas[0].ResponsableID != lucia.ID || tareas[0].CreadaPor != "bot" {
				t.Errorf("tarea: %+v", tareas[0])
			}
			ev, err := a.st.Actividad(ctx, a.cust.ID, 50)
			if err != nil {
				t.Fatal(err)
			}
			var vistos int
			for _, e := range ev {
				if e.Tipo == "derivacion" && e.RefID == a.conv.ID {
					vistos++
				}
			}
			if vistos != 2 {
				t.Errorf("la línea de tiempo debe traer las dos derivaciones: %d", vistos)
			}
			cola, err := a.st.ListDerivadas(ctx)
			if err != nil || len(cola) != 1 || cola[0].ConversationID != a.conv.ID || cola[0].Asesora != "Lucía" {
				t.Fatalf("cola de derivadas: %+v %v", cola, err)
			}
			if cola, _ := b.st.ListDerivadas(ctx); len(cola) != 0 {
				t.Errorf("la otra empresa no ve la cola: %+v", cola)
			}
			// Al reactivar el bot sale de la cola; la tarea hecha permite abrir otra en la próxima derivación.
			if err := a.st.SetBotPaused(ctx, a.conv.ID, false); err != nil {
				t.Fatal(err)
			}
			if cola, _ := a.st.ListDerivadas(ctx); len(cola) != 0 {
				t.Errorf("con el bot activo no hay cola: %+v", cola)
			}
			hecha := true
			if err := a.st.UpdateTarea(ConAutor(ctx, "admin"), tareas[0].ID, CambioTarea{Hecha: &hecha}); err != nil {
				t.Fatal(err)
			}
			if id, err := a.st.Derivar(ctx, a.conv, ""); err != nil || id == 0 {
				t.Errorf("tras cerrar la tarea, otra derivación abre una nueva: id=%d err=%v", id, err)
			}
		})
	}
}

func TestColoresGrupos(t *testing.T) {
	g := ColoresGrupos("")
	if g["azul noche"] != "sobrio" || g["rojo"] != "vibrante" || g["clara"] != "" {
		t.Fatalf("propuesta por defecto: %v %v %v", g["azul noche"], g["rojo"], g["clara"])
	}
	g = ColoresGrupos(`{"Rojo": "sobrio", "clara": "vibrante", "verde": "otra"}`)
	if g["rojo"] != "sobrio" || g["clara"] != "vibrante" || g["verde"] != "vibrante" {
		t.Errorf("la tienda pisa la propuesta y un grupo inválido se ignora: %v %v %v", g["rojo"], g["clara"], g["verde"])
	}
	for in, want := range map[string]string{" Azul  Noche ": "azul noche", "Marrón.": "marron", "Beige oscuro": "beige oscuro", "": ""} {
		if got := NormalizaColor(in); got != want {
			t.Errorf("NormalizaColor(%q) = %q, quiero %q", in, got, want)
		}
	}
}
