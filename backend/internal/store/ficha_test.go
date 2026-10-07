package store

import (
	"context"
	"errors"
	"fmt"
	"testing"
	"time"
)

func fichaIrla() *Ficha {
	return &Ficha{
		Codigo: "V35", Piezas: []string{"vestido"},
		Atributos: map[string]*Dato{
			"silueta": {Valor: "ajustada al cuerpo, estilo lápiz", Fuente: "ambas"},
			"tela":    {Valor: "roma", Fuente: "diners"},
			"cintura": {Valor: "ajustada, sin pretina", Fuente: "foto"},
			"forro":   nil,
		},
		Detalles:        []Dato{{Valor: "escote corazón", Fuente: "ambas"}, {Valor: "tirantes finos", Fuente: "foto"}},
		Discrepancias:   []string{"el catálogo lo registra en palo rosa, pero en las fotos es negro"},
		PendienteTienda: []string{"medidas por talla", "forro"},
	}
}

// Lo editado a mano queda con fuente «tienda»; lo que no cambió conserva la suya, aunque el navegador mande otra.
func TestNormalizarFicha(t *testing.T) {
	prev := fichaIrla()
	nueva := fichaIrla()
	nueva.Atributos["tela"] = &Dato{Valor: "crepé", Fuente: "diners"} // editado
	nueva.Atributos["forro"] = &Dato{Valor: "  forrado  ", Fuente: "foto"} // nuevo
	nueva.Atributos["silueta"].Fuente = "foto"                         // sin cambio de valor: se ignora la fuente
	nueva.Atributos["cintura"] = &Dato{Valor: ""}                      // borrado → no figura
	nueva.Detalles = append(nueva.Detalles, Dato{Valor: "abertura atrás", Fuente: "ambas"})
	c := "lavar a mano"
	nueva.Cuidados = &c
	f, err := NormalizarFicha(prev, nueva)
	if err != nil {
		t.Fatal(err)
	}
	cases := map[string]*Dato{
		"tela":    {Valor: "crepé", Fuente: FuenteManual},
		"forro":   {Valor: "forrado", Fuente: FuenteManual},
		"silueta": {Valor: "ajustada al cuerpo, estilo lápiz", Fuente: "ambas"},
	}
	for k, want := range cases {
		if got := f.Atributos[k]; got == nil || *got != *want {
			t.Errorf("%s = %+v, quiero %+v", k, got, want)
		}
	}
	if f.Atributos["cintura"] != nil || f.Atributos["escote"] != nil {
		t.Errorf("vacío o sin dato debe quedar null: %+v %+v", f.Atributos["cintura"], f.Atributos["escote"])
	}
	if len(f.Detalles) != 3 || f.Detalles[0].Fuente != "ambas" || f.Detalles[2].Fuente != FuenteManual {
		t.Errorf("detalles: %+v", f.Detalles)
	}
	if n, total := f.Datos(); n != 4 || total != 13 { // silueta, tela, forro + cuidados
		t.Errorf("datos %d/%d", n, total)
	}
	if _, err := NormalizarFicha(prev, &Ficha{Atributos: map[string]*Dato{"precio": {Valor: "1"}}}); err == nil {
		t.Error("un atributo desconocido debe rechazarse")
	}
}

// La ficha es de la empresa: con el id del producto de otra no se lee ni se escribe.
func TestFichaPorEmpresa(t *testing.T) {
	for nombre, base := range backends(t) {
		t.Run(nombre, func(t *testing.T) {
			ctx := context.Background()
			sfx := fmt.Sprintf("%d", time.Now().UnixNano()%1_000_000)
			a := nuevaEmpresa(t, base, "fa-"+sfx, "Alfa", 3)
			b := nuevaEmpresa(t, base, "fb-"+sfx, "Beta", 3)
			if err := a.st.SetFichaPorCodigo(ctx, "V21", fichaIrla()); err != nil {
				t.Fatal(err)
			}
			got, err := a.st.GetFicha(ctx, a.prod.ID)
			if err != nil || got == nil || got.Atributos["tela"].Valor != "roma" {
				t.Fatalf("ficha de A: %+v %v", got, err)
			}
			p, _ := a.st.GetProduct(ctx, a.prod.ID)
			if p.Ficha == nil || len(p.Ficha.Discrepancias) != 1 {
				t.Fatalf("el producto no trae su ficha: %+v", p.Ficha)
			}
			if f, _ := b.st.GetFicha(ctx, b.prod.ID); f != nil {
				t.Fatalf("B tiene la ficha de A (mismo código V21): %+v", f)
			}
			if _, err := b.st.GetFicha(ctx, a.prod.ID); !errors.Is(err, ErrNotFound) {
				t.Fatalf("B leyó la ficha de A: %v", err)
			}
			if err := b.st.SetFicha(ctx, a.prod.ID, &Ficha{}); !errors.Is(err, ErrNotFound) {
				t.Fatalf("B escribió la ficha de A: %v", err)
			}
			// Guardar el producto desde el panel no borra la ficha.
			p.Name = "Vestido renombrado"
			if err := a.st.SaveProduct(ctx, p); err != nil {
				t.Fatal(err)
			}
			if got, _ := a.st.GetFicha(ctx, a.prod.ID); got == nil {
				t.Fatal("guardar el producto borró la ficha")
			}
		})
	}
}
