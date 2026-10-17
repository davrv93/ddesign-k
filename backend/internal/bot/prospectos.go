package bot

// Prospectos a futuro (octubre de 2026, pedido de Luis). Con el evento lejos, el agente pregunta «¿lo quieres ya o
// coordinamos en <mes>?»; si ella elige más adelante, la memoria vuelve con sabemos.coordinar_en (la fecha del aviso) y
// sabemos.coordinar_marca (cuándo lo decidió). Aquí: la clienta pasa a «Más adelante» y queda una tarea para escribirle
// ese día. Si vuelve a escribir antes, la etapa la decide otra vez el bot (store.SalirDeMasAdelante).

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

func (b *Bot) decodeCtx(raw string, cc *convContext) error { return json.Unmarshal([]byte(raw), cc) }

// registrarProspecto: si la memoria trae una decisión de coordinar más adelante nueva (otra marca), etapa + tarea.
func (b *Bot) registrarProspecto(ctx context.Context, conv *store.Conversation, antes, despues json.RawMessage) {
	a, d := leerFichaMem(antes), leerFichaMem(despues)
	marca, fecha := d.Sab("coordinar_marca"), d.Sab("coordinar_en")
	if d.Sab("coordinar") != "despues" || fecha == "" || marca == "" || marca == a.Sab("coordinar_marca") {
		return
	}
	dia, err := time.ParseInLocation("2006-01-02", fecha, store.Lima)
	if err != nil {
		return
	}
	if err := b.store.EtapaPorSeguimiento(ctx, conv.ID, "mas_adelante"); err != nil {
		log.Printf("bot: prospecto %d: %v", conv.ID, err)
	}
	_ = b.store.DetenerSeguimiento(ctx, conv.ID) // ya quedó en escribirle más adelante: nada de seguimiento ahora
	custID, err := b.store.CustomerDeConversacion(ctx, conv.ID)
	if err != nil {
		return
	}
	vence := dia.Add(10 * time.Hour) // 10:00 de Lima
	evento := d.Sab("ocasion")
	if evento == "" {
		evento = "su evento"
	}
	cuando := d.FechaLegible()
	titulo := fmt.Sprintf("Escribir a %s: %s", firstNonEmpty(primerNombre(b.customerName(conv)), "la clienta"), evento)
	if cuando != "" {
		titulo += " el " + cuando
	}
	if p := d.Producto; p != "" {
		if prod, err := b.store.GetProductByCode(ctx, p); err == nil {
			titulo += " · le gustó el " + prod.Name
		}
	}
	t := &store.Tarea{CustomerID: custID, Titulo: titulo, Vence: &vence, CreadaPor: "bot"}
	if err := b.store.AddTarea(ctx, t); err != nil {
		log.Printf("bot: tarea de prospecto %d: %v", conv.ID, err)
		return
	}
	b.hito(ctx, conv, fmt.Sprintf("📅 Prefiere coordinar más adelante: se le escribe el %d de %s", dia.Day(), mesesLargos[dia.Month()-1]))
	b.Notify("tareas")
	b.Notify("customers")
}

func (b *Bot) ajusteSi(ctx context.Context, clave string) bool {
	return b.store.Setting(ctx, clave, "false") == "true"
}

// prospectosCfg: la configuración de prospectos para el agente, o nil si está apagado.
func (b *Bot) prospectosCfg(ctx context.Context) *agente.ProspectosConfig {
	if !b.ajusteSi(ctx, store.SettingProspectos) {
		return nil
	}
	num := func(clave string, def int) int {
		var n int
		if _, err := fmt.Sscan(b.store.Setting(ctx, clave, fmt.Sprint(def)), &n); err != nil || n < 1 || n > 365 {
			return def
		}
		return n
	}
	return &agente.ProspectosConfig{DesdeDias: num(store.SettingProspectoDesde, 45), AvisoDias: num(store.SettingProspectoAviso, 30)}
}
