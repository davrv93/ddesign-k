package api

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// Fuentes, promos y embudos por HTTP (docs/FUENTES_CONTRATO.md) con dos empresas: una no ve ni toca lo de la otra aun
// con sus ids, la asesora lee pero no escribe, y lo que recibe el robot es solo lo vigente.

func subir(h http.Handler, path, token, campo, nombre string, data []byte, extra map[string]string) *httptest.ResponseRecorder {
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	fw, _ := mw.CreateFormFile(campo, nombre)
	_, _ = fw.Write(data)
	for k, v := range extra {
		_ = mw.WriteField(k, v)
	}
	_ = mw.Close()
	r := httptest.NewRequest("POST", path, &buf)
	r.Header.Set("Content-Type", mw.FormDataContentType())
	r.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func docxCon(t *testing.T, parrafos ...string) []byte {
	t.Helper()
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	f, _ := zw.Create("word/document.xml")
	var b strings.Builder
	b.WriteString(`<?xml version="1.0"?><w:document xmlns:w="x"><w:body>`)
	for _, p := range parrafos {
		b.WriteString(`<w:p><w:r><w:t>` + p + `</w:t></w:r></w:p>`)
	}
	b.WriteString(`</w:body></w:document>`)
	_, _ = f.Write([]byte(b.String()))
	_ = zw.Close()
	return buf.Bytes()
}

func TestFuentesPorHTTP(t *testing.T) {
	h, st, _ := servidorMulti(t)
	sfx := strings.ReplaceAll(time.Now().Format("150405.000000"), ".", "")
	a := prepararEmpresa(t, h, st, "fa"+sfx, "Alfa")
	b := prepararEmpresa(t, h, st, "fb"+sfx, "Beta")
	A, B := "/"+a.slug+"/api", "/"+b.slug+"/api"
	code := func(w *httptest.ResponseRecorder, want int, que string) *httptest.ResponseRecorder {
		t.Helper()
		if w.Code != want {
			t.Fatalf("%s: %d (esperaba %d) %s", que, w.Code, want, w.Body)
		}
		return w
	}
	hoy := store.HoyLima(time.Now())
	manana := store.HoyLima(time.Now().AddDate(0, 0, 1))

	// Crear una promo (admin); la asesora no puede.
	cuerpo := `{"tipo":"promo","titulo":"Pelotera","contenido":"20% en vestidos","vigente_desde":"` + hoy + `","vigente_hasta":"` + manana +
		`","datos":{"mecanica":"porcentaje","valor":20,"alcance":{"tipo":"categoria","categoria":"vestido"},"respuesta_promo":"Sí, 20%"}}`
	code(llamar(h, "POST", A+"/fuentes", a.asesora, cuerpo), 403, "asesora crea")
	w := code(llamar(h, "POST", A+"/fuentes", a.admin, cuerpo), 201, "admin crea")
	var f store.Fuente
	_ = json.Unmarshal(w.Body.Bytes(), &f)
	if f.ID == 0 || f.Estado != "borrador" {
		t.Fatalf("creada: %+v", f)
	}
	id := itoa(f.ID)
	code(llamar(h, "POST", A+"/fuentes", a.admin, `{"tipo":"promo","titulo":"x","datos":{"mecanica":"porcentaje","valor":300}}`), 400, "promo inválida")

	// Aún en borrador: el robot no la recibe.
	w = code(llamar(h, "GET", A+"/conocimiento", a.asesora, ""), 200, "conocimiento")
	var c store.Conocimiento
	_ = json.Unmarshal(w.Body.Bytes(), &c)
	if len(c.Promos) != 0 {
		t.Fatalf("una promo en borrador llegó al robot: %+v", c)
	}
	code(llamar(h, "POST", A+"/fuentes/"+id+"/estado", a.asesora, `{"estado":"activo"}`), 403, "asesora activa")
	code(llamar(h, "POST", A+"/fuentes/"+id+"/estado", a.admin, `{"estado":"activo"}`), 200, "admin activa")
	w = code(llamar(h, "GET", A+"/conocimiento", a.admin, ""), 200, "conocimiento")
	_ = json.Unmarshal(w.Body.Bytes(), &c)
	if len(c.Promos) != 1 || c.Promos[0].Valor != 20 || c.Empresa != a.slug {
		t.Fatalf("conocimiento con la promo: %+v", c)
	}

	// Editar sube la versión; la lista recorta el contenido.
	code(llamar(h, "PUT", A+"/fuentes/"+id, a.admin, strings.Replace(cuerpo, "20% en vestidos", "25% en vestidos", 1)), 200, "editar")
	w = code(llamar(h, "GET", A+"/fuentes/"+id+"/versiones", a.asesora, ""), 200, "versiones")
	if !strings.Contains(w.Body.String(), "20% en vestidos") {
		t.Fatalf("versión anterior: %s", w.Body)
	}
	w = code(llamar(h, "GET", A+"/fuentes?tipo=promo", a.asesora, ""), 200, "lista")
	if !strings.Contains(w.Body.String(), `"resumen":"25% en vestidos"`) || strings.Contains(w.Body.String(), `"contenido":"25%`) {
		t.Fatalf("lista: %s", w.Body)
	}

	// La otra empresa: nada con el id ajeno.
	code(llamar(h, "GET", B+"/fuentes/"+id, b.admin, ""), 404, "otra empresa lee")
	code(llamar(h, "PUT", B+"/fuentes/"+id, b.admin, cuerpo), 404, "otra empresa edita")
	code(llamar(h, "POST", B+"/fuentes/"+id+"/estado", b.admin, `{"estado":"archivado"}`), 404, "otra empresa archiva")
	code(llamar(h, "DELETE", B+"/fuentes/"+id, b.admin, ""), 404, "otra empresa borra")
	code(llamar(h, "GET", B+"/fuentes/"+id+"/versiones", b.admin, ""), 404, "otra empresa ve versiones")
	w = code(llamar(h, "GET", B+"/conocimiento", b.admin, ""), 200, "conocimiento de la otra")
	if strings.Contains(w.Body.String(), "Pelotera") {
		t.Fatalf("la otra empresa recibe la promo: %s", w.Body)
	}
	code(llamar(h, "GET", A+"/fuentes/"+id, b.admin, ""), 401, "token de otra empresa")

	// Embudo de la promo: la promo apunta a él; el pedido entra cuando el robot usó la promo en su conversación.
	code(llamar(h, "POST", A+"/embudos", a.asesora, `{"nombre":"Pelotera","promo_id":`+id+`}`), 403, "asesora crea embudo")
	w = code(llamar(h, "POST", A+"/embudos", a.admin, `{"nombre":"Pelotera","promo_id":`+id+`}`), 201, "embudo")
	var e store.Embudo
	_ = json.Unmarshal(w.Body.Bytes(), &e)
	if len(e.Etapas) != 4 || e.Etapas[0] != "Interesada" {
		t.Fatalf("etapas sugeridas: %+v", e)
	}
	code(llamar(h, "POST", B+"/embudos", b.admin, `{"nombre":"x","promo_id":`+id+`}`), 404, "embudo con promo ajena")
	w = code(llamar(h, "GET", A+"/fuentes/"+id, a.admin, ""), 200, "promo con embudo")
	if !strings.Contains(w.Body.String(), `"embudo_id":`+itoa(e.ID)) {
		t.Fatalf("la promo no apunta a su embudo: %s", w.Body)
	}
	ts := st.ForTenant(mustTenant(t, st, a.slug))
	_, cv, err := ts.UpsertCustomer(t.Context(), "51900000009@s.whatsapp.net", "51900000009", "") // la de prepararEmpresa
	if err != nil {
		t.Fatal(err)
	}
	cvs := []*store.Conversation{cv}
	if err := ts.AnotarUsoFuentes(t.Context(), cvs[0].ID, []int64{f.ID}); err != nil {
		t.Fatal(err)
	}
	eid := itoa(e.ID)
	w = code(llamar(h, "GET", A+"/embudos/"+eid+"/pedidos", a.asesora, ""), 200, "pedidos del embudo")
	if !strings.Contains(w.Body.String(), `"etapa":"Interesada"`) {
		t.Fatalf("pedido en el embudo: %s", w.Body)
	}
	code(llamar(h, "PUT", A+"/embudos/"+eid+"/pedidos/"+itoa(a.pedido), a.asesora, `{"etapa":"Separada"}`), 200, "mover")
	code(llamar(h, "PUT", A+"/embudos/"+eid+"/pedidos/"+itoa(a.pedido), a.asesora, `{"etapa":"Volando"}`), 400, "etapa inválida")
	code(llamar(h, "PUT", B+"/embudos/"+eid+"/pedidos/"+itoa(a.pedido), b.admin, `{"etapa":"Separada"}`), 404, "otra empresa mueve")
	code(llamar(h, "PUT", A+"/embudos/"+eid+"/pedidos/"+itoa(b.pedido), a.admin, `{"etapa":"Separada"}`), 404, "pedido ajeno")
	w = code(llamar(h, "GET", A+"/promos/resumen", a.asesora, ""), 200, "resumen")
	if !strings.Contains(w.Body.String(), `"conversaciones":1`) || !strings.Contains(w.Body.String(), `"confirmados":1`) {
		t.Fatalf("resumen: %s", w.Body)
	}
	w = code(llamar(h, "GET", A+"/conversations/"+itoa(cvs[0].ID)+"/fuentes", a.asesora, ""), 200, "fuentes de la conversación")
	if !strings.Contains(w.Body.String(), "Pelotera") {
		t.Fatalf("fuentes de la conversación: %s", w.Body)
	}
	w = code(llamar(h, "GET", B+"/conversations/"+itoa(cvs[0].ID)+"/fuentes", b.admin, ""), 200, "fuentes de conversación ajena")
	if strings.Contains(w.Body.String(), "Pelotera") {
		t.Fatalf("la otra empresa ve las fuentes: %s", w.Body)
	}
	w = code(llamar(h, "GET", A+"/embudos", a.asesora, ""), 200, "embudos")
	if !strings.Contains(w.Body.String(), `"general":true`) || !strings.Contains(w.Body.String(), "Pelotera") {
		t.Fatalf("embudos: %s", w.Body)
	}
	code(llamar(h, "DELETE", A+"/embudos/"+eid+"/pedidos/"+itoa(a.pedido), a.asesora, ""), 200, "sacar")
	code(llamar(h, "DELETE", A+"/embudos/0", a.admin, ""), 400, "borrar el general")

	// Subir material: txt y docx; formato no admitido.
	w = code(subir(h, A+"/fuentes/subir", a.admin, "archivo", "cambios.txt", []byte("Cambios hasta 7 días.\n\n\n\nCon boleta."),
		map[string]string{"tipo": "politica"}), 201, "subir txt")
	if !strings.Contains(w.Body.String(), `"contenido":"Cambios hasta 7 días.\n\nCon boleta."`) || !strings.Contains(w.Body.String(), `"estado":"borrador"`) {
		t.Fatalf("txt: %s", w.Body)
	}
	w = code(subir(h, A+"/fuentes/subir", a.admin, "archivo", "guia.docx", docxCon(t, "Paso 1", "Saluda"),
		map[string]string{"tipo": "material", "titulo": "Guía"}), 201, "subir docx")
	if !strings.Contains(w.Body.String(), `Paso 1\nSaluda`) {
		t.Fatalf("docx: %s", w.Body)
	}
	code(subir(h, A+"/fuentes/subir", a.admin, "archivo", "foto.png", []byte{1, 2, 3}, nil), 400, "png")
	code(subir(h, A+"/fuentes/subir", a.asesora, "archivo", "x.txt", []byte("x"), nil), 403, "asesora sube")

	// Propuestas: la pasada a mano solo es de admin.
	code(llamar(h, "POST", A+"/fuentes/proponer", a.asesora, ""), 403, "asesora propone")
	code(llamar(h, "POST", A+"/fuentes/proponer", a.admin, ""), 200, "admin propone")
}
