package bot

import (
	"context"

	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// coloresGrupos: el mapa {color: sobrio|vibrante} de la tienda (Ajustes → Colores) que viaja al agente en cada turno.
func (b *Bot) coloresGrupos(ctx context.Context) map[string]string {
	return store.ColoresGrupos(b.store.Setting(ctx, store.SettingColores, ""))
}
