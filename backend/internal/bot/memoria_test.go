package bot

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// memDe lee la memoria guardada con la conversación.
func memDe(t *testing.T, cc convContext) map[string]any {
	t.Helper()
	m := map[string]any{}
	if len(cc.Memoria) > 0 {
		if err := json.Unmarshal(cc.Memoria, &m); err != nil {
			t.Fatalf("memoria ilegible: %s", cc.Memoria)
		}
	}
	return m
}

func sabemos(m map[string]any, campo string) string {
	sab, _ := m["sabemos"].(map[string]any)
	s, _ := sab[campo].(string)
	return s
}

func pendiente(m map[string]any) string { s, _ := m["pendiente"].(string); return s }

const memOcasion = `{"etapa":"prospeccion","pendiente":"horario","sabemos":{"ocasion":"matrimonio","talla":null},"preguntado":["ocasion","horario"]}`

// La memoria que devuelve el agente se guarda con la conversación y viaja en la petición siguiente.
func TestMemoriaViajaConLaConversacion(t *testing.T) {
	b, st, _ := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"es para un matrimonio": {Accion: "responder", Respuesta: "¡Qué bonito! ¿El evento es de día o de noche?", Etapa: "prospeccion",
			Memoria: json.RawMessage(memOcasion)},
		"de noche": {Accion: "responder", Respuesta: "¿Qué talla usas?", Etapa: "prospeccion"},
	}, &reqs)
	handle(b, text("es para un matrimonio"))
	if len(reqs[0].Memoria) != 0 {
		t.Fatalf("la primera petición no debía traer memoria: %s", reqs[0].Memoria)
	}
	_, cc := estadoDe(t, st)
	if m := memDe(t, cc); sabemos(m, "ocasion") != "matrimonio" || pendiente(m) != "horario" {
		t.Fatalf("la memoria debía guardarse: %s", cc.Memoria)
	}
	handle(b, text("de noche"))
	var m map[string]any
	_ = json.Unmarshal(reqs[1].Memoria, &m)
	if sabemos(m, "ocasion") != "matrimonio" || pendiente(m) != "horario" {
		t.Fatalf("la memoria debía volver al agente tal cual: %s", reqs[1].Memoria)
	}
	// El agente no devolvió memoria en el segundo turno (versión vieja): se conserva la que había.
	if _, cc := estadoDe(t, st); sabemos(memDe(t, cc), "ocasion") != "matrimonio" {
		t.Fatalf("una respuesta sin memoria no debía borrarla: %s", cc.Memoria)
	}
}

// Los reinicios del flujo (menú, cancelar) no borran lo que sabemos de la clienta, pero sí la pregunta pendiente.
func TestReinicioConservaLoQueSabemos(t *testing.T) {
	b, st, _ := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"es para un matrimonio": {Accion: "responder", Respuesta: "¿De día o de noche?", Etapa: "prospeccion", Memoria: json.RawMessage(memOcasion)},
	}, &reqs)
	handle(b, text("es para un matrimonio"))
	handle(b, text("menu"))
	_, cc := estadoDe(t, st)
	m := memDe(t, cc)
	if sabemos(m, "ocasion") != "matrimonio" {
		t.Fatalf("el menú no debía borrar lo que sabemos: %s", cc.Memoria)
	}
	if pendiente(m) != "" {
		t.Fatalf("el menú debía soltar la pregunta pendiente: %s", cc.Memoria)
	}
	handle(b, text("V01"))
	handle(b, text("no")) // cancela el pedido: otro reinicio
	if _, cc := estadoDe(t, st); sabemos(memDe(t, cc), "ocasion") != "matrimonio" || pendiente(memDe(t, cc)) != "" {
		t.Fatalf("cancelar no debía borrar lo que sabemos: %s", cc.Memoria)
	}
}

// Los estados fijos de Go dejan su pregunta pendiente: talla → confirmar → Lima o provincia → dirección.
func TestFlujoGoDejaSuPendiente(t *testing.T) {
	b, st, _ := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{}, &reqs)
	handle(b, text("V01"))
	_, cc := estadoDe(t, st)
	if m := memDe(t, cc); pendiente(m) != "talla" || m["producto"] != "V01" {
		t.Fatalf("esperando la talla, la pendiente es «talla» y el producto V01: %s", cc.Memoria)
	}
	handle(b, text("M"))
	_, cc = estadoDe(t, st)
	if m := memDe(t, cc); pendiente(m) != "confirmar" || sabemos(m, "talla") != "M" {
		t.Fatalf("con el resumen, la pendiente es «confirmar» y la talla queda sabida: %s", cc.Memoria)
	}
	handle(b, text("si"))
	if s, cc := estadoDe(t, st); s != stPayment || pendiente(memDe(t, cc)) != "lima_o_provincia" {
		t.Fatalf("confirmado, se espera Lima o provincia: %q %s", s, cc.Memoria)
	}
	handle(b, photo()) // el comprobante
	if s, cc := estadoDe(t, st); s != stLocation || pendiente(memDe(t, cc)) != "direccion" || sabemos(memDe(t, cc), "talla") != "M" {
		t.Fatalf("tras el comprobante se espera la dirección (y la talla sigue sabida): %q %s", s, cc.Memoria)
	}
}

// Una duda en pleno cierre la contesta el agente (con su propia pregunta), pero lo que sigue esperando el
// bot es la talla: la pendiente vuelve a «talla», no se queda la del agente.
func TestDudaEnTallaMantieneLaPendiente(t *testing.T) {
	b, st, _ := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"es de gasa?": {Accion: "responder", Respuesta: "Sí, lleva gasa 😊 ¿Para cuándo lo necesitas?", Etapa: "cierre",
			Memoria: json.RawMessage(`{"pendiente":"fecha","sabemos":{"ocasion":"boda"},"preguntado":["fecha"]}`)},
	}, &reqs)
	handle(b, text("V01"))
	handle(b, text("es de gasa?"))
	if last := reqs[len(reqs)-1]; pendiente(memDe(t, convContext{Memoria: last.Memoria})) != "talla" {
		t.Fatalf("el agente debía saber que se esperaba la talla: %s", last.Memoria)
	}
	s, cc := estadoDe(t, st)
	m := memDe(t, cc)
	if s != stSize || pendiente(m) != "talla" || sabemos(m, "ocasion") != "boda" {
		t.Fatalf("debía seguir esperando la talla, con lo que aportó el agente: %q %s", s, cc.Memoria)
	}
}

// Clienta que vuelve: el agente recibe sus tallas y prendas de pedidos anteriores (no el de hoy).
func TestPerfilDeClientaQueVuelve(t *testing.T) {
	b, _, _ := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"hola de nuevo": {Accion: "responder", Respuesta: "¡Qué gusto verte otra vez!", Etapa: "prospeccion"},
	}, &reqs)
	handle(b, text("hola de nuevo"))
	if reqs[0].Perfil != nil {
		t.Fatalf("sin pedidos anteriores no hay perfil: %+v", reqs[0].Perfil)
	}
	handle(b, text("V01"))
	handle(b, text("M"))
	handle(b, text("si")) // pedido confirmado
	handle(b, text("menu"))
	handle(b, text("hola de nuevo"))
	p := reqs[len(reqs)-1].Perfil
	if p == nil || p.Pedidos != 1 || len(p.Tallas) != 1 || p.Tallas[0] != "M" || p.Productos[0] != "V01" || p.Nombre != "Ana López" {
		t.Fatalf("el perfil debía traer el pedido anterior: %+v", p)
	}
}

// La foto también lleva y trae la memoria.
func TestFotoLlevaLaMemoria(t *testing.T) {
	b, st, _ := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"es para un matrimonio": {Accion: "responder", Respuesta: "¿De día o de noche?", Etapa: "prospeccion", Memoria: json.RawMessage(memOcasion)},
		"": {Accion: "responder", Respuesta: "¡Sí lo tenemos! Es el *V01*.", Etapa: "seguimiento",
			Foto:    &agente.PhotoResult{Nivel: "exacto", Caso: "online", Codigo: "V01", Similitud: 0.9},
			Memoria: json.RawMessage(`{"pendiente":"horario","producto":"V01","sabemos":{"ocasion":"matrimonio"}}`)},
	}, &reqs)
	handle(b, text("es para un matrimonio"))
	handle(b, photo())
	if last := reqs[len(reqs)-1]; len(last.Memoria) == 0 {
		t.Fatal("la petición de la foto debía llevar la memoria")
	}
	_, cc := estadoDe(t, st)
	if m := memDe(t, cc); m["producto"] != "V01" || sabemos(m, "ocasion") != "matrimonio" {
		t.Fatalf("la memoria de la foto debía guardarse: %s", cc.Memoria)
	}
}

// ordenesDe: los pedidos de la clienta de la conversación de prueba.
func ordenesDe(t *testing.T, b *Bot) []*store.Order {
	t.Helper()
	conv, err := b.store.ConversationByCustomer(context.Background(), 1)
	if err != nil {
		t.Fatalf("sin conversación: %v", err)
	}
	orders, _ := b.store.ListOrders(context.Background(), conv.CustomerID)
	return orders
}

// memConCita: la memoria que devuelve el agente cuando la clienta agenda (o no) su cita para probarse.
func memConCita(cita string) json.RawMessage {
	return json.RawMessage(`{"etapa":"cierre","producto":"V01","pendiente":"","temperatura":"caliente",` +
		`"temperatura_motivo":"evento el 10-oct (en 6 días)","sabemos":{"talla":"M","fecha_iso":"2026-10-10","cita":` + cita + `}}`)
}

// Método de venta: cuando el agente agenda una cita para probarse, el pedido queda en el tablero como consulta,
// con la prenda y una nota para la asesora, y se avisa al panel. No reserva stock. Repetir la misma cita no
// duplica nada; cambiarla se anota en el mismo pedido.
func TestCitaQuedaEnElTablero(t *testing.T) {
	b, st, _ := setup(t, `{}`, false)
	var topics []string
	b.Notify = func(s string) { topics = append(topics, s) }
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"quiero ir a probármelo":   {Accion: "responder", Respuesta: "¿Qué día y a qué hora te acomoda venir a probártelo?", Etapa: "cierre", Memoria: memConCita("null")},
		"el viernes a las 5":       {Accion: "responder", Respuesta: "¡Listo! 🗓️ Te esperamos el viernes 9 de octubre a las 5:00 p. m.", Etapa: "cierre", Memoria: memConCita(`"2026-10-09T17:00"`)},
		"gracias":                  {Accion: "responder", Respuesta: "¡A ti! 💙", Etapa: "cierre", Memoria: memConCita(`"2026-10-09T17:00"`)},
		"mejor el sábado a las 11": {Accion: "responder", Respuesta: "¡Listo! Te esperamos el sábado.", Etapa: "cierre", Memoria: memConCita(`"2026-10-10T11:00"`)},
	}, &reqs)

	handle(b, text("quiero ir a probármelo"))
	if o := ordenesDe(t, b); len(o) != 0 {
		t.Fatalf("pedir la cita sin día ni hora todavía no es una cita: %+v", o[0])
	}
	topics = nil
	handle(b, text("el viernes a las 5"))
	o := ordenesDe(t, b)
	if len(o) != 1 || o[0].Status != "consulta" || o[0].StockReserved {
		t.Fatalf("la cita debía dejar un pedido en consulta, sin reservar: %+v", o)
	}
	mustContain(t, o[0].Notes, "🗓️ Cita para probarse V01 talla M el vie 9-oct 17:00 · clienta caliente: evento el 10-oct (en 6 días)")
	if len(o[0].Items) != 1 || o[0].Items[0].ProductCode != "V01" || o[0].Items[0].Size != "M" {
		t.Fatalf("el pedido debía llevar la prenda y la talla de la cita: %+v", o[0].Items)
	}
	if !strings.Contains(strings.Join(topics, ","), "orders") {
		t.Fatalf("debía avisar al panel (orders): %v", topics)
	}
	if p, _ := st.GetProductByCode(context.Background(), "V01"); p.Variants[1].Available() != 2 || p.Variants[1].Reserved != 0 {
		t.Fatalf("la cita no reserva stock: %+v", p.Variants)
	}
	if _, cc := estadoDe(t, st); cc.OrderID != o[0].ID {
		t.Fatalf("el pedido de la cita queda como el de la conversación (si compra, se reutiliza): %d vs %d", cc.OrderID, o[0].ID)
	}

	handle(b, text("gracias"))
	if o := ordenesDe(t, b); len(o) != 1 || strings.Count(o[0].Notes, "🗓️") != 1 {
		t.Fatalf("la misma cita no se vuelve a anotar: %+v", o)
	}
	handle(b, text("mejor el sábado a las 11"))
	o = ordenesDe(t, b)
	if len(o) != 1 || strings.Count(o[0].Notes, "🗓️") != 2 {
		t.Fatalf("cambiar la cita se anota en el mismo pedido: %+v", o)
	}
	mustContain(t, o[0].Notes, "el sáb 10-oct 11:00")
}

// La temperatura de la clienta también va en la nota de las consultas que deja la foto.
func TestConsultaDeFotoLlevaLaTemperatura(t *testing.T) {
	b, _, _ := setup(t, `{}`, false)
	var last agente.PhotoRequest
	b.Agent = fakeAgenteFoto(t, agente.Reply{Accion: "responder", Respuesta: "Lo tenemos en Miraflores 👇", Etapa: "seguimiento",
		Foto:    &agente.PhotoResult{Nivel: "exacto", Caso: "sucursal", Codigo: "V01", Similitud: 0.88},
		Memoria: json.RawMessage(`{"temperatura":"tibio","temperatura_motivo":"evento el 17-oct (en 13 días)","sabemos":{}}`)}, &last)
	handle(b, photo())
	o := ordenesDe(t, b)
	if len(o) != 1 {
		t.Fatalf("debía quedar una consulta: %+v", o)
	}
	mustContain(t, o[0].Notes, "sucursal", "clienta tibia: evento el 17-oct (en 13 días)")
}

// Vuelve después de horas: conversación nueva. El agente no recibe el historial ni la memoria de la mañana, y
// la pausa por «asesora» caduca.
func TestVuelveDespuesDeHorasEsSesionNueva(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"quiero ver vestidos": {Accion: "responder", Respuesta: "Mira este 😍", Etapa: "seguimiento",
			Memoria:     json.RawMessage(`{"etapa":"seguimiento","producto":"V01","mostrados":["V01"],"pendiente":"talla","sabemos":{"ocasion":"boda"}}`),
			Sugerencias: []agente.Sugerencia{{Codigo: "V01", Fuente: "seed", Imagen: "/media/products/v01.jpg", Pie: "*V01* Vestido Esmeralda"}}},
		"hola": {Accion: "responder", Respuesta: "¡Hola! ¿Qué estás buscando hoy?", Etapa: "prospeccion"},
	}, &reqs)
	ctx := context.Background()
	handle(b, text("quiero ver vestidos"))
	handle(b, text("asesora")) // pide una persona: el bot se pausa
	convs, _ := st.ListConversations(ctx, 10)
	if !convs[0].BotPaused {
		t.Fatal("debía quedar en pausa")
	}
	// Pasan 7 horas.
	viejo := time.Now().UTC().Add(-7 * time.Hour)
	if _, err := st.DB.ExecContext(ctx, `UPDATE conversations SET last_message_at=?`, viejo); err != nil {
		t.Fatal(err)
	}
	if _, err := st.DB.ExecContext(ctx, `UPDATE messages SET created_at=?`, viejo); err != nil {
		t.Fatal(err)
	}
	n := len(fe.sent)
	handle(b, text("hola"))
	if len(fe.sent) == n {
		t.Fatal("tras horas, la pausa por asesora debía caducar y el bot contestar")
	}
	last := reqs[len(reqs)-1]
	if len(last.Historial) != 0 {
		t.Fatalf("no debía mandar el historial de la sesión anterior: %+v", last.Historial)
	}
	if strings.Contains(string(last.Memoria), "V01") || last.Etapa != "" {
		t.Fatalf("no debía arrastrar memoria ni etapa: %s etapa=%q", last.Memoria, last.Etapa)
	}
}

// «menu» devuelve el bot tras pedir una asesora.
func TestMenuQuitaLaPausaDeAsesora(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	handle(b, text("4"))
	mustContain(t, fe.lastText(), "asesora", "menu")
	n := len(fe.sent)
	handle(b, text("hola?")) // en pausa: no contesta
	if len(fe.sent) != n {
		t.Fatal("en pausa no debía contestar")
	}
	handle(b, text("menu"))
	mustContain(t, fe.lastText(), "1️⃣")
	if convs, _ := st.ListConversations(context.Background(), 10); convs[0].BotPaused {
		t.Fatal("«menu» debía quitar la pausa")
	}
}
