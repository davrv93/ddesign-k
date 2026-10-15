package kommo

import (
	"testing"
)

// Embudo de promo (15-10-2026): el lead de una conversación en la que el robot respondió con una promo con embudo espejo
// va a ese embudo de Kommo (con las etapas del bot) y lleva la etiqueta «promo: …»; sin embudo de promo, al general.
func TestLeadDePromoVaASuEmbudo(t *testing.T) {
	s, k, _ := preparar(t, false)
	aplicar(t, s, Evento{Canal: "whatsapp", Clave: "wa:1", ConversationID: 1, Nombre: "Ana", Telefono: "51911111111",
		Etapa: "prospeccion", Memoria: mem(`{}`)})
	general := k.EmbudoPorNombre(embudo)
	if general == nil || len(k.LeadsDe(general.ID)) != 1 {
		t.Fatal("el lead sin promo debía ir al embudo general")
	}
	aplicar(t, s, Evento{Canal: "whatsapp", Clave: "wa:2", ConversationID: 2, Nombre: "Lucía", Telefono: "51922222222",
		Etapa: "seguimiento", Memoria: mem(`{}`), Promo: "Pelotera", EmbudoPromo: "Promo · Pelotera"})
	pe := k.EmbudoPorNombre("Promo · Pelotera")
	if pe == nil {
		t.Fatal("no se creó el embudo de la promo")
	}
	ls := k.LeadsDe(pe.ID)
	if len(ls) != 1 || ls[0].StatusID != k.EstadoPorNombre(pe, "Seguimiento") || !contieneTag(ls[0].Tags, "promo: Pelotera") {
		t.Fatalf("lead de la promo: %+v", ls)
	}
	// La clienta del embudo general que luego recibe la promo pasa a ese embudo.
	aplicar(t, s, Evento{Canal: "whatsapp", Clave: "wa:1", ConversationID: 1, Nombre: "Ana", Telefono: "51911111111",
		Etapa: "seguimiento", Memoria: mem(`{}`), Promo: "Pelotera", EmbudoPromo: "Promo · Pelotera"})
	if len(k.LeadsDe(pe.ID)) != 2 || len(k.LeadsDe(general.ID)) != 0 {
		t.Fatalf("el lead debía moverse al embudo de la promo: promo %d, general %d", len(k.LeadsDe(pe.ID)), len(k.LeadsDe(general.ID)))
	}
}
