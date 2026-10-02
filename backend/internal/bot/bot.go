// Package bot implementa el menú conversacional de WhatsApp: catálogo, consulta por foto,
// verificación de stock, pedido, confirmación y ubicación.
package bot

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
	"github.com/davrv93/ddesign-k/backend/internal/ai"
	"github.com/davrv93/ddesign-k/backend/internal/config"
	"github.com/davrv93/ddesign-k/backend/internal/evolution"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// Estados de la conversación.
const (
	stIdle       = ""
	stPhoto      = "esperando_foto"
	stSize       = "esperando_talla"
	stConfirm    = "esperando_confirmacion"
	stLocation   = "esperando_ubicacion"
	stHumanAsked = "asesora"
)

type convContext struct {
	ProductID int64  `json:"product_id,omitempty"`
	Size      string `json:"size,omitempty"`
	Qty       int    `json:"qty,omitempty"`
	OrderID   int64  `json:"order_id,omitempty"`
}

type Bot struct {
	cfg   *config.Config
	store *store.Store
	evo   *evolution.Client
	ai    *ai.Client
	// Agent atiende el texto libre (clasificador local + DeepSeek). nil = sólo Gemini.
	Agent *agente.Client
	// Notify avisa al panel que algo cambió ("orders", "conversations", ...).
	Notify func(topic string)

	mu      sync.Mutex
	locks   map[string]*sync.Mutex
	outbox  map[string]chan outJob
	pending sync.WaitGroup
}

func New(cfg *config.Config, st *store.Store, evo *evolution.Client, aic *ai.Client) *Bot {
	return &Bot{cfg: cfg, store: st, evo: evo, ai: aic, Notify: func(string) {}, locks: map[string]*sync.Mutex{}, outbox: map[string]chan outJob{}}
}

// lock serializa los mensajes de un mismo chat para no mezclar estados.
func (b *Bot) lock(chat string) func() {
	b.mu.Lock()
	l, ok := b.locks[chat]
	if !ok {
		l = &sync.Mutex{}
		b.locks[chat] = l
	}
	b.mu.Unlock()
	l.Lock()
	return l.Unlock
}

func (b *Bot) MediaDir(sub string) string { return filepath.Join(b.cfg.DataDir, "media", sub) }

// Handle procesa un mensaje entrante del webhook.
func (b *Bot) Handle(ctx context.Context, in *Incoming) {
	if in == nil || in.IsGroup || in.Chat == "" {
		return
	}
	defer b.lock(in.Chat)()
	if b.store.MessageExists(ctx, in.ID) {
		return
	}
	name := in.PushName
	if in.FromMe {
		name = ""
	}
	cust, conv, err := b.store.UpsertCustomer(ctx, in.Chat, in.Phone, name)
	if err != nil {
		log.Printf("bot: cliente %s: %v", in.Chat, err)
		return
	}
	conv.Customer = cust

	msg := &store.Message{ConversationID: conv.ID, WAID: in.ID, Direction: "in", Kind: "text", Body: in.Text, Author: "cliente"}
	var img *ai.Image
	switch {
	case in.HasImage:
		msg.Kind = "image"
		data, mime, err := b.imageBytes(ctx, in)
		if err != nil {
			log.Printf("bot: descarga de imagen %s: %v", in.ID, err)
		} else {
			img = &ai.Image{Mime: mime, Data: data}
			if path, err := b.saveMedia("in", data, mime); err == nil {
				msg.Media = path
			}
		}
	case in.HasLocation:
		msg.Kind = "location"
		msg.Body = fmt.Sprintf("%.6f,%.6f %s", in.Lat, in.Lng, in.LocationTxt)
	}

	if in.FromMe {
		// Mensaje escrito por la dueña desde el celular: queda en el historial y el bot cede el chat.
		msg.Direction, msg.Author = "out", "asesora"
		_ = b.store.AddMessage(ctx, msg)
		if b.store.Setting(ctx, "pause_on_manual_reply", "true") == "true" && !conv.BotPaused {
			_ = b.store.SetBotPaused(ctx, conv.ID, true)
		}
		b.Notify("conversations")
		return
	}
	if err := b.store.AddMessage(ctx, msg); err != nil {
		log.Printf("bot: guardar mensaje: %v", err)
	}
	b.Notify("conversations")

	if conv.BotPaused || b.store.Setting(ctx, "bot_enabled", "true") != "true" {
		return
	}
	b.step(ctx, conv, in, msg, img)
}

func (b *Bot) imageBytes(ctx context.Context, in *Incoming) ([]byte, string, error) {
	if in.ImageB64 != "" {
		data, mime, err := evolution.DecodeDataURL(in.ImageB64)
		if err == nil {
			return data, firstNonEmpty(mime, in.ImageMime, http.DetectContentType(data)), nil
		}
	}
	if len(in.RawMessage) == 0 {
		return nil, "", errors.New("mensaje sin datos de imagen")
	}
	data, mime, err := b.evo.DownloadMedia(ctx, in.RawMessage)
	if err != nil {
		return nil, "", err
	}
	return data, firstNonEmpty(mime, in.ImageMime, http.DetectContentType(data)), nil
}

func (b *Bot) saveMedia(sub string, data []byte, mime string) (string, error) {
	dir := b.MediaDir(sub)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	ext := ".jpg"
	switch {
	case strings.Contains(mime, "png"):
		ext = ".png"
	case strings.Contains(mime, "webp"):
		ext = ".webp"
	}
	name := randomHex(16) + ext
	if err := os.WriteFile(filepath.Join(dir, name), data, 0o644); err != nil {
		return "", err
	}
	return "/media/" + sub + "/" + name, nil
}

func randomHex(n int) string {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// ---------------------------------------------------------------------------
// Máquina de estados

var (
	reCode   = regexp.MustCompile(`(?i)\b([a-z]{1,3}-?\d{1,4})\b`)
	reNumber = regexp.MustCompile(`\b(\d{1,2})\b`)
)

func normalize(s string) string {
	s = strings.ToLower(strings.TrimSpace(s))
	r := strings.NewReplacer("á", "a", "é", "e", "í", "i", "ó", "o", "ú", "u", "ü", "u", "!", "", "¡", "", "?", "", "¿", "", ".", "", ",", " ")
	return strings.Join(strings.Fields(r.Replace(s)), " ")
}

func isAny(s string, words ...string) bool {
	for _, w := range words {
		if s == w {
			return true
		}
	}
	return false
}

func hasAny(s string, words ...string) bool {
	for _, w := range words {
		if strings.Contains(" "+s+" ", " "+w+" ") {
			return true
		}
	}
	return false
}

func (b *Bot) step(ctx context.Context, conv *store.Conversation, in *Incoming, msg *store.Message, img *ai.Image) {
	var cc convContext
	_ = json.Unmarshal([]byte(conv.Context), &cc)
	text := normalize(in.Text)

	// Comandos globales.
	if isAny(text, "menu", "inicio", "0", "volver", "hola", "buenas", "buenos dias", "buenas tardes", "buenas noches", "hi") && !in.HasImage {
		b.sendMenu(ctx, conv)
		return
	}

	// Una foto siempre inicia una consulta de modelo.
	if in.HasImage {
		b.handlePhoto(ctx, conv, &cc, msg, img)
		return
	}

	switch conv.State {
	case stSize:
		b.handleSize(ctx, conv, &cc, text)
		return
	case stConfirm:
		b.handleConfirm(ctx, conv, &cc, text)
		return
	case stLocation:
		b.handleLocation(ctx, conv, &cc, in, text)
		return
	}

	if in.HasLocation {
		b.reply(ctx, conv, "📍 ¡Gracias por tu ubicación! Si deseas hacer un pedido, escribe *1* para ver el catálogo o envíanos la foto del modelo.")
		return
	}

	switch {
	case isAny(text, "1", "catalogo", "ver catalogo", "precios", "modelos"):
		b.sendCatalog(ctx, conv)
		return
	// «quiero ver su catálogo» en una frase: con agente lo presenta él, con fotos; sin agente, la lista.
	case hasAny(text, "catalogo") && b.Agent == nil:
		b.sendCatalog(ctx, conv)
		return
	case isAny(text, "2") || hasAny(text, "foto", "consultar modelo"):
		b.setState(ctx, conv, stPhoto, cc)
		b.reply(ctx, conv, "📸 ¡Perfecto! Envíanos la *foto* del modelo que te gustó y verificamos si lo tenemos en stock.")
		return
	case isAny(text, "3") || hasAny(text, "mi pedido", "estado"):
		b.sendOrderStatus(ctx, conv)
		return
	case isAny(text, "4", "asesora", "asesor", "humano", "persona"):
		b.handoff(ctx, conv)
		return
	}
	if m := reCode.FindStringSubmatch(in.Text); m != nil {
		if p, err := b.store.GetProductByCode(ctx, strings.ReplaceAll(m[1], "-", "")); err == nil && p.Active {
			b.offerProduct(ctx, conv, &cc, p, "", 0, nil)
			return
		}
	}
	b.freeText(ctx, conv, &cc, in.Text)
}

func (b *Bot) setState(ctx context.Context, conv *store.Conversation, state string, cc convContext) {
	raw, _ := json.Marshal(cc)
	conv.State, conv.Context = state, string(raw)
	if err := b.store.SetConversationState(ctx, conv.ID, state, string(raw)); err != nil {
		log.Printf("bot: estado: %v", err)
	}
}

func (b *Bot) money(v float64) string { return fmt.Sprintf("%s %.2f", b.cfg.Currency, v) }

func (b *Bot) sendMenu(ctx context.Context, conv *store.Conversation) {
	b.setState(ctx, conv, stIdle, convContext{})
	name := strings.Fields(conv.Customer.Name + " ")
	hello := "¡Hola! 👋"
	if len(name) > 0 {
		hello = "¡Hola " + name[0] + "! 👋"
	}
	b.reply(ctx, conv, hello+" Bienvenida a *"+b.cfg.BusinessName+"* ✨\n¿En qué te ayudamos hoy?\n\n"+
		"1️⃣ Ver catálogo y precios\n"+
		"2️⃣ Consultar un modelo (envíanos la foto 📸)\n"+
		"3️⃣ Estado de mi pedido\n"+
		"4️⃣ Hablar con una asesora\n\n"+
		"Responde con el *número* de la opción.")
}

func (b *Bot) sendCatalog(ctx context.Context, conv *store.Conversation) {
	products, err := b.store.ListProducts(ctx, true)
	if err != nil {
		b.reply(ctx, conv, "Uy, no pude cargar el catálogo en este momento 🙏. Intenta de nuevo en unos minutos.")
		return
	}
	var sb strings.Builder
	sb.WriteString("👗 *Catálogo " + b.cfg.BusinessName + "*\n\n")
	n := 0
	for _, p := range products {
		if p.TotalStock() == 0 {
			continue
		}
		sizes := []string{}
		for _, v := range p.Variants {
			if v.Stock > 0 {
				sizes = append(sizes, v.Size)
			}
		}
		fmt.Fprintf(&sb, "*%s* %s — %s (%s)\n", p.Code, p.Name, b.money(p.Price), strings.Join(sizes, ", "))
		n++
		if n >= 30 {
			break
		}
	}
	if n == 0 {
		sb.WriteString("Por ahora estamos renovando stock 🧵. ¡Vuelve pronto!\n")
	}
	if b.cfg.PublicURL != "" {
		sb.WriteString("\n🖼️ Mira las fotos aquí: " + b.cfg.PublicURL + "/catalogo\n")
	}
	sb.WriteString("\nPara pedir, responde con el *código* (ej. *V05*) o envíanos la *foto* del modelo 📸.")
	b.setState(ctx, conv, stIdle, convContext{})
	b.reply(ctx, conv, sb.String())
}

func (b *Bot) sendOrderStatus(ctx context.Context, conv *store.Conversation) {
	orders, err := b.store.ListOrders(ctx, conv.CustomerID)
	if err != nil {
		b.reply(ctx, conv, "No pude revisar tus pedidos ahora 🙏. Intenta en unos minutos.")
		return
	}
	labels := map[string]string{
		"pendiente": "⏳ Por confirmar", "confirmado": "✅ Confirmado", "preparando": "✂️ En preparación",
		"enviado": "🚚 En camino", "entregado": "🎉 Entregado", "cancelado": "❌ Cancelado",
	}
	var sb strings.Builder
	n := 0
	for _, o := range orders {
		if o.Status == "consulta" || len(o.Items) == 0 {
			continue
		}
		it := o.Items[0]
		fmt.Fprintf(&sb, "• Pedido *#%d* — %s %s talla %s ×%d — %s\n", o.ID, it.ProductCode, it.ProductName, it.Size, it.Qty, labels[o.Status])
		n++
		if n >= 5 {
			break
		}
	}
	if n == 0 {
		b.reply(ctx, conv, "Aún no tienes pedidos registrados 🛍️. Escribe *1* para ver el catálogo o envíanos la foto del modelo que te gusta.")
		return
	}
	b.reply(ctx, conv, "📦 *Tus pedidos*\n\n"+sb.String()+"\n¿Algo más? Escribe *menu* para ver las opciones.")
}

func (b *Bot) handoff(ctx context.Context, conv *store.Conversation) {
	b.setState(ctx, conv, stHumanAsked, convContext{})
	_ = b.store.SetBotPaused(ctx, conv.ID, true)
	b.reply(ctx, conv, "🙋‍♀️ ¡Listo! Una asesora te atenderá en breve por este mismo chat.")
	b.Notify("conversations")
}

// handlePhoto identifica el producto de la foto y ofrece stock.
func (b *Bot) handlePhoto(ctx context.Context, conv *store.Conversation, cc *convContext, msg *store.Message, img *ai.Image) {
	inquiry := func(notes string, conf float64) {
		o := &store.Order{CustomerID: conv.CustomerID, Status: "consulta", Source: "whatsapp",
			CustomerImage: msg.Media, Notes: notes, MatchConfidence: conf}
		if err := b.store.CreateOrder(ctx, o); err != nil {
			log.Printf("bot: consulta: %v", err)
		}
		b.Notify("orders")
	}
	if img == nil || !b.ai.Enabled() {
		inquiry("Foto recibida (sin análisis automático)", 0)
		b.reply(ctx, conv, "📸 ¡Recibimos tu foto! Una asesora revisará el modelo y te confirmará el stock en breve.")
		return
	}
	products, err := b.store.ListProducts(ctx, true)
	if err != nil || len(products) == 0 {
		inquiry("Foto recibida (catálogo vacío)", 0)
		b.reply(ctx, conv, "📸 ¡Recibimos tu foto! Una asesora te confirmará el stock en breve.")
		return
	}
	catalog := make([]ai.CatalogEntry, 0, len(products))
	byCode := map[string]*store.Product{}
	for _, p := range products {
		catalog = append(catalog, ai.CatalogEntry{Code: p.Code, Name: p.Name, Color: p.Color, Category: p.Category, Description: p.Description, Tags: p.AITags})
		byCode[p.Code] = p
	}
	b.reply(ctx, conv, "🔎 Un momento, estoy buscando ese modelo en nuestro catálogo…")
	actx, cancel := context.WithTimeout(ctx, time.Duration(b.cfg.GeminiTimeoutSec*2)*time.Second)
	defer cancel()
	res, err := b.ai.MatchProduct(actx, *img, catalog)
	if err != nil {
		log.Printf("bot: IA match: %v", err)
		inquiry("Foto recibida (la IA no respondió)", 0)
		b.reply(ctx, conv, "📸 ¡Recibimos tu foto! Una asesora revisará el modelo y te confirmará el stock en breve.")
		return
	}
	log.Printf("bot: IA (%s) foto=%s code=%s conf=%.2f alt=%v", res.Model, msg.Media, res.Code, res.Confidence, res.Alternatives)
	if p, ok := byCode[res.Code]; ok && res.Confidence >= b.cfg.MatchThreshold {
		b.offerProduct(ctx, conv, cc, p, msg.Media, res.Confidence, alternativesOf(res, byCode))
		return
	}
	// Sin coincidencia clara: sugerimos parecidos y dejamos la consulta en el tablero.
	inquiry("IA: "+res.Seen, res.Confidence)
	alts := alternativesOf(res, byCode)
	if p, ok := byCode[res.Code]; ok {
		alts = append([]*store.Product{p}, alts...)
	}
	if len(alts) == 0 {
		b.reply(ctx, conv, "🤔 No encontré ese modelo exacto en nuestro catálogo. Una asesora lo revisará y te escribirá en breve.\n\nMientras tanto, escribe *1* para ver los modelos disponibles.")
		return
	}
	var sb strings.Builder
	sb.WriteString("🤔 No estoy segura de cuál es. ¿Es alguno de estos?\n\n")
	for _, p := range alts {
		fmt.Fprintf(&sb, "*%s* %s — %s\n", p.Code, p.Name, b.money(p.Price))
	}
	sb.WriteString("\nResponde con el *código* o espera a que una asesora te ayude 🙌.")
	b.setState(ctx, conv, stIdle, convContext{})
	b.reply(ctx, conv, sb.String())
}

func alternativesOf(res *ai.MatchResult, byCode map[string]*store.Product) []*store.Product {
	var out []*store.Product
	seen := map[string]bool{res.Code: true}
	for _, c := range res.Alternatives {
		c = strings.ToUpper(strings.TrimSpace(c))
		if p, ok := byCode[c]; ok && !seen[c] {
			seen[c] = true
			out = append(out, p)
		}
	}
	return out
}

// offerProduct muestra el producto con su stock por talla y pide la talla.
func (b *Bot) offerProduct(ctx context.Context, conv *store.Conversation, cc *convContext, p *store.Product, customerImage string, conf float64, alts []*store.Product) {
	if p.TotalStock() == 0 {
		o := &store.Order{CustomerID: conv.CustomerID, Status: "consulta", Source: "whatsapp", CustomerImage: customerImage,
			MatchConfidence: conf, Notes: "Consultó " + p.Code + " (agotado)"}
		_ = b.store.CreateOrder(ctx, o)
		b.Notify("orders")
		text := "😔 El modelo *" + p.Code + " " + p.Name + "* está agotado por ahora."
		inStock := []string{}
		for _, a := range alts {
			if a.TotalStock() > 0 {
				inStock = append(inStock, fmt.Sprintf("*%s* %s — %s", a.Code, a.Name, b.money(a.Price)))
			}
		}
		if len(inStock) > 0 {
			text += "\n\nTe pueden gustar estos que sí tenemos:\n" + strings.Join(inStock, "\n") + "\n\nResponde con el *código* para pedirlo."
		} else {
			text += "\n\nEscribe *1* para ver los modelos disponibles o *4* para que una asesora te avise cuando llegue."
		}
		b.setState(ctx, conv, stIdle, convContext{})
		b.reply(ctx, conv, text)
		return
	}
	// Reutiliza la consulta abierta de esta conversación si la hay.
	next := convContext{ProductID: p.ID, Qty: 1}
	if cc.OrderID > 0 {
		if o, err := b.store.GetOrder(ctx, cc.OrderID); err == nil && (o.Status == "consulta" || o.Status == "pendiente") && !o.StockReserved {
			next.OrderID = o.ID
		}
	}
	item := store.OrderItem{ProductID: &p.ID, ProductCode: p.Code, ProductName: p.Name, Image: p.Image, Qty: 1, UnitPrice: p.Price}
	if next.OrderID > 0 {
		_ = b.store.ReplaceOrderItems(ctx, next.OrderID, []store.OrderItem{item})
		_, _ = b.store.UpdateOrderStatus(ctx, next.OrderID, "consulta", nil)
	} else {
		o := &store.Order{CustomerID: conv.CustomerID, Status: "consulta", Source: "whatsapp", CustomerImage: customerImage,
			MatchConfidence: conf, Items: []store.OrderItem{item}}
		if err := b.store.CreateOrder(ctx, o); err == nil {
			next.OrderID = o.ID
		}
	}
	b.Notify("orders")

	var sizes []string
	for _, v := range p.Variants {
		if v.Stock > 0 {
			sizes = append(sizes, fmt.Sprintf("%s (%d)", v.Size, v.Stock))
		}
	}
	intro := "✨ ¡Lo tenemos!"
	if customerImage != "" {
		intro = "✨ ¡Encontré tu modelo!"
	}
	caption := fmt.Sprintf("%s\n*%s — %s*\n%s\n💰 %s\n📏 Tallas disponibles: %s\n\n¿Qué *talla* deseas?",
		intro, p.Code, p.Name, p.Description, b.money(p.Price), strings.Join(sizes, ", "))
	b.setState(ctx, conv, stSize, next)
	if p.Image != "" {
		b.replyImage(ctx, conv, b.cfg.MediaBaseURL+p.Image, caption)
		return
	}
	b.reply(ctx, conv, caption)
}

var sizeAliases = map[string]string{
	"chica": "S", "pequena": "S", "small": "S", "mediana": "M", "medium": "M", "grande": "L", "large": "L",
	"extra grande": "XL", "xl": "XL", "xs": "XS", "unica": "UNICA", "talla unica": "UNICA", "estandar": "UNICA", "standard": "UNICA",
}

func parseSize(text string, p *store.Product) *store.Variant {
	t := normalize(text)
	t = strings.TrimPrefix(t, "talla ")
	for alias, sz := range sizeAliases {
		if hasAny(t, alias) {
			if v := p.VariantBySize(sz); v != nil {
				return v
			}
		}
	}
	for _, w := range strings.Fields(t) {
		if v := p.VariantBySize(w); v != nil {
			return v
		}
	}
	return nil
}

func parseQty(text string) int {
	words := map[string]int{"uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5}
	t := normalize(text)
	for w, n := range words {
		if hasAny(t, w) {
			return n
		}
	}
	if m := reNumber.FindStringSubmatch(t); m != nil {
		n, _ := strconv.Atoi(m[1])
		return n
	}
	return 0
}

// withoutWord quita una palabra suelta (la talla) para que "2 en talla 38" no lea 38 como cantidad.
func withoutWord(text, word string) string {
	var out []string
	for _, w := range strings.Fields(text) {
		if w != word {
			out = append(out, w)
		}
	}
	return strings.Join(out, " ")
}

func (b *Bot) handleSize(ctx context.Context, conv *store.Conversation, cc *convContext, text string) {
	p, err := b.store.GetProduct(ctx, cc.ProductID)
	if err != nil {
		b.sendMenu(ctx, conv)
		return
	}
	if isAny(text, "no", "cancelar", "ninguno") {
		b.cancelDraft(ctx, conv, cc)
		return
	}
	v := parseSize(text, p)
	if v == nil && len(p.Variants) == 1 && (isAny(text, "si", "ok", "dale") || parseQty(text) > 0) {
		v = &p.Variants[0]
	}
	if v == nil {
		if m := reCode.FindStringSubmatch(text); m != nil {
			if other, err := b.store.GetProductByCode(ctx, strings.ReplaceAll(m[1], "-", "")); err == nil && other.ID != p.ID {
				b.offerProduct(ctx, conv, cc, other, "", 0, nil)
				return
			}
		}
		sizes := []string{}
		for _, x := range p.Variants {
			if x.Stock > 0 {
				sizes = append(sizes, "*"+x.Size+"*")
			}
		}
		b.reply(ctx, conv, "¿Qué talla deseas? Tenemos: "+strings.Join(sizes, ", ")+"\n(Escribe *menu* para volver al inicio.)")
		return
	}
	if v.Stock <= 0 {
		b.reply(ctx, conv, "😔 La talla *"+v.Size+"* se agotó. ¿Te interesa otra talla?")
		return
	}
	qty := parseQty(withoutWord(text, strings.ToLower(v.Size)))
	if qty <= 0 {
		qty = 1
	}
	if qty > v.Stock {
		b.reply(ctx, conv, fmt.Sprintf("Solo nos quedan *%d* en talla %s. ¿Cuántas deseas?", v.Stock, v.Size))
		cc.Size = v.Size
		b.setState(ctx, conv, stSize, *cc)
		return
	}
	cc.Size, cc.Qty = v.Size, qty
	b.sendSummary(ctx, conv, cc, p, v)
}

func (b *Bot) sendSummary(ctx context.Context, conv *store.Conversation, cc *convContext, p *store.Product, v *store.Variant) {
	item := store.OrderItem{ProductID: &p.ID, VariantID: &v.ID, ProductCode: p.Code, ProductName: p.Name, Image: p.Image,
		Size: v.Size, Qty: cc.Qty, UnitPrice: p.Price}
	if cc.OrderID > 0 {
		if err := b.store.ReplaceOrderItems(ctx, cc.OrderID, []store.OrderItem{item}); err != nil {
			cc.OrderID = 0
		} else {
			_, _ = b.store.UpdateOrderStatus(ctx, cc.OrderID, "pendiente", nil)
		}
	}
	if cc.OrderID == 0 {
		o := &store.Order{CustomerID: conv.CustomerID, Status: "pendiente", Source: "whatsapp", Items: []store.OrderItem{item}}
		if err := b.store.CreateOrder(ctx, o); err != nil {
			b.reply(ctx, conv, "Uy, tuve un problema registrando tu pedido 🙏. Una asesora te ayudará en breve.")
			return
		}
		cc.OrderID = o.ID
	}
	b.Notify("orders")
	b.setState(ctx, conv, stConfirm, *cc)
	b.reply(ctx, conv, fmt.Sprintf("🧾 *Resumen de tu pedido #%d*\n\n• %s %s\n• Talla: *%s*\n• Cantidad: *%d*\n• Total: *%s*\n\n¿Confirmas tu pedido? Responde *SI* para confirmar o *NO* para cancelar.\n(Para cambiar la cantidad, escribe el número.)",
		cc.OrderID, p.Code, p.Name, v.Size, cc.Qty, b.money(p.Price*float64(cc.Qty))))
}

func (b *Bot) handleConfirm(ctx context.Context, conv *store.Conversation, cc *convContext, text string) {
	yes := isAny(text, "si", "sí", "s", "ok", "dale", "confirmo", "confirmar", "si confirmo", "yes", "claro", "de acuerdo", "listo") || strings.HasPrefix(text, "si ")
	no := isAny(text, "no", "n", "cancelar", "cancela", "no gracias")
	if !yes && !no {
		p, err := b.store.GetProduct(ctx, cc.ProductID)
		if n := parseQty(text); n > 0 && err == nil {
			if v := p.VariantBySize(cc.Size); v != nil {
				if n > v.Stock {
					b.reply(ctx, conv, fmt.Sprintf("Solo nos quedan *%d* en talla %s.", v.Stock, v.Size))
					return
				}
				cc.Qty = n
				b.sendSummary(ctx, conv, cc, p, v)
				return
			}
		}
		if err == nil {
			if v := parseSize(text, p); v != nil && v.Size != cc.Size {
				if v.Stock <= 0 {
					b.reply(ctx, conv, "😔 La talla *"+v.Size+"* se agotó.")
					return
				}
				cc.Size = v.Size
				b.sendSummary(ctx, conv, cc, p, v)
				return
			}
		}
		b.reply(ctx, conv, "Responde *SI* para confirmar tu pedido o *NO* para cancelarlo 🙏")
		return
	}
	if no {
		b.cancelDraft(ctx, conv, cc)
		return
	}
	_, err := b.store.UpdateOrderStatus(ctx, cc.OrderID, "confirmado", nil)
	if errors.Is(err, store.ErrNoStock) {
		b.setState(ctx, conv, stIdle, convContext{})
		b.reply(ctx, conv, "😔 ¡Uy! Justo se acaba de agotar esa talla. Escribe *1* para ver otros modelos o *4* para hablar con una asesora.")
		b.Notify("orders")
		return
	}
	if err != nil {
		log.Printf("bot: confirmar pedido %d: %v", cc.OrderID, err)
		b.reply(ctx, conv, "Tuve un problema confirmando tu pedido 🙏. Una asesora te escribirá en breve.")
		return
	}
	b.Notify("orders")
	b.Notify("products")
	b.setState(ctx, conv, stLocation, *cc)
	b.reply(ctx, conv, fmt.Sprintf("✅ ¡Pedido *#%d* confirmado! 🎉\n\nPara coordinar la entrega, compártenos tu *ubicación* 📍\n(toca el clip 📎 → *Ubicación* → Enviar tu ubicación actual)\no escríbenos tu *dirección completa* con referencia.", cc.OrderID))
}

func (b *Bot) cancelDraft(ctx context.Context, conv *store.Conversation, cc *convContext) {
	if cc.OrderID > 0 {
		_, _ = b.store.UpdateOrderStatus(ctx, cc.OrderID, "cancelado", nil)
		_ = b.store.UpdateOrderNotes(ctx, cc.OrderID, "Cancelado por el cliente en el chat")
		b.Notify("orders")
	}
	b.setState(ctx, conv, stIdle, convContext{})
	b.reply(ctx, conv, "Listo, no registramos el pedido 👌. Si quieres ver otros modelos escribe *1* o envíanos una foto 📸.")
}

func (b *Bot) handleLocation(ctx context.Context, conv *store.Conversation, cc *convContext, in *Incoming, text string) {
	var lat, lng *float64
	address := strings.TrimSpace(in.Text)
	switch {
	case in.HasLocation:
		lat, lng = &in.Lat, &in.Lng
		address = in.LocationTxt
	case len([]rune(address)) >= 10 && !isAny(text, "no se", "no tengo"):
		// Dirección escrita.
	default:
		b.reply(ctx, conv, "📍 Para el envío necesitamos tu *ubicación* (clip 📎 → Ubicación) o tu *dirección completa* con distrito y referencia.")
		return
	}
	if err := b.store.SetOrderLocation(ctx, cc.OrderID, lat, lng, address); err != nil {
		log.Printf("bot: ubicación pedido %d: %v", cc.OrderID, err)
	}
	b.Notify("orders")
	orderID := cc.OrderID
	b.setState(ctx, conv, stIdle, convContext{})
	b.reply(ctx, conv, fmt.Sprintf("🙌 ¡Gracias! Registramos la dirección de tu pedido *#%d*.\nUna asesora te escribirá para coordinar el pago y la entrega. 💖\n\nEscribe *3* cuando quieras revisar el estado de tu pedido.", orderID))
}

func (b *Bot) freeText(ctx context.Context, conv *store.Conversation, cc *convContext, raw string) {
	if b.Agent != nil && strings.TrimSpace(raw) != "" && b.agentReply(ctx, conv, cc, raw) {
		return
	}
	if !b.ai.Enabled() || strings.TrimSpace(raw) == "" {
		b.sendMenu(ctx, conv)
		return
	}
	actx, cancel := context.WithTimeout(ctx, time.Duration(b.cfg.GeminiTimeoutSec)*time.Second)
	defer cancel()
	in, err := b.ai.ClassifyIntent(actx, b.cfg.BusinessName, conv.State, raw, nil)
	if err != nil {
		b.sendMenu(ctx, conv)
		return
	}
	switch in.Intent {
	case "catalogo":
		b.sendCatalog(ctx, conv)
	case "foto":
		b.setState(ctx, conv, stPhoto, *cc)
		b.reply(ctx, conv, "📸 ¡Claro! Envíanos la *foto* del modelo y verificamos el stock al toque.")
	case "pedido_estado":
		b.sendOrderStatus(ctx, conv)
	case "asesora":
		b.handoff(ctx, conv)
	case "codigo":
		if p, err := b.store.GetProductByCode(ctx, in.Code); err == nil && p.Active {
			b.offerProduct(ctx, conv, cc, p, "", 0, nil)
			return
		}
		b.reply(ctx, conv, "No encontré ese código 🤔. Escribe *1* para ver el catálogo.")
	case "saludo", "si", "no", "talla", "cantidad":
		b.sendMenu(ctx, conv)
	default:
		reply := strings.TrimSpace(in.Reply)
		if reply == "" {
			b.sendMenu(ctx, conv)
			return
		}
		b.reply(ctx, conv, reply+"\n\nEscribe *menu* para ver las opciones 😊")
	}
}

// agentReply delega el texto libre al servicio agente. Devuelve false si no respondió,
// para que freeText siga con Gemini.
func (b *Bot) agentReply(ctx context.Context, conv *store.Conversation, cc *convContext, raw string) bool {
	timeout := time.Duration(b.cfg.AgentTimeoutSec) * time.Second
	if timeout <= 0 {
		timeout = 35 * time.Second
	}
	actx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	req := agente.Request{Mensaje: raw, Estado: conv.State, Negocio: b.cfg.BusinessName}
	if conv.Customer != nil {
		req.Cliente = conv.Customer.Name
	}
	// El mensaje actual ya está guardado: se toma el historial previo, sin él.
	if msgs, err := b.store.ListMessages(ctx, conv.ID, 11); err == nil {
		for _, m := range msgs {
			// Los pies de foto cuentan: así el agente sabe qué prendas ya ofreció.
			if (m.Kind != "text" && m.Kind != "image") || strings.TrimSpace(m.Body) == "" {
				continue
			}
			rol := m.Author
			if m.Direction == "in" {
				rol = "cliente"
			}
			req.Historial = append(req.Historial, agente.Turn{Rol: rol, Texto: m.Body})
		}
		if n := len(req.Historial); n > 0 && req.Historial[n-1].Rol == "cliente" && req.Historial[n-1].Texto == raw {
			req.Historial = req.Historial[:n-1]
		}
	}
	r, err := b.Agent.Chat(actx, req)
	if err != nil {
		log.Printf("bot: agente: %v", err)
		return false
	}
	switch r.Accion {
	case "catalogo":
		b.sendCatalog(ctx, conv)
	case "foto":
		b.setState(ctx, conv, stPhoto, *cc)
		b.reply(ctx, conv, "📸 ¡Claro! Envíanos la *foto* del modelo y verificamos el stock al toque.")
	case "pedido_estado":
		b.sendOrderStatus(ctx, conv)
	case "asesora":
		b.handoff(ctx, conv)
	case "codigo":
		p, err := b.store.GetProductByCode(ctx, r.Codigo)
		if err != nil || !p.Active {
			return false
		}
		b.offerProduct(ctx, conv, cc, p, "", 0, nil)
	default:
		if strings.TrimSpace(r.Respuesta) == "" {
			return false
		}
		// Un párrafo por mensaje, como escribe una persona por WhatsApp.
		for _, parte := range strings.Split(r.Respuesta, "\n\n") {
			if parte = strings.TrimSpace(parte); parte != "" {
				b.reply(ctx, conv, parte)
			}
		}
		for _, sg := range r.Sugerencias {
			b.replySuggestion(ctx, conv, sg)
		}
	}
	return true
}

// replySuggestion envía la foto de una prenda sugerida por el agente. Las fotos de la tienda
// las sirve este backend; las del catálogo de 100 modelos, el agente. En el panel ambas se ven
// por /media/ (el nginx del frontend reparte /media/catalogo/ al agente).
func (b *Bot) replySuggestion(ctx context.Context, conv *store.Conversation, sg agente.Sugerencia) {
	if !strings.HasPrefix(sg.Imagen, "/media/") || strings.Contains(sg.Imagen, "..") {
		return
	}
	url := b.cfg.MediaBaseURL + sg.Imagen
	if strings.HasPrefix(sg.Imagen, "/media/catalogo/") {
		url = b.Agent.BaseURL + sg.Imagen
	}
	m := &store.Message{Kind: "image", Body: sg.Pie, Media: sg.Imagen, Author: "bot"}
	if err := b.queueMessage(ctx, conv, m, outJob{image: url, caption: sg.Pie}); err != nil {
		log.Printf("bot: guardar sugerencia: %v", err)
	}
}

// ---------------------------------------------------------------------------
// Envío

func (b *Bot) reply(ctx context.Context, conv *store.Conversation, text string) {
	m := &store.Message{Kind: "text", Body: text, Author: "bot"}
	if err := b.queueMessage(ctx, conv, m, outJob{text: text}); err != nil {
		log.Printf("bot: guardar respuesta: %v", err)
	}
}

func (b *Bot) replyImage(ctx context.Context, conv *store.Conversation, url, caption string) {
	m := &store.Message{Kind: "image", Body: caption, Media: strings.TrimPrefix(url, b.cfg.MediaBaseURL), Author: "bot"}
	if err := b.queueMessage(ctx, conv, m, outJob{image: url, caption: caption}); err != nil {
		log.Printf("bot: guardar respuesta: %v", err)
	}
}

// SendManual encola un mensaje escrito por la asesora desde el panel.
func (b *Bot) SendManual(ctx context.Context, conv *store.Conversation, text string) error {
	return b.queueMessage(ctx, conv, &store.Message{Kind: "text", Body: text, Author: "asesora"}, outJob{text: text})
}

var statusNotices = map[string]string{
	"confirmado": "✅ Tu pedido *#%d* fue confirmado. ¡Gracias por comprar en %s!",
	"preparando": "✂️ Tu pedido *#%d* está en preparación. Te avisaremos cuando salga. — %s",
	"enviado":    "🚚 ¡Tu pedido *#%d* va en camino! Pronto lo tendrás contigo. — %s",
	"entregado":  "🎉 Tu pedido *#%d* fue entregado. ¡Gracias por elegir %s! 💖",
	"cancelado":  "Tu pedido *#%d* fue cancelado. Si tienes dudas, escríbenos. — %s",
}

// NotifyStatus avisa al cliente del cambio de estado de su pedido (si está activado en ajustes).
func (b *Bot) NotifyStatus(ctx context.Context, o *store.Order) {
	if b.store.Setting(ctx, "notify_status_changes", "true") != "true" {
		return
	}
	tpl, ok := statusNotices[o.Status]
	if !ok || o.ConversationID == 0 {
		return
	}
	conv, err := b.store.GetConversation(ctx, o.ConversationID)
	if err != nil {
		return
	}
	b.reply(ctx, conv, fmt.Sprintf(tpl, o.ID, b.cfg.BusinessName))
}
