package bot

import (
	"context"
	"testing"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
	"github.com/davrv93/ddesign-k/backend/internal/juicio"
)

// El Juicio debe decidir con la intención COMERCIAL. Hasta el 07-10-2026 recibía la de la tienda
// (saludo, catalogo…), que sus reglas no nombran, y decidía «enviar» el 100 % de las veces.
func TestJuicioUsaLaIntencionComercial(t *testing.T) {
	b, st, _ := setup(t, `{"intent":"otro","reply":"x"}`, false)
	ctx := context.Background()
	_, conv, err := st.UpsertCustomer(ctx, "51999000111@s.whatsapp.net", "51999000111", "Ana")
	if err != nil {
		t.Fatal(err)
	}
	casos := []struct {
		nombre string
		rep    agente.Reply
		quiero juicio.Accion
		intent string
	}{
		{"regateo: no se deriva, se contesta que no hay descuentos",
			agente.Reply{Intencion: "otro", Confianza: 0.9, Accion: "responder", Comercial: &agente.Comercial{Intent: "objecion_precio", Confianza: 0.95}},
			juicio.Enviar, "objecion_precio"},
		{"compra dudosa: se propone para revisión",
			agente.Reply{Intencion: "otro", Confianza: 0.9, Accion: "responder", Comercial: &agente.Comercial{Intent: "intencion_compra", Confianza: 0.5}},
			juicio.Sugerir, "intencion_compra"},
		{"pide una persona según el clasificador de la tienda",
			agente.Reply{Intencion: "asesora", Confianza: 0.97, Accion: "responder", Comercial: &agente.Comercial{Intent: "otro", Confianza: 0.8}},
			juicio.Derivar, "otro"},
		{"sin intención comercial: se usa la de la tienda",
			agente.Reply{Intencion: "saludo", Confianza: 0.99, Accion: "responder"},
			juicio.Enviar, "saludo"},
	}
	for _, c := range casos {
		rep := c.rep
		s := b.juzgar(ctx, conv, &convContext{Etapa: "seguimiento"}, &rep)
		if s.Accion != c.quiero {
			t.Errorf("%s: decisión %q (%s), quería %q", c.nombre, s.Accion, s.Motivo, c.quiero)
		}
		ds, err := st.DecisionsFor(ctx, c.intent, 1)
		if err != nil || len(ds) == 0 || ds[0].Decision != string(c.quiero) {
			t.Errorf("%s: la memoria de criterio no guardó la decisión con la intención %q: %v %+v", c.nombre, c.intent, err, ds)
		}
	}
}
