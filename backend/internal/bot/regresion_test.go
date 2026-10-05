package bot

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
)

// Pruebas de regresión del flujo de Go para los fallos vistos en WhatsApp el 04-10-2026. Lo que decide el agente
// (etapas, memoria, fotos) se prueba sin costo con `python3 -m app.regresion` (agente/README.md); aquí está lo que
// solo Go puede romper: la pausa por asesora, el pedido, el pago y lo que viaja al agente.

// «me llamo alvaro»: el agente contesta (ya no deriva) y el bot NO se pausa: los mensajes siguientes siguen
// llegando al agente. En WhatsApp real se pausó y «pero no tienes vestidos?», «hola?», «??» quedaron sin respuesta.
func TestDecirElNombreNoPausaElBot(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"hola":                     {Accion: "responder", Respuesta: "¡Hola! 😊\n\n¿Qué estás buscando hoy?", Etapa: "prospeccion"},
		"me llamo alvaro":          {Accion: "responder", Respuesta: "¡Mucho gusto, Alvaro!\n\n¿Qué estás buscando hoy?", Etapa: "prospeccion"},
		"pero no tienes vestidos?": {Accion: "responder", Respuesta: "¡Sí, tenemos vestidos! 😊\n\n¿Para qué ocasión buscas el vestido?", Etapa: "prospeccion"},
		"hola?":                    {Accion: "responder", Respuesta: "Aquí estoy 😊", Etapa: "prospeccion"},
		"??":                       {Accion: "responder", Respuesta: "Cuéntame, ¿para qué ocasión sería?", Etapa: "prospeccion"},
	}, &reqs)
	for _, m := range []string{"hola", "me llamo alvaro", "pero no tienes vestidos?", "hola?", "??"} {
		n := len(fe.sent)
		handle(b, text(m))
		if len(fe.sent) == n {
			t.Fatalf("«%s» se quedó sin respuesta", m)
		}
	}
	convs, _ := st.ListConversations(context.Background(), 10)
	if convs[0].BotPaused {
		t.Fatal("decir el nombre no debe pausar el bot")
	}
	if len(reqs) != 5 {
		t.Fatalf("los cinco mensajes debían llegar al agente: %d", len(reqs))
	}
}

// Si el agente SÍ deriva (lo pidió con palabras), el bot se pausa y calla; «menu» lo devuelve y el siguiente
// mensaje vuelve a llegar al agente.
func TestAsesoraPausaYMenuDevuelveElBot(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"quiero hablar con una persona": {Accion: "asesora", Etapa: "prospeccion"},
		"busco un vestido":              {Accion: "responder", Respuesta: "¿Para qué ocasión buscas el vestido?", Etapa: "prospeccion"},
	}, &reqs)
	handle(b, text("quiero hablar con una persona"))
	mustContain(t, fe.lastText(), "asesora")
	n := len(fe.sent)
	handle(b, text("busco un vestido")) // en pausa: nadie contesta (la asesora lo verá)
	if len(fe.sent) != n {
		t.Fatalf("en pausa el bot no debía contestar: %q", fe.lastText())
	}
	handle(b, text("menu"))
	if convs, _ := st.ListConversations(context.Background(), 10); convs[0].BotPaused {
		t.Fatal("«menu» debía quitar la pausa")
	}
	n = len(fe.sent)
	handle(b, text("busco un vestido"))
	mustContain(t, textosDesde(fe, n), "ocasión")
}

// «quiero el v21» son tres palabras: va al agente, que pregunta la talla (ya no arma el pedido con la del perfil).
// El pedido solo se arma cuando el agente devuelve `pedido` con la talla que ella dijo.
func TestPedidoSoloConLaTallaQueDijo(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"quiero el v01":          {Accion: "responder", Respuesta: "¡Perfecto! 😊 Para separar tu *V01* necesito tu talla.\n\n¿Usas talla *M*, como en tu pedido anterior, o prefieres otra talla?", Etapa: "cierre"},
		"mi talla es S disculpa": {Accion: "pedido", Codigo: "V01", Talla: "S", Etapa: "cierre"},
	}, &reqs)
	handle(b, text("quiero el v01"))
	if s, cc := estadoDe(t, st); s != stIdle || cc.OrderID != 0 {
		t.Fatalf("sin talla dicha no debía armarse ningún pedido: %q %+v", s, cc)
	}
	mustContain(t, fe.lastText(), "talla")
	handle(b, text("mi talla es S disculpa"))
	s, cc := estadoDe(t, st)
	if s != stConfirm || cc.Size != "S" {
		t.Fatalf("debía quedar el resumen en talla S (la que dijo, no la M de su pedido anterior): %q %+v", s, cc)
	}
	mustContain(t, fe.lastText(), "Resumen", "Talla: *S*")
	if sabemos(memDe(t, cc), "talla") != "S" || pendiente(memDe(t, cc)) != "confirmar" {
		t.Fatalf("la memoria debía llevar la talla S y la pendiente «confirmar»: %v", memDe(t, cc))
	}
}

// Ya dijo a dónde va el envío antes de confirmar: tras el SI no se le pregunta otra vez «¿Lima o provincia?»; el
// agente arma el total y los datos de pago en ese mismo turno.
func TestConfirmaConEnvioYaDichoNoLoVuelveAPreguntar(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	var reqs []agente.Request
	mem := json.RawMessage(`{"sabemos":{"envio":"provincia","ciudad":"Arequipa"},"producto":"V01"}`)
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"soy de arequipa": {Accion: "responder", Respuesta: "¡Anotado! 😊", Etapa: "cierre", Memoria: mem},
		"para Arequipa": {Accion: "responder", Etapa: "venta_confirmada", Memoria: mem,
			Respuesta: "El envío a Arequipa es *S/ 20.00*.\nTotal con tu prenda: *S/ 139.00*\n\n💳 *Datos para el pago*\n• Yape: …\n\nCuando hagas el pago, mándame la foto del comprobante por aquí y programo tu envío 🙌"},
	}, &reqs)
	handle(b, text("V01"))
	handle(b, text("M"))
	handle(b, text("soy de arequipa")) // en el resumen: lo contesta el agente y guarda la ciudad
	n := len(fe.sent)
	handle(b, text("si"))
	got := textosDesde(fe, n)
	mustContain(t, got, "confirmado", "Total con tu prenda: *S/ 139.00*", "Datos para el pago", "comprobante")
	if strings.Contains(got, "*Lima* o para *provincia*") {
		t.Fatalf("ya había dicho Arequipa: no debía volver a preguntar Lima o provincia:\n%s", got)
	}
	if s, cc := estadoDe(t, st); s != stPayment || cc.Etapa != "venta_confirmada" {
		t.Fatalf("debía quedar esperando el pago: %q %+v", s, cc)
	}
	if last := reqs[len(reqs)-1]; last.Mensaje != "para Arequipa" || last.Etapa != "venta_confirmada" || last.Producto != "V01" {
		t.Fatalf("el agente debía recibir el destino con la venta confirmada y la prenda: %+v", last)
	}
}

// Sin el envío dicho (o si el agente no contesta), tras el SI se pregunta como siempre.
func TestConfirmaSinEnvioPreguntaLimaOProvincia(t *testing.T) {
	b, _, fe := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{}, &reqs)
	handle(b, text("V01"))
	handle(b, text("M"))
	n := len(fe.sent)
	handle(b, text("si"))
	mustContain(t, textosDesde(fe, n), "confirmado", "*Lima* o para *provincia*")
}

// Con el pedido confirmado, el texto va al agente con la etapa y la prenda (él arma total y pago), «si ca ver»
// incluido; y la foto que llega es el comprobante, no una consulta de modelo.
func TestPagoSiCaVerYComprobante(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	var reqs []agente.Request
	pago := "💳 *Datos para el pago*\n• Yape: …\n\nCuando hagas el pago, mándame la foto del comprobante por aquí y programo tu envío 🙌"
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"lo quiero para trujillo": {Accion: "responder", Etapa: "venta_confirmada", Respuesta: "El envío a Trujillo es *S/ 20.00*.\nTotal con tu prenda: *S/ 139.00*\n\n" + pago},
		"si ca ver":               {Accion: "responder", Etapa: "venta_confirmada", Respuesta: "¡Perfecto! 🙌 Aquí espero tu comprobante para programar el envío."},
	}, &reqs)
	handle(b, text("V01"))
	handle(b, text("M"))
	handle(b, text("si"))
	n := len(fe.sent)
	handle(b, text("lo quiero para trujillo"))
	mustContain(t, textosDesde(fe, n), "S/ 139.00", "Datos para el pago")
	handle(b, text("si ca ver"))
	mustContain(t, fe.lastText(), "comprobante")
	for _, r := range reqs {
		if r.Etapa != "venta_confirmada" || r.Producto != "V01" || r.Estado != stPayment {
			t.Fatalf("en el pago el agente debía recibir etapa, prenda y estado: %+v", r)
		}
	}
	n = len(fe.sent)
	handle(b, photo())
	mustContain(t, textosDesde(fe, n), "comprobante")
	if s, _ := estadoDe(t, st); s != stLocation {
		t.Fatalf("tras el comprobante debía pedir la dirección: %q", s)
	}
}

// Vuelve después de más de 6 h con un pedido a medio confirmar: la sesión NO se reinicia (no se pierde el pedido);
// en reposo sí (TestVuelveDespuesDeHorasEsSesionNueva). Y en la sesión nueva el agente no recibe la etapa vieja.
func TestSesionNuevaNoArrastraEtapaNiPendiente(t *testing.T) {
	b, st, _ := setup(t, `{}`, false)
	var reqs []agente.Request
	mem := json.RawMessage(`{"producto":"V01","mostrados":["V01"],"pendiente":"talla","sabemos":{"ocasion":"matrimonio"}}`)
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"busco vestido": {Accion: "responder", Respuesta: "¿Qué talla usas normalmente?", Etapa: "seguimiento", Memoria: mem},
		"hola":          {Accion: "responder", Respuesta: "¡Hola! 😊\n\n¿Qué estás buscando hoy?", Etapa: "prospeccion"},
	}, &reqs)
	ctx := context.Background()
	handle(b, text("busco vestido"))
	convs, _ := st.ListConversations(ctx, 10)
	viejo := time.Now().UTC().Add(-7 * time.Hour)
	if _, err := st.DB.ExecContext(ctx, `UPDATE conversations SET last_message_at=? WHERE id=?`, viejo, convs[0].ID); err != nil {
		t.Fatal(err)
	}
	if _, err := st.DB.ExecContext(ctx, `UPDATE messages SET created_at=?`, viejo); err != nil {
		t.Fatal(err)
	}
	handle(b, text("hola"))
	last := reqs[len(reqs)-1]
	if last.Etapa != "" || len(last.Historial) != 0 {
		t.Fatalf("tras horas de silencio el agente no debía recibir la etapa ni el historial de antes: %+v", last)
	}
	if m := string(last.Memoria); strings.Contains(m, "V01") || strings.Contains(m, "matrimonio") || strings.Contains(m, "talla") {
		t.Fatalf("la memoria de la sesión anterior no debía viajar: %s", m)
	}
}
