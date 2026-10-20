package api

import (
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// Editor visual de flujos por HTTP: guardar un flujo, simularlo sin enviar (también la otra rama) y los rechazos de
// «Probar» (asesora, sin flujo, teléfono malo, otra empresa, sin WhatsApp en esta empresa).
func TestFlujosPorHTTP(t *testing.T) {
	h, st, _ := servidorMulti(t)
	sfx := strings.ReplaceAll(time.Now().Format("150405.000000"), ".", "")
	a := prepararEmpresa(t, h, st, "fla"+sfx, "Alfa")
	b := prepararEmpresa(t, h, st, "flb"+sfx, "Beta")
	A, B := "/"+a.slug+"/api", "/"+b.slug+"/api"
	code := func(w *httptest.ResponseRecorder, want int, que string) *httptest.ResponseRecorder {
		t.Helper()
		if w.Code != want {
			t.Fatalf("%s: %d (esperaba %d) %s", que, w.Code, want, w.Body)
		}
		return w
	}
	grafo := `{"nodos":[{"id":"i","tipo":"inicio","x":0,"y":0},{"id":"w","tipo":"esperar","minutos":60,"x":200,"y":0},
		{"id":"m","tipo":"mensaje","mensaje":"texto","texto":"Hola {nombre}, ¿te gustó el {modelo}?","x":400,"y":0},
		{"id":"c","tipo":"condicion","condicion":"respondio","x":600,"y":0},
		{"id":"t","tipo":"tarea","titulo":"Llamar a {nombre}","x":800,"y":-100},
		{"id":"m2","tipo":"mensaje","mensaje":"emoji","texto":"🥰","x":800,"y":100},{"id":"f","tipo":"fin","x":1000,"y":0}],
		"aristas":[{"desde":"i","salida":"siguiente","hasta":"w"},{"desde":"w","salida":"siguiente","hasta":"m"},
		{"desde":"m","salida":"siguiente","hasta":"c"},{"desde":"c","salida":"si","hasta":"t"},{"desde":"t","salida":"siguiente","hasta":"f"},
		{"desde":"c","salida":"no","hasta":"m2"}],"vista":{"x":10,"y":20,"zoom":0.8}}`

	// Guardar: el flujo vuelve igual y los pasos se derivan de los mensajes.
	w := code(llamar(h, "POST", A+"/secuencias", a.admin, `{"nombre":"Flujo","alcance":"todas","pasos":[],"grafo":`+grafo+`}`), 201, "crear flujo")
	var sec struct {
		ID    int64 `json:"id"`
		Pasos []struct {
			Tipo      string `json:"tipo"`
			EsperaMin int    `json:"espera_min"`
		} `json:"pasos"`
		Grafo struct {
			Nodos []struct{ ID string }  `json:"nodos"`
			Vista struct{ Zoom float64 } `json:"vista"`
		} `json:"grafo"`
	}
	_ = json.Unmarshal(w.Body.Bytes(), &sec)
	if len(sec.Pasos) != 2 || sec.Pasos[0].EsperaMin != 60 || len(sec.Grafo.Nodos) != 7 || sec.Grafo.Vista.Zoom != 0.8 {
		t.Fatalf("flujo guardado: %s", w.Body)
	}
	id := itoa(sec.ID)
	code(llamar(h, "POST", A+"/secuencias", a.admin,
		`{"nombre":"Roto","pasos":[],"grafo":{"nodos":[{"id":"i","tipo":"inicio"}],"aristas":[]}}`), 400, "flujo sin mensajes")

	// Simular sin enviar: rama «no» y rama «sí».
	sim := func(responde bool) string {
		body := `{"grafo":` + grafo + `,"producto":"V21","responde":` + map[bool]string{true: "true", false: "false"}[responde] + `}`
		return code(llamar(h, "POST", A+"/flujos/simular", a.asesora, body), 200, "simular").Body.String()
	}
	no := sim(false)
	if !strings.Contains(no, `"texto":"Hola Ana, ¿te gustó el`) || !strings.Contains(no, `"texto":"🥰"`) || strings.Contains(no, "Llamar a") {
		t.Fatalf("rama «no»: %s", no)
	}
	if si := sim(true); !strings.Contains(si, "crea la tarea «Llamar a {nombre}»") || strings.Contains(si, "🥰") {
		t.Fatalf("rama «sí»: %s", si)
	}

	// Probar: solo admin, con flujo guardado, teléfono válido, en su empresa y con WhatsApp.
	code(llamar(h, "POST", A+"/secuencias/"+id+"/probar", a.asesora, `{"telefono":"51911222333"}`), 403, "asesora prueba")
	code(llamar(h, "POST", B+"/secuencias/"+id+"/probar", b.admin, `{"telefono":"51911222333"}`), 409, "empresa sin WhatsApp")
	code(llamar(h, "POST", A+"/secuencias/"+id+"/probar", a.admin, `{"telefono":"12"}`), 400, "teléfono malo")
	code(llamar(h, "POST", A+"/secuencias/"+id+"/probar", a.admin, `{"telefono":"51911222333","escala_min":99}`), 400, "escala fuera de rango")
}
