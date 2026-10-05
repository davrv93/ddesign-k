package kommo

import (
	"context"
	"encoding/json"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/kommo/simulado"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

const embudo = "Baruka · Ventas por WhatsApp"

func preparar(t *testing.T, transcripcion bool) (*Sincronizador, *simulado.Kommo, *store.Store) {
	t.Helper()
	k := simulado.Nuevo(token)
	t.Cleanup(k.Close)
	st, err := store.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.DB.Close() })
	catalogo := map[string]struct {
		n string
		p float64
	}{"V35": {"Vestido Irla", 290}, "V42": {"Vestido Gala Capa Azul", 260}, "V21": {"Conjunto Xela", 180}}
	s := NuevoSincronizador(clientePrueba(k), st, Opciones{Embudo: embudo, Transcripcion: transcripcion,
		PanelURL: "https://proyectopostventa.site/baruka",
		Producto: func(_ context.Context, c string) (string, float64, bool) {
			x, ok := catalogo[c]
			return x.n, x.p, ok
		},
		Envios: func(context.Context) map[string]float64 { return map[string]float64{"lima": 15, "provincia": 20} }})
	t.Cleanup(func() {
		if len(k.Errores) > 0 {
			t.Errorf("el sincronizador mandó peticiones mal formadas: %v", k.Errores)
		}
		if k.Violaciones > 0 {
			t.Errorf("se pasó el límite de tasa %d veces", k.Violaciones)
		}
	})
	return s, k, st
}

func mem(s string) json.RawMessage { return json.RawMessage(s) }

func unLead(t *testing.T, k *simulado.Kommo) simulado.Lead {
	t.Helper()
	e := k.EmbudoPorNombre(embudo)
	if e == nil {
		t.Fatal("no se creó el embudo")
	}
	ls := k.LeadsDe(e.ID)
	if len(ls) != 1 {
		t.Fatalf("se esperaba 1 lead, hay %d", len(ls))
	}
	return ls[0]
}

func estadoNombre(k *simulado.Kommo, id int64) string {
	for _, s := range k.EmbudoPorNombre(embudo).Estados {
		if s.ID == id {
			return s.Name
		}
	}
	return "?"
}

func aplicar(t *testing.T, s *Sincronizador, ev Evento) {
	t.Helper()
	if err := s.Aplicar(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
}

// Chat web: un contacto sin teléfono por sesión, un lead que sigue las etapas, sus campos, etiquetas y notas; y con
// la venta confirmada el precio suma el envío.
func TestChatWebDePuntaAPunta(t *testing.T) {
	s, k, _ := preparar(t, true)
	web := func(w EventoWeb) Evento {
		w.Canal, w.Conversacion = "web", "sesion-abc123"
		ev, err := w.Evento()
		if err != nil {
			t.Fatal(err)
		}
		return ev
	}
	aplicar(t, s, web(EventoWeb{Etapa: "prospeccion", Mensaje: "hola, busco un vestido para una boda", Respuesta: "¡Qué lindo! ¿Para cuándo es?",
		Memoria: mem(`{"temperatura":"frio","sabemos":{"ocasion":"boda"}}`)}))
	l := unLead(t, k)
	if estadoNombre(k, l.StatusID) != "Prospección" || k.ValorDe(l, "Ocasión") != "boda" || k.ValorDe(l, "Canal") != "Web" {
		t.Fatalf("primer turno: estado %s, ocasión %q, canal %q", estadoNombre(k, l.StatusID), k.ValorDe(l, "Ocasión"), k.ValorDe(l, "Canal"))
	}
	if len(k.Contactos) != 1 || len(l.Contactos) != 1 {
		t.Fatalf("un contacto ligado al lead: %d contactos, lead con %v", len(k.Contactos), l.Contactos)
	}
	for _, c := range k.Contactos {
		if c.Telefono != "" || !strings.Contains(c.Nombre, "Chat web") {
			t.Fatalf("el contacto web no lleva teléfono y se nombra por la sesión: %+v", c)
		}
	}
	aplicar(t, s, web(EventoWeb{Etapa: "seguimiento", Intencion: "consulta_material", Sugerencias: []string{"V35"},
		Mensaje: "de qué tela es?", Respuesta: "Es de crepe 😊",
		Memoria: mem(`{"producto":"V35","temperatura":"caliente","temperatura_motivo":"evento el 10-oct (en 5 días)","sabemos":{"ocasion":"boda","fecha_iso":"2026-10-10","horario":"noche"}}`)}))
	l = unLead(t, k)
	if estadoNombre(k, l.StatusID) != "Seguimiento" || k.ValorDe(l, "Temperatura") != "Caliente" || k.ValorDe(l, "Día o noche") != "Noche" ||
		k.ValorDe(l, "Prenda en foco") != "V35 · Vestido Irla" || l.Precio != 290 {
		t.Fatalf("seguimiento: %s %q %q %q %d", estadoNombre(k, l.StatusID), k.ValorDe(l, "Temperatura"), k.ValorDe(l, "Día o noche"), k.ValorDe(l, "Prenda en foco"), l.Precio)
	}
	if f, _ := time.ParseInLocation("2006-01-02", "2026-10-10", lima); k.ValorDe(l, "Fecha del evento") != itoa(f.Add(12*time.Hour).Unix()) {
		t.Fatalf("fecha del evento en Unix (mediodía de Lima): %s", k.ValorDe(l, "Fecha del evento"))
	}
	if !contieneTag(l.Tags, "caliente") || contieneTag(l.Tags, "fría") || !contieneTag(l.Tags, "web") {
		t.Fatalf("etiquetas: %v", l.Tags)
	}
	aplicar(t, s, web(EventoWeb{Etapa: "cierre", Accion: "pedido", Codigo: "V35", Talla: "M",
		Memoria: mem(`{"producto":"V35","temperatura":"caliente","sabemos":{"talla":"M","cita":"2026-10-09T17:00"}}`)}))
	aplicar(t, s, web(EventoWeb{Etapa: "venta_confirmada", Mensaje: "sí, confirmo, es para Lima", EnvioCosto: 15,
		Memoria: mem(`{"producto":"V35","temperatura":"caliente","sabemos":{"talla":"M","envio":"lima","cita":"2026-10-09T17:00"}}`)}))
	l = unLead(t, k)
	if estadoNombre(k, l.StatusID) != "Venta confirmada" || l.Precio != 305 || k.ValorDe(l, "Ciudad / envío") != "Lima" || k.ValorDe(l, "Talla") != "M" {
		t.Fatalf("venta confirmada: %s precio %d envío %q talla %q", estadoNombre(k, l.StatusID), l.Precio, k.ValorDe(l, "Ciudad / envío"), k.ValorDe(l, "Talla"))
	}
	notas := strings.Join(k.NotasDe(l.ID), "\n")
	for _, w := range []string{"Preguntó por la tela del V35", "Se le mostró: V35 Vestido Irla", "Prenda en foco: V35 · Vestido Irla (S/ 290.00)",
		"Clienta caliente: evento el 10-oct", "Eligió talla M del V35", "Cita para probarse agendada el vie 9-oct 17:00 (V35 talla M)",
		"Venta confirmada en el chat: V35 · Vestido Irla talla M", "Clienta: de qué tela es?", "Bot: Es de crepe"} {
		if !strings.Contains(notas, w) {
			t.Errorf("falta en las notas: %q\n%s", w, notas)
		}
	}
	if len(k.Contactos) != 1 || len(k.LeadsDe(0)) != 1 {
		t.Fatalf("cuatro turnos, un contacto y un lead: %d / %d", len(k.Contactos), len(k.LeadsDe(0)))
	}
}

func itoa(n int64) string { return strconv.FormatInt(n, 10) }

func contieneTag(ts []string, t string) bool {
	for _, x := range ts {
		if x == t {
			return true
		}
	}
	return false
}

// Un turno sin cambios no manda un PATCH; las etiquetas puestas a mano en Kommo se conservan cuando cambian las nuestras.
func TestSoloMandaLoQueCambiaYRespetaEtiquetasManuales(t *testing.T) {
	s, k, _ := preparar(t, false)
	ev := Evento{Canal: "whatsapp", Clave: "wa:7", ConversationID: 7, Nombre: "Ana López", Telefono: "51900000001", Etapa: "prospeccion",
		Memoria: mem(`{"temperatura":"frio"}`)}
	aplicar(t, s, ev)
	patch := k.Cuenta("PATCH", "/api/v4/leads/")
	aplicar(t, s, ev)
	if k.Cuenta("PATCH", "/api/v4/leads/") != patch || len(k.NotasDe(unLead(t, k).ID)) != 0 { // la «fría» de arranque no se anota
		t.Fatalf("un turno igual no debía tocar el lead ni anotar de nuevo (patch %d→%d, notas %v)", patch, k.Cuenta("PATCH", "/api/v4/leads/"), k.NotasDe(unLead(t, k).ID))
	}
	l := unLead(t, k)
	k.Leads[l.ID].Tags = append(k.Leads[l.ID].Tags, "vip") // la asesora la puso a mano
	ev.Memoria = mem(`{"temperatura":"tibio"}`)
	aplicar(t, s, ev)
	l = unLead(t, k)
	if !contieneTag(l.Tags, "vip") || !contieneTag(l.Tags, "tibia") || contieneTag(l.Tags, "fría") {
		t.Fatalf("etiquetas: %v", l.Tags)
	}
	if k.ValorDe(l, "Conversación en kddesign") != "https://proyectopostventa.site/baruka/conversaciones/?c=7" {
		t.Fatalf("enlace al panel: %q", k.ValorDe(l, "Conversación en kddesign"))
	}
}

// WhatsApp: una clienta, un contacto (se busca por teléfono antes de crearlo); si se perdió el vínculo local, el lead
// se encuentra por «ID kddesign» y no se duplica.
func TestNoDuplicaContactoNiLead(t *testing.T) {
	s, k, st := preparar(t, false)
	ev := Evento{Canal: "whatsapp", Clave: "wa:3", ConversationID: 3, Nombre: "Rosa", Telefono: "51900000003", Etapa: "seguimiento"}
	aplicar(t, s, ev)
	if _, err := st.DB.Exec(`DELETE FROM kommo_vinculos`); err != nil {
		t.Fatal(err)
	}
	aplicar(t, s, ev)
	if len(k.Contactos) != 1 || len(k.LeadsDe(0)) != 1 {
		t.Fatalf("sin vínculo local no debía duplicar: %d contactos, %d leads", len(k.Contactos), len(k.LeadsDe(0)))
	}
	// Otra conversación de la misma clienta (otro chat) reutiliza el contacto.
	aplicar(t, s, Evento{Canal: "whatsapp", Clave: "wa:4", ConversationID: 4, Telefono: "51900000003", Etapa: "prospeccion"})
	if len(k.Contactos) != 1 || len(k.LeadsDe(0)) != 2 {
		t.Fatalf("mismo teléfono, mismo contacto: %d contactos, %d leads", len(k.Contactos), len(k.LeadsDe(0)))
	}
	for _, c := range k.Contactos {
		if c.Telefono != "+51900000003" || c.Nombre != "Rosa" {
			t.Fatalf("contacto: %+v", c)
		}
	}
}

// Pedido: confirmado → «Venta confirmada» con el envío en el precio; comprobante → «Venta pagada»; cancelado desde el
// tablero después de confirmar → «Venta perdida». Y «Venta confirmada» no vuelve atrás sola.
func TestPedidoMueveElEmbudo(t *testing.T) {
	s, k, _ := preparar(t, false)
	base := Evento{Canal: "whatsapp", Clave: "wa:9", ConversationID: 9, Telefono: "51900000009", Etapa: "cierre",
		Memoria: mem(`{"producto":"V35","sabemos":{"envio":"provincia","ciudad":"arequipa"}}`)}
	ev := base
	ev.Pedido = &Pedido{ID: 14, Estado: "pendiente", Total: 290, Codigo: "V35", Nombre: "Vestido Irla", Talla: "M", Cantidad: 1}
	aplicar(t, s, ev)
	if l := unLead(t, k); estadoNombre(k, l.StatusID) != "Cierre" || l.Precio != 290 {
		t.Fatalf("pendiente: %s %d", estadoNombre(k, l.StatusID), l.Precio)
	}
	ev.Pedido = &Pedido{ID: 14, Estado: "confirmado", Total: 290, Codigo: "V35", Nombre: "Vestido Irla", Talla: "M", Cantidad: 1}
	ev.Etapa = "venta_confirmada"
	aplicar(t, s, ev)
	l := unLead(t, k)
	if estadoNombre(k, l.StatusID) != "Venta confirmada" || l.Precio != 310 || k.ValorDe(l, "Ciudad / envío") != "Provincia · Arequipa" ||
		k.ValorDe(l, "Pedido kddesign") != "#14 · confirmado · S/ 290.00" {
		t.Fatalf("confirmado: %s %d %q %q", estadoNombre(k, l.StatusID), l.Precio, k.ValorDe(l, "Ciudad / envío"), k.ValorDe(l, "Pedido kddesign"))
	}
	ev.Etapa = "seguimiento" // el agente cree que dudó: el lead no vuelve atrás solo
	aplicar(t, s, ev)
	if l := unLead(t, k); estadoNombre(k, l.StatusID) != "Venta confirmada" {
		t.Fatalf("venta confirmada no retrocede sola: %s", estadoNombre(k, l.StatusID))
	}
	ev.Pagado = true
	aplicar(t, s, ev)
	if l := unLead(t, k); l.StatusID != EstadoGanado {
		t.Fatalf("con comprobante, venta pagada: %s", estadoNombre(k, l.StatusID))
	}
	notas := strings.Join(k.NotasDe(l.ID), "\n")
	for _, w := range []string{"Resumen del pedido #14: V35 talla M ×1 · S/ 290.00 (falta su SI)", "Pedido #14 confirmado: V35 talla M ×1",
		"Comprobante de pago recibido (pedido #14). Falta validarlo."} {
		if !strings.Contains(notas, w) {
			t.Errorf("falta en las notas: %q\n%s", w, notas)
		}
	}
	// Otro pedido que se cancela desde el tablero después de confirmarlo: venta perdida.
	s2, k2, _ := preparar(t, false)
	ev2 := base
	ev2.Clave, ev2.Etapa = "wa:10", "venta_confirmada"
	ev2.Pedido = &Pedido{ID: 20, Estado: "confirmado", Total: 180, Codigo: "V21", Cantidad: 1}
	aplicar(t, s2, ev2)
	ev2.Pedido = &Pedido{ID: 20, Estado: "cancelado", Total: 180, Codigo: "V21", Cantidad: 1}
	aplicar(t, s2, ev2)
	if l := k2.LeadsDe(0)[0]; l.StatusID != EstadoPerdido {
		t.Fatalf("cancelado tras confirmar: perdido, quedó %d", l.StatusID)
	}
}

// Lead cerrado: los mensajes de la misma sesión solo anotan; una sesión nueva es otra venta → otro lead, mismo contacto.
func TestLeadCerradoYSesionNueva(t *testing.T) {
	s, k, _ := preparar(t, false)
	ev := Evento{Canal: "whatsapp", Clave: "wa:5", ConversationID: 5, Telefono: "51900000005", Etapa: "venta_confirmada", Sesion: 100, Pagado: true,
		Pedido: &Pedido{ID: 30, Estado: "confirmado", Total: 260, Codigo: "V42", Cantidad: 1}}
	aplicar(t, s, ev)
	aplicar(t, s, Evento{Canal: "whatsapp", Clave: "wa:5", ConversationID: 5, Telefono: "51900000005", Etapa: "prospeccion", Sesion: 100,
		Hitos: []string{"gracias"}})
	if n := len(k.LeadsDe(0)); n != 1 {
		t.Fatalf("misma sesión: sigue el mismo lead, hay %d", n)
	}
	aplicar(t, s, Evento{Canal: "whatsapp", Clave: "wa:5", ConversationID: 5, Telefono: "51900000005", Etapa: "prospeccion", Sesion: 999})
	ls := k.LeadsDe(0)
	if len(ls) != 2 || ls[0].StatusID != EstadoGanado || estadoNombre(k, ls[1].StatusID) != "Prospección" || len(k.Contactos) != 1 {
		t.Fatalf("sesión nueva tras un lead cerrado: otro lead, mismo contacto (%d leads, %d contactos)", len(ls), len(k.Contactos))
	}
}

// La transcripción no lleva datos de pago ni números largos.
func TestTranscripcionSinDatosDePago(t *testing.T) {
	s, k, _ := preparar(t, true)
	aplicar(t, s, Evento{Canal: "whatsapp", Clave: "wa:6", Telefono: "51900000006", Etapa: "venta_confirmada", Turno: []Linea{
		{"cliente", "listo, ¿a dónde pago?"},
		{"bot", "💳 *Datos para el pago*\n• Yape: 999 888 777 (Fulana)"},
		{"cliente", "mi otro número es 987654321"}}})
	n := strings.Join(k.NotasDe(unLead(t, k).ID), "\n")
	if strings.Contains(n, "999") || strings.Contains(n, "Yape") || strings.Contains(n, "987654321") || !strings.Contains(n, "[datos de pago omitidos]") {
		t.Fatalf("la transcripción filtró datos: %s", n)
	}
}

// Encolar no espera a Kommo: con Kommo caído y lento, 20 eventos se encolan al instante y el trabajador no se cuelga.
func TestEncolarNoBloqueaConKommoCaido(t *testing.T) {
	s, k, _ := preparar(t, false)
	k.SetCaido(true)
	k.Demora = 300 * time.Millisecond
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.Iniciar(ctx)
	inicio := time.Now()
	for i := 0; i < 20; i++ {
		s.Encolar(Evento{Canal: "web", Clave: "web:caido", Etapa: "prospeccion"})
	}
	if d := time.Since(inicio); d > 50*time.Millisecond {
		t.Fatalf("Encolar tardó %v: no debe esperar a Kommo", d)
	}
	k.SetCaido(false)
	k.Demora = 0
	if !s.Esperar(2 * time.Minute) {
		t.Fatal("el trabajador no vació la cola")
	}
	k.Errores = nil
}

// Una cola llena descarta, no bloquea.
func TestColaLlenaDescarta(t *testing.T) {
	k := simulado.Nuevo(token)
	defer k.Close()
	st, _ := store.Open(t.TempDir())
	defer st.DB.Close()
	s := NuevoSincronizador(clientePrueba(k), st, Opciones{Cola: 2})
	inicio := time.Now()
	for i := 0; i < 10; i++ {
		s.Encolar(Evento{Clave: "web:x"}) // sin trabajador: se llenan 2 y el resto se descarta
	}
	if time.Since(inicio) > 50*time.Millisecond {
		t.Fatal("con la cola llena, Encolar no debe bloquear")
	}
}

func TestEventoWebValida(t *testing.T) {
	for _, w := range []EventoWeb{{Canal: "whatsapp", Conversacion: "abcd"}, {Canal: "web", Conversacion: "../x"}, {Canal: "web", Conversacion: "ab"}} {
		if _, err := w.Evento(); err == nil {
			t.Errorf("debía rechazar %+v", w)
		}
	}
}
