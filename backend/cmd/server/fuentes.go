package main

import (
	"context"
	"log"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/api"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// cronFuentes (15-10-2026, plan de fuentes §2.5): cada minuto pasa a «vencido» las promos y fuentes cuya fecha «hasta»
// ya pasó (en Lima); de madrugada (03:00 Lima) arma las propuestas de preguntas frecuentes del robot con las
// conversaciones del día. Nada de esto escribe a clientas: solo cambia lo que el robot sabe y lo que muestra el panel.
// Lo que el bot lee ya filtra por fecha, así que un minuto de retraso no hace que ofrezca una promo vencida.
func cronFuentes(ctx context.Context, st *store.Store, hub *api.Hub, multi bool) {
	t := time.NewTicker(time.Minute)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case ahora := <-t.C:
			porEmpresa(ctx, st, multi, func(ts *store.Store) {
				if n, err := ts.VencerFuentes(ctx, ahora); err != nil {
					log.Printf("fuentes: vencer (empresa %d): %v", ts.TenantID(), err)
				} else if n > 0 {
					log.Printf("fuentes: %d vencidas (empresa %d)", n, ts.TenantID())
					hub.PublishTo(ts.TenantID(), "fuentes")
				}
				proponerDeMadrugada(ctx, ts, hub, ahora)
			})
		}
	}
}

// proponerDeMadrugada corre una vez al día, desde las 03:00 de Lima, sobre los mensajes desde la pasada anterior (o
// las últimas 24 h la primera vez).
func proponerDeMadrugada(ctx context.Context, ts *store.Store, hub *api.Hub, ahora time.Time) {
	if ahora.In(store.Lima).Hour() < 3 {
		return
	}
	hoy := store.HoyLima(ahora)
	ultima, _ := time.Parse(time.RFC3339, ts.Setting(ctx, store.SettingPropuestasUltima, ""))
	if !ultima.IsZero() && store.HoyLima(ultima) == hoy {
		return
	}
	desde := ultima
	if desde.IsZero() || ahora.Sub(desde) > 72*time.Hour {
		desde = ahora.Add(-24 * time.Hour)
	}
	n, err := ts.ProponerPreguntas(ctx, desde)
	if err != nil {
		log.Printf("fuentes: propuestas (empresa %d): %v", ts.TenantID(), err)
		return
	}
	_ = ts.SetSetting(ctx, store.SettingPropuestasUltima, ahora.UTC().Format(time.RFC3339))
	if n > 0 {
		log.Printf("fuentes: %d preguntas frecuentes propuestas (empresa %d)", n, ts.TenantID())
		hub.PublishTo(ts.TenantID(), "fuentes")
	}
}
