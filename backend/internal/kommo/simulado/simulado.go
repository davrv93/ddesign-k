// Package simulado es un Kommo (API v4) de mentira para pruebas: guarda embudos, campos, contactos, leads y notas en
// memoria y VALIDA la forma de cada petición como lo haría Kommo (cabecera Bearer, JSON, arreglos donde van
// arreglos, ids que existen, enum_id del campo, fechas en Unix, estados del embudo, límite de 7 peticiones/s). Lo
// usan las pruebas del sincronizador, del bot y del seed. No es para producción.
package simulado

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	Ganado  = 142
	Perdido = 143
)

type Estado struct {
	ID    int64  `json:"id"`
	Name  string `json:"name"`
	Sort  int    `json:"sort"`
	Color string `json:"color,omitempty"`
	Type  int    `json:"type"`
}

type Embudo struct {
	ID      int64
	Nombre  string
	Estados []Estado
}

type Enum struct {
	ID    int64  `json:"id"`
	Value string `json:"value"`
	Sort  int    `json:"sort,omitempty"`
}

type Campo struct {
	ID    int64  `json:"id"`
	Name  string `json:"name"`
	Type  string `json:"type"`
	Sort  int    `json:"sort,omitempty"`
	Enums []Enum `json:"enums,omitempty"`
}

type Valor struct {
	Value    any    `json:"value,omitempty"`
	EnumID   int64  `json:"enum_id,omitempty"`
	EnumCode string `json:"enum_code,omitempty"`
}

type CampoValor struct {
	FieldID   int64   `json:"field_id,omitempty"`
	FieldCode string  `json:"field_code,omitempty"`
	Values    []Valor `json:"values"`
}

type Lead struct {
	ID         int64
	Nombre     string
	Precio     int64
	StatusID   int64
	PipelineID int64
	Campos     map[int64][]Valor
	Tags       []string
	Contactos  []int64
	Creado     int64 // created_at (unix); las pruebas pueden fijarlo
}

type Contacto struct {
	ID       int64
	Nombre   string
	Telefono string
	Tags     []string
}

type Nota struct {
	ID       int64
	EntityID int64
	Texto    string
}

// Peticion registrada para que las pruebas revisen qué se mandó.
type Peticion struct {
	Metodo, Ruta string
	Cuerpo       string
	Status       int
	En           time.Time
}

// Kommo es el servidor simulado.
type Kommo struct {
	*httptest.Server
	Token string

	mu         sync.Mutex
	sig        int64
	Embudos    []*Embudo
	Campos     []*Campo
	Leads      map[int64]*Lead
	Contactos  map[int64]*Contacto
	Notas      []Nota
	Peticiones []Peticion
	// Violaciones del límite de tasa (más de MaxPorSegundo en una ventana de 1 s). Cada una contestó 429.
	Violaciones   int
	MaxPorSegundo int
	// Errores de validación: cada uno contestó 400. Una prueba que termina con alguno, falla.
	Errores []string
	// Fallos inyectados: las próximas n peticiones devuelven ese status (429, 503…).
	fallos      int
	fallaStatus int
	// Caido: todas las peticiones devuelven 503 (y tardan Demora).
	Caido  bool
	Demora time.Duration
	tiempo []time.Time
}

// Nuevo arranca el Kommo simulado con un embudo «Embudo» por defecto (como una cuenta nueva) y el campo de sistema
// PHONE de los contactos.
func Nuevo(token string) *Kommo {
	k := &Kommo{Token: token, sig: 1000, Leads: map[int64]*Lead{}, Contactos: map[int64]*Contacto{}, MaxPorSegundo: 7}
	k.Embudos = []*Embudo{{ID: k.id(), Nombre: "Embudo", Estados: []Estado{
		{ID: k.id(), Name: "Leads entrantes", Sort: 10, Type: 1}, {ID: k.id(), Name: "Contacto inicial", Sort: 20},
		{ID: Ganado, Name: "Logrado con éxito", Sort: 10000}, {ID: Perdido, Name: "Venta perdida", Sort: 11000}}}}
	k.Server = httptest.NewServer(http.HandlerFunc(k.servir))
	return k
}

func (k *Kommo) id() int64 { k.sig++; return k.sig }

// Fallar hace que las próximas n peticiones devuelvan status.
func (k *Kommo) Fallar(n, status int) {
	k.mu.Lock()
	k.fallos, k.fallaStatus = n, status
	k.mu.Unlock()
}

func (k *Kommo) SetCaido(v bool) { k.mu.Lock(); k.Caido = v; k.mu.Unlock() }

// Copias para las pruebas (con el candado).
func (k *Kommo) LeadsDe(pipeline int64) []Lead {
	k.mu.Lock()
	defer k.mu.Unlock()
	var out []Lead
	for _, l := range k.Leads {
		if pipeline == 0 || l.PipelineID == pipeline {
			c := *l
			out = append(out, c)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

func (k *Kommo) NotasDe(lead int64) []string {
	k.mu.Lock()
	defer k.mu.Unlock()
	var out []string
	for _, n := range k.Notas {
		if n.EntityID == lead {
			out = append(out, n.Texto)
		}
	}
	return out
}

func (k *Kommo) EmbudoPorNombre(n string) *Embudo {
	k.mu.Lock()
	defer k.mu.Unlock()
	for _, e := range k.Embudos {
		if e.Nombre == n {
			return e
		}
	}
	return nil
}

// EstadoPorNombre da el id del estado de ese embudo.
func (k *Kommo) EstadoPorNombre(e *Embudo, n string) int64 {
	for _, s := range e.Estados {
		if s.Name == n {
			return s.ID
		}
	}
	return 0
}

// CampoPorNombre devuelve el campo de lead.
func (k *Kommo) CampoPorNombre(n string) *Campo {
	k.mu.Lock()
	defer k.mu.Unlock()
	for _, c := range k.Campos {
		if c.Name == n {
			return c
		}
	}
	return nil
}

// ValorDe: el valor de un campo de un lead como texto (select → texto de la opción).
func (k *Kommo) ValorDe(l Lead, campo string) string {
	c := k.CampoPorNombre(campo)
	if c == nil {
		return ""
	}
	vs := l.Campos[c.ID]
	if len(vs) == 0 {
		return ""
	}
	if vs[0].EnumID > 0 {
		for _, e := range c.Enums {
			if e.ID == vs[0].EnumID {
				return e.Value
			}
		}
	}
	switch x := vs[0].Value.(type) {
	case float64:
		return strconv.FormatInt(int64(x), 10)
	default:
		return fmt.Sprint(x)
	}
}

// Cuenta de peticiones por método y prefijo de ruta («POST /api/v4/leads»).
func (k *Kommo) Cuenta(metodo, prefijo string) int {
	k.mu.Lock()
	defer k.mu.Unlock()
	n := 0
	for _, p := range k.Peticiones {
		if p.Metodo == metodo && strings.HasPrefix(p.Ruta, prefijo) {
			n++
		}
	}
	return n
}

// ---------------------------------------------------------------------------

type respuesta struct {
	status int
	cuerpo any
}

func (k *Kommo) servir(w http.ResponseWriter, r *http.Request) {
	cuerpo, _ := io.ReadAll(r.Body)
	k.mu.Lock()
	caido, demora := k.Caido, k.Demora
	k.mu.Unlock()
	if demora > 0 {
		time.Sleep(demora)
	}
	k.mu.Lock()
	defer k.mu.Unlock()
	ruta := r.URL.Path
	if r.URL.RawQuery != "" {
		ruta += "?" + r.URL.RawQuery
	}
	res := k.atender(r, cuerpo, caido)
	k.Peticiones = append(k.Peticiones, Peticion{Metodo: r.Method, Ruta: ruta, Cuerpo: string(cuerpo), Status: res.status, En: time.Now()})
	if res.status == 400 {
		k.Errores = append(k.Errores, fmt.Sprintf("%s %s: %v", r.Method, ruta, res.cuerpo))
	}
	if res.status == http.StatusNoContent {
		w.WriteHeader(res.status)
		return
	}
	w.Header().Set("Content-Type", "application/hal+json")
	w.WriteHeader(res.status)
	_ = json.NewEncoder(w).Encode(res.cuerpo)
}

func malo(f string, a ...any) respuesta {
	return respuesta{400, map[string]any{"title": "Bad Request", "detail": fmt.Sprintf(f, a...)}}
}

func (k *Kommo) atender(r *http.Request, cuerpo []byte, caido bool) respuesta {
	ahora := time.Now()
	// Límite de tasa: más de MaxPorSegundo en el último segundo → 429.
	var recientes []time.Time
	for _, t := range k.tiempo {
		if ahora.Sub(t) < time.Second {
			recientes = append(recientes, t)
		}
	}
	k.tiempo = append(recientes, ahora)
	if len(k.tiempo) > k.MaxPorSegundo {
		k.Violaciones++
		return respuesta{429, map[string]any{"title": "Too Many Requests"}}
	}
	if caido {
		return respuesta{503, map[string]any{"title": "Service Unavailable"}}
	}
	if k.fallos > 0 {
		k.fallos--
		return respuesta{k.fallaStatus, map[string]any{"title": http.StatusText(k.fallaStatus)}}
	}
	if r.Header.Get("Authorization") != "Bearer "+k.Token {
		return respuesta{401, map[string]any{"title": "Unauthorized"}}
	}
	if (r.Method == http.MethodPost || r.Method == http.MethodPatch) && !strings.HasPrefix(r.Header.Get("Content-Type"), "application/json") {
		return malo("Content-Type debe ser application/json")
	}
	p := strings.TrimSuffix(r.URL.Path, "/")
	q := r.URL.Query()
	partes := strings.Split(strings.TrimPrefix(p, "/api/v4/"), "/")
	switch {
	case !strings.HasPrefix(p, "/api/v4/"):
		return respuesta{404, map[string]any{"title": "Not Found"}}
	case r.Method == http.MethodGet && p == "/api/v4/leads/pipelines":
		return k.listarEmbudos()
	case r.Method == http.MethodPost && p == "/api/v4/leads/pipelines":
		return k.crearEmbudos(cuerpo)
	case r.Method == http.MethodPost && len(partes) == 4 && partes[0] == "leads" && partes[1] == "pipelines" && partes[3] == "statuses":
		return k.crearEstados(partes[2], cuerpo)
	case r.Method == http.MethodGet && p == "/api/v4/leads/custom_fields":
		return k.listarCampos(q)
	case r.Method == http.MethodPost && p == "/api/v4/leads/custom_fields":
		return k.crearCampos(cuerpo)
	case r.Method == http.MethodPost && p == "/api/v4/contacts":
		return k.crearContactos(cuerpo)
	case r.Method == http.MethodGet && p == "/api/v4/contacts":
		return k.buscarContactos(q)
	case r.Method == http.MethodGet && len(partes) == 2 && partes[0] == "contacts":
		id, _ := strconv.ParseInt(partes[1], 10, 64)
		if c := k.Contactos[id]; c != nil {
			return respuesta{200, contactoJSON(c)}
		}
		return respuesta{204, nil}
	case r.Method == http.MethodPatch && len(partes) == 2 && partes[0] == "contacts":
		return k.editarContacto(partes[1], cuerpo)
	case r.Method == http.MethodPost && p == "/api/v4/leads":
		return k.crearLeads(cuerpo)
	case r.Method == http.MethodGet && p == "/api/v4/leads":
		return k.buscarLeads(q)
	case r.Method == http.MethodPatch && p == "/api/v4/leads":
		return k.editarLeads(cuerpo)
	case r.Method == http.MethodGet && len(partes) == 2 && partes[0] == "leads":
		return k.leerLead(partes[1])
	case r.Method == http.MethodPatch && len(partes) == 2 && partes[0] == "leads":
		var l map[string]json.RawMessage
		if err := json.Unmarshal(cuerpo, &l); err != nil {
			return malo("PATCH /leads/{id} espera un objeto: %v", err)
		}
		id, _ := strconv.ParseInt(partes[1], 10, 64)
		return k.editarLead(id, l)
	case r.Method == http.MethodPost && p == "/api/v4/leads/notes":
		return k.crearNotas(cuerpo)
	}
	return respuesta{404, map[string]any{"title": "Not Found", "detail": r.Method + " " + p}}
}

func arreglo(cuerpo []byte, v any) error {
	if t := strings.TrimSpace(string(cuerpo)); !strings.HasPrefix(t, "[") {
		return fmt.Errorf("el cuerpo debe ser un arreglo JSON")
	}
	return json.Unmarshal(cuerpo, v)
}

func (k *Kommo) embudo(id int64) *Embudo {
	for _, e := range k.Embudos {
		if e.ID == id {
			return e
		}
	}
	return nil
}

func embudoJSON(e *Embudo) map[string]any {
	return map[string]any{"id": e.ID, "name": e.Nombre, "_embedded": map[string]any{"statuses": e.Estados}}
}

func (k *Kommo) listarEmbudos() respuesta {
	var ps []any
	for _, e := range k.Embudos {
		ps = append(ps, embudoJSON(e))
	}
	return respuesta{200, map[string]any{"_total_items": len(ps), "_embedded": map[string]any{"pipelines": ps}}}
}

func (k *Kommo) crearEmbudos(cuerpo []byte) respuesta {
	var in []struct {
		Name     string `json:"name"`
		Embedded struct {
			Statuses []Estado `json:"statuses"`
		} `json:"_embedded"`
	}
	if err := arreglo(cuerpo, &in); err != nil {
		return malo("%v", err)
	}
	var out []any
	for _, p := range in {
		if p.Name == "" {
			return malo("embudo sin nombre")
		}
		e := &Embudo{ID: k.id(), Nombre: p.Name}
		ganado, perdido := "Logrado con éxito", "Venta perdida"
		for _, s := range p.Embedded.Statuses {
			switch s.ID {
			case Ganado:
				ganado = s.Name
			case Perdido:
				perdido = s.Name
			case 0:
				if s.Name == "" {
					return malo("estado sin nombre")
				}
				s.ID = k.id()
				e.Estados = append(e.Estados, s)
			default:
				return malo("estado con id %d al crear", s.ID)
			}
		}
		if len(e.Estados) == 0 {
			return malo("un embudo necesita al menos un estado")
		}
		e.Estados = append(e.Estados, Estado{ID: Ganado, Name: ganado, Sort: 10000}, Estado{ID: Perdido, Name: perdido, Sort: 11000})
		k.Embudos = append(k.Embudos, e)
		out = append(out, embudoJSON(e))
	}
	return respuesta{200, map[string]any{"_embedded": map[string]any{"pipelines": out}}}
}

func (k *Kommo) crearEstados(idTxt string, cuerpo []byte) respuesta {
	id, _ := strconv.ParseInt(idTxt, 10, 64)
	e := k.embudo(id)
	if e == nil {
		return respuesta{404, map[string]any{"title": "Not Found"}}
	}
	var in []Estado
	if err := arreglo(cuerpo, &in); err != nil {
		return malo("%v", err)
	}
	var out []Estado
	for _, s := range in {
		s.ID = k.id()
		e.Estados = append(e.Estados, s)
		out = append(out, s)
	}
	return respuesta{200, map[string]any{"_embedded": map[string]any{"statuses": out}}}
}

func (k *Kommo) listarCampos(q map[string][]string) respuesta {
	if len(k.Campos) == 0 {
		return respuesta{http.StatusNoContent, nil}
	}
	return respuesta{200, map[string]any{"_page": 1, "_links": map[string]any{"self": map[string]string{"href": "/"}},
		"_embedded": map[string]any{"custom_fields": k.Campos}}}
}

var tipos = map[string]bool{"text": true, "numeric": true, "select": true, "multiselect": true, "date": true, "date_time": true,
	"checkbox": true, "url": true, "textarea": true}

func (k *Kommo) crearCampos(cuerpo []byte) respuesta {
	var in []Campo
	if err := arreglo(cuerpo, &in); err != nil {
		return malo("%v", err)
	}
	var out []*Campo
	for _, c := range in {
		if c.Name == "" || !tipos[c.Type] {
			return malo("campo %q con tipo %q inválido", c.Name, c.Type)
		}
		if (c.Type == "select" || c.Type == "multiselect") && len(c.Enums) == 0 {
			return malo("el select %q necesita enums", c.Name)
		}
		for _, x := range k.Campos {
			if x.Name == c.Name {
				return malo("campo %q duplicado", c.Name)
			}
		}
		nc := c
		nc.ID = k.id()
		for i := range nc.Enums {
			nc.Enums[i].ID = k.id()
		}
		k.Campos = append(k.Campos, &nc)
		out = append(out, &nc)
	}
	return respuesta{201, map[string]any{"_embedded": map[string]any{"custom_fields": out}}}
}

func (k *Kommo) campo(id int64) *Campo {
	for _, c := range k.Campos {
		if c.ID == id {
			return c
		}
	}
	return nil
}

// validarValores revisa cada valor contra el tipo del campo, como Kommo.
func (k *Kommo) validarValores(cvs []CampoValor) (map[int64][]Valor, error) {
	out := map[int64][]Valor{}
	for _, cv := range cvs {
		c := k.campo(cv.FieldID)
		if c == nil {
			return nil, fmt.Errorf("field_id %d no existe", cv.FieldID)
		}
		if len(cv.Values) == 0 {
			return nil, fmt.Errorf("campo %q sin values", c.Name)
		}
		for _, v := range cv.Values {
			switch c.Type {
			case "select":
				ok := false
				for _, e := range c.Enums {
					ok = ok || e.ID == v.EnumID
				}
				if !ok {
					return nil, fmt.Errorf("enum_id %d no es de %q", v.EnumID, c.Name)
				}
			case "date", "date_time":
				if n, ok := v.Value.(float64); !ok || n < 1e9 {
					return nil, fmt.Errorf("%q espera Unix timestamp, llegó %v", c.Name, v.Value)
				}
			case "checkbox":
				if _, ok := v.Value.(bool); !ok {
					return nil, fmt.Errorf("%q espera true/false, llegó %v", c.Name, v.Value)
				}
			case "text", "url", "textarea":
				s, ok := v.Value.(string)
				if !ok || s == "" {
					return nil, fmt.Errorf("%q espera texto, llegó %v", c.Name, v.Value)
				}
				if c.Type == "url" && !strings.HasPrefix(s, "http") {
					return nil, fmt.Errorf("%q espera una URL", c.Name)
				}
			}
		}
		out[cv.FieldID] = cv.Values
	}
	return out, nil
}

type tagIn struct {
	ID   int64  `json:"id"`
	Name string `json:"name"`
}

type embIn struct {
	Tags     []tagIn `json:"tags"`
	Contacts []struct {
		ID int64 `json:"id"`
	} `json:"contacts"`
}

func tagsDe(ts []tagIn) ([]string, error) {
	var out []string
	for _, t := range ts {
		if t.Name == "" {
			return nil, fmt.Errorf("etiqueta sin nombre")
		}
		out = append(out, t.Name)
	}
	return out, nil
}

func (k *Kommo) crearContactos(cuerpo []byte) respuesta {
	var in []struct {
		Name         string       `json:"name"`
		CustomFields []CampoValor `json:"custom_fields_values"`
		Embedded     embIn        `json:"_embedded"`
		RequestID    string       `json:"request_id"`
	}
	if err := arreglo(cuerpo, &in); err != nil {
		return malo("%v", err)
	}
	var out []any
	for _, c := range in {
		ct := &Contacto{ID: k.id(), Nombre: c.Name}
		for _, f := range c.CustomFields {
			if f.FieldCode != "PHONE" || len(f.Values) == 0 {
				return malo("contacto: solo se admite el campo PHONE, llegó %+v", f)
			}
			if f.Values[0].EnumCode != "MOB" && f.Values[0].EnumCode != "WORK" {
				return malo("PHONE necesita enum_code MOB o WORK")
			}
			ct.Telefono, _ = f.Values[0].Value.(string)
		}
		var err error
		if ct.Tags, err = tagsDe(c.Embedded.Tags); err != nil {
			return malo("%v", err)
		}
		k.Contactos[ct.ID] = ct
		out = append(out, map[string]any{"id": ct.ID, "request_id": c.RequestID})
	}
	return respuesta{200, map[string]any{"_embedded": map[string]any{"contacts": out}}}
}

func contactoJSON(c *Contacto) map[string]any {
	m := map[string]any{"id": c.ID, "name": c.Name()}
	if c.Telefono != "" {
		m["custom_fields_values"] = []any{map[string]any{"field_code": "PHONE", "values": []any{map[string]any{"value": c.Telefono, "enum_code": "MOB"}}}}
	}
	return m
}

func (c *Contacto) Name() string { return c.Nombre }

func (k *Kommo) buscarContactos(q map[string][]string) respuesta {
	qq := strings.Join(q["query"], "")
	var out []any
	for _, c := range k.Contactos {
		if qq != "" && (strings.Contains(strings.ReplaceAll(c.Telefono, "+", ""), qq) || strings.Contains(c.Nombre, qq)) {
			out = append(out, contactoJSON(c))
		}
	}
	if len(out) == 0 {
		return respuesta{http.StatusNoContent, nil}
	}
	return respuesta{200, map[string]any{"_embedded": map[string]any{"contacts": out}}}
}

func (k *Kommo) editarContacto(idTxt string, cuerpo []byte) respuesta {
	id, _ := strconv.ParseInt(idTxt, 10, 64)
	c := k.Contactos[id]
	if c == nil {
		return respuesta{404, map[string]any{"title": "Not Found"}}
	}
	var in struct {
		Name string `json:"name"`
	}
	if err := json.Unmarshal(cuerpo, &in); err != nil || strings.HasPrefix(strings.TrimSpace(string(cuerpo)), "[") {
		return malo("PATCH /contacts/{id} espera un objeto")
	}
	if in.Name != "" {
		c.Nombre = in.Name
	}
	return respuesta{200, map[string]any{"id": c.ID}}
}

type leadIn struct {
	ID           int64        `json:"id"`
	Name         string       `json:"name"`
	Price        *int64       `json:"price"`
	StatusID     int64        `json:"status_id"`
	PipelineID   int64        `json:"pipeline_id"`
	CustomFields []CampoValor `json:"custom_fields_values"`
	Embedded     *embIn       `json:"_embedded"`
	RequestID    string       `json:"request_id"`
}

func (k *Kommo) validarEstado(pipeline, status int64) error {
	e := k.embudo(pipeline)
	if e == nil {
		return fmt.Errorf("pipeline_id %d no existe", pipeline)
	}
	for _, s := range e.Estados {
		if s.ID == status {
			return nil
		}
	}
	return fmt.Errorf("status_id %d no es del embudo %d", status, pipeline)
}

func (k *Kommo) crearLeads(cuerpo []byte) respuesta {
	var in []leadIn
	if err := arreglo(cuerpo, &in); err != nil {
		return malo("%v", err)
	}
	if len(in) > 250 {
		return malo("más de 250 leads en una petición")
	}
	var out []any
	for _, l := range in {
		if l.StatusID > 0 {
			if err := k.validarEstado(l.PipelineID, l.StatusID); err != nil {
				return malo("%v", err)
			}
		}
		campos, err := k.validarValores(l.CustomFields)
		if err != nil {
			return malo("%v", err)
		}
		nl := &Lead{ID: k.id(), Nombre: l.Name, StatusID: l.StatusID, PipelineID: l.PipelineID, Campos: campos, Creado: time.Now().Unix()}
		if l.Price != nil {
			nl.Precio = *l.Price
		}
		if l.Embedded != nil {
			if nl.Tags, err = tagsDe(l.Embedded.Tags); err != nil {
				return malo("%v", err)
			}
			for _, c := range l.Embedded.Contacts {
				if k.Contactos[c.ID] == nil {
					return malo("contacto %d no existe", c.ID)
				}
				nl.Contactos = append(nl.Contactos, c.ID)
			}
		}
		k.Leads[nl.ID] = nl
		out = append(out, map[string]any{"id": nl.ID, "request_id": l.RequestID})
	}
	return respuesta{200, map[string]any{"_embedded": map[string]any{"leads": out}}}
}

func leadJSON(l *Lead) map[string]any {
	var cfs []any
	for id, vs := range l.Campos {
		cfs = append(cfs, map[string]any{"field_id": id, "values": vs})
	}
	tags := []any{}
	for _, t := range l.Tags {
		tags = append(tags, map[string]any{"name": t})
	}
	cs := []any{}
	for _, c := range l.Contactos {
		cs = append(cs, map[string]any{"id": c})
	}
	return map[string]any{"id": l.ID, "name": l.Nombre, "price": l.Precio, "status_id": l.StatusID, "pipeline_id": l.PipelineID,
		"created_at": l.Creado, "custom_fields_values": cfs, "_embedded": map[string]any{"tags": tags, "contacts": cs}}
}

func (k *Kommo) buscarLeads(q map[string][]string) respuesta {
	qq := strings.Join(q["query"], "")
	pipe, _ := strconv.ParseInt(strings.Join(q["filter[pipeline_id][]"], ""), 10, 64)
	limit, _ := strconv.Atoi(strings.Join(q["limit"], ""))
	if limit <= 0 || limit > 250 {
		return malo("limit entre 1 y 250")
	}
	page, _ := strconv.Atoi(strings.Join(q["page"], ""))
	page = max(page, 1)
	var todos []*Lead
	for _, l := range k.Leads {
		if pipe > 0 && l.PipelineID != pipe {
			continue
		}
		if qq != "" && !leadContiene(l, qq) {
			continue
		}
		todos = append(todos, l)
	}
	sort.Slice(todos, func(i, j int) bool { return todos[i].ID < todos[j].ID })
	ini := (page - 1) * limit
	if ini >= len(todos) {
		return respuesta{http.StatusNoContent, nil}
	}
	fin := min(ini+limit, len(todos))
	var out []any
	for _, l := range todos[ini:fin] {
		out = append(out, leadJSON(l))
	}
	links := map[string]any{"self": map[string]string{"href": "/"}}
	if fin < len(todos) {
		links["next"] = map[string]string{"href": "/?page=" + strconv.Itoa(page+1)}
	}
	return respuesta{200, map[string]any{"_page": page, "_links": links, "_embedded": map[string]any{"leads": out}}}
}

// leadContiene imita la búsqueda de Kommo: nombre y valores de los campos llenos.
func leadContiene(l *Lead, q string) bool {
	if strings.Contains(l.Nombre, q) {
		return true
	}
	for _, t := range l.Tags {
		if t == q {
			return true
		}
	}
	for _, vs := range l.Campos {
		for _, v := range vs {
			if s, ok := v.Value.(string); ok && strings.Contains(s, q) {
				return true
			}
		}
	}
	return false
}

func (k *Kommo) leerLead(idTxt string) respuesta {
	id, _ := strconv.ParseInt(idTxt, 10, 64)
	l := k.Leads[id]
	if l == nil {
		return respuesta{204, nil}
	}
	return respuesta{200, leadJSON(l)}
}

func (k *Kommo) editarLead(id int64, campos map[string]json.RawMessage) respuesta {
	l := k.Leads[id]
	if l == nil {
		return respuesta{404, map[string]any{"title": "Not Found"}}
	}
	raw, _ := json.Marshal(campos)
	var in leadIn
	if err := json.Unmarshal(raw, &in); err != nil {
		return malo("%v", err)
	}
	if in.ID != 0 && in.ID != id {
		return malo("id del cuerpo distinto del de la ruta")
	}
	if in.StatusID > 0 {
		pipe := in.PipelineID
		if pipe == 0 {
			pipe = l.PipelineID
		}
		if err := k.validarEstado(pipe, in.StatusID); err != nil {
			return malo("%v", err)
		}
		l.StatusID, l.PipelineID = in.StatusID, pipe
	}
	if in.Price != nil {
		l.Precio = *in.Price
	}
	if in.Name != "" {
		l.Nombre = in.Name
	}
	vals, err := k.validarValores(in.CustomFields)
	if err != nil {
		return malo("%v", err)
	}
	for id, v := range vals {
		l.Campos[id] = v
	}
	if in.Embedded != nil && in.Embedded.Tags != nil {
		// Como Kommo: las etiquetas que llegan REEMPLAZAN a las que había.
		if l.Tags, err = tagsDe(in.Embedded.Tags); err != nil {
			return malo("%v", err)
		}
	}
	return respuesta{200, map[string]any{"id": l.ID, "updated_at": time.Now().Unix()}}
}

func (k *Kommo) editarLeads(cuerpo []byte) respuesta {
	var in []map[string]json.RawMessage
	if err := arreglo(cuerpo, &in); err != nil {
		return malo("%v", err)
	}
	var out []any
	for _, l := range in {
		var id int64
		_ = json.Unmarshal(l["id"], &id)
		if r := k.editarLead(id, l); r.status != 200 {
			return r
		}
		out = append(out, map[string]any{"id": id})
	}
	return respuesta{200, map[string]any{"_embedded": map[string]any{"leads": out}}}
}

func (k *Kommo) crearNotas(cuerpo []byte) respuesta {
	var in []struct {
		EntityID int64  `json:"entity_id"`
		NoteType string `json:"note_type"`
		Params   struct {
			Text string `json:"text"`
		} `json:"params"`
	}
	if err := arreglo(cuerpo, &in); err != nil {
		return malo("%v", err)
	}
	var out []any
	for _, n := range in {
		if k.Leads[n.EntityID] == nil {
			return malo("lead %d no existe", n.EntityID)
		}
		if n.NoteType != "common" || strings.TrimSpace(n.Params.Text) == "" {
			return malo("nota común con texto, llegó %q", n.NoteType)
		}
		nn := Nota{ID: k.id(), EntityID: n.EntityID, Texto: n.Params.Text}
		k.Notas = append(k.Notas, nn)
		out = append(out, map[string]any{"id": nn.ID, "entity_id": nn.EntityID})
	}
	return respuesta{200, map[string]any{"_embedded": map[string]any{"notes": out}}}
}
