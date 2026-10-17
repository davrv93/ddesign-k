package bot

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
	"github.com/davrv93/ddesign-k/backend/internal/store"
	"github.com/davrv93/ddesign-k/backend/internal/whatsapp/cloud"
	"github.com/davrv93/ddesign-k/backend/internal/whatsapp/cloud/simulado"
)

func memProducto(codigo string, extra string) json.RawMessage {
	return json.RawMessage(fmt.Sprintf(`{"etapa":"seguimiento","producto":%q,"temperatura":"tibio","sabemos":{"ocasion":"boda"%s}}`, codigo, extra))
}

func textosA(fe *fakeEvolution, desde int) []string {
	fe.mu.Lock()
	defer fe.mu.Unlock()
	var out []string
	for _, m := range fe.sent[desde:] {
		if t, ok := m["text"].(string); ok {
			out = append(out, t)
		} else if c, ok := m["caption"].(string); ok {
			out = append(out, "[img] "+c)
		}
	}
	return out
}

func enviadosN(fe *fakeEvolution) int {
	fe.mu.Lock()
	defer fe.mu.Unlock()
	return len(fe.sent)
}

// Seguimiento por modelo: se arma al quedar esperando con un modelo en foco, sale paso a paso a su hora, se pausa si
// ella escribe y sigue desde el siguiente paso cuando vuelve a callarse; respeta horario, tope y ventana de 24 h.
func TestSecuenciaDeSeguimiento(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	ctx := context.Background()
	_ = st.SetSetting(ctx, store.SettingSeguimientoHorario, "00:00-24:00")
	p, _ := st.GetProductByCode(ctx, "V01")
	if _, err := st.GuardarContenido(ctx, p.ID, "beneficio", "Su tela no se arruga y estiliza la figura.", "/media/beneficio.jpg", "web"); err != nil {
		t.Fatal(err)
	}
	q := &store.Secuencia{Nombre: "Vestidos", Activa: true, Pasos: []store.Paso{
		{EsperaMin: 60, Tipo: "emoji", Texto: "🥰"},
		{EsperaMin: 300, Tipo: "beneficio", Texto: "{nombre}, mira lo que tiene el {modelo}:"},
		{EsperaMin: 300, Tipo: "tip"}, // sin tip cargado: se salta
		{EsperaMin: 300, Tipo: "texto", Texto: "¿Te ayudo a elegir la talla?"},
	}}
	if err := st.CrearSecuencia(ctx, q); err != nil {
		t.Fatal(err)
	}
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"me gusta el esmeralda": {Accion: "responder", Respuesta: "¡Es precioso! ¿Para qué ocasión?", Memoria: memProducto("V01", "")},
		"ah ok":                 {Accion: "responder", Respuesta: "Cuéntame si tienes dudas 😊", Memoria: memProducto("V01", "")},
	}, &reqs)

	handle(b, text("me gusta el esmeralda"))
	conv, _ := st.ConversationByCustomer(ctx, 1)
	e, _ := st.SeguimientoDe(ctx, conv.ID)
	if e == nil || e.Estado != "activo" || e.Paso != 1 {
		t.Fatalf("se arma con el modelo en foco: %+v", e)
	}
	ahora := time.Now()
	if n := b.TickSeguimiento(ctx, ahora); n != 0 {
		t.Fatal("antes de la hora no sale nada")
	}
	base := enviadosN(fe)
	if n := b.TickSeguimiento(ctx, ahora.Add(61*time.Minute)); n != 1 {
		t.Fatal("a la hora sale el emoji")
	}
	b.Drain(5 * time.Second)
	if got := textosA(fe, base); len(got) != 1 || got[0] != "🥰" {
		t.Fatalf("emoji: %v", got)
	}
	// Segundo paso: beneficio con su imagen y los marcadores reemplazados.
	base = enviadosN(fe)
	b.TickSeguimiento(ctx, ahora.Add(61*time.Minute+301*time.Minute))
	b.Drain(5 * time.Second)
	got := textosA(fe, base)
	if len(got) != 1 || !strings.HasPrefix(got[0], "[img] Ana, mira lo que tiene el Vestido Esmeralda:") ||
		!strings.Contains(got[0], "no se arruga") {
		t.Fatalf("beneficio: %v", got)
	}
	// Ella escribe: se pausa. Al callarse otra vez, sigue con el paso 3 (que se salta) y luego el 4.
	handle(b, text("ah ok"))
	if e, _ := st.SeguimientoDe(ctx, conv.ID); e.Estado != "activo" || e.Paso != 3 {
		t.Fatalf("tras responder y callarse, sigue desde el paso 3: %+v", e)
	}
	base = enviadosN(fe)
	b.TickSeguimiento(ctx, time.Now().Add(301*time.Minute)) // el tip no existe: se salta y se programa el 4
	b.TickSeguimiento(ctx, time.Now().Add(602*time.Minute))
	b.Drain(5 * time.Second)
	if got := textosA(fe, base); len(got) != 1 || got[0] != "¿Te ayudo a elegir la talla?" {
		t.Fatalf("paso 4: %v", got)
	}
	if e, _ := st.SeguimientoDe(ctx, conv.ID); e.Estado != "terminado" {
		t.Fatalf("terminó: %+v", e)
	}
	var motivo string
	_ = st.DB.QueryRowContext(ctx, `SELECT motivo FROM seguimiento_envios WHERE conversation_id=? AND resultado='omitido'`, conv.ID).Scan(&motivo)
	if !strings.Contains(motivo, "no tiene tip cargado") {
		t.Fatalf("el paso saltado queda explicado: %q", motivo)
	}
	var respondio int
	_ = st.DB.QueryRowContext(ctx, `SELECT COUNT(*) FROM seguimiento_envios WHERE conversation_id=? AND resultado='respondio'`, conv.ID).Scan(&respondio)
	if respondio != 1 {
		t.Fatalf("se anota que respondió: %d", respondio)
	}
}

// Horario, tope diario y ventana de 24 h.
func TestSeguimientoHorarioTopeYVentana(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	ctx := context.Background()
	q := &store.Secuencia{Nombre: "V", Activa: true, Pasos: []store.Paso{{EsperaMin: 30, Tipo: "texto", Texto: "¿Seguimos?"},
		{EsperaMin: 60 * 30, Tipo: "texto", Texto: "Pasó más de un día"}}}
	_ = st.CrearSecuencia(ctx, q)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{"hola": {Accion: "responder", Respuesta: "¡Hola!", Memoria: memProducto("V01", "")}}, &reqs)
	handle(b, text("hola"))
	conv, _ := st.ConversationByCustomer(ctx, 1)

	// Fuera de horario: se pospone a la apertura.
	_ = st.SetSetting(ctx, store.SettingSeguimientoHorario, "09:00-10:00")
	l := time.Now().In(store.Lima)
	noche := time.Date(l.Year(), l.Month(), l.Day(), 23, 0, 0, 0, store.Lima)
	if noche.Before(time.Now().Add(31 * time.Minute)) {
		noche = noche.AddDate(0, 0, 1)
	}
	if b.TickSeguimiento(ctx, noche) != 0 {
		t.Fatal("de noche no sale")
	}
	if e, _ := st.SeguimientoDe(ctx, conv.ID); e.ProximoEn.In(store.Lima).Hour() != 9 {
		t.Fatalf("se pospone a las 9: %v", e.ProximoEn)
	}
	// Tope 0: no sale y se pospone a mañana.
	_ = st.SetSetting(ctx, store.SettingSeguimientoHorario, "00:00-24:00")
	_ = st.SetSetting(ctx, store.SettingTopePrincipal, "0")
	_ = st.PosponerSeguimiento(ctx, conv.ID, time.Now())
	base := enviadosN(fe)
	if b.TickSeguimiento(ctx, time.Now().Add(time.Minute)) != 0 || enviadosN(fe) != base {
		t.Fatal("con el tope alcanzado no sale")
	}
	_ = st.SetSetting(ctx, store.SettingTopePrincipal, "25")
	_ = st.PosponerSeguimiento(ctx, conv.ID, time.Now())
	if b.TickSeguimiento(ctx, time.Now().Add(time.Minute)) != 1 {
		t.Fatal("con cupo sale")
	}
	// Segundo paso a las 30 h: fuera de la ventana y con «saltar» (por defecto), no sale.
	b.Drain(5 * time.Second)
	base = enviadosN(fe)
	b.TickSeguimiento(ctx, time.Now().Add(31*time.Hour))
	b.Drain(5 * time.Second)
	if enviadosN(fe) != base {
		t.Fatal("fuera de la ventana de 24 h con «saltar» no sale")
	}
	// Una persona escribe desde el panel: se detiene.
	_ = st.ArmarSeguimiento(ctx, conv.ID, q, "V01", time.Now()) // terminado: no se rearma
	if e, _ := st.SeguimientoDe(ctx, conv.ID); e.Estado != "terminado" {
		t.Fatalf("estado: %+v", e)
	}
}

// La oferta usa la promo vigente con el precio calculado; sin promo, se salta.
func TestContenidoPasoOferta(t *testing.T) {
	_, st, _ := setup(t, `{}`, false)
	ctx := context.Background()
	conoc := &store.Conocimiento{Promos: []store.PromoConocimiento{{ID: 7, Titulo: "Fiestas", VigenteHasta: "2026-10-28",
		PromoDatos: store.PromoDatos{Mecanica: "porcentaje", Valor: 20, Condiciones: "No acumulable."}}}}
	l := ContenidoPaso(ctx, st, conoc, "http://b", store.Paso{Tipo: "oferta", PromoID: 7}, "V01", "Ana López")
	if l.Omitido || !strings.Contains(l.Texto, "*S/ 95.20*") || !strings.Contains(l.Texto, "antes S/ 119.00") ||
		!strings.Contains(l.Texto, "28 de octubre") {
		t.Fatalf("oferta: %+v", l)
	}
	if l := ContenidoPaso(ctx, st, conoc, "http://b", store.Paso{Tipo: "oferta", PromoID: 99}, "V01", ""); !l.Omitido {
		t.Fatal("promo que no está vigente: se salta")
	}
	if l := ContenidoPaso(ctx, st, nil, "http://b", store.Paso{Tipo: "beneficio"}, "", ""); !l.Omitido {
		t.Fatal("sin modelo: se salta")
	}
}

// Prospecto: la memoria trae «coordinar después» → etapa «Más adelante» + tarea; si vuelve a escribir, se suelta.
func TestProspectoMasAdelante(t *testing.T) {
	b, st, _ := setup(t, `{}`, false)
	ctx := context.Background()
	_ = st.SetSetting(ctx, store.SettingProspectos, "true")
	var reqs []agente.Request
	marca := `,"coordinar":"despues","coordinar_en":"2026-12-15","coordinar_marca":"2026-10-16T10:00","fecha_iso":"2027-01-15"`
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"es para enero":           {Accion: "responder", Respuesta: "¿Lo quieres ya o lo coordinamos en diciembre?", Memoria: memProducto("V01", `,"fecha_iso":"2027-01-15"`)},
		"mejor en diciembre":      {Accion: "responder", Respuesta: "¡Perfecto! Te escribo a inicios de diciembre 💛", Memoria: memProducto("V01", marca)},
		"hola, ya me decidí jaja": {Accion: "responder", Respuesta: "¡Qué alegría!", Memoria: memProducto("V01", marca)},
	}, &reqs)
	handle(b, text("es para enero"))
	if reqs[0].Prospectos == nil || reqs[0].Prospectos.DesdeDias != 45 || reqs[0].Prospectos.AvisoDias != 30 {
		t.Fatalf("el agente recibe la configuración: %+v", reqs[0].Prospectos)
	}
	handle(b, text("mejor en diciembre"))
	c, _ := st.GetClienta(ctx, 1)
	if c.Etapa != "mas_adelante" {
		t.Fatalf("etapa: %q", c.Etapa)
	}
	ts, _ := st.ListTareas(ctx, store.FiltroTareas{CustomerID: 1})
	if len(ts) != 1 || ts[0].Vence == nil || ts[0].Vence.In(store.Lima).Format("2006-01-02 15:04") != "2026-12-15 10:00" ||
		!strings.Contains(ts[0].Titulo, "boda") || !strings.Contains(ts[0].Titulo, "Vestido Esmeralda") {
		t.Fatalf("tarea: %+v", ts)
	}
	handle(b, text("hola, ya me decidí jaja"))
	if c, _ := st.GetClienta(ctx, 1); c.Etapa == "mas_adelante" {
		t.Fatal("volvió a escribir: la etapa la decide el bot")
	}
	if ts, _ := st.ListTareas(ctx, store.FiltroTareas{CustomerID: 1}); len(ts) != 1 {
		t.Fatalf("la misma decisión no crea otra tarea: %d", len(ts))
	}
	// Sin el ajuste, el agente no recibe prospectos.
	_ = st.SetSetting(ctx, store.SettingProspectos, "false")
	if b.prospectosCfg(ctx) != nil {
		t.Fatal("apagado = nil")
	}
}

// Canal oficial: la clienta escribe por la API oficial, se le contesta por ahí (con botones de verdad), el anuncio
// queda como origen, y el aviso de pedido fuera de las 24 h sale con plantilla.
func TestCanalCloud(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	ctx := context.Background()
	sim := simulado.New("tok", "111", "999")
	t.Cleanup(sim.Close)
	b.UsarCloud(cloud.New(sim.URL, "", "111", "999", "tok", "sec", "ver"))
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"info del vestido": {Accion: "responder", Respuesta: "¡Claro! ¿Qué quieres ver?", Memoria: memProducto("V01", ""),
			RespuestasRapidas: []agente.RespuestaRapida{{Label: "Ver colores"}, {Label: "Ver tallas"}}},
	}, &reqs)
	sim.Entrante("51911222333", time.Now())
	in := DesdeCloud(cloud.Mensaje{ID: "wamid.A1", From: "51911222333", Nombre: "Lucía", Tipo: "text", Texto: "info del vestido",
		Referral: &cloud.Referral{SourceType: "ad", SourceID: "120210", Headline: "Vestido Esmeralda", CtwaClid: "ARx"}})
	base := enviadosN(fe)
	handle(b, in)
	b.Drain(5 * time.Second)
	if enviadosN(fe) != base {
		t.Fatal("nada debía salir por evolution")
	}
	env := sim.Enviados()
	if len(env) == 0 || env[0].To != "51911222333" || !strings.Contains(env[0].Texto, "¿Qué quieres ver?") {
		t.Fatalf("respuesta por la API oficial: %+v", env)
	}
	var botones *simulado.Enviado
	for i := range env {
		if env[i].Tipo == "interactive" {
			botones = &env[i]
		}
	}
	if botones == nil || len(botones.Opciones) != 2 || botones.Opciones[0] != "Ver colores" {
		t.Fatalf("botones de verdad: %+v", env)
	}
	if len(sim.Errores()) != 0 {
		t.Fatalf("el simulado rechazó: %v", sim.Errores())
	}
	conv, _ := st.ConversationByCustomer(ctx, 1)
	if o, _ := st.OrigenDe(ctx, conv.ID); o == nil || o.AdID != "120210" || o.Canal != "cloud" || o.CtwaClid != "ARx" {
		t.Fatalf("origen: %+v", o)
	}
	if !reqs[0].DesdeAnuncio || reqs[0].Anuncio != "Vestido Esmeralda" {
		t.Fatalf("el agente sabe que llegó por el anuncio: %+v", reqs[0])
	}

	// Aviso de pedido con la ventana cerrada: plantilla.
	o := &store.Order{CustomerID: conv.CustomerID, Status: "enviado", ConversationID: conv.ID}
	_ = st.SetSetting(ctx, "plantilla_estado_pedido", "pedido_estado")
	sim.Plantilla("pedido_estado", "APPROVED")
	b.store.DB.ExecContext(ctx, `UPDATE messages SET created_at=? WHERE conversation_id=? AND direction='in'`,
		time.Now().Add(-30*time.Hour), conv.ID)
	n0 := len(sim.Enviados())
	b.NotifyStatus(ctx, o)
	env = sim.Enviados()
	if len(env) != n0+1 || env[n0].Tipo != "template" || env[n0].Plantilla != "pedido_estado" || env[n0].Params[1] != "Enviado" {
		t.Fatalf("plantilla de estado: %+v", env[n0:])
	}
}
