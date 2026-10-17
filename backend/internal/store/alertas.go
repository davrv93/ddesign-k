package store

// Alertas de venta (15-10-2026, pedido de la tienda): cuando el robot detecta una clienta CALIENTE (evento en 14 días o
// menos, quiere comprar, pidió cita o lo necesita con urgencia) se crea una alerta. El panel la abre como modal y, si en
// Ajustes hay un número, el bot manda un WhatsApp a ese número. Tabla propia (`alertas`), creada al final de
// migrarFuentes; no va en TenantTables por la misma razón que las de fuentes.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"
)

// Ajustes de la alerta (Ajustes → «Clienta caliente»).
const (
	SettingAlertaWhatsApp = "alerta_caliente_whatsapp" // número que recibe el aviso (solo dígitos); "" = sin aviso
	SettingAlertaPanel    = "alerta_caliente_panel"    // "true" | "false": abrir el modal en el panel
)

// AlertaRepetida: la conversación ya tuvo una alerta del mismo tipo hace menos de AlertaVentana.
var AlertaRepetida = errors.New("alerta repetida")

// AlertaVentana: una clienta caliente avisa una vez por semana, aunque su temperatura suba y baje.
const AlertaVentana = 7 * 24 * time.Hour

type Alerta struct {
	ID              int64           `json:"id"`
	Tipo            string          `json:"tipo"`
	ConversationID  int64           `json:"conversation_id"`
	CustomerID      int64           `json:"customer_id"`
	Nombre          string          `json:"nombre"`
	Telefono        string          `json:"telefono"`
	Motivo          string          `json:"motivo"`
	Resumen         json.RawMessage `json:"resumen"`
	CreadoEn        time.Time       `json:"creado_en"`
	AtendidaPor     string          `json:"atendida_por"`
	AtendidaEn      *time.Time      `json:"atendida_en"`
	WhatsappEnviado bool            `json:"whatsapp_enviado"`
}

const schemaAlertasSQLite = `
CREATE TABLE IF NOT EXISTS alertas (
	id               INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id        INTEGER NOT NULL DEFAULT 1,
	conversation_id  INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
	customer_id      INTEGER NOT NULL DEFAULT 0,
	tipo             TEXT NOT NULL DEFAULT 'caliente',
	motivo           TEXT NOT NULL DEFAULT '',
	resumen          TEXT NOT NULL DEFAULT '{}',
	whatsapp_enviado INTEGER NOT NULL DEFAULT 0,
	atendida_por     TEXT NOT NULL DEFAULT '',
	atendida_en      DATETIME,
	creado_en        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_alertas_tenant ON alertas(tenant_id, atendida_en, id);
CREATE INDEX IF NOT EXISTS idx_alertas_conv ON alertas(tenant_id, conversation_id, tipo, creado_en);
`

const schemaAlertasMySQL = `CREATE TABLE IF NOT EXISTS alertas (
	id               BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id        BIGINT NOT NULL,
	conversation_id  BIGINT NOT NULL,
	customer_id      BIGINT NOT NULL DEFAULT 0,
	tipo             VARCHAR(30) NOT NULL DEFAULT 'caliente',
	motivo           VARCHAR(500) NOT NULL DEFAULT '',
	resumen          TEXT NOT NULL DEFAULT '{}',
	whatsapp_enviado TINYINT(1) NOT NULL DEFAULT 0,
	atendida_por     VARCHAR(100) NOT NULL DEFAULT '',
	atendida_en      DATETIME NULL,
	creado_en        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	KEY idx_alertas_tenant (tenant_id, atendida_en, id),
	KEY idx_alertas_conv (tenant_id, conversation_id, tipo, creado_en),
	FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`

func (s *Store) migrarAlertas() error {
	if !s.mysql() {
		if _, err := s.DB.Exec(schemaAlertasSQLite); err != nil {
			return err
		}
		return s.migrarSeguimiento()
	}
	if _, err := s.DB.Exec(schemaAlertasMySQL); err != nil {
		return err
	}
	return s.migrarSeguimiento() // seguimiento, contenido, números y origen (seguimiento.go)
}

// CrearAlerta guarda una alerta nueva. Si la conversación ya tuvo una del mismo tipo en la última semana devuelve
// AlertaRepetida y no crea nada. La conversación tiene que ser de la empresa.
func (s *Store) CrearAlerta(ctx context.Context, a *Alerta) error {
	if a.Tipo == "" {
		a.Tipo = "caliente"
	}
	var custID int64
	if err := s.DB.QueryRowContext(ctx, `SELECT customer_id FROM conversations WHERE id=? AND tenant_id=?`,
		a.ConversationID, s.tid).Scan(&custID); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return ErrNotFound
		}
		return err
	}
	var ultima sql.NullTime
	if err := s.DB.QueryRowContext(ctx, `SELECT creado_en FROM alertas WHERE tenant_id=? AND conversation_id=? AND tipo=?
		ORDER BY id DESC LIMIT 1`, s.tid, a.ConversationID, a.Tipo).Scan(&ultima); err != nil && !errors.Is(err, sql.ErrNoRows) {
		return err
	}
	if ultima.Valid && time.Since(ultima.Time) < AlertaVentana {
		return AlertaRepetida
	}
	if len(a.Resumen) == 0 || !json.Valid(a.Resumen) {
		a.Resumen = json.RawMessage(`{}`)
	}
	if len([]rune(a.Motivo)) > 500 {
		a.Motivo = string([]rune(a.Motivo)[:500])
	}
	t := now()
	res, err := s.DB.ExecContext(ctx, `INSERT INTO alertas(tenant_id, conversation_id, customer_id, tipo, motivo, resumen, creado_en)
		VALUES(?,?,?,?,?,?,?)`, s.tid, a.ConversationID, custID, a.Tipo, a.Motivo, string(a.Resumen), t)
	if err != nil {
		return err
	}
	a.ID, _ = res.LastInsertId()
	a.CustomerID, a.CreadoEn = custID, t
	return nil
}

// MarcarAlertaEnviada anota que el aviso por WhatsApp salió.
func (s *Store) MarcarAlertaEnviada(ctx context.Context, id int64) error {
	_, err := s.DB.ExecContext(ctx, `UPDATE alertas SET whatsapp_enviado=1 WHERE id=? AND tenant_id=?`, id, s.tid)
	return err
}

// AtenderAlerta la marca atendida por quien está en el contexto (store.ConAutor). Atender una ya atendida no cambia nada.
func (s *Store) AtenderAlerta(ctx context.Context, id int64) error {
	var n int
	if err := s.DB.QueryRowContext(ctx, `SELECT COUNT(*) FROM alertas WHERE id=? AND tenant_id=?`, id, s.tid).Scan(&n); err != nil {
		return err
	}
	if n == 0 {
		return ErrNotFound
	}
	_, err := s.DB.ExecContext(ctx, `UPDATE alertas SET atendida_por=?, atendida_en=? WHERE id=? AND tenant_id=? AND atendida_en IS NULL`,
		autorDe(ctx), now(), id, s.tid)
	return err
}

// ListarAlertas: las pendientes (o todas), la más nueva primero, con el nombre y el teléfono de la clienta. Hasta 200.
func (s *Store) ListarAlertas(ctx context.Context, soloPendientes bool) ([]*Alerta, error) {
	q := `SELECT a.id, a.tipo, a.conversation_id, a.customer_id, COALESCE(c.name,''), COALESCE(c.phone,''), a.motivo, a.resumen,
		a.creado_en, a.atendida_por, a.atendida_en, a.whatsapp_enviado
		FROM alertas a LEFT JOIN customers c ON c.id=a.customer_id AND c.tenant_id=a.tenant_id
		WHERE a.tenant_id=?`
	if soloPendientes {
		q += ` AND a.atendida_en IS NULL`
	}
	q += ` ORDER BY a.id DESC LIMIT 200`
	rows, err := s.DB.QueryContext(ctx, q, s.tid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*Alerta{}
	for rows.Next() {
		a := &Alerta{}
		var resumen string
		var atendida sql.NullTime
		var wa int
		if err := rows.Scan(&a.ID, &a.Tipo, &a.ConversationID, &a.CustomerID, &a.Nombre, &a.Telefono, &a.Motivo, &resumen,
			&a.CreadoEn, &a.AtendidaPor, &atendida, &wa); err != nil {
			return nil, err
		}
		a.Resumen = json.RawMessage(resumen)
		if !json.Valid(a.Resumen) {
			a.Resumen = json.RawMessage(`{}`)
		}
		if atendida.Valid {
			t := atendida.Time
			a.AtendidaEn = &t
		}
		a.WhatsappEnviado = wa != 0
		out = append(out, a)
	}
	return out, rows.Err()
}
