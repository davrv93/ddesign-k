package api

import (
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/ai"
	"github.com/davrv93/ddesign-k/backend/internal/auth"
	"github.com/davrv93/ddesign-k/backend/internal/bot"
	"github.com/davrv93/ddesign-k/backend/internal/config"
	"github.com/davrv93/ddesign-k/backend/internal/store"
	"github.com/davrv93/ddesign-k/backend/internal/whatsapp/cloud"
	"github.com/davrv93/ddesign-k/backend/internal/whatsapp/cloud/simulado"
)

// Secuencias, contenido, campañas y números por HTTP: permisos (admin / asesora) y aislamiento entre empresas.
func TestSeguimientoPorHTTP(t *testing.T) {
	h, st, _ := servidorMulti(t)
	sfx := strings.ReplaceAll(time.Now().Format("150405.000000"), ".", "")
	a := prepararEmpresa(t, h, st, "sga"+sfx, "Alfa")
	b := prepararEmpresa(t, h, st, "sgb"+sfx, "Beta")
	A, B := "/"+a.slug+"/api", "/"+b.slug+"/api"
	code := func(w *httptest.ResponseRecorder, want int, que string) *httptest.ResponseRecorder {
		t.Helper()
		if w.Code != want {
			t.Fatalf("%s: %d (esperaba %d) %s", que, w.Code, want, w.Body)
		}
		return w
	}

	// Ajustes nuevos con su valor por defecto.
	var aj map[string]string
	_ = json.Unmarshal(code(llamar(h, "GET", A+"/settings", a.admin, ""), 200, "ajustes").Body.Bytes(), &aj)
	for k, v := range map[string]string{"bienvenida_anuncio": "false", "prospectos_habilitado": "false", "prospecto_desde_dias": "45",
		"prospecto_aviso_dias": "30", "seguimiento_horario": "08:00-21:00", "precio_al_llegar_por_anuncio": "false"} {
		if aj[k] != v {
			t.Fatalf("ajuste %s = %q", k, aj[k])
		}
	}

	// Contenido: importar (admin) y leer; lo de otra empresa no se ve.
	code(llamar(h, "POST", A+"/contenido/importar", a.asesora, `[]`), 403, "asesora importa")
	w := code(llamar(h, "POST", A+"/contenido/importar", a.admin,
		`[{"codigo":"V21","clave":"beneficio","texto":"No se arruga"},{"codigo":"NOEXISTE","clave":"tip","texto":"x"}]`), 200, "importar")
	if !strings.Contains(w.Body.String(), `"importados":1`) || !strings.Contains(w.Body.String(), `"omitidos":1`) {
		t.Fatalf("importar: %s", w.Body)
	}
	var res []store.ResumenContenido
	_ = json.Unmarshal(code(llamar(h, "GET", A+"/contenido/resumen", a.asesora, ""), 200, "resumen").Body.Bytes(), &res)
	if len(res) != 1 || !res[0].Beneficio {
		t.Fatalf("resumen: %+v", res)
	}
	pid := itoa(res[0].ProductID)
	code(llamar(h, "PUT", A+"/productos/"+pid+"/contenido/tip", a.admin, `{"texto":"Con sandalias doradas"}`), 200, "tip manual")
	code(llamar(h, "GET", B+"/productos/"+pid+"/contenido", b.admin, ""), 404, "otra empresa lee contenido")
	code(llamar(h, "PUT", A+"/productos/"+pid+"/contenido/otra", a.admin, `{"texto":"x"}`), 400, "clave inválida")

	// Secuencias.
	cuerpo := `{"nombre":"Vestidos","activa":false,"pasos":[{"espera_min":60,"tipo":"emoji","texto":"🥰"},{"espera_min":300,"tipo":"beneficio"},{"espera_min":300,"tipo":"tip","texto":"Un tip para el {modelo}:"}]}`
	code(llamar(h, "POST", A+"/secuencias", a.asesora, cuerpo), 403, "asesora crea secuencia")
	code(llamar(h, "POST", A+"/secuencias", a.admin, `{"nombre":"x","pasos":[]}`), 400, "sin pasos")
	var q store.Secuencia
	_ = json.Unmarshal(code(llamar(h, "POST", A+"/secuencias", a.admin, cuerpo), 201, "crear secuencia").Body.Bytes(), &q)
	id := itoa(q.ID)
	code(llamar(h, "GET", B+"/secuencias/"+id+"/vista-previa", b.admin, ""), 404, "vista previa ajena")
	var previa []pasoPrevia
	_ = json.Unmarshal(code(llamar(h, "GET", A+"/secuencias/"+id+"/vista-previa?producto=V21", a.asesora, ""), 200, "vista previa").Body.Bytes(), &previa)
	if len(previa) != 3 || previa[0].Texto != "🥰" || previa[1].Texto != "No se arruga" || previa[2].Omitido ||
		!strings.Contains(previa[2].Texto, "Un tip para el Vestido Alfa:") {
		t.Fatalf("vista previa: %+v", previa)
	}
	code(llamar(h, "POST", A+"/secuencias/"+id+"/activa", a.admin, `{"activa":true}`), 200, "activar")
	var qs []store.Secuencia
	_ = json.Unmarshal(code(llamar(h, "GET", A+"/secuencias", a.asesora, ""), 200, "listar").Body.Bytes(), &qs)
	if len(qs) != 1 || !qs[0].Activa {
		t.Fatalf("lista: %+v", qs)
	}
	if w := code(llamar(h, "GET", B+"/secuencias", b.admin, ""), 200, "listar ajena"); strings.TrimSpace(w.Body.String()) != "[]" {
		t.Fatalf("la otra empresa no ve secuencias: %s", w.Body)
	}
	code(llamar(h, "PUT", B+"/secuencias/"+id, b.admin, cuerpo), 404, "editar ajena")
	code(llamar(h, "DELETE", B+"/secuencias/"+id, b.admin, ""), 404, "borrar ajena")

	// Conversación, campañas y números.
	conv, _ := st.ForTenant(mustTenant(t, st, a.slug)).ConversationByCustomer(t.Context(), a.clienta)
	if w := code(llamar(h, "GET", A+"/conversations/"+itoa(conv.ID)+"/seguimiento", a.asesora, ""), 200, "seguimiento conv"); strings.TrimSpace(w.Body.String()) != "null" {
		t.Fatalf("sin seguimiento: %s", w.Body)
	}
	code(llamar(h, "POST", B+"/conversations/"+itoa(conv.ID)+"/seguimiento/detener", b.admin, ""), 404, "detener ajena")
	code(llamar(h, "POST", A+"/conversations/"+itoa(conv.ID)+"/seguimiento/detener", a.asesora, ""), 200, "detener")
	code(llamar(h, "GET", A+"/campanas?desde=2026-01-01&hasta=2026-12-31", a.asesora, ""), 200, "campañas")
	code(llamar(h, "GET", A+"/campanas?desde=2026-12-31&hasta=2026-01-01", a.asesora, ""), 400, "fechas al revés")
	code(llamar(h, "POST", A+"/numeros", a.admin, `{"nombre":"Avisos","numero":"51999888777"}`), 409, "sin WhatsApp no hay números")
	code(llamar(h, "POST", A+"/numeros", a.asesora, `{"nombre":"Avisos"}`), 403, "asesora crea número")
	if w := code(llamar(h, "GET", A+"/whatsapp-cloud/plantillas", a.admin, ""), 200, "plantillas"); strings.TrimSpace(w.Body.String()) != "[]" {
		t.Fatalf("sin API oficial: %s", w.Body)
	}
	code(llamar(h, "DELETE", A+"/secuencias/"+id, a.admin, ""), 200, "borrar")
}

// Webhook de la API oficial: verificación (GET), firma obligatoria (POST) y el mensaje llega al bot y se contesta por
// la API oficial.
func TestWebhookCloud(t *testing.T) {
	st, err := store.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.DB.Close() })
	sim := simulado.New("tok", "111", "999")
	t.Cleanup(sim.Close)
	cfg := &config.Config{DataDir: t.TempDir(), BusinessName: "Baruka", Currency: "S/", WebhookSecret: "s3",
		WACloudToken: "tok", WACloudPhoneNumberID: "111", WACloudAppSecret: "secreto-app", WACloudVerifyToken: "verif"}
	aic := ai.New("", "m", "", time.Second)
	b := bot.New(cfg, st, nil, aic)
	b.UsarCloud(cloud.New(sim.URL, "", "111", "999", "tok", "secreto-app", "verif"))
	h := New(cfg, st, nil, aic, b, auth.New("jwt", "admin", "clave-admin-larga"), NewHub()).Routes()

	if w := llamar(h, "GET", "/webhook/whatsapp-cloud?hub.mode=subscribe&hub.verify_token=mal&hub.challenge=123", "", ""); w.Code != 403 {
		t.Fatalf("token de verificación malo: %d", w.Code)
	}
	if w := llamar(h, "GET", "/webhook/whatsapp-cloud?hub.mode=subscribe&hub.verify_token=verif&hub.challenge=123", "", ""); w.Code != 200 || w.Body.String() != "123" {
		t.Fatalf("verificación: %d %s", w.Code, w.Body)
	}
	body := `{"entry":[{"changes":[{"field":"messages","value":{"metadata":{"phone_number_id":"111"},"contacts":[{"wa_id":"51911000111","profile":{"name":"Ana"}}],"messages":[{"id":"wamid.X1","from":"51911000111","timestamp":"1792080000","type":"text","text":{"body":"hola"}}]}}]}]}`
	if w := llamar(h, "POST", "/webhook/whatsapp-cloud", "", body); w.Code != 403 {
		t.Fatalf("sin firma: %d", w.Code)
	}
	sim.Entrante("51911000111", time.Now())
	r := httptest.NewRequest("POST", "/webhook/whatsapp-cloud", strings.NewReader(body))
	r.Header.Set("X-Hub-Signature-256", cloud.Firmar("secreto-app", []byte(body)))
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 200 {
		t.Fatalf("con firma: %d %s", w.Code, w.Body)
	}
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) && len(sim.Enviados()) == 0 {
		time.Sleep(50 * time.Millisecond)
	}
	b.Drain(5 * time.Second)
	env := sim.Enviados()
	if len(env) == 0 || env[0].To != "51911000111" {
		t.Fatalf("la respuesta sale por la API oficial: %+v (errores %v)", env, sim.Errores())
	}
	if c, err := st.ConversationByCustomer(t.Context(), 1); err != nil || c == nil {
		t.Fatalf("conversación creada: %v", err)
	}
}
