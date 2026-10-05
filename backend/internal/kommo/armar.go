package kommo

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/config"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// Armar crea el sincronizador con la configuración del backend: nombre y precio de las prendas del catálogo (SQLite)
// y costos de envío del agente (agent/seed/venta.json, vía /health), para no copiar esos datos aquí.
func Armar(cfg *config.Config, st *store.Store) *Sincronizador {
	return NuevoSincronizador(Nuevo(cfg.KommoSubdomain, cfg.KommoToken), st, Opciones{
		Embudo:        cfg.KommoPipeline,
		Transcripcion: cfg.KommoTranscript,
		PanelURL:      cfg.PublicURL,
		Moneda:        cfg.Currency,
		Producto:      ProductoDe(st),
		Envios:        EnviosDelAgente(cfg.AgentURL),
	})
}

// ProductoDe busca la prenda en el catálogo del backend.
func ProductoDe(st *store.Store) func(context.Context, string) (string, float64, bool) {
	return func(ctx context.Context, codigo string) (string, float64, bool) {
		p, err := st.GetProductByCode(ctx, strings.ToUpper(codigo))
		if err != nil {
			return "", 0, false
		}
		return p.Name, p.Price, true
	}
}

// EnviosDelAgente lee los costos de envío del /health del agente y los guarda 10 minutos. Sin agente: sin costo.
func EnviosDelAgente(agentURL string) func(context.Context) map[string]float64 {
	agentURL = strings.TrimRight(agentURL, "/")
	var mu sync.Mutex
	var cache map[string]float64
	var hasta time.Time
	cli := &http.Client{Timeout: 5 * time.Second}
	return func(ctx context.Context) map[string]float64 {
		if agentURL == "" {
			return nil
		}
		mu.Lock()
		defer mu.Unlock()
		if time.Now().Before(hasta) {
			return cache
		}
		hasta = time.Now().Add(10 * time.Minute)
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, agentURL+"/health", nil)
		if err != nil {
			return cache
		}
		res, err := cli.Do(req)
		if err != nil {
			hasta = time.Now().Add(time.Minute)
			return cache
		}
		defer res.Body.Close()
		var h struct {
			Envios map[string]float64 `json:"envios"`
		}
		if json.NewDecoder(res.Body).Decode(&h) == nil && len(h.Envios) > 0 {
			cache = h.Envios
		}
		return cache
	}
}
