// Package store guarda el estado del CRM en SQLite (una tienda, el despliegue de /baruka/) o en MariaDB
// (JMD Ventas, multiempresa).
//
// Multiempresa: todas las tablas de negocio llevan tenant_id. Un *Store siempre está ligado a una empresa
// (ForTenant) y cada consulta filtra por ella: no hay método que lea o escriba filas de otra empresa. El Store
// que devuelve OpenMySQL no pertenece a ninguna (tid 0): sin ForTenant no ve nada. El de SQLite (Open) es la
// empresa 1, la tienda de siempre, para que el despliegue de una sola tienda siga igual.
package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	_ "github.com/go-sql-driver/mysql"
	_ "modernc.org/sqlite"
)

var ErrNotFound = errors.New("no encontrado")

const (
	DialectSQLite = "sqlite"
	DialectMySQL  = "mysql"
)

type Store struct {
	DB      *sql.DB
	Dialect string
	tid     int64 // empresa a la que está ligado este Store; 0 = ninguna (no ve nada)
}

// ForTenant devuelve una copia del Store ligada a la empresa id. Comparte la conexión.
func (s *Store) ForTenant(id int64) *Store {
	c := *s
	c.tid = id
	return &c
}

// TenantID es la empresa a la que está ligado el Store.
func (s *Store) TenantID() int64 { return s.tid }

func (s *Store) mysql() bool { return s.Dialect == DialectMySQL }

// Open abre (o crea) la SQLite de una sola tienda en dataDir/crm.db. El Store queda ligado a la empresa 1.
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
	s := &Store{DB: db, Dialect: DialectSQLite, tid: 1}
	if err := s.migrateSQLite(); err != nil {
		return nil, fmt.Errorf("migración: %w", err)
	}
	return s, nil
}

// OpenMySQL abre MariaDB/MySQL con el DSN de go-sql-driver (parseTime=true y loc=UTC se añaden si faltan) y
// crea las tablas que falten. El Store devuelto no pertenece a ninguna empresa: usa ForTenant.
func OpenMySQL(dsn string) (*Store, error) {
	dsn = withParams(dsn, map[string]string{"parseTime": "true", "loc": "UTC", "charset": "utf8mb4", "multiStatements": "false",
		// Filas encontradas, no cambiadas (como SQLite): un UPDATE que no cambia nada no es «no existe».
		"clientFoundRows": "true"})
	db, err := sql.Open("mysql", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(10)
	db.SetMaxIdleConns(4)
	db.SetConnMaxLifetime(30 * time.Minute)
	var lastErr error
	for i := 0; i < 30; i++ { // MariaDB puede tardar en aceptar conexiones al arrancar el stack
		if lastErr = db.Ping(); lastErr == nil {
			break
		}
		time.Sleep(2 * time.Second)
	}
	if lastErr != nil {
		return nil, fmt.Errorf("mariadb: %w", lastErr)
	}
	s := &Store{DB: db, Dialect: DialectMySQL}
	if err := s.migrateMySQL(); err != nil {
		return nil, fmt.Errorf("migración: %w", err)
	}
	return s, nil
}

func withParams(dsn string, params map[string]string) string {
	base, query, _ := strings.Cut(dsn, "?")
	have := map[string]bool{}
	for _, kv := range strings.Split(query, "&") {
		if k, _, ok := strings.Cut(kv, "="); ok {
			have[k] = true
		}
	}
	parts := []string{}
	if query != "" {
		parts = append(parts, query)
	}
	for _, k := range []string{"parseTime", "loc", "charset", "multiStatements", "clientFoundRows"} {
		if v, ok := params[k]; ok && !have[k] {
			parts = append(parts, k+"="+v)
		}
	}
	return base + "?" + strings.Join(parts, "&")
}

// TenantTables son las tablas de negocio, en orden de dependencia (las hijas después). Todas llevan tenant_id.
var TenantTables = []string{
	"products", "product_variants", "customers", "conversations", "messages", "orders", "order_items",
	"stock_reservations", "warehouses", "warehouse_stock", "settings", "followups", "decisiones", "pares_dpo",
	"kommo_vinculos", "notas", "tareas", "actividad", "cliente_etiquetas", "users",
}

const schemaSQLite = `
CREATE TABLE IF NOT EXISTS tenants (
	id         INTEGER PRIMARY KEY AUTOINCREMENT,
	slug       TEXT NOT NULL UNIQUE,
	name       TEXT NOT NULL,
	currency   TEXT NOT NULL DEFAULT 'S/',
	whatsapp   TEXT NOT NULL DEFAULT '',
	active     INTEGER NOT NULL DEFAULT 1,
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS users (
	id            INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id     INTEGER NOT NULL DEFAULT 1,
	username      TEXT NOT NULL,
	password_hash TEXT NOT NULL,
	name          TEXT NOT NULL DEFAULT '',
	role          TEXT NOT NULL DEFAULT 'admin',
	active        INTEGER NOT NULL DEFAULT 1,
	created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	UNIQUE(tenant_id, username)
);
CREATE TABLE IF NOT EXISTS products (
	id          INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id   INTEGER NOT NULL DEFAULT 1,
	code        TEXT NOT NULL,
	name        TEXT NOT NULL,
	description TEXT NOT NULL DEFAULT '',
	category    TEXT NOT NULL DEFAULT '',
	color       TEXT NOT NULL DEFAULT '',
	price       REAL NOT NULL DEFAULT 0,
	image       TEXT NOT NULL DEFAULT '',
	ai_tags     TEXT NOT NULL DEFAULT '',
	active      INTEGER NOT NULL DEFAULT 1,
	created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	UNIQUE(tenant_id, code)
);
CREATE TABLE IF NOT EXISTS product_variants (
	id         INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id  INTEGER NOT NULL DEFAULT 1,
	product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
	size       TEXT NOT NULL,
	stock      INTEGER NOT NULL DEFAULT 0,
	UNIQUE(product_id, size)
);
CREATE TABLE IF NOT EXISTS customers (
	id         INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id  INTEGER NOT NULL DEFAULT 1,
	jid        TEXT NOT NULL,
	phone      TEXT NOT NULL DEFAULT '',
	name       TEXT NOT NULL DEFAULT '',
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	UNIQUE(tenant_id, jid)
);
CREATE TABLE IF NOT EXISTS conversations (
	id              INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id       INTEGER NOT NULL DEFAULT 1,
	customer_id     INTEGER NOT NULL UNIQUE REFERENCES customers(id) ON DELETE CASCADE,
	state           TEXT NOT NULL DEFAULT '',
	context         TEXT NOT NULL DEFAULT '{}',
	bot_paused      INTEGER NOT NULL DEFAULT 0,
	paused_at       DATETIME,
	unread          INTEGER NOT NULL DEFAULT 0,
	last_message    TEXT NOT NULL DEFAULT '',
	last_message_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	agent_version   TEXT NOT NULL DEFAULT '',
	agent_last      TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS messages (
	id              INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id       INTEGER NOT NULL DEFAULT 1,
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
CREATE TABLE IF NOT EXISTS orders (
	id               INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id        INTEGER NOT NULL DEFAULT 1,
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
CREATE TABLE IF NOT EXISTS order_items (
	id           INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id    INTEGER NOT NULL DEFAULT 1,
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
	tenant_id  INTEGER NOT NULL DEFAULT 1,
	order_id   INTEGER NOT NULL UNIQUE REFERENCES orders(id) ON DELETE CASCADE,
	variant_id INTEGER NOT NULL REFERENCES product_variants(id) ON DELETE CASCADE,
	qty        INTEGER NOT NULL,
	expires_at DATETIME NOT NULL,
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Sucursales físicas y su stock. Va por código (no por product_id) porque el catálogo de 100
-- modelos que maneja el agente no está en products.
CREATE TABLE IF NOT EXISTS warehouses (
	tenant_id INTEGER NOT NULL DEFAULT 1,
	id        TEXT NOT NULL,
	name      TEXT NOT NULL,
	address   TEXT NOT NULL DEFAULT '',
	hours     TEXT NOT NULL DEFAULT '',
	position  INTEGER NOT NULL DEFAULT 0,
	PRIMARY KEY(tenant_id, id)
);
CREATE TABLE IF NOT EXISTS warehouse_stock (
	tenant_id    INTEGER NOT NULL DEFAULT 1,
	warehouse_id TEXT NOT NULL,
	product_code TEXT NOT NULL,
	size         TEXT NOT NULL,
	qty          INTEGER NOT NULL DEFAULT 0,
	updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	PRIMARY KEY(tenant_id, warehouse_id, product_code, size)
);
CREATE TABLE IF NOT EXISTS settings (
	tenant_id INTEGER NOT NULL DEFAULT 1,
	key       TEXT NOT NULL,
	value     TEXT NOT NULL,
	PRIMARY KEY(tenant_id, key)
);
-- Recordatorios de seguimiento enviados a una conversación en silencio (stopping agent). El tope lo
-- aplica la Capa de Juicio; aquí se cuenta cuántos lleva. Se borra cuando la clienta vuelve a escribir.
CREATE TABLE IF NOT EXISTS followups (
	conversation_id INTEGER PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
	tenant_id       INTEGER NOT NULL DEFAULT 1,
	count           INTEGER NOT NULL DEFAULT 0,
	last_at         DATETIME
);
-- Memoria de criterio (case-based reasoning): qué se decidió en cada turno, por qué y con qué resultado.
CREATE TABLE IF NOT EXISTS decisiones (
	id              INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id       INTEGER NOT NULL DEFAULT 1,
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
-- Pares para alineación (DPO): turno de la clienta, propuesta del bot y respuesta de la persona.
CREATE TABLE IF NOT EXISTS pares_dpo (
	id              INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id       INTEGER NOT NULL DEFAULT 1,
	conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
	contexto        TEXT NOT NULL DEFAULT '',
	respuesta_bot   TEXT NOT NULL DEFAULT '',
	respuesta_humana TEXT NOT NULL DEFAULT '',
	created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Vínculo con Kommo CRM (internal/kommo): lead y contacto de Kommo de cada conversación.
CREATE TABLE IF NOT EXISTS kommo_vinculos (
	tenant_id       INTEGER NOT NULL DEFAULT 1,
	clave           TEXT NOT NULL,
	canal           TEXT NOT NULL DEFAULT '',
	conversation_id INTEGER NOT NULL DEFAULT 0,
	lead_id         INTEGER NOT NULL DEFAULT 0,
	contact_id      INTEGER NOT NULL DEFAULT 0,
	estado          TEXT NOT NULL DEFAULT '{}',
	updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	PRIMARY KEY(tenant_id, clave)
);
-- CRM (store/crm.go): notas internas, tareas, actividad de la clienta y etiquetas.
CREATE TABLE IF NOT EXISTS notas (
	id          INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id   INTEGER NOT NULL DEFAULT 1,
	customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
	order_id    INTEGER REFERENCES orders(id) ON DELETE CASCADE,
	texto       TEXT NOT NULL,
	autor       TEXT NOT NULL DEFAULT '',
	autor_id    INTEGER NOT NULL DEFAULT 0,
	created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS tareas (
	id             INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id      INTEGER NOT NULL DEFAULT 1,
	customer_id    INTEGER REFERENCES customers(id) ON DELETE CASCADE,
	order_id       INTEGER REFERENCES orders(id) ON DELETE SET NULL,
	titulo         TEXT NOT NULL,
	vence          DATETIME,
	responsable_id INTEGER NOT NULL DEFAULT 0,
	hecha          INTEGER NOT NULL DEFAULT 0,
	hecha_at       DATETIME,
	creada_por     TEXT NOT NULL DEFAULT '',
	created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS actividad (
	id          INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id   INTEGER NOT NULL DEFAULT 1,
	customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
	tipo        TEXT NOT NULL,
	texto       TEXT NOT NULL DEFAULT '',
	autor       TEXT NOT NULL DEFAULT '',
	ref_id      INTEGER NOT NULL DEFAULT 0,
	created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS cliente_etiquetas (
	tenant_id   INTEGER NOT NULL DEFAULT 1,
	customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
	etiqueta    TEXT NOT NULL,
	PRIMARY KEY(customer_id, etiqueta)
);
`

// Índices: van después de añadir tenant_id a una base anterior (si no, fallarían).
const indexesSQLite = `
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_wa ON messages(wa_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_tenant ON orders(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_products_tenant ON products(tenant_id, code);
CREATE INDEX IF NOT EXISTS idx_conversations_tenant ON conversations(tenant_id, last_message_at);
CREATE INDEX IF NOT EXISTS idx_reservations_variant ON stock_reservations(variant_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_wstock_code ON warehouse_stock(product_code);
CREATE INDEX IF NOT EXISTS idx_decisiones_intent ON decisiones(intent, id);
CREATE INDEX IF NOT EXISTS idx_decisiones_conv ON decisiones(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_pares_conv ON pares_dpo(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_kommo_conv ON kommo_vinculos(conversation_id);
CREATE INDEX IF NOT EXISTS idx_notas_cliente ON notas(tenant_id, customer_id, id);
CREATE INDEX IF NOT EXISTS idx_notas_pedido ON notas(tenant_id, order_id);
CREATE INDEX IF NOT EXISTS idx_tareas_tenant ON tareas(tenant_id, hecha, vence);
CREATE INDEX IF NOT EXISTS idx_tareas_cliente ON tareas(tenant_id, customer_id);
CREATE INDEX IF NOT EXISTS idx_actividad_cliente ON actividad(tenant_id, customer_id, id);
CREATE INDEX IF NOT EXISTS idx_etiquetas_tenant ON cliente_etiquetas(tenant_id, etiqueta);
CREATE INDEX IF NOT EXISTS idx_customers_etapa ON customers(tenant_id, etapa);
`

// migrateSQLite crea las tablas y, en una base de una sola tienda anterior a la multiempresa, añade tenant_id
// (= 1) a las tablas que no lo tienen. Las restricciones UNIQUE viejas (code, jid, key) se quedan como estaban:
// esa base es de una sola empresa.
func (s *Store) migrateSQLite() error {
	if _, err := s.DB.Exec(schemaSQLite); err != nil {
		return err
	}
	for _, t := range TenantTables {
		has, err := s.sqliteHasColumn(t, "tenant_id")
		if err != nil {
			return err
		}
		if !has {
			if _, err := s.DB.Exec(`ALTER TABLE ` + t + ` ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1`); err != nil {
				return fmt.Errorf("%s: %w", t, err)
			}
		}
	}
	for _, c := range [][3]string{
		{"conversations", "agent_version", "TEXT NOT NULL DEFAULT ''"},
		{"conversations", "agent_last", "TEXT NOT NULL DEFAULT ''"},
		{"tenants", "orden", "INTEGER NOT NULL DEFAULT 100"},
		{"tenants", "color", "TEXT NOT NULL DEFAULT ''"},
		{"tenants", "logo", "TEXT NOT NULL DEFAULT ''"},
		{"products", "ficha", "TEXT NOT NULL DEFAULT ''"},
		// CRM (store/crm.go): ficha de la clienta, embudo y asignación.
		{"customers", "email", "TEXT NOT NULL DEFAULT ''"},
		{"customers", "ciudad", "TEXT NOT NULL DEFAULT ''"},
		{"customers", "etapa", "TEXT NOT NULL DEFAULT ''"},
		{"customers", "etapa_fijada", "INTEGER NOT NULL DEFAULT 0"},
		{"customers", "asesora_id", "INTEGER NOT NULL DEFAULT 0"},
		{"orders", "asesora_id", "INTEGER NOT NULL DEFAULT 0"},
	} {
		if has, err := s.sqliteHasColumn(c[0], c[1]); err != nil {
			return err
		} else if !has {
			if _, err := s.DB.Exec(`ALTER TABLE ` + c[0] + ` ADD COLUMN ` + c[1] + ` ` + c[2]); err != nil {
				return err
			}
		}
	}
	if _, err := s.DB.Exec(indexesSQLite); err != nil {
		return err
	}
	// La tienda de siempre es la empresa 1.
	if _, err := s.DB.Exec(`INSERT OR IGNORE INTO tenants(id, slug, name) VALUES(1, 'default', 'Tienda')`); err != nil {
		return err
	}
	return s.rellenarEtapas()
}

func (s *Store) sqliteHasColumn(table, col string) (bool, error) {
	rows, err := s.DB.Query(`SELECT name FROM pragma_table_info(?)`, table)
	if err != nil {
		return false, err
	}
	defer rows.Close()
	for rows.Next() {
		var n string
		if err := rows.Scan(&n); err != nil {
			return false, err
		}
		if n == col {
			return true, nil
		}
	}
	return false, rows.Err()
}

// Esquema MariaDB: el mismo que SQLite, con tipos de MySQL. Los ids son globales (AUTO_INCREMENT); la unicidad de
// negocio (código de producto, jid, clave de ajuste) es por empresa.
var schemaMySQL = []string{
	`CREATE TABLE IF NOT EXISTS tenants (
	id         BIGINT AUTO_INCREMENT PRIMARY KEY,
	slug       VARCHAR(40) NOT NULL UNIQUE,
	name       VARCHAR(200) NOT NULL,
	currency   VARCHAR(10) NOT NULL DEFAULT 'S/',
	whatsapp   VARCHAR(30) NOT NULL DEFAULT '',
	active     TINYINT NOT NULL DEFAULT 1,
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
)`,
	`CREATE TABLE IF NOT EXISTS users (
	id            BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id     BIGINT NOT NULL,
	username      VARCHAR(100) NOT NULL,
	password_hash VARCHAR(255) NOT NULL,
	name          VARCHAR(200) NOT NULL DEFAULT '',
	role          VARCHAR(20) NOT NULL DEFAULT 'admin',
	active        TINYINT NOT NULL DEFAULT 1,
	created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	UNIQUE KEY uq_users (tenant_id, username),
	FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS products (
	id          BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id   BIGINT NOT NULL,
	code        VARCHAR(64) NOT NULL,
	name        VARCHAR(255) NOT NULL,
	description TEXT NOT NULL DEFAULT '',
	category    VARCHAR(100) NOT NULL DEFAULT '',
	color       VARCHAR(100) NOT NULL DEFAULT '',
	price       DOUBLE NOT NULL DEFAULT 0,
	image       VARCHAR(500) NOT NULL DEFAULT '',
	ai_tags     TEXT NOT NULL DEFAULT '',
	active      TINYINT NOT NULL DEFAULT 1,
	created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	UNIQUE KEY uq_products_code (tenant_id, code),
	FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS product_variants (
	id         BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id  BIGINT NOT NULL,
	product_id BIGINT NOT NULL,
	size       VARCHAR(20) NOT NULL,
	stock      INT NOT NULL DEFAULT 0,
	UNIQUE KEY uq_variant (product_id, size),
	KEY idx_variants_tenant (tenant_id),
	FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS customers (
	id         BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id  BIGINT NOT NULL,
	jid        VARCHAR(191) NOT NULL,
	phone      VARCHAR(40) NOT NULL DEFAULT '',
	name       VARCHAR(255) NOT NULL DEFAULT '',
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	UNIQUE KEY uq_customers_jid (tenant_id, jid),
	FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS conversations (
	id              BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id       BIGINT NOT NULL,
	customer_id     BIGINT NOT NULL UNIQUE,
	state           VARCHAR(64) NOT NULL DEFAULT '',
	context         MEDIUMTEXT NOT NULL DEFAULT '{}',
	bot_paused      TINYINT NOT NULL DEFAULT 0,
	paused_at       DATETIME NULL,
	unread          INT NOT NULL DEFAULT 0,
	last_message    TEXT NOT NULL DEFAULT '',
	last_message_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	-- Del agente V2 (rama feat/agente-v2): versión fijada por la asesora y la que habló en el último turno. Esta
	-- rama no las usa; están para que la migración no pierda el dato y la fusión no tenga que migrar otra vez.
	agent_version   VARCHAR(16) NOT NULL DEFAULT '',
	agent_last      VARCHAR(32) NOT NULL DEFAULT '',
	KEY idx_conversations_tenant (tenant_id, last_message_at),
	FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS messages (
	id              BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id       BIGINT NOT NULL,
	conversation_id BIGINT NOT NULL,
	wa_id           VARCHAR(191) NOT NULL DEFAULT '',
	direction       VARCHAR(8) NOT NULL,
	kind            VARCHAR(16) NOT NULL DEFAULT 'text',
	body            MEDIUMTEXT NOT NULL DEFAULT '',
	media           VARCHAR(500) NOT NULL DEFAULT '',
	author          VARCHAR(32) NOT NULL DEFAULT '',
	status          VARCHAR(16) NOT NULL DEFAULT '',
	created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	KEY idx_messages_conv (conversation_id, id),
	KEY idx_messages_wa (tenant_id, wa_id),
	FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS orders (
	id               BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id        BIGINT NOT NULL,
	customer_id      BIGINT NOT NULL,
	status           VARCHAR(20) NOT NULL DEFAULT 'consulta',
	total            DOUBLE NOT NULL DEFAULT 0,
	notes            TEXT NOT NULL DEFAULT '',
	source           VARCHAR(20) NOT NULL DEFAULT 'whatsapp',
	customer_image   VARCHAR(500) NOT NULL DEFAULT '',
	match_confidence DOUBLE NOT NULL DEFAULT 0,
	location_lat     DOUBLE NULL,
	location_lng     DOUBLE NULL,
	location_text    TEXT NOT NULL DEFAULT '',
	stock_reserved   TINYINT NOT NULL DEFAULT 0,
	position         DOUBLE NOT NULL DEFAULT 0,
	created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	KEY idx_orders_tenant (tenant_id, status),
	FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS order_items (
	id           BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id    BIGINT NOT NULL,
	order_id     BIGINT NOT NULL,
	product_id   BIGINT NULL,
	variant_id   BIGINT NULL,
	product_code VARCHAR(64) NOT NULL DEFAULT '',
	product_name VARCHAR(255) NOT NULL DEFAULT '',
	image        VARCHAR(500) NOT NULL DEFAULT '',
	size         VARCHAR(20) NOT NULL DEFAULT '',
	qty          INT NOT NULL DEFAULT 1,
	unit_price   DOUBLE NOT NULL DEFAULT 0,
	KEY idx_items_tenant (tenant_id),
	FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
	FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL,
	FOREIGN KEY (variant_id) REFERENCES product_variants(id) ON DELETE SET NULL
)`,
	`CREATE TABLE IF NOT EXISTS stock_reservations (
	id         BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id  BIGINT NOT NULL,
	order_id   BIGINT NOT NULL UNIQUE,
	variant_id BIGINT NOT NULL,
	qty        INT NOT NULL,
	expires_at DATETIME NOT NULL,
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	KEY idx_reservations_variant (variant_id, expires_at),
	FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
	FOREIGN KEY (variant_id) REFERENCES product_variants(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS warehouses (
	tenant_id BIGINT NOT NULL,
	id        VARCHAR(64) NOT NULL,
	name      VARCHAR(200) NOT NULL,
	address   VARCHAR(500) NOT NULL DEFAULT '',
	hours     VARCHAR(200) NOT NULL DEFAULT '',
	position  INT NOT NULL DEFAULT 0,
	PRIMARY KEY (tenant_id, id),
	FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS warehouse_stock (
	tenant_id    BIGINT NOT NULL,
	warehouse_id VARCHAR(64) NOT NULL,
	product_code VARCHAR(64) NOT NULL,
	size         VARCHAR(20) NOT NULL,
	qty          INT NOT NULL DEFAULT 0,
	updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	PRIMARY KEY (tenant_id, warehouse_id, product_code, size),
	KEY idx_wstock_code (tenant_id, product_code),
	FOREIGN KEY (tenant_id, warehouse_id) REFERENCES warehouses(tenant_id, id) ON DELETE CASCADE
)`,
	"CREATE TABLE IF NOT EXISTS settings (\n" +
		"\ttenant_id BIGINT NOT NULL,\n" +
		"\t`key`     VARCHAR(100) NOT NULL,\n" +
		"\tvalue     MEDIUMTEXT NOT NULL,\n" +
		"\tPRIMARY KEY (tenant_id, `key`),\n" +
		"\tFOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE\n)",
	"CREATE TABLE IF NOT EXISTS followups (\n" +
		"\tconversation_id BIGINT PRIMARY KEY,\n" +
		"\ttenant_id       BIGINT NOT NULL,\n" +
		"\t`count`         INT NOT NULL DEFAULT 0,\n" +
		"\tlast_at         DATETIME NULL,\n" +
		"\tFOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE\n)",
	`CREATE TABLE IF NOT EXISTS decisiones (
	id              BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id       BIGINT NOT NULL,
	conversation_id BIGINT NOT NULL,
	etapa           VARCHAR(40) NOT NULL DEFAULT '',
	intent          VARCHAR(60) NOT NULL DEFAULT '',
	caso            TEXT NOT NULL DEFAULT '',
	decision        VARCHAR(40) NOT NULL DEFAULT '',
	razon           TEXT NOT NULL DEFAULT '',
	resultado       TEXT NOT NULL DEFAULT '',
	autor           VARCHAR(20) NOT NULL DEFAULT '',
	created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	KEY idx_decisiones_intent (tenant_id, intent, id),
	KEY idx_decisiones_conv (conversation_id, id),
	FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS pares_dpo (
	id               BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id        BIGINT NOT NULL,
	conversation_id  BIGINT NOT NULL,
	contexto         MEDIUMTEXT NOT NULL DEFAULT '',
	respuesta_bot    MEDIUMTEXT NOT NULL DEFAULT '',
	respuesta_humana MEDIUMTEXT NOT NULL DEFAULT '',
	created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	KEY idx_pares_conv (conversation_id, id),
	FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS kommo_vinculos (
	tenant_id       BIGINT NOT NULL,
	clave           VARCHAR(191) NOT NULL,
	canal           VARCHAR(20) NOT NULL DEFAULT '',
	conversation_id BIGINT NOT NULL DEFAULT 0,
	lead_id         BIGINT NOT NULL DEFAULT 0,
	contact_id      BIGINT NOT NULL DEFAULT 0,
	estado          MEDIUMTEXT NOT NULL DEFAULT '{}',
	updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	PRIMARY KEY (tenant_id, clave),
	KEY idx_kommo_conv (tenant_id, conversation_id),
	FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
)`,
	// CRM (store/crm.go): notas internas, tareas, actividad de la clienta y etiquetas.
	`CREATE TABLE IF NOT EXISTS notas (
	id          BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id   BIGINT NOT NULL,
	customer_id BIGINT NOT NULL,
	order_id    BIGINT NULL,
	texto       TEXT NOT NULL,
	autor       VARCHAR(100) NOT NULL DEFAULT '',
	autor_id    BIGINT NOT NULL DEFAULT 0,
	created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	KEY idx_notas_cliente (tenant_id, customer_id, id),
	KEY idx_notas_pedido (tenant_id, order_id),
	FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
	FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS tareas (
	id             BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id      BIGINT NOT NULL,
	customer_id    BIGINT NULL,
	order_id       BIGINT NULL,
	titulo         VARCHAR(300) NOT NULL,
	vence          DATETIME NULL,
	responsable_id BIGINT NOT NULL DEFAULT 0,
	hecha          TINYINT NOT NULL DEFAULT 0,
	hecha_at       DATETIME NULL,
	creada_por     VARCHAR(100) NOT NULL DEFAULT '',
	created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	KEY idx_tareas_tenant (tenant_id, hecha, vence),
	KEY idx_tareas_cliente (tenant_id, customer_id),
	FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
	FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
	FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL
)`,
	`CREATE TABLE IF NOT EXISTS actividad (
	id          BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id   BIGINT NOT NULL,
	customer_id BIGINT NOT NULL,
	tipo        VARCHAR(20) NOT NULL,
	texto       TEXT NOT NULL DEFAULT '',
	autor       VARCHAR(100) NOT NULL DEFAULT '',
	ref_id      BIGINT NOT NULL DEFAULT 0,
	created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	KEY idx_actividad_cliente (tenant_id, customer_id, id),
	FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS cliente_etiquetas (
	tenant_id   BIGINT NOT NULL,
	customer_id BIGINT NOT NULL,
	etiqueta    VARCHAR(40) NOT NULL,
	PRIMARY KEY (customer_id, etiqueta),
	KEY idx_etiquetas_tenant (tenant_id, etiqueta),
	FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
)`,
}

func (s *Store) migrateMySQL() error {
	for _, q := range schemaMySQL {
		if _, err := s.DB.Exec(q + ` ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`); err != nil {
			return fmt.Errorf("%.60s…: %w", q, err)
		}
	}
	// Columnas añadidas después de crear la tabla (MariaDB admite IF NOT EXISTS).
	for _, q := range []string{
		`ALTER TABLE conversations ADD COLUMN IF NOT EXISTS agent_version VARCHAR(16) NOT NULL DEFAULT ''`,
		`ALTER TABLE conversations ADD COLUMN IF NOT EXISTS agent_last VARCHAR(32) NOT NULL DEFAULT ''`,
		// Portada de JMD Ventas: orden de las tarjetas, color del monograma (vacío = derivado del slug) y logo.
		`ALTER TABLE tenants ADD COLUMN IF NOT EXISTS orden INT NOT NULL DEFAULT 100`,
		`ALTER TABLE tenants ADD COLUMN IF NOT EXISTS color VARCHAR(16) NOT NULL DEFAULT ''`,
		`ALTER TABLE tenants ADD COLUMN IF NOT EXISTS logo VARCHAR(500) NOT NULL DEFAULT ''`,
		// Ficha técnica de la prenda (JSON, store/ficha.go).
		`ALTER TABLE products ADD COLUMN IF NOT EXISTS ficha MEDIUMTEXT NOT NULL DEFAULT ''`,
		// CRM (store/crm.go): ficha de la clienta, embudo y asignación.
		`ALTER TABLE customers ADD COLUMN IF NOT EXISTS email VARCHAR(200) NOT NULL DEFAULT ''`,
		`ALTER TABLE customers ADD COLUMN IF NOT EXISTS ciudad VARCHAR(120) NOT NULL DEFAULT ''`,
		`ALTER TABLE customers ADD COLUMN IF NOT EXISTS etapa VARCHAR(30) NOT NULL DEFAULT ''`,
		`ALTER TABLE customers ADD COLUMN IF NOT EXISTS etapa_fijada TINYINT NOT NULL DEFAULT 0`,
		`ALTER TABLE customers ADD COLUMN IF NOT EXISTS asesora_id BIGINT NOT NULL DEFAULT 0`,
		`ALTER TABLE orders ADD COLUMN IF NOT EXISTS asesora_id BIGINT NOT NULL DEFAULT 0`,
		`CREATE INDEX IF NOT EXISTS idx_customers_etapa ON customers (tenant_id, etapa)`,
	} {
		if _, err := s.DB.Exec(q); err != nil {
			return err
		}
	}
	return s.rellenarEtapas()
}

func now() time.Time { return time.Now().UTC() }

// Setting devuelve el valor de un ajuste de la empresa o def si no existe.
func (s *Store) Setting(ctx context.Context, key, def string) string {
	var v string
	if err := s.DB.QueryRowContext(ctx, "SELECT value FROM settings WHERE tenant_id=? AND `key`=?", s.tid, key).Scan(&v); err != nil {
		return def
	}
	return v
}

func (s *Store) SetSetting(ctx context.Context, key, value string) error {
	q := "INSERT INTO settings(tenant_id, `key`, value) VALUES(?,?,?) ON CONFLICT DO UPDATE SET value=excluded.value"
	if s.mysql() {
		q = "INSERT INTO settings(tenant_id, `key`, value) VALUES(?,?,?) ON DUPLICATE KEY UPDATE value=VALUES(value)"
	}
	_, err := s.DB.ExecContext(ctx, q, s.tid, key, value)
	return err
}

func (s *Store) Settings(ctx context.Context) (map[string]string, error) {
	rows, err := s.DB.QueryContext(ctx, "SELECT `key`, value FROM settings WHERE tenant_id=?", s.tid)
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
