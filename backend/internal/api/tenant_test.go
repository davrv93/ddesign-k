package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/ai"
	"github.com/davrv93/ddesign-k/backend/internal/auth"
	"github.com/davrv93/ddesign-k/backend/internal/bot"
	"github.com/davrv93/ddesign-k/backend/internal/config"
	"github.com/davrv93/ddesign-k/backend/internal/evolution"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// Multiempresa por HTTP: dos empresas detrás del mismo backend, /alfa/… y /beta/…. Cada una ve solo lo suyo, un
// token de una no abre la otra, las fotos van por carpeta y una ruta desconocida es 404.

func servidorMulti(t *testing.T) (http.Handler, *store.Store, string) {
	t.Helper()
	var st *store.Store
	var err error
	if dsn := os.Getenv("JMD_TEST_MYSQL_DSN"); dsn != "" {
		st, err = store.OpenMySQL(dsn)
	} else {
		st, err = store.Open(t.TempDir())
	}
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.DB.Close() })
	data := t.TempDir()
	// EvolutionURL vacío: el stack nuevo no tiene WhatsApp.
	cfg := &config.Config{DataDir: data, BusinessName: "JMD", Currency: "S/", MultiTenant: true}
	evo := evolution.New("http://127.0.0.1:1", "g", "kd", "t")
	aic := ai.New("", "m", "", time.Second)
	b := bot.New(cfg, st.ForTenant(0), evo, aic)
	s := New(cfg, st, evo, aic, b, auth.New("jwt-multi", "admin", ""), NewHub())
	return s.Routes(), st, data
}

func llamar(h http.Handler, method, path, token, body string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	if body != "" {
		r.Header.Set("Content-Type", "application/json")
	}
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func login(t *testing.T, h http.Handler, slug, clave string) string {
	t.Helper()
	w := llamar(h, "POST", "/"+slug+"/api/auth/login", "", `{"user":"admin","password":"`+clave+`"}`)
	if w.Code != 200 {
		t.Fatalf("login %s: %d %s", slug, w.Code, w.Body)
	}
	var out struct{ Token string }
	_ = json.Unmarshal(w.Body.Bytes(), &out)
	return out.Token
}

func TestEmpresasAisladasPorHTTP(t *testing.T) {
	h, st, data := servidorMulti(t)
	ctx := context.Background()
	sfx := time.Now().Format("150405.000000")
	sfx = strings.ReplaceAll(sfx, ".", "")
	alfa, beta := "alfa"+sfx, "beta"+sfx
	ids := map[string]int64{}
	for slug, nombre := range map[string]string{alfa: "Alfa Moda", beta: "Beta Ropa"} {
		orden := map[string]int{alfa: 2, beta: 1}[slug]
		tn := &store.Tenant{Slug: slug, Name: nombre, WhatsApp: "51900000000", Orden: orden}
		if err := st.UpsertTenant(ctx, tn); err != nil {
			t.Fatal(err)
		}
		ids[slug] = tn.ID
		ts := st.ForTenant(tn.ID)
		if err := ts.UpsertUser(ctx, "admin", "clave-"+slug, "Admin", "admin"); err != nil {
			t.Fatal(err)
		}
		if err := ts.SaveProduct(ctx, &store.Product{Code: "V21", Name: "Prenda de " + nombre, Price: 50, Active: true,
			Variants: []store.Variant{{Size: "M", Stock: 2}}}); err != nil {
			t.Fatal(err)
		}
	}
	ta, tb := login(t, h, alfa, "clave-"+alfa), login(t, h, beta, "clave-"+beta)

	// Portada: lista pública en su orden, solo con los campos de la tarjeta.
	w0 := llamar(h, "GET", "/api/empresas", "", "")
	if w0.Code != 200 {
		t.Fatalf("/api/empresas: %d", w0.Code)
	}
	for _, prohibido := range []string{"whatsapp", "51900000000", `"id"`, "currency", "admin", "password"} {
		if strings.Contains(w0.Body.String(), prohibido) {
			t.Fatalf("/api/empresas expone %q: %s", prohibido, w0.Body)
		}
	}
	var emps []EmpresaPublica
	_ = json.Unmarshal(w0.Body.Bytes(), &emps)
	pos := map[string]int{}
	for i, e := range emps {
		pos[e.Slug] = i
		if e.Slug == alfa && (e.Productos != 1 || e.Nombre != "Alfa Moda" || !strings.HasPrefix(e.Color, "#") || len(e.Color) != 7) {
			t.Fatalf("tarjeta de alfa: %+v", e)
		}
	}
	if _, ok := pos[alfa]; !ok || pos[beta] > pos[alfa] {
		t.Fatalf("orden de la portada: beta (1) antes que alfa (2): %v", pos)
	}

	// La clave de una empresa no entra en la otra.
	if w := llamar(h, "POST", "/"+alfa+"/api/auth/login", "", `{"user":"admin","password":"clave-`+beta+`"}`); w.Code != 401 {
		t.Fatalf("la clave de beta entró en alfa: %d", w.Code)
	}
	// Sin usuario del .env en multiempresa.
	if w := llamar(h, "POST", "/"+alfa+"/api/auth/login", "", `{"user":"admin","password":""}`); w.Code != 401 {
		t.Fatalf("clave vacía: %d", w.Code)
	}

	// Cada token ve solo su catálogo.
	for slug, tok := range map[string]string{alfa: ta, beta: tb} {
		w := llamar(h, "GET", "/"+slug+"/api/products", tok, "")
		if w.Code != 200 {
			t.Fatalf("%s productos: %d %s", slug, w.Code, w.Body)
		}
		var ps []store.Product
		_ = json.Unmarshal(w.Body.Bytes(), &ps)
		if len(ps) != 1 || !strings.Contains(ps[0].Name, map[string]string{alfa: "Alfa", beta: "Beta"}[slug]) {
			t.Fatalf("%s ve %d productos: %+v", slug, len(ps), ps)
		}
	}
	// El token de alfa no abre beta.
	if w := llamar(h, "GET", "/"+beta+"/api/products", ta, ""); w.Code != 401 {
		t.Fatalf("el token de alfa abrió beta: %d", w.Code)
	}
	if w := llamar(h, "GET", "/"+beta+"/api/orders", ta, ""); w.Code != 401 {
		t.Fatalf("el token de alfa abrió los pedidos de beta: %d", w.Code)
	}

	// Con un id de beta, alfa no encuentra nada (ni para leer ni para borrar).
	pb, _ := st.ForTenant(ids[beta]).GetProductByCode(ctx, "V21")
	if w := llamar(h, "PUT", "/"+alfa+"/api/products/"+itoa(pb.ID), ta, `{"code":"V21","name":"pisado","active":true}`); w.Code == 200 {
		t.Fatalf("alfa editó el producto de beta: %d", w.Code)
	}
	_ = llamar(h, "DELETE", "/"+alfa+"/api/products/"+itoa(pb.ID), ta, "")
	if p, err := st.ForTenant(ids[beta]).GetProduct(ctx, pb.ID); err != nil || p.Name != "Prenda de Beta Ropa" {
		t.Fatalf("el producto de beta cambió: %+v %v", p, err)
	}

	// Catálogo público por empresa, con su nombre.
	w := llamar(h, "GET", "/"+beta+"/api/public/catalog", "", "")
	var cat struct {
		Business string
		Products []struct{ Name string }
	}
	_ = json.Unmarshal(w.Body.Bytes(), &cat)
	if w.Code != 200 || cat.Business != "Beta Ropa" || len(cat.Products) != 1 || cat.Products[0].Name != "Prenda de Beta Ropa" {
		t.Fatalf("catálogo de beta: %d %+v", w.Code, cat)
	}

	// Fotos por carpeta de empresa.
	dir := filepath.Join(data, "tenants", alfa, "media", "products")
	_ = os.MkdirAll(dir, 0o755)
	_ = os.WriteFile(filepath.Join(dir, "foto.jpg"), []byte("jpg-alfa"), 0o644)
	if w := llamar(h, "GET", "/"+alfa+"/media/products/foto.jpg", "", ""); w.Code != 200 || w.Body.String() != "jpg-alfa" {
		t.Fatalf("foto de alfa: %d", w.Code)
	}
	if w := llamar(h, "GET", "/"+beta+"/media/products/foto.jpg", "", ""); w.Code != 404 {
		t.Fatalf("beta sirvió la foto de alfa: %d", w.Code)
	}

	// Rutas desconocidas o reservadas.
	for _, p := range []string{"/noexiste/api/public/info", "/api/public/info", "/media/products/foto.jpg", "/../etc/passwd"} {
		if w := llamar(h, "GET", p, "", ""); w.Code != 404 {
			t.Fatalf("%s: %d (esperaba 404)", p, w.Code)
		}
	}
	// WhatsApp apagado en el stack nuevo: el panel lo ve como no disponible y no envía nada.
	if w := llamar(h, "GET", "/"+alfa+"/api/whatsapp/status", ta, ""); !strings.Contains(w.Body.String(), `"available":false`) {
		t.Fatalf("whatsapp: %s", w.Body)
	}
	if w := llamar(h, "POST", "/"+alfa+"/webhook/evolution/x", "", "{}"); w.Code != 403 && w.Code != 404 {
		t.Fatalf("webhook: %d", w.Code)
	}
}

func itoa(n int64) string { b, _ := json.Marshal(n); return string(b) }

// Ficha técnica por HTTP: se lee y se guarda en el producto de la empresa; lo editado queda con fuente «tienda» y la
// ficha de una empresa no se toca con el token de otra.
func TestFichaPorHTTP(t *testing.T) {
	h, st, _ := servidorMulti(t)
	ctx := context.Background()
	sfx := strings.ReplaceAll(time.Now().Format("150405.000000"), ".", "")
	ids := map[string]int64{}
	toks := map[string]string{}
	for _, slug := range []string{"fx" + sfx, "fy" + sfx} {
		tn := &store.Tenant{Slug: slug, Name: slug}
		if err := st.UpsertTenant(ctx, tn); err != nil {
			t.Fatal(err)
		}
		ts := st.ForTenant(tn.ID)
		_ = ts.UpsertUser(ctx, "admin", "clave-"+slug, "", "")
		p := &store.Product{Code: "V35", Name: "Irla", Active: true, Variants: []store.Variant{{Size: "M", Stock: 1}}}
		if err := ts.SaveProduct(ctx, p); err != nil {
			t.Fatal(err)
		}
		if err := ts.SetFicha(ctx, p.ID, &store.Ficha{Codigo: "V35", Atributos: map[string]*store.Dato{
			"tela": {Valor: "roma", Fuente: "diners"}, "largo": {Valor: "a la rodilla", Fuente: "ambas"}}}); err != nil {
			t.Fatal(err)
		}
		ids[slug] = p.ID
		toks[slug] = login(t, h, slug, "clave-"+slug)
	}
	x, y := "fx"+sfx, "fy"+sfx
	id := itoa(ids[x])

	w := llamar(h, "GET", "/"+x+"/api/products/"+id+"/ficha", toks[x], "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"roma"`) {
		t.Fatalf("GET ficha: %d %s", w.Code, w.Body)
	}
	w = llamar(h, "PUT", "/"+x+"/api/products/"+id+"/ficha", toks[x],
		`{"atributos":{"tela":{"valor":"crepé","fuente":"diners"},"largo":{"valor":"a la rodilla","fuente":"foto"},"forro":{"valor":"sí, forrado"}},"cuidados":"lavar a mano","pendiente_tienda":["medidas por talla"]}`)
	if w.Code != 200 {
		t.Fatalf("PUT ficha: %d %s", w.Code, w.Body)
	}
	f, _ := st.ForTenant(mustTenant(t, st, x)).GetFicha(ctx, ids[x])
	if f.Atributos["tela"].Fuente != store.FuenteManual || f.Atributos["forro"].Fuente != store.FuenteManual ||
		f.Atributos["largo"].Fuente != "ambas" || f.Cuidados == nil {
		t.Fatalf("fuentes tras editar: tela %+v forro %+v largo %+v", f.Atributos["tela"], f.Atributos["forro"], f.Atributos["largo"])
	}
	// La lista de productos trae la ficha (el panel la muestra).
	if w := llamar(h, "GET", "/"+x+"/api/products", toks[x], ""); !strings.Contains(w.Body.String(), `"crepé"`) {
		t.Fatalf("lista sin ficha: %s", w.Body)
	}
	// Otra empresa: ni con su token en la ruta ajena, ni con el id ajeno en su ruta.
	if w := llamar(h, "PUT", "/"+x+"/api/products/"+id+"/ficha", toks[y], `{"atributos":{}}`); w.Code != 401 {
		t.Fatalf("token de otra empresa: %d", w.Code)
	}
	if w := llamar(h, "PUT", "/"+y+"/api/products/"+id+"/ficha", toks[y], `{"atributos":{}}`); w.Code != 404 {
		t.Fatalf("id de otra empresa: %d", w.Code)
	}
	if w := llamar(h, "PUT", "/"+x+"/api/products/"+id+"/ficha", toks[x], `{"atributos":{"precio":{"valor":"9"}}}`); w.Code != 400 {
		t.Fatalf("atributo desconocido: %d", w.Code)
	}
}

func mustTenant(t *testing.T, st *store.Store, slug string) int64 {
	t.Helper()
	tn, err := st.TenantBySlug(context.Background(), slug)
	if err != nil {
		t.Fatal(err)
	}
	return tn.ID
}
