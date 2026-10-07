package store

import (
	"context"
	"database/sql"
	"errors"
	"strings"
	"time"
)

type Variant struct {
	ID        int64  `json:"id"`
	ProductID int64  `json:"product_id"`
	Size      string `json:"size"`
	Stock     int    `json:"stock"`    // físico
	Reserved  int    `json:"reserved"` // reservas vigentes de clientas que están confirmando
}

// Available es lo que se puede ofrecer ahora mismo: stock físico menos reservas vigentes.
func (v Variant) Available() int {
	if n := v.Stock - v.Reserved; n > 0 {
		return n
	}
	return 0
}

type Product struct {
	ID          int64     `json:"id"`
	Code        string    `json:"code"`
	Name        string    `json:"name"`
	Description string    `json:"description"`
	Category    string    `json:"category"`
	Color       string    `json:"color"`
	Price       float64   `json:"price"`
	Image       string    `json:"image"`
	AITags      string    `json:"ai_tags"`
	Active      bool      `json:"active"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
	Variants    []Variant `json:"variants"`
}

// TotalStock suma el stock físico de todas las tallas.
func (p *Product) TotalStock() int {
	n := 0
	for _, v := range p.Variants {
		n += v.Stock
	}
	return n
}

// TotalAvailable suma lo que se puede ofrecer ahora (físico menos reservas vigentes).
func (p *Product) TotalAvailable() int {
	n := 0
	for _, v := range p.Variants {
		n += v.Available()
	}
	return n
}

// VariantBySize busca una talla sin distinguir mayúsculas.
func (p *Product) VariantBySize(size string) *Variant {
	size = strings.TrimSpace(strings.ToUpper(size))
	for i := range p.Variants {
		if strings.ToUpper(p.Variants[i].Size) == size {
			return &p.Variants[i]
		}
	}
	return nil
}

const productCols = `id, code, name, description, category, color, price, image, ai_tags, active, created_at, updated_at`

func scanProduct(sc interface{ Scan(...any) error }) (*Product, error) {
	p := &Product{}
	var active int
	if err := sc.Scan(&p.ID, &p.Code, &p.Name, &p.Description, &p.Category, &p.Color, &p.Price,
		&p.Image, &p.AITags, &active, &p.CreatedAt, &p.UpdatedAt); err != nil {
		return nil, err
	}
	p.Active = active == 1
	return p, nil
}

func (s *Store) loadVariants(ctx context.Context, products []*Product) error {
	if len(products) == 0 {
		return nil
	}
	byID := map[int64]*Product{}
	for _, p := range products {
		p.Variants = []Variant{}
		byID[p.ID] = p
	}
	// Solo las tallas de los productos pedidos, y siempre dentro de la empresa.
	ids := make([]any, 0, len(products)+2)
	ids = append(ids, now(), s.tid)
	for _, p := range products {
		ids = append(ids, p.ID)
	}
	rows, err := s.DB.QueryContext(ctx, `SELECT v.id, v.product_id, v.size, v.stock,
		COALESCE((SELECT SUM(r.qty) FROM stock_reservations r WHERE r.variant_id=v.id AND r.tenant_id=v.tenant_id AND r.expires_at>?), 0)
		FROM product_variants v WHERE v.tenant_id=? AND v.product_id IN (?`+repeat(",?", len(products)-1)+`) ORDER BY v.id`, ids...)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var v Variant
		if err := rows.Scan(&v.ID, &v.ProductID, &v.Size, &v.Stock, &v.Reserved); err != nil {
			return err
		}
		if p, ok := byID[v.ProductID]; ok {
			p.Variants = append(p.Variants, v)
		}
	}
	return rows.Err()
}

func (s *Store) ListProducts(ctx context.Context, onlyActive bool) ([]*Product, error) {
	q := `SELECT ` + productCols + ` FROM products WHERE tenant_id=?`
	if onlyActive {
		q += ` AND active=1`
	}
	q += ` ORDER BY code`
	rows, err := s.DB.QueryContext(ctx, q, s.tid)
	if err != nil {
		return nil, err
	}
	var out []*Product
	for rows.Next() {
		p, err := scanProduct(rows)
		if err != nil {
			rows.Close()
			return nil, err
		}
		out = append(out, p)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return out, s.loadVariants(ctx, out)
}

func (s *Store) GetProduct(ctx context.Context, id int64) (*Product, error) {
	p, err := scanProduct(s.DB.QueryRowContext(ctx, `SELECT `+productCols+` FROM products WHERE id=? AND tenant_id=?`, id, s.tid))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return p, s.loadVariants(ctx, []*Product{p})
}

func (s *Store) GetProductByCode(ctx context.Context, code string) (*Product, error) {
	p, err := scanProduct(s.DB.QueryRowContext(ctx,
		`SELECT `+productCols+` FROM products WHERE tenant_id=? AND upper(code)=upper(?)`, s.tid, strings.TrimSpace(code)))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return p, s.loadVariants(ctx, []*Product{p})
}

func (s *Store) CountProducts(ctx context.Context) (int, error) {
	var n int
	err := s.DB.QueryRowContext(ctx, `SELECT count(*) FROM products WHERE tenant_id=?`, s.tid).Scan(&n)
	return n, err
}

// SaveProduct crea o actualiza un producto y reemplaza sus tallas.
// Las tallas que ya existen conservan su id para no romper pedidos previos.
func (s *Store) SaveProduct(ctx context.Context, p *Product) error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	active := 0
	if p.Active {
		active = 1
	}
	p.Code = strings.ToUpper(strings.TrimSpace(p.Code))
	if p.ID == 0 {
		res, err := tx.ExecContext(ctx, `INSERT INTO products(tenant_id,code,name,description,category,color,price,image,ai_tags,active,created_at,updated_at)
			VALUES(?,?,?,?,?,?,?,?,?,?,?,?)`, s.tid, p.Code, p.Name, p.Description, p.Category, p.Color, p.Price, p.Image, p.AITags, active, now(), now())
		if err != nil {
			return err
		}
		p.ID, _ = res.LastInsertId()
	} else {
		res, err := tx.ExecContext(ctx, `UPDATE products SET code=?,name=?,description=?,category=?,color=?,price=?,image=?,ai_tags=?,active=?,updated_at=?
			WHERE id=? AND tenant_id=?`, p.Code, p.Name, p.Description, p.Category, p.Color, p.Price, p.Image, p.AITags, active, now(), p.ID, s.tid)
		if err != nil {
			return err
		}
		// MySQL cuenta 0 filas si nada cambió: la existencia se comprueba aparte.
		if n, _ := res.RowsAffected(); n == 0 {
			var one int
			if err := tx.QueryRowContext(ctx, `SELECT 1 FROM products WHERE id=? AND tenant_id=?`, p.ID, s.tid).Scan(&one); err != nil {
				return ErrNotFound
			}
		}
	}
	keep := []any{p.ID, s.tid}
	placeholders := []string{}
	for i := range p.Variants {
		v := &p.Variants[i]
		v.Size = strings.ToUpper(strings.TrimSpace(v.Size))
		if v.Size == "" {
			continue
		}
		if v.Stock < 0 {
			v.Stock = 0
		}
		v.ProductID = p.ID
		up := `INSERT INTO product_variants(tenant_id,product_id,size,stock) VALUES(?,?,?,?)
			ON CONFLICT DO UPDATE SET stock=excluded.stock`
		if s.mysql() {
			up = `INSERT INTO product_variants(tenant_id,product_id,size,stock) VALUES(?,?,?,?)
			ON DUPLICATE KEY UPDATE stock=VALUES(stock)`
		}
		if _, err := tx.ExecContext(ctx, up, s.tid, p.ID, v.Size, v.Stock); err != nil {
			return err
		}
		if err := tx.QueryRowContext(ctx, `SELECT id FROM product_variants WHERE product_id=? AND size=? AND tenant_id=?`,
			p.ID, v.Size, s.tid).Scan(&v.ID); err != nil {
			return err
		}
		keep = append(keep, v.Size)
		placeholders = append(placeholders, "?")
	}
	q := `DELETE FROM product_variants WHERE product_id=? AND tenant_id=?`
	if len(placeholders) > 0 {
		q += ` AND size NOT IN (` + strings.Join(placeholders, ",") + `)`
	}
	if _, err := tx.ExecContext(ctx, q, keep...); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *Store) SetProductImage(ctx context.Context, id int64, image, aiTags string) error {
	_, err := s.DB.ExecContext(ctx, `UPDATE products SET image=?, ai_tags=CASE WHEN ?='' THEN ai_tags ELSE ? END, updated_at=? WHERE id=? AND tenant_id=?`,
		image, aiTags, aiTags, now(), id, s.tid)
	return err
}

func (s *Store) DeleteProduct(ctx context.Context, id int64) error {
	_, err := s.DB.ExecContext(ctx, `DELETE FROM products WHERE id=? AND tenant_id=?`, id, s.tid)
	return err
}
