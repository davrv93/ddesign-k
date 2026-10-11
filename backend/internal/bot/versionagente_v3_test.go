package bot

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
)

func TestResolverV3(t *testing.T) {
	casos := []struct {
		nombre       string
		p            Politica
		fijada, tel  string
		conv         int64
		version, why string
	}{
		{"global V3", Politica{Global: "v3"}, "", "51987654321", 1, "v3", "global"},
		{"la conversación fijada en V3 gana al global", Politica{Global: "v1"}, "v3", "51987654321", 1, "v3", "conversación"},
		{"número de prueba va a V3", Politica{Global: "v1", V3Numeros: []string{"987654321"}}, "", "+51 987 654 321", 1, "v3", "número"},
		{"número de V3 gana al de V2", Politica{Global: "v1", Numeros: []string{"51987654321"}, V3Numeros: []string{"51987654321"}}, "", "51987654321", 1, "v3", "número"},
		{"ab con 0 % nadie va a V3", Politica{Global: "ab", Percent: 0, V3Percent: 0}, "", "51987654321", 1, "v1", "ab"},
		{"ab con todo en V3", Politica{Global: "ab", Percent: 0, V3Percent: 100}, "", "51987654321", 1, "v3", "ab"},
	}
	for _, c := range casos {
		v, why := c.p.Resolver(c.fijada, c.tel, c.conv)
		if v != c.version || why != c.why {
			t.Errorf("%s: %q/%q, quería %q/%q", c.nombre, v, why, c.version, c.why)
		}
	}
}

func TestModoDeV3(t *testing.T) {
	p := Politica{Modo: "activo", V3Modo: "sombra"}
	if p.ModoDe("v3") != "sombra" || p.ModoDe("v2") != "activo" {
		t.Fatalf("el modo de cada versión: v3=%q v2=%q", p.ModoDe("v3"), p.ModoDe("v2"))
	}
}

func TestEtiquetaV3(t *testing.T) {
	casos := map[string]*agente.Reply{
		"v3":          {Version: "v3", V3: &agente.V3Info{Modo: "activo"}},
		"v3→v1":       {Version: "v3", V3: &agente.V3Info{Modo: "activo", Caida: "sin clave"}},
		"v3 (sombra)": {Version: "v3", V3: &agente.V3Info{Modo: "sombra"}},
	}
	esperado := map[string]string{"v3": "v3", "v3→v1": "v3→v1", "v3 (sombra)": "v3 (sombra)"}
	for n, r := range casos {
		if got := etiquetaAgente(r); got != esperado[n] {
			t.Errorf("%s: %q, quería %q", n, got, esperado[n])
		}
	}
}

func TestPoliticaV3DeAjustes(t *testing.T) {
	b, st, _ := setup(t, `{}`, false)
	ctx := context.Background()
	for k, v := range map[string]string{
		AjusteVersion: "v3", AjusteV3Porcentaje: "20", AjusteV3Numeros: "+51 987 654 321", AjusteModoV3: "activo",
	} {
		if err := st.SetSetting(ctx, k, v); err != nil {
			t.Fatal(err)
		}
	}
	p := PoliticaAgente(ctx, b.store)
	if p.Global != "v3" || p.V3Percent != 20 || p.V3Modo != "activo" || len(p.V3Numeros) != 1 || p.V3Numeros[0] != "51987654321" {
		t.Fatalf("política V3 leída mal: %+v", p)
	}
}

// La ficha de V3 viaja opaca: el bot no la interpreta ni la recorta; solo toca los campos que ya tocaba.
func TestFichaV3OpaqueEnGo(t *testing.T) {
	ficha := json.RawMessage(`{"sabemos":{"talla":"M"},"pendiente":"","producto":"V21",` +
		`"pedido":{"codigo":"V21","talla":"M","confirmado":false},"herramientas":["stock","enviar_fotos"],"extra":"x"}`)
	out := memEditar(ficha, func(m map[string]any) { m["pendiente"] = "talla" })
	var got map[string]any
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatal(err)
	}
	if _, ok := got["pedido"]; !ok {
		t.Fatal("el bot perdió el pedido de la ficha V3")
	}
	if _, ok := got["herramientas"]; !ok {
		t.Fatal("el bot perdió las herramientas de la ficha V3")
	}
	if got["pendiente"] != "talla" {
		t.Fatalf("no actualizó pendiente: %v", got["pendiente"])
	}
	if _, ok := got["sabemos"].(map[string]any); !ok {
		t.Fatal("perdió sabemos")
	}
}
