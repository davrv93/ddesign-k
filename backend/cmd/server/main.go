package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
	"github.com/davrv93/ddesign-k/backend/internal/ai"
	"github.com/davrv93/ddesign-k/backend/internal/api"
	"github.com/davrv93/ddesign-k/backend/internal/auth"
	"github.com/davrv93/ddesign-k/backend/internal/bot"
	"github.com/davrv93/ddesign-k/backend/internal/config"
	"github.com/davrv93/ddesign-k/backend/internal/evolution"
	"github.com/davrv93/ddesign-k/backend/internal/seed"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

func main() {
	cfg := config.Load()
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	st, err := store.Open(cfg.DataDir)
	if err != nil {
		log.Fatalf("sqlite: %v", err)
	}
	defer st.DB.Close()
	if cfg.SeedCatalog {
		if err := seed.Run(ctx, st, cfg.DataDir); err != nil {
			log.Printf("seed: %v", err)
		}
		if err := seed.Warehouses(ctx, st); err != nil {
			log.Printf("seed sucursales: %v", err)
		}
	}
	go purgeReservations(ctx, st)

	evo := evolution.New(cfg.EvolutionURL, cfg.EvolutionGlobalKey, cfg.EvolutionInstance, cfg.EvolutionInstanceToken)
	aic := ai.New(cfg.GeminiAPIKey, cfg.GeminiModel, cfg.GeminiFallbackModel, time.Duration(cfg.GeminiTimeoutSec)*time.Second)
	hub := api.NewHub()
	b := bot.New(cfg, st, evo, aic)
	b.Notify = hub.Publish
	b.Agent = agente.New(cfg.AgentURL, time.Duration(cfg.AgentTimeoutSec)*time.Second)
	srv := api.New(cfg, st, evo, aic, b, auth.New(cfg.JWTSecret, cfg.AdminUser, cfg.AdminPassword), hub)

	go srv.Bootstrap(ctx)
	go resumePausedBots(ctx, st, hub)
	go followups(ctx, st, b, hub)

	httpSrv := &http.Server{
		Addr:              ":" + cfg.Port,
		Handler:           srv.Routes(),
		ReadHeaderTimeout: 10 * time.Second,
		// Sin WriteTimeout: /api/events es un stream (SSE) de larga duración.
	}
	go func() {
		<-ctx.Done()
		sctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(sctx)
	}()
	log.Printf("%s CRM escuchando en :%s (IA: %s → %s)", cfg.BusinessName, cfg.Port, cfg.GeminiModel, cfg.GeminiFallbackModel)
	if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
	// Entrega lo que quedó en cola antes de salir.
	b.Drain(15 * time.Second)
}

// followups manda un único recordatorio a las conversaciones que quedaron en silencio (stopping
// agent). Está apagado por defecto (`seguimiento_habilitado`): sin activarlo no se escribe a nadie.
// Solo actúa en horario de atención de Lima y la Capa de Juicio pone el tope de recordatorios.
func followups(ctx context.Context, st *store.Store, b *bot.Bot, hub *api.Hub) {
	lima := time.FixedZone("America/Lima", -5*3600) // Perú no tiene horario de verano
	t := time.NewTicker(time.Hour)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			if st.Setting(ctx, "seguimiento_habilitado", "false") != "true" {
				continue
			}
			if h := time.Now().In(lima).Hour(); h < 8 || h >= 21 {
				continue
			}
			hours, _ := strconv.Atoi(st.Setting(ctx, "seguimiento_horas", "24"))
			if hours <= 0 {
				hours = 24
			}
			convs, err := st.ConversationsToFollowUp(ctx, time.Duration(hours)*time.Hour, 2)
			if err != nil {
				log.Printf("seguimiento: %v", err)
				continue
			}
			for _, cv := range convs {
				b.Followup(ctx, cv)
			}
			if len(convs) > 0 {
				hub.Publish("conversations")
			}
		}
	}
}

// resumePausedBots devuelve el chat al bot cuando la asesora lo dejó pausado demasiado tiempo.
func resumePausedBots(ctx context.Context, st *store.Store, hub *api.Hub) {
	t := time.NewTicker(10 * time.Minute)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			hours, _ := strconv.Atoi(st.Setting(ctx, "bot_resume_hours", "12"))
			if hours <= 0 {
				continue
			}
			if n, err := st.ResumeStalePaused(ctx, hours); err == nil && n > 0 {
				log.Printf("bot: reactivado en %d conversaciones", n)
				hub.Publish("conversations")
			}
		}
	}
}

// purgeReservations limpia las reservas de stock vencidas cada minuto.
func purgeReservations(ctx context.Context, st *store.Store) {
	t := time.NewTicker(time.Minute)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			if _, err := st.PurgeExpiredReservations(ctx); err != nil {
				log.Printf("reservas: %v", err)
			}
		}
	}
}
