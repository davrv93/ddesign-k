package bot

import (
	"encoding/json"
	"testing"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
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
