package store

// CRM básico sobre la tabla customers: ficha de la clienta, etiquetas, embudo (etapa), asignación a una persona del
// equipo, notas internas, tareas y la línea de tiempo. Como todo el Store, cada consulta filtra por la empresa
// (tenant_id) y los ids de otra empresa no encuentran nada.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"
	"unicode/utf8"
)

// Etapas del embudo, en orden. Las cuatro primeras son las del agente (agente/app/etapas.py); «perdida» solo la pone
// una persona.
var Etapas = []string{"prospeccion", "seguimiento", "cierre", "venta_confirmada", "perdida"}

func ValidEtapa(e string) bool {
	for _, x := range Etapas {
		if x == e {
			return true
		}
	}
	return false
}

// EstadoLabel: los estados del pedido como los nombra el panel (frontend/src/lib/format.ts).
var EstadoLabel = map[string]string{
	"consulta": "Consulta", "pendiente": "Por confirmar", "confirmado": "Confirmado", "preparando": "En preparación",
	"enviado": "Enviado", "entregado": "Entregado", "cancelado": "Cancelado",
}

var EtapaLabel = map[string]string{
	"": "Sin etapa", "prospeccion": "Prospección", "seguimiento": "Seguimiento", "cierre": "Cierre",
	"venta_confirmada": "Venta confirmada", "perdida": "Perdida",
}

// Estados del pedido que cuentan como compra (los que descuentan stock).
const compraSQL = `('confirmado','preparando','enviado','entregado')`

// Lima no tiene horario de verano: UTC−5 fijo (no depende de tzdata).
var Lima = time.FixedZone("Lima", -5*3600)

// ---------------------------------------------------------------------------
// Autor de un cambio

type autorKey struct{}

// ConAutor deja en el contexto quién hace el cambio (la persona del panel). Lo que llega sin autor lo hizo el bot.
func ConAutor(ctx context.Context, nombre string) context.Context {
	return context.WithValue(ctx, autorKey{}, nombre)
}

func autorDe(ctx context.Context) string {
	if a, _ := ctx.Value(autorKey{}).(string); a != "" {
		return a
	}
	return "bot"
}

func (s *Store) registrar(ctx context.Context, q queryer, customerID int64, tipo, texto string, ref int64) error {
	_, err := q.ExecContext(ctx, `INSERT INTO actividad(tenant_id, customer_id, tipo, texto, autor, ref_id, created_at)
		SELECT tenant_id, id, ?, ?, ?, ?, ? FROM customers WHERE id=? AND tenant_id=?`,
		tipo, texto, autorDe(ctx), ref, now(), customerID, s.tid)
	return err
}

// ---------------------------------------------------------------------------
// Clientas

type Clienta struct {
	ID            int64      `json:"id"`
	JID           string     `json:"jid"`
	Phone         string     `json:"phone"`
	Name          string     `json:"name"`
	Email         string     `json:"email"`
	Ciudad        string     `json:"ciudad"`
	Etapa         string     `json:"etapa"`
	EtapaFijada   bool       `json:"etapa_fijada"`
	AsesoraID     int64      `json:"asesora_id"`
	Asesora       string     `json:"asesora"`
	Etiquetas     []string   `json:"etiquetas"`
	Pedidos       int        `json:"pedidos"`
	TotalComprado float64    `json:"total_comprado"`
	UltimaCompra  *time.Time `json:"ultima_compra"`
	Conversacion  int64      `json:"conversation_id"`
	UltimoMensaje *time.Time `json:"ultimo_mensaje"`
	NoLeidos      int        `json:"no_leidos"`
	CreatedAt     time.Time  `json:"created_at"`
}

// FiltroClientas: todo opcional. Asesora −1 = sin asignar. Compra: "con" | "sin".
type FiltroClientas struct {
	Q        string
	Etapa    string // "" = todas; "sin" = sin etapa
	Etiqueta string
	Asesora  int64
	Compra   string
	Limit    int
}

const clientaSelect = `SELECT cu.id, cu.jid, cu.phone, cu.name, cu.email, cu.ciudad, cu.etapa, cu.etapa_fijada, cu.asesora_id,
	coalesce(nullif(u.name, ''), u.username, ''), cu.created_at,
	(SELECT count(*) FROM orders o WHERE o.customer_id=cu.id AND o.tenant_id=cu.tenant_id),
	(SELECT coalesce(sum(o.total), 0) FROM orders o WHERE o.customer_id=cu.id AND o.tenant_id=cu.tenant_id AND o.status IN ` + compraSQL + `),
	(SELECT max(o.created_at) FROM orders o WHERE o.customer_id=cu.id AND o.tenant_id=cu.tenant_id AND o.status IN ` + compraSQL + `),
	coalesce(cv.id, 0), cv.last_message_at, coalesce(cv.unread, 0)
	FROM customers cu
	LEFT JOIN conversations cv ON cv.customer_id=cu.id AND cv.tenant_id=cu.tenant_id
	LEFT JOIN users u ON u.id=cu.asesora_id AND u.tenant_id=cu.tenant_id`

func scanClienta(sc interface{ Scan(...any) error }) (*Clienta, error) {
	c := &Clienta{Etiquetas: []string{}}
	var fijada int
	var ultima, ultMsg any
	if err := sc.Scan(&c.ID, &c.JID, &c.Phone, &c.Name, &c.Email, &c.Ciudad, &c.Etapa, &fijada, &c.AsesoraID, &c.Asesora,
		&c.CreatedAt, &c.Pedidos, &c.TotalComprado, &ultima, &c.Conversacion, &ultMsg, &c.NoLeidos); err != nil {
		return nil, err
	}
	c.EtapaFijada = fijada == 1
	c.UltimaCompra = tiempo(ultima)
	if c.Conversacion > 0 {
		c.UltimoMensaje = tiempo(ultMsg)
	}
	return c, nil
}

// tiempo lee una fecha que llega de una expresión (max(), coalesce): SQLite la da como texto, MariaDB como time.Time.
func tiempo(v any) *time.Time {
	switch x := v.(type) {
	case time.Time:
		t := x.UTC()
		return &t
	case []byte:
		return tiempo(string(x))
	case string:
		// modernc/sqlite guarda time.Time con el formato de Time.String().
		for _, layout := range []string{"2006-01-02 15:04:05.999999999 -0700 MST", "2006-01-02 15:04:05.999999999-07:00", time.RFC3339Nano, "2006-01-02 15:04:05.999999999",
			"2006-01-02 15:04:05", "2006-01-02T15:04:05Z"} {
			if t, err := time.Parse(layout, x); err == nil {
				t = t.UTC()
				return &t
			}
		}
	}
	return nil
}

func (s *Store) ListClientas(ctx context.Context, f FiltroClientas) ([]*Clienta, error) {
	q := clientaSelect + ` WHERE cu.tenant_id=?`
	args := []any{s.tid}
	if t := strings.ToLower(strings.TrimSpace(f.Q)); t != "" {
		like := "%" + strings.NewReplacer("%", "", "_", "").Replace(t) + "%"
		q += ` AND (lower(cu.name) LIKE ? OR cu.phone LIKE ? OR lower(cu.email) LIKE ? OR lower(cu.ciudad) LIKE ?)`
		args = append(args, like, like, like, like)
	}
	switch {
	case f.Etapa == "sin":
		q += ` AND cu.etapa=''`
	case f.Etapa != "":
		q += ` AND cu.etapa=?`
		args = append(args, f.Etapa)
	}
	if f.Etiqueta != "" {
		q += ` AND EXISTS (SELECT 1 FROM cliente_etiquetas e WHERE e.customer_id=cu.id AND e.tenant_id=cu.tenant_id AND e.etiqueta=?)`
		args = append(args, normEtiqueta(f.Etiqueta))
	}
	switch {
	case f.Asesora < 0:
		q += ` AND cu.asesora_id=0`
	case f.Asesora > 0:
		q += ` AND cu.asesora_id=?`
		args = append(args, f.Asesora)
	}
	switch f.Compra {
	case "con":
		q += ` AND EXISTS (SELECT 1 FROM orders o WHERE o.customer_id=cu.id AND o.tenant_id=cu.tenant_id AND o.status IN ` + compraSQL + `)`
	case "sin":
		q += ` AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id=cu.id AND o.tenant_id=cu.tenant_id AND o.status IN ` + compraSQL + `)`
	}
	if f.Limit <= 0 || f.Limit > 1000 {
		f.Limit = 500
	}
	q += ` ORDER BY coalesce(cv.last_message_at, cu.created_at) DESC, cu.id DESC LIMIT ?`
	args = append(args, f.Limit)
	rows, err := s.DB.QueryContext(ctx, q, args...)
	if err != nil {
		return nil, err
	}
	var out []*Clienta
	for rows.Next() {
		c, err := scanClienta(rows)
		if err != nil {
			rows.Close()
			return nil, err
		}
		out = append(out, c)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return out, s.cargarEtiquetas(ctx, out)
}

func (s *Store) GetClienta(ctx context.Context, id int64) (*Clienta, error) {
	c, err := scanClienta(s.DB.QueryRowContext(ctx, clientaSelect+` WHERE cu.tenant_id=? AND cu.id=?`, s.tid, id))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return c, s.cargarEtiquetas(ctx, []*Clienta{c})
}

func (s *Store) cargarEtiquetas(ctx context.Context, cs []*Clienta) error {
	if len(cs) == 0 {
		return nil
	}
	by := map[int64]*Clienta{}
	for _, c := range cs {
		by[c.ID] = c
	}
	rows, err := s.DB.QueryContext(ctx, `SELECT customer_id, etiqueta FROM cliente_etiquetas WHERE tenant_id=? ORDER BY etiqueta`, s.tid)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var id int64
		var e string
		if err := rows.Scan(&id, &e); err != nil {
			return err
		}
		if c := by[id]; c != nil {
			c.Etiquetas = append(c.Etiquetas, e)
		}
	}
	return rows.Err()
}

// DatosClienta: los campos editables de la ficha (nil = no cambia).
type DatosClienta struct {
	Name   *string `json:"name"`
	Phone  *string `json:"phone"`
	Email  *string `json:"email"`
	Ciudad *string `json:"ciudad"`
}

func recorta(s string, n int) string {
	s = strings.TrimSpace(s)
	if utf8.RuneCountInString(s) > n {
		s = string([]rune(s)[:n])
	}
	return s
}

func (s *Store) UpdateClienta(ctx context.Context, id int64, d DatosClienta) error {
	sets, args, cambios := []string{}, []any{}, []string{}
	add := func(col, label string, v *string, n int) {
		if v != nil {
			sets = append(sets, col+"=?")
			args = append(args, recorta(*v, n))
			cambios = append(cambios, label)
		}
	}
	add("name", "nombre", d.Name, 200)
	if d.Phone != nil {
		digits := soloDigitos(*d.Phone)
		d.Phone = &digits
	}
	add("phone", "teléfono", d.Phone, 30)
	if d.Email != nil {
		e := strings.ToLower(strings.TrimSpace(*d.Email))
		if e != "" && (!strings.Contains(e, "@") || strings.ContainsAny(e, " ,;")) {
			return errors.New("correo inválido")
		}
		d.Email = &e
	}
	add("email", "correo", d.Email, 200)
	add("ciudad", "ciudad", d.Ciudad, 120)
	if len(sets) == 0 {
		return nil
	}
	res, err := s.DB.ExecContext(ctx, `UPDATE customers SET `+strings.Join(sets, ", ")+` WHERE id=? AND tenant_id=?`,
		append(args, id, s.tid)...)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return ErrNotFound
	}
	return s.registrar(ctx, s.DB, id, "edicion", "Editó "+strings.Join(cambios, ", "), 0)
}

func soloDigitos(p string) string {
	return strings.Map(func(r rune) rune {
		if r >= '0' && r <= '9' {
			return r
		}
		return -1
	}, p)
}

// SetEtapa fija a mano la etapa del embudo (el bot ya no la mueve). Con automatica, el bot la vuelve a mover y la
// etapa actual se conserva hasta su próximo turno.
func (s *Store) SetEtapa(ctx context.Context, id int64, etapa string, automatica bool) error {
	if etapa != "" && !ValidEtapa(etapa) {
		return fmt.Errorf("etapa inválida: %s", etapa)
	}
	var prev string
	var fijada int
	if err := s.DB.QueryRowContext(ctx, `SELECT etapa, etapa_fijada FROM customers WHERE id=? AND tenant_id=?`, id, s.tid).
		Scan(&prev, &fijada); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return ErrNotFound
		}
		return err
	}
	if automatica {
		if fijada == 0 {
			return nil
		}
		if _, err := s.DB.ExecContext(ctx, `UPDATE customers SET etapa_fijada=0 WHERE id=? AND tenant_id=?`, id, s.tid); err != nil {
			return err
		}
		return s.registrar(ctx, s.DB, id, "etapa", "La etapa vuelve a moverla el bot", 0)
	}
	if _, err := s.DB.ExecContext(ctx, `UPDATE customers SET etapa=?, etapa_fijada=1 WHERE id=? AND tenant_id=?`, etapa, id, s.tid); err != nil {
		return err
	}
	if prev == etapa && fijada == 1 {
		return nil
	}
	return s.registrar(ctx, s.DB, id, "etapa", "Etapa: "+EtapaLabel[prev]+" → "+EtapaLabel[etapa], 0)
}

// etapaDelBot: el bot guardó el contexto de la conversación; si trae una etapa distinta y nadie la fijó a mano, pasa a
// la clienta y queda en su línea de tiempo.
func (s *Store) etapaDelBot(ctx context.Context, convID int64, contextJSON string) {
	if !strings.Contains(contextJSON, `"etapa"`) {
		return
	}
	var c struct {
		Etapa string `json:"etapa"`
	}
	if json.Unmarshal([]byte(contextJSON), &c) != nil || !ValidEtapa(c.Etapa) {
		return
	}
	var custID int64
	var prev string
	if err := s.DB.QueryRowContext(ctx, `SELECT cu.id, cu.etapa FROM conversations cv JOIN customers cu ON cu.id=cv.customer_id
		AND cu.tenant_id=cv.tenant_id WHERE cv.id=? AND cv.tenant_id=? AND cu.etapa_fijada=0`, convID, s.tid).Scan(&custID, &prev); err != nil {
		return
	}
	if prev == c.Etapa {
		return
	}
	if _, err := s.DB.ExecContext(ctx, `UPDATE customers SET etapa=? WHERE id=? AND tenant_id=? AND etapa_fijada=0`, c.Etapa, custID, s.tid); err != nil {
		return
	}
	_ = s.registrar(ConAutor(ctx, "bot"), s.DB, custID, "etapa", "Etapa: "+EtapaLabel[prev]+" → "+EtapaLabel[c.Etapa], 0)
}

// rellenarEtapas copia, una sola vez por clienta, la etapa que el bot dejó en el contexto de la conversación (antes de
// que existiera customers.etapa). Recorre todas las empresas: cada fila va a su propia clienta.
func (s *Store) rellenarEtapas() error {
	rows, err := s.DB.Query(`SELECT cv.customer_id, cv.context FROM conversations cv JOIN customers cu ON cu.id=cv.customer_id
		WHERE cu.etapa='' AND cu.etapa_fijada=0 AND cv.context LIKE '%"etapa"%'`)
	if err != nil {
		return err
	}
	type par struct {
		id    int64
		etapa string
	}
	var ps []par
	for rows.Next() {
		var id int64
		var raw string
		if err := rows.Scan(&id, &raw); err != nil {
			rows.Close()
			return err
		}
		var c struct {
			Etapa string `json:"etapa"`
		}
		if json.Unmarshal([]byte(raw), &c) == nil && ValidEtapa(c.Etapa) {
			ps = append(ps, par{id, c.Etapa})
		}
	}
	rows.Close()
	for _, p := range ps {
		if _, err := s.DB.Exec(`UPDATE customers SET etapa=? WHERE id=? AND etapa=''`, p.etapa, p.id); err != nil {
			return err
		}
	}
	return rows.Err()
}

// Asignar deja la clienta a cargo de una persona del equipo (0 = nadie).
func (s *Store) AsignarClienta(ctx context.Context, id, userID int64) error {
	nombre, err := s.nombreUsuario(ctx, userID)
	if err != nil {
		return err
	}
	res, err := s.DB.ExecContext(ctx, `UPDATE customers SET asesora_id=? WHERE id=? AND tenant_id=?`, userID, id, s.tid)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return ErrNotFound
	}
	return s.registrar(ctx, s.DB, id, "asignacion", asignacionTexto("Clienta", "a", nombre), 0)
}

// AsignarPedido deja el pedido a cargo de una persona del equipo (0 = nadie).
func (s *Store) AsignarPedido(ctx context.Context, orderID, userID int64) error {
	nombre, err := s.nombreUsuario(ctx, userID)
	if err != nil {
		return err
	}
	var custID int64
	if err := s.DB.QueryRowContext(ctx, `SELECT customer_id FROM orders WHERE id=? AND tenant_id=?`, orderID, s.tid).Scan(&custID); err != nil {
		return ErrNotFound
	}
	if _, err := s.DB.ExecContext(ctx, `UPDATE orders SET asesora_id=? WHERE id=? AND tenant_id=?`, userID, orderID, s.tid); err != nil {
		return err
	}
	return s.registrar(ctx, s.DB, custID, "asignacion", asignacionTexto(fmt.Sprintf("Pedido #%d", orderID), "o", nombre), orderID)
}

func asignacionTexto(que, genero, nombre string) string {
	if nombre == "" {
		return que + " sin asignar"
	}
	return que + " asignad" + genero + " a " + nombre
}

// nombreUsuario valida que el usuario sea de esta empresa (0 = nadie, nombre vacío).
func (s *Store) nombreUsuario(ctx context.Context, userID int64) (string, error) {
	if userID == 0 {
		return "", nil
	}
	var name, username string
	if err := s.DB.QueryRowContext(ctx, `SELECT name, username FROM users WHERE id=? AND tenant_id=? AND active=1`, userID, s.tid).
		Scan(&name, &username); err != nil {
		return "", fmt.Errorf("usuario %d: %w", userID, ErrNotFound)
	}
	if name != "" {
		return name, nil
	}
	return username, nil
}

// ---------------------------------------------------------------------------
// Etiquetas

func normEtiqueta(e string) string {
	e = strings.ToLower(strings.Join(strings.Fields(e), " "))
	return strings.Trim(recorta(e, 40), ",;#")
}

// SetEtiquetas reemplaza las etiquetas de la clienta (máximo 12).
func (s *Store) SetEtiquetas(ctx context.Context, id int64, etiquetas []string) error {
	seen := map[string]bool{}
	var limpias []string
	for _, e := range etiquetas {
		if e = normEtiqueta(e); e != "" && !seen[e] && len(limpias) < 12 {
			seen[e] = true
			limpias = append(limpias, e)
		}
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var one int
	if err := tx.QueryRowContext(ctx, `SELECT 1 FROM customers WHERE id=? AND tenant_id=?`, id, s.tid).Scan(&one); err != nil {
		return ErrNotFound
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM cliente_etiquetas WHERE customer_id=? AND tenant_id=?`, id, s.tid); err != nil {
		return err
	}
	for _, e := range limpias {
		if _, err := tx.ExecContext(ctx, `INSERT INTO cliente_etiquetas(tenant_id, customer_id, etiqueta) VALUES(?,?,?)`, s.tid, id, e); err != nil {
			return err
		}
	}
	return tx.Commit()
}

type EtiquetaUso struct {
	Etiqueta string `json:"etiqueta"`
	Clientas int    `json:"clientas"`
}

func (s *Store) Etiquetas(ctx context.Context) ([]EtiquetaUso, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT etiqueta, count(*) FROM cliente_etiquetas WHERE tenant_id=? GROUP BY etiqueta ORDER BY count(*) DESC, etiqueta`, s.tid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []EtiquetaUso{}
	for rows.Next() {
		var e EtiquetaUso
		if err := rows.Scan(&e.Etiqueta, &e.Clientas); err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

// ---------------------------------------------------------------------------
// Notas internas

type Nota struct {
	ID         int64     `json:"id"`
	CustomerID int64     `json:"customer_id"`
	OrderID    int64     `json:"order_id"`
	Texto      string    `json:"texto"`
	Autor      string    `json:"autor"`
	AutorID    int64     `json:"autor_id"`
	CreatedAt  time.Time `json:"created_at"`
}

// AddNota guarda una nota en la clienta o en un pedido (con OrderID, la clienta sale del pedido).
func (s *Store) AddNota(ctx context.Context, n *Nota) error {
	n.Texto = recorta(n.Texto, 4000)
	if n.Texto == "" {
		return errors.New("la nota está vacía")
	}
	if n.OrderID > 0 {
		if err := s.DB.QueryRowContext(ctx, `SELECT customer_id FROM orders WHERE id=? AND tenant_id=?`, n.OrderID, s.tid).Scan(&n.CustomerID); err != nil {
			return fmt.Errorf("pedido %d: %w", n.OrderID, ErrNotFound)
		}
	} else {
		var one int
		if err := s.DB.QueryRowContext(ctx, `SELECT 1 FROM customers WHERE id=? AND tenant_id=?`, n.CustomerID, s.tid).Scan(&one); err != nil {
			return fmt.Errorf("clienta %d: %w", n.CustomerID, ErrNotFound)
		}
	}
	n.CreatedAt = now()
	var oid any
	if n.OrderID > 0 {
		oid = n.OrderID
	}
	res, err := s.DB.ExecContext(ctx, `INSERT INTO notas(tenant_id, customer_id, order_id, texto, autor, autor_id, created_at) VALUES(?,?,?,?,?,?,?)`,
		s.tid, n.CustomerID, oid, n.Texto, n.Autor, n.AutorID, n.CreatedAt)
	if err != nil {
		return err
	}
	n.ID, _ = res.LastInsertId()
	return nil
}

// ListNotas: las de la clienta (todas, también las de sus pedidos) o las de un pedido.
func (s *Store) ListNotas(ctx context.Context, customerID, orderID int64) ([]*Nota, error) {
	q := `SELECT id, customer_id, coalesce(order_id, 0), texto, autor, autor_id, created_at FROM notas WHERE tenant_id=?`
	args := []any{s.tid}
	if orderID > 0 {
		q += ` AND order_id=?`
		args = append(args, orderID)
	} else {
		q += ` AND customer_id=?`
		args = append(args, customerID)
	}
	rows, err := s.DB.QueryContext(ctx, q+` ORDER BY id DESC LIMIT 200`, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*Nota{}
	for rows.Next() {
		n := &Nota{}
		if err := rows.Scan(&n.ID, &n.CustomerID, &n.OrderID, &n.Texto, &n.Autor, &n.AutorID, &n.CreatedAt); err != nil {
			return nil, err
		}
		out = append(out, n)
	}
	return out, rows.Err()
}

// DeleteNota: la borra su autora (autorID) o una admin.
func (s *Store) DeleteNota(ctx context.Context, id, autorID int64, admin bool) error {
	q := `DELETE FROM notas WHERE id=? AND tenant_id=?`
	args := []any{id, s.tid}
	if !admin {
		q += ` AND autor_id=?`
		args = append(args, autorID)
	}
	res, err := s.DB.ExecContext(ctx, q, args...)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return ErrNotFound
	}
	return nil
}

// ---------------------------------------------------------------------------
// Tareas

type Tarea struct {
	ID            int64      `json:"id"`
	CustomerID    int64      `json:"customer_id"`
	Clienta       string     `json:"clienta"`
	OrderID       int64      `json:"order_id"`
	Titulo        string     `json:"titulo"`
	Vence         *time.Time `json:"vence"`
	ResponsableID int64      `json:"responsable_id"`
	Responsable   string     `json:"responsable"`
	Hecha         bool       `json:"hecha"`
	HechaAt       *time.Time `json:"hecha_at"`
	CreadaPor     string     `json:"creada_por"`
	CreatedAt     time.Time  `json:"created_at"`
}

// FiltroTareas. Vista: "pendientes" (por defecto), "hoy", "vencidas", "hechas", "todas".
type FiltroTareas struct {
	Vista         string
	ResponsableID int64 // 0 = de todas
	CustomerID    int64
	Limit         int
}

// DiaLima: inicio y fin (UTC) del día de hoy en Lima.
func DiaLima(t time.Time) (time.Time, time.Time) {
	l := t.In(Lima)
	ini := time.Date(l.Year(), l.Month(), l.Day(), 0, 0, 0, 0, Lima)
	return ini.UTC(), ini.Add(24 * time.Hour).UTC()
}

const tareaSelect = `SELECT t.id, coalesce(t.customer_id, 0), coalesce(nullif(cu.name, ''), cu.phone, ''), coalesce(t.order_id, 0), t.titulo,
	t.vence, t.responsable_id, coalesce(nullif(u.name, ''), u.username, ''), t.hecha, t.hecha_at, t.creada_por, t.created_at
	FROM tareas t
	LEFT JOIN customers cu ON cu.id=t.customer_id AND cu.tenant_id=t.tenant_id
	LEFT JOIN users u ON u.id=t.responsable_id AND u.tenant_id=t.tenant_id`

func scanTarea(sc interface{ Scan(...any) error }) (*Tarea, error) {
	t := &Tarea{}
	var vence, hechaAt sql.NullTime
	var hecha int
	if err := sc.Scan(&t.ID, &t.CustomerID, &t.Clienta, &t.OrderID, &t.Titulo, &vence, &t.ResponsableID, &t.Responsable,
		&hecha, &hechaAt, &t.CreadaPor, &t.CreatedAt); err != nil {
		return nil, err
	}
	t.Hecha = hecha == 1
	if vence.Valid {
		v := vence.Time.UTC()
		t.Vence = &v
	}
	if hechaAt.Valid {
		h := hechaAt.Time.UTC()
		t.HechaAt = &h
	}
	return t, nil
}

func (s *Store) ListTareas(ctx context.Context, f FiltroTareas) ([]*Tarea, error) {
	q := tareaSelect + ` WHERE t.tenant_id=?`
	args := []any{s.tid}
	ini, fin := DiaLima(now())
	orden := ` ORDER BY t.vence IS NULL, t.vence, t.id`
	switch f.Vista {
	case "hoy":
		q += ` AND t.hecha=0 AND t.vence >= ? AND t.vence < ?`
		args = append(args, ini, fin)
	case "vencidas":
		q += ` AND t.hecha=0 AND t.vence < ?`
		args = append(args, ini)
	case "hechas":
		q += ` AND t.hecha=1`
		orden = ` ORDER BY t.hecha_at DESC, t.id DESC`
	case "todas":
		orden = ` ORDER BY t.hecha, t.vence IS NULL, t.vence, t.id`
	default:
		q += ` AND t.hecha=0`
	}
	if f.ResponsableID > 0 {
		q += ` AND t.responsable_id=?`
		args = append(args, f.ResponsableID)
	}
	if f.CustomerID > 0 {
		q += ` AND t.customer_id=?`
		args = append(args, f.CustomerID)
	}
	if f.Limit <= 0 || f.Limit > 500 {
		f.Limit = 300
	}
	rows, err := s.DB.QueryContext(ctx, q+orden+` LIMIT ?`, append(args, f.Limit)...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*Tarea{}
	for rows.Next() {
		t, err := scanTarea(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, t)
	}
	return out, rows.Err()
}

func (s *Store) GetTarea(ctx context.Context, id int64) (*Tarea, error) {
	t, err := scanTarea(s.DB.QueryRowContext(ctx, tareaSelect+` WHERE t.tenant_id=? AND t.id=?`, s.tid, id))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	return t, err
}

// checkVinculos: la clienta, el pedido y la responsable de una tarea son de esta empresa. Con pedido y sin clienta, la
// clienta sale del pedido.
func (s *Store) checkVinculos(ctx context.Context, t *Tarea) error {
	if t.OrderID > 0 {
		var cid int64
		if err := s.DB.QueryRowContext(ctx, `SELECT customer_id FROM orders WHERE id=? AND tenant_id=?`, t.OrderID, s.tid).Scan(&cid); err != nil {
			return fmt.Errorf("pedido %d: %w", t.OrderID, ErrNotFound)
		}
		if t.CustomerID == 0 {
			t.CustomerID = cid
		}
	}
	if t.CustomerID > 0 {
		var one int
		if err := s.DB.QueryRowContext(ctx, `SELECT 1 FROM customers WHERE id=? AND tenant_id=?`, t.CustomerID, s.tid).Scan(&one); err != nil {
			return fmt.Errorf("clienta %d: %w", t.CustomerID, ErrNotFound)
		}
	}
	_, err := s.nombreUsuario(ctx, t.ResponsableID)
	return err
}

func nulo(id int64) any {
	if id > 0 {
		return id
	}
	return nil
}

func (s *Store) AddTarea(ctx context.Context, t *Tarea) error {
	t.Titulo = recorta(t.Titulo, 300)
	if t.Titulo == "" {
		return errors.New("la tarea necesita un título")
	}
	if err := s.checkVinculos(ctx, t); err != nil {
		return err
	}
	t.CreatedAt = now()
	var vence any
	if t.Vence != nil {
		vence = t.Vence.UTC()
	}
	res, err := s.DB.ExecContext(ctx, `INSERT INTO tareas(tenant_id, customer_id, order_id, titulo, vence, responsable_id, hecha, creada_por, created_at)
		VALUES(?,?,?,?,?,?,0,?,?)`, s.tid, nulo(t.CustomerID), nulo(t.OrderID), t.Titulo, vence, t.ResponsableID, t.CreadaPor, t.CreatedAt)
	if err != nil {
		return err
	}
	t.ID, _ = res.LastInsertId()
	return nil
}

// CambioTarea: nil = no cambia. SinVence quita la fecha.
type CambioTarea struct {
	Titulo        *string    `json:"titulo"`
	Vence         *time.Time `json:"vence"`
	SinVence      bool       `json:"sin_vence"`
	ResponsableID *int64     `json:"responsable_id"`
	Hecha         *bool      `json:"hecha"`
}

func (s *Store) UpdateTarea(ctx context.Context, id int64, c CambioTarea) error {
	t, err := s.GetTarea(ctx, id)
	if err != nil {
		return err
	}
	if c.Titulo != nil {
		if t.Titulo = recorta(*c.Titulo, 300); t.Titulo == "" {
			return errors.New("la tarea necesita un título")
		}
	}
	if c.ResponsableID != nil {
		if _, err := s.nombreUsuario(ctx, *c.ResponsableID); err != nil {
			return err
		}
		t.ResponsableID = *c.ResponsableID
	}
	var vence any
	if c.SinVence {
		t.Vence = nil
	} else if c.Vence != nil {
		t.Vence = c.Vence
	}
	if t.Vence != nil {
		vence = t.Vence.UTC()
	}
	var hechaAt any
	if t.HechaAt != nil {
		hechaAt = *t.HechaAt
	}
	if c.Hecha != nil && *c.Hecha != t.Hecha {
		t.Hecha = *c.Hecha
		hechaAt = nil
		if t.Hecha {
			hechaAt = now()
		}
	}
	hecha := 0
	if t.Hecha {
		hecha = 1
	}
	_, err = s.DB.ExecContext(ctx, `UPDATE tareas SET titulo=?, vence=?, responsable_id=?, hecha=?, hecha_at=? WHERE id=? AND tenant_id=?`,
		t.Titulo, vence, t.ResponsableID, hecha, hechaAt, id, s.tid)
	return err
}

func (s *Store) DeleteTarea(ctx context.Context, id int64) error {
	res, err := s.DB.ExecContext(ctx, `DELETE FROM tareas WHERE id=? AND tenant_id=?`, id, s.tid)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return ErrNotFound
	}
	return nil
}

// ---------------------------------------------------------------------------
// Línea de tiempo

type Evento struct {
	Tipo    string    `json:"tipo"` // mensaje | pedido | estado | etapa | asignacion | edicion | nota | tarea | tarea_hecha
	Texto   string    `json:"texto"`
	Autor   string    `json:"autor"`
	Detalle string    `json:"detalle,omitempty"` // mensaje: in | out; pedido: estado actual
	RefID   int64     `json:"ref_id,omitempty"`
	Monto   float64   `json:"monto,omitempty"` // pedido: total
	At      time.Time `json:"at"`
}

// Actividad junta, de la más reciente a la más antigua: mensajes, pedidos, cambios (estado, etapa, asignación),
// notas y tareas de la clienta.
func (s *Store) Actividad(ctx context.Context, customerID int64, limit int) ([]Evento, error) {
	var one int
	if err := s.DB.QueryRowContext(ctx, `SELECT 1 FROM customers WHERE id=? AND tenant_id=?`, customerID, s.tid).Scan(&one); err != nil {
		return nil, ErrNotFound
	}
	if limit <= 0 || limit > 500 {
		limit = 200
	}
	var out []Evento
	leer := func(q string, args []any, fn func(sc interface{ Scan(...any) error }) error) error {
		rows, err := s.DB.QueryContext(ctx, q, args...)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			if err := fn(rows); err != nil {
				return err
			}
		}
		return rows.Err()
	}
	// Mensajes (los últimos).
	if err := leer(`SELECT m.direction, m.kind, m.body, m.author, m.created_at FROM messages m
		JOIN conversations cv ON cv.id=m.conversation_id AND cv.tenant_id=m.tenant_id
		WHERE m.tenant_id=? AND cv.customer_id=? ORDER BY m.id DESC LIMIT ?`, []any{s.tid, customerID, limit},
		func(sc interface{ Scan(...any) error }) error {
			var e Evento
			var kind string
			if err := sc.Scan(&e.Detalle, &kind, &e.Texto, &e.Autor, &e.At); err != nil {
				return err
			}
			e.Tipo = "mensaje"
			if e.Texto == "" {
				e.Texto = map[string]string{"image": "📷 Foto", "location": "📍 Ubicación"}[kind]
			}
			e.Texto = recorta(e.Texto, 280)
			if e.Autor == "" {
				e.Autor = map[string]string{"in": "cliente", "out": "bot"}[e.Detalle]
			}
			out = append(out, e)
			return nil
		}); err != nil {
		return nil, err
	}
	// Pedidos creados.
	if err := leer(`SELECT id, status, total, source, created_at FROM orders WHERE tenant_id=? AND customer_id=? ORDER BY id DESC LIMIT ?`,
		[]any{s.tid, customerID, limit}, func(sc interface{ Scan(...any) error }) error {
			var e Evento
			var status, source string
			if err := sc.Scan(&e.RefID, &status, &e.Monto, &source, &e.At); err != nil {
				return err
			}
			e.Tipo, e.Autor, e.Detalle = "pedido", map[bool]string{true: "manual", false: "bot"}[source == "manual"], status
			e.Texto = fmt.Sprintf("Pedido #%d creado", e.RefID)
			out = append(out, e)
			return nil
		}); err != nil {
		return nil, err
	}
	// Cambios registrados.
	if err := leer(`SELECT tipo, texto, autor, ref_id, created_at FROM actividad WHERE tenant_id=? AND customer_id=? ORDER BY id DESC LIMIT ?`,
		[]any{s.tid, customerID, limit}, func(sc interface{ Scan(...any) error }) error {
			var e Evento
			if err := sc.Scan(&e.Tipo, &e.Texto, &e.Autor, &e.RefID, &e.At); err != nil {
				return err
			}
			out = append(out, e)
			return nil
		}); err != nil {
		return nil, err
	}
	// Notas.
	if err := leer(`SELECT texto, autor, coalesce(order_id, 0), created_at FROM notas WHERE tenant_id=? AND customer_id=? ORDER BY id DESC LIMIT ?`,
		[]any{s.tid, customerID, limit}, func(sc interface{ Scan(...any) error }) error {
			e := Evento{Tipo: "nota"}
			if err := sc.Scan(&e.Texto, &e.Autor, &e.RefID, &e.At); err != nil {
				return err
			}
			out = append(out, e)
			return nil
		}); err != nil {
		return nil, err
	}
	// Tareas: creada y, si se hizo, hecha.
	if err := leer(`SELECT id, titulo, creada_por, hecha, hecha_at, created_at FROM tareas WHERE tenant_id=? AND customer_id=? ORDER BY id DESC LIMIT ?`,
		[]any{s.tid, customerID, limit}, func(sc interface{ Scan(...any) error }) error {
			var id int64
			var titulo, por string
			var hecha int
			var hechaAt sql.NullTime
			var at time.Time
			if err := sc.Scan(&id, &titulo, &por, &hecha, &hechaAt, &at); err != nil {
				return err
			}
			out = append(out, Evento{Tipo: "tarea", Texto: "Tarea: " + titulo, Autor: por, RefID: id, At: at})
			if hecha == 1 && hechaAt.Valid {
				out = append(out, Evento{Tipo: "tarea_hecha", Texto: "Hecha: " + titulo, RefID: id, At: hechaAt.Time})
			}
			return nil
		}); err != nil {
		return nil, err
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].At.After(out[j].At) })
	if len(out) > limit {
		out = out[:limit]
	}
	for i := range out {
		out[i].At = out[i].At.UTC()
	}
	if out == nil {
		out = []Evento{}
	}
	return out, nil
}

// TodosLosPedidos: todos los pedidos de la empresa, del más nuevo al más viejo (para exportar).
func (s *Store) TodosLosPedidos(ctx context.Context, limit int) ([]*Order, error) {
	rows, err := s.DB.QueryContext(ctx, orderSelect+` WHERE o.tenant_id=? ORDER BY o.id DESC LIMIT ?`, s.tid, limit)
	if err != nil {
		return nil, err
	}
	var out []*Order
	for rows.Next() {
		o, err := scanOrder(rows)
		if err != nil {
			rows.Close()
			return nil, err
		}
		out = append(out, o)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return out, s.loadItems(ctx, s.DB, out)
}

// ---------------------------------------------------------------------------
// Panel de inicio

type Inicio struct {
	VentasMes         float64        `json:"ventas_mes"`
	VentasMesAnterior float64        `json:"ventas_mes_anterior"`
	PedidosVendidos   int            `json:"pedidos_vendidos"` // del mes
	PedidosMes        int            `json:"pedidos_mes"`      // creados en el mes, de cualquier estado
	PorEstado         map[string]int `json:"por_estado"`       // abiertos + cerrados este mes
	ClientasNuevas    int            `json:"clientas_nuevas"`
	ClientasTotal     int            `json:"clientas_total"`
	Embudo            map[string]int `json:"embudo"` // clientas por etapa ("" = sin etapa)
	TareasVencidas    int            `json:"tareas_vencidas"`
	TareasHoy         int            `json:"tareas_hoy"`
	MisVencidas       int            `json:"mis_vencidas"`
	MisHoy            int            `json:"mis_hoy"`
	SinLeer           int            `json:"sin_leer"` // conversaciones con mensajes sin leer
	StockBajo         int            `json:"stock_bajo"`
}

// MesLima: inicio (UTC) del mes en curso y del anterior, en hora de Lima.
func MesLima(t time.Time) (time.Time, time.Time) {
	l := t.In(Lima)
	ini := time.Date(l.Year(), l.Month(), 1, 0, 0, 0, 0, Lima)
	return ini.UTC(), ini.AddDate(0, -1, 0).UTC()
}

func (s *Store) Inicio(ctx context.Context, yo int64) (*Inicio, error) {
	in := &Inicio{PorEstado: map[string]int{}, Embudo: map[string]int{}}
	mes, anterior := MesLima(now())
	hoy, manana := DiaLima(now())
	q := s.DB
	if err := q.QueryRowContext(ctx, `SELECT coalesce(sum(total), 0), count(*) FROM orders WHERE tenant_id=? AND status IN `+compraSQL+` AND created_at >= ?`,
		s.tid, mes).Scan(&in.VentasMes, &in.PedidosVendidos); err != nil {
		return nil, err
	}
	_ = q.QueryRowContext(ctx, `SELECT coalesce(sum(total), 0) FROM orders WHERE tenant_id=? AND status IN `+compraSQL+` AND created_at >= ? AND created_at < ?`,
		s.tid, anterior, mes).Scan(&in.VentasMesAnterior)
	_ = q.QueryRowContext(ctx, `SELECT count(*) FROM orders WHERE tenant_id=? AND created_at >= ?`, s.tid, mes).Scan(&in.PedidosMes)
	rows, err := q.QueryContext(ctx, `SELECT status, count(*) FROM orders WHERE tenant_id=?
		AND (status NOT IN ('entregado','cancelado') OR updated_at >= ?) GROUP BY status`, s.tid, mes)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var k string
		var n int
		if err := rows.Scan(&k, &n); err != nil {
			rows.Close()
			return nil, err
		}
		in.PorEstado[k] = n
	}
	rows.Close()
	rows, err = q.QueryContext(ctx, `SELECT etapa, count(*) FROM customers WHERE tenant_id=? GROUP BY etapa`, s.tid)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var k string
		var n int
		if err := rows.Scan(&k, &n); err != nil {
			rows.Close()
			return nil, err
		}
		in.Embudo[k] = n
		in.ClientasTotal += n
	}
	rows.Close()
	_ = q.QueryRowContext(ctx, `SELECT count(*) FROM customers WHERE tenant_id=? AND created_at >= ?`, s.tid, mes).Scan(&in.ClientasNuevas)
	_ = q.QueryRowContext(ctx, `SELECT coalesce(sum(CASE WHEN vence < ? THEN 1 ELSE 0 END), 0), coalesce(sum(CASE WHEN vence >= ? AND vence < ? THEN 1 ELSE 0 END), 0),
		coalesce(sum(CASE WHEN vence < ? AND responsable_id=? THEN 1 ELSE 0 END), 0), coalesce(sum(CASE WHEN vence >= ? AND vence < ? AND responsable_id=? THEN 1 ELSE 0 END), 0)
		FROM tareas WHERE tenant_id=? AND hecha=0 AND vence IS NOT NULL`, hoy, hoy, manana, hoy, yo, hoy, manana, yo, s.tid).
		Scan(&in.TareasVencidas, &in.TareasHoy, &in.MisVencidas, &in.MisHoy)
	_ = q.QueryRowContext(ctx, `SELECT count(*) FROM conversations WHERE tenant_id=? AND unread > 0`, s.tid).Scan(&in.SinLeer)
	_ = q.QueryRowContext(ctx, `SELECT count(*) FROM product_variants v JOIN products p ON p.id=v.product_id AND p.tenant_id=v.tenant_id
		WHERE v.tenant_id=? AND p.active=1 AND v.stock<=1`, s.tid).Scan(&in.StockBajo)
	return in, nil
}
