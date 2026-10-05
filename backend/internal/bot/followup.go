package bot

import (
	"context"
	"encoding/json"
	"log"

	"github.com/davrv93/ddesign-k/backend/internal/juicio"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// recordatorioPorEstado: el texto del único (o segundo) recordatorio a una conversación en silencio.
// Sin la presión de un cierre: es un «sigo aquí» con el paso pendiente a mano.
var recordatorioPorEstado = map[string]string{
	stSize:    "¿Sigues por ahí? 😊 Cuando quieras dime tu talla y te la separo.",
	stConfirm: "Cuando estés lista, responde *SI* y confirmo tu pedido 😊",
	stPhoto:   "Cuando puedas, mándame la foto del modelo que viste y lo busco 📸",
}

const recordatorioGenerico = "¿Seguimos? Si te quedó alguna duda, dime y te ayudo con gusto 😊"

// Followup manda un recordatorio a una conversación en silencio. La Capa de Juicio decide si se envía
// (p. ej. dos recordatorios ya sin respuesta = callar); el job de fondo ya filtró el silencio y el tope.
func (b *Bot) Followup(ctx context.Context, conv *store.Conversation) {
	if conv == nil || conv.Customer == nil {
		return
	}
	var cc convContext
	_ = json.Unmarshal([]byte(conv.Context), &cc)
	s := juicio.Decidir(juicio.Entrada{Etapa: cc.Etapa, Confianza: 1, IntentosSinRespuesta: b.store.FollowupCount(ctx, conv.ID)})
	if juicioObserva() {
		if reg := juicioLog(conv.ID, cc.Etapa, "", 1, 0, 0, 0, "seguimiento", s); reg != "" {
			log.Printf("[JUICIO] %s", reg)
		}
	}
	if s.Accion != juicio.Enviar {
		return
	}
	texto := recordatorioPorEstado[conv.State]
	if texto == "" {
		texto = recordatorioGenerico
	}
	if err := b.queueMessage(ctx, conv, &store.Message{Kind: "text", Body: texto, Author: "bot"}, outJob{text: texto}); err != nil {
		log.Printf("bot: recordatorio %d: %v", conv.ID, err)
		return
	}
	_ = b.store.MarkFollowUp(ctx, conv.ID)
}
