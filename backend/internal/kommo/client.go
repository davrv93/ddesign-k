// Package kommo lleva al CRM Kommo (antes amoCRM, API v4) lo que el bot de Baruka captura en WhatsApp y en el chat
// web: un contacto por clienta, un lead por conversación en el embudo «Baruka · Ventas por WhatsApp», sus campos
// (temperatura, ocasión, fecha, talla, prenda, cita…), etiquetas y notas con los hitos.
//
// Todo corre en segundo plano (Sincronizador): un Kommo lento o caído nunca retrasa ni cambia la respuesta a la
// clienta. Documentación de la API: https://developers.kommo.com/ (límite: 7 peticiones/s; 429 al pasarse y 403 a la
// IP si se insiste).
package kommo

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand/v2"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Client habla con https://{subdominio}.kommo.com/api/v4 con un token de larga duración de una integración privada.
type Client struct {
	BaseURL string // https://{subdominio}.kommo.com (sin /api/v4)
	token   string
	HTTP    *http.Client

	// Límite de tasa propio, por debajo del de Kommo (7/s): un 429 repetido acaba en 403 para toda la IP.
	Intervalo time.Duration
	// Reintentos ante 429, 5xx y errores de red, con espera exponencial (o la de Retry-After).
	Intentos  int
	EsperaMin time.Duration
	EsperaMax time.Duration

	mu        sync.Mutex
	siguiente time.Time
}

// Nuevo arma el cliente. subdominio puede venir como «baruka», «baruka.kommo.com» o una URL completa (pruebas).
func Nuevo(subdominio, token string) *Client {
	base := strings.TrimRight(strings.TrimSpace(subdominio), "/")
	switch {
	case strings.HasPrefix(base, "http://") || strings.HasPrefix(base, "https://"):
	case strings.Contains(base, "."):
		base = "https://" + base
	default:
		base = "https://" + base + ".kommo.com"
	}
	return &Client{BaseURL: base, token: strings.TrimSpace(token), HTTP: &http.Client{Timeout: 20 * time.Second},
		Intervalo: time.Second / 6, Intentos: 5, EsperaMin: 500 * time.Millisecond, EsperaMax: 20 * time.Second}
}

// URLLead es el enlace al lead en la interfaz de Kommo.
func (c *Client) URLLead(id int64) string { return fmt.Sprintf("%s/leads/detail/%d", c.BaseURL, id) }

// ErrorAPI es una respuesta de Kommo que no es 2xx. Nunca incluye el token.
type ErrorAPI struct {
	Metodo, Ruta string
	Status       int
	Cuerpo       string
}

func (e *ErrorAPI) Error() string {
	return fmt.Sprintf("kommo: %s %s → HTTP %d: %s", e.Metodo, e.Ruta, e.Status, e.Cuerpo)
}

// reintentable: 429 (límite de tasa) y 5xx. Un 4xx distinto es un error nuestro: reintentar no lo arregla.
func reintentable(status int) bool { return status == http.StatusTooManyRequests || status >= 500 }

// esperarTurno respeta el intervalo mínimo entre peticiones (compartido por todas las goroutines).
func (c *Client) esperarTurno(ctx context.Context) error {
	c.mu.Lock()
	ahora := time.Now()
	t := c.siguiente
	if t.Before(ahora) {
		t = ahora
	}
	c.siguiente = t.Add(c.Intervalo)
	c.mu.Unlock()
	if d := time.Until(t); d > 0 {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(d):
		}
	}
	return nil
}

// do hace la petición con límite de tasa y reintentos. out puede ser nil. Devuelve el status final (204 = vacío).
func (c *Client) do(ctx context.Context, metodo, ruta string, cuerpo, out any) (int, error) {
	var raw []byte
	if cuerpo != nil {
		var err error
		if raw, err = json.Marshal(cuerpo); err != nil {
			return 0, err
		}
	}
	intentos := max(c.Intentos, 1)
	var ultimo error
	for i := 0; i < intentos; i++ {
		if i > 0 {
			if err := dormir(ctx, c.espera(i, ultimo)); err != nil {
				return 0, err
			}
		}
		if err := c.esperarTurno(ctx); err != nil {
			return 0, err
		}
		var body io.Reader
		if raw != nil {
			body = bytes.NewReader(raw)
		}
		req, err := http.NewRequestWithContext(ctx, metodo, c.BaseURL+ruta, body)
		if err != nil {
			return 0, err
		}
		req.Header.Set("Authorization", "Bearer "+c.token)
		req.Header.Set("Accept", "application/json")
		if raw != nil {
			req.Header.Set("Content-Type", "application/json")
		}
		res, err := c.HTTP.Do(req)
		if err != nil {
			if ctx.Err() != nil {
				return 0, ctx.Err()
			}
			ultimo = fmt.Errorf("kommo: %s %s: %w", metodo, ruta, quitarToken(err, c.token))
			continue
		}
		datos, _ := io.ReadAll(io.LimitReader(res.Body, 4<<20))
		res.Body.Close()
		if res.StatusCode >= 200 && res.StatusCode < 300 {
			if out != nil && res.StatusCode != http.StatusNoContent && len(bytes.TrimSpace(datos)) > 0 {
				if err := json.Unmarshal(datos, out); err != nil {
					return res.StatusCode, fmt.Errorf("kommo: %s %s: respuesta ilegible: %w", metodo, ruta, err)
				}
			}
			return res.StatusCode, nil
		}
		e := &ErrorAPI{Metodo: metodo, Ruta: ruta, Status: res.StatusCode, Cuerpo: recortar(string(datos), 300)}
		if !reintentable(res.StatusCode) {
			return res.StatusCode, e
		}
		ultimo = conEspera{e, retryAfter(res.Header.Get("Retry-After"))}
	}
	return 0, ultimo
}

// conEspera lleva el Retry-After de un 429/503 al siguiente intento.
type conEspera struct {
	error
	d time.Duration
}

func (c conEspera) Unwrap() error { return c.error }

func (c *Client) espera(intento int, ultimo error) time.Duration {
	var ce conEspera
	if errors.As(ultimo, &ce) && ce.d > 0 {
		return min(ce.d, c.EsperaMax)
	}
	d := c.EsperaMin << (intento - 1)
	if d <= 0 || d > c.EsperaMax {
		d = c.EsperaMax
	}
	return d/2 + time.Duration(rand.Int64N(int64(d/2)+1)) // con jitter: dos procesos no reintentan a la vez
}

func retryAfter(v string) time.Duration {
	if n, err := strconv.Atoi(strings.TrimSpace(v)); err == nil && n >= 0 {
		return time.Duration(n) * time.Second
	}
	return 0
}

func dormir(ctx context.Context, d time.Duration) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-time.After(d):
		return nil
	}
}

func recortar(s string, n int) string {
	s = strings.TrimSpace(s)
	if len(s) > n {
		return s[:n] + "…"
	}
	return s
}

// quitarToken: un error de red no debería traer el token, pero por si acaso.
func quitarToken(err error, token string) error {
	if token == "" || !strings.Contains(err.Error(), token) {
		return err
	}
	return errors.New(strings.ReplaceAll(err.Error(), token, "***"))
}

// ---------------------------------------------------------------------------
// Tipos de la API v4 (solo lo que usamos)

// Valor de un campo personalizado: {"value": …} o {"enum_id": …}. En los campos del sistema del contacto (PHONE) va
// además enum_code («MOB», «WORK»).
type Valor struct {
	Value    any    `json:"value,omitempty"`
	EnumID   int64  `json:"enum_id,omitempty"`
	EnumCode string `json:"enum_code,omitempty"`
}

// CampoValor es un elemento de custom_fields_values.
type CampoValor struct {
	FieldID   int64   `json:"field_id,omitempty"`
	FieldCode string  `json:"field_code,omitempty"`
	Values    []Valor `json:"values"`
}

type Etiqueta struct {
	ID   int64  `json:"id,omitempty"`
	Name string `json:"name,omitempty"`
}

type Ref struct {
	ID int64 `json:"id"`
}

type Embebidos struct {
	Tags     []Etiqueta `json:"tags,omitempty"`
	Contacts []Ref      `json:"contacts,omitempty"`
}

// Lead para crear o actualizar. Los punteros vacíos no se mandan (un PATCH solo cambia lo que trae).
type Lead struct {
	ID           int64        `json:"id,omitempty"`
	Name         string       `json:"name,omitempty"`
	Price        *int64       `json:"price,omitempty"`
	StatusID     int64        `json:"status_id,omitempty"`
	PipelineID   int64        `json:"pipeline_id,omitempty"`
	CustomFields []CampoValor `json:"custom_fields_values,omitempty"`
	Embedded     *Embebidos   `json:"_embedded,omitempty"`
	RequestID    string       `json:"request_id,omitempty"`
}

type Contacto struct {
	ID           int64        `json:"id,omitempty"`
	Name         string       `json:"name,omitempty"`
	FirstName    string       `json:"first_name,omitempty"`
	CustomFields []CampoValor `json:"custom_fields_values,omitempty"`
	Embedded     *Embebidos   `json:"_embedded,omitempty"`
	RequestID    string       `json:"request_id,omitempty"`
}

// Nota común (note_type «common») sobre un lead.
type Nota struct {
	EntityID int64      `json:"entity_id"`
	NoteType string     `json:"note_type"`
	Params   NotaParams `json:"params"`
}

type NotaParams struct {
	Text string `json:"text"`
}

// LeadLeido es un lead tal como lo devuelve GET.
type LeadLeido struct {
	ID           int64  `json:"id"`
	Name         string `json:"name"`
	Price        int64  `json:"price"`
	StatusID     int64  `json:"status_id"`
	PipelineID   int64  `json:"pipeline_id"`
	CustomFields []struct {
		FieldID int64 `json:"field_id"`
		Values  []struct {
			Value any `json:"value"`
		} `json:"values"`
	} `json:"custom_fields_values"`
	Embedded struct {
		Tags     []Etiqueta `json:"tags"`
		Contacts []Ref      `json:"contacts"`
	} `json:"_embedded"`
}

// creado es un elemento de la respuesta de un POST: el id y el request_id que mandamos (o su posición).
type creado struct {
	ID        int64  `json:"id"`
	RequestID string `json:"request_id"`
}

type ids struct {
	Embedded struct {
		Leads    []creado `json:"leads"`
		Contacts []creado `json:"contacts"`
	} `json:"_embedded"`
}

// ---------------------------------------------------------------------------
// Llamadas

// CrearContactos: POST /api/v4/contacts. Devuelve los ids en el mismo orden.
func (c *Client) CrearContactos(ctx context.Context, cs []Contacto) ([]int64, error) {
	for i := range cs {
		cs[i].RequestID = strconv.Itoa(i)
	}
	var out ids
	if _, err := c.do(ctx, http.MethodPost, "/api/v4/contacts", cs, &out); err != nil {
		return nil, err
	}
	return refs(out.Embedded.Contacts, len(cs), "contactos")
}

// ActualizarContacto: PATCH /api/v4/contacts/{id}.
func (c *Client) ActualizarContacto(ctx context.Context, ct Contacto) error {
	id := ct.ID
	ct.ID = 0
	_, err := c.do(ctx, http.MethodPatch, fmt.Sprintf("/api/v4/contacts/%d", id), ct, nil)
	return err
}

// BuscarContactos: GET /api/v4/contacts?query=… (busca en los campos llenos, p. ej. el teléfono).
func (c *Client) BuscarContactos(ctx context.Context, q string) ([]Contacto, error) {
	var out struct {
		Embedded struct {
			Contacts []Contacto `json:"contacts"`
		} `json:"_embedded"`
	}
	if _, err := c.do(ctx, http.MethodGet, "/api/v4/contacts?limit=50&query="+url.QueryEscape(q), nil, &out); err != nil {
		return nil, err
	}
	return out.Embedded.Contacts, nil
}

// CrearLeads: POST /api/v4/leads.
func (c *Client) CrearLeads(ctx context.Context, ls []Lead) ([]int64, error) {
	for i := range ls {
		ls[i].RequestID = strconv.Itoa(i)
	}
	var out ids
	if _, err := c.do(ctx, http.MethodPost, "/api/v4/leads", ls, &out); err != nil {
		return nil, err
	}
	return refs(out.Embedded.Leads, len(ls), "leads")
}

// ActualizarLead: PATCH /api/v4/leads/{id}. Ojo: si trae _embedded.tags, REEMPLAZA las etiquetas del lead.
func (c *Client) ActualizarLead(ctx context.Context, l Lead) error {
	id := l.ID
	l.ID = 0
	_, err := c.do(ctx, http.MethodPatch, fmt.Sprintf("/api/v4/leads/%d", id), l, nil)
	return err
}

// ActualizarLeads: PATCH /api/v4/leads (varios a la vez, máx. 250; Kommo recomienda ≤ 50).
func (c *Client) ActualizarLeads(ctx context.Context, ls []Lead) error {
	_, err := c.do(ctx, http.MethodPatch, "/api/v4/leads", ls, nil)
	return err
}

// Lead: GET /api/v4/leads/{id}?with=contacts.
func (c *Client) Lead(ctx context.Context, id int64) (*LeadLeido, error) {
	var l LeadLeido
	if _, err := c.do(ctx, http.MethodGet, fmt.Sprintf("/api/v4/leads/%d?with=contacts", id), nil, &l); err != nil {
		return nil, err
	}
	return &l, nil
}

// BuscarLeads: GET /api/v4/leads?query=…&filter[pipeline_id][]=… (paginado; 204 = sin resultados).
func (c *Client) BuscarLeads(ctx context.Context, q string, pipelineID int64, pagina int) ([]LeadLeido, bool, error) {
	v := url.Values{"limit": {"250"}, "page": {strconv.Itoa(max(pagina, 1))}, "with": {"contacts"}}
	if q != "" {
		v.Set("query", q)
	}
	if pipelineID > 0 {
		v.Set("filter[pipeline_id][]", strconv.FormatInt(pipelineID, 10))
	}
	var out struct {
		Links struct {
			Next *struct{} `json:"next"`
		} `json:"_links"`
		Embedded struct {
			Leads []LeadLeido `json:"leads"`
		} `json:"_embedded"`
	}
	st, err := c.do(ctx, http.MethodGet, "/api/v4/leads?"+v.Encode(), nil, &out)
	if err != nil || st == http.StatusNoContent {
		return nil, false, err
	}
	return out.Embedded.Leads, out.Links.Next != nil, nil
}

// CrearNotas: POST /api/v4/leads/notes.
func (c *Client) CrearNotas(ctx context.Context, ns []Nota) error {
	if len(ns) == 0 {
		return nil
	}
	_, err := c.do(ctx, http.MethodPost, "/api/v4/leads/notes", ns, nil)
	return err
}

// refs ordena los ids creados por request_id (lo pusimos igual a la posición); sin él, por orden de llegada.
func refs(rs []creado, n int, que string) ([]int64, error) {
	if len(rs) != n {
		return nil, fmt.Errorf("kommo: se crearon %d %s de %d", len(rs), que, n)
	}
	out := make([]int64, n)
	for i, r := range rs {
		k, err := strconv.Atoi(r.RequestID)
		if err != nil || k < 0 || k >= n || out[k] != 0 {
			k = i
		}
		out[k] = r.ID
	}
	return out, nil
}
