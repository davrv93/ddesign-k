package api

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// El login de cada tienda lee /<slug>/api/public/empresa: su logo, color, dirección, horario y galería (o las fotos
// de su catálogo). Lo de una tienda nunca aparece en la otra, y la galería solo admite archivos de la propia tienda.
func TestEmpresaPublicaParaElLogin(t *testing.T) {
	h, st, _ := servidorMulti(t)
	ctx := context.Background()
	sfx := strings.ReplaceAll(time.Now().Format("150405.000000"), ".", "")
	a, b := "la"+sfx, "lb"+sfx
	for _, slug := range []string{a, b} {
		tn := &store.Tenant{Slug: slug, Name: "Tienda " + slug}
		if err := st.UpsertTenant(ctx, tn); err != nil {
			t.Fatal(err)
		}
		if err := st.ForTenant(tn.ID).SaveProduct(ctx, &store.Product{Code: "V21", Name: "x", Active: true, Image: "/media/products/" + slug + ".jpg",
			Variants: []store.Variant{{Size: "M", Stock: 1}}}); err != nil {
			t.Fatal(err)
		}
	}
	if err := st.SetTenantDatos(ctx, a, "Jr. Prueba 123, Lima", "Lunes a sábado, 9:00–21:00"); err != nil {
		t.Fatal(err)
	}
	if err := st.SetTenantGaleria(ctx, a, []string{store.PrefijoAssets(a) + "look-01.jpg", store.PrefijoAssets(a) + "look-02.jpg"}); err != nil {
		t.Fatal(err)
	}
	if err := st.SetTenantGaleria(ctx, a, []string{store.PrefijoAssets(b) + "ajena.jpg"}); err == nil {
		t.Fatal("la galería aceptó una foto de otra tienda")
	}
	if err := st.SetTenantGaleria(ctx, a, []string{store.PrefijoAssets(a) + "../" + b + "/x.jpg"}); err == nil {
		t.Fatal("la galería aceptó una ruta con ..")
	}

	type emp struct {
		Slug, Nombre, Color, Logo, Direccion, Horario string
		Galeria                                       []string
		FotosCatalogo                                 []string `json:"fotos_catalogo"`
	}
	leer := func(slug string) emp {
		w := llamar(h, "GET", "/"+slug+"/api/public/empresa", "", "")
		if w.Code != 200 {
			t.Fatalf("%s: %d", slug, w.Code)
		}
		var e emp
		_ = json.Unmarshal(w.Body.Bytes(), &e)
		return e
	}
	ea, eb := leer(a), leer(b)
	if ea.Direccion != "Jr. Prueba 123, Lima" || ea.Horario == "" || len(ea.Galeria) != 2 || !strings.HasPrefix(ea.Color, "#") {
		t.Fatalf("empresa A: %+v", ea)
	}
	if len(ea.FotosCatalogo) != 1 || !strings.Contains(ea.FotosCatalogo[0], a) {
		t.Fatalf("fotos del catálogo de A: %v", ea.FotosCatalogo)
	}
	if eb.Direccion != "" || len(eb.Galeria) != 0 || len(eb.FotosCatalogo) != 1 || strings.Contains(eb.FotosCatalogo[0], a) {
		t.Fatalf("B ve datos de A: %+v", eb)
	}
	if w := llamar(h, "GET", "/noexiste/api/public/empresa", "", ""); w.Code != 404 {
		t.Fatalf("tienda inexistente: %d", w.Code)
	}
}
