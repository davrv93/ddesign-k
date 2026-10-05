package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
)

// peticion hace una llamada autenticada al API y devuelve la respuesta.
func peticion(t *testing.T, s *Server, metodo, ruta, cuerpo string) *httptest.ResponseRecorder {
	t.Helper()
	tok, err := s.auth.Login("admin", "clave")
	if err != nil {
		t.Fatal(err)
	}
	r := httptest.NewRequest(metodo, ruta, strings.NewReader(cuerpo))
	r.Header.Set("Authorization", "Bearer "+tok)
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	s.Routes().ServeHTTP(w, r)
	return w
}

func ajustes(t *testing.T, w *httptest.ResponseRecorder) map[string]string {
	t.Helper()
	var m map[string]string
	if err := json.Unmarshal(w.Body.Bytes(), &m); err != nil {
		t.Fatalf("ajustes: %v: %s", err, w.Body.String())
	}
	return m
}

func TestAjustesDeVersionTienenValoresSegurosPorDefecto(t *testing.T) {
	s, _ := servidor(t, "")
	w := peticion(t, s, "GET", "/api/settings", "")
	m := ajustes(t, w)
	if w.Code != 200 || m["agent_version"] != "v1" || m["agent_v2_percent"] != "0" || m["agent_v2_modo"] != "sombra" || m["agent_v2_phones"] != "" {
		t.Fatalf("por defecto V1, 0 %%, sombra y sin números: %d %v", w.Code, m)
	}
}

func TestGuardarAjustesDeVersion(t *testing.T) {
	s, _ := servidor(t, "")
	w := peticion(t, s, "PUT", "/api/settings", `{"agent_version":" AB ","agent_v2_percent":"25","agent_v2_modo":"Activo","agent_v2_phones":"+51 987 654 321, 987-654-321 , abc"}`)
	m := ajustes(t, w)
	if w.Code != 200 || m["agent_version"] != "ab" || m["agent_v2_percent"] != "25" || m["agent_v2_modo"] != "activo" {
		t.Fatalf("no se guardó/normalizó: %d %v", w.Code, m)
	}
	if m["agent_v2_phones"] != "51987654321,987654321" {
		t.Fatalf("los números se limpian a dígitos y sin vacíos: %q", m["agent_v2_phones"])
	}
}

func TestAjustesInvalidosSeRechazanYNoCambianNada(t *testing.T) {
	s, _ := servidor(t, "")
	for _, c := range []struct{ nombre, cuerpo string }{
		{"versión", `{"agent_version":"v3"}`},
		{"modo", `{"agent_v2_modo":"loco"}`},
		{"porcentaje alto", `{"agent_v2_percent":"101"}`},
		{"porcentaje negativo", `{"agent_v2_percent":"-1"}`},
		{"porcentaje no numérico", `{"agent_v2_percent":"mucho"}`},
		{"uno bueno y uno malo", `{"bot_resume_hours":"6","agent_version":"v3"}`},
	} {
		if w := peticion(t, s, "PUT", "/api/settings", c.cuerpo); w.Code != 400 {
			t.Errorf("%s: debía ser 400 y fue %d", c.nombre, w.Code)
		}
	}
	m := ajustes(t, peticion(t, s, "GET", "/api/settings", ""))
	if m["bot_resume_hours"] != "12" || m["agent_version"] != "v1" {
		t.Fatalf("un valor malo no debe dejar la mitad guardada: %v", m)
	}
}

func TestFijarLaVersionDeUnaConversacion(t *testing.T) {
	s, st := servidor(t, "")
	ctx := context.Background()
	_, conv, err := st.UpsertCustomer(ctx, "51987654321@s.whatsapp.net", "51987654321", "Ana")
	if err != nil {
		t.Fatal(err)
	}
	ruta := "/api/conversations/" + strconv.FormatInt(conv.ID, 10) + "/agent-version"
	if w := peticion(t, s, "POST", ruta, `{"version":"V2"}`); w.Code != 200 {
		t.Fatalf("fijar V2: %d %s", w.Code, w.Body)
	}
	if c, _ := st.GetConversation(ctx, conv.ID); c.AgentVersion != "v2" {
		t.Fatalf("agent_version = %q", c.AgentVersion)
	}
	if w := peticion(t, s, "POST", ruta, `{"version":""}`); w.Code != 200 {
		t.Fatalf("soltar: %d", w.Code)
	}
	if c, _ := st.GetConversation(ctx, conv.ID); c.AgentVersion != "" {
		t.Fatalf("debía volver a los ajustes: %q", c.AgentVersion)
	}
	if w := peticion(t, s, "POST", ruta, `{"version":"v7"}`); w.Code != 400 {
		t.Fatalf("versión inválida: %d", w.Code)
	}
	if w := peticion(t, s, "POST", "/api/conversations/9999/agent-version", `{"version":"v2"}`); w.Code != 404 {
		t.Fatalf("conversación inexistente: %d", w.Code)
	}
	if w := peticion(t, s, "POST", "/api/conversations/x/agent-version", `{"version":"v2"}`); w.Code != 400 {
		t.Fatalf("id inválido: %d", w.Code)
	}
}

func TestVersionDelAgenteSeVeEnLaListaDeConversaciones(t *testing.T) {
	s, st := servidor(t, "")
	ctx := context.Background()
	_, conv, _ := st.UpsertCustomer(ctx, "51987654321@s.whatsapp.net", "51987654321", "Ana")
	_ = st.SetAgentVersion(ctx, conv.ID, "v2")
	_ = st.SetAgentLast(ctx, conv.ID, "v2→v1")
	_, _ = st.DB.Exec(`UPDATE conversations SET last_message='hola' WHERE id=?`, conv.ID)
	w := peticion(t, s, "GET", "/api/conversations", "")
	var lista []struct {
		AgentVersion string `json:"agent_version"`
		AgentLast    string `json:"agent_last"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &lista); err != nil || len(lista) != 1 || lista[0].AgentVersion != "v2" || lista[0].AgentLast != "v2→v1" {
		t.Fatalf("la lista debe traer agent_version y agent_last: %v %s", err, w.Body)
	}
}

func TestMetricasDelAgenteSinAgenteNoFallan(t *testing.T) {
	s, _ := servidor(t, "")
	w := peticion(t, s, "GET", "/api/agent/metricas", "")
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), `"disponible":false`) {
		t.Fatalf("sin agente debe decir que no está disponible: %d %s", w.Code, w.Body)
	}
}

func TestSinSesionNoSePuedeCambiarLaVersion(t *testing.T) {
	s, _ := servidor(t, "")
	for _, r := range []struct{ m, ruta string }{{"PUT", "/api/settings"}, {"POST", "/api/conversations/1/agent-version"}, {"GET", "/api/agent/metricas"}} {
		req := httptest.NewRequest(r.m, r.ruta, strings.NewReader(`{}`))
		w := httptest.NewRecorder()
		s.Routes().ServeHTTP(w, req)
		if w.Code != http.StatusUnauthorized {
			t.Errorf("%s %s sin sesión: %d", r.m, r.ruta, w.Code)
		}
	}
}
