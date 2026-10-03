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
