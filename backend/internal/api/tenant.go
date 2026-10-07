package api

import (
	"context"
	"net/http"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// Multiempresa (JMD Ventas): la empresa sale del primer segmento de la ruta, /<slug>/api/…, que el nginx del panel
// pasa tal cual. El enrutador la busca, la deja en el contexto y entrega el resto de la ruta (/api/…) a las rutas de
// siempre. Cada manejador usa s.st(r): un Store ligado a esa empresa, que filtra todas sus consultas por ella.
//
// Sin MULTITENANT (el despliegue de /baruka/), no hay prefijo: todo es la empresa 1 de la SQLite.

type ctxKey int

const tenantKey ctxKey = 1

// WithTenant deja la empresa en el contexto (lo usa el enrutador y las pruebas).
func WithTenant(ctx context.Context, t *store.Tenant) context.Context {
	return context.WithValue(ctx, tenantKey, t)
}

// TenantOf es la empresa de la petición (nil fuera del modo multiempresa).
func TenantOf(ctx context.Context) *store.Tenant {
	t, _ := ctx.Value(tenantKey).(*store.Tenant)
	return t
}

// tenantID de la petición: la del contexto o, sin multiempresa, la del Store base (1).
func (s *Server) tenantID(r *http.Request) int64 {
	if !s.cfg.MultiTenant {
		return s.store.TenantID()
	}
	if t := TenantOf(r.Context()); t != nil {
		return t.ID
	}
	return -1 // ninguna: no coincide con ninguna fila
}

// st es el Store de la empresa de la petición.
func (s *Server) st(r *http.Request) *store.Store {
	if !s.cfg.MultiTenant {
		return s.store
	}
	return s.store.ForTenant(s.tenantID(r))
}

// pub avisa a los paneles abiertos de la misma empresa.
func (s *Server) pub(r *http.Request, topic string) { s.hub.PublishTo(s.tenantID(r), topic) }

// business: nombre, moneda y WhatsApp público de la empresa.
func (s *Server) business(r *http.Request) (name, currency, whatsapp string) {
	if t := TenantOf(r.Context()); s.cfg.MultiTenant && t != nil {
		return t.Name, t.Currency, t.WhatsApp
	}
	return s.cfg.BusinessName, s.cfg.Currency, s.cfg.WhatsAppNumber
}

// mediaRoot: carpeta de fotos de la empresa. Multiempresa: DATA_DIR/tenants/<slug>/media.
func (s *Server) mediaRoot(r *http.Request) string {
	if t := TenantOf(r.Context()); s.cfg.MultiTenant && t != nil {
		return filepath.Join(s.cfg.DataDir, "tenants", t.Slug, "media")
	}
	return filepath.Join(s.cfg.DataDir, "media")
}

// waEnabled: la empresa de la petición es la que tiene el WhatsApp (el bot) de este backend.
func (s *Server) waEnabled(r *http.Request) bool {
	return s.cfg.EvolutionURL != "" && s.isBotTenant(r)
}

// EmpresaPublica es lo único que la portada de JMD Ventas sabe de cada empresa. Nada de ids, WhatsApp, moneda ni
// datos de clientas: solo lo que se pinta en su tarjeta.
type EmpresaPublica struct {
	Slug      string `json:"slug"`
	Nombre    string `json:"nombre"`
	Orden     int    `json:"orden"`
	Color     string `json:"color"`
	Logo      string `json:"logo"`
	Productos int    `json:"productos"` // productos activos
}

// empresasPublicas: GET /api/empresas (sin empresa en la ruta), las empresas activas en su orden.
func (s *Server) empresasPublicas(w http.ResponseWriter, r *http.Request) {
	ts, err := s.store.Tenants(r.Context())
	if err != nil {
		writeErr(w, 500, "no se pudo leer las empresas")
		return
	}
	out := make([]EmpresaPublica, 0, len(ts))
	for _, t := range ts {
		if !store.ValidSlug(t.Slug) { // p. ej. «default», la tienda de una SQLite de una sola empresa
			continue
		}
		n, _ := s.store.ForTenant(t.ID).CountActiveProducts(r.Context())
		out = append(out, EmpresaPublica{Slug: t.Slug, Nombre: t.Name, Orden: t.Orden, Color: store.ColorDe(t), Logo: t.Logo, Productos: n})
	}
	w.Header().Set("Cache-Control", "public, max-age=60")
	writeJSON(w, 200, out)
}

// isBotTenant: la empresa de la petición es la del bot (sin multiempresa, siempre).
func (s *Server) isBotTenant(r *http.Request) bool {
	return !s.cfg.MultiTenant || (s.BotTenant > 0 && s.tenantID(r) == s.BotTenant)
}

// tenantCache evita una consulta por petición para resolver el slug.
type tenantCache struct {
	mu sync.Mutex
	m  map[string]cachedTenant
}

type cachedTenant struct {
	t   *store.Tenant
	exp time.Time
}

func (c *tenantCache) get(ctx context.Context, st *store.Store, slug string) *store.Tenant {
	c.mu.Lock()
	if e, ok := c.m[slug]; ok && time.Now().Before(e.exp) {
		c.mu.Unlock()
		return e.t
	}
	c.mu.Unlock()
	t, err := st.TenantBySlug(ctx, slug)
	if err != nil && err != store.ErrNotFound {
		return nil // error de base: no se cachea
	}
	if t != nil && !t.Active {
		t = nil
	}
	c.mu.Lock()
	if c.m == nil {
		c.m = map[string]cachedTenant{}
	}
	c.m[slug] = cachedTenant{t: t, exp: time.Now().Add(30 * time.Second)}
	c.mu.Unlock()
	return t
}

// tenantRouter resuelve /<slug>/… → empresa en el contexto y /… a las rutas de siempre.
func (s *Server) tenantRouter(next http.Handler) http.Handler {
	cache := &tenantCache{}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/healthz" {
			next.ServeHTTP(w, r)
			return
		}
		if r.URL.Path == "/api/empresas" && r.Method == http.MethodGet {
			s.empresasPublicas(w, r)
			return
		}
		rest := strings.TrimPrefix(r.URL.Path, "/")
		slug, tail, _ := strings.Cut(rest, "/")
		if !store.ValidSlug(slug) {
			writeErr(w, http.StatusNotFound, "empresa no encontrada")
			return
		}
		t := cache.get(r.Context(), s.store, slug)
		if t == nil {
			writeErr(w, http.StatusNotFound, "empresa no encontrada")
			return
		}
		r2 := r.Clone(WithTenant(r.Context(), t))
		r2.URL.Path = "/" + tail
		r2.URL.RawPath = ""
		next.ServeHTTP(w, r2)
	})
}
