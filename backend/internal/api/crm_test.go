package api

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/ai"
	"github.com/davrv93/ddesign-k/backend/internal/auth"
	"github.com/davrv93/ddesign-k/backend/internal/bot"
	"github.com/davrv93/ddesign-k/backend/internal/config"
	"github.com/davrv93/ddesign-k/backend/internal/evolution"
	"github.com/davrv93/ddesign-k/backend/internal/kommo"
	"github.com/davrv93/ddesign-k/backend/internal/kommo/simulado"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

func servidor(t *testing.T, secreto string) (*Server, *store.Store) {
	t.Helper()
	st, err := store.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.DB.Close() })
	cfg := &config.Config{DataDir: t.TempDir(), BusinessName: "Baruka Design", Currency: "S/", CRMEventSecret: secreto}
	evo := evolution.New("http://127.0.0.1:1", "g", "kd", "t")
	aic := ai.New("", "m", "", time.Second)
	b := bot.New(cfg, st, evo, aic)
	return New(cfg, st, evo, aic, b, auth.New("jwt", "admin", "clave"), NewHub()), st
}

const cuerpoWeb = `{"canal":"web","conversacion":"sesion-1234","etapa":"seguimiento","mensaje":"de qué tela es?","respuesta":"De crepe",
"intencion":"consulta_material","memoria":{"producto":"V35","temperatura":"tibio","sabemos":{"ocasion":"boda"}}}`

func postear(h http.Handler, secreto string, cabeceras map[string]string, cuerpo string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(http.MethodPost, "/api/internal/crm/evento", strings.NewReader(cuerpo))
	r.Header.Set("Content-Type", "application/json")
	if secreto != "" {
		r.Header.Set("X-CRM-Secret", secreto)
	}
	for k, v := range cabeceras {
		r.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

// La ruta interna solo existe con secreto, solo atiende a la red interna (sin cabeceras de proxy) y valida el cuerpo.
func TestCRMEventoProtegido(t *testing.T) {
	s, _ := servidor(t, "")
	if w := postear(s.Routes(), "x", nil, cuerpoWeb); w.Code != 404 {
		t.Fatalf("sin CRM_EVENT_SECRET la ruta no existe: %d", w.Code)
	}
	s, _ = servidor(t, "secreto-largo")
	h := s.Routes()
	if w := postear(h, "secreto-largo", map[string]string{"X-Forwarded-For": "1.2.3.4"}, cuerpoWeb); w.Code != 404 {
		t.Fatalf("desde el proxy (internet) no se atiende: %d", w.Code)
	}
	if w := postear(h, "otro", nil, cuerpoWeb); w.Code != 403 {
		t.Fatalf("secreto equivocado: %d", w.Code)
	}
	if w := postear(h, "secreto-largo", nil, `{"canal":"web","conversacion":"../../x"}`); w.Code != 400 {
		t.Fatalf("conversación inválida: %d", w.Code)
	}
	if w := postear(h, "secreto-largo", nil, cuerpoWeb); w.Code != 202 || !strings.Contains(w.Body.String(), `"kommo":false`) {
		t.Fatalf("sin Kommo se acepta y no hace nada: %d %s", w.Code, w.Body)
	}
}

// Con Kommo, el turno del chat web llega al mismo sincronizador que WhatsApp y el panel enlaza el lead.
func TestCRMEventoLlegaAKommo(t *testing.T) {
	s, st := servidor(t, "secreto-largo")
	k := simulado.Nuevo("tok")
	defer k.Close()
	c := kommo.Nuevo(k.URL, "tok")
	c.EsperaMin, c.EsperaMax = 5*time.Millisecond, 50*time.Millisecond
	s.Kommo = kommo.NuevoSincronizador(c, st, kommo.Opciones{Producto: kommo.ProductoDe(st)})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.Kommo.Iniciar(ctx)
	if w := postear(s.Routes(), "secreto-largo", nil, cuerpoWeb); w.Code != 202 {
		t.Fatalf("%d %s", w.Code, w.Body)
	}
	if !s.Kommo.Esperar(time.Minute) {
		t.Fatal("no se aplicó")
	}
	ls := k.LeadsDe(0)
	if len(ls) != 1 || !strings.Contains(strings.Join(k.NotasDe(ls[0].ID), "\n"), "Preguntó por la tela del V35") || len(k.Errores) > 0 {
		t.Fatalf("lead del chat web: %+v notas %v errores %v", ls, k.NotasDe(ls[0].ID), k.Errores)
	}
}
