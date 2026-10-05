package kommo

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"

	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// Plan es lo que Aplicar haría con un evento, sin llamar a Kommo (kommo-seed --dry-run).
type Plan struct {
	Clave   string
	Accion  string // crear | actualizar | solo notas (lead cerrado)
	Lead    string
	Estado  string
	Precio  int64
	Campos  map[string]string
	Tags    []string
	Hitos   []string
	Contact string
}

// esquemaDeMentira tiene ids ficticios: alcanza para calcular estado, campos y precio sin Kommo.
func esquemaDeMentira() *Esquema {
	e := &Esquema{PipelineID: 1, Estados: map[string]int64{"ganado": EstadoGanado, "perdido": EstadoPerdido}, Campos: map[string]Campo{}}
	for i, et := range Etapas {
		e.Estados[et.Clave] = int64(10 + i)
	}
	for i, d := range Campos {
		c := Campo{ID: int64(100 + i), Tipo: d.Tipo}
		if len(d.Opciones) > 0 {
			c.Enums = map[string]int64{}
			for j, op := range d.Opciones {
				c.Enums[normal(op)] = int64(1000*(i+1) + j)
			}
		}
		e.Campos[d.Clave] = c
	}
	return e
}

func nombreEstado(id int64, e *Esquema) string {
	switch id {
	case EstadoGanado:
		return NombreGanado
	case EstadoPerdido:
		return NombrePerdido
	}
	for _, et := range Etapas {
		if e.Estados[et.Clave] == id {
			return et.Nombre
		}
	}
	return fmt.Sprint(id)
}

// Simular calcula el plan de un evento con lo que hay en el vínculo local.
func (s *Sincronizador) Simular(ctx context.Context, ev Evento) Plan {
	e := esquemaDeMentira()
	var prev estadoLead
	accion := "crear"
	if v, err := s.st.VinculoKommo(ctx, ev.Clave); err == nil {
		_ = json.Unmarshal([]byte(v.Estado), &prev)
		if v.LeadID > 0 {
			accion = fmt.Sprintf("actualizar lead %d", v.LeadID)
			if prev.Cerrado {
				accion = fmt.Sprintf("solo notas (lead %d cerrado)", v.LeadID)
			}
		}
	} else if err != store.ErrNotFound {
		accion = "error: " + err.Error()
	}
	d := s.resolver(ctx, ev)
	st := estadoObjetivo(ev, prev, e)
	_, txt := s.campos(ev, d, e)
	contacto := "nuevo (sin teléfono)"
	if ev.Telefono != "" {
		contacto = "buscar por teléfono +" + enmascarar(ev.Telefono) + " o crear"
	}
	return Plan{Clave: ev.Clave, Accion: accion, Lead: s.nombreLead(ev, d), Estado: nombreEstado(st, e), Precio: s.precio(ctx, ev, d, st, e),
		Campos: txt, Tags: s.etiquetas(ev, d), Hitos: s.hitos(ev, d, prev, st, e), Contact: contacto}
}

// enmascarar deja ver solo los 3 últimos dígitos (la salida del dry-run puede ir a un log o a un chat).
func enmascarar(tel string) string {
	if len(tel) <= 3 {
		return tel
	}
	return strings.Repeat("•", len(tel)-3) + tel[len(tel)-3:]
}

// Texto legible del plan.
func (p Plan) String() string {
	var b strings.Builder
	fmt.Fprintf(&b, "%s → %s · «%s» · %s · %s\n", p.Clave, p.Accion, p.Lead, p.Estado, fmt.Sprintf("precio %d", p.Precio))
	fmt.Fprintf(&b, "   contacto: %s · etiquetas: %s\n", p.Contact, strings.Join(p.Tags, ", "))
	var ks []string
	for k := range p.Campos {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	for _, k := range ks {
		fmt.Fprintf(&b, "   %s = %s\n", k, p.Campos[k])
	}
	for _, h := range p.Hitos {
		fmt.Fprintf(&b, "   nota: %s\n", strings.ReplaceAll(h, "\n", " / "))
	}
	return b.String()
}
