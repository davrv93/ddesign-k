package api

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// CRM por HTTP con dos empresas: clientas, notas, tareas, equipo, inicio y CSV. Un token de una empresa no ve ni toca
// nada de la otra, aun con sus ids; y una asesora no puede lo que es de admin.

type empresaHTTP struct {
	slug, admin, asesora string // tokens
	clienta, pedido, ases int64
}

func prepararEmpresa(t *testing.T, h http.Handler, st *store.Store, slug, nombre string) *empresaHTTP {
	t.Helper()
	ctx := context.Background()
	tn := &store.Tenant{Slug: slug, Name: nombre}
	if err := st.UpsertTenant(ctx, tn); err != nil {
		t.Fatal(err)
	}
	ts := st.ForTenant(tn.ID)
	if err := ts.UpsertUser(ctx, "admin", "clave-"+slug, "Admin "+nombre, "admin"); err != nil {
		t.Fatal(err)
	}
	p := &store.Product{Code: "V21", Name: "Vestido " + nombre, Price: 80, Active: true, Variants: []store.Variant{{Size: "M", Stock: 3}}}
	if err := ts.SaveProduct(ctx, p); err != nil {
		t.Fatal(err)
	}
	cu, _, err := ts.UpsertCustomer(ctx, "51900000009@s.whatsapp.net", "51900000009", "=HYPERLINK(\"x\") "+nombre)
	if err != nil {
		t.Fatal(err)
	}
	o := &store.Order{CustomerID: cu.ID, Status: "confirmado", Items: []store.OrderItem{{ProductID: &p.ID, VariantID: &p.Variants[0].ID,
		ProductCode: "V21", ProductName: p.Name, Size: "M", Qty: 1, UnitPrice: 80}}}
	if err := ts.CreateOrder(ctx, o); err != nil {
		t.Fatal(err)
	}
	e := &empresaHTTP{slug: slug, clienta: cu.ID, pedido: o.ID}
	e.admin = login(t, h, slug, "clave-"+slug)
	w := llamar(h, "POST", "/"+slug+"/api/equipo", e.admin, `{"username":"lucia","name":"Lucía","role":"asesora","password":"clave-lucia-`+slug+`"}`)
	if w.Code != 200 {
		t.Fatalf("crear asesora: %d %s", w.Code, w.Body)
	}
	var m store.Miembro
	_ = json.Unmarshal(w.Body.Bytes(), &m)
	e.ases = m.ID
	w = llamar(h, "POST", "/"+slug+"/api/auth/login", "", `{"user":"lucia","password":"clave-lucia-`+slug+`"}`)
	var out struct{ Token string }
	_ = json.Unmarshal(w.Body.Bytes(), &out)
	if out.Token == "" {
		t.Fatalf("login de la asesora: %d %s", w.Code, w.Body)
	}
	e.asesora = out.Token
	return e
}

func TestCRMPorHTTP(t *testing.T) {
	h, st, _ := servidorMulti(t)
	sfx := strings.ReplaceAll(time.Now().Format("150405.000000"), ".", "")
	a := prepararEmpresa(t, h, st, "ca"+sfx, "Alfa")
	b := prepararEmpresa(t, h, st, "cb"+sfx, "Beta")
	A, B := "/"+a.slug+"/api", "/"+b.slug+"/api"
	ok := func(w interface{ Result() *http.Response }, code int, que string) {
		t.Helper()
		if got := w.Result().StatusCode; got != code {
			t.Fatalf("%s: %d (esperaba %d)", que, got, code)
		}
	}

	// Quién soy.
	w := llamar(h, "GET", A+"/me", a.asesora, "")
	if !strings.Contains(w.Body.String(), `"role":"asesora"`) || !strings.Contains(w.Body.String(), `"admin":false`) {
		t.Fatalf("/me de la asesora: %s", w.Body)
	}

	// La asesora atiende: ficha, notas, tareas, etapa, etiquetas, asignar.
	ok(llamar(h, "PATCH", A+"/clientas/"+itoa(a.clienta), a.asesora,
		`{"ciudad":"Arequipa","etiquetas":["vip"],"etapa":"seguimiento","asesora_id":`+itoa(a.ases)+`}`), 200, "asesora edita la ficha")
	ok(llamar(h, "POST", A+"/notas", a.asesora, `{"clienta_id":`+itoa(a.clienta)+`,"texto":"Quiere el vestido para el 20"}`), 200, "nota")
	ok(llamar(h, "POST", A+"/notas", a.asesora, `{"pedido_id":`+itoa(a.pedido)+`,"texto":"Pagó con Yape"}`), 200, "nota del pedido")
	ok(llamar(h, "POST", A+"/tareas", a.asesora, `{"titulo":"Llamar el viernes","clienta_id":`+itoa(a.clienta)+`,"vence":"`+
		time.Now().Add(-48*time.Hour).UTC().Format(time.RFC3339)+`"}`), 200, "tarea")
	ok(llamar(h, "PATCH", A+"/orders/"+itoa(a.pedido), a.asesora, `{"asesora_id":`+itoa(a.ases)+`}`), 200, "asignar pedido")
	w = llamar(h, "GET", A+"/tareas?vista=vencidas&responsable=yo", a.asesora, "")
	var tareas []store.Tarea
	_ = json.Unmarshal(w.Body.Bytes(), &tareas)
	if len(tareas) != 1 || tareas[0].ResponsableID != a.ases {
		t.Fatalf("mis tareas vencidas: %s", w.Body)
	}
	w = llamar(h, "GET", A+"/clientas/"+itoa(a.clienta), a.asesora, "")
	var ficha struct {
		Clienta store.Clienta
		Pedidos []store.Order
		Notas   []store.Nota
		Tareas  []store.Tarea
	}
	_ = json.Unmarshal(w.Body.Bytes(), &ficha)
	if ficha.Clienta.Ciudad != "Arequipa" || ficha.Clienta.Etapa != "seguimiento" || ficha.Clienta.Asesora != "Lucía" ||
		len(ficha.Pedidos) != 1 || ficha.Pedidos[0].AsesoraID != a.ases || len(ficha.Notas) != 2 || len(ficha.Tareas) != 1 {
		t.Fatalf("ficha: %s", w.Body)
	}
	w = llamar(h, "GET", A+"/clientas/"+itoa(a.clienta)+"/actividad", a.asesora, "")
	for _, x := range []string{`"tipo":"etapa"`, `"tipo":"nota"`, `"tipo":"tarea"`, `"tipo":"asignacion"`, `"autor":"Lucía"`} {
		if !strings.Contains(w.Body.String(), x) {
			t.Fatalf("actividad sin %s: %s", x, w.Body)
		}
	}
	w = llamar(h, "GET", A+"/inicio", a.asesora, "")
	var ini struct{ Inicio store.Inicio }
	_ = json.Unmarshal(w.Body.Bytes(), &ini)
	if ini.Inicio.MisVencidas != 1 || ini.Inicio.ClientasTotal != 1 || ini.Inicio.VentasMes != 80 || ini.Inicio.Embudo["seguimiento"] != 1 {
		t.Fatalf("inicio: %s", w.Body)
	}

	// Lo que es de admin, la asesora no lo puede.
	for _, c := range [][3]string{
		{"PUT", A + "/settings", `{"bot_enabled":"false"}`},
		{"DELETE", A + "/orders/" + itoa(a.pedido), ""},
		{"GET", A + "/export/clientas.csv", ""},
		{"GET", A + "/export/pedidos.csv", ""},
		{"POST", A + "/equipo", `{"username":"otra","password":"clave-larga-x","role":"admin"}`},
		{"PATCH", A + "/equipo/" + itoa(a.ases), `{"role":"admin"}`},
		{"POST", A + "/whatsapp/logout", ""},
	} {
		ok(llamar(h, c[0], c[1], a.asesora, c[2]), 403, "asesora: "+c[0]+" "+c[1])
	}

	// CSV de admin: BOM, solo su empresa y fórmulas neutralizadas.
	w = llamar(h, "GET", A+"/export/clientas.csv", a.admin, "")
	csv := w.Body.String()
	if w.Code != 200 || !strings.HasPrefix(csv, "\xef\xbb\xbfid,nombre") || !strings.Contains(csv, `'=HYPERLINK`) || strings.Contains(csv, "Beta") ||
		!strings.Contains(csv, "Arequipa") {
		t.Fatalf("CSV de clientas: %d %q", w.Code, csv)
	}
	w = llamar(h, "GET", A+"/export/pedidos.csv", a.admin, "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), "V21 Vestido Alfa talla M x1") || strings.Contains(w.Body.String(), "Beta") ||
		!strings.Contains(w.Body.String(), "Lucía") {
		t.Fatalf("CSV de pedidos: %d %q", w.Code, w.Body)
	}

	// Aislamiento: el token de A en la ruta de B no entra; con los ids de B en la ruta de A, nada.
	ok(llamar(h, "GET", B+"/clientas", a.admin, ""), 401, "token de A en B")
	w = llamar(h, "GET", A+"/clientas", a.admin, "")
	if strings.Contains(w.Body.String(), "Beta") || !strings.Contains(w.Body.String(), "Alfa") {
		t.Fatalf("lista de A: %s", w.Body)
	}
	tb := st.ForTenant(mustTenant(t, st, b.slug))
	bt := &store.Tarea{Titulo: "de B", CustomerID: b.clienta}
	if err := tb.AddTarea(context.Background(), bt); err != nil {
		t.Fatal(err)
	}
	bn := &store.Nota{CustomerID: b.clienta, Texto: "nota de B"}
	if err := tb.AddNota(context.Background(), bn); err != nil {
		t.Fatal(err)
	}
	for _, c := range [][3]string{
		{"GET", A + "/clientas/" + itoa(b.clienta), ""},
		{"PATCH", A + "/clientas/" + itoa(b.clienta), `{"name":"pisada","etapa":"perdida"}`},
		{"GET", A + "/clientas/" + itoa(b.clienta) + "/actividad", ""},
		{"POST", A + "/notas", `{"clienta_id":` + itoa(b.clienta) + `,"texto":"intrusa"}`},
		{"POST", A + "/notas", `{"pedido_id":` + itoa(b.pedido) + `,"texto":"intrusa"}`},
		{"DELETE", A + "/notas/" + itoa(bn.ID), ""},
		{"POST", A + "/tareas", `{"titulo":"x","clienta_id":` + itoa(b.clienta) + `}`},
		{"POST", A + "/tareas", `{"titulo":"x","responsable_id":` + itoa(b.ases) + `}`},
		{"PATCH", A + "/tareas/" + itoa(bt.ID), `{"hecha":true}`},
		{"DELETE", A + "/tareas/" + itoa(bt.ID), ""},
		{"PATCH", A + "/equipo/" + itoa(b.ases), `{"active":false}`},
		{"PATCH", A + "/orders/" + itoa(b.pedido), `{"asesora_id":` + itoa(a.ases) + `}`},
		{"PATCH", A + "/orders/" + itoa(a.pedido), `{"asesora_id":` + itoa(b.ases) + `}`},
		{"PATCH", A + "/clientas/" + itoa(a.clienta), `{"asesora_id":` + itoa(b.ases) + `}`},
	} {
		ok(llamar(h, c[0], c[1], a.admin, c[2]), 404, "A con datos de B: "+c[0]+" "+c[1]+" "+c[2])
	}
	if w := llamar(h, "GET", A+"/notas?clienta="+itoa(b.clienta), a.admin, ""); strings.Contains(w.Body.String(), "nota de B") {
		t.Fatalf("A leyó las notas de B: %s", w.Body)
	}
	if w := llamar(h, "GET", A+"/tareas?vista=todas", a.admin, ""); strings.Contains(w.Body.String(), "de B") {
		t.Fatalf("A leyó las tareas de B: %s", w.Body)
	}
	if w := llamar(h, "GET", A+"/equipo", a.admin, ""); strings.Count(w.Body.String(), `"username"`) != 2 {
		t.Fatalf("equipo de A: %s", w.Body)
	}
	cb, _ := tb.GetClienta(context.Background(), b.clienta)
	if cb.Etapa != "" || strings.Contains(cb.Name, "pisada") {
		t.Fatalf("la clienta de B cambió: %+v", cb)
	}
	if bt2, _ := tb.GetTarea(context.Background(), bt.ID); bt2 == nil || bt2.Hecha {
		t.Fatalf("la tarea de B cambió: %+v", bt2)
	}
	if m, _ := tb.GetMiembro(context.Background(), b.ases); m == nil || !m.Active {
		t.Fatalf("la asesora de B cambió: %+v", m)
	}

	// Equipo: la admin no se desactiva a sí misma, y una asesora desactivada pierde la sesión al instante.
	w = llamar(h, "GET", A+"/me", a.admin, "")
	var yo struct{ Usuario Sesion }
	_ = json.Unmarshal(w.Body.Bytes(), &yo)
	ok(llamar(h, "PATCH", A+"/equipo/"+itoa(yo.Usuario.ID), a.admin, `{"active":false}`), 409, "desactivarse a sí misma")
	ok(llamar(h, "PATCH", A+"/equipo/"+itoa(yo.Usuario.ID), a.admin, `{"role":"asesora"}`), 409, "quedarse sin admin")
	ok(llamar(h, "PATCH", A+"/equipo/"+itoa(a.ases), a.admin, `{"active":false}`), 200, "desactivar asesora")
	ok(llamar(h, "GET", A+"/clientas", a.asesora, ""), 401, "asesora desactivada")
	// B no se enteró de nada.
	ok(llamar(h, "GET", B+"/clientas", b.asesora, ""), 200, "asesora de B sigue activa")
}
