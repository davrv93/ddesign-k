package kommo

import (
	"context"
	"encoding/json"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/kommo/simulado"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// 05-10-2026 14:41 en Lima (UTC−5).
var inicioPrueba = time.Date(2026, 10, 5, 19, 41, 0, 0, time.UTC)

func leadDe(t *testing.T, k *simulado.Kommo, clave string) simulado.Lead {
	t.Helper()
	for _, l := range k.LeadsDe(0) {
		if k.ValorDe(l, "ID kddesign") == clave {
			return l
		}
	}
	t.Fatalf("no hay lead con ID kddesign %q", clave)
	return simulado.Lead{}
}

func contactoDe(t *testing.T, k *simulado.Kommo, l simulado.Lead) *simulado.Contacto {
	t.Helper()
	if len(l.Contactos) == 0 || k.Contactos[l.Contactos[0]] == nil {
		t.Fatalf("el lead %q no tiene contacto", l.Nombre)
	}
	return k.Contactos[l.Contactos[0]]
}

var reLeerLead = regexp.MustCompile(`^/api/v4/leads/\d+`)

// lecturas cuenta los GET de un lead y los de un contacto.
func lecturas(k *simulado.Kommo) (leads, contactos int) {
	for _, p := range k.Peticiones {
		if p.Metodo != "GET" {
			continue
		}
		if reLeerLead.MatchString(p.Ruta) {
			leads++
		}
		if strings.HasPrefix(p.Ruta, "/api/v4/contacts/") {
			contactos++
		}
	}
	return
}

func web(t *testing.T, w EventoWeb) Evento {
	t.Helper()
	w.Canal = "web"
	ev, err := w.Evento()
	if err != nil {
		t.Fatal(err)
	}
	ev.Cuando = inicioPrueba
	return ev
}

// Cada formato de nombre, de lead y de contacto.
func TestNombresDeLeadYContacto(t *testing.T) {
	s, k, _ := preparar(t, false)
	casos := []struct {
		que      string
		ev       Evento
		lead     string
		contacto string
	}{
		{"WhatsApp con nombre", Evento{Canal: "whatsapp", Clave: "wa:1", ConversationID: 1, Nombre: "David Roncal", Telefono: "51987654692"},
			"David Roncal · WhatsApp", "David Roncal"},
		{"WhatsApp sin nombre, con teléfono", Evento{Canal: "whatsapp", Clave: "wa:2", ConversationID: 2, Telefono: "51987654693"},
			"WhatsApp +•••693", "WhatsApp +•••693"},
		{"WhatsApp sin nombre ni teléfono (LID)", Evento{Canal: "whatsapp", Clave: "wa:450", ConversationID: 450},
			"WhatsApp · conversación 450", "WhatsApp · conversación 450"},
		{"chat web sin nombre (la UI de prueba manda «Ana»)", web(t, EventoWeb{Conversacion: "web-0123456789abcdef", Nombre: "Ana", Etapa: "prospeccion"}),
			"Clienta web · 05/10 14:41", "Clienta web · 05/10 14:41"},
		{"chat web con el nombre que dijo la clienta", web(t, EventoWeb{Conversacion: "web-fedcba9876543210", Nombre: "Ana",
			Memoria: mem(`{"sabemos":{"nombre":"Lucía"}}`)}), "Lucía · Web", "Lucía"},
		{"demo de WhatsApp (sin cambios)", Evento{Canal: "whatsapp", Clave: "demo:01", Nombre: "Ana Demo", Telefono: "51900000001", Demo: true},
			"DEMO · Ana Demo · WhatsApp", "DEMO · Ana Demo"},
		{"demo web (sin cambios)", Evento{Canal: "web", Clave: "demo:02", Nombre: "Elena Demo", Demo: true},
			"DEMO · Elena Demo · chat web", "DEMO · Elena Demo"},
	}
	for _, c := range casos {
		aplicar(t, s, c.ev)
		l := leadDe(t, k, c.ev.Clave)
		if l.Nombre != c.lead {
			t.Errorf("%s: lead %q, se esperaba %q", c.que, l.Nombre, c.lead)
		}
		if ct := contactoDe(t, k, l); ct.Nombre != c.contacto {
			t.Errorf("%s: contacto %q, se esperaba %q", c.que, ct.Nombre, c.contacto)
		}
		if strings.Contains(l.Nombre, "987654") || strings.Contains(l.Nombre, "web-") || strings.Contains(l.Nombre, "Chat web") {
			t.Errorf("%s: el nombre lleva teléfono o id de sesión: %q", c.que, l.Nombre)
		}
	}
	if ct := contactoDe(t, k, leadDe(t, k, "wa:2")); ct.Telefono != "+51987654693" {
		t.Errorf("el teléfono completo va en el campo del contacto: %q", ct.Telefono)
	}
	// Los turnos siguientes no leen el lead para decidir el nombre (ya es el automático).
	g, _ := lecturas(k)
	ev := casos[3].ev
	ev.Etapa, ev.Cuando = "seguimiento", inicioPrueba.Add(2*time.Hour)
	aplicar(t, s, ev)
	if g2, _ := lecturas(k); g2 != g {
		t.Errorf("un turno más leyó el lead %d veces", g2-g)
	}
	if l := leadDe(t, k, ev.Clave); l.Nombre != "Clienta web · 05/10 14:41" {
		t.Errorf("el nombre web es el del inicio de la sesión, no el del último turno: %q", l.Nombre)
	}
}

// legado deja el lead y su contacto como los dejaba el código de antes del 05-10-2026: nombres viejos y un estado
// local sin nombre_lead ni inicio.
func legado(t *testing.T, k *simulado.Kommo, st *store.Store, clave, lead, contacto string, creado time.Time) {
	t.Helper()
	l := leadDe(t, k, clave)
	k.Leads[l.ID].Nombre = lead
	k.Leads[l.ID].Creado = creado.Unix()
	if contacto != "" {
		contactoDe(t, k, l).Nombre = contacto
	}
	v, err := st.VinculoKommo(context.Background(), clave)
	if err != nil {
		t.Fatal(err)
	}
	var e map[string]any
	_ = json.Unmarshal([]byte(v.Estado), &e)
	delete(e, "nombre_lead")
	delete(e, "inicio")
	raw, _ := json.Marshal(e)
	v.Estado = string(raw)
	if err := st.GuardarVinculoKommo(context.Background(), v); err != nil {
		t.Fatal(err)
	}
}

// Los leads con el nombre automático viejo se renombran al aplicar un evento (una lectura, una vez); los que una
// persona renombró a mano, no.
func TestRenombraSoloNombresAutomaticosViejos(t *testing.T) {
	s, k, st := preparar(t, false)
	sinNombre := Evento{Canal: "whatsapp", Clave: "wa:450", ConversationID: 450, Etapa: "prospeccion"}
	conTel := Evento{Canal: "whatsapp", Clave: "wa:451", ConversationID: 451, Telefono: "51987654692", Etapa: "prospeccion"}
	nombreLuego := Evento{Canal: "whatsapp", Clave: "wa:452", ConversationID: 452, Etapa: "prospeccion"}
	aMano := Evento{Canal: "whatsapp", Clave: "wa:453", ConversationID: 453, Etapa: "prospeccion"}
	cerrado := Evento{Canal: "whatsapp", Clave: "wa:454", ConversationID: 454, Etapa: "venta_confirmada", Sesion: 100, Pagado: true}
	webViejo := web(t, EventoWeb{Conversacion: "web-0123456789abcdef", Etapa: "prospeccion"})
	for _, ev := range []Evento{sinNombre, conTel, nombreLuego, aMano, cerrado, webViejo} {
		aplicar(t, s, ev)
	}
	legado(t, k, st, "wa:450", "Chat web wa:450 · WhatsApp", "Chat web wa:450", time.Now())
	legado(t, k, st, "wa:451", "+51987654692 · WhatsApp", "+51987654692", time.Now())
	legado(t, k, st, "wa:452", "Chat web wa:452 · WhatsApp", "Chat web wa:452", time.Now())
	legado(t, k, st, "wa:453", "Rosa (boda 12-oct, llamar)", "Rosa Vip", time.Now())
	legado(t, k, st, "wa:454", "Chat web wa:454 · WhatsApp", "Chat web wa:454", time.Now())
	legado(t, k, st, "web:web-0123456789abcdef", "Chat web web-0123 · chat web", "Chat web web-0123", inicioPrueba)

	nombreLuego.Nombre = "Carla Ruiz"
	webViejo.Cuando = inicioPrueba.Add(3 * time.Hour) // el nombre sale de la creación del lead, no de este turno
	cerrado.Etapa = "prospeccion"                     // misma sesión: el lead cerrado solo anota… y se renombra
	g0, c0 := lecturas(k)
	for _, ev := range []Evento{sinNombre, conTel, nombreLuego, aMano, cerrado, webViejo} {
		aplicar(t, s, ev)
	}
	esperado := map[string][2]string{
		"wa:450":                   {"WhatsApp · conversación 450", "WhatsApp · conversación 450"},
		"wa:451":                   {"WhatsApp +•••692", "WhatsApp +•••692"},
		"wa:452":                   {"Carla Ruiz · WhatsApp", "Carla Ruiz"},
		"wa:453":                   {"Rosa (boda 12-oct, llamar)", "Rosa Vip"}, // puesto a mano: no se toca
		"wa:454":                   {"WhatsApp · conversación 454", "WhatsApp · conversación 454"},
		"web:web-0123456789abcdef": {"Clienta web · 05/10 14:41", "Clienta web · 05/10 14:41"},
	}
	for clave, w := range esperado {
		l := leadDe(t, k, clave)
		if l.Nombre != w[0] {
			t.Errorf("%s: lead %q, se esperaba %q", clave, l.Nombre, w[0])
		}
		if c := contactoDe(t, k, l); c.Nombre != w[1] {
			t.Errorf("%s: contacto %q, se esperaba %q", clave, c.Nombre, w[1])
		}
	}
	if l := leadDe(t, k, "wa:454"); l.StatusID != EstadoGanado {
		t.Errorf("el lead cerrado solo cambia de nombre: quedó en %d", l.StatusID)
	}
	// Una lectura del lead por cada lead de antes (6), y del contacto solo en los que tenían el nombre viejo sin
	// nombre de clienta (450, 451, 454 y el web: el 452 lo pone contacto() con el nombre que ahora se sabe).
	g1, c1 := lecturas(k)
	if g1-g0 != 6 || c1-c0 != 4 {
		t.Errorf("lecturas: %d del lead (se esperaban 6) y %d de contactos (4)", g1-g0, c1-c0)
	}
	// Ya decidido: el turno siguiente no vuelve a leer nada, tampoco el renombrado a mano.
	for _, ev := range []Evento{sinNombre, conTel, nombreLuego, aMano, cerrado, webViejo} {
		aplicar(t, s, ev)
	}
	if g2, c2 := lecturas(k); g2 != g1 || c2 != c1 {
		t.Errorf("el segundo turno leyó otra vez: %d leads, %d contactos", g2-g1, c2-c1)
	}
}

// Un lead con el nombre automático nuevo sigue al día (la clienta dice su nombre), salvo que una persona lo haya
// renombrado en Kommo.
func TestNombreNuevoSigueAlDiaSalvoEditadoAMano(t *testing.T) {
	s, k, _ := preparar(t, false)
	a := Evento{Canal: "whatsapp", Clave: "wa:20", ConversationID: 20, Telefono: "51900000020", Etapa: "prospeccion"}
	b := Evento{Canal: "whatsapp", Clave: "wa:21", ConversationID: 21, Telefono: "51900000021", Etapa: "prospeccion"}
	aplicar(t, s, a)
	aplicar(t, s, b)
	k.Leads[leadDe(t, k, "wa:21").ID].Nombre = "Señora del vestido azul"
	a.Memoria, b.Memoria = mem(`{"sabemos":{"nombre":"Carla"}}`), mem(`{"sabemos":{"nombre":"Marta"}}`)
	aplicar(t, s, a)
	aplicar(t, s, b)
	if n := leadDe(t, k, "wa:20").Nombre; n != "Carla · WhatsApp" {
		t.Errorf("el automático se pone al día: %q", n)
	}
	if n := leadDe(t, k, "wa:21").Nombre; n != "Señora del vestido azul" {
		t.Errorf("el puesto a mano no se toca: %q", n)
	}
	// Y si el automático vuelve a cambiar, el editado a mano sigue sin tocarse.
	b.Memoria = mem(`{"sabemos":{"nombre":"Marta Gil"}}`)
	aplicar(t, s, b)
	if n := leadDe(t, k, "wa:21").Nombre; n != "Señora del vestido azul" {
		t.Errorf("el puesto a mano no se toca nunca: %q", n)
	}
}

// RenombrarWeb (kommo-seed --renombrar): los leads del chat web con el nombre viejo, que ya no reciben eventos.
func TestRenombrarWeb(t *testing.T) {
	s, k, st := preparar(t, false)
	ctx := context.Background()
	viejo := web(t, EventoWeb{Conversacion: "web-0123456789abcdef", Etapa: "prospeccion"})
	sinVinculo := web(t, EventoWeb{Conversacion: "web-aaaabbbbccccdddd", Etapa: "prospeccion"})
	aMano := web(t, EventoWeb{Conversacion: "web-1111222233334444", Etapa: "prospeccion"})
	wa := Evento{Canal: "whatsapp", Clave: "wa:9", ConversationID: 9, Etapa: "prospeccion"}
	for _, ev := range []Evento{viejo, sinVinculo, aMano, wa} {
		aplicar(t, s, ev)
	}
	legado(t, k, st, viejo.Clave, "Chat web web-0123 · chat web", "Chat web web-0123", inicioPrueba)
	legado(t, k, st, sinVinculo.Clave, "Chat web web-aaaa · chat web", "Chat web web-aaaa", inicioPrueba.Add(time.Hour))
	legado(t, k, st, aMano.Clave, "Chat web web-1111 · chat web (Lucía, boda)", "Chat web web-1111", inicioPrueba)
	legado(t, k, st, wa.Clave, "Chat web wa:9 · WhatsApp", "Chat web wa:9", inicioPrueba)
	if _, err := st.DB.Exec(`DELETE FROM kommo_vinculos WHERE clave = ?`, sinVinculo.Clave); err != nil {
		t.Fatal(err)
	}

	var vistos []string
	n, err := s.RenombrarWeb(ctx, func(v, nu string) { vistos = append(vistos, v+" → "+nu) })
	if err != nil || n != 2 || leadDe(t, k, viejo.Clave).Nombre != "Chat web web-0123 · chat web" {
		t.Fatalf("simular: %d %v %v (no debía escribir)", n, err, vistos)
	}
	if n, err = s.RenombrarWeb(ctx, nil); err != nil || n != 2 {
		t.Fatalf("renombrar: %d %v", n, err)
	}
	for clave, w := range map[string][2]string{
		viejo.Clave:      {"Clienta web · 05/10 14:41", "Clienta web · 05/10 14:41"},
		sinVinculo.Clave: {"Clienta web · 05/10 15:41", "Clienta web · 05/10 15:41"},
		aMano.Clave:      {"Chat web web-1111 · chat web (Lucía, boda)", "Chat web web-1111"},
		wa.Clave:         {"Chat web wa:9 · WhatsApp", "Chat web wa:9"}, // WhatsApp: lo hace --desde-base
	} {
		l := leadDe(t, k, clave)
		if l.Nombre != w[0] || contactoDe(t, k, l).Nombre != w[1] {
			t.Errorf("%s: lead %q contacto %q, se esperaba %q / %q", clave, l.Nombre, contactoDe(t, k, l).Nombre, w[0], w[1])
		}
	}
	// El vínculo local queda al día: si la sesión sigue, no se vuelve a leer el lead para el nombre.
	g, _ := lecturas(k)
	viejo.Etapa = "seguimiento"
	aplicar(t, s, viejo)
	if g2, _ := lecturas(k); g2 != g {
		t.Errorf("tras --renombrar, el turno siguiente leyó el lead %d veces", g2-g)
	}
	if n, _ := s.RenombrarWeb(ctx, nil); n != 0 {
		t.Errorf("la segunda pasada no tiene nada que renombrar: %d", n)
	}
}
