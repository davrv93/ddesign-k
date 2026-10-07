package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"
)

// Reserve aparta `qty` unidades de la variante para el pedido mientras la clienta confirma.
// Sustituye la reserva anterior del mismo pedido. Devuelve cuántas unidades había disponibles
// (sin contar la propia reserva); si no alcanzan, ErrNoStock.
func (s *Store) Reserve(ctx context.Context, orderID, variantID int64, qty int, ttl time.Duration) (available int, err error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	// El pedido tiene que ser de esta empresa (los ids son globales).
	var one int
	if err := tx.QueryRowContext(ctx, `SELECT 1 FROM orders WHERE id=? AND tenant_id=?`, orderID, s.tid).Scan(&one); err != nil {
		return 0, ErrNotFound
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM stock_reservations WHERE order_id=? AND tenant_id=?`, orderID, s.tid); err != nil {
		return 0, err
	}
	err = tx.QueryRowContext(ctx, `SELECT v.stock - COALESCE((SELECT SUM(r.qty) FROM stock_reservations r
		WHERE r.variant_id=v.id AND r.tenant_id=v.tenant_id AND r.expires_at>?), 0) FROM product_variants v WHERE v.id=? AND v.tenant_id=?`,
		now(), variantID, s.tid).Scan(&available)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, ErrNotFound
	}
	if err != nil {
		return 0, err
	}
	if available < 0 {
		available = 0
	}
	if qty > available {
		return available, fmt.Errorf("%w: disponibles %d", ErrNoStock, available)
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO stock_reservations(tenant_id, order_id, variant_id, qty, expires_at, created_at) VALUES(?,?,?,?,?,?)`,
		s.tid, orderID, variantID, qty, now().Add(ttl), now())
	if err != nil {
		return available, err
	}
	return available, tx.Commit()
}

// ReleaseReservation libera la reserva del pedido (cancelación o cambio de modelo).
func (s *Store) ReleaseReservation(ctx context.Context, orderID int64) error {
	_, err := s.DB.ExecContext(ctx, `DELETE FROM stock_reservations WHERE order_id=? AND tenant_id=?`, orderID, s.tid)
	return err
}

// PurgeExpiredReservations borra las reservas vencidas. Las consultas ya las ignoran por
// expires_at; esto sólo evita que la tabla crezca.
func (s *Store) PurgeExpiredReservations(ctx context.Context) (int64, error) {
	res, err := s.DB.ExecContext(ctx, `DELETE FROM stock_reservations WHERE tenant_id=? AND expires_at<=?`, s.tid, now())
	if err != nil {
		return 0, err
	}
	return res.RowsAffected()
}

// ---------------------------------------------------------------------------
// Sucursales

type Warehouse struct {
	ID      string `json:"id"`
	Name    string `json:"name"`
	Address string `json:"address"`
	Hours   string `json:"hours"`
}

// WarehouseStock es el stock por talla de un código en una sucursal.
type WarehouseStock struct {
	Warehouse
	Sizes map[string]int `json:"sizes"`
}

func (s *Store) ListWarehouses(ctx context.Context) ([]Warehouse, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT id, name, address, hours FROM warehouses WHERE tenant_id=? ORDER BY position, id`, s.tid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Warehouse{}
	for rows.Next() {
		var w Warehouse
		if err := rows.Scan(&w.ID, &w.Name, &w.Address, &w.Hours); err != nil {
			return nil, err
		}
		out = append(out, w)
	}
	return out, rows.Err()
}

// SeedWarehouses carga sucursales y stock sólo si la tabla de sucursales está vacía.
func (s *Store) SeedWarehouses(ctx context.Context, ws []Warehouse, stock map[string]map[string]map[string]int) (bool, error) {
	var n int
	if err := s.DB.QueryRowContext(ctx, `SELECT COUNT(*) FROM warehouses WHERE tenant_id=?`, s.tid).Scan(&n); err != nil {
		return false, err
	}
	if n > 0 {
		return false, nil
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	for i, w := range ws {
		if _, err := tx.ExecContext(ctx, `INSERT INTO warehouses(tenant_id,id,name,address,hours,position) VALUES(?,?,?,?,?,?)`, s.tid, w.ID, w.Name, w.Address, w.Hours, i); err != nil {
			return false, err
		}
	}
	for code, porSucursal := range stock {
		for wid, tallas := range porSucursal {
			for size, qty := range tallas {
				if _, err := tx.ExecContext(ctx, `INSERT INTO warehouse_stock(tenant_id,warehouse_id,product_code,size,qty,updated_at) VALUES(?,?,?,?,?,?)`,
					s.tid, wid, code, size, qty, now()); err != nil {
					return false, err
				}
			}
		}
	}
	return true, tx.Commit()
}

// WarehouseStockByCode devuelve, por código, las sucursales con unidades y sus tallas.
func (s *Store) WarehouseStockByCode(ctx context.Context, codes []string) (map[string][]WarehouseStock, error) {
	out := map[string][]WarehouseStock{}
	if len(codes) == 0 {
		return out, nil
	}
	q := `SELECT ws.product_code, w.id, w.name, w.address, w.hours, ws.size, ws.qty FROM warehouse_stock ws
		JOIN warehouses w ON w.id=ws.warehouse_id AND w.tenant_id=ws.tenant_id
		WHERE ws.tenant_id=? AND ws.qty>0 AND ws.product_code IN (?` + repeat(",?", len(codes)-1) + `)
		ORDER BY ws.product_code, w.position, w.id, ws.size`
	args := make([]any, 0, len(codes)+1)
	args = append(args, s.tid)
	for _, c := range codes {
		args = append(args, c)
	}
	rows, err := s.DB.QueryContext(ctx, q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var code, size string
		var w Warehouse
		var qty int
		if err := rows.Scan(&code, &w.ID, &w.Name, &w.Address, &w.Hours, &size, &qty); err != nil {
			return nil, err
		}
		list := out[code]
		if len(list) == 0 || list[len(list)-1].ID != w.ID {
			list = append(list, WarehouseStock{Warehouse: w, Sizes: map[string]int{}})
		}
		list[len(list)-1].Sizes[size] = qty
		out[code] = list
	}
	return out, rows.Err()
}

func repeat(s string, n int) string {
	out := ""
	for i := 0; i < n; i++ {
		out += s
	}
	return out
}
