package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"
)

// Columnas del kanban, en orden.
var OrderStatuses = []string{"consulta", "pendiente", "confirmado", "preparando", "enviado", "entregado", "cancelado"}

// Estados en los que el pedido tiene el stock descontado.
var reservedStatuses = map[string]bool{"confirmado": true, "preparando": true, "enviado": true, "entregado": true}

var ErrNoStock = errors.New("stock insuficiente")

func ValidStatus(s string) bool {
	for _, x := range OrderStatuses {
		if x == s {
			return true
		}
	}
	return false
}

type OrderItem struct {
	ID          int64   `json:"id"`
	OrderID     int64   `json:"order_id"`
	ProductID   *int64  `json:"product_id"`
	VariantID   *int64  `json:"variant_id"`
	ProductCode string  `json:"product_code"`
	ProductName string  `json:"product_name"`
	Image       string  `json:"image"`
	Size        string  `json:"size"`
	Qty         int     `json:"qty"`
	UnitPrice   float64 `json:"unit_price"`
}

type Order struct {
	ID              int64       `json:"id"`
	CustomerID      int64       `json:"customer_id"`
	Status          string      `json:"status"`
	Total           float64     `json:"total"`
	Notes           string      `json:"notes"`
	Source          string      `json:"source"`
	CustomerImage   string      `json:"customer_image"`
	MatchConfidence float64     `json:"match_confidence"`
	LocationLat     *float64    `json:"location_lat"`
	LocationLng     *float64    `json:"location_lng"`
	LocationText    string      `json:"location_text"`
	StockReserved   bool        `json:"stock_reserved"`
	Position        float64     `json:"position"`
	AsesoraID       int64       `json:"asesora_id"` // persona del equipo a cargo (0 = nadie)
	CreatedAt       time.Time   `json:"created_at"`
	UpdatedAt       time.Time   `json:"updated_at"`
	Customer        *Customer   `json:"customer"`
	ConversationID  int64       `json:"conversation_id"`
	Items           []OrderItem `json:"items"`
}

const orderSelect = `SELECT o.id, o.customer_id, o.status, o.total, o.notes, o.source, o.customer_image, o.match_confidence,
	o.location_lat, o.location_lng, o.location_text, o.stock_reserved, o.position, o.asesora_id, o.created_at, o.updated_at,
	cu.id, cu.jid, cu.phone, cu.name, cu.created_at, coalesce(cv.id, 0)
	FROM orders o JOIN customers cu ON cu.id=o.customer_id AND cu.tenant_id=o.tenant_id
	LEFT JOIN conversations cv ON cv.customer_id=cu.id AND cv.tenant_id=o.tenant_id`

func scanOrder(sc interface{ Scan(...any) error }) (*Order, error) {
	o := &Order{Customer: &Customer{}, Items: []OrderItem{}}
	var reserved int
	var lat, lng sql.NullFloat64
	err := sc.Scan(&o.ID, &o.CustomerID, &o.Status, &o.Total, &o.Notes, &o.Source, &o.CustomerImage, &o.MatchConfidence,
		&lat, &lng, &o.LocationText, &reserved, &o.Position, &o.AsesoraID, &o.CreatedAt, &o.UpdatedAt,
		&o.Customer.ID, &o.Customer.JID, &o.Customer.Phone, &o.Customer.Name, &o.Customer.CreatedAt, &o.ConversationID)
	if lat.Valid {
		o.LocationLat = &lat.Float64
	}
	if lng.Valid {
		o.LocationLng = &lng.Float64
	}
	o.StockReserved = reserved == 1
	return o, err
}

func (s *Store) loadItems(ctx context.Context, q queryer, orders []*Order) error {
	if len(orders) == 0 {
		return nil
	}
	byID := map[int64]*Order{}
	ids := make([]any, 0, len(orders)+1)
	ids = append(ids, s.tid)
	ph := ""
	for i, o := range orders {
		byID[o.ID] = o
		ids = append(ids, o.ID)
		if i > 0 {
			ph += ","
		}
		ph += "?"
	}
	rows, err := q.QueryContext(ctx, `SELECT id, order_id, product_id, variant_id, product_code, product_name, image, size, qty, unit_price
		FROM order_items WHERE tenant_id=? AND order_id IN (`+ph+`) ORDER BY id`, ids...)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var it OrderItem
		var pid, vid sql.NullInt64
		if err := rows.Scan(&it.ID, &it.OrderID, &pid, &vid, &it.ProductCode, &it.ProductName, &it.Image, &it.Size, &it.Qty, &it.UnitPrice); err != nil {
			return err
		}
		if pid.Valid {
			it.ProductID = &pid.Int64
		}
		if vid.Valid {
			it.VariantID = &vid.Int64
		}
		byID[it.OrderID].Items = append(byID[it.OrderID].Items, it)
	}
	return rows.Err()
}

type queryer interface {
	QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error)
	QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row
	ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error)
}

func (s *Store) ListOrders(ctx context.Context, customerID int64) ([]*Order, error) {
	q := orderSelect + ` WHERE o.tenant_id=?`
	args := []any{s.tid}
	if customerID > 0 {
		q += ` AND o.customer_id=?`
		args = append(args, customerID)
	} else {
		// El tablero no necesita los entregados/cancelados de hace más de 30 días.
		q += ` AND (o.status NOT IN ('entregado','cancelado') OR o.updated_at > ?)`
		args = append(args, now().Add(-30*24*time.Hour))
	}
	q += ` ORDER BY o.position, o.id DESC`
	rows, err := s.DB.QueryContext(ctx, q, args...)
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

func (s *Store) GetOrder(ctx context.Context, id int64) (*Order, error) {
	o, err := scanOrder(s.DB.QueryRowContext(ctx, orderSelect+` WHERE o.id=? AND o.tenant_id=?`, id, s.tid))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return o, s.loadItems(ctx, s.DB, []*Order{o})
}

// CreateOrder inserta el pedido con sus ítems. Si el estado reserva stock, lo descuenta.
func (s *Store) CreateOrder(ctx context.Context, o *Order) error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if o.Status == "" {
		o.Status = "consulta"
	}
	// La clienta tiene que ser de esta empresa (los ids son globales).
	var one int
	if err := tx.QueryRowContext(ctx, `SELECT 1 FROM customers WHERE id=? AND tenant_id=?`, o.CustomerID, s.tid).Scan(&one); err != nil {
		return fmt.Errorf("cliente %d: %w", o.CustomerID, ErrNotFound)
	}
	o.Total = 0
	for _, it := range o.Items {
		o.Total += float64(it.Qty) * it.UnitPrice
	}
	res, err := tx.ExecContext(ctx, `INSERT INTO orders(tenant_id,customer_id,status,total,notes,source,customer_image,match_confidence,
		location_lat,location_lng,location_text,position,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
		s.tid, o.CustomerID, o.Status, o.Total, o.Notes, o.Source, o.CustomerImage, o.MatchConfidence,
		o.LocationLat, o.LocationLng, o.LocationText, -float64(time.Now().Unix()), now(), now())
	if err != nil {
		return err
	}
	o.ID, _ = res.LastInsertId()
	for i := range o.Items {
		it := &o.Items[i]
		it.OrderID = o.ID
		if err := s.checkItem(ctx, tx, it); err != nil {
			return err
		}
		r, err := tx.ExecContext(ctx, `INSERT INTO order_items(tenant_id,order_id,product_id,variant_id,product_code,product_name,image,size,qty,unit_price)
			VALUES(?,?,?,?,?,?,?,?,?,?)`, s.tid, o.ID, it.ProductID, it.VariantID, it.ProductCode, it.ProductName, it.Image, it.Size, it.Qty, it.UnitPrice)
		if err != nil {
			return err
		}
		it.ID, _ = r.LastInsertId()
	}
	if reservedStatuses[o.Status] {
		if err := s.reserveStock(ctx, tx, o.ID, -1); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// checkItem: el producto y la talla de un ítem tienen que ser de esta empresa.
func (s *Store) checkItem(ctx context.Context, tx *sql.Tx, it *OrderItem) error {
	var one int
	if it.ProductID != nil {
		if err := tx.QueryRowContext(ctx, `SELECT 1 FROM products WHERE id=? AND tenant_id=?`, *it.ProductID, s.tid).Scan(&one); err != nil {
			return fmt.Errorf("producto %d: %w", *it.ProductID, ErrNotFound)
		}
	}
	if it.VariantID != nil {
		if err := tx.QueryRowContext(ctx, `SELECT 1 FROM product_variants WHERE id=? AND tenant_id=?`, *it.VariantID, s.tid).Scan(&one); err != nil {
			return fmt.Errorf("talla %d: %w", *it.VariantID, ErrNotFound)
		}
	}
	return nil
}

// reserveStock mueve el stock de los ítems del pedido: sign=-1 descuenta, +1 devuelve.
func (s *Store) reserveStock(ctx context.Context, tx *sql.Tx, orderID int64, sign int) error {
	rows, err := tx.QueryContext(ctx, `SELECT variant_id, qty, product_name, size FROM order_items
		WHERE order_id=? AND tenant_id=? AND variant_id IS NOT NULL`, orderID, s.tid)
	if err != nil {
		return err
	}
	type line struct {
		vid        int64
		qty        int
		name, size string
	}
	var lines []line
	for rows.Next() {
		var l line
		if err := rows.Scan(&l.vid, &l.qty, &l.name, &l.size); err != nil {
			rows.Close()
			return err
		}
		lines = append(lines, l)
	}
	rows.Close()
	if sign < 0 {
		// Al descontar el físico la reserva temporal ya cumplió su función.
		if _, err := tx.ExecContext(ctx, `DELETE FROM stock_reservations WHERE order_id=? AND tenant_id=?`, orderID, s.tid); err != nil {
			return err
		}
	}
	for _, l := range lines {
		if sign < 0 {
			res, err := tx.ExecContext(ctx, `UPDATE product_variants SET stock=stock-? WHERE id=? AND tenant_id=? AND stock>=?`, l.qty, l.vid, s.tid, l.qty)
			if err != nil {
				return err
			}
			if n, _ := res.RowsAffected(); n == 0 {
				return fmt.Errorf("%w: %s talla %s", ErrNoStock, l.name, l.size)
			}
		} else if _, err := tx.ExecContext(ctx, `UPDATE product_variants SET stock=stock+? WHERE id=? AND tenant_id=?`, l.qty, l.vid, s.tid); err != nil {
			return err
		}
	}
	reserved := 0
	if sign < 0 {
		reserved = 1
	}
	_, err = tx.ExecContext(ctx, `UPDATE orders SET stock_reserved=? WHERE id=? AND tenant_id=?`, reserved, orderID, s.tid)
	return err
}

// UpdateOrderStatus cambia la columna del pedido y ajusta el stock según corresponda.
func (s *Store) UpdateOrderStatus(ctx context.Context, id int64, status string, position *float64) (prev string, err error) {
	if !ValidStatus(status) {
		return "", fmt.Errorf("estado inválido: %s", status)
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return "", err
	}
	defer tx.Rollback()
	var reserved int
	var custID int64
	if err := tx.QueryRowContext(ctx, `SELECT status, stock_reserved, customer_id FROM orders WHERE id=? AND tenant_id=?`, id, s.tid).Scan(&prev, &reserved, &custID); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return "", ErrNotFound
		}
		return "", err
	}
	want := reservedStatuses[status]
	if want && reserved == 0 {
		if err := s.reserveStock(ctx, tx, id, -1); err != nil {
			return prev, err
		}
	} else if !want && reserved == 1 {
		if err := s.reserveStock(ctx, tx, id, +1); err != nil {
			return prev, err
		}
	}
	if position != nil {
		_, err = tx.ExecContext(ctx, `UPDATE orders SET status=?, position=?, updated_at=? WHERE id=? AND tenant_id=?`, status, *position, now(), id, s.tid)
	} else {
		_, err = tx.ExecContext(ctx, `UPDATE orders SET status=?, updated_at=? WHERE id=? AND tenant_id=?`, status, now(), id, s.tid)
	}
	if err != nil {
		return prev, err
	}
	if prev != status { // línea de tiempo de la clienta (store/crm.go)
		if err := s.registrar(ctx, tx, custID, "estado", fmt.Sprintf("Pedido #%d: %s → %s", id, EstadoLabel[prev], EstadoLabel[status]), id); err != nil {
			return prev, err
		}
	}
	return prev, tx.Commit()
}

func (s *Store) UpdateOrderNotes(ctx context.Context, id int64, notes string) error {
	_, err := s.DB.ExecContext(ctx, `UPDATE orders SET notes=?, updated_at=? WHERE id=? AND tenant_id=?`, notes, now(), id, s.tid)
	return err
}

func (s *Store) SetOrderLocation(ctx context.Context, id int64, lat, lng *float64, text string) error {
	_, err := s.DB.ExecContext(ctx, `UPDATE orders SET location_lat=?, location_lng=?, location_text=?, updated_at=? WHERE id=? AND tenant_id=?`,
		lat, lng, text, now(), id, s.tid)
	return err
}

func (s *Store) DeleteOrder(ctx context.Context, id int64) error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var reserved int
	if err := tx.QueryRowContext(ctx, `SELECT stock_reserved FROM orders WHERE id=? AND tenant_id=?`, id, s.tid).Scan(&reserved); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return ErrNotFound
		}
		return err
	}
	if reserved == 1 {
		if err := s.reserveStock(ctx, tx, id, +1); err != nil {
			return err
		}
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM orders WHERE id=? AND tenant_id=?`, id, s.tid); err != nil {
		return err
	}
	return tx.Commit()
}

type Stats struct {
	ByStatus      map[string]int `json:"by_status"`
	SalesMonth    float64        `json:"sales_month"`
	Customers     int            `json:"customers"`
	LowStock      int            `json:"low_stock"`
	OpenInquiries int            `json:"open_inquiries"`
}

func (s *Store) Stats(ctx context.Context) (*Stats, error) {
	st := &Stats{ByStatus: map[string]int{}}
	rows, err := s.DB.QueryContext(ctx, `SELECT status, count(*) FROM orders WHERE tenant_id=? GROUP BY status`, s.tid)
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
		st.ByStatus[k] = n
	}
	rows.Close()
	t := now()
	monthStart := time.Date(t.Year(), t.Month(), 1, 0, 0, 0, 0, time.UTC)
	_ = s.DB.QueryRowContext(ctx, `SELECT coalesce(sum(total),0) FROM orders WHERE tenant_id=? AND stock_reserved=1
		AND created_at >= ?`, s.tid, monthStart).Scan(&st.SalesMonth)
	_ = s.DB.QueryRowContext(ctx, `SELECT count(*) FROM customers WHERE tenant_id=?`, s.tid).Scan(&st.Customers)
	_ = s.DB.QueryRowContext(ctx, `SELECT count(*) FROM product_variants v JOIN products p ON p.id=v.product_id AND p.tenant_id=v.tenant_id
		WHERE v.tenant_id=? AND p.active=1 AND v.stock<=1`, s.tid).Scan(&st.LowStock)
	st.OpenInquiries = st.ByStatus["consulta"]
	return st, nil
}

// ReplaceOrderItems reemplaza los ítems de un pedido que aún no reservó stock y recalcula el total.
func (s *Store) ReplaceOrderItems(ctx context.Context, orderID int64, items []OrderItem) error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var reserved int
	if err := tx.QueryRowContext(ctx, `SELECT stock_reserved FROM orders WHERE id=? AND tenant_id=?`, orderID, s.tid).Scan(&reserved); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return ErrNotFound
		}
		return err
	}
	if reserved == 1 {
		return errors.New("el pedido ya tiene stock reservado")
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM order_items WHERE order_id=? AND tenant_id=?`, orderID, s.tid); err != nil {
		return err
	}
	total := 0.0
	for _, it := range items {
		total += float64(it.Qty) * it.UnitPrice
		if err := s.checkItem(ctx, tx, &it); err != nil {
			return err
		}
		if _, err := tx.ExecContext(ctx, `INSERT INTO order_items(tenant_id,order_id,product_id,variant_id,product_code,product_name,image,size,qty,unit_price)
			VALUES(?,?,?,?,?,?,?,?,?,?)`, s.tid, orderID, it.ProductID, it.VariantID, it.ProductCode, it.ProductName, it.Image, it.Size, it.Qty, it.UnitPrice); err != nil {
			return err
		}
	}
	if _, err := tx.ExecContext(ctx, `UPDATE orders SET total=?, updated_at=? WHERE id=? AND tenant_id=?`, total, now(), orderID, s.tid); err != nil {
		return err
	}
	return tx.Commit()
}
