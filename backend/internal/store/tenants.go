package store

import (
	"context"
	"crypto/pbkdf2"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"database/sql"
	"encoding/base64"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	"golang.org/x/crypto/bcrypt"
)

// Tenant es una empresa de JMD Ventas. Su slug es la ruta: /jmdventas/<slug>/.
type Tenant struct {
	ID        int64     `json:"id"`
	Slug      string    `json:"slug"`
	Name      string    `json:"name"`
	Currency  string    `json:"currency"`
	WhatsApp  string    `json:"whatsapp"`
	Active    bool      `json:"active"`
	CreatedAt time.Time `json:"created_at"`
}

var reSlug = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{1,39}$`)

// Rutas que el panel o el backend ya usan en el primer segmento: no pueden ser el slug de una empresa.
var reservedSlugs = map[string]bool{
	"api": true, "media": true, "build": true, "assets": true, "healthz": true, "webhook": true, "login": true,
	"catalogo": true, "admin": true, "static": true, "default": true, "favicon.svg": true, "robots.txt": true,
}

// ValidSlug dice si el texto sirve como ruta de una empresa (minúsculas, cifras y guiones; 2 a 40).
func ValidSlug(slug string) bool { return reSlug.MatchString(slug) && !reservedSlugs[slug] }

const tenantCols = `id, slug, name, currency, whatsapp, active, created_at`

func scanTenant(sc interface{ Scan(...any) error }) (*Tenant, error) {
	t := &Tenant{}
	var active int
	if err := sc.Scan(&t.ID, &t.Slug, &t.Name, &t.Currency, &t.WhatsApp, &active, &t.CreatedAt); err != nil {
		return nil, err
	}
	t.Active = active == 1
	return t, nil
}

// TenantBySlug busca una empresa por su ruta (no depende de la empresa del Store).
func (s *Store) TenantBySlug(ctx context.Context, slug string) (*Tenant, error) {
	t, err := scanTenant(s.DB.QueryRowContext(ctx, `SELECT `+tenantCols+` FROM tenants WHERE slug=?`, slug))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	return t, err
}

// Tenant es la empresa a la que está ligado el Store.
func (s *Store) Tenant(ctx context.Context) (*Tenant, error) {
	t, err := scanTenant(s.DB.QueryRowContext(ctx, `SELECT `+tenantCols+` FROM tenants WHERE id=?`, s.tid))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	return t, err
}

// Tenants lista las empresas activas (para los trabajos de fondo, que recorren una por una).
func (s *Store) Tenants(ctx context.Context) ([]*Tenant, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT `+tenantCols+` FROM tenants WHERE active=1 ORDER BY id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*Tenant
	for rows.Next() {
		t, err := scanTenant(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, t)
	}
	return out, rows.Err()
}

// UpsertTenant crea la empresa o actualiza su nombre, moneda y WhatsApp. Es el alta: idempotente por slug.
func (s *Store) UpsertTenant(ctx context.Context, t *Tenant) error {
	t.Slug = strings.ToLower(strings.TrimSpace(t.Slug))
	if !ValidSlug(t.Slug) {
		return fmt.Errorf("slug inválido %q: minúsculas, cifras y guiones (2 a 40), y no una ruta reservada", t.Slug)
	}
	if strings.TrimSpace(t.Name) == "" {
		return errors.New("el nombre de la empresa es obligatorio")
	}
	if t.Currency == "" {
		t.Currency = "S/"
	}
	q := `INSERT INTO tenants(slug, name, currency, whatsapp, active, created_at) VALUES(?,?,?,?,1,?)
		ON CONFLICT DO UPDATE SET name=excluded.name, currency=excluded.currency, whatsapp=excluded.whatsapp, active=1`
	if s.mysql() {
		q = `INSERT INTO tenants(slug, name, currency, whatsapp, active, created_at) VALUES(?,?,?,?,1,?)
		ON DUPLICATE KEY UPDATE name=VALUES(name), currency=VALUES(currency), whatsapp=VALUES(whatsapp), active=1`
	}
	if _, err := s.DB.ExecContext(ctx, q, t.Slug, t.Name, t.Currency, t.WhatsApp, now()); err != nil {
		return err
	}
	got, err := s.TenantBySlug(ctx, t.Slug)
	if err != nil {
		return err
	}
	*t = *got
	return nil
}

// ---------------------------------------------------------------------------
// Usuarios del panel (por empresa)

type User struct {
	ID       int64  `json:"id"`
	TenantID int64  `json:"tenant_id"`
	Username string `json:"username"`
	Name     string `json:"name"`
	Role     string `json:"role"`
}

// UpsertUser crea o actualiza (clave incluida) un usuario de la empresa del Store.
func (s *Store) UpsertUser(ctx context.Context, username, password, name, role string) error {
	username = strings.TrimSpace(username)
	if username == "" || len(password) < 8 {
		return errors.New("usuario obligatorio y clave de al menos 8 caracteres")
	}
	if role == "" {
		role = "admin"
	}
	h, err := HashPassword(password)
	if err != nil {
		return err
	}
	q := `INSERT INTO users(tenant_id, username, password_hash, name, role, active, created_at) VALUES(?,?,?,?,?,1,?)
		ON CONFLICT DO UPDATE SET password_hash=excluded.password_hash, name=excluded.name, role=excluded.role, active=1`
	if s.mysql() {
		q = `INSERT INTO users(tenant_id, username, password_hash, name, role, active, created_at) VALUES(?,?,?,?,?,1,?)
		ON DUPLICATE KEY UPDATE password_hash=VALUES(password_hash), name=VALUES(name), role=VALUES(role), active=1`
	}
	_, err = s.DB.ExecContext(ctx, q, s.tid, username, h, name, role, now())
	return err
}

// CountUsers cuenta los usuarios activos de la empresa.
func (s *Store) CountUsers(ctx context.Context) (int, error) {
	var n int
	err := s.DB.QueryRowContext(ctx, `SELECT count(*) FROM users WHERE tenant_id=? AND active=1`, s.tid).Scan(&n)
	return n, err
}

// CheckUser valida usuario y clave dentro de la empresa del Store.
func (s *Store) CheckUser(ctx context.Context, username, password string) (*User, error) {
	u := &User{}
	var hash string
	err := s.DB.QueryRowContext(ctx, `SELECT id, tenant_id, username, name, role, password_hash FROM users
		WHERE tenant_id=? AND username=? AND active=1`, s.tid, strings.TrimSpace(username)).
		Scan(&u.ID, &u.TenantID, &u.Username, &u.Name, &u.Role, &hash)
	if err != nil {
		// Mismo coste que una clave errónea: no se distingue un usuario inexistente.
		_ = CheckPassword("pbkdf2-sha256$210000$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", password)
		return nil, errors.New("usuario o contraseña incorrectos")
	}
	if !CheckPassword(hash, password) {
		return nil, errors.New("usuario o contraseña incorrectos")
	}
	return u, nil
}

const pbkdf2Iter = 210000

// HashPassword: PBKDF2-SHA256 con sal aleatoria («pbkdf2-sha256$iter$sal$hash», base64 sin relleno).
func HashPassword(password string) (string, error) {
	salt := make([]byte, 16)
	if _, err := rand.Read(salt); err != nil {
		return "", err
	}
	key, err := pbkdf2.Key(sha256.New, password, salt, pbkdf2Iter, 32)
	if err != nil {
		return "", err
	}
	enc := base64.RawStdEncoding
	return fmt.Sprintf("pbkdf2-sha256$%d$%s$%s", pbkdf2Iter, enc.EncodeToString(salt), enc.EncodeToString(key)), nil
}

func CheckPassword(hash, password string) bool {
	// bcrypt: los usuarios que vienen de la SQLite de /baruka/ (tabla users con pass_hash, internal/auth de esa rama).
	if strings.HasPrefix(hash, "$2a$") || strings.HasPrefix(hash, "$2b$") || strings.HasPrefix(hash, "$2y$") {
		return bcrypt.CompareHashAndPassword([]byte(hash), []byte(password)) == nil
	}
	parts := strings.Split(hash, "$")
	if len(parts) != 4 || parts[0] != "pbkdf2-sha256" {
		return false
	}
	iter, err := strconv.Atoi(parts[1])
	if err != nil || iter < 1000 {
		return false
	}
	enc := base64.RawStdEncoding
	salt, err1 := enc.DecodeString(parts[2])
	want, err2 := enc.DecodeString(parts[3])
	if err1 != nil || err2 != nil {
		return false
	}
	got, err := pbkdf2.Key(sha256.New, password, salt, iter, len(want))
	if err != nil {
		return false
	}
	return subtle.ConstantTimeCompare(got, want) == 1
}
