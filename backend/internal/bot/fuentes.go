package bot

import (
	"context"
	"log"
	"sync"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// Fuentes de información del robot (15-10-2026, docs/FUENTES_CONTRATO.md §3): en cada turno el agente recibe las
// promos, preguntas frecuentes, guion, políticas y material VIGENTES de la tienda, y devuelve en qué fuentes se apoyó.
// El bot guarda ese uso por conversación: de ahí salen el embudo de cada promo y sus cifras.

// cacheConocimiento evita leer las fuentes en cada mensaje de una ráfaga. 20 s: una promo activada se nota enseguida.
type cacheConocimiento struct {
	mu  sync.Mutex
	c   *store.Conocimiento
	exp time.Time
}

const ttlConocimiento = 20 * time.Second

// conocimiento: lo que el robot sabe hoy, o nil si la tienda no cargó nada (el agente sigue con su seed).
func (b *Bot) conocimiento(ctx context.Context) *store.Conocimiento {
	b.conoc.mu.Lock()
	defer b.conoc.mu.Unlock()
	if time.Now().Before(b.conoc.exp) {
		return b.conoc.c
	}
	empresa := b.cfg.WhatsAppTenant
	if empresa == "" {
		empresa = "tienda"
	}
	c, err := b.store.ConocimientoVigente(ctx, empresa, time.Now())
	if err != nil {
		log.Printf("bot: fuentes: %v", err)
		return b.conoc.c // la última buena
	}
	if c.Vacio() {
		c = nil
	}
	b.conoc.c, b.conoc.exp = c, time.Now().Add(ttlConocimiento)
	return c
}

// conocimientoReq: el valor para agente.Request (nil = no se manda el campo).
func (b *Bot) conocimientoReq(ctx context.Context) any {
	if c := b.conocimiento(ctx); c != nil {
		return c
	}
	return nil
}

// anotarFuentes guarda las fuentes con que respondió el agente en este turno.
func (b *Bot) anotarFuentes(ctx context.Context, conv *store.Conversation, r *agente.Reply) {
	if r == nil || len(r.FuentesUsadas) == 0 {
		return
	}
	if err := b.store.AnotarUsoFuentes(ctx, conv.ID, r.FuentesUsadas); err != nil {
		log.Printf("bot: anotar fuentes: %v", err)
	}
}
