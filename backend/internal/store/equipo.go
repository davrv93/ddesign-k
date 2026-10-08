package store

// Equipo de la empresa: usuarios del panel con su rol. Dos roles: «admin» (todo) y «asesora» (atiende clientas,
// pedidos, notas y tareas; no toca ajustes, WhatsApp, equipo ni exportaciones, ni borra pedidos o productos).

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"
)

const (
	RolAdmin   = "admin"
	RolAsesora = "asesora"
)

func ValidRol(r string) bool { return r == RolAdmin || r == RolAsesora }

// Miembro es un usuario tal como lo ve el panel (sin hash de clave).
type Miembro struct {
	ID        int64     `json:"id"`
	Username  string    `json:"username"`
	Name      string    `json:"name"`
	Role      string    `json:"role"`
	Active    bool      `json:"active"`
	CreatedAt time.Time `json:"created_at"`
}

// EsAdmin: los usuarios de antes de los roles (rol vacío u otro) siguen siendo admin; solo «asesora» recorta.
func (m *Miembro) EsAdmin() bool { return m.Role != RolAsesora }

const miembroCols = `id, username, name, role, active, created_at`

func scanMiembro(sc interface{ Scan(...any) error }) (*Miembro, error) {
	m := &Miembro{}
	var active int
	if err := sc.Scan(&m.ID, &m.Username, &m.Name, &m.Role, &active, &m.CreatedAt); err != nil {
		return nil, err
	}
	m.Active = active == 1
	return m, nil
}

func (s *Store) ListMiembros(ctx context.Context) ([]*Miembro, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT `+miembroCols+` FROM users WHERE tenant_id=? ORDER BY active DESC, name, username`, s.tid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*Miembro{}
	for rows.Next() {
		m, err := scanMiembro(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

// MiembroPorUsuario busca el usuario (activo o no) de la empresa por su nombre de usuario.
func (s *Store) MiembroPorUsuario(ctx context.Context, username string) (*Miembro, error) {
	m, err := scanMiembro(s.DB.QueryRowContext(ctx, `SELECT `+miembroCols+` FROM users WHERE tenant_id=? AND username=?`, s.tid, username))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	return m, err
}

func (s *Store) GetMiembro(ctx context.Context, id int64) (*Miembro, error) {
	m, err := scanMiembro(s.DB.QueryRowContext(ctx, `SELECT `+miembroCols+` FROM users WHERE tenant_id=? AND id=?`, s.tid, id))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	return m, err
}

var ErrExiste = errors.New("ya existe un usuario con ese nombre")

// CrearMiembro da de alta a una persona del equipo. A diferencia de UpsertUser (el alta por consola), no pisa a nadie.
func (s *Store) CrearMiembro(ctx context.Context, username, password, name, role string) (*Miembro, error) {
	username = strings.TrimSpace(username)
	if username == "" || len(username) > 60 || strings.ContainsAny(username, " /\\\"'") {
		return nil, errors.New("usuario inválido: sin espacios ni comillas, hasta 60 caracteres")
	}
	if !ValidRol(role) {
		return nil, fmt.Errorf("rol inválido: %s", role)
	}
	if _, err := s.MiembroPorUsuario(ctx, username); err == nil {
		return nil, ErrExiste
	}
	if err := s.UpsertUser(ctx, username, password, recorta(name, 200), role); err != nil {
		return nil, err
	}
	return s.MiembroPorUsuario(ctx, username)
}

// CambioMiembro: nil = no cambia.
type CambioMiembro struct {
	Name     *string `json:"name"`
	Role     *string `json:"role"`
	Active   *bool   `json:"active"`
	Password *string `json:"password"`
}

var ErrUltimaAdmin = errors.New("la empresa se quedaría sin ninguna admin activa")

// UpdateMiembro cambia nombre, rol, estado o clave. Nunca deja a la empresa sin una admin activa.
func (s *Store) UpdateMiembro(ctx context.Context, id int64, c CambioMiembro) (*Miembro, error) {
	m, err := s.GetMiembro(ctx, id)
	if err != nil {
		return nil, err
	}
	if c.Role != nil && !ValidRol(*c.Role) {
		return nil, fmt.Errorf("rol inválido: %s", *c.Role)
	}
	dejaDeSerAdmin := m.Active && m.EsAdmin() && ((c.Role != nil && *c.Role != RolAdmin) || (c.Active != nil && !*c.Active))
	if dejaDeSerAdmin {
		var n int
		if err := s.DB.QueryRowContext(ctx, `SELECT count(*) FROM users WHERE tenant_id=? AND active=1 AND role<>? AND id<>?`,
			s.tid, RolAsesora, id).Scan(&n); err != nil {
			return nil, err
		}
		if n == 0 {
			return nil, ErrUltimaAdmin
		}
	}
	sets, args := []string{}, []any{}
	if c.Name != nil {
		sets, args = append(sets, "name=?"), append(args, recorta(*c.Name, 200))
	}
	if c.Role != nil {
		sets, args = append(sets, "role=?"), append(args, *c.Role)
	}
	if c.Active != nil {
		a := 0
		if *c.Active {
			a = 1
		}
		sets, args = append(sets, "active=?"), append(args, a)
	}
	if c.Password != nil {
		if len(*c.Password) < 8 {
			return nil, errors.New("la clave debe tener al menos 8 caracteres")
		}
		h, err := HashPassword(*c.Password)
		if err != nil {
			return nil, err
		}
		sets, args = append(sets, "password_hash=?"), append(args, h)
	}
	if len(sets) > 0 {
		if _, err := s.DB.ExecContext(ctx, `UPDATE users SET `+strings.Join(sets, ", ")+` WHERE id=? AND tenant_id=?`, append(args, id, s.tid)...); err != nil {
			return nil, err
		}
	}
	return s.GetMiembro(ctx, id)
}
