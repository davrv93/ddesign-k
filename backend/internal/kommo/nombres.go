package kommo

import (
	"context"
	"encoding/json"
	"fmt"
	"slices"
	"strings"
	"time"
)

// Nombres de lead y de contacto en Kommo: legibles para quien mira el embudo, sin ids de sesión y sin el teléfono
// completo (ese va en el campo PHONE del contacto).
//
//	WhatsApp con nombre          lead «Rosa Pérez · WhatsApp»     contacto «Rosa Pérez»
//	WhatsApp con teléfono        lead «WhatsApp +•••692»          contacto «WhatsApp +•••692»
//	WhatsApp sin nada (JID LID)  lead «WhatsApp · conversación 450»
//	Chat web con nombre          lead «Lucía · Web»               contacto «Lucía»
//	Chat web sin nombre          lead «Clienta web · 05/10 14:41» (inicio de la sesión en hora de Lima)
//	Demo (kommo-seed --demo)     sin cambios: «DEMO · Ana Demo · WhatsApp», contacto «DEMO · Ana Demo»
//
// Los leads creados antes con el formato viejo («Chat web web-1a2b3c4d · chat web», «Chat web wa:450 · WhatsApp»,
// «+51987654692 · WhatsApp») se renombran solos la próxima vez que se aplica un evento suyo (y kommo-seed --desde-base
// / --renombrar). Uno que una persona renombró a mano no se toca.

// placeholderWeb: la UI de prueba de /demo-design manda siempre `cliente: "Ana"`; no es el nombre de nadie. El nombre
// real del chat web es el que la clienta dice («me llamo Lucía»), que llega en la memoria (sabemos.nombre).
var placeholderWeb = map[string]bool{"ana": true}

// clienta: el nombre de la clienta, si se sabe. En WhatsApp manda el del perfil; en la web, el que ella dijo.
func clienta(ev Evento, d datos) string {
	if ev.Canal == "web" {
		n := strings.TrimSpace(ev.Nombre)
		if placeholderWeb[strings.ToLower(n)] {
			n = ""
		}
		return firstNonEmpty(d.mem.s("nombre"), n)
	}
	return firstNonEmpty(ev.Nombre, d.mem.s("nombre"))
}

// inicioDe: inicio de la sesión (unix) o, si el canal no lo dice, el momento del evento.
func inicioDe(ev Evento) int64 {
	switch {
	case ev.Sesion > 1e12: // milisegundos
		return ev.Sesion / 1000
	case ev.Sesion > 0:
		return ev.Sesion
	case !ev.Cuando.IsZero():
		return ev.Cuando.Unix()
	}
	return time.Now().Unix()
}

// sinNombre: cómo se llama a la clienta que no dio su nombre.
func sinNombre(ev Evento, inicio int64) string {
	if ev.Canal == "web" {
		return "Clienta web · " + time.Unix(inicio, 0).In(lima).Format("02/01 15:04")
	}
	if tel := soloDigitos(ev.Telefono); len(tel) >= 3 {
		return "WhatsApp +•••" + tel[len(tel)-3:]
	}
	id := strings.TrimPrefix(ev.Clave, "wa:")
	if ev.ConversationID > 0 {
		id = fmt.Sprint(ev.ConversationID)
	}
	return "WhatsApp · conversación " + id
}

// nombreLead: el nombre automático del lead. inicio solo cuenta en el chat web sin nombre.
func nombreLead(ev Evento, d datos, inicio int64) string {
	if ev.Demo {
		return nombreLeadViejo(ev, d)
	}
	quien := clienta(ev, d)
	switch {
	case quien == "":
		return sinNombre(ev, inicio)
	case ev.Canal == "web":
		return quien + " · Web"
	}
	return quien + " · WhatsApp"
}

// nombreContacto: el nombre con que se crea el contacto (sin el canal: es la persona).
func nombreContacto(ev Evento, d datos, inicio int64) string {
	if ev.Demo {
		n := firstNonEmpty(ev.Nombre, d.mem.s("nombre"))
		if n == "" {
			n = strings.TrimSuffix(strings.TrimSuffix(strings.TrimPrefix(nombreLeadViejo(ev, d), "DEMO · "), " · WhatsApp"), " · chat web")
		}
		if !strings.HasPrefix(n, "DEMO") {
			n = "DEMO · " + n
		}
		return n
	}
	if quien := clienta(ev, d); quien != "" {
		return quien
	}
	return sinNombre(ev, inicio)
}

// nombreLeadViejo es el formato hasta el 05-10-2026, tal cual. Sigue vivo para la demo y para reconocer los nombres
// que puso kddesign (y que nadie cambió) en los leads de antes.
func nombreLeadViejo(ev Evento, d datos) string {
	quien := firstNonEmpty(ev.Nombre, d.mem.s("nombre"))
	if quien == "" && ev.Telefono != "" {
		quien = "+" + ev.Telefono
	}
	if quien == "" {
		quien = "Chat web " + corto(ev.Clave)
	}
	n := quien + " · " + canalViejo(ev.Canal)
	if ev.Demo {
		n = "DEMO · " + n
	}
	return n
}

func canalViejo(canal string) string {
	return map[string]string{"whatsapp": "WhatsApp", "web": "chat web"}[canal]
}

func corto(clave string) string {
	clave = strings.TrimPrefix(clave, "web:")
	if len(clave) > 8 {
		return clave[:8]
	}
	return clave
}

// leadsViejos: los nombres que el código viejo pudo poner a este lead. Además del que da el evento de hoy, los de sin
// nombre: la clienta pudo decir su nombre después de creado el lead, y el viejo nunca lo renombraba.
func leadsViejos(ev Evento, d datos) []string {
	c := canalViejo(ev.Canal)
	out := []string{nombreLeadViejo(ev, d), "Chat web " + corto(ev.Clave) + " · " + c}
	if ev.Telefono != "" {
		out = append(out, "+"+ev.Telefono+" · "+c)
	}
	return out
}

// contactosViejos: lo mismo para el contacto (el código viejo le ponía el nombre del lead sin « · canal»).
func contactosViejos(ev Evento) []string {
	out := []string{"Chat web " + corto(ev.Clave)}
	if ev.Telefono != "" {
		out = append(out, "+"+ev.Telefono)
	}
	return out
}

// renombrar decide si el lead existente cambia de nombre. Devuelve el nombre nuevo, o "" si se queda como está.
//
// Solo se cambia un nombre que puso kddesign: el último automático que se guardó (prev.NombreLead) o uno del formato
// viejo, idéntico. Si una persona lo cambió en Kommo, no se toca. Para saber el nombre actual hace falta leer el lead,
// pero solo cuando el nombre automático cambia (lead de antes, o la clienta dijo su nombre): leer reaprovecha la
// lectura de las etiquetas si en este turno también hace falta.
func (s *Sincronizador) renombrar(ev Evento, d datos, prev *estadoLead, leer func() (*LeadLeido, error)) (string, bool, error) {
	if ev.Demo {
		prev.NombreLead = nombreLead(ev, d, 0) // la demo conserva su nombre: nada que mirar
		return "", false, nil
	}
	inicio := prev.Inicio
	if ev.Canal == "web" && inicio == 0 {
		// Lead de antes de guardar el inicio: su fecha de creación en Kommo es la del primer turno de la sesión.
		l, err := leer()
		if err != nil {
			return "", false, err
		}
		inicio = l.CreatedAt
		if inicio == 0 {
			inicio = inicioDe(ev)
		}
		prev.Inicio = inicio
	}
	nuevo := nombreLead(ev, d, inicio)
	if nuevo == prev.NombreLead {
		return "", false, nil
	}
	l, err := leer()
	if err != nil {
		return "", false, err
	}
	nuestro := prev.NombreLead != "" && l.Name == prev.NombreLead
	viejo := slices.Contains(leadsViejos(ev, d), l.Name)
	prev.NombreLead = nuevo // decidido: no se vuelve a leer hasta que el nombre automático cambie otra vez
	if l.Name == nuevo || l.Name == "" || (!nuestro && !viejo) {
		return "", false, nil
	}
	return nuevo, viejo, nil
}

// renombrarContacto cambia el nombre automático viejo del contacto («Chat web web-1a2b3c4d», «+51987654692») por el
// nuevo. Solo se llama cuando el lead tenía el nombre viejo y la clienta sigue sin nombre (con nombre, contacto() ya
// lo pone). Un contacto que se llama distinto (lo encontró por teléfono, o una persona lo cambió) no se toca.
func (s *Sincronizador) renombrarContacto(ctx context.Context, id int64, viejos []string, nuevo string) error {
	if id == 0 {
		return nil
	}
	c, err := s.c.Contacto(ctx, id)
	if err != nil {
		return err
	}
	if c.Name == nuevo || !slices.Contains(viejos, c.Name) {
		return nil
	}
	return s.c.ActualizarContacto(ctx, Contacto{ID: id, Name: nuevo})
}

// lector lee el lead una sola vez por evento (ya: lo que se leyó antes, p. ej. al buscarlo por «ID kddesign»).
func (s *Sincronizador) lector(ctx context.Context, id int64, ya *LeadLeido) func() (*LeadLeido, error) {
	return func() (*LeadLeido, error) {
		if ya == nil {
			l, err := s.c.Lead(ctx, id)
			if err != nil {
				return nil, err
			}
			ya = l
		}
		return ya, nil
	}
}

// RenombrarWeb pone el nombre nuevo a los leads del chat web que conservan el automático viejo («Chat web web-1a2b3c4d ·
// chat web»). Hace falta aparte porque las sesiones web no viven en la SQLite: --desde-base no las recorre, y una
// sesión web cerrada no vuelve a mandar eventos. Los de WhatsApp los renombra Aplicar (kommo-seed --desde-base), que
// sabe el nombre y el teléfono. Devuelve cuántos leads cambió. Con simular, no escribe nada.
func (s *Sincronizador) RenombrarWeb(ctx context.Context, simular func(viejo, nuevo string)) (int, error) {
	e, err := s.Esquema(ctx)
	if err != nil {
		return 0, err
	}
	campo := e.Campos[CClave].ID
	type cambio struct {
		lead     Lead
		contacto int64
		clave    string
		inicio   int64
	}
	var cs []cambio
	for pag := 1; pag <= 40; pag++ {
		ls, mas, err := s.c.BuscarLeads(ctx, "Chat web", e.PipelineID, pag)
		if err != nil {
			return 0, err
		}
		for _, l := range ls {
			clave := ""
			for _, f := range l.CustomFields {
				if f.FieldID == campo && len(f.Values) > 0 {
					clave = fmt.Sprint(f.Values[0].Value)
				}
			}
			ev := Evento{Canal: "web", Clave: clave}
			if !strings.HasPrefix(clave, "web:") || l.Name != nombreLeadViejo(ev, datos{}) {
				continue // no es del chat web, o ya no lleva el nombre automático viejo (nuevo, o puesto a mano)
			}
			inicio := l.CreatedAt
			if inicio == 0 {
				inicio = time.Now().Unix()
			}
			c := cambio{lead: Lead{ID: l.ID, Name: nombreLead(ev, datos{}, inicio)}, clave: clave, inicio: inicio}
			if len(l.Embedded.Contacts) > 0 {
				c.contacto = l.Embedded.Contacts[0].ID
			}
			cs = append(cs, c)
		}
		if !mas {
			break
		}
	}
	if simular != nil {
		for _, c := range cs {
			simular("Chat web "+corto(c.clave)+" · chat web", c.lead.Name)
		}
		return len(cs), nil
	}
	for i := 0; i < len(cs); i += 50 {
		var ls []Lead
		for _, c := range cs[i:min(i+50, len(cs))] {
			ls = append(ls, c.lead)
		}
		if err := s.c.ActualizarLeads(ctx, ls); err != nil {
			return i, err
		}
	}
	for _, c := range cs {
		ev := Evento{Canal: "web", Clave: c.clave}
		if err := s.renombrarContacto(ctx, c.contacto, contactosViejos(ev), sinNombre(ev, c.inicio)); err != nil {
			return len(cs), err
		}
		// Si el vínculo sigue en la SQLite, que el próximo turno de esa sesión no vuelva a leer el lead.
		if v, err := s.st.VinculoKommo(ctx, c.clave); err == nil && v.LeadID == c.lead.ID {
			var prev estadoLead
			_ = json.Unmarshal([]byte(v.Estado), &prev)
			prev.NombreLead, prev.Inicio = c.lead.Name, c.inicio
			if err := s.guardar(ctx, v, prev); err != nil {
				return len(cs), err
			}
		}
	}
	return len(cs), nil
}
