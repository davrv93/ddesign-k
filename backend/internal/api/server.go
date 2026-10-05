// Package api expone la API REST del panel, el webhook de evolution-go y los archivos de medios.
package api

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/ai"
	"github.com/davrv93/ddesign-k/backend/internal/auth"
	"github.com/davrv93/ddesign-k/backend/internal/bot"
	"github.com/davrv93/ddesign-k/backend/internal/config"
	"github.com/davrv93/ddesign-k/backend/internal/evolution"
	"github.com/davrv93/ddesign-k/backend/internal/kommo"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

type Server struct {
	cfg   *config.Config
	store *store.Store
	evo   *evolution.Client
	ai    *ai.Client
	bot   *bot.Bot
	auth  *auth.Auth
	hub   *Hub
	// Kommo es el sincronizador con el CRM (nil = apagado). Recibe los turnos del chat web y da el enlace «Ver en
	// Kommo» de las conversaciones.
	Kommo *kommo.Sincronizador
}

func New(cfg *config.Config, st *store.Store, evo *evolution.Client, aic *ai.Client, b *bot.Bot, a *auth.Auth, hub *Hub) *Server {
	return &Server{cfg: cfg, store: st, evo: evo, ai: aic, bot: b, auth: a, hub: hub}
}

func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) { writeJSON(w, 200, map[string]string{"status": "ok"}) })
	mux.HandleFunc("POST /api/auth/login", s.login)
	mux.HandleFunc("GET /api/public/catalog", s.publicCatalog)
	mux.HandleFunc("GET /api/public/info", s.publicInfo)
	// Stock como herramienta: el agente lo consulta en el momento de responder, nunca lo memoriza.
	mux.HandleFunc("GET /api/public/stock", s.publicStock)
	mux.HandleFunc("GET /api/public/stock/{code}", s.publicStock)
	mux.Handle("GET /media/", s.media())
	mux.HandleFunc("POST /webhook/evolution/{secret}", s.webhook)
	// Interna: el agente avisa los turnos del chat web para el CRM. Solo desde la red de Docker (el nginx del panel
	// la bloquea) y con CRM_EVENT_SECRET.
	mux.HandleFunc("POST /api/internal/crm/evento", s.crmEvento)

	p := func(pattern string, h http.HandlerFunc) { mux.Handle(pattern, s.requireAuth(h)) }
	p("GET /api/me", func(w http.ResponseWriter, r *http.Request) { writeJSON(w, 200, map[string]any{"ok": true}) })
	p("GET /api/events", s.hub.ServeHTTP)
	p("GET /api/stats", s.stats)

	p("GET /api/whatsapp/status", s.waStatus)
	p("POST /api/whatsapp/connect", s.waConnect)
	p("GET /api/whatsapp/qr", s.waQR)
	p("POST /api/whatsapp/pair", s.waPair)
	p("POST /api/whatsapp/logout", s.waLogout)

	p("GET /api/products", s.listProducts)
	p("POST /api/products", s.saveProduct)
	p("PUT /api/products/{id}", s.saveProduct)
	p("DELETE /api/products/{id}", s.deleteProduct)
	p("POST /api/products/{id}/image", s.uploadProductImage)
	p("POST /api/products/describe", s.describeImage)

	p("GET /api/orders", s.listOrders)
	p("POST /api/orders", s.createOrder)
	p("PATCH /api/orders/{id}", s.patchOrder)
	p("DELETE /api/orders/{id}", s.deleteOrder)

	p("GET /api/conversations", s.listConversations)
	p("GET /api/conversations/{id}/messages", s.listMessages)
	p("POST /api/conversations/{id}/send", s.sendMessage)
	p("POST /api/conversations/{id}/bot", s.setBot)
	p("POST /api/conversations/{id}/agent-version", s.setAgentVersion)
	p("GET /api/agent/metricas", s.agentMetricas)

	p("GET /api/settings", s.getSettings)
	p("PUT /api/settings", s.putSettings)

	return logRequests(mux)
}

// ---------------------------------------------------------------------------
// utilidades

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]string{"error": msg})
}

func readJSON(r *http.Request, v any) error {
	return json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(v)
}

func pathID(r *http.Request) (int64, error) {
	return strconv.ParseInt(r.PathValue("id"), 10, 64)
}

func logRequests(h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		h.ServeHTTP(w, r)
		if !strings.HasPrefix(r.URL.Path, "/media/") && r.URL.Path != "/api/events" && r.URL.Path != "/healthz" {
			log.Printf("%s %s %s", r.Method, r.URL.Path, time.Since(start).Round(time.Millisecond))
		}
	})
}

func (s *Server) requireAuth(h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		tok := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if tok == "" {
			tok = r.URL.Query().Get("token") // EventSource no permite cabeceras
		}
		if _, err := s.auth.Verify(tok); err != nil {
			writeErr(w, http.StatusUnauthorized, err.Error())
			return
		}
		h.ServeHTTP(w, r)
	})
}

// ---------------------------------------------------------------------------
// públicos

func (s *Server) login(w http.ResponseWriter, r *http.Request) {
	var in struct{ User, Password string }
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	tok, err := s.auth.Login(in.User, in.Password)
	if err != nil {
		time.Sleep(700 * time.Millisecond) // frena fuerza bruta
		writeErr(w, 401, err.Error())
		return
	}
	writeJSON(w, 200, map[string]string{"token": tok})
}

func (s *Server) publicInfo(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, map[string]string{"business": s.cfg.BusinessName, "currency": s.cfg.Currency})
}

func (s *Server) publicCatalog(w http.ResponseWriter, r *http.Request) {
	products, err := s.store.ListProducts(r.Context(), true)
	if err != nil {
		writeErr(w, 500, err.Error())
		return
	}
	type pub struct {
		Code        string   `json:"code"`
		Name        string   `json:"name"`
		Description string   `json:"description"`
		Category    string   `json:"category"`
		Color       string   `json:"color"`
		Price       float64  `json:"price"`
		Image       string   `json:"image"`
		Sizes       []string `json:"sizes"`
	}
	out := []pub{}
	for _, p := range products {
		sizes := []string{}
		for _, v := range p.Variants {
			if v.Available() > 0 {
				sizes = append(sizes, v.Size)
			}
		}
		out = append(out, pub{p.Code, p.Name, p.Description, p.Category, p.Color, p.Price, p.Image, sizes})
	}
	writeJSON(w, 200, map[string]any{"business": s.cfg.BusinessName, "currency": s.cfg.Currency, "whatsapp": s.cfg.WhatsAppNumber, "products": out})
}

// publicStock responde la disponibilidad real de uno o varios códigos (?codes=V05,VES-003):
// stock online (físico, reservado, disponible) por talla y stock por sucursal.
func (s *Server) publicStock(w http.ResponseWriter, r *http.Request) {
	var codes []string
	if c := r.PathValue("code"); c != "" {
		codes = []string{c}
	} else {
		for _, c := range strings.Split(r.URL.Query().Get("codes"), ",") {
			if c = strings.TrimSpace(c); c != "" {
				codes = append(codes, c)
			}
		}
	}
	if len(codes) == 0 || len(codes) > 50 {
		writeErr(w, 400, "indica entre 1 y 50 códigos")
		return
	}
	for i := range codes {
		codes[i] = strings.ToUpper(codes[i])
	}
	type size struct {
		Stock     int `json:"stock"`
		Reserved  int `json:"reserved"`
		Available int `json:"available"`
	}
	type entry struct {
		Code     string                 `json:"code"`
		Product  bool                   `json:"product"` // true si se vende en la tienda virtual
		Online   map[string]size        `json:"online"`
		Branches []store.WarehouseStock `json:"branches"`
	}
	products, err := s.store.ListProducts(r.Context(), true)
	if err != nil {
		writeErr(w, 500, err.Error())
		return
	}
	byCode := map[string]*store.Product{}
	for _, p := range products {
		byCode[strings.ToUpper(p.Code)] = p
	}
	branches, err := s.store.WarehouseStockByCode(r.Context(), codes)
	if err != nil {
		writeErr(w, 500, err.Error())
		return
	}
	out := make([]entry, 0, len(codes))
	for _, c := range codes {
		e := entry{Code: c, Online: map[string]size{}, Branches: branches[c]}
		if e.Branches == nil {
			e.Branches = []store.WarehouseStock{}
		}
		if p, ok := byCode[c]; ok {
			e.Product = true
			for _, v := range p.Variants {
				e.Online[v.Size] = size{v.Stock, v.Reserved, v.Available()}
			}
		}
		out = append(out, e)
	}
	writeJSON(w, 200, map[string]any{"stock": out, "at": time.Now().UTC()})
}

func (s *Server) media() http.Handler {
	root := filepath.Join(s.cfg.DataDir, "media")
	fs := http.StripPrefix("/media/", http.FileServer(http.Dir(root)))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/") { // sin listado de carpetas
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Cache-Control", "public, max-age=86400")
		fs.ServeHTTP(w, r)
	})
}

func (s *Server) webhook(w http.ResponseWriter, r *http.Request) {
	if subtle.ConstantTimeCompare([]byte(r.PathValue("secret")), []byte(s.cfg.WebhookSecret)) != 1 {
		writeErr(w, 403, "forbidden")
		return
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 64<<20))
	if err != nil {
		writeErr(w, 400, "body")
		return
	}
	in, err := bot.ParseWebhook(body)
	if err != nil {
		log.Printf("webhook: payload no reconocido: %v", err)
	}
	// Se responde de inmediato; evolution-go reintenta si tardamos.
	writeJSON(w, 200, map[string]bool{"received": true})
	if in != nil {
		go func() {
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
			defer cancel()
			s.bot.Handle(ctx, in)
		}()
	}
}

// ---------------------------------------------------------------------------
// WhatsApp

func (s *Server) WebhookURL() string {
	return s.cfg.WebhookBaseURL + "/webhook/evolution/" + s.cfg.WebhookSecret
}

// Bootstrap crea la instancia y, si ya hay sesión, actualiza el webhook. Reintenta hasta que evolution-go responda.
func (s *Server) Bootstrap(ctx context.Context) {
	for attempt := 0; ; attempt++ {
		if _, err := s.evo.EnsureInstance(ctx); err != nil {
			if attempt%10 == 0 {
				log.Printf("whatsapp: esperando a evolution-go: %v", err)
			}
		} else {
			st, err := s.evo.Status(ctx)
			if err == nil && st.LoggedIn {
				if err := s.evo.Connect(ctx, s.WebhookURL()); err != nil {
					log.Printf("whatsapp: connect: %v", err)
				}
			}
			log.Printf("whatsapp: instancia %q lista (sesión activa: %v)", s.cfg.EvolutionInstance, err == nil && st.LoggedIn)
			return
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(3 * time.Second):
		}
	}
}

func (s *Server) waStatus(w http.ResponseWriter, r *http.Request) {
	st, err := s.evo.Status(r.Context())
	if err != nil {
		writeJSON(w, 200, map[string]any{"available": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"available": true, "connected": st.Connected, "logged_in": st.LoggedIn, "name": st.Name,
		"instance": s.cfg.EvolutionInstance})
}

func (s *Server) waConnect(w http.ResponseWriter, r *http.Request) {
	if _, err := s.evo.EnsureInstance(r.Context()); err != nil {
		writeErr(w, 502, err.Error())
		return
	}
	if err := s.evo.Connect(r.Context(), s.WebhookURL()); err != nil {
		writeErr(w, 502, err.Error())
		return
	}
	s.hub.Publish("whatsapp")
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func (s *Server) waQR(w http.ResponseWriter, r *http.Request) {
	qr, err := s.evo.QR(r.Context())
	if err != nil {
		writeJSON(w, 200, map[string]any{"pending": true, "error": err.Error()})
		return
	}
	writeJSON(w, 200, qr)
}

func (s *Server) waPair(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Phone string `json:"phone"`
	}
	if err := readJSON(r, &in); err != nil || in.Phone == "" {
		writeErr(w, 400, "número requerido")
		return
	}
	digits := strings.Map(func(r rune) rune {
		if r >= '0' && r <= '9' {
			return r
		}
		return -1
	}, in.Phone)
	if _, err := s.evo.EnsureInstance(r.Context()); err != nil {
		writeErr(w, 502, err.Error())
		return
	}
	if err := s.evo.Connect(r.Context(), s.WebhookURL()); err != nil {
		writeErr(w, 502, err.Error())
		return
	}
	code, err := s.evo.Pair(r.Context(), digits)
	if err != nil {
		writeErr(w, 502, err.Error())
		return
	}
	writeJSON(w, 200, map[string]string{"code": code})
}

func (s *Server) waLogout(w http.ResponseWriter, r *http.Request) {
	if err := s.evo.Logout(r.Context()); err != nil {
		writeErr(w, 502, err.Error())
		return
	}
	s.hub.Publish("whatsapp")
	writeJSON(w, 200, map[string]bool{"ok": true})
}

// ---------------------------------------------------------------------------
// productos

func (s *Server) listProducts(w http.ResponseWriter, r *http.Request) {
	products, err := s.store.ListProducts(r.Context(), false)
	if err != nil {
		writeErr(w, 500, err.Error())
		return
	}
	if products == nil {
		products = []*store.Product{}
	}
	writeJSON(w, 200, products)
}

func (s *Server) saveProduct(w http.ResponseWriter, r *http.Request) {
	var p store.Product
	if err := readJSON(r, &p); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	if r.PathValue("id") != "" {
		id, err := pathID(r)
		if err != nil {
			writeErr(w, 400, "id inválido")
			return
		}
		p.ID = id
		if old, err := s.store.GetProduct(r.Context(), id); err == nil && p.Image == "" {
			p.Image = old.Image
		}
	} else {
		p.ID = 0
	}
	if strings.TrimSpace(p.Code) == "" || strings.TrimSpace(p.Name) == "" {
		writeErr(w, 400, "código y nombre son obligatorios")
		return
	}
	if err := s.store.SaveProduct(r.Context(), &p); err != nil {
		code := 500
		if strings.Contains(err.Error(), "UNIQUE") {
			code, err = 409, errors.New("ya existe un producto con ese código")
		}
		writeErr(w, code, err.Error())
		return
	}
	saved, _ := s.store.GetProduct(r.Context(), p.ID)
	s.hub.Publish("products")
	writeJSON(w, 200, saved)
}

func (s *Server) deleteProduct(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	if err := s.store.DeleteProduct(r.Context(), id); err != nil {
		writeErr(w, 500, err.Error())
		return
	}
	s.hub.Publish("products")
	writeJSON(w, 200, map[string]bool{"ok": true})
}

// readUpload lee el campo "image" de un multipart (máx. 8 MB).
func readUpload(r *http.Request) ([]byte, string, error) {
	if err := r.ParseMultipartForm(8 << 20); err != nil {
		return nil, "", errors.New("imagen demasiado grande o formulario inválido")
	}
	f, _, err := r.FormFile("image")
	if err != nil {
		return nil, "", errors.New("falta el archivo 'image'")
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, 8<<20))
	if err != nil {
		return nil, "", err
	}
	mime := http.DetectContentType(data)
	if !strings.HasPrefix(mime, "image/") {
		return nil, "", errors.New("el archivo no es una imagen")
	}
	return data, mime, nil
}

func (s *Server) uploadProductImage(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	p, err := s.store.GetProduct(r.Context(), id)
	if err != nil {
		writeErr(w, 404, "producto no encontrado")
		return
	}
	data, mime, err := readUpload(r)
	if err != nil {
		writeErr(w, 400, err.Error())
		return
	}
	ext := map[string]string{"image/png": ".png", "image/webp": ".webp"}[mime]
	if ext == "" {
		ext = ".jpg"
	}
	dir := filepath.Join(s.cfg.DataDir, "media", "products")
	_ = os.MkdirAll(dir, 0o755)
	name := strings.ToLower(p.Code) + "-" + strconv.FormatInt(time.Now().Unix(), 36) + ext
	if err := os.WriteFile(filepath.Join(dir, name), data, 0o644); err != nil {
		writeErr(w, 500, err.Error())
		return
	}
	tags := ""
	if r.URL.Query().Get("describe") == "1" && s.ai.Enabled() {
		ctx, cancel := context.WithTimeout(r.Context(), time.Duration(s.cfg.GeminiTimeoutSec)*time.Second)
		if d, err := s.ai.DescribeProduct(ctx, ai.Image{Mime: mime, Data: data}); err == nil {
			tags = d.Tags
		}
		cancel()
	}
	if err := s.store.SetProductImage(r.Context(), id, "/media/products/"+name, tags); err != nil {
		writeErr(w, 500, err.Error())
		return
	}
	saved, _ := s.store.GetProduct(r.Context(), id)
	s.hub.Publish("products")
	writeJSON(w, 200, saved)
}

func (s *Server) describeImage(w http.ResponseWriter, r *http.Request) {
	if !s.ai.Enabled() {
		writeErr(w, 400, "IA desactivada (falta GEMINI_API_KEY)")
		return
	}
	data, mime, err := readUpload(r)
	if err != nil {
		writeErr(w, 400, err.Error())
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), time.Duration(s.cfg.GeminiTimeoutSec)*time.Second)
	defer cancel()
	d, err := s.ai.DescribeProduct(ctx, ai.Image{Mime: mime, Data: data})
	if err != nil {
		writeErr(w, 502, err.Error())
		return
	}
	writeJSON(w, 200, d)
}

// ---------------------------------------------------------------------------
// pedidos

func (s *Server) listOrders(w http.ResponseWriter, r *http.Request) {
	orders, err := s.store.ListOrders(r.Context(), 0)
	if err != nil {
		writeErr(w, 500, err.Error())
		return
	}
	if orders == nil {
		orders = []*store.Order{}
	}
	writeJSON(w, 200, map[string]any{"statuses": store.OrderStatuses, "orders": orders})
}

// createOrder registra un pedido manual (p. ej. tomado por teléfono o en tienda).
func (s *Server) createOrder(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Phone     string `json:"phone"`
		Name      string `json:"name"`
		ProductID int64  `json:"product_id"`
		Size      string `json:"size"`
		Qty       int    `json:"qty"`
		Status    string `json:"status"`
		Notes     string `json:"notes"`
		Address   string `json:"address"`
	}
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	phone := strings.Map(func(r rune) rune {
		if r >= '0' && r <= '9' {
			return r
		}
		return -1
	}, in.Phone)
	if len(phone) < 8 {
		writeErr(w, 400, "teléfono inválido (incluye código de país, ej. 51987654321)")
		return
	}
	if in.Status == "" {
		in.Status = "confirmado"
	}
	if !store.ValidStatus(in.Status) {
		writeErr(w, 400, "estado inválido")
		return
	}
	cust, _, err := s.store.UpsertCustomer(r.Context(), phone+"@s.whatsapp.net", phone, in.Name)
	if err != nil {
		writeErr(w, 500, err.Error())
		return
	}
	o := &store.Order{CustomerID: cust.ID, Status: in.Status, Source: "manual", Notes: in.Notes, LocationText: in.Address}
	if in.ProductID > 0 {
		p, err := s.store.GetProduct(r.Context(), in.ProductID)
		if err != nil {
			writeErr(w, 404, "producto no encontrado")
			return
		}
		v := p.VariantBySize(in.Size)
		if v == nil {
			writeErr(w, 400, "talla no existe para este producto")
			return
		}
		if in.Qty <= 0 {
			in.Qty = 1
		}
		o.Items = []store.OrderItem{{ProductID: &p.ID, VariantID: &v.ID, ProductCode: p.Code, ProductName: p.Name, Image: p.Image,
			Size: v.Size, Qty: in.Qty, UnitPrice: p.Price}}
	}
	if err := s.store.CreateOrder(r.Context(), o); err != nil {
		code := 500
		if errors.Is(err, store.ErrNoStock) {
			code = 409
		}
		writeErr(w, code, err.Error())
		return
	}
	s.hub.Publish("orders")
	s.hub.Publish("products")
	saved, _ := s.store.GetOrder(r.Context(), o.ID)
	writeJSON(w, 200, saved)
}

func (s *Server) patchOrder(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	var in struct {
		Status   *string  `json:"status"`
		Position *float64 `json:"position"`
		Notes    *string  `json:"notes"`
		Notify   *bool    `json:"notify"`
	}
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	ctx := r.Context()
	if in.Notes != nil {
		if err := s.store.UpdateOrderNotes(ctx, id, *in.Notes); err != nil {
			writeErr(w, 500, err.Error())
			return
		}
	}
	changed := false
	if in.Status != nil {
		prev, err := s.store.UpdateOrderStatus(ctx, id, *in.Status, in.Position)
		if err != nil {
			code := 500
			switch {
			case errors.Is(err, store.ErrNoStock):
				code = 409
			case errors.Is(err, store.ErrNotFound):
				code = 404
			case strings.HasPrefix(err.Error(), "estado inválido"):
				code = 400
			}
			writeErr(w, code, err.Error())
			return
		}
		changed = prev != *in.Status
	}
	o, err := s.store.GetOrder(ctx, id)
	if err != nil {
		writeErr(w, 404, "pedido no encontrado")
		return
	}
	if changed && (in.Notify == nil || *in.Notify) {
		go s.bot.NotifyStatus(context.Background(), o)
	}
	if changed {
		go s.bot.PedidoCambio(context.Background(), o) // CRM: pagado, enviado o cancelado desde el tablero
	}
	s.hub.Publish("orders")
	s.hub.Publish("products")
	writeJSON(w, 200, o)
}

func (s *Server) deleteOrder(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	if err := s.store.DeleteOrder(r.Context(), id); err != nil {
		writeErr(w, 500, err.Error())
		return
	}
	s.hub.Publish("orders")
	s.hub.Publish("products")
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func (s *Server) stats(w http.ResponseWriter, r *http.Request) {
	st, err := s.store.Stats(r.Context())
	if err != nil {
		writeErr(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, st)
}

// ---------------------------------------------------------------------------
// conversaciones

func (s *Server) listConversations(w http.ResponseWriter, r *http.Request) {
	convs, err := s.store.ListConversations(r.Context(), 200)
	if err != nil {
		writeErr(w, 500, err.Error())
		return
	}
	if convs == nil {
		convs = []*store.Conversation{}
	}
	writeJSON(w, 200, convs)
}

func (s *Server) listMessages(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	conv, err := s.store.GetConversation(r.Context(), id)
	if err != nil {
		writeErr(w, 404, "conversación no encontrada")
		return
	}
	msgs, err := s.store.ListMessages(r.Context(), id, 300)
	if err != nil {
		writeErr(w, 500, err.Error())
		return
	}
	if msgs == nil {
		msgs = []*store.Message{}
	}
	if conv.Unread > 0 {
		_ = s.store.MarkRead(r.Context(), id)
		s.hub.Publish("conversations")
	}
	orders, _ := s.store.ListOrders(r.Context(), conv.CustomerID)
	if orders == nil {
		orders = []*store.Order{}
	}
	out := map[string]any{"conversation": conv, "messages": msgs, "orders": orders}
	if s.Kommo != nil {
		if lead := s.store.LeadKommoDeConversacion(r.Context(), id); lead > 0 {
			out["kommo_url"] = s.Kommo.Cliente().URLLead(lead)
		}
	}
	writeJSON(w, 200, out)
}

// crmEvento recibe del agente el turno de una conversación del chat web y lo encola al mismo sincronizador que
// WhatsApp. Responde enseguida (202): Kommo va en segundo plano.
func (s *Server) crmEvento(w http.ResponseWriter, r *http.Request) {
	// Sin secreto la ruta no existe. Con cabeceras de proxy, la petición vino de fuera (el nginx del panel las pone):
	// esta ruta es solo para la red interna de Docker.
	if s.cfg.CRMEventSecret == "" || r.Header.Get("X-Forwarded-For") != "" || r.Header.Get("X-Real-IP") != "" {
		http.NotFound(w, r)
		return
	}
	if subtle.ConstantTimeCompare([]byte(r.Header.Get("X-CRM-Secret")), []byte(s.cfg.CRMEventSecret)) != 1 {
		writeErr(w, 403, "forbidden")
		return
	}
	var in kommo.EventoWeb
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	ev, err := in.Evento()
	if err != nil {
		writeErr(w, 400, err.Error())
		return
	}
	if s.Kommo == nil {
		writeJSON(w, 202, map[string]bool{"ok": true, "kommo": false})
		return
	}
	s.Kommo.Encolar(ev)
	writeJSON(w, 202, map[string]bool{"ok": true, "kommo": true})
}

func (s *Server) sendMessage(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	var in struct {
		Text     string `json:"text"`
		PauseBot *bool  `json:"pause_bot"`
	}
	if err := readJSON(r, &in); err != nil || strings.TrimSpace(in.Text) == "" {
		writeErr(w, 400, "mensaje vacío")
		return
	}
	conv, err := s.store.GetConversation(r.Context(), id)
	if err != nil {
		writeErr(w, 404, "conversación no encontrada")
		return
	}
	if err := s.bot.SendManual(r.Context(), conv, strings.TrimSpace(in.Text)); err != nil {
		writeErr(w, 502, err.Error())
		return
	}
	// Si la asesora escribe, el bot se hace a un lado (salvo que pida lo contrario).
	if in.PauseBot == nil || *in.PauseBot {
		_ = s.store.SetBotPaused(r.Context(), id, true)
	}
	s.hub.Publish("conversations")
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func (s *Server) setBot(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	var in struct {
		Paused bool `json:"paused"`
	}
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	if err := s.store.SetBotPaused(r.Context(), id, in.Paused); err != nil {
		writeErr(w, 500, err.Error())
		return
	}
	s.hub.Publish("conversations")
	writeJSON(w, 200, map[string]bool{"ok": true})
}

// setAgentVersion fija la versión del agente (v1 | v2) de UNA conversación; "" la devuelve a la política de ajustes.
func (s *Server) setAgentVersion(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	var in struct {
		Version string `json:"version"`
	}
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	if in.Version = strings.ToLower(strings.TrimSpace(in.Version)); in.Version != "" && in.Version != "v1" && in.Version != "v2" {
		writeErr(w, 400, "version debe ser v1, v2 o vacía")
		return
	}
	if err := s.store.SetAgentVersion(r.Context(), id, in.Version); err != nil {
		if errors.Is(err, store.ErrNotFound) {
			writeErr(w, 404, "conversación no encontrada")
			return
		}
		writeErr(w, 500, err.Error())
		return
	}
	s.hub.Publish("conversations")
	writeJSON(w, 200, map[string]string{"version": in.Version})
}

// agentMetricas devuelve las métricas por versión del agente (turnos, latencia, acuerdo de V2 con V1…) para el panel.
func (s *Server) agentMetricas(w http.ResponseWriter, r *http.Request) {
	if s.bot == nil || s.bot.Agent == nil {
		writeJSON(w, 200, map[string]any{"disponible": false})
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 4*time.Second)
	defer cancel()
	v, err := s.bot.Agent.Metricas(ctx)
	if err != nil {
		writeJSON(w, 200, map[string]any{"disponible": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"disponible": true, "versiones": v})
}

// ---------------------------------------------------------------------------
// ajustes

var settingDefaults = map[string]string{
	"bot_enabled":           "true",
	"notify_status_changes": "true",
	"pause_on_manual_reply": "true",
	"bot_resume_hours":      "12",
	// Versión del agente (V1 o V2) y cómo se reparte entre las clientas. Ver internal/bot/versionagente.go.
	bot.AjusteVersion:    bot.VersionPorDefecto, // v1 | v2 | ab
	bot.AjustePorcentaje: "0",                   // 0–100: con «ab», % de clientas que va a V2
	bot.AjusteNumeros:    "",                    // números que siempre van a V2 (pruebas)
	bot.AjusteModoV2:     "sombra",              // sombra | activo
}

// validarAjuste revisa (y limpia) el valor de los ajustes de versión; el resto pasa tal cual.
func validarAjuste(k, v string) (string, error) {
	switch k {
	case bot.AjusteVersion:
		if v = strings.ToLower(strings.TrimSpace(v)); v != "v1" && v != "v2" && v != "ab" {
			return "", errors.New("agent_version debe ser v1, v2 o ab")
		}
	case bot.AjusteModoV2:
		if v = strings.ToLower(strings.TrimSpace(v)); v != "sombra" && v != "activo" {
			return "", errors.New("agent_v2_modo debe ser sombra o activo")
		}
	case bot.AjustePorcentaje:
		n, err := strconv.Atoi(strings.TrimSpace(v))
		if err != nil || n < 0 || n > 100 {
			return "", errors.New("agent_v2_percent debe ser un entero de 0 a 100")
		}
		v = strconv.Itoa(n)
	case bot.AjusteNumeros:
		v = strings.Join(bot.SoloDigitos(strings.Split(v, ",")), ",")
	}
	return v, nil
}

func (s *Server) getSettings(w http.ResponseWriter, r *http.Request) {
	saved, err := s.store.Settings(r.Context())
	if err != nil {
		writeErr(w, 500, err.Error())
		return
	}
	out := map[string]string{}
	for k, v := range settingDefaults {
		out[k] = v
		if sv, ok := saved[k]; ok {
			out[k] = sv
		}
	}
	writeJSON(w, 200, out)
}

func (s *Server) putSettings(w http.ResponseWriter, r *http.Request) {
	var in map[string]string
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	// Primero se valida todo: un valor malo no deja la mitad de los ajustes cambiada.
	limpios := map[string]string{}
	for k, v := range in {
		if _, ok := settingDefaults[k]; !ok {
			continue
		}
		v, err := validarAjuste(k, v)
		if err != nil {
			writeErr(w, 400, err.Error())
			return
		}
		limpios[k] = v
	}
	for k, v := range limpios {
		if err := s.store.SetSetting(r.Context(), k, v); err != nil {
			writeErr(w, 500, err.Error())
			return
		}
	}
	s.getSettings(w, r)
}
