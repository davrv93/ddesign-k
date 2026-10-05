package bot

import (
	"context"
	"strings"

	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// registrarDPO guarda el par para alineación cuando una persona responde: el último mensaje de la
// clienta, lo que el bot había propuesto antes y lo que escribió la persona. Alimenta un futuro
// ajuste (DPO/few-shot) con el criterio real de la tienda. Silencioso: si no hay propuesta del bot,
// no guarda nada.
func (b *Bot) registrarDPO(ctx context.Context, conv *store.Conversation, respuestaHumana string) {
	respuestaHumana = strings.TrimSpace(respuestaHumana)
	if conv == nil || respuestaHumana == "" {
		return
	}
	msgs, err := b.store.ListMessages(ctx, conv.ID, 10)
	if err != nil {
		return
	}
	var botTxt, clienteTxt string
	for i := len(msgs) - 1; i >= 0; i-- {
		m := msgs[i]
		if botTxt == "" && m.Direction == "out" && m.Author == "bot" && strings.TrimSpace(m.Body) != "" {
			botTxt = m.Body
		}
		if clienteTxt == "" && m.Direction == "in" && strings.TrimSpace(m.Body) != "" {
			clienteTxt = m.Body
		}
		if botTxt != "" && clienteTxt != "" {
			break
		}
	}
	_ = b.store.SaveParDPO(ctx, conv.ID, &store.ParDPO{Contexto: clienteTxt, RespuestaBot: botTxt, RespuestaHumana: respuestaHumana})
}
