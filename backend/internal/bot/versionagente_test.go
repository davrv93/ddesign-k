package bot

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
)

func TestResolverVersionDelAgente(t *testing.T) {
	casos := []struct {
		nombre       string
		p            Politica
		fijada, tel  string
		conv         int64
		version, why string
	}{
		{"por defecto es V1", Politica{Global: "v1"}, "", "51987654321", 1, "v1", "global"},
		{"global V2", Politica{Global: "v2"}, "", "51987654321", 1, "v2", "global"},
		{"la conversación fijada en V2 gana a un global V1", Politica{Global: "v1"}, "v2", "51987654321", 1, "v2", "conversación"},
		{"la conversación fijada en V1 gana a un global V2", Politica{Global: "v2"}, "v1", "51987654321", 1, "v1", "conversación"},
		{"un valor fijado inválido se ignora", Politica{Global: "v1"}, "v9", "51987654321", 1, "v1", "global"},
		{"número de prueba va a V2 aunque el global sea V1", Politica{Global: "v1", Numeros: []string{"51987654321"}}, "", "51987654321", 1, "v2", "número"},
		{"el número se compara por sus últimos 9 dígitos", Politica{Global: "v1", Numeros: []string{"987654321"}}, "", "+51 987 654 321", 1, "v2", "número"},
		{"otro número no entra", Politica{Global: "v1", Numeros: []string{"987654320"}}, "", "51987654321", 1, "v1", "global"},
		{"la conversación fijada en V1 gana a la lista de números", Politica{Global: "v1", Numeros: []string{"51987654321"}}, "v1", "51987654321", 1, "v1", "conversación"},
		{"ab con 0 % nadie va a V2", Politica{Global: "ab", Percent: 0}, "", "51987654321", 1, "v1", "ab"},
		{"ab con 100 % todas van a V2", Politica{Global: "ab", Percent: 100}, "", "51987654321", 1, "v2", "ab"},
		{"sin teléfono se reparte por la conversación", Politica{Global: "ab", Percent: 100}, "", "", 7, "v2", "ab"},
	}
	for _, c := range casos {
		v, why := c.p.Resolver(c.fijada, c.tel, c.conv)
		if v != c.version || why != c.why {
			t.Errorf("%s: %q/%q, quería %q/%q", c.nombre, v, why, c.version, c.why)
		}
	}
}

func TestCubetaEsEstableYReparteElPorcentaje(t *testing.T) {
	if Cubeta("987654321") != Cubeta("987654321") {
		t.Fatal("la misma clienta debe caer siempre en la misma cubeta")
	}
	for _, pct := range []int{10, 30, 50, 90} {
		p := Politica{Global: "ab", Percent: pct}
		v2 := 0
		const n = 2000
		for i := 0; i < n; i++ {
			if v, _ := p.Resolver("", "5198"+strconv.Itoa(1000000+i), int64(i)); v == "v2" {
				v2++
			}
		}
		if got := v2 * 100 / n; got < pct-4 || got > pct+4 {
			t.Errorf("con %d %% fue a V2 el %d %%", pct, got)
		}
	}
}

func TestSubirElPorcentajeNoCambiaDeVersionALasQueYaEstabanEnV2(t *testing.T) {
	for i := 0; i < 500; i++ {
		tel := "5198" + strconv.Itoa(2000000+i)
		antes, _ := Politica{Global: "ab", Percent: 20}.Resolver("", tel, int64(i))
		despues, _ := Politica{Global: "ab", Percent: 60}.Resolver("", tel, int64(i))
		if antes == "v2" && despues != "v2" {
			t.Fatalf("%s estaba en V2 con 20 %% y salió con 60 %%", tel)
		}
	}
}

func TestPoliticaDeAjustesSeguraPorDefecto(t *testing.T) {
	b, st, _ := setup(t, `{}`, false)
	ctx := context.Background()
	p := PoliticaAgente(ctx, b.store)
	if p.Global != "v1" || p.Percent != 0 || p.Modo != "sombra" || len(p.Numeros) != 0 {
		t.Fatalf("sin ajustes debía ser V1, 0 %%, sombra: %+v", p)
	}
	for k, v := range map[string]string{AjusteVersion: "ab", AjustePorcentaje: "35", AjusteNumeros: "+51 987 654 321, 999 111 222, 51987654321", AjusteModoV2: "activo"} {
		if err := st.SetSetting(ctx, k, v); err != nil {
			t.Fatal(err)
		}
	}
	p = PoliticaAgente(ctx, st)
	if p.Global != "ab" || p.Percent != 35 || p.Modo != "activo" {
		t.Fatalf("política leída mal: %+v", p)
	}
	if len(p.Numeros) != 2 || p.Numeros[0] != "51987654321" || p.Numeros[1] != "999111222" {
		t.Fatalf("los números se limpian y no se repiten: %v", p.Numeros)
	}
	// Valores corruptos en la base: nunca dejan a una clienta sin versión.
	for k, v := range map[string]string{AjusteVersion: "loco", AjustePorcentaje: "9000", AjusteModoV2: "x"} {
		_ = st.SetSetting(ctx, k, v)
	}
	p = PoliticaAgente(ctx, st)
	if p.Global != "v1" || p.Percent != 100 || p.Modo != "sombra" {
		t.Fatalf("valores inválidos deben caer a V1/sombra y el porcentaje se acota: %+v", p)
	}
}

func TestEtiquetaDeQuienHablo(t *testing.T) {
	casos := map[string]*agente.Reply{
		"v1":          {Version: "v1"},
		"v1 sin dato": nil,
		"v2":          {Version: "v2", V2: &agente.V2Info{Modo: "activo", Enviado: "v2"}},
		"v2→v1":       {Version: "v2", V2: &agente.V2Info{Modo: "activo", Enviado: "v1"}},
		"v2 (sombra)": {Version: "v2", V2: &agente.V2Info{Modo: "sombra", Enviado: "v1"}},
	}
	esperado := map[string]string{"v1": "v1", "v1 sin dato": "v1", "v2": "v2", "v2→v1": "v2→v1", "v2 (sombra)": "v2 (sombra)"}
	for n, r := range casos {
		if got := etiquetaAgente(r); got != esperado[n] {
			t.Errorf("%s: %q, quería %q", n, got, esperado[n])
		}
	}
}

// agenteQueResponde es un agente de mentira que devuelve la versión pedida y guarda la última petición.
func agenteQueResponde(t *testing.T, ultima *agente.Request, enviado string) *agente.Client {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		*ultima = agente.Request{} // Decode no pone a cero los campos que no vienen
		_ = json.NewDecoder(r.Body).Decode(ultima)
		rep := agente.Reply{Intencion: "saludo", Accion: "responder", Respuesta: "¡Hola Ana! 😊", Version: ultima.Version}
		if ultima.Version == "v2" {
			rep.V2 = &agente.V2Info{Modo: ultima.Modo, Enviado: enviado}
		}
		_ = json.NewEncoder(w).Encode(rep)
	}))
	t.Cleanup(srv.Close)
	return agente.New(srv.URL, 5*time.Second)
}

func TestElBotPideLaVersionQueDiceLaPolitica(t *testing.T) {
	b, st, _ := setup(t, `{}`, false)
	var last agente.Request
	b.Agent = agenteQueResponde(t, &last, "v2")
	ctx := context.Background()

	handle(b, text("hola"))
	if last.Version != "v1" || last.Modo != "" {
		t.Fatalf("por defecto va V1 y sin modo: version=%q modo=%q", last.Version, last.Modo)
	}

	_ = st.SetSetting(ctx, AjusteVersion, "v2")
	_ = st.SetSetting(ctx, AjusteModoV2, "activo")
	handle(b, text("hola"))
	if last.Version != "v2" || last.Modo != "activo" {
		t.Fatalf("con V2 global y modo activo: version=%q modo=%q", last.Version, last.Modo)
	}

	_ = st.SetSetting(ctx, AjusteModoV2, "sombra")
	handle(b, text("hola"))
	if last.Version != "v2" || last.Modo != "sombra" {
		t.Fatalf("con V2 en sombra: version=%q modo=%q", last.Version, last.Modo)
	}

	// Una persona fija la conversación en V1: gana al global.
	cu, conv, err := st.UpsertCustomer(ctx, "51987654321@s.whatsapp.net", "51987654321", "Ana López")
	if err != nil || cu == nil {
		t.Fatal(err)
	}
	if err := st.SetAgentVersion(ctx, conv.ID, "v1"); err != nil {
		t.Fatal(err)
	}
	handle(b, text("hola"))
	if last.Version != "v1" || last.Modo != "" {
		t.Fatalf("la conversación fijada en V1 debía ganar: version=%q modo=%q", last.Version, last.Modo)
	}
}

func TestElBotAnotaQuienHabloEnElUltimoTurno(t *testing.T) {
	b, st, _ := setup(t, `{}`, false)
	var last agente.Request
	b.Agent = agenteQueResponde(t, &last, "v1")
	ctx := context.Background()
	_ = st.SetSetting(ctx, AjusteVersion, "v2")
	_ = st.SetSetting(ctx, AjusteModoV2, "activo")
	handle(b, text("hola"))
	_, conv, _ := st.UpsertCustomer(ctx, "51987654321@s.whatsapp.net", "51987654321", "Ana López")
	got, err := st.GetConversation(ctx, conv.ID)
	if err != nil || got.AgentLast != "v2→v1" {
		t.Fatalf("V2 activo miró y habló V1: agent_last=%q err=%v", got.AgentLast, err)
	}
	b.Agent = agenteQueResponde(t, &last, "v2")
	handle(b, text("hola"))
	got, _ = st.GetConversation(ctx, conv.ID)
	if got.AgentLast != "v2" {
		t.Fatalf("V2 habló: agent_last=%q", got.AgentLast)
	}
	_ = st.SetSetting(ctx, AjusteVersion, "v1")
	handle(b, text("hola"))
	got, _ = st.GetConversation(ctx, conv.ID)
	if got.AgentLast != "v1" {
		t.Fatalf("de vuelta en V1: agent_last=%q", got.AgentLast)
	}
}
