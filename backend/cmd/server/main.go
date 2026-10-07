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
	"github.com/davrv93/ddesign-k/backend/internal/kommo"
	"github.com/davrv93/ddesign-k/backend/internal/seed"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

func main() {
	cfg := config.Load()
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	st, err := openStore(cfg)
	if err != nil {
		log.Fatalf("base de datos: %v", err)
	}
	defer st.DB.Close()
	// El bot (WhatsApp, Kommo, seguimiento) es de una sola empresa. Sin multiempresa es la tienda de siempre; con
	// multiempresa, la de WHATSAPP_TENANT, o ninguna (tid 0: no lee ni escribe nada).
	botSt := st
	var botTenant int64
	if cfg.MultiTenant {
		botSt = st.ForTenant(0)
		if cfg.WhatsAppTenant != "" {
			if t, err := st.TenantBySlug(ctx, cfg.WhatsAppTenant); err == nil {
				botSt, botTenant = st.ForTenant(t.ID), t.ID
			} else {
				log.Printf("whatsapp: la empresa %q no existe; el bot queda sin empresa", cfg.WhatsAppTenant)
			}
		}
	}
	if cfg.SeedCatalog && !cfg.MultiTenant {
		if err := seed.Run(ctx, st, cfg.DataDir); err != nil {
			log.Printf("seed: %v", err)
		}
		if err := seed.Warehouses(ctx, st); err != nil {
			log.Printf("seed sucursales: %v", err)
		}
	}
	go purgeReservations(ctx, st, cfg.MultiTenant)

	evo := evolution.New(cfg.EvolutionURL, cfg.EvolutionGlobalKey, cfg.EvolutionInstance, cfg.EvolutionInstanceToken)
	aic := ai.New(cfg.GeminiAPIKey, cfg.GeminiModel, cfg.GeminiFallbackModel, time.Duration(cfg.GeminiTimeoutSec)*time.Second)
	hub := api.NewHub()
	b := bot.New(cfg, botSt, evo, aic)
	b.Notify = hub.Publish
	if cfg.MultiTenant {
		b.Notify = func(topic string) { hub.PublishTo(botTenant, topic) }
	}
	b.Agent = agente.New(cfg.AgentURL, time.Duration(cfg.AgentTimeoutSec)*time.Second)
	srv := api.New(cfg, st, evo, aic, b, auth.New(cfg.JWTSecret, cfg.AdminUser, cfg.AdminPassword), hub)
	srv.BotTenant = botTenant
	// Kommo CRM: apagado por defecto. Va en segundo plano: si Kommo cae, el bot contesta igual.
	var crm *kommo.Sincronizador
	switch {
	case cfg.KommoListo():
		crm = kommo.Armar(cfg, botSt)
		crm.Iniciar(ctx)
		b.CRM, srv.Kommo = crm, crm
		log.Printf("kommo: sincronización activa con %s (embudo %q, transcripción %v)", crm.Cliente().BaseURL, cfg.KommoPipeline, cfg.KommoTranscript)
	case cfg.KommoEnabled:
		log.Printf("kommo: KOMMO_ENABLED=1 pero falta KOMMO_SUBDOMAIN o KOMMO_TOKEN; queda apagado")
	}

	if cfg.EvolutionURL != "" && (!cfg.MultiTenant || botTenant > 0) {
		go srv.Bootstrap(ctx)
	} else {
		log.Printf("whatsapp: apagado en este backend (EVOLUTION_URL=off o sin WHATSAPP_TENANT)")
	}
	go resumePausedBots(ctx, st, hub, cfg.MultiTenant)
	if !cfg.MultiTenant || botTenant > 0 {
		go followups(ctx, botSt, b, hub)
	}

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
	if cfg.MultiTenant {
		log.Printf("JMD Ventas (multiempresa, %s) escuchando en :%s", st.Dialect, cfg.Port)
	} else {
		log.Printf("%s CRM escuchando en :%s (IA: %s → %s)", cfg.BusinessName, cfg.Port, cfg.GeminiModel, cfg.GeminiFallbackModel)
	}
	if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
	// Entrega lo que quedó en cola antes de salir.
	b.Drain(15 * time.Second)
	if crm != nil {
		crm.Esperar(10 * time.Second)
	}
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

// openStore: MariaDB (DB_DRIVER=mysql) o la SQLite de DATA_DIR.
func openStore(cfg *config.Config) (*store.Store, error) {
	if cfg.DBDriver == "mysql" {
		return store.OpenMySQL(cfg.DBDSN)
	}
	return store.Open(cfg.DataDir)
}

// porEmpresa recorre las empresas activas con un Store ligado a cada una (sin multiempresa, solo la tienda).
func porEmpresa(ctx context.Context, st *store.Store, multi bool, fn func(ts *store.Store)) {
	if !multi {
		fn(st)
		return
	}
	ts, err := st.Tenants(ctx)
	if err != nil {
		log.Printf("empresas: %v", err)
		return
	}
	for _, t := range ts {
		fn(st.ForTenant(t.ID))
	}
}

// resumePausedBots devuelve el chat al bot cuando la asesora lo dejó pausado demasiado tiempo.
func resumePausedBots(ctx context.Context, st *store.Store, hub *api.Hub, multi bool) {
	t := time.NewTicker(10 * time.Minute)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			porEmpresa(ctx, st, multi, func(ts *store.Store) {
				hours, _ := strconv.Atoi(ts.Setting(ctx, "bot_resume_hours", "12"))
				if hours <= 0 {
					return
				}
				if n, err := ts.ResumeStalePaused(ctx, hours); err == nil && n > 0 {
					log.Printf("bot: reactivado en %d conversaciones (empresa %d)", n, ts.TenantID())
					hub.PublishTo(ts.TenantID(), "conversations")
				}
			})
		}
	}
}

// purgeReservations limpia las reservas de stock vencidas cada minuto.
func purgeReservations(ctx context.Context, st *store.Store, multi bool) {
	t := time.NewTicker(time.Minute)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			porEmpresa(ctx, st, multi, func(ts *store.Store) {
				if _, err := ts.PurgeExpiredReservations(ctx); err != nil {
					log.Printf("reservas: %v", err)
				}
			})
		}
	}
}
