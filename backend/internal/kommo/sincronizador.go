package kommo

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"math"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// Opciones del sincronizador.
type Opciones struct {
	Embudo        string // nombre del embudo (KOMMO_PIPELINE_NAME)
	Transcripcion bool   // KOMMO_SYNC_TRANSCRIPT: el último intercambio va como nota (sin datos de pago)
	PanelURL      string // PUBLIC_URL: enlace a la conversación en el panel de kddesign
	Moneda        string
	// Producto da nombre y precio de una prenda por su código (el catálogo del backend).
	Producto func(ctx context.Context, codigo string) (nombre string, precio float64, ok bool)
	// Envios da el costo del envío por zona («lima», «provincia»). nil o vacío = sin costo de envío en el precio.
	Envios func(ctx context.Context) map[string]float64
	// Cola: eventos en espera antes de descartar (Kommo caído mucho rato). 0 = 512.
	Cola int
}

// Sincronizador recibe eventos de los dos canales y los aplica a Kommo en segundo plano, de uno en uno (el límite
// de tasa es de la cuenta). Encolar nunca bloquea: si la cola está llena, el evento se descarta con un aviso.
type Sincronizador struct {
	c  *Client
	st *store.Store
	op Opciones

	cola     chan Evento
	pend     sync.WaitGroup
	mu       sync.Mutex
	esq      *Esquema
	arranque sync.Once
}

func NuevoSincronizador(c *Client, st *store.Store, op Opciones) *Sincronizador {
	if op.Embudo == "" {
		op.Embudo = "Baruka · Ventas por WhatsApp"
	}
	if op.Moneda == "" {
		op.Moneda = "S/"
	}
	if op.Cola <= 0 {
		op.Cola = 512
	}
	return &Sincronizador{c: c, st: st, op: op, cola: make(chan Evento, op.Cola)}
}

// Cliente devuelve el cliente de Kommo (para el enlace «Ver en Kommo»).
func (s *Sincronizador) Cliente() *Client { return s.c }

// Store devuelve la SQLite donde viven los vínculos.
func (s *Sincronizador) Store() *store.Store { return s.st }

// Iniciar arranca el trabajador. Se para cuando ctx termina.
func (s *Sincronizador) Iniciar(ctx context.Context) {
	s.arranque.Do(func() { go s.trabajar(ctx) })
}

// Encolar deja el evento para el trabajador y vuelve enseguida.
func (s *Sincronizador) Encolar(ev Evento) {
	if ev.Cuando.IsZero() {
		ev.Cuando = time.Now()
	}
	s.pend.Add(1)
	select {
	case s.cola <- ev:
	default:
		s.pend.Done()
		log.Printf("[KOMMO] cola llena: se descarta el evento de %s", ev.Clave)
	}
}

// Esperar aguarda a que se apliquen los eventos en cola (pruebas y apagado).
func (s *Sincronizador) Esperar(timeout time.Duration) bool {
	listo := make(chan struct{})
	go func() { s.pend.Wait(); close(listo) }()
	select {
	case <-listo:
		return true
	case <-time.After(timeout):
		return false
	}
}

func (s *Sincronizador) trabajar(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case ev := <-s.cola:
			actx, cancel := context.WithTimeout(ctx, 3*time.Minute)
			if err := s.Aplicar(actx, ev); err != nil {
				log.Printf("[KOMMO] %s: %v", ev.Clave, err)
			}
			cancel()
			s.pend.Done()
		}
	}
}

// Esquema devuelve el embudo y los campos, creándolos la primera vez.
func (s *Sincronizador) Esquema(ctx context.Context) (*Esquema, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.esq != nil {
		return s.esq, nil
	}
	e, err := Asegurar(ctx, s.c, s.op.Embudo)
	if err != nil {
		return nil, err
	}
	s.esq = e
	if raw, err := json.Marshal(e); err == nil {
		_ = s.st.SetSetting(ctx, "kommo_esquema", string(raw)) // a la vista para diagnosticar; se rehace al arrancar
	}
	return e, nil
}

// estadoLead es lo último que se mandó al lead (vinculo.Estado): solo se envía lo que cambia.
type estadoLead struct {
	Status       int64             `json:"status,omitempty"`
	Precio       int64             `json:"precio,omitempty"`
	Campos       map[string]string `json:"campos,omitempty"`
	Tags         []string          `json:"tags,omitempty"`
	Sesion       int64             `json:"sesion,omitempty"`
	PedidoID     int64             `json:"pedido_id,omitempty"`
	PedidoEstado string            `json:"pedido_estado,omitempty"`
	Pagado       bool              `json:"pagado,omitempty"`
	Nombre       string            `json:"nombre,omitempty"` // nombre de la clienta puesto en el contacto
	Cerrado      bool              `json:"cerrado,omitempty"`
	// NombreLead es el último nombre automático decidido para el lead (vacío = lead de antes del 05-10-2026: se mira
	// una vez si conserva el nombre viejo). Inicio es el inicio de la sesión, para el nombre del chat web.
	NombreLead string `json:"nombre_lead,omitempty"`
	Inicio     int64  `json:"inicio,omitempty"`
}

// datos es lo que el evento dice del lead, ya resuelto.
type datos struct {
	mem         memoria
	codigo      string
	prenda      string // «V35 · Vestido Irla»
	precioPren  float64
	talla       string
	envio       string // lima | provincia
	ciudad      string
	temperatura string // Fría | Tibia | Caliente
	cita        string // 2026-10-09T17:00
	anuncio     bool
	anuncioTag  string
}

func (s *Sincronizador) resolver(ctx context.Context, ev Evento) datos {
	d := datos{mem: leerMemoria(ev.Memoria)}
	d.codigo = strings.ToUpper(strings.TrimSpace(ev.Producto))
	if ev.Pedido != nil && ev.Pedido.Codigo != "" {
		d.codigo = strings.ToUpper(ev.Pedido.Codigo)
	} else if d.mem.Producto != "" {
		d.codigo = strings.ToUpper(d.mem.Producto)
	}
	if d.codigo != "" {
		d.prenda = d.codigo
		if ev.Pedido != nil && ev.Pedido.Nombre != "" && strings.EqualFold(ev.Pedido.Codigo, d.codigo) {
			d.prenda += " · " + ev.Pedido.Nombre
			if ev.Pedido.Cantidad > 0 {
				d.precioPren = ev.Pedido.Total / float64(ev.Pedido.Cantidad)
			}
		}
		if s.op.Producto != nil {
			if nombre, precio, ok := s.op.Producto(ctx, d.codigo); ok {
				if !strings.Contains(d.prenda, "·") && nombre != "" {
					d.prenda += " · " + nombre
				}
				if d.precioPren == 0 {
					d.precioPren = precio
				}
			}
		}
	}
	d.talla = firstNonEmpty(func() string {
		if ev.Pedido != nil {
			return ev.Pedido.Talla
		}
		return ""
	}(), ev.Talla, d.mem.s("talla"))
	d.envio, d.ciudad = d.mem.s("envio"), d.mem.s("ciudad")
	d.temperatura = tempNombre[d.mem.Temperatura]
	d.cita = d.mem.s("cita")
	llego := strings.TrimSpace(d.mem.LlegoPor)
	d.anuncio = ev.Anuncio || strings.HasPrefix(strings.ToLower(llego), "anuncio")
	if d.anuncio {
		cod := reCodigo.FindString(ev.AnuncioTitulo)
		if cod == "" {
			cod = reCodigo.FindString(llego)
		}
		d.anuncioTag = strings.TrimSpace("anuncio " + strings.ToUpper(cod))
	}
	return d
}

func firstNonEmpty(xs ...string) string {
	for _, x := range xs {
		if strings.TrimSpace(x) != "" {
			return strings.TrimSpace(x)
		}
	}
	return ""
}

func (s *Sincronizador) dinero(v float64) string { return fmt.Sprintf("%s %.2f", s.op.Moneda, v) }

// campos: clave → valor ya en la forma de Kommo, más su texto para comparar con lo último enviado.
func (s *Sincronizador) campos(ev Evento, d datos, e *Esquema) (map[string]Valor, map[string]string) {
	vals, txt := map[string]Valor{}, map[string]string{}
	pon := func(clave string, v Valor, t string) {
		if _, ok := e.Campos[clave]; ok && t != "" {
			vals[clave], txt[clave] = v, t
		}
	}
	opcion := func(clave, op string) {
		if id := e.Campos[clave].Enums[normal(op)]; id > 0 && op != "" {
			pon(clave, Valor{EnumID: id}, op)
		}
	}
	opcion(CTemperatura, d.temperatura)
	if o := d.mem.s("ocasion"); o != "" {
		pon(COcasion, Valor{Value: o}, o)
	}
	if t, ok := parseFecha(d.mem.s("fecha_iso")); ok {
		pon(CFecha, Valor{Value: t.Unix()}, d.mem.s("fecha_iso"))
	}
	opcion(CHorario, horarioNombre[d.mem.s("horario")])
	if d.talla != "" {
		pon(CTalla, Valor{Value: strings.ToUpper(d.talla)}, strings.ToUpper(d.talla))
	}
	if d.prenda != "" {
		pon(CPrenda, Valor{Value: d.prenda}, d.prenda)
	}
	if env := textoEnvio(d.envio, d.ciudad); env != "" {
		pon(CEnvio, Valor{Value: env}, env)
	}
	if t, ok := parseCita(d.cita); ok {
		pon(CCita, Valor{Value: t.Unix()}, d.cita)
	}
	canal := map[string]string{"whatsapp": "WhatsApp", "web": "Web"}[ev.Canal]
	opcion(CCanal, canal)
	pon(CAnuncio, Valor{Value: d.anuncio}, fmt.Sprint(d.anuncio))
	if p := ev.Pedido; p != nil && p.ID > 0 {
		t := fmt.Sprintf("#%d · %s · %s", p.ID, p.Estado, s.dinero(p.Total))
		pon(CPedido, Valor{Value: t}, t)
	}
	if ev.ConversationID > 0 && s.op.PanelURL != "" {
		u := fmt.Sprintf("%s/conversaciones/?c=%d", strings.TrimRight(s.op.PanelURL, "/"), ev.ConversationID)
		pon(CConversa, Valor{Value: u}, u)
	}
	pon(CClave, Valor{Value: ev.Clave}, ev.Clave)
	return vals, txt
}

func textoEnvio(envio, ciudad string) string {
	switch {
	case envio == "lima":
		return strings.TrimSpace("Lima " + titulo(ciudad))
	case envio == "provincia" && ciudad != "":
		return "Provincia · " + titulo(ciudad)
	case envio == "provincia":
		return "Provincia"
	case ciudad != "":
		return titulo(ciudad)
	}
	return ""
}

func titulo(s string) string {
	ps := strings.Fields(strings.ToLower(s))
	for i, p := range ps {
		r := []rune(p)
		r[0] = []rune(strings.ToUpper(string(r[0])))[0]
		ps[i] = string(r)
	}
	return strings.Join(ps, " ")
}

// etiquetas que maneja kddesign (las demás, puestas a mano en Kommo, se respetan).
func gestionada(t string) bool {
	t = normal(t)
	return t == "whatsapp" || t == "web" || t == "kddesign" || t == "fría" || t == "tibia" || t == "caliente" ||
		strings.HasPrefix(t, "anuncio")
}

func (s *Sincronizador) etiquetas(ev Evento, d datos) []string {
	tags := []string{"kddesign", ev.Canal}
	if d.temperatura != "" {
		tags = append(tags, strings.ToLower(d.temperatura))
	}
	if d.anuncioTag != "" {
		tags = append(tags, d.anuncioTag)
	}
	if ev.Demo {
		tags = append(tags, "demo")
	}
	return tags
}

// estadoObjetivo: a qué estado del embudo va el lead. La etapa del agente manda entre los estados abiertos; el pedido
// manda por encima (confirmado = venta confirmada; comprobante o pedido en preparación/enviado/entregado = ganado;
// cancelado después de confirmado = perdido). «Venta confirmada» no vuelve atrás sola: si la clienta se arrepiente con
// el stock ya descontado, lo decide una persona (el bot deja la nota en el pedido).
func estadoObjetivo(ev Evento, prev estadoLead, e *Esquema) int64 {
	etapa := ev.Etapa
	if _, ok := e.Estados[etapa]; !ok || etapa == "ganado" || etapa == "perdido" {
		etapa = ""
	}
	obj := e.Estados[etapa]
	if obj == 0 {
		obj = prev.Status
	}
	if obj == 0 {
		obj = e.Estados["prospeccion"]
	}
	vc := e.Estados["venta_confirmada"]
	if p := ev.Pedido; p != nil {
		switch {
		case p.Estado == "cancelado" && (confirmados[prev.PedidoEstado] || prev.Status == vc):
			return EstadoPerdido
		case p.Estado == "preparando" || p.Estado == "enviado" || p.Estado == "entregado":
			return EstadoGanado
		case p.Estado == "confirmado":
			obj = vc
		}
	}
	if ev.Pagado && (obj == vc || prev.Status == vc) {
		return EstadoGanado
	}
	if prev.Status == vc && obj != EstadoGanado && obj != EstadoPerdido {
		return vc
	}
	return obj
}

// Aplicar lleva un evento a Kommo: contacto, lead (crear o actualizar solo lo que cambió) y notas. Es síncrono; el
// bot lo usa a través de Encolar.
func (s *Sincronizador) Aplicar(ctx context.Context, ev Evento) error {
	if ev.Clave == "" {
		return errors.New("evento sin clave")
	}
	e, err := s.Esquema(ctx)
	if err != nil {
		return err
	}
	v, err := s.st.VinculoKommo(ctx, ev.Clave)
	if err != nil && !errors.Is(err, store.ErrNotFound) {
		return err
	}
	var prev estadoLead
	if v != nil {
		_ = json.Unmarshal([]byte(v.Estado), &prev)
	} else {
		v = &store.VinculoKommo{Clave: ev.Clave, Canal: ev.Canal, ConversationID: ev.ConversationID}
	}
	d := s.resolver(ctx, ev)

	// Lead cerrado (ganado o perdido): una sesión nueva o un pedido nuevo es otra venta → otro lead para el mismo
	// contacto. Si no, solo se anotan los hitos en el lead cerrado.
	if v.LeadID > 0 && prev.Cerrado {
		nuevaVenta := (ev.Sesion > prev.Sesion && ev.Etapa != "" && ev.Etapa != "venta_confirmada") ||
			(ev.Pedido != nil && ev.Pedido.ID > 0 && ev.Pedido.ID != prev.PedidoID && !confirmados[ev.Pedido.Estado] && ev.Pedido.Estado != "cancelado")
		if !nuevaVenta {
			// El lead cerrado no se mueve, pero un nombre automático viejo sí se cambia por el nuevo.
			if err := s.ponerNombre(ctx, ev, d, v, &prev, s.lector(ctx, v.LeadID, nil), nil); err != nil {
				return err
			}
			if err := s.notas(ctx, v.LeadID, s.hitos(ev, d, prev, prev.Status, e), ev); err != nil {
				return err
			}
			prev.Pagado = prev.Pagado || ev.Pagado
			prev.Sesion = max(prev.Sesion, ev.Sesion)
			if p := ev.Pedido; p != nil && p.ID == prev.PedidoID {
				prev.PedidoEstado = p.Estado
			}
			// El lead cerrado no se toca, pero lo visto ya se anotó: que no se repita en la nota siguiente.
			_, txt := s.campos(ev, d, e)
			if prev.Campos == nil {
				prev.Campos = map[string]string{}
			}
			for k, t := range txt {
				prev.Campos[k] = t
			}
			return s.guardar(ctx, v, prev)
		}
		v.LeadID, prev = 0, estadoLead{Nombre: prev.Nombre}
	}

	inicio := prev.Inicio
	if inicio == 0 {
		inicio = inicioDe(ev)
	}
	if err := s.contacto(ctx, ev, d, v, &prev, inicio); err != nil {
		return err
	}
	status := estadoObjetivo(ev, prev, e)
	precio := s.precio(ctx, ev, d, status, e)
	vals, txt := s.campos(ev, d, e)
	tags := s.etiquetas(ev, d)
	hitos := s.hitos(ev, d, prev, status, e)

	var leido *LeadLeido // el lead tal como está en Kommo, si ya se leyó
	if v.LeadID == 0 {
		if l := s.leadPorClave(ctx, ev.Clave, e); l != nil {
			v.LeadID, leido = l.ID, l
			if v.ContactID == 0 && len(l.Embedded.Contacts) > 0 {
				v.ContactID = l.Embedded.Contacts[0].ID
			}
		}
	}
	if v.LeadID == 0 {
		prev.NombreLead = nombreLead(ev, d, inicio)
		if ev.Canal == "web" {
			prev.Inicio = inicio
		}
		l := Lead{Name: prev.NombreLead, Price: &precio, StatusID: status, PipelineID: e.PipelineID,
			CustomFields: valores(vals, e), Embedded: &Embebidos{Tags: nombres(tags)}}
		if v.ContactID > 0 {
			l.Embedded.Contacts = []Ref{{ID: v.ContactID}}
		}
		ids, err := s.c.CrearLeads(ctx, []Lead{l})
		if err != nil {
			return err
		}
		v.LeadID = ids[0]
	} else {
		cambio := Lead{ID: v.LeadID}
		hay := false
		leer := s.lector(ctx, v.LeadID, leido)
		if err := s.ponerNombre(ctx, ev, d, v, &prev, leer, &cambio); err != nil {
			return err
		}
		hay = cambio.Name != ""
		if status != prev.Status {
			cambio.StatusID, cambio.PipelineID, hay = status, e.PipelineID, true
		}
		if precio != prev.Precio {
			cambio.Price, hay = &precio, true
		}
		var cambiados []string
		for k, t := range txt {
			if prev.Campos[k] != t {
				cambiados = append(cambiados, k)
			}
		}
		if len(cambiados) > 0 {
			sub := map[string]Valor{}
			for _, k := range cambiados {
				sub[k] = vals[k]
			}
			cambio.CustomFields, hay = valores(sub, e), true
		}
		if !slices.Equal(tags, prev.Tags) {
			// PATCH con _embedded.tags reemplaza todas: se conservan las que puso una persona en Kommo.
			final := nombres(tags)
			if actual, err := leer(); err == nil {
				for _, t := range actual.Embedded.Tags {
					if !gestionada(t.Name) && !slices.Contains(tags, t.Name) {
						final = append(final, Etiqueta{Name: t.Name})
					}
				}
			} else {
				return err
			}
			cambio.Embedded, hay = &Embebidos{Tags: final}, true
		}
		if hay {
			if err := s.c.ActualizarLead(ctx, cambio); err != nil {
				return err
			}
		}
	}

	nuevo := estadoLead{Status: status, Precio: precio, Campos: txt, Tags: tags, Sesion: max(ev.Sesion, prev.Sesion),
		Pagado: prev.Pagado || ev.Pagado, Nombre: prev.Nombre, PedidoID: prev.PedidoID, PedidoEstado: prev.PedidoEstado,
		Cerrado: status == EstadoGanado || status == EstadoPerdido, NombreLead: prev.NombreLead, Inicio: prev.Inicio}
	if ev.Pedido != nil && ev.Pedido.ID > 0 {
		nuevo.PedidoID, nuevo.PedidoEstado = ev.Pedido.ID, ev.Pedido.Estado
	}
	if err := s.guardar(ctx, v, nuevo); err != nil {
		return err
	}
	return s.notas(ctx, v.LeadID, hitos, ev)
}

func (s *Sincronizador) guardar(ctx context.Context, v *store.VinculoKommo, e estadoLead) error {
	raw, _ := json.Marshal(e)
	v.Estado = string(raw)
	return s.st.GuardarVinculoKommo(ctx, v)
}

// ponerNombre: si el nombre automático del lead cambió y el que tiene en Kommo es nuestro (el último automático o el del
// formato viejo), lo cambia. Con cambio, el nombre viaja en el PATCH que ya se iba a mandar; sin él, va solo. Si el
// nombre viejo era el de sin nombre, el contacto creado con él también se renombra.
func (s *Sincronizador) ponerNombre(ctx context.Context, ev Evento, d datos, v *store.VinculoKommo, prev *estadoLead,
	leer func() (*LeadLeido, error), cambio *Lead) error {
	nuevo, viejo, err := s.renombrar(ev, d, prev, leer)
	if err != nil || nuevo == "" {
		return err
	}
	if cambio != nil {
		cambio.Name = nuevo
	} else if err := s.c.ActualizarLead(ctx, Lead{ID: v.LeadID, Name: nuevo}); err != nil {
		return err
	}
	if viejo && clienta(ev, d) == "" {
		return s.renombrarContacto(ctx, v.ContactID, contactosViejos(ev), nombreContacto(ev, d, prev.Inicio))
	}
	return nil
}

// precio del lead: la prenda (o el total del pedido) y, con la venta confirmada, el envío si se sabe a dónde va.
func (s *Sincronizador) precio(ctx context.Context, ev Evento, d datos, status int64, e *Esquema) int64 {
	base := d.precioPren
	if p := ev.Pedido; p != nil && p.Total > 0 && p.Estado != "consulta" {
		base = p.Total
	}
	if status == e.Estados["venta_confirmada"] || status == EstadoGanado {
		costo := ev.EnvioCosto
		if costo == 0 && d.envio != "" && s.op.Envios != nil {
			costo = s.op.Envios(ctx)[d.envio]
		}
		if base > 0 {
			base += costo
		}
	}
	return int64(math.Round(base))
}

// contacto crea o reutiliza el contacto de la clienta. WhatsApp: se busca por teléfono antes de crear (una clienta,
// un contacto, aunque escriba en varias sesiones). Chat web: un contacto por sesión, sin teléfono.
func (s *Sincronizador) contacto(ctx context.Context, ev Evento, d datos, v *store.VinculoKommo, prev *estadoLead, inicio int64) error {
	nombre := clienta(ev, d)
	if v.ContactID > 0 {
		if nombre != "" && nombre != prev.Nombre {
			if err := s.c.ActualizarContacto(ctx, Contacto{ID: v.ContactID, Name: nombre}); err != nil {
				return err
			}
			prev.Nombre = nombre
		}
		return nil
	}
	if ev.Telefono != "" {
		encontrados, err := s.c.BuscarContactos(ctx, ev.Telefono)
		if err != nil {
			return err
		}
		for _, c := range encontrados {
			if tieneTelefono(c, ev.Telefono) {
				v.ContactID = c.ID
				break
			}
		}
	}
	if v.ContactID > 0 {
		if nombre != "" {
			prev.Nombre = nombre // ya existía: no se le cambia el nombre que tenga en Kommo
		}
		return nil
	}
	ct := Contacto{Name: nombreContacto(ev, d, inicio), Embedded: &Embebidos{Tags: nombres([]string{"kddesign", ev.Canal})}}
	if ev.Demo {
		ct.Embedded.Tags = append(ct.Embedded.Tags, Etiqueta{Name: "demo"})
	}
	if ev.Telefono != "" {
		ct.CustomFields = []CampoValor{{FieldCode: "PHONE", Values: []Valor{{Value: "+" + ev.Telefono, EnumCode: "MOB"}}}}
	}
	ids, err := s.c.CrearContactos(ctx, []Contacto{ct})
	if err != nil {
		return err
	}
	v.ContactID, prev.Nombre = ids[0], nombre
	// Se guarda ya: si lo que sigue falla, el reintento no crea otro contacto.
	return s.guardar(ctx, v, *prev)
}

func soloDigitos(s string) string {
	return strings.Map(func(r rune) rune {
		if r >= '0' && r <= '9' {
			return r
		}
		return -1
	}, s)
}

func tieneTelefono(c Contacto, tel string) bool {
	tel = soloDigitos(tel)
	if len(tel) > 9 {
		tel = tel[len(tel)-9:]
	}
	for _, f := range c.CustomFields {
		for _, v := range f.Values {
			if s, ok := v.Value.(string); ok && tel != "" && strings.HasSuffix(soloDigitos(s), tel) {
				return true
			}
		}
	}
	return false
}

// leadPorClave: si se perdió el vínculo local (base restaurada, volcado repetido), el lead abierto se encuentra por el
// campo «ID kddesign» antes de crear otro.
func (s *Sincronizador) leadPorClave(ctx context.Context, clave string, e *Esquema) *LeadLeido {
	campo := e.Campos[CClave].ID
	ls, _, err := s.c.BuscarLeads(ctx, clave, e.PipelineID, 1)
	if err != nil {
		return nil
	}
	for _, l := range ls {
		if l.StatusID == EstadoGanado || l.StatusID == EstadoPerdido {
			continue // un lead cerrado es una venta anterior: no se reabre
		}
		for _, f := range l.CustomFields {
			if f.FieldID != campo {
				continue
			}
			for _, v := range f.Values {
				if fmt.Sprint(v.Value) == clave {
					return &l
				}
			}
		}
	}
	return nil
}

func nombres(tags []string) []Etiqueta {
	out := make([]Etiqueta, 0, len(tags))
	for _, t := range tags {
		out = append(out, Etiqueta{Name: t})
	}
	return out
}

// valores en el orden de Campos (estable: las pruebas y los diffs lo agradecen).
func valores(vals map[string]Valor, e *Esquema) []CampoValor {
	var out []CampoValor
	for _, d := range Campos {
		if v, ok := vals[d.Clave]; ok {
			out = append(out, CampoValor{FieldID: e.Campos[d.Clave].ID, Values: []Valor{v}})
		}
	}
	return out
}

// hitos: lo que pasó en este turno, en frases para la asesora. Los explícitos los manda el canal (comprobante,
// asesora, foto…); los demás salen de comparar con lo último enviado (prenda, cita, pedido, temperatura).
func (s *Sincronizador) hitos(ev Evento, d datos, prev estadoLead, status int64, e *Esquema) []string {
	var out []string
	if f, ok := hitoIntencion[ev.Intencion]; ok {
		del := ""
		if strings.Contains(f, "%s") && d.codigo != "" {
			del = " del " + d.codigo
		}
		if strings.Contains(f, "%s") {
			f = fmt.Sprintf(f, del)
		}
		out = append(out, "💬 "+f)
	}
	if len(ev.Mostrados) > 0 {
		var ps []string
		for _, c := range ev.Mostrados {
			n := strings.ToUpper(c)
			if s.op.Producto != nil {
				if nombre, _, ok := s.op.Producto(context.Background(), n); ok && nombre != "" {
					n += " " + nombre
				}
			}
			ps = append(ps, n)
		}
		out = append(out, "📸 Se le mostró: "+strings.Join(ps, ", "))
	}
	if d.prenda != "" && prev.Campos[CPrenda] != d.prenda {
		t := "👗 Prenda en foco: " + d.prenda
		if d.precioPren > 0 {
			t += " (" + s.dinero(d.precioPren) + ")"
		}
		out = append(out, t)
	}
	// La temperatura se anota cuando cambia; la «fría» de arranque (sin datos) no dice nada.
	if d.temperatura != "" && prev.Campos[CTemperatura] != d.temperatura && (prev.Campos[CTemperatura] != "" || d.temperatura != "Fría") {
		t := "🌡️ Clienta " + strings.ToLower(d.temperatura)
		if d.mem.Motivo != "" {
			t += ": " + d.mem.Motivo
		}
		out = append(out, t)
	}
	if t, ok := parseCita(d.cita); ok && prev.Campos[CCita] != d.cita {
		c := "🗓️ Cita para probarse agendada el " + cuandoCorto(t)
		if d.codigo != "" {
			c += " (" + d.codigo
			if d.talla != "" {
				c += " talla " + strings.ToUpper(d.talla)
			}
			c += ")"
		}
		out = append(out, c)
	}
	out = append(out, ev.Hitos...)
	if p := ev.Pedido; p != nil && p.ID > 0 && (p.ID != prev.PedidoID || p.Estado != prev.PedidoEstado) && p.Estado != "consulta" {
		det := ""
		if p.Codigo != "" {
			det = fmt.Sprintf(": %s talla %s ×%d", p.Codigo, firstNonEmpty(p.Talla, "—"), max(p.Cantidad, 1))
		}
		switch p.Estado {
		case "pendiente":
			out = append(out, fmt.Sprintf("🧾 Resumen del pedido #%d%s · %s (falta su SI)", p.ID, det, s.dinero(p.Total)))
		case "confirmado":
			out = append(out, fmt.Sprintf("✅ Pedido #%d confirmado%s · %s", p.ID, det, s.dinero(p.Total)))
		case "cancelado":
			out = append(out, fmt.Sprintf("❌ Pedido #%d cancelado", p.ID))
		default:
			out = append(out, fmt.Sprintf("📦 Pedido #%d: %s", p.ID, p.Estado))
		}
	}
	if ev.Pagado && !prev.Pagado {
		t := "💳 Comprobante de pago recibido"
		if ev.Pedido != nil && ev.Pedido.ID > 0 {
			t += fmt.Sprintf(" (pedido #%d)", ev.Pedido.ID)
		}
		out = append(out, t+". Falta validarlo.")
	}
	if ev.Pedido == nil && status == e.Estados["venta_confirmada"] && prev.Status != status {
		t := "✅ Venta confirmada en el chat"
		if d.prenda != "" {
			t += ": " + d.prenda
			if d.talla != "" {
				t += " talla " + strings.ToUpper(d.talla)
			}
		}
		out = append(out, t)
	}
	return out
}

// notas: un turno = como mucho una petición (los hitos juntos en una nota y, si se pidió, la transcripción en otra).
func (s *Sincronizador) notas(ctx context.Context, lead int64, hitos []string, ev Evento) error {
	var ns []Nota
	if len(hitos) > 0 {
		ns = append(ns, Nota{EntityID: lead, NoteType: "common", Params: NotaParams{Text: strings.Join(hitos, "\n")}})
	}
	if s.op.Transcripcion && len(ev.Turno) > 0 {
		var b strings.Builder
		b.WriteString("💬 " + map[string]string{"whatsapp": "WhatsApp", "web": "Chat web"}[ev.Canal] + "\n")
		for _, l := range ev.Turno {
			quien := map[string]string{"cliente": "Clienta", "bot": "Bot", "asesora": "Asesora"}[l.Rol]
			if quien == "" {
				quien = l.Rol
			}
			t := Limpiar(strings.TrimSpace(l.Texto))
			if len([]rune(t)) > 1000 {
				t = string([]rune(t)[:1000]) + "…"
			}
			if t != "" {
				b.WriteString(quien + ": " + t + "\n")
			}
		}
		ns = append(ns, Nota{EntityID: lead, NoteType: "common", Params: NotaParams{Text: strings.TrimSpace(b.String())}})
	}
	return s.c.CrearNotas(ctx, ns)
}
