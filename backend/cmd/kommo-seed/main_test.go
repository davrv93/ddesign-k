package main

import (
	"context"
	"encoding/json"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/bot"
	"github.com/davrv93/ddesign-k/backend/internal/kommo"
	"github.com/davrv93/ddesign-k/backend/internal/kommo/simulado"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// Catálogo de prueba con la forma del de producción (/baruka/api/public/catalog).
var catPrueba = map[string]producto{
	"V21": {Code: "V21", Name: "Vestido Kendall", Price: 320}, "V24": {Code: "V24", Name: "Conjunto Kabanova Azul", Price: 330},
	"V28": {Code: "V28", Name: "Vestido Azra Turquesa", Price: 520}, "V31": {Code: "V31", Name: "Vestido Pandora", Price: 330},
	"V35": {Code: "V35", Name: "Vestido Irla", Price: 220}, "V38": {Code: "V38", Name: "Blazer Begonia", Price: 288},
	"V41": {Code: "V41", Name: "Vestido Holly", Price: 330}, "V42": {Code: "V42", Name: "Vestido Gala Capa Azul", Price: 260},
}

func sincPrueba(t *testing.T) (*kommo.Sincronizador, *simulado.Kommo, *store.Store) {
	t.Helper()
	k := simulado.Nuevo("tok")
	t.Cleanup(k.Close)
	st, err := store.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.DB.Close() })
	c := kommo.Nuevo(k.URL, "tok")
	c.EsperaMin, c.EsperaMax = 5*time.Millisecond, 50*time.Millisecond
	s := kommo.NuevoSincronizador(c, st, kommo.Opciones{Embudo: "Baruka · Ventas por WhatsApp",
		Producto: func(_ context.Context, cod string) (string, float64, bool) {
			p, ok := catPrueba[cod]
			return p.Name, p.Price, ok
		},
		Envios: func(context.Context) map[string]float64 { return map[string]float64{"lima": 15, "provincia": 20} }})
	t.Cleanup(func() {
		if len(k.Errores) > 0 || k.Violaciones > 0 {
			t.Errorf("errores %v / violaciones %d", k.Errores, k.Violaciones)
		}
	})
	return s, k, st
}

// Humo del seed contra el Kommo simulado: 24 leads demo repartidos por todas las etapas (incluidos ganado y perdido) y
// temperaturas, con prendas del catálogo, etiqueta «demo» y teléfonos del rango reservado. Correrlo dos veces no
// duplica; --limpiar los cierra como perdidos.
func TestSeedDemo(t *testing.T) {
	s, k, st := sincPrueba(t)
	ctx := context.Background()
	hoy := time.Date(2026, 10, 5, 10, 0, 0, 0, time.UTC)
	if err := aplicarTodos(ctx, s, Demo(catPrueba, hoy), false); err != nil {
		t.Fatal(err)
	}
	emb := k.EmbudoPorNombre("Baruka · Ventas por WhatsApp")
	ls := k.LeadsDe(emb.ID)
	if len(ls) != len(filas) || len(filas) < 20 || len(filas) > 30 {
		t.Fatalf("se esperaban %d leads demo (20–30), hay %d", len(filas), len(ls))
	}
	porEstado, porTemp := map[string]int{}, map[string]int{}
	for _, l := range ls {
		nombre := "?"
		for _, e := range emb.Estados {
			if e.ID == l.StatusID {
				nombre = e.Name
			}
		}
		porEstado[nombre]++
		porTemp[k.ValorDe(l, "Temperatura")]++
		if !strings.HasPrefix(l.Nombre, "DEMO · ") || !contiene(l.Tags, "demo") {
			t.Errorf("lead demo sin marcar: %q %v", l.Nombre, l.Tags)
		}
		if p := k.ValorDe(l, "Prenda en foco"); p != "" && !strings.Contains(p, " · ") {
			t.Errorf("prenda sin nombre del catálogo: %q", p)
		}
	}
	for _, e := range []string{"Prospección", "Seguimiento", "Cierre", "Venta confirmada", "Venta pagada", "Venta perdida"} {
		if porEstado[e] == 0 {
			t.Errorf("ningún lead demo en %q: %v", e, porEstado)
		}
	}
	for _, tp := range []string{"Fría", "Tibia", "Caliente"} {
		if porTemp[tp] == 0 {
			t.Errorf("ningún lead demo %q: %v", tp, porTemp)
		}
	}
	for _, c := range k.Contactos {
		if c.Telefono != "" && !strings.HasPrefix(c.Telefono, "+51900000") {
			t.Errorf("teléfono demo fuera del rango reservado: %s", c.Telefono)
		}
		if !strings.HasPrefix(c.Nombre, "DEMO · ") {
			t.Errorf("contacto demo sin marcar: %q", c.Nombre)
		}
	}
	var notas int
	for _, l := range ls {
		notas += len(k.NotasDe(l.ID))
	}
	if notas < len(ls) {
		t.Errorf("cada lead demo lleva al menos una nota: %d notas", notas)
	}
	// Idempotente.
	if err := aplicarTodos(ctx, s, Demo(catPrueba, hoy), false); err != nil {
		t.Fatal(err)
	}
	if n := len(k.LeadsDe(emb.ID)); n != len(filas) {
		t.Fatalf("la segunda corrida duplicó: %d leads", n)
	}
	// Un lead que no es demo no se toca al limpiar.
	if err := s.Aplicar(ctx, kommo.Evento{Canal: "whatsapp", Clave: "wa:1", Telefono: "51911111111", Etapa: "seguimiento"}); err != nil {
		t.Fatal(err)
	}
	if err := limpiarDemo(ctx, s); err != nil {
		t.Fatal(err)
	}
	for _, l := range k.LeadsDe(emb.ID) {
		esDemo := contiene(l.Tags, "demo")
		if esDemo && l.StatusID != kommo.EstadoPerdido {
			t.Errorf("lead demo sin cerrar: %q", l.Nombre)
		}
		if !esDemo && l.StatusID == kommo.EstadoPerdido {
			t.Errorf("--limpiar tocó un lead real: %q", l.Nombre)
		}
	}
	if v, _ := st.VinculoKommo(ctx, "demo:01"); v != nil {
		t.Error("los vínculos demo debían olvidarse")
	}
}

func contiene(xs []string, x string) bool {
	for _, y := range xs {
		if y == x {
			return true
		}
	}
	return false
}

// --desde-base con --dry-run: muestra el plan de cada conversación de la SQLite sin hacer una sola petición; sin
// --dry-run las sube.
func TestSeedDesdeBase(t *testing.T) {
	s, k, st := sincPrueba(t)
	ctx := context.Background()
	if err := st.SaveProduct(ctx, &store.Product{Code: "V35", Name: "Vestido Irla", Price: 220, Active: true,
		Variants: []store.Variant{{Size: "M", Stock: 3}}}); err != nil {
		t.Fatal(err)
	}
	_, conv, err := st.UpsertCustomer(ctx, "51900000077@s.whatsapp.net", "51900000077", "Clienta Base")
	if err != nil {
		t.Fatal(err)
	}
	ctxJSON, _ := json.Marshal(map[string]any{"etapa": "cierre", "memoria": map[string]any{"producto": "V35", "temperatura": "caliente",
		"sabemos": map[string]any{"ocasion": "boda", "talla": "M"}}})
	_ = st.SetConversationState(ctx, conv.ID, "", string(ctxJSON))
	_ = st.AddMessage(ctx, &store.Message{ConversationID: conv.ID, Direction: "in", Kind: "text", Body: "me gusta el Irla", Author: "cliente"})
	conv, _ = st.GetConversation(ctx, conv.ID)

	evs := []kommo.Evento{bot.EventoDeConversacion(ctx, st, conv)}
	plan := s.Simular(ctx, evs[0]).String()
	for _, w := range []string{"wa:", "crear", "Cierre", "V35 · Vestido Irla", "precio 220", "Importado de kddesign", "•••"} {
		if !strings.Contains(plan, w) {
			t.Errorf("falta %q en el plan:\n%s", w, plan)
		}
	}
	if err := aplicarTodos(ctx, s, evs, true); err != nil {
		t.Fatal(err)
	}
	if len(k.Peticiones) != 0 {
		t.Fatalf("--dry-run no debe llamar a Kommo: %d peticiones", len(k.Peticiones))
	}
	if err := aplicarTodos(ctx, s, evs, false); err != nil {
		t.Fatal(err)
	}
	ls := k.LeadsDe(0)
	if len(ls) != 1 || k.ValorDe(ls[0], "Ocasión") != "boda" || k.ValorDe(ls[0], "Talla") != "M" {
		t.Fatalf("volcado: %+v", ls)
	}
}

// viejo deja el lead y su contacto como los nombraba el código de antes del 05-10-2026 (y el vínculo local sin el
// nombre automático guardado).
func viejo(t *testing.T, k *simulado.Kommo, st *store.Store, clave, lead, contacto string) *simulado.Lead {
	t.Helper()
	v, err := st.VinculoKommo(context.Background(), clave)
	if err != nil {
		t.Fatal(err)
	}
	l := k.Leads[v.LeadID]
	l.Nombre = lead
	k.Contactos[v.ContactID].Nombre = contacto
	var e map[string]any
	_ = json.Unmarshal([]byte(v.Estado), &e)
	delete(e, "nombre_lead")
	delete(e, "inicio")
	raw, _ := json.Marshal(e)
	v.Estado = string(raw)
	if err := st.GuardarVinculoKommo(context.Background(), v); err != nil {
		t.Fatal(err)
	}
	return l
}

// Tras desplegar: --desde-base renombra los leads de WhatsApp con el nombre viejo y --renombrar los del chat web;
// los renombrados a mano se quedan como están.
func TestSeedRenombraLeadsViejos(t *testing.T) {
	s, k, st := sincPrueba(t)
	ctx := context.Background()
	_, conv, err := st.UpsertCustomer(ctx, "51900000078@s.whatsapp.net", "51900000078", "")
	if err != nil {
		t.Fatal(err)
	}
	_, conv2, err := st.UpsertCustomer(ctx, "51900000079@s.whatsapp.net", "51900000079", "")
	if err != nil {
		t.Fatal(err)
	}
	desdeBase := func() {
		var evs []kommo.Evento
		for _, id := range []int64{conv.ID, conv2.ID} {
			c, _ := st.GetConversation(ctx, id)
			evs = append(evs, bot.EventoDeConversacion(ctx, st, c))
		}
		if err := aplicarTodos(ctx, s, evs, false); err != nil {
			t.Fatal(err)
		}
	}
	desdeBase()
	w, err := kommo.EventoWeb{Canal: "web", Conversacion: "web-0123456789abcdef", Etapa: "prospeccion"}.Evento()
	if err != nil {
		t.Fatal(err)
	}
	w.Cuando = time.Date(2026, 10, 5, 19, 41, 0, 0, time.UTC)
	if err := s.Aplicar(ctx, w); err != nil {
		t.Fatal(err)
	}
	waViejo := viejo(t, k, st, "wa:"+itoa(conv.ID), "+51900000078 · WhatsApp", "+51900000078")
	aMano := viejo(t, k, st, "wa:"+itoa(conv2.ID), "Clienta de Arequipa", "+51900000079")
	webViejo := viejo(t, k, st, w.Clave, "Chat web web-0123 · chat web", "Chat web web-0123")
	webViejo.Creado = w.Cuando.Unix()

	desdeBase()
	if err := renombrarWeb(ctx, s, false); err != nil {
		t.Fatal(err)
	}
	if waViejo.Nombre != "WhatsApp +•••078" || k.Contactos[waViejo.Contactos[0]].Nombre != "WhatsApp +•••078" {
		t.Errorf("WhatsApp viejo: lead %q contacto %q", waViejo.Nombre, k.Contactos[waViejo.Contactos[0]].Nombre)
	}
	if aMano.Nombre != "Clienta de Arequipa" {
		t.Errorf("el renombrado a mano no se toca: %q", aMano.Nombre)
	}
	if webViejo.Nombre != "Clienta web · 05/10 14:41" || k.Contactos[webViejo.Contactos[0]].Nombre != "Clienta web · 05/10 14:41" {
		t.Errorf("web viejo: lead %q contacto %q", webViejo.Nombre, k.Contactos[webViejo.Contactos[0]].Nombre)
	}
	if n := len(k.LeadsDe(0)); n != 3 {
		t.Errorf("renombrar no crea leads: %d", n)
	}
}

func itoa(n int64) string { return strconv.FormatInt(n, 10) }
