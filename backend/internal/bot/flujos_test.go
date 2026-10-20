package bot

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// flujoRamas: emoji → espera → ¿respondió? (sí: tarea; no: beneficio → espera → pregunta → «perdida»).
func flujoRamas() *store.Grafo {
	return &store.Grafo{
		Nodos: []store.Nodo{
			{ID: "ini", Tipo: "inicio"},
			{ID: "e1", Tipo: "esperar", Minutos: 60},
			{ID: "m1", Tipo: "mensaje", Mensaje: "emoji", Texto: "🥰"},
			{ID: "e2", Tipo: "esperar", Minutos: 300},
			{ID: "c1", Tipo: "condicion", Condicion: "respondio"},
			{ID: "t1", Tipo: "tarea", Titulo: "Atender a {nombre}: respondió ({modelo})"},
			{ID: "m2", Tipo: "mensaje", Mensaje: "beneficio", Texto: "Mira lo que tiene el {modelo}: {contenido}"},
			{ID: "e3", Tipo: "esperar", Minutos: 60},
			{ID: "m3", Tipo: "mensaje", Mensaje: "texto", Texto: "¿Te ayudo con la talla, {nombre}?"},
			{ID: "et", Tipo: "etapa", Etapa: "perdida"},
			{ID: "fin", Tipo: "fin"},
		},
		Aristas: []store.Arista{
			{Desde: "ini", Salida: "siguiente", Hasta: "e1"}, {Desde: "e1", Salida: "siguiente", Hasta: "m1"},
			{Desde: "m1", Salida: "siguiente", Hasta: "e2"}, {Desde: "e2", Salida: "siguiente", Hasta: "c1"},
			{Desde: "c1", Salida: "si", Hasta: "t1"}, {Desde: "t1", Salida: "siguiente", Hasta: "fin"},
			{Desde: "c1", Salida: "no", Hasta: "m2"}, {Desde: "m2", Salida: "siguiente", Hasta: "e3"},
			{Desde: "e3", Salida: "siguiente", Hasta: "m3"}, {Desde: "m3", Salida: "siguiente", Hasta: "et"},
			{Desde: "et", Salida: "siguiente", Hasta: "fin"},
		},
	}
}

func prepararFlujo(t *testing.T) (*Bot, *store.Store, *fakeEvolution, *store.Secuencia) {
	t.Helper()
	b, st, fe := setup(t, `{}`, false)
	ctx := context.Background()
	_ = st.SetSetting(ctx, store.SettingSeguimientoHorario, "00:00-24:00")
	p, _ := st.GetProductByCode(ctx, "V01")
	if _, err := st.GuardarContenido(ctx, p.ID, "beneficio", "su tela no se arruga.", "", "web"); err != nil {
		t.Fatal(err)
	}
	q := &store.Secuencia{Nombre: "Flujo de ramas", Activa: true, Grafo: flujoRamas()}
	if err := st.CrearSecuencia(ctx, q); err != nil {
		t.Fatal(err)
	}
	if len(q.Pasos) != 3 {
		t.Fatalf("los pasos se derivan de los mensajes: %+v", q.Pasos)
	}
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"me gusta el esmeralda": {Accion: "responder", Respuesta: "¡Es precioso! ¿Para qué ocasión?", Memoria: memProducto("V01", "")},
		"ah ok":                 {Accion: "responder", Respuesta: "Cuéntame si tienes dudas 😊", Memoria: memProducto("V01", "")},
	}, &reqs)
	return b, st, fe, q
}

// Sin respuesta: el flujo toma la rama «no», manda beneficio y pregunta a su hora y termina pasando a «perdida».
func TestFlujoRamaSinRespuesta(t *testing.T) {
	b, st, fe, _ := prepararFlujo(t)
	ctx := context.Background()
	handle(b, text("me gusta el esmeralda"))
	conv, _ := st.ConversationByCustomer(ctx, 1)
	e, _ := st.SeguimientoDe(ctx, conv.ID)
	if e == nil || e.Estado != "activo" || e.Nodo != "ini" {
		t.Fatalf("el flujo se arma en el inicio: %+v", e)
	}
	ahora := time.Now().Add(2 * time.Minute)
	base := enviadosN(fe)
	if n := b.TickSeguimiento(ctx, ahora); n != 0 {
		t.Fatal("el inicio lleva a la espera: no sale nada todavía")
	}
	if e, _ = st.SeguimientoDe(ctx, conv.ID); e.Nodo != "e1" {
		t.Fatalf("queda esperando en e1: %+v", e)
	}
	ahora = ahora.Add(61 * time.Minute)
	if n := b.TickSeguimiento(ctx, ahora); n != 1 {
		t.Fatal("cumplida la espera sale el emoji")
	}
	b.Drain(5 * time.Second)
	if got := textosA(fe, base); len(got) != 1 || got[0] != "🥰" {
		t.Fatalf("emoji: %v", got)
	}
	base = enviadosN(fe)
	ahora = ahora.Add(301 * time.Minute)
	b.TickSeguimiento(ctx, ahora) // ¿respondió? no → beneficio
	b.Drain(5 * time.Second)
	if got := textosA(fe, base); len(got) != 1 || !strings.Contains(got[0], "Vestido Esmeralda: su tela no se arruga.") {
		t.Fatalf("beneficio con {modelo} y {contenido}: %v", got)
	}
	base = enviadosN(fe)
	ahora = ahora.Add(61 * time.Minute)
	b.TickSeguimiento(ctx, ahora)
	b.Drain(5 * time.Second)
	if got := textosA(fe, base); len(got) != 1 || got[0] != "¿Te ayudo con la talla, Ana?" {
		t.Fatalf("pregunta final: %v", got)
	}
	if e, _ = st.SeguimientoDe(ctx, conv.ID); e.Estado != "terminado" || e.Paso != 3 {
		t.Fatalf("termina con 3 mensajes: %+v", e)
	}
	cl, _ := st.GetClienta(ctx, conv.CustomerID)
	if cl.Etapa != "perdida" {
		t.Fatalf("el nodo de etapa la pasa a perdida: %q", cl.Etapa)
	}
}

// Responde después del emoji: el flujo se pausa; cuando vuelve a callarse sigue en la espera (que vuelve a contar) y la
// condición «¿respondió?» toma la rama «sí» y crea la tarea.
func TestFlujoRespondeYTomaLaRamaSi(t *testing.T) {
	b, st, fe, _ := prepararFlujo(t)
	ctx := context.Background()
	handle(b, text("me gusta el esmeralda"))
	conv, _ := st.ConversationByCustomer(ctx, 1)
	ahora := time.Now().Add(2 * time.Minute)
	b.TickSeguimiento(ctx, ahora)
	ahora = ahora.Add(61 * time.Minute)
	b.TickSeguimiento(ctx, ahora) // emoji
	b.Drain(5 * time.Second)
	time.Sleep(1100 * time.Millisecond) // su mensaje queda después del envío
	handle(b, text("ah ok"))
	e, _ := st.SeguimientoDe(ctx, conv.ID)
	if e.Estado != "activo" || e.Nodo != "e2" || e.ProximoEn == nil || e.ProximoEn.Before(time.Now().Add(299*time.Minute)) {
		t.Fatalf("al callarse de nuevo sigue en la espera, contando desde ahora: %+v", e)
	}
	base := enviadosN(fe)
	b.TickSeguimiento(ctx, time.Now().Add(301*time.Minute))
	b.Drain(5 * time.Second)
	if got := textosA(fe, base); len(got) != 0 {
		t.Fatalf("la rama «sí» no manda mensajes: %v", got)
	}
	tareas, _ := st.ListTareas(ctx, store.FiltroTareas{CustomerID: conv.CustomerID})
	if len(tareas) != 1 || tareas[0].Titulo != "Atender a Ana: respondió (Vestido Esmeralda)" {
		t.Fatalf("tarea de la rama «sí»: %+v", tareas)
	}
	if e, _ = st.SeguimientoDe(ctx, conv.ID); e.Estado != "terminado" {
		t.Fatalf("termina: %+v", e)
	}
}

// «Probar» del editor: corre aunque la secuencia esté apagada y fuera de horario, con esperas de 1 minuto, pasa a otro
// flujo con «ir a» y anota los envíos como prueba (no cuentan para el tope).
func TestFlujoPruebaConIrA(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	ctx := context.Background()
	_ = st.SetSetting(ctx, store.SettingSeguimientoHorario, "08:00-08:01")
	destino := &store.Secuencia{Nombre: "Destino", Grafo: &store.Grafo{
		Nodos:   []store.Nodo{{ID: "i", Tipo: "inicio"}, {ID: "b1", Tipo: "mensaje", Mensaje: "texto", Texto: "Mensaje del flujo B"}, {ID: "f", Tipo: "fin"}},
		Aristas: []store.Arista{{Desde: "i", Salida: "siguiente", Hasta: "b1"}, {Desde: "b1", Salida: "siguiente", Hasta: "f"}},
	}}
	if err := st.CrearSecuencia(ctx, destino); err != nil {
		t.Fatal(err)
	}
	origen := &store.Secuencia{Nombre: "Origen", Grafo: &store.Grafo{
		Nodos: []store.Nodo{{ID: "i", Tipo: "inicio"}, {ID: "a1", Tipo: "mensaje", Mensaje: "texto", Texto: "Hola {nombre}, soy el flujo A"},
			{ID: "w", Tipo: "esperar", Minutos: 300}, {ID: "ir", Tipo: "ir_a", Destino: destino.ID}},
		Aristas: []store.Arista{{Desde: "i", Salida: "siguiente", Hasta: "a1"}, {Desde: "a1", Salida: "siguiente", Hasta: "w"},
			{Desde: "w", Salida: "siguiente", Hasta: "ir"}},
	}}
	if err := st.CrearSecuencia(ctx, origen); err != nil {
		t.Fatal(err)
	}
	_, conv, err := st.UpsertCustomer(ctx, "51911222333@s.whatsapp.net", "51911222333", "")
	if err != nil {
		t.Fatal(err)
	}
	if err := st.ArmarPrueba(ctx, conv.ID, origen, "V01", 1); err != nil {
		t.Fatal(err)
	}
	madrugada := time.Now().In(store.Lima).AddDate(0, 0, 1)
	madrugada = time.Date(madrugada.Year(), madrugada.Month(), madrugada.Day(), 3, 0, 0, 0, store.Lima)
	base := enviadosN(fe)
	if n := b.TickSeguimiento(ctx, madrugada); n != 1 {
		t.Fatal("la prueba sale ya, fuera de horario y con la secuencia apagada")
	}
	e, _ := st.SeguimientoDe(ctx, conv.ID)
	if e.Nodo != "w" || e.ProximoEn == nil || e.ProximoEn.Sub(madrugada) != time.Minute {
		t.Fatalf("la espera de 300 min dura 1 min en la prueba: %+v", e)
	}
	b.TickSeguimiento(ctx, madrugada.Add(2*time.Minute))
	b.Drain(5 * time.Second)
	got := textosA(fe, base)
	if len(got) != 2 || got[0] != "Hola, soy el flujo A" || got[1] != "Mensaje del flujo B" {
		t.Fatalf("A y luego B por «ir a»: %v", got)
	}
	if e, _ = st.SeguimientoDe(ctx, conv.ID); e.Estado != "terminado" || e.SecuenciaID != destino.ID {
		t.Fatalf("termina en el flujo B: %+v", e)
	}
	if n := st.EnviadosHoy(ctx, 0); n != 0 {
		t.Fatalf("las pruebas no cuentan para el tope: %d", n)
	}
}
