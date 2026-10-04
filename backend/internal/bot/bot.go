// Package bot implementa el menú conversacional de WhatsApp: catálogo, consulta por foto,
// verificación de stock, pedido, confirmación y ubicación.
package bot

import (
	"context"
	"crypto/rand"
	"encoding/base64"
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

// reserveTTL es cuánto se aparta la talla mientras la clienta confirma el resumen.
const reserveTTL = 10 * time.Minute

// Estados de la conversación.
const (
	stIdle     = ""
	stPhoto    = "esperando_foto"
	stSize     = "esperando_talla"
	stConfirm  = "esperando_confirmacion"
	stLocation = "esperando_ubicacion"
	// stPayment: pedido confirmado; falta acordar el envío, pagar y mandar el comprobante. Solo con agente.
	stPayment    = "esperando_pago"
	stHumanAsked = "asesora"
)

type convContext struct {
	ProductID int64  `json:"product_id,omitempty"`
	Size      string `json:"size,omitempty"`
	Qty       int    `json:"qty,omitempty"`
	OrderID   int64  `json:"order_id,omitempty"`
	// Etapa comercial que decidió el agente: prospeccion | seguimiento | cierre | venta_confirmada.
	Etapa   string `json:"etapa,omitempty"`
	Voucher bool   `json:"voucher,omitempty"` // ya mandó el comprobante de pago
	Address bool   `json:"address,omitempty"` // ya dio la dirección de envío
	// Llegó desde un anuncio de clic a WhatsApp (y su título): se recuerda toda la conversación.
	Anuncio      bool   `json:"anuncio,omitempty"`
	AnuncioTitle string `json:"anuncio_title,omitempty"`
	// Memoria de la conversación (ver memoria.go): lo que ya sabemos de la clienta y la pregunta pendiente.
	// La actualiza el agente; sobrevive a los reinicios del flujo, como Anuncio.
	Memoria json.RawMessage `json:"memoria,omitempty"`
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
	if in.FromAd && (!cc.Anuncio || (in.AdTitle != "" && in.AdTitle != cc.AnuncioTitle)) {
		cc.Anuncio, cc.AnuncioTitle = true, firstNonEmpty(in.AdTitle, cc.AnuncioTitle)
		b.setState(ctx, conv, conv.State, cc)
	}

	// Comandos globales. Con agente, el saludo lo contesta él como una persona; el menú sigue a mano
	// con «menu» o «0».
	saludo := isAny(text, "hola", "buenas", "buenos dias", "buenas tardes", "buenas noches", "hi")
	if (isAny(text, "menu", "inicio", "0", "volver") || (saludo && b.Agent == nil)) && !in.HasImage {
		b.sendMenu(ctx, conv)
		return
	}

	// Con el pedido confirmado, la foto que llega es el comprobante de pago. Cualquier otra foto inicia
	// una consulta de modelo.
	if in.HasImage {
		if conv.State == stPayment {
			b.handleVoucher(ctx, conv, &cc)
			return
		}
		b.handlePhoto(ctx, conv, &cc, msg, img)
		return
	}

	switch conv.State {
	case stSize:
		b.handleSize(ctx, conv, &cc, text, in.Text)
		return
	case stConfirm:
		b.handleConfirm(ctx, conv, &cc, text, in.Text)
		return
	case stPayment:
		b.handlePayment(ctx, conv, &cc, in, text)
		return
	case stLocation:
		b.handleLocation(ctx, conv, &cc, in, text)
		return
	}

	if in.HasLocation {
		b.reply(ctx, conv, "📍 ¡Gracias por tu ubicación! Si deseas hacer un pedido, escribe *1* para ver el catálogo o envíanos la foto del modelo.")
		return
	}

	// Con agente, sólo los números del menú van directo a su flujo; las frases las clasifica el agente.
	// Las palabras sueltas se equivocaban: «cómo hago mi pedido» caía en el estado del pedido.
	sinAgente := b.Agent == nil
	switch {
	case isAny(text, "1") || (sinAgente && isAny(text, "catalogo", "ver catalogo", "precios", "modelos")):
		b.sendCatalog(ctx, conv)
		return
	// «quiero ver su catálogo» en una frase: con agente lo presenta él, con fotos; sin agente, la lista.
	case hasAny(text, "catalogo") && sinAgente:
		b.sendCatalog(ctx, conv)
		return
	case isAny(text, "2") || (sinAgente && hasAny(text, "foto", "consultar modelo")):
		b.setState(ctx, conv, stPhoto, cc)
		b.reply(ctx, conv, "📸 ¡Perfecto! Envíanos la *foto* del modelo que te gustó y verificamos si lo tenemos en stock.")
		return
	case isAny(text, "3") || (sinAgente && hasAny(text, "mi pedido", "estado")):
		b.sendOrderStatus(ctx, conv)
		return
	case isAny(text, "4", "asesora", "asesor", "humano", "persona"):
		b.handoff(ctx, conv)
		return
	}
	// El código solo («V42», «el V42») es pedirlo: va al flujo del pedido. Dentro de una frase («¿de qué
	// tela es el V42?») es una pregunta, y preguntar no es comprar: la contesta el agente.
	if m := reCode.FindStringSubmatch(in.Text); m != nil && (sinAgente || len(strings.Fields(text)) <= 2) {
		if p, err := b.store.GetProductByCode(ctx, strings.ReplaceAll(m[1], "-", "")); err == nil && p.Active {
			b.offerProduct(ctx, conv, &cc, p, "", 0, nil)
			return
		}
	}
	b.freeText(ctx, conv, &cc, in.Text)
}

func (b *Bot) setState(ctx context.Context, conv *store.Conversation, state string, cc convContext) {
	var prev convContext
	_ = json.Unmarshal([]byte(conv.Context), &prev)
	// Que llegó por un anuncio vale para toda la conversación: los reinicios del flujo (menú, cancelar,
	// pedido cerrado) no lo borran.
	if !cc.Anuncio && prev.Anuncio {
		cc.Anuncio, cc.AnuncioTitle = true, prev.AnuncioTitle
	}
	// La memoria tampoco: un reinicio (convContext nuevo) conserva lo que sabemos de la clienta, pero suelta la
	// pregunta pendiente, porque el flujo cambió. Al entrar en un estado del flujo de Go, la pendiente es la
	// de ese estado (talla, confirmar, envío, dirección).
	reinicio := len(cc.Memoria) == 0 && len(prev.Memoria) > 0
	if reinicio {
		cc.Memoria = prev.Memoria
	}
	if p, ok := pendientePorEstado[state]; ok && (state != conv.State || reinicio || len(cc.Memoria) == 0) {
		cc.Memoria = memConPendiente(cc.Memoria, p)
	} else if reinicio {
		cc.Memoria = memConPendiente(cc.Memoria, "")
	}
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
		if p.TotalAvailable() == 0 {
			continue
		}
		sizes := []string{}
		for _, v := range p.Variants {
			if v.Available() > 0 {
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
	if img != nil && b.Agent != nil && b.agentPhoto(ctx, conv, cc, msg, img, inquiry) {
		return
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
	if p.TotalAvailable() == 0 {
		o := &store.Order{CustomerID: conv.CustomerID, Status: "consulta", Source: "whatsapp", CustomerImage: customerImage,
			MatchConfidence: conf, Notes: "Consultó " + p.Code + " (agotado)"}
		_ = b.store.CreateOrder(ctx, o)
		b.Notify("orders")
		text := "😔 El modelo *" + p.Code + " " + p.Name + "* está agotado por ahora."
		inStock := []string{}
		for _, a := range alts {
			if a.TotalAvailable() > 0 {
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
	next := b.draftFor(ctx, conv, cc, p, customerImage, conf)
	next.Memoria = memEditar(memoriaActual(conv, cc), func(m map[string]any) { m["producto"] = p.Code })

	var sizes []string
	for _, v := range p.Variants {
		if n := v.Available(); n > 0 {
			sizes = append(sizes, fmt.Sprintf("%s (%d)", v.Size, n))
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

// draftFor deja la consulta del pedido con el producto p (reutiliza la abierta de la conversación si la
// hay) y devuelve el contexto con el que sigue el flujo de talla.
func (b *Bot) draftFor(ctx context.Context, conv *store.Conversation, cc *convContext, p *store.Product, customerImage string, conf float64) convContext {
	next := convContext{ProductID: p.ID, Qty: 1, Etapa: "cierre"} // armar el pedido ya es cerrar
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
	return next
}

// orderWithSize: la clienta ya dijo modelo y talla («el Kabanova rojo en L»): se arma el pedido y se va
// directo al resumen, sin volver a preguntar la talla. Si la talla no sirve, handleSize vuelve a pedirla.
func (b *Bot) orderWithSize(ctx context.Context, conv *store.Conversation, cc *convContext, p *store.Product, size string) {
	if p.TotalAvailable() == 0 {
		b.offerProduct(ctx, conv, cc, p, "", 0, nil)
		return
	}
	next := b.draftFor(ctx, conv, cc, p, "", 0)
	b.setState(ctx, conv, stSize, next)
	b.handleSize(ctx, conv, &next, normalize(size), "") // sin texto libre: si la talla no sirve, se pregunta
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

// handleSize espera la talla. `raw` es el mensaje tal como llegó: si no es una talla sino una pregunta o
// un comentario, lo contesta el agente y se recuerda el paso pendiente, en vez de repetir «¿Qué talla?».
func (b *Bot) handleSize(ctx context.Context, conv *store.Conversation, cc *convContext, text, raw string) {
	p, err := b.store.GetProduct(ctx, cc.ProductID)
	if err != nil {
		b.sendMenu(ctx, conv)
		return
	}
	if isAny(text, "no", "cancelar", "ninguno") {
		b.cancelDraft(ctx, conv, cc)
		return
	}
	recordar := func() string {
		sizes := []string{}
		for _, x := range p.Variants {
			if x.Available() > 0 {
				sizes = append(sizes, "*"+x.Size+"*")
			}
		}
		return "Cuando quieras, dime tu talla y te lo separo 😊 Tenemos: " + strings.Join(sizes, ", ")
	}
	// «¿la M me quedará si mido 1.60?» nombra una talla pero es una duda, no una elección.
	if esPregunta(text, raw) {
		if r := b.askAgent(ctx, conv, cc, raw); r != nil && b.closingReply(ctx, conv, cc, r, p, "talla", recordar()) {
			return
		}
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
			if x.Available() > 0 {
				sizes = append(sizes, "*"+x.Size+"*")
			}
		}
		if r := b.askAgent(ctx, conv, cc, raw); r != nil && b.closingReply(ctx, conv, cc, r, p, "talla", recordar()) {
			return
		}
		b.fijarPendiente(ctx, conv, cc, "talla")
		b.reply(ctx, conv, "¿Qué talla deseas? Tenemos: "+strings.Join(sizes, ", ")+"\n(Escribe *menu* para volver al inicio.)")
		return
	}
	if v.Available() <= 0 {
		b.reply(ctx, conv, "😔 La talla *"+v.Size+"* se agotó. ¿Te interesa otra talla?")
		return
	}
	qty := parseQty(withoutWord(text, strings.ToLower(v.Size)))
	if qty <= 0 {
		qty = 1
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
	// Se aparta la talla unos minutos: otra clienta que pregunte ahora ya no la ve disponible.
	if avail, err := b.store.Reserve(ctx, cc.OrderID, v.ID, cc.Qty, reserveTTL); err != nil {
		if errors.Is(err, store.ErrNoStock) {
			cc.Size, cc.Qty = "", 0
			b.setState(ctx, conv, stSize, *cc)
			if avail <= 0 {
				b.reply(ctx, conv, "😔 Justo se nos acaba de reservar la talla *"+v.Size+"*. ¿Te interesa otra talla?")
			} else {
				b.reply(ctx, conv, fmt.Sprintf("Solo nos quedan *%d* disponibles en talla %s. ¿Cuántas deseas?", avail, v.Size))
			}
			return
		}
		log.Printf("bot: reservar pedido %d: %v", cc.OrderID, err)
	}
	b.Notify("orders")
	cc.Memoria = memSabemos(memoriaActual(conv, cc), "talla", v.Size) // con el resumen, la pendiente es «confirmar»
	b.setState(ctx, conv, stConfirm, *cc)
	b.reply(ctx, conv, fmt.Sprintf("🧾 *Resumen de tu pedido #%d*\n\n• %s %s\n• Talla: *%s*\n• Cantidad: *%d*\n• Total: *%s*\n\nTe la apartamos por *%d minutos* ⏳\n¿Confirmas tu pedido? Responde *SI* para confirmar o *NO* para cancelar.\n(Para cambiar la cantidad, escribe el número.)",
		cc.OrderID, p.Code, p.Name, v.Size, cc.Qty, b.money(p.Price*float64(cc.Qty)), int(reserveTTL.Minutes())))
}

func (b *Bot) handleConfirm(ctx context.Context, conv *store.Conversation, cc *convContext, text, raw string) {
	yes := isAny(text, "si", "sí", "s", "ok", "dale", "confirmo", "confirmar", "si confirmo", "yes", "claro", "de acuerdo", "listo") || strings.HasPrefix(text, "si ")
	no := isAny(text, "no", "n", "cancelar", "cancela", "no gracias")
	if !yes && !no {
		p, err := b.store.GetProduct(ctx, cc.ProductID)
		// Cambiar cantidad o talla vuelve a reservar; sendSummary avisa si ya no alcanza. Solo en mensajes
		// cortos: «¿el envío cuesta 15 soles?» trae un número y no es una cantidad.
		corto := len(strings.Fields(text)) <= 4 && !esPregunta(text, raw)
		if n := parseQty(text); n > 0 && err == nil && corto {
			if v := p.VariantBySize(cc.Size); v != nil {
				cc.Qty = n
				b.sendSummary(ctx, conv, cc, p, v)
				return
			}
		}
		if err == nil && corto {
			if v := parseSize(text, p); v != nil && v.Size != cc.Size {
				cc.Size = v.Size
				b.sendSummary(ctx, conv, cc, p, v)
				return
			}
		}
		// No es sí ni no: una duda («¿hacen envíos a Cusco?»), una objeción o un «sí» dicho de otra forma.
		// Lo resuelve el agente; el bot ya no se queda repitiendo «Responde SI o NO».
		if r := b.askAgent(ctx, conv, cc, raw); r != nil && err == nil {
			if r.Etapa == "venta_confirmada" {
				b.confirmOrder(ctx, conv, cc)
				return
			}
			if b.closingReply(ctx, conv, cc, r, p, "confirm", "Cuando estés lista, responde *SI* y confirmo tu pedido 😊") {
				return
			}
		}
		b.fijarPendiente(ctx, conv, cc, "confirmar")
		b.reply(ctx, conv, "Responde *SI* para confirmar tu pedido o *NO* para cancelarlo 🙏")
		return
	}
	if no {
		b.cancelDraft(ctx, conv, cc)
		return
	}
	b.confirmOrder(ctx, conv, cc)
}

// confirmOrder pasa el pedido a confirmado (descuenta el stock). Con agente sigue la venta: envío, total,
// pago y comprobante. Sin agente pide la ubicación y deja el pago a una asesora.
func (b *Bot) confirmOrder(ctx context.Context, conv *store.Conversation, cc *convContext) {
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
	if b.Agent != nil {
		cc.Etapa = "venta_confirmada"
		b.setState(ctx, conv, stPayment, *cc)
		b.reply(ctx, conv, fmt.Sprintf("✅ ¡Pedido *#%d* confirmado! 🎉 Ya quedó separado para ti.", cc.OrderID))
		b.reply(ctx, conv, "¿El envío sería para *Lima* o para *provincia*? 🚚")
		return
	}
	b.setState(ctx, conv, stLocation, *cc)
	b.reply(ctx, conv, fmt.Sprintf("✅ ¡Pedido *#%d* confirmado! 🎉\n\nPara coordinar la entrega, compártenos tu *ubicación* 📍\n(toca el clip 📎 → *Ubicación* → Enviar tu ubicación actual)\no escríbenos tu *dirección completa* con referencia.", cc.OrderID))
}

// esPregunta: el mensaje es una duda o un comentario largo, no una respuesta corta al paso del pedido.
func esPregunta(text, raw string) bool {
	return strings.ContainsAny(raw, "?¿") || len(strings.Fields(text)) > 6
}

// closingReply entrega la respuesta del agente a un mensaje libre recibido en pleno cierre (esperando
// talla o confirmación). Si la clienta sigue en el cierre, se le recuerda el paso pendiente; si dudó o
// pidió ver otra prenda, se sale del pedido sin presionar. Devuelve false si no hubo nada que enviar.
func (b *Bot) closingReply(ctx context.Context, conv *store.Conversation, cc *convContext, r *agente.Reply, p *store.Product, clave, recordatorio string) bool {
	if r.Etapa == "venta_confirmada" {
		// Dijo «sí» a una pregunta del agente cuando aún no hay resumen que confirmar (esperando talla).
		// No se da por vendida: con la talla se arma el resumen; sin ella, se pide.
		if strings.TrimSpace(r.Talla) != "" {
			b.orderWithSize(ctx, conv, cc, p, r.Talla)
		} else {
			b.fijarPendiente(ctx, conv, cc, pendientePorEstado[conv.State])
			b.reply(ctx, conv, recordatorio)
		}
		return true
	}
	switch r.Accion {
	case "asesora":
		b.handoff(ctx, conv)
		return true
	case "pedido", "codigo":
		other, err := b.store.GetProductByCode(ctx, r.Codigo)
		if err != nil || !other.Active {
			return false
		}
		if r.Accion == "pedido" && strings.TrimSpace(r.Talla) != "" {
			b.orderWithSize(ctx, conv, cc, other, r.Talla)
		} else {
			b.offerProduct(ctx, conv, cc, other, "", 0, nil)
		}
		return true
	}
	if strings.TrimSpace(r.Respuesta) == "" {
		return false
	}
	otraPrenda := false
	for _, sg := range r.Sugerencias {
		if sg.Codigo != "" && !strings.EqualFold(sg.Codigo, p.Code) {
			otraPrenda = true
		}
	}
	if otraPrenda || (r.Etapa != "" && r.Etapa != "cierre" && r.Etapa != "venta_confirmada") {
		b.pauseDraft(ctx, conv, cc, r.Etapa)
		b.sendAgentText(ctx, conv, r)
		return true
	}
	b.sendAgentText(ctx, conv, r)
	if !strings.Contains(strings.ToLower(r.Respuesta), clave) {
		b.reply(ctx, conv, recordatorio)
	}
	// Sigue en el cierre: lo que se espera es la talla o el SI, pregunte lo que pregunte el agente.
	b.fijarPendiente(ctx, conv, cc, pendientePorEstado[conv.State])
	return true
}

// pauseDraft saca la conversación del cierre sin cancelar nada: la clienta dudó («lo voy a pensar») o
// pidió ver otros modelos. Se libera la talla apartada y el pedido vuelve a consulta; si regresa, se retoma.
func (b *Bot) pauseDraft(ctx context.Context, conv *store.Conversation, cc *convContext, etapa string) {
	if cc.OrderID > 0 {
		_ = b.store.ReleaseReservation(ctx, cc.OrderID)
		if o, err := b.store.GetOrder(ctx, cc.OrderID); err == nil && o.Status == "pendiente" {
			_, _ = b.store.UpdateOrderStatus(ctx, cc.OrderID, "consulta", nil)
		}
		b.Notify("orders")
	}
	if etapa == "" || etapa == "cierre" || etapa == "venta_confirmada" {
		etapa = "seguimiento"
	}
	cc.Etapa = etapa
	b.setState(ctx, conv, stIdle, *cc)
}

// handlePayment: pedido confirmado. El agente lleva los pasos (Lima o provincia → total → datos de pago →
// comprobante) y contesta lo que pregunte en el camino.
func (b *Bot) handlePayment(ctx context.Context, conv *store.Conversation, cc *convContext, in *Incoming, text string) {
	if in.HasLocation {
		if err := b.store.SetOrderLocation(ctx, cc.OrderID, &in.Lat, &in.Lng, in.LocationTxt); err != nil {
			log.Printf("bot: ubicación pedido %d: %v", cc.OrderID, err)
		}
		cc.Address = true
		cc.Memoria = memConPendiente(memoriaActual(conv, cc), "voucher")
		b.setState(ctx, conv, stPayment, *cc)
		b.Notify("orders")
		b.reply(ctx, conv, "📍 ¡Anotado! Ya tengo la dirección de tu envío.\n\nCuando hagas el pago, envíame la *foto del comprobante* y lo programamos 🙌")
		return
	}
	switch {
	case isAny(text, "3"):
		b.sendOrderStatus(ctx, conv)
		return
	case isAny(text, "4", "asesora", "asesor", "humano", "persona"):
		b.handoff(ctx, conv)
		return
	}
	cc.Etapa = "venta_confirmada"
	r := b.askAgent(ctx, conv, cc, in.Text)
	if r == nil || (r.Accion == "responder" && strings.TrimSpace(r.Respuesta) == "") {
		b.reply(ctx, conv, fmt.Sprintf("Una asesora te escribirá enseguida para coordinar el pago y el envío de tu pedido *#%d* 💖", cc.OrderID))
		return
	}
	if r.Accion != "responder" && r.Accion != "" {
		b.dispatchAgent(ctx, conv, cc, r)
		return
	}
	if r.Etapa != "" && r.Etapa != "venta_confirmada" {
		// Se arrepintió con el pedido ya confirmado (y el stock descontado): lo decide una persona.
		b.addOrderNote(ctx, cc.OrderID, "⚠️ La clienta pidió cancelar por el chat después de confirmar. Revisar.")
		b.setState(ctx, conv, stIdle, convContext{Etapa: r.Etapa, Memoria: cc.Memoria})
		b.Notify("orders")
	}
	b.sendAgentText(ctx, conv, r)
}

// handleVoucher: la foto que llega con el pedido confirmado es el comprobante de pago. Queda anotado en
// el pedido para que una asesora lo valide, y se pide la dirección si aún falta.
func (b *Bot) handleVoucher(ctx context.Context, conv *store.Conversation, cc *convContext) {
	b.addOrderNote(ctx, cc.OrderID, "💳 Comprobante de pago recibido por WhatsApp (la foto está en la conversación). Falta validarlo.")
	b.Notify("orders")
	if cc.Address {
		orderID := cc.OrderID
		b.setState(ctx, conv, stIdle, convContext{})
		b.reply(ctx, conv, fmt.Sprintf("🙌 ¡Gracias! Recibimos tu comprobante del pedido *#%d*.\nApenas lo validemos programamos tu envío y te avisamos por aquí 💖", orderID))
		return
	}
	cc.Voucher = true
	b.setState(ctx, conv, stLocation, *cc)
	b.reply(ctx, conv, "🙌 ¡Gracias! Recibimos tu comprobante. Una asesora lo valida y te confirma por aquí.")
	b.reply(ctx, conv, "Para programar tu envío 🚚 compárteme tu *dirección completa* con distrito y referencia, o tu *ubicación* 📍 (clip 📎 → Ubicación).")
}

func (b *Bot) addOrderNote(ctx context.Context, orderID int64, note string) {
	if orderID <= 0 {
		return
	}
	if o, err := b.store.GetOrder(ctx, orderID); err == nil && strings.TrimSpace(o.Notes) != "" {
		note = o.Notes + "\n" + note
	}
	if err := b.store.UpdateOrderNotes(ctx, orderID, note); err != nil {
		log.Printf("bot: nota del pedido %d: %v", orderID, err)
	}
}

func (b *Bot) cancelDraft(ctx context.Context, conv *store.Conversation, cc *convContext) {
	if cc.OrderID > 0 {
		_ = b.store.ReleaseReservation(ctx, cc.OrderID)
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
	// Una pregunta no es una dirección: «¿cuándo llega?» la contesta el agente y se vuelve a pedir.
	if !in.HasLocation && b.Agent != nil && strings.ContainsAny(in.Text, "?¿") {
		if cc.Etapa == "" {
			cc.Etapa = "venta_confirmada"
		}
		if r := b.askAgent(ctx, conv, cc, in.Text); r != nil && r.Accion == "responder" && strings.TrimSpace(r.Respuesta) != "" {
			b.sendAgentText(ctx, conv, r)
			b.reply(ctx, conv, "Y para el envío, compárteme tu *dirección completa* con distrito y referencia, o tu *ubicación* 📍")
			b.fijarPendiente(ctx, conv, cc, "direccion")
			return
		}
	}
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
	orderID, pagado := cc.OrderID, cc.Voucher
	b.setState(ctx, conv, stIdle, convContext{})
	if pagado {
		b.reply(ctx, conv, fmt.Sprintf("🙌 ¡Gracias! Registramos la dirección de tu pedido *#%d*.\nApenas validemos tu pago programamos el envío y te avisamos por aquí 💖\n\nEscribe *3* cuando quieras revisar el estado de tu pedido.", orderID))
		return
	}
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
func (b *Bot) agentContext(ctx context.Context) (context.Context, context.CancelFunc) {
	timeout := time.Duration(b.cfg.AgentTimeoutSec) * time.Second
	if timeout <= 0 {
		timeout = 35 * time.Second
	}
	return context.WithTimeout(ctx, timeout)
}

// agentHistory devuelve los últimos turnos de texto previos al mensaje actual, que ya está
// guardado (si coincide con `current`, se descarta).
func (b *Bot) agentHistory(ctx context.Context, conv *store.Conversation, current string) []agente.Turn {
	var out []agente.Turn
	msgs, err := b.store.ListMessages(ctx, conv.ID, 11)
	if err != nil {
		return nil
	}
	for _, m := range msgs {
		// Los pies de foto cuentan: así el agente sabe qué prendas ya ofreció.
		if (m.Kind != "text" && m.Kind != "image") || strings.TrimSpace(m.Body) == "" {
			continue
		}
		rol := m.Author
		if m.Direction == "in" {
			rol = "cliente"
		}
		out = append(out, agente.Turn{Rol: rol, Texto: m.Body})
	}
	if n := len(out); n > 0 && out[n-1].Rol == "cliente" && out[n-1].Texto == current {
		out = out[:n-1]
	}
	return out
}

func (b *Bot) customerName(conv *store.Conversation) string {
	if conv.Customer != nil {
		return conv.Customer.Name
	}
	return ""
}

// askAgent consulta al agente con la etapa comercial y el pedido en curso. nil si no hay agente o no respondió.
func (b *Bot) askAgent(ctx context.Context, conv *store.Conversation, cc *convContext, raw string) *agente.Reply {
	if b.Agent == nil || strings.TrimSpace(raw) == "" {
		return nil
	}
	actx, cancel := b.agentContext(ctx)
	defer cancel()
	req := agente.Request{Mensaje: raw, Estado: conv.State, Negocio: b.cfg.BusinessName,
		Cliente: b.customerName(conv), Historial: b.agentHistory(ctx, conv, raw),
		Etapa: cc.Etapa, Conversacion: strconv.FormatInt(conv.ID, 10), Talla: cc.Size,
		DesdeAnuncio: cc.Anuncio, Anuncio: cc.AnuncioTitle,
		Memoria: memoriaActual(conv, cc), Perfil: b.perfil(ctx, conv, cc)}
	if conv.State != stIdle && cc.ProductID > 0 {
		if p, err := b.store.GetProduct(ctx, cc.ProductID); err == nil {
			req.Producto = p.Code
		}
	}
	r, err := b.Agent.Chat(actx, req)
	if err != nil {
		log.Printf("bot: agente: %v", err)
		return nil
	}
	b.guardarMemoria(ctx, conv, cc, r)
	return r
}

// guardarMemoria guarda la memoria que devolvió el agente, en el mismo estado (sin tocar su pendiente): pase
// lo que pase después (catálogo, menú, pedido), la siguiente petición la lleva.
func (b *Bot) guardarMemoria(ctx context.Context, conv *store.Conversation, cc *convContext, r *agente.Reply) {
	if len(r.Memoria) == 0 || string(r.Memoria) == "null" {
		return
	}
	cc.Memoria = r.Memoria
	b.setState(ctx, conv, conv.State, *cc)
}

func (b *Bot) agentReply(ctx context.Context, conv *store.Conversation, cc *convContext, raw string) bool {
	r := b.askAgent(ctx, conv, cc, raw)
	if r == nil {
		return false
	}
	return b.dispatchAgent(ctx, conv, cc, r)
}

// dispatchAgent ejecuta lo que decidió el agente y guarda la etapa comercial en la que queda la conversación.
func (b *Bot) dispatchAgent(ctx context.Context, conv *store.Conversation, cc *convContext, r *agente.Reply) bool {
	if r.Etapa != "" {
		cc.Etapa = r.Etapa
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
	case "pedido":
		p, err := b.store.GetProductByCode(ctx, r.Codigo)
		if err != nil || !p.Active || strings.TrimSpace(r.Talla) == "" {
			return false
		}
		b.orderWithSize(ctx, conv, cc, p, r.Talla)
	default:
		if strings.TrimSpace(r.Respuesta) == "" {
			return false
		}
		b.setState(ctx, conv, conv.State, *cc) // la etapa viaja con la conversación
		b.sendAgentText(ctx, conv, r)
	}
	return true
}

// sendAgentText manda un párrafo por mensaje, como escribe una persona por WhatsApp, y
// después las fotos sugeridas.
func (b *Bot) sendAgentText(ctx context.Context, conv *store.Conversation, r *agente.Reply) {
	for _, parte := range strings.Split(r.Respuesta, "\n\n") {
		if parte = strings.TrimSpace(parte); parte != "" {
			b.reply(ctx, conv, parte)
		}
	}
	for _, sg := range r.Sugerencias {
		b.replySuggestion(ctx, conv, sg)
	}
}

// agentPhoto busca la prenda de la foto con el agente (embeddings de imagen locales).
// Devuelve false si el agente no respondió, para que handlePhoto siga con Gemini.
func (b *Bot) agentPhoto(ctx context.Context, conv *store.Conversation, cc *convContext, msg *store.Message, img *ai.Image, inquiry func(string, float64)) bool {
	actx, cancel := b.agentContext(ctx)
	defer cancel()
	req := agente.PhotoRequest{ImagenB64: base64.StdEncoding.EncodeToString(img.Data), Mensaje: msg.Body,
		Estado: conv.State, Negocio: b.cfg.BusinessName, Cliente: b.customerName(conv),
		Historial: b.agentHistory(ctx, conv, msg.Body), Etapa: cc.Etapa, DesdeAnuncio: cc.Anuncio, Anuncio: cc.AnuncioTitle,
		Memoria: memoriaActual(conv, cc), Perfil: b.perfil(ctx, conv, cc)}
	r, err := b.Agent.Photo(actx, req)
	if err != nil || r.Foto == nil {
		log.Printf("bot: agente foto: %v", err)
		return false
	}
	b.guardarMemoria(ctx, conv, cc, r)
	log.Printf("bot: agente foto=%s %s %s sim=%.3f", msg.Media, r.Foto.Caso, r.Foto.Codigo, r.Foto.Similitud)
	if r.Accion == "codigo" {
		if p, err := b.store.GetProductByCode(ctx, r.Codigo); err == nil && p.Active {
			b.offerProduct(ctx, conv, cc, p, msg.Media, r.Foto.Similitud, nil)
			return true
		}
	}
	// Todo lo que no termina en pedido queda en el tablero para que una asesora haga seguimiento.
	inquiry(fmt.Sprintf("Agente (foto): %s · %s · similitud %.2f", r.Foto.Caso, r.Foto.Codigo, r.Foto.Similitud), r.Foto.Similitud)
	b.setState(ctx, conv, stIdle, convContext{Etapa: firstNonEmpty(r.Etapa, cc.Etapa), Memoria: cc.Memoria})
	b.sendAgentText(ctx, conv, r)
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
