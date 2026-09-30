package bot

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/ai"
	"github.com/davrv93/ddesign-k/backend/internal/config"
	"github.com/davrv93/ddesign-k/backend/internal/evolution"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// fakeEvolution registra lo que el bot envía por WhatsApp.
type fakeEvolution struct {
	mu    sync.Mutex
	sent  []map[string]any
	delay time.Duration // simula evolution-go reintentando mientras WhatsApp reconecta
}

func (f *fakeEvolution) handler(w http.ResponseWriter, r *http.Request) {
	time.Sleep(f.delay)
	var body map[string]any
	_ = json.NewDecoder(r.Body).Decode(&body)
	body["_path"] = r.URL.Path
	f.mu.Lock()
	f.sent = append(f.sent, body)
	n := len(f.sent)
	f.mu.Unlock()
	w.Header().Set("Content-Type", "application/json")
	_, _ = io.WriteString(w, `{"message":"success","data":{"Info":{"ID":"OUT`+strings.Repeat("X", n)+`"}}}`)
}

func (f *fakeEvolution) last() map[string]any {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.sent[len(f.sent)-1]
}

func (f *fakeEvolution) lastText() string {
	m := f.last()
	if t, ok := m["text"].(string); ok {
		return t
	}
	s, _ := m["caption"].(string)
	return s
}

func setup(t *testing.T, geminiReply string, failPrimary bool) (*Bot, *store.Store, *fakeEvolution) {
	t.Helper()
	st, err := store.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.DB.Close() })
	ctx := context.Background()
	for _, p := range []*store.Product{
		{Code: "V01", Name: "Vestido Esmeralda", Price: 119, Active: true, Image: "/media/products/v01.jpg",
			Variants: []store.Variant{{Size: "S", Stock: 1}, {Size: "M", Stock: 2}}},
		{Code: "V07", Name: "Vestido Sirena Celeste", Price: 299, Active: true, Variants: []store.Variant{{Size: "M", Stock: 0}}},
	} {
		if err := st.SaveProduct(ctx, p); err != nil {
			t.Fatal(err)
		}
	}
	fe := &fakeEvolution{}
	evoSrv := httptest.NewServer(http.HandlerFunc(fe.handler))
	t.Cleanup(evoSrv.Close)
	gem := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if failPrimary && strings.Contains(r.URL.Path, "gemini") {
			http.Error(w, `{"error":{"code":429,"message":"quota"}}`, http.StatusTooManyRequests)
			return
		}
		resp := map[string]any{"candidates": []any{map[string]any{"content": map[string]any{"parts": []any{map[string]any{"text": geminiReply}}}}}}
		_ = json.NewEncoder(w).Encode(resp)
	}))
	t.Cleanup(gem.Close)

	cfg := &config.Config{DataDir: t.TempDir(), BusinessName: "Baruka Design", Currency: "S/", MatchThreshold: 0.6,
		GeminiTimeoutSec: 5, MediaBaseURL: "http://backend:8080"}
	aic := ai.New("test-key", "gemini-3.5-flash-lite", "gemma-4-26b-a4b-it,gemma-4-31b-it", 5*time.Second)
	aic.Endpoint = gem.URL + "/"
	b := New(cfg, st, evolution.New(evoSrv.URL, "g", "kddesign", "tok"), aic)
	return b, st, fe
}

// handle procesa un mensaje y espera a que las respuestas salgan por el outbox.
func handle(b *Bot, in *Incoming) {
	b.Handle(context.Background(), in)
	b.Drain(5 * time.Second)
}

var msgSeq int

func text(s string) *Incoming {
	msgSeq++
	return &Incoming{ID: "IN" + strings.Repeat("A", msgSeq), Chat: "51987654321@s.whatsapp.net", Phone: "51987654321", PushName: "Ana López", Text: s}
}

func photo() *Incoming {
	in := text("")
	in.HasImage = true
	in.ImageMime = "image/jpeg"
	in.ImageB64 = "data:image/jpeg;base64," + base64.StdEncoding.EncodeToString([]byte("\xff\xd8\xff fake jpeg"))
	return in
}

func mustContain(t *testing.T, got string, want ...string) {
	t.Helper()
	for _, w := range want {
		if !strings.Contains(got, w) {
			t.Fatalf("se esperaba %q en:\n%s", w, got)
		}
	}
}

func TestFlujoCompletoFotoTallaConfirmaUbicacion(t *testing.T) {
	b, st, fe := setup(t, "```json\n{\"code\":\"V01\",\"confidence\":0.92,\"seen\":\"vestido verde corto\",\"alternatives\":[]}\n```", true)
	ctx := context.Background()

	handle(b, text("Hola"))
	mustContain(t, fe.lastText(), "Hola Ana", "Baruka Design", "1️⃣", "4️⃣")

	handle(b, photo()) // gemini falla (429) → responde gemma
	last := fe.last()
	if last["_path"] != "/send/media" || last["url"] != "http://backend:8080/media/products/v01.jpg" {
		t.Fatalf("se esperaba la foto del catálogo, llegó %v", last)
	}
	mustContain(t, fe.lastText(), "V01", "S/ 119.00", "S (1)", "M (2)", "talla")

	handle(b, text("quiero 2 en talla M"))
	mustContain(t, fe.lastText(), "Resumen", "Talla: *M*", "Cantidad: *2*", "S/ 238.00", "SI")

	handle(b, text("sí"))
	mustContain(t, fe.lastText(), "confirmado", "ubicación")
	p, _ := st.GetProductByCode(ctx, "V01")
	if v := p.VariantBySize("M"); v.Stock != 0 {
		t.Fatalf("el stock de M debía bajar a 0, quedó %d", v.Stock)
	}

	loc := text("")
	loc.HasLocation, loc.Lat, loc.Lng, loc.LocationTxt = true, -12.0464, -77.0428, "Lima"
	handle(b, loc)
	mustContain(t, fe.lastText(), "Gracias", "#1")

	o, err := st.GetOrder(ctx, 1)
	if err != nil {
		t.Fatal(err)
	}
	if o.Status != "confirmado" || !o.StockReserved || o.LocationLat == nil || *o.LocationLat != -12.0464 || o.Total != 238 {
		t.Fatalf("pedido inesperado: %+v", o)
	}
	if o.CustomerImage == "" || o.MatchConfidence < 0.9 {
		t.Fatalf("el pedido debía guardar la foto del cliente y la confianza: %+v", o)
	}

	// Cancelar desde el tablero devuelve el stock.
	if _, err := st.UpdateOrderStatus(ctx, o.ID, "cancelado", nil); err != nil {
		t.Fatal(err)
	}
	p, _ = st.GetProductByCode(ctx, "V01")
	if v := p.VariantBySize("M"); v.Stock != 2 {
		t.Fatalf("el stock de M debía volver a 2, quedó %d", v.Stock)
	}
}

func TestProductoAgotadoYCodigo(t *testing.T) {
	b, st, fe := setup(t, `{"code":"V07","confidence":0.9,"seen":"sirena celeste","alternatives":["V01"]}`, false)
	ctx := context.Background()
	handle(b, photo())
	mustContain(t, fe.lastText(), "agotado", "V01")

	handle(b, text("v01"))
	mustContain(t, fe.lastText(), "¡Lo tenemos!", "V01")
	handle(b, text("XL"))
	mustContain(t, fe.lastText(), "Tenemos: *S*, *M*")
	handle(b, text("no"))
	mustContain(t, fe.lastText(), "no registramos")

	orders, _ := st.ListOrders(ctx, 0)
	statuses := map[string]int{}
	for _, o := range orders {
		statuses[o.Status]++
	}
	if statuses["consulta"] != 1 || statuses["cancelado"] != 1 {
		t.Fatalf("se esperaba 1 consulta (agotado) y 1 cancelado, hay %v", statuses)
	}
}

func TestSinCoincidenciaQuedaComoConsulta(t *testing.T) {
	b, st, fe := setup(t, `{"code":"","confidence":0.2,"seen":"blusa roja","alternatives":[]}`, false)
	ctx := context.Background()
	handle(b, photo())
	mustContain(t, fe.lastText(), "No encontré")
	orders, _ := st.ListOrders(ctx, 0)
	if len(orders) != 1 || orders[0].Status != "consulta" || !strings.Contains(orders[0].Notes, "blusa roja") {
		t.Fatalf("se esperaba una consulta con la descripción de la IA: %+v", orders)
	}
}

func TestAsesoraPausaBotYMensajePropio(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	ctx := context.Background()
	handle(b, text("4"))
	mustContain(t, fe.lastText(), "asesora")
	n := len(fe.sent)
	handle(b, text("hola??"))
	if len(fe.sent) != n {
		t.Fatal("con el bot pausado no debía responder")
	}
	conv, _ := st.ConversationByCustomer(ctx, 1)
	if !conv.BotPaused {
		t.Fatal("la conversación debía quedar pausada")
	}
	// Reintento del webhook con el mismo ID: se ignora.
	dup := text("x")
	handle(b, dup)
	handle(b, dup)
	msgs, _ := st.ListMessages(ctx, conv.ID, 100)
	count := 0
	for _, m := range msgs {
		if m.WAID == dup.ID {
			count++
		}
	}
	if count != 1 {
		t.Fatalf("mensaje duplicado guardado %d veces", count)
	}
}

func TestParseWebhook(t *testing.T) {
	img := `{"event":"Message","instanceName":"kddesign","data":{"Info":{"ID":"ABC","Chat":"51987654321@s.whatsapp.net","Sender":"51987654321@s.whatsapp.net","IsFromMe":false,"IsGroup":false,"PushName":"Ana"},
		"Message":{"imageMessage":{"caption":"¿tienen este?","mimetype":"image/jpeg","mediaKey":"AAA="},"base64":"QUJD"}}}`
	in, err := ParseWebhook([]byte(img))
	if err != nil || in == nil {
		t.Fatal(err)
	}
	if !in.HasImage || in.Text != "¿tienen este?" || in.Phone != "51987654321" || in.ImageB64 != "QUJD" {
		t.Fatalf("%+v", in)
	}
	if strings.Contains(string(in.RawMessage), "base64") || !strings.Contains(string(in.RawMessage), "mediaKey") {
		t.Fatalf("RawMessage debe ir sin base64 y con la clave del medio: %s", in.RawMessage)
	}

	loc := `{"event":"Message","data":{"Info":{"ID":"L1","Chat":"123@lid","SenderAlt":"51911111111@s.whatsapp.net"},
		"Message":{"locationMessage":{"degreesLatitude":-12.1,"degreesLongitude":-77.0,"name":"Casa","address":"Av. Arequipa 123"}}}}`
	in, _ = ParseWebhook([]byte(loc))
	if !in.HasLocation || in.Lat != -12.1 || in.Chat != "51911111111@s.whatsapp.net" || in.LocationTxt != "Casa - Av. Arequipa 123" {
		t.Fatalf("%+v", in)
	}

	txt := `{"event":"Message","data":{"Info":{"ID":"T1","Chat":"51911111111@s.whatsapp.net"},"Message":{"extendedTextMessage":{"text":" 1 "}}}}`
	in, _ = ParseWebhook([]byte(txt))
	if in.Text != "1" {
		t.Fatalf("%+v", in)
	}

	other, _ := ParseWebhook([]byte(`{"event":"ReadReceipt","data":{}}`))
	if other != nil {
		t.Fatal("otros eventos deben ignorarse")
	}
}

func TestEnvioLentoNoBloqueaAlBot(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	fe.delay = 2 * time.Second
	ctx := context.Background()
	start := time.Now()
	b.Handle(ctx, text("hola"))
	b.Handle(ctx, text("1"))
	if d := time.Since(start); d > time.Second {
		t.Fatalf("el bot esperó al envío (%v); debía encolarlo", d)
	}
	conv, _ := st.ConversationByCustomer(ctx, 1)
	msgs, _ := st.ListMessages(ctx, conv.ID, 100)
	if len(msgs) != 4 || msgs[1].Status != "pending" || !strings.Contains(msgs[3].Body, "Catálogo") {
		t.Fatalf("se esperaban 2 entrantes y 2 respuestas pendientes en orden: %+v", msgs)
	}
	if !b.Drain(10 * time.Second) {
		t.Fatal("la cola no se vació")
	}
	msgs, _ = st.ListMessages(ctx, conv.ID, 100)
	if msgs[1].Status != "sent" || msgs[1].WAID == "" || msgs[3].Status != "sent" {
		t.Fatalf("las respuestas debían quedar enviadas: %+v %+v", msgs[1], msgs[3])
	}
	if !strings.Contains(fe.sent[0]["text"].(string), "Hola Ana") || !strings.Contains(fe.sent[1]["text"].(string), "Catálogo") {
		t.Fatal("las respuestas debían salir en orden")
	}
}
