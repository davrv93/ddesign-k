// Package store guarda el estado del CRM en SQLite.
package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	_ "modernc.org/sqlite"
)

var ErrNotFound = errors.New("no encontrado")

type Store struct {
	DB *sql.DB
}

func Open(dataDir string) (*Store, error) {
	if err := os.MkdirAll(dataDir, 0o755); err != nil {
		return nil, err
	}
	dsn := "file:" + filepath.Join(dataDir, "crm.db") +
		"?_pragma=foreign_keys(1)&_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)&_pragma=synchronous(NORMAL)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	// SQLite admite un solo escritor: una conexión evita "database is locked".
	db.SetMaxOpenConns(1)
	s := &Store{DB: db}
	if err := s.migrate(); err != nil {
		return nil, fmt.Errorf("migración: %w", err)
	}
	return s, nil
}

const schema = `
CREATE TABLE IF NOT EXISTS products (
	id          INTEGER PRIMARY KEY AUTOINCREMENT,
	code        TEXT NOT NULL UNIQUE,
	name        TEXT NOT NULL,
	description TEXT NOT NULL DEFAULT '',
	category    TEXT NOT NULL DEFAULT '',
	color       TEXT NOT NULL DEFAULT '',
	price       REAL NOT NULL DEFAULT 0,
	image       TEXT NOT NULL DEFAULT '',
	ai_tags     TEXT NOT NULL DEFAULT '',
	active      INTEGER NOT NULL DEFAULT 1,
	created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS product_variants (
	id         INTEGER PRIMARY KEY AUTOINCREMENT,
	product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
	size       TEXT NOT NULL,
	stock      INTEGER NOT NULL DEFAULT 0,
	UNIQUE(product_id, size)
);
CREATE TABLE IF NOT EXISTS customers (
	id         INTEGER PRIMARY KEY AUTOINCREMENT,
	jid        TEXT NOT NULL UNIQUE,
	phone      TEXT NOT NULL DEFAULT '',
	name       TEXT NOT NULL DEFAULT '',
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS conversations (
	id              INTEGER PRIMARY KEY AUTOINCREMENT,
	customer_id     INTEGER NOT NULL UNIQUE REFERENCES customers(id) ON DELETE CASCADE,
	state           TEXT NOT NULL DEFAULT '',
	context         TEXT NOT NULL DEFAULT '{}',
	bot_paused      INTEGER NOT NULL DEFAULT 0,
	paused_at       DATETIME,
	unread          INTEGER NOT NULL DEFAULT 0,
	last_message    TEXT NOT NULL DEFAULT '',
	last_message_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS messages (
	id              INTEGER PRIMARY KEY AUTOINCREMENT,
	conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
	wa_id           TEXT NOT NULL DEFAULT '',
	direction       TEXT NOT NULL,
	kind            TEXT NOT NULL DEFAULT 'text',
	body            TEXT NOT NULL DEFAULT '',
	media           TEXT NOT NULL DEFAULT '',
	author          TEXT NOT NULL DEFAULT '',
	status          TEXT NOT NULL DEFAULT '',
	created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_wa ON messages(wa_id);
CREATE TABLE IF NOT EXISTS orders (
	id               INTEGER PRIMARY KEY AUTOINCREMENT,
	customer_id      INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
	status           TEXT NOT NULL DEFAULT 'consulta',
	total            REAL NOT NULL DEFAULT 0,
	notes            TEXT NOT NULL DEFAULT '',
	source           TEXT NOT NULL DEFAULT 'whatsapp',
	customer_image   TEXT NOT NULL DEFAULT '',
	match_confidence REAL NOT NULL DEFAULT 0,
	location_lat     REAL,
	location_lng     REAL,
	location_text    TEXT NOT NULL DEFAULT '',
	stock_reserved   INTEGER NOT NULL DEFAULT 0,
	position         REAL NOT NULL DEFAULT 0,
	created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE TABLE IF NOT EXISTS order_items (
	id           INTEGER PRIMARY KEY AUTOINCREMENT,
	order_id     INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
	product_id   INTEGER REFERENCES products(id) ON DELETE SET NULL,
	variant_id   INTEGER REFERENCES product_variants(id) ON DELETE SET NULL,
	product_code TEXT NOT NULL DEFAULT '',
	product_name TEXT NOT NULL DEFAULT '',
	image        TEXT NOT NULL DEFAULT '',
	size         TEXT NOT NULL DEFAULT '',
	qty          INTEGER NOT NULL DEFAULT 1,
	unit_price   REAL NOT NULL DEFAULT 0
);
-- Reserva temporal de stock mientras la clienta confirma: disponible = stock - reservas vigentes.
CREATE TABLE IF NOT EXISTS stock_reservations (
	id         INTEGER PRIMARY KEY AUTOINCREMENT,
	order_id   INTEGER NOT NULL UNIQUE REFERENCES orders(id) ON DELETE CASCADE,
	variant_id INTEGER NOT NULL REFERENCES product_variants(id) ON DELETE CASCADE,
	qty        INTEGER NOT NULL,
	expires_at DATETIME NOT NULL,
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_reservations_variant ON stock_reservations(variant_id, expires_at);
-- Sucursales físicas y su stock. Va por código (no por product_id) porque el catálogo de 100
-- modelos que maneja el agente no está en products.
CREATE TABLE IF NOT EXISTS warehouses (
	id       TEXT PRIMARY KEY,
	name     TEXT NOT NULL,
	address  TEXT NOT NULL DEFAULT '',
	hours    TEXT NOT NULL DEFAULT '',
	position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS warehouse_stock (
	warehouse_id TEXT NOT NULL REFERENCES warehouses(id) ON DELETE CASCADE,
	product_code TEXT NOT NULL,
	size         TEXT NOT NULL,
	qty          INTEGER NOT NULL DEFAULT 0,
	updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	PRIMARY KEY(warehouse_id, product_code, size)
);
CREATE INDEX IF NOT EXISTS idx_wstock_code ON warehouse_stock(product_code);
CREATE TABLE IF NOT EXISTS settings (
	key   TEXT PRIMARY KEY,
	value TEXT NOT NULL
);
-- Recordatorios de seguimiento enviados a una conversación en silencio (stopping agent). El tope lo
-- aplica la Capa de Juicio; aquí se cuenta cuántos lleva. Se borra cuando la clienta vuelve a escribir.
CREATE TABLE IF NOT EXISTS followups (
	conversation_id INTEGER PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
	count           INTEGER NOT NULL DEFAULT 0,
	last_at         DATETIME
);
-- Memoria de criterio (case-based reasoning): qué se decidió en cada turno, por qué y con qué resultado.
-- Guarda tanto las decisiones de la Capa de Juicio (autor «bot») como las intervenciones humanas
-- (autor «asesora»): es el dato que alimentará el criterio del bot y la medida de discernimiento.
CREATE TABLE IF NOT EXISTS decisiones (
	id              INTEGER PRIMARY KEY AUTOINCREMENT,
	conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
	etapa           TEXT NOT NULL DEFAULT '',
	intent          TEXT NOT NULL DEFAULT '',
	caso            TEXT NOT NULL DEFAULT '',
	decision        TEXT NOT NULL DEFAULT '',
	razon           TEXT NOT NULL DEFAULT '',
	resultado       TEXT NOT NULL DEFAULT '',
	autor           TEXT NOT NULL DEFAULT '',
	created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_decisiones_intent ON decisiones(intent, id);
CREATE INDEX IF NOT EXISTS idx_decisiones_conv ON decisiones(conversation_id, id);
-- Pares para alineación (DPO): cuando una persona responde en un chat, se guarda lo último que dijo la
-- clienta, lo que había propuesto el bot y lo que escribió la persona. Es el dato con el que el bot
-- aprende el criterio de la tienda, no el genérico.
CREATE TABLE IF NOT EXISTS pares_dpo (
	id              INTEGER PRIMARY KEY AUTOINCREMENT,
	conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
	contexto        TEXT NOT NULL DEFAULT '',
	respuesta_bot   TEXT NOT NULL DEFAULT '',
	respuesta_humana TEXT NOT NULL DEFAULT '',
	created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pares_conv ON pares_dpo(conversation_id, id);
-- Vínculo con Kommo CRM (internal/kommo): qué lead y qué contacto de Kommo corresponden a cada conversación, para
-- no duplicarlos. clave = «wa:<conversation_id>» (WhatsApp) o «web:<sesión>» (chat web). estado guarda lo último que
-- se mandó (estado del embudo, precio, campos, etiquetas) para enviar solo lo que cambió.
CREATE TABLE IF NOT EXISTS kommo_vinculos (
	clave           TEXT PRIMARY KEY,
	canal           TEXT NOT NULL DEFAULT '',
	conversation_id INTEGER NOT NULL DEFAULT 0,
	lead_id         INTEGER NOT NULL DEFAULT 0,
	contact_id      INTEGER NOT NULL DEFAULT 0,
	estado          TEXT NOT NULL DEFAULT '{}',
	updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_kommo_conv ON kommo_vinculos(conversation_id);
`

func (s *Store) migrate() error {
	_, err := s.DB.Exec(schema)
	return err
}

func now() time.Time { return time.Now().UTC() }

// Setting devuelve el valor de un ajuste o def si no existe.
func (s *Store) Setting(ctx context.Context, key, def string) string {
	var v string
	if err := s.DB.QueryRowContext(ctx, `SELECT value FROM settings WHERE key=?`, key).Scan(&v); err != nil {
		return def
	}
	return v
}

func (s *Store) SetSetting(ctx context.Context, key, value string) error {
	_, err := s.DB.ExecContext(ctx, `INSERT INTO settings(key,value) VALUES(?,?)
		ON CONFLICT(key) DO UPDATE SET value=excluded.value`, key, value)
	return err
}

func (s *Store) Settings(ctx context.Context) (map[string]string, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT key, value FROM settings`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]string{}
	for rows.Next() {
		var k, v string
		if err := rows.Scan(&k, &v); err != nil {
			return nil, err
		}
		out[k] = v
	}
	return out, rows.Err()
}
