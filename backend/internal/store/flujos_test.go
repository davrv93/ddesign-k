package store

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"
)

func grafoBase() *Grafo {
	return &Grafo{
		Nodos: []Nodo{{ID: "i", Tipo: "inicio"}, {ID: "w", Tipo: "esperar", Minutos: 30},
			{ID: "m", Tipo: "mensaje", Mensaje: "texto", Texto: "hola"}, {ID: "c", Tipo: "condicion", Condicion: "respondio"},
			{ID: "m2", Tipo: "mensaje", Mensaje: "emoji", Texto: "🥰"}, {ID: "f", Tipo: "fin"}},
		Aristas: []Arista{{"i", "siguiente", "w"}, {"w", "siguiente", "m"}, {"m", "siguiente", "c"}, {"c", "si", "f"}, {"c", "no", "m2"}},
	}
}

func TestValidarGrafo(t *testing.T) {
	if err := ValidarGrafo(grafoBase()); err != nil {
		t.Fatalf("grafo válido: %v", err)
	}
	casos := map[string]func(g *Grafo){
		"sin inicio":                func(g *Grafo) { g.Nodos[0].Tipo = "fin" },
		"dos inicios":               func(g *Grafo) { g.Nodos[5].Tipo = "inicio" },
		"arista a nodo inexistente": func(g *Grafo) { g.Aristas = append(g.Aristas, Arista{"m2", "siguiente", "zzz"}) },
		"salida usada dos veces":    func(g *Grafo) { g.Aristas = append(g.Aristas, Arista{"c", "si", "m2"}) },
		"salida que no existe":      func(g *Grafo) { g.Aristas = append(g.Aristas, Arista{"m2", "si", "f"}) },
		"ciclo sin espera":          func(g *Grafo) { g.Aristas = append(g.Aristas, Arista{"m2", "siguiente", "m"}) },
		"espera fuera de rango":     func(g *Grafo) { g.Nodos[1].Minutos = 0 },
		"mensaje vacío":             func(g *Grafo) { g.Nodos[2].Texto = "" },
		"condición sin valor":       func(g *Grafo) { g.Nodos[3].Condicion = "temperatura" },
		"inicio sin conectar":       func(g *Grafo) { g.Aristas = g.Aristas[1:] },
		"ids repetidos":             func(g *Grafo) { g.Nodos[4].ID = "m" },
		"nada lleva al inicio":      func(g *Grafo) { g.Aristas = append(g.Aristas, Arista{"m2", "siguiente", "i"}) },
	}
	for nombre, romper := range casos {
		g := grafoBase()
		romper(g)
		if err := ValidarGrafo(g); err == nil || !errors.Is(err, ErrSeguimientoInvalido) {
			t.Errorf("%s: debía fallar (%v)", nombre, err)
		}
	}
	// Un ciclo CON espera es válido (repetir un recordatorio cada cierto tiempo).
	g := grafoBase()
	g.Aristas = append(g.Aristas, Arista{"m2", "siguiente", "w"})
	if err := ValidarGrafo(g); err != nil {
		t.Errorf("ciclo con espera: %v", err)
	}
}

func TestPasosDerivadosYDesdePasos(t *testing.T) {
	g := grafoBase()
	ps := g.PasosDerivados()
	if len(ps) != 2 || ps[0].Texto != "hola" || ps[0].EsperaMin != 30 || ps[1].Tipo != "emoji" {
		t.Fatalf("mensajes en orden con la espera acumulada: %+v", ps)
	}
	if g.IndiceMensaje("m2") != 1 || g.IndiceMensaje("w") != -1 {
		t.Fatal("índice de mensaje")
	}
	vieja := []Paso{{EsperaMin: 60, Tipo: "emoji", Texto: "🥰"}, {EsperaMin: 300, Tipo: "beneficio"}}
	g2 := GrafoDesdePasos(vieja)
	if err := ValidarGrafo(g2); err != nil {
		t.Fatalf("una lista vieja pasa a flujo válido: %v", err)
	}
	d := g2.PasosDerivados()
	if len(d) != 2 || d[0].EsperaMin != 60 || d[1].EsperaMin != 300 || d[1].Tipo != "beneficio" {
		t.Fatalf("y vuelve a dar los mismos pasos: %+v", d)
	}
	if g2.esperaAntesDeMensaje(1) != "espera-2" || g2.esperaAntesDeMensaje(9) != "" {
		t.Fatal("retomar una lista vieja en la espera del paso que tocaba")
	}
	if !strings.Contains(nombreNodo(&g2.Nodos[0]), "Inicio") {
		t.Fatal("nombre legible del nodo")
	}
}

// Estado del flujo por conversación en SQLite y en MariaDB: armar en el inicio, avanzar de nodo, pausa y «continuar»,
// prueba con la secuencia apagada, último envío y aislamiento entre empresas.
func TestEstadoDelFlujo(t *testing.T) {
	for nombre, base := range backends(t) {
		t.Run(nombre, func(t *testing.T) {
			ctx := context.Background()
			sfx := fmt.Sprintf("%d", time.Now().UnixNano()%1_000_000)
			a := nuevaEmpresa(t, base, "fla-"+sfx, "Alfa", 5)
			b := nuevaEmpresa(t, base, "flb-"+sfx, "Beta", 5)
			q := &Secuencia{Nombre: "Flujo", Activa: true, Grafo: grafoBase()}
			if err := a.st.CrearSecuencia(ctx, q); err != nil {
				t.Fatal(err)
			}
			leida, err := a.st.GetSecuencia(ctx, q.ID)
			if err != nil || leida.Grafo == nil || len(leida.Grafo.Nodos) != 6 || len(leida.Pasos) != 2 {
				t.Fatalf("el grafo se guarda y se lee: %+v %v", leida, err)
			}
			if _, err := b.st.GetSecuencia(ctx, q.ID); !errors.Is(err, ErrNotFound) {
				t.Fatalf("otra empresa no ve el flujo: %v", err)
			}
			ahora := time.Now()
			if err := a.st.ArmarSeguimiento(ctx, a.conv.ID, leida, "V01", ahora); err != nil {
				t.Fatal(err)
			}
			e, _ := a.st.SeguimientoDe(ctx, a.conv.ID)
			if e == nil || e.Nodo != "i" || e.Estado != EstadoActivo {
				t.Fatalf("se arma en el inicio: %+v", e)
			}
			if err := a.st.PonerNodo(ctx, a.conv.ID, q.ID, "w", 0, ahora.Add(30*time.Minute)); err != nil {
				t.Fatal(err)
			}
			if _, err := a.st.PausarYVer(ctx, a.conv.ID); err != nil {
				t.Fatal(err)
			}
			if err := a.st.ArmarSeguimiento(ctx, a.conv.ID, leida, "V01", ahora.Add(time.Hour)); err != nil {
				t.Fatal(err)
			}
			if e, _ = a.st.SeguimientoDe(ctx, a.conv.ID); e.Nodo != "w" || e.Estado != EstadoActivo || e.ProximoEn.Sub(ahora.Add(time.Hour)) < 29*time.Minute {
				t.Fatalf("«continuar» sigue en la espera y vuelve a contar: %+v", e)
			}
			// Prueba con la secuencia apagada: entra en los vencidos y el armado normal no la pisa.
			if err := a.st.ActivarSecuencia(ctx, q.ID, false); err != nil {
				t.Fatal(err)
			}
			apagada, _ := a.st.GetSecuencia(ctx, q.ID)
			if err := a.st.ArmarPrueba(ctx, a.conv.ID, apagada, "V01", 1); err != nil {
				t.Fatal(err)
			}
			if err := a.st.ArmarSeguimiento(ctx, a.conv.ID, apagada, "V01", ahora); err != nil {
				t.Fatal(err)
			}
			vencidos, err := a.st.SeguimientosVencidos(ctx, ahora.Add(time.Minute), 10)
			if err != nil || len(vencidos) != 1 || !vencidos[0].Prueba || vencidos[0].Nodo != "i" || vencidos[0].EscalaMin != 1 {
				t.Fatalf("la prueba corre aunque la secuencia esté apagada: %+v %v", vencidos, err)
			}
			if v, _ := b.st.SeguimientosVencidos(ctx, ahora.Add(time.Minute), 10); len(v) != 0 {
				t.Fatalf("la otra empresa no ve la prueba: %+v", v)
			}
			if _, ok := a.st.UltimoEnvioFlujo(ctx, a.conv.ID); ok {
				t.Fatal("aún no salió nada")
			}
			if err := a.st.AnotarEnvio(ctx, a.conv.ID, q.ID, 1, 0, "texto", "prueba", ""); err != nil {
				t.Fatal(err)
			}
			if _, ok := a.st.UltimoEnvioFlujo(ctx, a.conv.ID); !ok {
				t.Fatal("el envío de la prueba cuenta como último envío del flujo")
			}
			if n := a.st.EnviadosHoy(ctx, 0); n != 0 {
				t.Fatalf("pero no para el tope: %d", n)
			}
			if err := a.st.TerminarFlujo(ctx, a.conv.ID, q.ID); err != nil {
				t.Fatal(err)
			}
			if e, _ = a.st.SeguimientoDe(ctx, a.conv.ID); e.Estado != "terminado" {
				t.Fatalf("terminado: %+v", e)
			}
		})
	}
}
