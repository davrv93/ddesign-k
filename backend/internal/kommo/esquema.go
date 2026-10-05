package kommo

import (
	"context"
	"fmt"
	"net/http"
	"strconv"
	"strings"
)

// Estados de sistema de Kommo: existen en todos los embudos con el mismo id.
const (
	EstadoGanado  int64 = 142
	EstadoPerdido int64 = 143
)

// Etapas del bot → estados del embudo, en orden. La etapa la decide el agente (agente/app/etapas.py) y el bot Go la
// guarda en la conversación; aquí solo se refleja.
var Etapas = []struct {
	Clave, Nombre, Color string
}{
	{"prospeccion", "Prospección", "#d6eaff"},
	{"seguimiento", "Seguimiento", "#fffeb2"},
	{"cierre", "Cierre", "#ffdc7f"},
	{"venta_confirmada", "Venta confirmada", "#87f2c0"},
}

// Nombres que se ponen a los estados de sistema al crear el embudo.
const (
	NombreGanado  = "Venta pagada"
	NombrePerdido = "Venta perdida"
)

// Claves de los campos personalizados del lead.
const (
	CTemperatura = "temperatura"
	COcasion     = "ocasion"
	CFecha       = "fecha_evento"
	CHorario     = "horario"
	CTalla       = "talla"
	CPrenda      = "prenda"
	CEnvio       = "envio"
	CCita        = "cita"
	CCanal       = "canal"
	CAnuncio     = "anuncio"
	CPedido      = "pedido"
	CConversa    = "conversacion"
	CClave       = "id_kddesign"
)

// DefCampo describe un campo personalizado del lead. Se busca por nombre: si alguien lo renombra en Kommo, se crea
// otro (y el viejo queda con su historial).
type DefCampo struct {
	Clave, Nombre, Tipo string
	Opciones            []string // select
}

// Campos que se crean en los leads (POST /api/v4/leads/custom_fields). Tipos según la API: text, select, date,
// date_time, checkbox, url. El valor de date/date_time va en Unix; checkbox, true/false; select, enum_id.
var Campos = []DefCampo{
	{CTemperatura, "Temperatura", "select", []string{"Fría", "Tibia", "Caliente"}},
	{COcasion, "Ocasión", "text", nil},
	{CFecha, "Fecha del evento", "date", nil},
	{CHorario, "Día o noche", "select", []string{"Día", "Noche"}},
	{CTalla, "Talla", "text", nil},
	{CPrenda, "Prenda en foco", "text", nil},
	{CEnvio, "Ciudad / envío", "text", nil},
	{CCita, "Cita para probarse", "date_time", nil},
	{CCanal, "Canal", "select", []string{"WhatsApp", "Web"}},
	{CAnuncio, "Llegó por anuncio", "checkbox", nil},
	{CPedido, "Pedido kddesign", "text", nil},
	{CConversa, "Conversación en kddesign", "url", nil},
	{CClave, "ID kddesign", "text", nil},
}

// Campo ya creado en Kommo.
type Campo struct {
	ID    int64            `json:"id"`
	Tipo  string           `json:"tipo"`
	Enums map[string]int64 `json:"enums,omitempty"` // opción → enum_id
}

// Esquema son los ids del embudo, sus estados y los campos en esta cuenta de Kommo.
type Esquema struct {
	PipelineID int64            `json:"pipeline_id"`
	Estados    map[string]int64 `json:"estados"` // etapa del bot → status_id (más «ganado» y «perdido»)
	Campos     map[string]Campo `json:"campos"`  // clave → campo
}

type estadoAPI struct {
	ID    int64  `json:"id,omitempty"`
	Name  string `json:"name"`
	Sort  int    `json:"sort,omitempty"`
	Color string `json:"color,omitempty"`
	Type  int    `json:"type,omitempty"`
}

type pipelineAPI struct {
	ID           int64  `json:"id,omitempty"`
	Name         string `json:"name"`
	Sort         int    `json:"sort,omitempty"`
	IsMain       bool   `json:"is_main"`
	IsUnsortedOn bool   `json:"is_unsorted_on"`
	Embedded     struct {
		Statuses []estadoAPI `json:"statuses"`
	} `json:"_embedded"`
}

type campoAPI struct {
	ID    int64  `json:"id,omitempty"`
	Name  string `json:"name"`
	Type  string `json:"type"`
	Sort  int    `json:"sort,omitempty"`
	Enums []struct {
		ID    int64  `json:"id,omitempty"`
		Value string `json:"value"`
		Sort  int    `json:"sort,omitempty"`
	} `json:"enums,omitempty"`
}

// Asegurar busca el embudo por nombre y lo crea si no existe (con sus estados); si existe, añade los estados que le
// falten. Igual con los campos del lead. Es idempotente: correrlo dos veces no duplica nada. Necesita un token de
// administrador (crear embudos y campos es solo para administradores).
func Asegurar(ctx context.Context, c *Client, nombreEmbudo string) (*Esquema, error) {
	e := &Esquema{Estados: map[string]int64{}, Campos: map[string]Campo{}}
	if err := asegurarEmbudo(ctx, c, nombreEmbudo, e); err != nil {
		return nil, err
	}
	if err := asegurarCampos(ctx, c, e); err != nil {
		return nil, err
	}
	return e, nil
}

func normal(s string) string { return strings.ToLower(strings.TrimSpace(s)) }

func asegurarEmbudo(ctx context.Context, c *Client, nombre string, e *Esquema) error {
	var lista struct {
		Embedded struct {
			Pipelines []pipelineAPI `json:"pipelines"`
		} `json:"_embedded"`
	}
	if _, err := c.do(ctx, http.MethodGet, "/api/v4/leads/pipelines", nil, &lista); err != nil {
		return err
	}
	var p *pipelineAPI
	for i := range lista.Embedded.Pipelines {
		if normal(lista.Embedded.Pipelines[i].Name) == normal(nombre) {
			p = &lista.Embedded.Pipelines[i]
			break
		}
	}
	if p == nil {
		nuevo := pipelineAPI{Name: nombre, Sort: 10 * (len(lista.Embedded.Pipelines) + 1), IsUnsortedOn: false}
		for i, et := range Etapas {
			nuevo.Embedded.Statuses = append(nuevo.Embedded.Statuses, estadoAPI{Name: et.Nombre, Sort: 10 * (i + 1), Color: et.Color})
		}
		nuevo.Embedded.Statuses = append(nuevo.Embedded.Statuses,
			estadoAPI{ID: EstadoGanado, Name: NombreGanado}, estadoAPI{ID: EstadoPerdido, Name: NombrePerdido})
		var creado struct {
			Embedded struct {
				Pipelines []pipelineAPI `json:"pipelines"`
			} `json:"_embedded"`
		}
		if _, err := c.do(ctx, http.MethodPost, "/api/v4/leads/pipelines", []pipelineAPI{nuevo}, &creado); err != nil {
			return err
		}
		if len(creado.Embedded.Pipelines) != 1 {
			return fmt.Errorf("kommo: crear el embudo %q no devolvió el embudo", nombre)
		}
		p = &creado.Embedded.Pipelines[0]
	}
	e.PipelineID = p.ID
	porNombre := map[string]int64{}
	for _, s := range p.Embedded.Statuses {
		porNombre[normal(s.Name)] = s.ID
	}
	var faltan []estadoAPI
	for i, et := range Etapas {
		if id, ok := porNombre[normal(et.Nombre)]; ok {
			e.Estados[et.Clave] = id
		} else {
			faltan = append(faltan, estadoAPI{Name: et.Nombre, Sort: 10 * (i + 1), Color: et.Color})
		}
	}
	if len(faltan) > 0 {
		var creados struct {
			Embedded struct {
				Statuses []estadoAPI `json:"statuses"`
			} `json:"_embedded"`
		}
		if _, err := c.do(ctx, http.MethodPost, "/api/v4/leads/pipelines/"+strconv.FormatInt(p.ID, 10)+"/statuses", faltan, &creados); err != nil {
			return err
		}
		for _, s := range creados.Embedded.Statuses {
			for _, et := range Etapas {
				if normal(s.Name) == normal(et.Nombre) {
					e.Estados[et.Clave] = s.ID
				}
			}
		}
	}
	for _, et := range Etapas {
		if e.Estados[et.Clave] == 0 {
			return fmt.Errorf("kommo: el embudo %q quedó sin el estado %q", nombre, et.Nombre)
		}
	}
	e.Estados["ganado"], e.Estados["perdido"] = EstadoGanado, EstadoPerdido
	return nil
}

func asegurarCampos(ctx context.Context, c *Client, e *Esquema) error {
	existentes := map[string]campoAPI{}
	for pag := 1; pag <= 20; pag++ {
		var out struct {
			Links struct {
				Next *struct{} `json:"next"`
			} `json:"_links"`
			Embedded struct {
				CustomFields []campoAPI `json:"custom_fields"`
			} `json:"_embedded"`
		}
		st, err := c.do(ctx, http.MethodGet, fmt.Sprintf("/api/v4/leads/custom_fields?limit=250&page=%d", pag), nil, &out)
		if err != nil {
			return err
		}
		for _, f := range out.Embedded.CustomFields {
			existentes[normal(f.Name)] = f
		}
		if st == http.StatusNoContent || out.Links.Next == nil {
			break
		}
	}
	var faltan []campoAPI
	var claves []string
	for _, d := range Campos {
		if f, ok := existentes[normal(d.Nombre)]; ok {
			if f.Type != d.Tipo {
				return fmt.Errorf("kommo: el campo %q ya existe con tipo %q (se esperaba %q); renómbralo en Kommo", d.Nombre, f.Type, d.Tipo)
			}
			e.Campos[d.Clave] = campoDe(f)
			continue
		}
		nuevo := campoAPI{Name: d.Nombre, Type: d.Tipo, Sort: 500 + len(faltan)}
		for i, op := range d.Opciones {
			nuevo.Enums = append(nuevo.Enums, struct {
				ID    int64  `json:"id,omitempty"`
				Value string `json:"value"`
				Sort  int    `json:"sort,omitempty"`
			}{Value: op, Sort: 10 * (i + 1)})
		}
		faltan = append(faltan, nuevo)
		claves = append(claves, d.Clave)
	}
	if len(faltan) == 0 {
		return faltanOpciones(e)
	}
	var creados struct {
		Embedded struct {
			CustomFields []campoAPI `json:"custom_fields"`
		} `json:"_embedded"`
	}
	if _, err := c.do(ctx, http.MethodPost, "/api/v4/leads/custom_fields", faltan, &creados); err != nil {
		return err
	}
	for _, f := range creados.Embedded.CustomFields {
		for i, n := range faltan {
			if normal(f.Name) == normal(n.Name) {
				e.Campos[claves[i]] = campoDe(f)
			}
		}
	}
	for _, d := range Campos {
		if e.Campos[d.Clave].ID == 0 {
			return fmt.Errorf("kommo: no se pudo crear el campo %q", d.Nombre)
		}
	}
	return faltanOpciones(e)
}

// faltanOpciones: un select creado a mano sin alguna de nuestras opciones no se puede llenar con enum_id.
func faltanOpciones(e *Esquema) error {
	for _, d := range Campos {
		for _, op := range d.Opciones {
			if e.Campos[d.Clave].Enums[normal(op)] == 0 {
				return fmt.Errorf("kommo: al campo %q le falta la opción %q; añádela en Kommo o borra el campo", d.Nombre, op)
			}
		}
	}
	return nil
}

func campoDe(f campoAPI) Campo {
	c := Campo{ID: f.ID, Tipo: f.Type}
	if len(f.Enums) > 0 {
		c.Enums = map[string]int64{}
		for _, en := range f.Enums {
			c.Enums[normal(en.Value)] = en.ID
		}
	}
	return c
}
