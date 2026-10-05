package bot

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
	"github.com/davrv93/ddesign-k/backend/internal/kommo"
	"github.com/davrv93/ddesign-k/backend/internal/kommo/simulado"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

const embudoPrueba = "Baruka · Ventas por WhatsApp"

// conKommo engancha el bot a un Kommo simulado, como hace main.go con KOMMO_ENABLED=1.
func conKommo(t *testing.T, b *Bot, st *store.Store) (*kommo.Sincronizador, *simulado.Kommo) {
	t.Helper()
	k := simulado.Nuevo("tok")
	t.Cleanup(k.Close)
	c := kommo.Nuevo(k.URL, "tok")
	c.EsperaMin, c.EsperaMax = 5*time.Millisecond, 50*time.Millisecond
	s := kommo.NuevoSincronizador(c, st, kommo.Opciones{Embudo: embudoPrueba, Transcripcion: true,
		PanelURL: "https://proyectopostventa.site/baruka", Producto: kommo.ProductoDe(st),
		Envios: func(context.Context) map[string]float64 { return map[string]float64{"lima": 15, "provincia": 20} }})
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	s.Iniciar(ctx)
	b.CRM = s
	return s, k
}

// leadUnico espera a que el sincronizador vacíe la cola y devuelve el único lead del embudo.
func leadUnico(t *testing.T, s *kommo.Sincronizador, k *simulado.Kommo) simulado.Lead {
	t.Helper()
	if !s.Esperar(2 * time.Minute) {
		t.Fatal("el sincronizador no terminó")
	}
	if len(k.Errores) > 0 || k.Violaciones > 0 {
		t.Fatalf("peticiones mal formadas %v / violaciones del límite %d", k.Errores, k.Violaciones)
	}
	e := k.EmbudoPorNombre(embudoPrueba)
	if e == nil {
		t.Fatal("sin embudo")
	}
	ls := k.LeadsDe(e.ID)
	if len(ls) != 1 {
		t.Fatalf("se esperaba un lead, hay %d", len(ls))
	}
	return ls[0]
}

func nombreEstado(k *simulado.Kommo, id int64) string {
	for _, s := range k.EmbudoPorNombre(embudoPrueba).Estados {
		if s.ID == id {
			return s.Name
		}
	}
	return "?"
}

// La memoria que va devolviendo el agente en la conversación de prueba (acumula, como la real).
func memGuion(extra string) json.RawMessage {
	return json.RawMessage(`{"producto":"V01","temperatura":"caliente","temperatura_motivo":"evento el 10-oct (en 5 días)",` +
		`"sabemos":{"ocasion":"boda","fecha_iso":"2026-10-10","horario":"noche"` + extra + `}}`)
}

func guionVenta() map[string]agente.Reply {
	com := func(i string) *agente.Comercial { return &agente.Comercial{Intent: i} }
	return map[string]agente.Reply{
		"hola": {Accion: "responder", Respuesta: "¡Hola! ¿Qué estás buscando hoy?", Etapa: "prospeccion", Comercial: com("saludo"),
			Memoria: json.RawMessage(`{"temperatura":"frio","sabemos":{}}`)},
		"busco un vestido para una boda el 10 de octubre en la noche": {Accion: "responder", Etapa: "prospeccion", Comercial: com("consulta_producto"),
			Respuesta: "¡Qué lindo plan! Déjame buscarte algo especial.",
			Memoria:   json.RawMessage(`{"temperatura":"caliente","temperatura_motivo":"evento el 10-oct (en 5 días)","sabemos":{"ocasion":"boda","fecha_iso":"2026-10-10","horario":"noche"}}`)},
		"que me recomiendas": {Accion: "responder", Etapa: "seguimiento", Comercial: com("interesado"),
			Respuesta:   "Para una boda de noche te va increíble el Esmeralda 😍\n\n¿Qué talla usas?",
			Sugerencias: []agente.Sugerencia{{Codigo: "V01", Fuente: "seed", Imagen: "/media/products/v01.jpg", Pie: "*V01* Vestido Esmeralda"}},
			Memoria:     memGuion("")},
		"de que tela es": {Accion: "responder", Etapa: "seguimiento", Comercial: com("consulta_material"), Respuesta: "Es de crepe con caída 😊",
			Memoria: memGuion("")},
		"quiero ir a probármelo el viernes a las 5": {Accion: "responder", Etapa: "cierre", Comercial: com("consulta_ubicacion"),
			Respuesta: "¡Listo! 🗓️ Te esperamos el viernes 9 de octubre a las 5:00 p. m.", Memoria: memGuion(`,"talla":"M","cita":"2026-10-09T17:00"`)},
		"para lima": {Accion: "responder", Etapa: "venta_confirmada", Comercial: com("consulta_delivery"),
			Respuesta: "El envío a Lima es S/ 15.00. Total: S/ 134.00", Memoria: memGuion(`,"talla":"M","cita":"2026-10-09T17:00","envio":"lima"`)},
	}
}

// WhatsApp de punta a punta: saludo → indagación → una opción → tela → cita → pedido → SI → envío → comprobante. El
// lead recorre el embudo y termina en «Venta pagada», con sus campos y una nota por hito.
func TestKommoFlujoWhatsAppCompleto(t *testing.T) {
	b, st, _ := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, guionVenta(), &reqs)
	s, k := conKommo(t, b, st)

	handle(b, text("hola"))
	if l := leadUnico(t, s, k); nombreEstado(k, l.StatusID) != "Prospección" || k.ValorDe(l, "Canal") != "WhatsApp" || l.Nombre != "Ana López · WhatsApp" {
		t.Fatalf("tras el saludo: %s %q %q", nombreEstado(k, l.StatusID), k.ValorDe(l, "Canal"), l.Nombre)
	}
	for _, c := range k.Contactos {
		if c.Telefono != "+51987654321" || c.Nombre != "Ana López" {
			t.Fatalf("contacto de WhatsApp con su teléfono: %+v", c)
		}
	}
	handle(b, text("busco un vestido para una boda el 10 de octubre en la noche"))
	handle(b, text("que me recomiendas"))
	l := leadUnico(t, s, k)
	if nombreEstado(k, l.StatusID) != "Seguimiento" || k.ValorDe(l, "Temperatura") != "Caliente" || k.ValorDe(l, "Ocasión") != "boda" ||
		k.ValorDe(l, "Prenda en foco") != "V01 · Vestido Esmeralda" || l.Precio != 119 {
		t.Fatalf("opción ofrecida: %s %q %q %q %d", nombreEstado(k, l.StatusID), k.ValorDe(l, "Temperatura"), k.ValorDe(l, "Ocasión"), k.ValorDe(l, "Prenda en foco"), l.Precio)
	}
	handle(b, text("de que tela es"))
	handle(b, text("quiero ir a probármelo el viernes a las 5"))
	l = leadUnico(t, s, k)
	if nombreEstado(k, l.StatusID) != "Cierre" || k.ValorDe(l, "Cita para probarse") == "" || k.ValorDe(l, "Talla") != "M" {
		t.Fatalf("cita: %s cita=%q talla=%q", nombreEstado(k, l.StatusID), k.ValorDe(l, "Cita para probarse"), k.ValorDe(l, "Talla"))
	}
	handle(b, text("V01"))
	handle(b, text("M"))
	if l := leadUnico(t, s, k); nombreEstado(k, l.StatusID) != "Cierre" || k.ValorDe(l, "Pedido kddesign") != "#1 · pendiente · S/ 119.00" {
		t.Fatalf("resumen: %s %q", nombreEstado(k, l.StatusID), k.ValorDe(l, "Pedido kddesign"))
	}
	handle(b, text("si"))
	if l := leadUnico(t, s, k); nombreEstado(k, l.StatusID) != "Venta confirmada" {
		t.Fatalf("tras el SI: %s", nombreEstado(k, l.StatusID))
	}
	handle(b, text("para lima"))
	l = leadUnico(t, s, k)
	if nombreEstado(k, l.StatusID) != "Venta confirmada" || l.Precio != 134 || k.ValorDe(l, "Ciudad / envío") != "Lima" {
		t.Fatalf("envío: %s precio %d envío %q", nombreEstado(k, l.StatusID), l.Precio, k.ValorDe(l, "Ciudad / envío"))
	}
	handle(b, photo()) // el comprobante
	l = leadUnico(t, s, k)
	if l.StatusID != kommo.EstadoGanado || nombreEstado(k, l.StatusID) != "Venta pagada" {
		t.Fatalf("con el comprobante, venta pagada: %s", nombreEstado(k, l.StatusID))
	}
	if k.ValorDe(l, "Conversación en kddesign") == "" || k.ValorDe(l, "Llegó por anuncio") != "false" || k.ValorDe(l, "Día o noche") != "Noche" {
		t.Fatalf("campos: conversación %q anuncio %q horario %q", k.ValorDe(l, "Conversación en kddesign"), k.ValorDe(l, "Llegó por anuncio"), k.ValorDe(l, "Día o noche"))
	}
	notas := strings.Join(k.NotasDe(l.ID), "\n")
	for _, w := range []string{
		"📸 Se le mostró: V01 Vestido Esmeralda",
		"💬 Preguntó por la tela del V01",
		"🗓️ Cita para probarse agendada el vie 9-oct 17:00 (V01 talla M)",
		"🧾 Resumen del pedido #1: V01 talla M ×1 · S/ 119.00 (falta su SI)",
		"✅ Pedido #1 confirmado: V01 talla M ×1 · S/ 119.00",
		"💳 Comprobante de pago recibido (pedido #1). Falta validarlo.",
		"Clienta: de que tela es", "Bot: Es de crepe con caída",
	} {
		if !strings.Contains(notas, w) {
			t.Errorf("falta en las notas: %q", w)
		}
	}
	if len(k.Contactos) != 1 || len(k.LeadsDe(0)) != 1 {
		t.Fatalf("toda la conversación es un contacto y un lead: %d / %d", len(k.Contactos), len(k.LeadsDe(0)))
	}
	// El vínculo queda en la SQLite: el panel puede enlazar «Ver en Kommo».
	if id := st.LeadKommoDeConversacion(context.Background(), 1); id != l.ID {
		t.Fatalf("vínculo de la conversación: %d, se esperaba %d", id, l.ID)
	}
}

// Pedir una asesora y cancelar desde el tablero quedan en el lead.
func TestKommoAsesoraYCambioDelTablero(t *testing.T) {
	b, st, _ := setup(t, `{}`, false)
	s, k := conKommo(t, b, st)
	handle(b, text("V01"))
	handle(b, text("M"))
	handle(b, text("si")) // sin agente: confirmado, pide la ubicación
	o, _ := st.GetOrder(context.Background(), 1)
	if _, err := st.UpdateOrderStatus(context.Background(), o.ID, "cancelado", nil); err != nil {
		t.Fatal(err)
	}
	o, _ = st.GetOrder(context.Background(), 1)
	b.PedidoCambio(context.Background(), o)
	l := leadUnico(t, s, k)
	if l.StatusID != kommo.EstadoPerdido {
		t.Fatalf("cancelado en el tablero después de confirmar: venta perdida, quedó %s", nombreEstado(k, l.StatusID))
	}
	handle(b, text("menu"))
	handle(b, text("4"))
	notas := strings.Join(k.NotasDe(leadUnico(t, s, k).ID), "\n")
	for _, w := range []string{"pasó a «cancelado»", "❌ Pedido #1 cancelado", "Pidió hablar con una asesora"} {
		if !strings.Contains(notas, w) {
			t.Errorf("falta en las notas: %q\n%s", w, notas)
		}
	}
}

// Un Kommo caído (y lento) no cambia ni retrasa lo que el bot contesta: el mismo guion da las mismas respuestas con y
// sin CRM, y el turno no espera a Kommo.
func TestKommoCaidoNoCambiaLasRespuestas(t *testing.T) {
	correr := func(caido bool) ([]string, time.Duration) {
		b, st, fe := setup(t, `{}`, false)
		var reqs []agente.Request
		b.Agent = agenteGuion(t, guionVenta(), &reqs)
		if caido {
			_, k := conKommo(t, b, st)
			k.SetCaido(true)
			k.Demora = 2 * time.Second
		}
		inicio := time.Now()
		for _, m := range []string{"hola", "busco un vestido para una boda el 10 de octubre en la noche", "que me recomiendas", "V01", "M", "si"} {
			handle(b, text(m))
		}
		d := time.Since(inicio)
		fe.mu.Lock()
		defer fe.mu.Unlock()
		var out []string
		for _, m := range fe.sent {
			s, _ := m["text"].(string)
			if c, ok := m["caption"].(string); ok {
				s += "|" + c
			}
			out = append(out, s)
		}
		return out, d
	}
	sin, dSin := correr(false)
	con, dCon := correr(true)
	if strings.Join(sin, "\n---\n") != strings.Join(con, "\n---\n") {
		t.Fatalf("con Kommo caído el bot contestó distinto:\nsin CRM:\n%s\n\ncon CRM caído:\n%s", strings.Join(sin, "\n---\n"), strings.Join(con, "\n---\n"))
	}
	if dCon > dSin+time.Second {
		t.Fatalf("Kommo caído retrasó al bot: %v frente a %v", dCon, dSin)
	}
}
