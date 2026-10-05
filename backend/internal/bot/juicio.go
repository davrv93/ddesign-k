package bot

import (
	"context"
	"encoding/json"
	"log"
	"os"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
	"github.com/davrv93/ddesign-k/backend/internal/juicio"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// modoJuicio controla la Capa de Juicio (internal/juicio):
//   - "" u "off": no hace nada.
//   - "sombra":   se calcula, se registra [JUICIO] y en la memoria de criterio, pero no cambia lo que se envía.
//   - "activo":   además se aplica (derivar y callar; «sugerir» aún no tiene panel de aprobación).
//
// Por defecto va en «sombra»: registra lo que decidiría sin cambiar el comportamiento del bot. Se pone
// "activo" cuando los datos del registro justifiquen que acierta.
var modoJuicio = firstNonEmpty(os.Getenv("JUICIO_MODO"), "sombra")

// juicioObserva: se calcula y se registra la decisión (sombra o activo).
func juicioObserva() bool { return modoJuicio == "sombra" || modoJuicio == "activo" }

// juicioLog arma el registro de auditoría de una decisión de la Capa de Juicio.
func juicioLog(convID int64, etapa, intent string, confianza, sentimiento, urgencia, conversion float64, propuesta string, s juicio.Salida) string {
	out, err := json.Marshal(map[string]any{
		"conversation_id":  convID,
		"etapa":            etapa,
		"intent":           intent,
		"confianza":        confianza,
		"sentimiento":      sentimiento,
		"urgencia":         urgencia,
		"conversion":       conversion,
		"accion_propuesta": propuesta,
		"decision":         string(s.Accion),
		"riesgo":           s.Riesgo.String(),
		"motivo":           s.Motivo,
	})
	if err != nil {
		return ""
	}
	return string(out)
}

// juzgar calcula la decisión de la Capa de Juicio para la propuesta del agente y la registra.
func (b *Bot) juzgar(ctx context.Context, conv *store.Conversation, cc *convContext, r *agente.Reply) juicio.Salida {
	etapa := firstNonEmpty(r.Etapa, cc.Etapa)
	e := juicio.Entrada{
		Etapa:       etapa,
		Intent:      r.Intencion,
		Confianza:   r.Confianza,
		Accion:      r.Accion,
		Sentimiento: r.Sentimiento, // F2: ánimo y urgencia por reglas (agente/app/animo.py)
		Urgencia:    r.Urgencia,
	}
	e.Conversion = juicio.EstimarConversion(e) // F4: probabilidad de cierre (heurística)
	s := juicio.Decidir(e)
	if juicioObserva() {
		if reg := juicioLog(conv.ID, etapa, r.Intencion, r.Confianza, r.Sentimiento, r.Urgencia, e.Conversion, r.Accion, s); reg != "" {
			log.Printf("[JUICIO] %s", reg)
		}
		// Memoria de criterio: qué se decidió, con qué caso y por qué (autor «bot»).
		_ = b.store.SaveDecision(ctx, &store.Decision{ConversationID: conv.ID, Etapa: etapa, Intent: r.Intencion,
			Caso: r.Intencion, Decision: string(s.Accion), Razon: s.Motivo, Autor: "bot"})
	}
	return s
}

// gateJuicio aplica la decisión cuando la Capa de Juicio está en modo «activo». Devuelve true si la
// propuesta ya quedó resuelta (derivada o silenciada) y el bot no debe enviarla por su cuenta.
// En sombra siempre devuelve false: solo registra.
func (b *Bot) gateJuicio(ctx context.Context, conv *store.Conversation, cc *convContext, r *agente.Reply) bool {
	s := b.juzgar(ctx, conv, cc, r)
	if modoJuicio != "activo" {
		return false
	}
	switch s.Accion {
	case juicio.Derivar:
		b.handoff(ctx, conv)
		return true
	case juicio.Callar:
		return true
	}
	// «sugerir» todavía no tiene panel de aprobación: en fase 1 sale como hoy.
	return false
}
