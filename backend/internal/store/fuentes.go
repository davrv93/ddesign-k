package store

// Fuentes de información del robot (15-10-2026, docs/FUENTES_CONTRATO.md): promos, preguntas frecuentes, guion,
// políticas y material que la tienda registra en el panel. El bot manda las VIGENTES al agente en cada turno
// (Conocimiento); el agente no reentrena nada: lee lo aprobado.
//
// Va en tablas propias (fuentes, fuentes_versiones, embudos, pedido_embudo, uso_fuentes), creadas por migrarFuentes, para
// no tocar orders.go ni conversations.go, que otra sesión edita en paralelo. Como todo el Store, cada consulta filtra
// por tenant_id: con el id de una fuente de otra empresa no se lee, cambia ni borra nada.

import (
	"context"
	"crypto/sha1"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
	"unicode/utf8"
)

// Tipos y estados de una fuente.
var (
	TiposFuente   = []string{"promo", "faq", "guion", "politica", "material"}
	EstadosFuente = []string{"borrador", "activo", "vencido", "archivado", "propuesta", "descartada"}
	// Mecánicas de una promo (el precio lo calcula el agente con la tabla del contrato, §3).
	Mecanicas = []string{"porcentaje", "monto", "precio_especial", "dos_por", "envio_gratis", "regalo", "otra"}
)

func enLista(x string, lista []string) bool {
	for _, v := range lista {
		if v == x {
			return true
		}
	}
	return false
}

// TablasFuentes: las tablas de este módulo (todas con tenant_id). No van en TenantTables porque `jmd migrar-sqlite`
// copia esa lista desde la SQLite de una sola tienda, que no las tiene.
var TablasFuentes = []string{"fuentes", "fuentes_versiones", "embudos", "pedido_embudo", "uso_fuentes"}

type Fuente struct {
	ID             int64           `json:"id"`
	Tipo           string          `json:"tipo"`
	Titulo         string          `json:"titulo"`
	Contenido      string          `json:"contenido"`
	Resumen        string          `json:"resumen,omitempty"` // solo en listas: contenido recortado
	Estado         string          `json:"estado"`
	VigenteDesde   string          `json:"vigente_desde"` // YYYY-MM-DD (Lima) o ""
	VigenteHasta   string          `json:"vigente_hasta"`
	Vigente        bool            `json:"vigente"`
	Programada     bool            `json:"programada"`
	Etiquetas      []string        `json:"etiquetas"`
	Datos          json.RawMessage `json:"datos"`
	Origen         string          `json:"origen"`
	Archivo        string          `json:"archivo"`
	Version        int             `json:"version"`
	ActualizadoPor string          `json:"actualizado_por"`
	ActualizadoEn  time.Time       `json:"actualizado_en"`
	CreadoEn       time.Time       `json:"creado_en"`
}

// Datos de una promo y de una pregunta frecuente (el resto de tipos no lleva datos).
type PromoDatos struct {
	Mecanica        string       `json:"mecanica"`
	Valor           float64      `json:"valor"`
	Alcance         PromoAlcance `json:"alcance"`
	Condiciones     string       `json:"condiciones"`
	MensajeApertura string       `json:"mensaje_apertura"`
	RespuestaPromo  string       `json:"respuesta_promo"`
	RespuestaFuera  string       `json:"respuesta_fuera"`
	Objetivo        string       `json:"objetivo"`
	Meta            float64      `json:"meta"`
	Publico         string       `json:"publico"`
	EmbudoID        int64        `json:"embudo_id"`
}

type PromoAlcance struct {
	Tipo      string   `json:"tipo"` // todo | categoria | coleccion | prendas
	Categoria string   `json:"categoria"`
	Coleccion string   `json:"coleccion"`
	Codigos   []string `json:"codigos"`
}

type FaqDatos struct {
	Pregunta    string   `json:"pregunta"`
	Variantes   []string `json:"variantes"`
	Respuesta   string   `json:"respuesta"`
	Ocurrencias int      `json:"ocurrencias,omitempty"`
}

type FuenteVersion struct {
	Version   int             `json:"version"`
	Titulo    string          `json:"titulo"`
	Contenido string          `json:"contenido"`
	Datos     json.RawMessage `json:"datos"`
	Autor     string          `json:"autor"`
	CreadoEn  time.Time       `json:"creado_en"`
}

var ErrFuenteInvalida = errors.New("fuente inválida")

// SettingPreciosSoloSiPregunta: «true» = el robot y el bot dicen precios de prendas solo cuando la clienta los pregunta
// (Ajustes, 15-10-2026). En el cierre de la compra (resumen, total, pago) el precio va siempre.
const SettingPreciosSoloSiPregunta = "precios_solo_si_pregunta"

// ---------------------------------------------------------------------------
// Migración

const schemaFuentesSQLite = `
CREATE TABLE IF NOT EXISTS fuentes (
	id              INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id       INTEGER NOT NULL DEFAULT 1,
	tipo            TEXT NOT NULL,
	titulo          TEXT NOT NULL,
	contenido       TEXT NOT NULL DEFAULT '',
	estado          TEXT NOT NULL DEFAULT 'borrador',
	vigente_desde   TEXT NOT NULL DEFAULT '',
	vigente_hasta   TEXT NOT NULL DEFAULT '',
	etiquetas       TEXT NOT NULL DEFAULT '[]',
	datos           TEXT NOT NULL DEFAULT '{}',
	origen          TEXT NOT NULL DEFAULT 'manual',
	archivo         TEXT NOT NULL DEFAULT '',
	version         INTEGER NOT NULL DEFAULT 1,
	actualizado_por TEXT NOT NULL DEFAULT '',
	actualizado_en  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	creado_en       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_fuentes_tenant ON fuentes(tenant_id, tipo, estado);
CREATE TABLE IF NOT EXISTS fuentes_versiones (
	id         INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id  INTEGER NOT NULL DEFAULT 1,
	fuente_id  INTEGER NOT NULL REFERENCES fuentes(id) ON DELETE CASCADE,
	version    INTEGER NOT NULL,
	titulo     TEXT NOT NULL DEFAULT '',
	contenido  TEXT NOT NULL DEFAULT '',
	datos      TEXT NOT NULL DEFAULT '{}',
	autor      TEXT NOT NULL DEFAULT '',
	creado_en  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_fversiones ON fuentes_versiones(tenant_id, fuente_id, version);
CREATE TABLE IF NOT EXISTS embudos (
	id                INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id         INTEGER NOT NULL DEFAULT 1,
	nombre            TEXT NOT NULL,
	etapas            TEXT NOT NULL DEFAULT '[]',
	promo_id          INTEGER NOT NULL DEFAULT 0,
	kommo_pipeline_id INTEGER NOT NULL DEFAULT 0,
	creado_en         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_embudos_tenant ON embudos(tenant_id, promo_id);
CREATE TABLE IF NOT EXISTS pedido_embudo (
	tenant_id      INTEGER NOT NULL DEFAULT 1,
	order_id       INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
	embudo_id      INTEGER NOT NULL REFERENCES embudos(id) ON DELETE CASCADE,
	etapa          TEXT NOT NULL DEFAULT '',
	actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	PRIMARY KEY(order_id, embudo_id)
);
CREATE INDEX IF NOT EXISTS idx_pedido_embudo ON pedido_embudo(tenant_id, embudo_id);
CREATE TABLE IF NOT EXISTS uso_fuentes (
	id              INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id       INTEGER NOT NULL DEFAULT 1,
	conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
	fuente_id       INTEGER NOT NULL,
	creado_en       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_uso_conv ON uso_fuentes(tenant_id, conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_uso_fuente ON uso_fuentes(tenant_id, fuente_id, creado_en);
`

var schemaFuentesMySQL = []string{
	`CREATE TABLE IF NOT EXISTS fuentes (
	id              BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id       BIGINT NOT NULL,
	tipo            VARCHAR(20) NOT NULL,
	titulo          VARCHAR(300) NOT NULL,
	contenido       MEDIUMTEXT NOT NULL DEFAULT '',
	estado          VARCHAR(20) NOT NULL DEFAULT 'borrador',
	vigente_desde   VARCHAR(10) NOT NULL DEFAULT '',
	vigente_hasta   VARCHAR(10) NOT NULL DEFAULT '',
	etiquetas       TEXT NOT NULL DEFAULT '[]',
	datos           MEDIUMTEXT NOT NULL DEFAULT '{}',
	origen          VARCHAR(200) NOT NULL DEFAULT 'manual',
	archivo         VARCHAR(500) NOT NULL DEFAULT '',
	version         INT NOT NULL DEFAULT 1,
	actualizado_por VARCHAR(100) NOT NULL DEFAULT '',
	actualizado_en  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	creado_en       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	KEY idx_fuentes_tenant (tenant_id, tipo, estado),
	FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS fuentes_versiones (
	id         BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id  BIGINT NOT NULL,
	fuente_id  BIGINT NOT NULL,
	version    INT NOT NULL,
	titulo     VARCHAR(300) NOT NULL DEFAULT '',
	contenido  MEDIUMTEXT NOT NULL DEFAULT '',
	datos      MEDIUMTEXT NOT NULL DEFAULT '{}',
	autor      VARCHAR(100) NOT NULL DEFAULT '',
	creado_en  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	KEY idx_fversiones (tenant_id, fuente_id, version),
	FOREIGN KEY (fuente_id) REFERENCES fuentes(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS embudos (
	id                BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id         BIGINT NOT NULL,
	nombre            VARCHAR(200) NOT NULL,
	etapas            TEXT NOT NULL DEFAULT '[]',
	promo_id          BIGINT NOT NULL DEFAULT 0,
	kommo_pipeline_id BIGINT NOT NULL DEFAULT 0,
	creado_en         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	KEY idx_embudos_tenant (tenant_id, promo_id),
	FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS pedido_embudo (
	tenant_id      BIGINT NOT NULL,
	order_id       BIGINT NOT NULL,
	embudo_id      BIGINT NOT NULL,
	etapa          VARCHAR(100) NOT NULL DEFAULT '',
	actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	PRIMARY KEY (order_id, embudo_id),
	KEY idx_pedido_embudo (tenant_id, embudo_id),
	FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
	FOREIGN KEY (embudo_id) REFERENCES embudos(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS uso_fuentes (
	id              BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id       BIGINT NOT NULL,
	conversation_id BIGINT NOT NULL,
	fuente_id       BIGINT NOT NULL,
	creado_en       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	KEY idx_uso_conv (tenant_id, conversation_id, id),
	KEY idx_uso_fuente (tenant_id, fuente_id, creado_en),
	FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
)`,
}

// migrarFuentes crea las tablas de este módulo (idempotente). La llaman migrateSQLite y migrateMySQL al final.
func (s *Store) migrarFuentes() error {
	if !s.mysql() {
		_, err := s.DB.Exec(schemaFuentesSQLite)
		return err
	}
	for _, q := range schemaFuentesMySQL {
		if _, err := s.DB.Exec(q + ` ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`); err != nil {
			return fmt.Errorf("%.60s…: %w", q, err)
		}
	}
	return nil
}

// ---------------------------------------------------------------------------
// Fechas (Lima)

// HoyLima: la fecha de hoy en Lima, YYYY-MM-DD.
func HoyLima(t time.Time) string { return t.In(Lima).Format("2006-01-02") }

func fechaValida(f string) bool {
	if f == "" {
		return true
	}
	_, err := time.Parse("2006-01-02", f)
	return err == nil
}

// calcVigencia rellena Vigente y Programada según el estado y las fechas (inclusivas) de hoy en Lima.
func (f *Fuente) calcVigencia(hoy string) {
	f.Vigente = f.Estado == "activo" && (f.VigenteDesde == "" || f.VigenteDesde <= hoy) && (f.VigenteHasta == "" || f.VigenteHasta >= hoy)
	f.Programada = f.Estado == "activo" && f.VigenteDesde != "" && f.VigenteDesde > hoy
}

// ---------------------------------------------------------------------------
// Validación

// normalizar limpia y valida una fuente antes de guardarla.
func (f *Fuente) normalizar() error {
	f.Tipo = strings.TrimSpace(f.Tipo)
	f.Titulo = strings.TrimSpace(f.Titulo)
	if !enLista(f.Tipo, TiposFuente) {
		return fmt.Errorf("%w: tipo %q", ErrFuenteInvalida, f.Tipo)
	}
	if f.Titulo == "" || utf8.RuneCountInString(f.Titulo) > 300 {
		return fmt.Errorf("%w: el título es obligatorio (hasta 300 caracteres)", ErrFuenteInvalida)
	}
	if f.Estado == "" {
		f.Estado = "borrador"
	}
	if !enLista(f.Estado, EstadosFuente) {
		return fmt.Errorf("%w: estado %q", ErrFuenteInvalida, f.Estado)
	}
	if !fechaValida(f.VigenteDesde) || !fechaValida(f.VigenteHasta) {
		return fmt.Errorf("%w: las fechas van como AAAA-MM-DD", ErrFuenteInvalida)
	}
	if f.VigenteDesde != "" && f.VigenteHasta != "" && f.VigenteHasta < f.VigenteDesde {
		return fmt.Errorf("%w: «hasta» es anterior a «desde»", ErrFuenteInvalida)
	}
	if len(f.Contenido) > 400_000 {
		return fmt.Errorf("%w: el contenido es demasiado largo", ErrFuenteInvalida)
	}
	var et []string
	for _, e := range f.Etiquetas {
		if e = strings.TrimSpace(e); e != "" && !enLista(e, et) {
			et = append(et, e)
		}
	}
	f.Etiquetas = et
	if f.Origen == "" {
		f.Origen = "manual"
	}
	if len(f.Datos) == 0 || string(f.Datos) == "null" {
		f.Datos = json.RawMessage(`{}`)
	}
	switch f.Tipo {
	case "promo":
		var p PromoDatos
		if err := json.Unmarshal(f.Datos, &p); err != nil {
			return fmt.Errorf("%w: datos de la promo: %v", ErrFuenteInvalida, err)
		}
		if p.Mecanica == "" {
			p.Mecanica = "otra"
		}
		if !enLista(p.Mecanica, Mecanicas) {
			return fmt.Errorf("%w: mecánica %q", ErrFuenteInvalida, p.Mecanica)
		}
		if p.Valor < 0 || (p.Mecanica == "porcentaje" && p.Valor > 100) {
			return fmt.Errorf("%w: valor %v fuera de rango", ErrFuenteInvalida, p.Valor)
		}
		if p.Alcance.Tipo == "" {
			p.Alcance.Tipo = "todo"
		}
		if !enLista(p.Alcance.Tipo, []string{"todo", "categoria", "coleccion", "prendas"}) {
			return fmt.Errorf("%w: alcance %q", ErrFuenteInvalida, p.Alcance.Tipo)
		}
		if p.Publico == "" {
			p.Publico = "todas"
		}
		var cods []string
		for _, c := range p.Alcance.Codigos {
			if c = strings.ToUpper(strings.TrimSpace(c)); c != "" && !enLista(c, cods) {
				cods = append(cods, c)
			}
		}
		if cods == nil {
			cods = []string{} // [] y no null en el JSON
		}
		p.Alcance.Codigos = cods
		if p.Alcance.Tipo == "prendas" && len(cods) == 0 {
			return fmt.Errorf("%w: elige al menos una prenda", ErrFuenteInvalida)
		}
		f.Datos, _ = json.Marshal(p)
	case "faq":
		var q FaqDatos
		if err := json.Unmarshal(f.Datos, &q); err != nil {
			return fmt.Errorf("%w: datos de la pregunta: %v", ErrFuenteInvalida, err)
		}
		q.Pregunta, q.Respuesta = strings.TrimSpace(q.Pregunta), strings.TrimSpace(q.Respuesta)
		var vs []string
		for _, v := range q.Variantes {
			if v = strings.TrimSpace(v); v != "" && !enLista(v, vs) {
				vs = append(vs, v)
			}
		}
		q.Variantes = vs
		if q.Pregunta == "" {
			return fmt.Errorf("%w: la pregunta es obligatoria", ErrFuenteInvalida)
		}
		// Una propuesta del robot puede llegar sin respuesta; para activarla hace falta (lo revisa CambiarEstadoFuente).
		f.Datos, _ = json.Marshal(q)
	default:
		f.Datos = json.RawMessage(`{}`)
	}
	return nil
}

// ---------------------------------------------------------------------------
// CRUD

const fuenteCols = `id, tipo, titulo, contenido, estado, vigente_desde, vigente_hasta, etiquetas, datos, origen, archivo, version,
	actualizado_por, actualizado_en, creado_en`

func scanFuente(sc interface{ Scan(...any) error }, hoy string) (*Fuente, error) {
	f := &Fuente{}
	var et, datos string
	if err := sc.Scan(&f.ID, &f.Tipo, &f.Titulo, &f.Contenido, &f.Estado, &f.VigenteDesde, &f.VigenteHasta, &et, &datos, &f.Origen,
		&f.Archivo, &f.Version, &f.ActualizadoPor, &f.ActualizadoEn, &f.CreadoEn); err != nil {
		return nil, err
	}
	_ = json.Unmarshal([]byte(et), &f.Etiquetas)
	if f.Etiquetas == nil {
		f.Etiquetas = []string{}
	}
	f.Datos = json.RawMessage(datos)
	if !json.Valid(f.Datos) {
		f.Datos = json.RawMessage(`{}`)
	}
	f.calcVigencia(hoy)
	return f, nil
}

// FiltroFuentes: lista por tipo, estado y texto.
type FiltroFuentes struct {
	Tipo, Estado, Q string
}

func (s *Store) ListarFuentes(ctx context.Context, fl FiltroFuentes) ([]*Fuente, error) {
	q := `SELECT ` + fuenteCols + ` FROM fuentes WHERE tenant_id=?`
	args := []any{s.tid}
	if fl.Tipo != "" {
		q += ` AND tipo=?`
		args = append(args, fl.Tipo)
	}
	switch fl.Estado {
	case "":
		q += ` AND estado NOT IN ('descartada')` // las descartadas solo se ven pidiéndolas
	case "programada", "vigente":
		q += ` AND estado='activo'`
	default:
		q += ` AND estado=?`
		args = append(args, fl.Estado)
	}
	if t := strings.TrimSpace(fl.Q); t != "" {
		q += ` AND (lower(titulo) LIKE ? OR lower(contenido) LIKE ? OR lower(datos) LIKE ?)`
		like := "%" + strings.ToLower(t) + "%"
		args = append(args, like, like, like)
	}
	q += ` ORDER BY actualizado_en DESC, id DESC`
	rows, err := s.DB.QueryContext(ctx, q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	hoy := HoyLima(time.Now())
	out := []*Fuente{}
	for rows.Next() {
		f, err := scanFuente(rows, hoy)
		if err != nil {
			return nil, err
		}
		if (fl.Estado == "programada" && !f.Programada) || (fl.Estado == "vigente" && !f.Vigente) {
			continue
		}
		out = append(out, f)
	}
	return out, rows.Err()
}

func (s *Store) GetFuente(ctx context.Context, id int64) (*Fuente, error) {
	f, err := scanFuente(s.DB.QueryRowContext(ctx, `SELECT `+fuenteCols+` FROM fuentes WHERE id=? AND tenant_id=?`, id, s.tid),
		HoyLima(time.Now()))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	return f, err
}

// CrearFuente guarda una fuente nueva (versión 1).
func (s *Store) CrearFuente(ctx context.Context, f *Fuente) error {
	if err := f.normalizar(); err != nil {
		return err
	}
	et, _ := json.Marshal(f.Etiquetas)
	t := now()
	res, err := s.DB.ExecContext(ctx, `INSERT INTO fuentes(tenant_id, tipo, titulo, contenido, estado, vigente_desde, vigente_hasta,
		etiquetas, datos, origen, archivo, version, actualizado_por, actualizado_en, creado_en) VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)`,
		s.tid, f.Tipo, f.Titulo, f.Contenido, f.Estado, f.VigenteDesde, f.VigenteHasta, string(et), string(f.Datos), f.Origen,
		f.Archivo, autorDe(ctx), t, t)
	if err != nil {
		return err
	}
	f.ID, _ = res.LastInsertId()
	f.Version, f.ActualizadoPor, f.ActualizadoEn, f.CreadoEn = 1, autorDe(ctx), t, t
	f.calcVigencia(HoyLima(time.Now()))
	return nil
}

// GuardarFuente actualiza una fuente. Si cambió el título, el contenido o los datos, la versión anterior queda en
// fuentes_versiones y la versión sube. El tipo no cambia (una promo no se vuelve FAQ).
func (s *Store) GuardarFuente(ctx context.Context, f *Fuente) error {
	prev, err := s.GetFuente(ctx, f.ID)
	if err != nil {
		return err
	}
	f.Tipo = prev.Tipo
	if f.Estado == "" {
		f.Estado = prev.Estado
	}
	if f.Origen == "" {
		f.Origen = prev.Origen
	}
	if f.Archivo == "" {
		f.Archivo = prev.Archivo
	}
	if err := f.normalizar(); err != nil {
		return err
	}
	cambio := prev.Titulo != f.Titulo || prev.Contenido != f.Contenido || !jsonIgual(prev.Datos, f.Datos)
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	version := prev.Version
	if cambio {
		if _, err := tx.ExecContext(ctx, `INSERT INTO fuentes_versiones(tenant_id, fuente_id, version, titulo, contenido, datos, autor, creado_en)
			VALUES(?,?,?,?,?,?,?,?)`, s.tid, prev.ID, prev.Version, prev.Titulo, prev.Contenido, string(prev.Datos), prev.ActualizadoPor,
			prev.ActualizadoEn); err != nil {
			return err
		}
		version++
	}
	et, _ := json.Marshal(f.Etiquetas)
	t := now()
	if _, err := tx.ExecContext(ctx, `UPDATE fuentes SET titulo=?, contenido=?, estado=?, vigente_desde=?, vigente_hasta=?, etiquetas=?,
		datos=?, origen=?, archivo=?, version=?, actualizado_por=?, actualizado_en=? WHERE id=? AND tenant_id=?`,
		f.Titulo, f.Contenido, f.Estado, f.VigenteDesde, f.VigenteHasta, string(et), string(f.Datos), f.Origen, f.Archivo, version,
		autorDe(ctx), t, f.ID, s.tid); err != nil {
		return err
	}
	if err := tx.Commit(); err != nil {
		return err
	}
	f.Version, f.ActualizadoPor, f.ActualizadoEn, f.CreadoEn = version, autorDe(ctx), t, prev.CreadoEn
	f.calcVigencia(HoyLima(time.Now()))
	return nil
}

func jsonIgual(a, b json.RawMessage) bool {
	var x, y any
	if json.Unmarshal(a, &x) != nil || json.Unmarshal(b, &y) != nil {
		return string(a) == string(b)
	}
	ja, _ := json.Marshal(x)
	jb, _ := json.Marshal(y)
	return string(ja) == string(jb)
}

// CambiarEstadoFuente: activar, volver a borrador, archivar o descartar (una propuesta). Activar una pregunta
// frecuente sin respuesta no se puede: el robot no tendría qué decir.
func (s *Store) CambiarEstadoFuente(ctx context.Context, id int64, estado string) (*Fuente, error) {
	if !enLista(estado, []string{"activo", "borrador", "archivado", "descartada"}) {
		return nil, fmt.Errorf("%w: estado %q", ErrFuenteInvalida, estado)
	}
	f, err := s.GetFuente(ctx, id)
	if err != nil {
		return nil, err
	}
	if estado == "activo" {
		if f.Tipo == "faq" {
			var q FaqDatos
			_ = json.Unmarshal(f.Datos, &q)
			if strings.TrimSpace(q.Respuesta) == "" {
				return nil, fmt.Errorf("%w: escribe la respuesta antes de activarla", ErrFuenteInvalida)
			}
		}
		if f.VigenteHasta != "" && f.VigenteHasta < HoyLima(time.Now()) {
			return nil, fmt.Errorf("%w: ya venció (%s): cambia la fecha «hasta»", ErrFuenteInvalida, f.VigenteHasta)
		}
	}
	if _, err := s.DB.ExecContext(ctx, `UPDATE fuentes SET estado=?, actualizado_por=?, actualizado_en=? WHERE id=? AND tenant_id=?`,
		estado, autorDe(ctx), now(), id, s.tid); err != nil {
		return nil, err
	}
	return s.GetFuente(ctx, id)
}

func (s *Store) BorrarFuente(ctx context.Context, id int64) error {
	res, err := s.DB.ExecContext(ctx, `DELETE FROM fuentes WHERE id=? AND tenant_id=?`, id, s.tid)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return ErrNotFound
	}
	_, _ = s.DB.ExecContext(ctx, `DELETE FROM fuentes_versiones WHERE fuente_id=? AND tenant_id=?`, id, s.tid) // SQLite sin FK activas en bases viejas
	return nil
}

func (s *Store) VersionesFuente(ctx context.Context, id int64) ([]FuenteVersion, error) {
	if _, err := s.GetFuente(ctx, id); err != nil {
		return nil, err
	}
	rows, err := s.DB.QueryContext(ctx, `SELECT version, titulo, contenido, datos, autor, creado_en FROM fuentes_versiones
		WHERE fuente_id=? AND tenant_id=? ORDER BY version DESC`, id, s.tid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []FuenteVersion{}
	for rows.Next() {
		var v FuenteVersion
		var datos string
		if err := rows.Scan(&v.Version, &v.Titulo, &v.Contenido, &datos, &v.Autor, &v.CreadoEn); err != nil {
			return nil, err
		}
		v.Datos = json.RawMessage(datos)
		out = append(out, v)
	}
	return out, rows.Err()
}

// VencerFuentes pasa a «vencido» las activas cuya fecha «hasta» ya pasó (en Lima). Devuelve cuántas cambió.
func (s *Store) VencerFuentes(ctx context.Context, ahora time.Time) (int64, error) {
	res, err := s.DB.ExecContext(ctx, `UPDATE fuentes SET estado='vencido', actualizado_por='bot', actualizado_en=?
		WHERE tenant_id=? AND estado='activo' AND vigente_hasta<>'' AND vigente_hasta<?`, now(), s.tid, HoyLima(ahora))
	if err != nil {
		return 0, err
	}
	return res.RowsAffected()
}

// ---------------------------------------------------------------------------
// Conocimiento: lo que recibe el agente (contrato §3)

type PromoConocimiento struct {
	ID           int64  `json:"id"`
	Titulo       string `json:"titulo"`
	Contenido    string `json:"contenido"`
	VigenteDesde string `json:"vigente_desde"`
	VigenteHasta string `json:"vigente_hasta"`
	PromoDatos
}

type FaqConocimiento struct {
	ID        int64    `json:"id"`
	Pregunta  string   `json:"pregunta"`
	Variantes []string `json:"variantes"`
	Respuesta string   `json:"respuesta"`
}

type TextoConocimiento struct {
	ID        int64  `json:"id"`
	Titulo    string `json:"titulo"`
	Contenido string `json:"contenido"`
}

type Conocimiento struct {
	Empresa   string              `json:"empresa"`
	Version   string              `json:"version"`
	Hoy       string              `json:"hoy"`
	Promos    []PromoConocimiento `json:"promos"`
	Faqs      []FaqConocimiento   `json:"faqs"`
	Politicas []TextoConocimiento `json:"politicas"`
	Guion     *TextoConocimiento  `json:"guion"`
	Material  []TextoConocimiento `json:"material"`
}

// Vacio: nada que mandar (el agente sigue con su seed).
func (c *Conocimiento) Vacio() bool {
	return c == nil || len(c.Promos)+len(c.Faqs)+len(c.Politicas)+len(c.Material) == 0 && c.Guion == nil
}

// Tope del material que viaja en cada petición: lo demás se recorta (el agente solo cita párrafos que se parezcan).
const maxMaterial = 120_000

// ConocimientoVigente arma lo que el robot sabe hoy: solo fuentes activas y dentro de sus fechas. `version` es un
// hash del contenido (cambia solo si cambia algo que el agente lee), para que el agente cachee lo derivado.
func (s *Store) ConocimientoVigente(ctx context.Context, empresa string, ahora time.Time) (*Conocimiento, error) {
	hoy := HoyLima(ahora)
	rows, err := s.DB.QueryContext(ctx, `SELECT `+fuenteCols+` FROM fuentes WHERE tenant_id=? AND estado='activo' ORDER BY id`, s.tid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	c := &Conocimiento{Empresa: empresa, Hoy: hoy, Promos: []PromoConocimiento{}, Faqs: []FaqConocimiento{},
		Politicas: []TextoConocimiento{}, Material: []TextoConocimiento{}}
	material := 0
	for rows.Next() {
		f, err := scanFuente(rows, hoy)
		if err != nil {
			return nil, err
		}
		if !f.Vigente {
			continue
		}
		switch f.Tipo {
		case "promo":
			var p PromoDatos
			if json.Unmarshal(f.Datos, &p) != nil {
				continue
			}
			c.Promos = append(c.Promos, PromoConocimiento{ID: f.ID, Titulo: f.Titulo, Contenido: f.Contenido,
				VigenteDesde: f.VigenteDesde, VigenteHasta: f.VigenteHasta, PromoDatos: p})
		case "faq":
			var q FaqDatos
			if json.Unmarshal(f.Datos, &q) != nil || q.Respuesta == "" {
				continue
			}
			c.Faqs = append(c.Faqs, FaqConocimiento{ID: f.ID, Pregunta: q.Pregunta, Variantes: q.Variantes, Respuesta: q.Respuesta})
		case "politica":
			c.Politicas = append(c.Politicas, TextoConocimiento{ID: f.ID, Titulo: f.Titulo, Contenido: f.Contenido})
		case "guion":
			// Un solo guion: el más reciente que esté activo.
			if c.Guion == nil || f.ID > c.Guion.ID {
				c.Guion = &TextoConocimiento{ID: f.ID, Titulo: f.Titulo, Contenido: f.Contenido}
			}
		case "material":
			txt := f.Contenido
			if material+len(txt) > maxMaterial {
				txt = recortar(txt, maxMaterial-material)
			}
			if txt == "" {
				continue
			}
			material += len(txt)
			c.Material = append(c.Material, TextoConocimiento{ID: f.ID, Titulo: f.Titulo, Contenido: txt})
		}
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	sinVersion := *c
	sinVersion.Version, sinVersion.Hoy = "", "" // «hoy» no cambia lo que se sabe: la vigencia ya filtró
	raw, _ := json.Marshal(sinVersion)
	h := sha1.Sum(raw)
	c.Version = hex.EncodeToString(h[:8])
	return c, nil
}

// recortar corta s a lo sumo n bytes sin partir una letra.
func recortar(s string, n int) string {
	if n <= 0 {
		return ""
	}
	if len(s) <= n {
		return s
	}
	for n > 0 && !utf8.RuneStart(s[n]) {
		n--
	}
	return s[:n]
}

// ---------------------------------------------------------------------------
// Uso de fuentes (con qué respondió el robot)

// AnotarUsoFuentes guarda las fuentes en que se apoyó el robot en un turno. Solo ids de esta empresa.
func (s *Store) AnotarUsoFuentes(ctx context.Context, convID int64, ids []int64) error {
	vistos := map[int64]bool{}
	for _, id := range ids {
		if id <= 0 || vistos[id] {
			continue
		}
		vistos[id] = true
		if _, err := s.DB.ExecContext(ctx, `INSERT INTO uso_fuentes(tenant_id, conversation_id, fuente_id, creado_en)
			SELECT ?, cv.id, f.id, ? FROM conversations cv JOIN fuentes f ON f.tenant_id=cv.tenant_id AND f.id=?
			WHERE cv.id=? AND cv.tenant_id=?`, s.tid, now(), id, convID, s.tid); err != nil {
			return err
		}
	}
	return nil
}

type FuenteUsada struct {
	FuenteID int64     `json:"fuente_id"`
	Titulo   string    `json:"titulo"`
	Tipo     string    `json:"tipo"`
	CreadoEn time.Time `json:"creado_en"`
}

// FuentesDeConversacion: cada fuente usada en la conversación, con la última vez que se usó.
func (s *Store) FuentesDeConversacion(ctx context.Context, convID int64) ([]FuenteUsada, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT u.fuente_id, f.titulo, f.tipo, max(u.creado_en) FROM uso_fuentes u
		JOIN fuentes f ON f.id=u.fuente_id AND f.tenant_id=u.tenant_id
		WHERE u.tenant_id=? AND u.conversation_id=? GROUP BY u.fuente_id, f.titulo, f.tipo ORDER BY max(u.creado_en) DESC`, s.tid, convID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []FuenteUsada{}
	for rows.Next() {
		var u FuenteUsada
		var cuando any
		if err := rows.Scan(&u.FuenteID, &u.Titulo, &u.Tipo, &cuando); err != nil {
			return nil, err
		}
		u.CreadoEn = aTiempo(cuando)
		out = append(out, u)
	}
	return out, rows.Err()
}

// aTiempo: max(datetime) vuelve como texto en SQLite y como time.Time en MariaDB.
func aTiempo(v any) time.Time {
	switch x := v.(type) {
	case time.Time:
		return x
	case string:
		return parseTiempo(x)
	case []byte:
		return parseTiempo(string(x))
	}
	return time.Time{}
}

func parseTiempo(s string) time.Time {
	// SQLite (modernc) guarda «2026-10-15 13:50:36.393413 +0000 UTC».
	for _, l := range []string{"2006-01-02 15:04:05.999999999 -0700 MST", "2006-01-02 15:04:05.999999999-07:00", "2006-01-02T15:04:05.999999999Z07:00", "2006-01-02 15:04:05",
		time.RFC3339Nano} {
		if t, err := time.Parse(l, s); err == nil {
			return t
		}
	}
	return time.Time{}
}

// PromoDeConversacion: la última promo con la que respondió el robot en la conversación (0 = ninguna) y desde cuándo.
func (s *Store) PromoDeConversacion(ctx context.Context, convID int64) (int64, time.Time, error) {
	var id int64
	var cuando any
	err := s.DB.QueryRowContext(ctx, `SELECT u.fuente_id, min(u.creado_en) FROM uso_fuentes u
		JOIN fuentes f ON f.id=u.fuente_id AND f.tenant_id=u.tenant_id AND f.tipo='promo'
		WHERE u.tenant_id=? AND u.conversation_id=? GROUP BY u.fuente_id ORDER BY max(u.id) DESC LIMIT 1`, s.tid, convID).Scan(&id, &cuando)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, time.Time{}, nil
	}
	return id, aTiempo(cuando), err
}

// ---------------------------------------------------------------------------
// Precio con promo en el lado del CRM (15-10-2026): el robot le dice a la clienta el precio con promo y el pedido que
// arma el bot tiene que llevar ese mismo precio. Misma tabla que el agente (contrato §3); la colección de baruka.pe no
// se conoce aquí (son handles de la web), así que una promo por colección no rebaja el pedido: lo ajusta una persona.

// categoriasPromo: categoría del contrato → raíz con que se reconoce en el nombre o la categoría del producto.
var categoriasPromo = map[string]string{"vestido": "vestid", "blusa": "blus", "enterizo": "enteriz", "pantalon": "pantal",
	"conjunto": "conjunt", "blazer": "blazer", "falda": "fald"}

func cubrePromo(p PromoConocimiento, prod *Product) bool {
	switch p.Alcance.Tipo {
	case "", "todo":
		return true
	case "prendas":
		return enLista(strings.ToUpper(prod.Code), p.Alcance.Codigos)
	case "categoria":
		raiz := categoriasPromo[p.Alcance.Categoria]
		if raiz == "" {
			return false
		}
		for _, t := range []string{prod.Name, prod.Category} {
			for _, w := range strings.Fields(sinTildesLower(t)) {
				if strings.HasPrefix(w, raiz) {
					return true
				}
			}
		}
	}
	return false
}

func sinTildesLower(s string) string { return sinTildes.Replace(strings.ToLower(s)) }

// redondear a céntimos, la mitad hacia arriba (como el agente: 289,90 × 0,85 = 246,42).
func redondear(x float64) float64 { return float64(int64(x*100+0.5+1e-9)) / 100 }

// PrecioConPromo: el precio de una unidad con la promo vigente que cubre el producto (la que más rebaja) y esa promo;
// sin promo, el precio de siempre y nil. Solo rebajan porcentaje, monto y precio especial (2 por, envío gratis y regalo
// no cambian el precio unitario).
func (c *Conocimiento) PrecioConPromo(prod *Product) (float64, *PromoConocimiento) {
	if c == nil || prod == nil {
		return 0, nil
	}
	mejor, cual := prod.Price, (*PromoConocimiento)(nil)
	for i := range c.Promos {
		p := c.Promos[i]
		if !cubrePromo(p, prod) {
			continue
		}
		precio := prod.Price
		switch p.Mecanica {
		case "porcentaje":
			precio = redondear(prod.Price * (100 - p.Valor) / 100)
		case "monto":
			precio = redondear(prod.Price - p.Valor)
			if precio < 0 {
				precio = 0
			}
		case "precio_especial":
			precio = redondear(p.Valor)
		}
		if precio < mejor {
			mejor, cual = precio, &c.Promos[i]
		}
	}
	return mejor, cual
}
