package bot

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
)

// La V2 del agente guarda la pila de temas pendientes («suspender, no cancelar») en la misma ficha que ya viaja:
// `memoria.v2.temas`. Go la trata como opaca; esta prueba comprueba que SOBREVIVE a lo que Go hace con la ficha
// (guardarla, devolverla en la petición siguiente y editar `pendiente`/`sabemos` en sus flujos) sin perder ni deformar nada.
const memConPila = `{"etapa":"seguimiento","pendiente":"talla","sabemos":{"ocasion":"matrimonio","talla":null},"preguntado":["talla"],` +
	`"v2":{"temas":{"v":1,"turno":1000000,"pendientes":[{"topic":"size","slot":"talla","question":"¿Qué talla usas normalmente?",` +
	`"status":"suspended","priority":85,"attempts":2,"desde":4,"ask":6}],"actual":null,"ultima_retoma":{"slot":"talla","turno":6},` +
	`"ofrecidas":[{"label":"Soy talla M","payload":{"intent":"provide_size","size":"M"}}]}}}`

func temasDe(t *testing.T, raw json.RawMessage) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("memoria ilegible: %s", raw)
	}
	v2, _ := m["v2"].(map[string]any)
	tm, _ := v2["temas"].(map[string]any)
	return tm
}

func TestPilaDeTemasSobreviveAlRoundTripDeGo(t *testing.T) {
	// Las dos ediciones que hace Go sobre la ficha como mapa genérico: la pregunta pendiente y un dato de la clienta.
	for nombre, raw := range map[string]json.RawMessage{
		"memConPendiente": memConPendiente(json.RawMessage(memConPila), "confirmar"),
		"memSabemos":      memSabemos(json.RawMessage(memConPila), "talla", "M"),
		"memEditar":       memEditar(json.RawMessage(memConPila), func(m map[string]any) { m["producto"] = "V31" }),
	} {
		tm := temasDe(t, raw)
		if tm == nil {
			t.Fatalf("%s: la pila de V2 se perdió: %s", nombre, raw)
		}
		pend, _ := tm["pendientes"].([]any)
		if len(pend) != 1 || pend[0].(map[string]any)["slot"] != "talla" || pend[0].(map[string]any)["status"] != "suspended" {
			t.Fatalf("%s: los pendientes cambiaron: %s", nombre, raw)
		}
		if !strings.Contains(string(raw), `"turno":1000000`) {
			t.Fatalf("%s: un entero se deformó al pasar por float64 (¿1e+06?): %s", nombre, raw)
		}
		ofr, _ := tm["ofrecidas"].([]any)
		if len(ofr) != 1 || ofr[0].(map[string]any)["payload"].(map[string]any)["size"] != "M" {
			t.Fatalf("%s: las respuestas rápidas ofrecidas cambiaron: %s", nombre, raw)
		}
	}
}

func TestPilaDeTemasViajaConLaConversacion(t *testing.T) {
	b, st, _ := setup(t, `{}`, false)
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"hacen delivery a surco": {Accion: "responder", Respuesta: "El envío a Lima es S/ 15.\n\nPara seguir, ¿qué talla usas?", Etapa: "seguimiento",
			Memoria: json.RawMessage(memConPila)},
		"y aceptan yape": {Accion: "responder", Respuesta: "Los datos te los paso al confirmar.", Etapa: "seguimiento"},
	}, &reqs)
	handle(b, text("hacen delivery a surco"))
	_, cc := estadoDe(t, st)
	if temasDe(t, cc.Memoria) == nil {
		t.Fatalf("la pila de V2 debía guardarse con la conversación: %s", cc.Memoria)
	}
	handle(b, text("y aceptan yape"))
	if len(reqs) < 2 {
		t.Fatalf("faltó la segunda petición")
	}
	if tm := temasDe(t, reqs[1].Memoria); tm == nil || tm["turno"] != float64(1000000) {
		t.Fatalf("la pila debía volver al agente tal cual en la petición siguiente: %s", reqs[1].Memoria)
	}
	// Una respuesta sin memoria (agente viejo o V1) no borra la que había.
	if _, cc := estadoDe(t, st); temasDe(t, cc.Memoria) == nil {
		t.Fatalf("una respuesta sin memoria no debía borrar la pila: %s", cc.Memoria)
	}
}
