package api

// CRM del panel: clientas (lista, ficha, edición, etiquetas, etapa, asignación), notas internas, tareas, línea de
// tiempo, equipo con roles, panel de inicio y exportación a CSV. Todo por empresa (s.st(r)).

import (
	"context"
	"encoding/csv"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// ---------------------------------------------------------------------------
// Sesión: quién es y qué rol tiene. El rol se lee de la base en cada petición, así un cambio vale al instante.

type Sesion struct {
	ID       int64  `json:"id"` // 0 = el usuario del .env (despliegue de una tienda)
	Username string `json:"username"`
	Name     string `json:"name"`
	Role     string `json:"role"`
	Admin    bool   `json:"admin"`
}

// Nombre para mostrar (autor de notas, tareas y cambios).
func (u *Sesion) Nombre() string {
	if u.Name != "" {
		return u.Name
	}
	return u.Username
}

const sesionKey ctxKey = 2

func sesionDe(ctx context.Context) *Sesion {
	if u, _ := ctx.Value(sesionKey).(*Sesion); u != nil {
		return u
	}
	return &Sesion{}
}

// cargarSesion busca al usuario del token en la empresa. Desactivado o borrado = sin sesión. En el despliegue de una
// tienda, el usuario del .env (que no está en la tabla) es admin.
func (s *Server) cargarSesion(r *http.Request, username string) (*Sesion, int, error) {
	m, err := s.st(r).MiembroPorUsuario(r.Context(), username)
	switch {
	case err == nil && !m.Active:
		return nil, http.StatusUnauthorized, errors.New("tu usuario está desactivado")
	case err == nil:
		return &Sesion{ID: m.ID, Username: m.Username, Name: m.Name, Role: m.Role, Admin: m.EsAdmin()}, 0, nil
	case errors.Is(err, store.ErrNotFound) && !s.cfg.MultiTenant:
		return &Sesion{Username: username, Name: username, Role: store.RolAdmin, Admin: true}, 0, nil
	case errors.Is(err, store.ErrNotFound):
		return nil, http.StatusUnauthorized, errors.New("tu usuario ya no existe")
	default:
		return nil, http.StatusInternalServerError, err
	}
}

// soloAdmin: lo que una asesora no puede hacer (ajustes, WhatsApp, equipo, borrar, exportar).
func (s *Server) soloAdmin(h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !sesionDe(r.Context()).Admin {
			writeErr(w, http.StatusForbidden, "solo una admin puede hacer esto")
			return
		}
		h.ServeHTTP(w, r)
	})
}

func (s *Server) me(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, map[string]any{"ok": true, "usuario": sesionDe(r.Context())})
}

// errStore traduce los errores del store a HTTP.
func errStore(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, store.ErrNotFound):
		writeErr(w, 404, "no encontrado")
	case errors.Is(err, store.ErrExiste), errors.Is(err, store.ErrUltimaAdmin):
		writeErr(w, 409, err.Error())
	default:
		msg := err.Error()
		for _, p := range []string{"inválid", "vací", "necesita", "al menos", "obligatori"} {
			if strings.Contains(msg, p) {
				writeErr(w, 400, msg)
				return
			}
		}
		writeErr(w, 500, msg)
	}
}

func qInt(r *http.Request, k string) int64 {
	n, _ := strconv.ParseInt(r.URL.Query().Get(k), 10, 64)
	return n
}

// ---------------------------------------------------------------------------
// Panel de inicio

func (s *Server) inicio(w http.ResponseWriter, r *http.Request) {
	in, err := s.st(r).Inicio(r.Context(), sesionDe(r.Context()).ID)
	if err != nil {
		errStore(w, err)
		return
	}
	_, currency, _ := s.business(r)
	writeJSON(w, 200, map[string]any{"inicio": in, "etapas": store.Etapas, "moneda": currency})
}

// ---------------------------------------------------------------------------
// Clientas

func (s *Server) listClientas(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	f := store.FiltroClientas{Q: q.Get("q"), Etapa: q.Get("etapa"), Etiqueta: q.Get("etiqueta"), Compra: q.Get("compra"), Limit: int(qInt(r, "limit"))}
	switch a := q.Get("asesora"); a {
	case "":
	case "sin":
		f.Asesora = -1
	case "yo":
		f.Asesora = sesionDe(r.Context()).ID
	default:
		f.Asesora, _ = strconv.ParseInt(a, 10, 64)
	}
	cs, err := s.st(r).ListClientas(r.Context(), f)
	if err != nil {
		errStore(w, err)
		return
	}
	if cs == nil {
		cs = []*store.Clienta{}
	}
	writeJSON(w, 200, cs)
}

// crearClienta registra un contacto a mano (llegó por teléfono, Instagram o a la tienda). Si el número ya existe,
// devuelve esa clienta.
func (s *Server) crearClienta(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Name, Phone, Email, Ciudad string
	}
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	phone := strings.Map(func(r rune) rune {
		if r >= '0' && r <= '9' {
			return r
		}
		return -1
	}, in.Phone)
	if len(phone) < 8 {
		writeErr(w, 400, "teléfono inválido (incluye código de país, ej. 51987654321)")
		return
	}
	st := s.st(r)
	cu, _, err := st.UpsertCustomer(r.Context(), phone+"@s.whatsapp.net", phone, strings.TrimSpace(in.Name))
	if err != nil {
		errStore(w, err)
		return
	}
	d := store.DatosClienta{}
	if in.Email != "" {
		d.Email = &in.Email
	}
	if in.Ciudad != "" {
		d.Ciudad = &in.Ciudad
	}
	if d.Email != nil || d.Ciudad != nil {
		if err := st.UpdateClienta(r.Context(), cu.ID, d); err != nil {
			errStore(w, err)
			return
		}
	}
	c, err := st.GetClienta(r.Context(), cu.ID)
	if err != nil {
		errStore(w, err)
		return
	}
	s.pub(r, "clientas")
	writeJSON(w, 200, c)
}

func (s *Server) getClienta(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	st := s.st(r)
	c, err := st.GetClienta(r.Context(), id)
	if err != nil {
		errStore(w, err)
		return
	}
	orders, err := st.ListOrders(r.Context(), id)
	if err != nil {
		errStore(w, err)
		return
	}
	if orders == nil {
		orders = []*store.Order{}
	}
	notas, _ := st.ListNotas(r.Context(), id, 0)
	tareas, _ := st.ListTareas(r.Context(), store.FiltroTareas{Vista: "todas", CustomerID: id, Limit: 100})
	writeJSON(w, 200, map[string]any{"clienta": c, "pedidos": orders, "notas": notas, "tareas": tareas})
}

// patchClienta: datos, etiquetas, etapa (o volver a automática) y asesora, todo opcional.
func (s *Server) patchClienta(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	var in struct {
		store.DatosClienta
		Etiquetas       *[]string `json:"etiquetas"`
		Etapa           *string   `json:"etapa"`
		EtapaAutomatica bool      `json:"etapa_automatica"`
		AsesoraID       *int64    `json:"asesora_id"`
	}
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	ctx, st := r.Context(), s.st(r)
	if _, err := st.GetClienta(ctx, id); err != nil {
		errStore(w, err)
		return
	}
	if err := st.UpdateClienta(ctx, id, in.DatosClienta); err != nil {
		errStore(w, err)
		return
	}
	if in.Etiquetas != nil {
		if err := st.SetEtiquetas(ctx, id, *in.Etiquetas); err != nil {
			errStore(w, err)
			return
		}
	}
	if in.EtapaAutomatica || in.Etapa != nil {
		e := ""
		if in.Etapa != nil {
			e = *in.Etapa
		}
		if err := st.SetEtapa(ctx, id, e, in.EtapaAutomatica); err != nil {
			errStore(w, err)
			return
		}
	}
	if in.AsesoraID != nil {
		if err := st.AsignarClienta(ctx, id, *in.AsesoraID); err != nil {
			errStore(w, err)
			return
		}
	}
	c, err := st.GetClienta(ctx, id)
	if err != nil {
		errStore(w, err)
		return
	}
	s.pub(r, "clientas")
	writeJSON(w, 200, c)
}

func (s *Server) actividadClienta(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	ev, err := s.st(r).Actividad(r.Context(), id, int(qInt(r, "limit")))
	if err != nil {
		errStore(w, err)
		return
	}
	writeJSON(w, 200, ev)
}

func (s *Server) listEtiquetas(w http.ResponseWriter, r *http.Request) {
	es, err := s.st(r).Etiquetas(r.Context())
	if err != nil {
		errStore(w, err)
		return
	}
	writeJSON(w, 200, es)
}

// ---------------------------------------------------------------------------
// Notas internas

func (s *Server) listNotas(w http.ResponseWriter, r *http.Request) {
	c, o := qInt(r, "clienta"), qInt(r, "pedido")
	if c == 0 && o == 0 {
		writeErr(w, 400, "indica el cliente o el pedido")
		return
	}
	ns, err := s.st(r).ListNotas(r.Context(), c, o)
	if err != nil {
		errStore(w, err)
		return
	}
	writeJSON(w, 200, ns)
}

func (s *Server) crearNota(w http.ResponseWriter, r *http.Request) {
	var in struct {
		ClientaID int64  `json:"clienta_id"`
		PedidoID  int64  `json:"pedido_id"`
		Texto     string `json:"texto"`
	}
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	u := sesionDe(r.Context())
	n := &store.Nota{CustomerID: in.ClientaID, OrderID: in.PedidoID, Texto: in.Texto, Autor: u.Nombre(), AutorID: u.ID}
	if err := s.st(r).AddNota(r.Context(), n); err != nil {
		errStore(w, err)
		return
	}
	s.pub(r, "clientas")
	writeJSON(w, 200, n)
}

func (s *Server) borrarNota(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	u := sesionDe(r.Context())
	if err := s.st(r).DeleteNota(r.Context(), id, u.ID, u.Admin); err != nil {
		if errors.Is(err, store.ErrNotFound) {
			writeErr(w, 404, "nota no encontrada (o no es tuya)")
			return
		}
		errStore(w, err)
		return
	}
	s.pub(r, "clientas")
	writeJSON(w, 200, map[string]bool{"ok": true})
}

// ---------------------------------------------------------------------------
// Tareas

func (s *Server) listTareas(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	f := store.FiltroTareas{Vista: q.Get("vista"), CustomerID: qInt(r, "clienta"), Limit: int(qInt(r, "limit"))}
	switch resp := q.Get("responsable"); resp {
	case "yo":
		f.ResponsableID = sesionDe(r.Context()).ID
	default:
		f.ResponsableID, _ = strconv.ParseInt(resp, 10, 64)
	}
	ts, err := s.st(r).ListTareas(r.Context(), f)
	if err != nil {
		errStore(w, err)
		return
	}
	writeJSON(w, 200, ts)
}

func (s *Server) crearTarea(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Titulo        string     `json:"titulo"`
		Vence         *time.Time `json:"vence"`
		ResponsableID *int64     `json:"responsable_id"`
		ClientaID     int64      `json:"clienta_id"`
		PedidoID      int64      `json:"pedido_id"`
	}
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	u := sesionDe(r.Context())
	t := &store.Tarea{Titulo: in.Titulo, Vence: in.Vence, ResponsableID: u.ID, CustomerID: in.ClientaID, OrderID: in.PedidoID, CreadaPor: u.Nombre()}
	if in.ResponsableID != nil { // sin responsable: queda para quien la crea
		t.ResponsableID = *in.ResponsableID
	}
	st := s.st(r)
	if err := st.AddTarea(r.Context(), t); err != nil {
		errStore(w, err)
		return
	}
	saved, _ := st.GetTarea(r.Context(), t.ID)
	s.pub(r, "tareas")
	writeJSON(w, 200, saved)
}

// puedeTocarTarea: la admin, la responsable o quien la creó.
func puedeTocarTarea(u *Sesion, t *store.Tarea) bool {
	return u.Admin || (u.ID > 0 && t.ResponsableID == u.ID) || t.CreadaPor == u.Nombre()
}

func (s *Server) patchTarea(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	var in store.CambioTarea
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	st := s.st(r)
	t, err := st.GetTarea(r.Context(), id)
	if err != nil {
		errStore(w, err)
		return
	}
	// Marcarla hecha lo puede cualquiera del equipo; cambiar el resto, quien tiene que ver con ella.
	soloHecha := in.Titulo == nil && in.Vence == nil && !in.SinVence && in.ResponsableID == nil
	if !soloHecha && !puedeTocarTarea(sesionDe(r.Context()), t) {
		writeErr(w, 403, "solo la responsable, quien la creó o una admin pueden cambiar esta tarea")
		return
	}
	if err := st.UpdateTarea(r.Context(), id, in); err != nil {
		errStore(w, err)
		return
	}
	saved, _ := st.GetTarea(r.Context(), id)
	s.pub(r, "tareas")
	writeJSON(w, 200, saved)
}

func (s *Server) borrarTarea(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	st := s.st(r)
	t, err := st.GetTarea(r.Context(), id)
	if err != nil {
		errStore(w, err)
		return
	}
	if !puedeTocarTarea(sesionDe(r.Context()), t) {
		writeErr(w, 403, "solo la responsable, quien la creó o una admin pueden borrar esta tarea")
		return
	}
	if err := st.DeleteTarea(r.Context(), id); err != nil {
		errStore(w, err)
		return
	}
	s.pub(r, "tareas")
	writeJSON(w, 200, map[string]bool{"ok": true})
}

// ---------------------------------------------------------------------------
// Equipo

func (s *Server) listEquipo(w http.ResponseWriter, r *http.Request) {
	ms, err := s.st(r).ListMiembros(r.Context())
	if err != nil {
		errStore(w, err)
		return
	}
	writeJSON(w, 200, ms)
}

func (s *Server) crearMiembro(w http.ResponseWriter, r *http.Request) {
	var in struct{ Username, Name, Role, Password string }
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	if in.Role == "" {
		in.Role = store.RolAsesora
	}
	m, err := s.st(r).CrearMiembro(r.Context(), in.Username, in.Password, in.Name, in.Role)
	if err != nil {
		errStore(w, err)
		return
	}
	s.pub(r, "equipo")
	writeJSON(w, 200, m)
}

func (s *Server) patchMiembro(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	var in store.CambioMiembro
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	if u := sesionDe(r.Context()); u.ID == id && in.Active != nil && !*in.Active {
		writeErr(w, 409, "no puedes desactivarte a ti misma")
		return
	}
	m, err := s.st(r).UpdateMiembro(r.Context(), id, in)
	if err != nil {
		errStore(w, err)
		return
	}
	s.pub(r, "equipo")
	writeJSON(w, 200, m)
}

// ---------------------------------------------------------------------------
// Exportar a CSV (solo admin). UTF-8 con BOM para que Excel lea las tildes.

// celda neutraliza lo que una hoja de cálculo ejecutaría como fórmula.
func celda(v string) string {
	if v != "" && strings.ContainsRune("=+-@\t\r", rune(v[0])) {
		return "'" + v
	}
	return v
}

func fechaLima(t *time.Time) string {
	if t == nil || t.IsZero() {
		return ""
	}
	return t.In(store.Lima).Format("2006-01-02 15:04")
}

func escribirCSV(w http.ResponseWriter, nombre string, filas [][]string) {
	w.Header().Set("Content-Type", "text/csv; charset=utf-8")
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s-%s.csv"`, nombre, time.Now().In(store.Lima).Format("20060102")))
	w.Header().Set("Cache-Control", "no-store")
	_, _ = w.Write([]byte{0xEF, 0xBB, 0xBF})
	cw := csv.NewWriter(w)
	for _, f := range filas {
		for i := range f {
			f[i] = celda(f[i])
		}
		_ = cw.Write(f)
	}
	cw.Flush()
}

func monto(v float64) string { return strconv.FormatFloat(v, 'f', 2, 64) }

func (s *Server) exportClientas(w http.ResponseWriter, r *http.Request) {
	cs, err := s.st(r).ListClientas(r.Context(), store.FiltroClientas{Limit: 1000})
	if err != nil {
		errStore(w, err)
		return
	}
	filas := [][]string{{"id", "nombre", "telefono", "correo", "ciudad", "etapa", "etiquetas", "asesora", "pedidos",
		"total_comprado", "ultima_compra", "ultimo_mensaje", "alta"}}
	for _, c := range cs {
		created := c.CreatedAt
		filas = append(filas, []string{strconv.FormatInt(c.ID, 10), c.Name, c.Phone, c.Email, c.Ciudad, store.EtapaLabel[c.Etapa],
			strings.Join(c.Etiquetas, ", "), c.Asesora, strconv.Itoa(c.Pedidos), monto(c.TotalComprado), fechaLima(c.UltimaCompra),
			fechaLima(c.UltimoMensaje), fechaLima(&created)})
	}
	escribirCSV(w, "clientes", filas)
}

func (s *Server) exportPedidos(w http.ResponseWriter, r *http.Request) {
	st := s.st(r)
	pedidos, err := st.TodosLosPedidos(r.Context(), 10000)
	if err != nil {
		errStore(w, err)
		return
	}
	nombres := map[int64]string{}
	if ms, err := st.ListMiembros(r.Context()); err == nil {
		for _, m := range ms {
			nombres[m.ID] = (&Sesion{Name: m.Name, Username: m.Username}).Nombre()
		}
	}
	filas := [][]string{{"id", "fecha", "estado", "cliente", "telefono", "productos", "total", "origen", "asesora", "direccion", "notas"}}
	for _, o := range pedidos {
		var items []string
		for _, it := range o.Items {
			x := fmt.Sprintf("%s %s", it.ProductCode, it.ProductName)
			if it.Size != "" {
				x += " talla " + it.Size
			}
			items = append(items, fmt.Sprintf("%s x%d", strings.TrimSpace(x), it.Qty))
		}
		created := o.CreatedAt
		filas = append(filas, []string{strconv.FormatInt(o.ID, 10), fechaLima(&created), o.Status, o.Customer.Name, o.Customer.Phone,
			strings.Join(items, "; "), monto(o.Total), o.Source, nombres[o.AsesoraID], o.LocationText, o.Notes})
	}
	escribirCSV(w, "pedidos", filas)
}
