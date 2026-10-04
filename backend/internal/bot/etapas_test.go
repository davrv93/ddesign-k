package bot

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// agenteGuion contesta según el mensaje y guarda todas las peticiones, para comprobar qué etapa y
// qué pedido recibe el agente en cada turno.
func agenteGuion(t *testing.T, guion map[string]agente.Reply, reqs *[]agente.Request) *agente.Client {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req agente.Request
		_ = json.NewDecoder(r.Body).Decode(&req)
		*reqs = append(*reqs, req)
		rep, ok := guion[req.Mensaje]
		if !ok {
			http.Error(w, "caído", http.StatusBadGateway)
			return
		}
		_ = json.NewEncoder(w).Encode(rep)
	}))
	t.Cleanup(srv.Close)
	return agente.New(srv.URL, 5*time.Second)
}

func estadoDe(t *testing.T, st *store.Store) (string, convContext) {
	t.Helper()
	convs, err := st.ListConversations(context.Background(), 10)
	if err != nil || len(convs) == 0 {
		t.Fatalf("sin conversación: %v", err)
	}
	var cc convContext
	_ = json.Unmarshal([]byte(convs[0].Context), &cc)
	return convs[0].State, cc
}

func textosDesde(fe *fakeEvolution, n int) string {
	fe.mu.Lock()
	defer fe.mu.Unlock()
	var out []string
	for _, m := range fe.sent[n:] {
		if s, ok := m["text"].(string); ok {
			out = append(out, s)
		} else if s, ok := m["caption"].(string); ok {
			out = append(out, s)
		}
	}
	return strings.Join(out, "\n---\n")
}

const rigido = "Responde *SI* para confirmar tu pedido o *NO* para cancelarlo"

// La etapa que decide el agente se guarda con la conversación y vuelve en el siguiente mensaje.
func TestEtapaViajaConLaConversacion(t *testing.T) {
	b, st, _ := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"cuanto cuesta":  {Accion: "responder", Respuesta: "Está *S/ 119.00* 😊", Etapa: "seguimiento"},
		"es para cuando": {Accion: "responder", Respuesta: "¿Para cuándo lo necesitas?", Etapa: "seguimiento"},
	}, &reqs)
	handle(b, text("cuanto cuesta"))
	if reqs[0].Etapa != "" {
		t.Fatalf("la primera petición no debía traer etapa: %q", reqs[0].Etapa)
	}
	if _, cc := estadoDe(t, st); cc.Etapa != "seguimiento" {
		t.Fatalf("la etapa debía guardarse: %+v", cc)
	}
	handle(b, text("es para cuando"))
	if reqs[1].Etapa != "seguimiento" || reqs[1].Conversacion == "" {
		t.Fatalf("la etapa debía volver al agente: %+v", reqs[1])
	}
}

// Preguntar con el código dentro de una frase no arranca el pedido: lo contesta el agente.
func TestCodigoEnUnaFraseNoArrancaElPedido(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"de que tela es el V01?": {Accion: "responder", Respuesta: "Es de gasa con forro 😊", Etapa: "seguimiento"},
	}, &reqs)
	handle(b, text("de que tela es el V01?"))
	mustContain(t, fe.lastText(), "gasa")
	if s, _ := estadoDe(t, st); s != stIdle {
		t.Fatalf("una pregunta no debía dejar el chat esperando talla: %q", s)
	}
	handle(b, text("V01")) // el código solo sí es pedirlo
	if s, _ := estadoDe(t, st); s != stSize {
		t.Fatalf("el código solo debía arrancar el pedido: %q", s)
	}
}

// Con el resumen delante, una pregunta la contesta el agente y se recuerda el paso; el bot no repite
// «Responde SI o NO» ni pierde el pedido.
func TestPreguntaEnConfirmacionNoAtasca(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"hacen envios a cusco?": {Accion: "responder", Respuesta: "Sí, enviamos a provincia por Olva o Shalom 🚚", Etapa: "cierre"},
	}, &reqs)
	handle(b, text("V01"))
	handle(b, text("M"))
	n := len(fe.sent)
	handle(b, text("hacen envios a cusco?"))
	got := textosDesde(fe, n)
	mustContain(t, got, "Olva", "responde *SI*")
	if strings.Contains(got, rigido) {
		t.Fatalf("no debía contestar con la frase rígida:\n%s", got)
	}
	last := reqs[len(reqs)-1]
	if last.Etapa != "cierre" || last.Producto != "V01" || last.Talla != "M" || last.Estado != stConfirm {
		t.Fatalf("el agente debía recibir etapa y pedido en curso: %+v", last)
	}
	if s, _ := estadoDe(t, st); s != stConfirm {
		t.Fatalf("debía seguir esperando la confirmación: %q", s)
	}
	// Un número dentro de una pregunta no es una cantidad.
	p, _ := st.GetProductByCode(context.Background(), "V01")
	if v := p.VariantBySize("M"); v.Reserved != 1 {
		t.Fatalf("la reserva debía seguir en 1: %+v", v)
	}
}

// «Lo voy a pensar» con el resumen delante: sale del cierre, se libera la talla y nadie la presiona.
func TestDudaEnConfirmacionSaleDelCierre(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"mmm lo voy a pensar": {Accion: "responder", Respuesta: "Claro, tómate tu tiempo 😊\n\n¿Hay algo que te haga dudar?", Etapa: "seguimiento"},
	}, &reqs)
	ctx := context.Background()
	handle(b, text("V01"))
	handle(b, text("S"))
	n := len(fe.sent)
	handle(b, text("mmm lo voy a pensar"))
	got := textosDesde(fe, n)
	mustContain(t, got, "tómate tu tiempo")
	if strings.Contains(got, "SI") {
		t.Fatalf("no debía insistir con la confirmación:\n%s", got)
	}
	s, cc := estadoDe(t, st)
	if s != stIdle || cc.Etapa != "seguimiento" || cc.OrderID == 0 {
		t.Fatalf("debía quedar libre, en seguimiento y con el pedido para retomarlo: %q %+v", s, cc)
	}
	p, _ := st.GetProductByCode(ctx, "V01")
	if p.VariantBySize("S").Available() != 1 {
		t.Fatalf("la talla debía liberarse: %+v", p.VariantBySize("S"))
	}
	if o, err := st.GetOrder(ctx, cc.OrderID); err != nil || o.Status != "consulta" {
		t.Fatalf("el pedido debía volver a consulta: %+v %v", o, err)
	}
}

// Venta completa: confirma → Lima o provincia → total y pago (agente) → comprobante → dirección.
func TestConfirmaPagoComprobanteYDireccion(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"sii por favor": {Accion: "responder", Respuesta: "¡Listo! 🎉", Etapa: "venta_confirmada"},
		"para lima":     {Accion: "responder", Respuesta: "El envío a Lima es *S/ 15.00*.\n\n*TOTAL: S/ 134.00*", Etapa: "venta_confirmada"},
		"cuando llega?": {Accion: "responder", Respuesta: "Llega en 1 a 2 días hábiles 🚚", Etapa: "venta_confirmada"},
	}, &reqs)
	ctx := context.Background()
	handle(b, text("V01"))
	handle(b, text("M"))
	n := len(fe.sent)
	handle(b, text("sii por favor")) // un «sí» que Go no conoce: lo reconoce el agente por la etapa
	mustContain(t, textosDesde(fe, n), "confirmado", "*Lima* o para *provincia*")
	s, cc := estadoDe(t, st)
	if s != stPayment || cc.Etapa != "venta_confirmada" {
		t.Fatalf("debía quedar esperando el pago: %q %+v", s, cc)
	}
	if o, _ := st.GetOrder(ctx, cc.OrderID); o.Status != "confirmado" {
		t.Fatalf("el pedido debía quedar confirmado: %s", o.Status)
	}

	handle(b, text("para lima"))
	mustContain(t, fe.lastText(), "TOTAL: S/ 134.00")
	if last := reqs[len(reqs)-1]; last.Etapa != "venta_confirmada" || last.Producto != "V01" {
		t.Fatalf("el agente debía saber que la venta está confirmada y de qué prenda: %+v", last)
	}

	n = len(fe.sent)
	handle(b, photo()) // el comprobante, no una consulta de modelo
	mustContain(t, textosDesde(fe, n), "comprobante", "dirección completa")
	if s, _ := estadoDe(t, st); s != stLocation {
		t.Fatalf("tras el comprobante debía pedir la dirección: %q", s)
	}
	if o, _ := st.GetOrder(ctx, cc.OrderID); !strings.Contains(o.Notes, "Comprobante de pago recibido") {
		t.Fatalf("el comprobante debía quedar anotado en el pedido: %q", o.Notes)
	}

	n = len(fe.sent)
	handle(b, text("cuando llega?")) // una pregunta no es una dirección
	mustContain(t, textosDesde(fe, n), "días hábiles", "dirección completa")
	if o, _ := st.GetOrder(ctx, cc.OrderID); o.LocationText != "" {
		t.Fatalf("la pregunta no debía guardarse como dirección: %q", o.LocationText)
	}

	handle(b, text("Av. Los Ruiseñores 123, Santa Anita, frente al parque"))
	mustContain(t, fe.lastText(), "validemos tu pago")
	if s, _ := estadoDe(t, st); s != stIdle {
		t.Fatalf("la venta debía cerrar el flujo: %q", s)
	}
	if o, _ := st.GetOrder(ctx, cc.OrderID); !strings.Contains(o.LocationText, "Ruiseñores") {
		t.Fatalf("faltó la dirección: %q", o.LocationText)
	}
}

// Esperando la talla, «muéstrame otros modelos» suelta el pedido en vez de repetir «¿Qué talla deseas?».
func TestOtrosModelosEnTallaSueltaElPedido(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"mejor muestrame otros modelos": {Accion: "responder", Respuesta: "¡Claro! Mira este 😍", Etapa: "cierre",
			Sugerencias: []agente.Sugerencia{{Codigo: "V07", Fuente: "seed", Imagen: "/media/products/v07.jpg", Pie: "*V07* Vestido Sirena Celeste"}}},
		"y la M me quedara si mido 1.60?": {Accion: "responder", Respuesta: "Sí, la M te queda bien con 1.60 😊 ¿Qué talla prefieres?", Etapa: "cierre"},
		"ya pues":                         {Accion: "responder", Respuesta: "¡Listo! 🎉 quedó separado.", Etapa: "venta_confirmada", Codigo: "V01", Talla: "M"},
	}, &reqs)
	handle(b, text("V01"))
	n := len(fe.sent)
	handle(b, text("y la M me quedara si mido 1.60?")) // nombra una talla, pero es una duda
	mustContain(t, textosDesde(fe, n), "1.60")
	if s, _ := estadoDe(t, st); s != stSize {
		t.Fatalf("una duda sobre la talla no debía armar el pedido: %q", s)
	}
	// Un «sí» sin resumen delante no es una venta: con la talla se arma el resumen y se pide confirmar.
	n = len(fe.sent)
	handle(b, text("ya pues"))
	got0 := textosDesde(fe, n)
	mustContain(t, got0, "Resumen de tu pedido", "Talla: *M*")
	if strings.Contains(got0, "quedó separado") {
		t.Fatalf("no debía dar la venta por hecha:\n%s", got0)
	}
	if s, _ := estadoDe(t, st); s != stConfirm {
		t.Fatalf("debía quedar esperando la confirmación: %q", s)
	}
	handle(b, text("V01")) // vuelve a elegir talla para el caso siguiente
	n = len(fe.sent)
	handle(b, text("mejor muestrame otros modelos"))
	got := textosDesde(fe, n)
	mustContain(t, got, "Mira este", "V07")
	if strings.Contains(got, "¿Qué talla deseas?") || strings.Contains(got, "dime tu talla") {
		t.Fatalf("no debía insistir con la talla:\n%s", got)
	}
	if s, cc := estadoDe(t, st); s != stIdle || cc.Etapa != "seguimiento" {
		t.Fatalf("debía soltar el pedido: %q %+v", s, cc)
	}
}

// Un mensaje que llega desde un anuncio de clic a WhatsApp trae la referencia al anuncio. Solo entonces
// «este vestido» es el del anuncio; sin ella, el agente pregunta cuál.
func TestAnuncioSeDetectaYViajaAlAgente(t *testing.T) {
	ad := `{"event":"Message","data":{"Info":{"ID":"AD1","Chat":"51911111111@s.whatsapp.net"},"Message":{"extendedTextMessage":{
		"text":"Hola, quisiera saber si todavía tienen este vestido",
		"contextInfo":{"externalAdReply":{"title":"Vestido Gala Capa Azul","body":"Nueva colección","sourceType":"ad","sourceURL":"https://fb.me/x"},
		"conversionSource":"FB_Ads","entryPointConversionSource":"ctwa_ad"}}}}}`
	in, err := ParseWebhook([]byte(ad))
	if err != nil || !in.FromAd || in.AdTitle != "Vestido Gala Capa Azul" {
		t.Fatalf("debía detectar el anuncio: %+v %v", in, err)
	}
	plano, _ := ParseWebhook([]byte(`{"event":"Message","data":{"Info":{"ID":"P1","Chat":"51911111111@s.whatsapp.net"},"Message":{"conversation":"hola, tienen este vestido?"}}}`))
	if plano.FromAd {
		t.Fatal("un mensaje normal no viene de un anuncio")
	}

	b, st, _ := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"Hola, quisiera saber si todavía tienen este vestido": {Accion: "responder", Respuesta: "¡Sí! 😊", Etapa: "prospeccion"},
		"es para una boda": {Accion: "responder", Respuesta: "¡Qué lindo!", Etapa: "prospeccion"},
	}, &reqs)
	primero := text("Hola, quisiera saber si todavía tienen este vestido")
	primero.FromAd, primero.AdTitle = true, "Vestido Gala Capa Azul"
	handle(b, primero)
	handle(b, text("es para una boda")) // el segundo mensaje ya no trae el anuncio: se recuerda
	for k, r := range reqs {
		if !r.DesdeAnuncio || r.Anuncio != "Vestido Gala Capa Azul" {
			t.Fatalf("petición %d sin el anuncio: %+v", k, r)
		}
	}
	handle(b, text("menu")) // reiniciar el flujo no lo borra
	if _, cc := estadoDe(t, st); !cc.Anuncio {
		t.Fatalf("el menú no debía borrar el anuncio: %+v", cc)
	}
}

func TestSinAnuncioNoSeMarca(t *testing.T) {
	b, _, _ := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"hola tienen este vestido?": {Accion: "responder", Respuesta: "¿Me pasas la foto o el nombre?", Etapa: "prospeccion"},
	}, &reqs)
	handle(b, text("hola tienen este vestido?"))
	if len(reqs) != 1 || reqs[0].DesdeAnuncio {
		t.Fatalf("sin anuncio no debía marcarse: %+v", reqs)
	}
}
