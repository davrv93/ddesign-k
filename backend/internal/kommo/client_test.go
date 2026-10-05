package kommo

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/kommo/simulado"
)

const token = "tok-secreto-de-prueba"

// clientePrueba: el cliente contra el Kommo simulado, con esperas cortas para que las pruebas vayan rápido.
func clientePrueba(k *simulado.Kommo) *Client {
	c := Nuevo(k.URL, token)
	c.EsperaMin, c.EsperaMax = 5*time.Millisecond, 50*time.Millisecond
	return c
}

func TestNuevoArmaLaURL(t *testing.T) {
	for in, want := range map[string]string{
		"baruka":                    "https://baruka.kommo.com",
		"baruka.kommo.com":          "https://baruka.kommo.com",
		"https://baruka.kommo.com/": "https://baruka.kommo.com",
		"http://127.0.0.1:9":        "http://127.0.0.1:9",
	} {
		if got := Nuevo(in, "x").BaseURL; got != want {
			t.Errorf("%q → %q, se esperaba %q", in, got, want)
		}
	}
	if u := Nuevo("baruka", "x").URLLead(42); u != "https://baruka.kommo.com/leads/detail/42" {
		t.Errorf("enlace al lead: %s", u)
	}
}

// Cada petición lleva el token como Bearer, y el cuerpo es JSON.
func TestCabecerasYCuerpo(t *testing.T) {
	var auth, ctype atomic.Value
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		auth.Store(r.Header.Get("Authorization"))
		ctype.Store(r.Header.Get("Content-Type"))
		_, _ = w.Write([]byte(`{"_embedded":{"leads":[{"id":7,"request_id":"0"}]}}`))
	}))
	defer srv.Close()
	ids, err := Nuevo(srv.URL, token).CrearLeads(context.Background(), []Lead{{Name: "x"}})
	if err != nil || len(ids) != 1 || ids[0] != 7 {
		t.Fatalf("ids %v err %v", ids, err)
	}
	if auth.Load() != "Bearer "+token || ctype.Load() != "application/json" {
		t.Fatalf("cabeceras: %v %v", auth.Load(), ctype.Load())
	}
}

// Con 30 peticiones seguidas, el cliente no pasa nunca de 7 por segundo (Kommo contesta 429 y, si se insiste, 403 a
// toda la IP).
func TestLimiteDeTasa(t *testing.T) {
	k := simulado.Nuevo(token)
	defer k.Close()
	c := clientePrueba(k)
	inicio := time.Now()
	for i := 0; i < 30; i++ {
		if _, err := c.BuscarContactos(context.Background(), "51900000000"); err != nil {
			t.Fatal(err)
		}
	}
	if k.Violaciones != 0 {
		t.Fatalf("el cliente pasó el límite %d veces", k.Violaciones)
	}
	if d := time.Since(inicio); d < 4*time.Second {
		t.Fatalf("30 peticiones en %v: va más rápido que 6/s", d)
	}
}

// 429 y 5xx se reintentan con espera; al final la petición sale.
func TestReintentaAnte429Y5xx(t *testing.T) {
	for _, st := range []int{429, 502, 503, 504} {
		k := simulado.Nuevo(token)
		k.Fallar(2, st)
		c := clientePrueba(k)
		if _, err := c.BuscarContactos(context.Background(), "x"); err != nil {
			t.Fatalf("%d: debía reintentar y salir: %v", st, err)
		}
		if n := k.Cuenta("GET", "/api/v4/contacts"); n != 3 {
			t.Fatalf("%d: se esperaban 3 intentos, hubo %d", st, n)
		}
		k.Close()
	}
}

// Retry-After manda sobre la espera exponencial.
func TestRespetaRetryAfter(t *testing.T) {
	var n atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if n.Add(1) == 1 {
			w.Header().Set("Retry-After", "1")
			w.WriteHeader(429)
			return
		}
		w.WriteHeader(204)
	}))
	defer srv.Close()
	c := Nuevo(srv.URL, token)
	c.EsperaMin, c.EsperaMax = time.Millisecond, 5*time.Second
	inicio := time.Now()
	if _, err := c.BuscarContactos(context.Background(), "x"); err != nil {
		t.Fatal(err)
	}
	if d := time.Since(inicio); d < 900*time.Millisecond {
		t.Fatalf("no esperó el Retry-After (%v)", d)
	}
}

// Un 400 es un error nuestro: no se reintenta. Y el token nunca sale en el error.
func TestNoReintenta4xxNiMuestraElToken(t *testing.T) {
	k := simulado.Nuevo(token)
	defer k.Close()
	c := clientePrueba(k)
	err := c.CrearNotas(context.Background(), []Nota{{EntityID: 999, NoteType: "common", Params: NotaParams{Text: "x"}}})
	var e *ErrorAPI
	if !errors.As(err, &e) || e.Status != 400 {
		t.Fatalf("se esperaba un 400: %v", err)
	}
	if n := k.Cuenta("POST", "/api/v4/leads/notes"); n != 1 {
		t.Fatalf("un 400 no se reintenta; hubo %d intentos", n)
	}
	k.Errores = nil
	malo := Nuevo(k.URL, "otro-token")
	malo.EsperaMin, malo.EsperaMax = time.Millisecond, time.Millisecond
	_, err = malo.BuscarContactos(context.Background(), "x")
	if !errors.As(err, &e) || e.Status != 401 || strings.Contains(err.Error(), "otro-token") {
		t.Fatalf("token inválido → 401 sin mostrar el token: %v", err)
	}
}

// Kommo caído: el cliente se rinde tras sus intentos, sin colgarse.
func TestKommoCaidoDevuelveError(t *testing.T) {
	k := simulado.Nuevo(token)
	defer k.Close()
	k.SetCaido(true)
	c := clientePrueba(k)
	if _, err := c.BuscarContactos(context.Background(), "x"); err == nil {
		t.Fatal("con Kommo caído debía fallar")
	}
	if n := k.Cuenta("GET", "/api/v4/contacts"); n != c.Intentos {
		t.Fatalf("se esperaban %d intentos, hubo %d", c.Intentos, n)
	}
}

// El embudo, sus estados y los campos se crean una vez; la segunda pasada solo lee.
func TestAsegurarEsIdempotente(t *testing.T) {
	k := simulado.Nuevo(token)
	defer k.Close()
	c := clientePrueba(k)
	ctx := context.Background()
	e1, err := Asegurar(ctx, c, "Baruka · Ventas por WhatsApp")
	if err != nil {
		t.Fatal(err)
	}
	emb := k.EmbudoPorNombre("Baruka · Ventas por WhatsApp")
	if emb == nil || e1.PipelineID != emb.ID {
		t.Fatalf("embudo no creado: %+v", e1)
	}
	var nombres []string
	for _, s := range emb.Estados {
		nombres = append(nombres, s.Name)
	}
	if got := strings.Join(nombres, " → "); got != "Prospección → Seguimiento → Cierre → Venta confirmada → Venta pagada → Venta perdida" {
		t.Fatalf("estados: %s", got)
	}
	if len(k.Campos) != len(Campos) || e1.Campos[CTemperatura].Enums["caliente"] == 0 {
		t.Fatalf("campos: %d de %d; %+v", len(k.Campos), len(Campos), e1.Campos[CTemperatura])
	}
	posts := k.Cuenta("POST", "/api/v4/leads/")
	e2, err := Asegurar(ctx, c, "Baruka · Ventas por WhatsApp")
	if err != nil {
		t.Fatal(err)
	}
	if k.Cuenta("POST", "/api/v4/leads/") != posts || len(k.Embudos) != 2 || len(k.Campos) != len(Campos) {
		t.Fatalf("la segunda pasada no debía crear nada (embudos %d, campos %d)", len(k.Embudos), len(k.Campos))
	}
	if e2.PipelineID != e1.PipelineID || e2.Campos[CCita].ID != e1.Campos[CCita].ID || e2.Estados["cierre"] != e1.Estados["cierre"] {
		t.Fatal("la segunda pasada debía dar los mismos ids")
	}
	if len(k.Errores) > 0 {
		t.Fatalf("peticiones mal formadas: %v", k.Errores)
	}
}

// Si alguien borró un estado del embudo en Kommo, se vuelve a crear solo ese.
func TestAsegurarRecreaEstadoFaltante(t *testing.T) {
	k := simulado.Nuevo(token)
	defer k.Close()
	c := clientePrueba(k)
	ctx := context.Background()
	if _, err := Asegurar(ctx, c, "Ventas"); err != nil {
		t.Fatal(err)
	}
	emb := k.EmbudoPorNombre("Ventas")
	var quedan []simulado.Estado
	for _, s := range emb.Estados {
		if s.Name != "Cierre" {
			quedan = append(quedan, s)
		}
	}
	emb.Estados = quedan
	e, err := Asegurar(ctx, c, "Ventas")
	if err != nil {
		t.Fatal(err)
	}
	if e.Estados["cierre"] == 0 || k.Cuenta("POST", "/api/v4/leads/pipelines/") != 1 {
		t.Fatalf("debía recrear «Cierre»: %+v", e.Estados)
	}
}
