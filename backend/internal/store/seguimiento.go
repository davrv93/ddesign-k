package store

// Seguimiento por modelo, contenido de seguimiento, números de WhatsApp y origen de cada conversación (octubre de 2026,
// pedido de Luis, dueño de Baruka). Contrato en docs/SEGUIMIENTO_CONTRATO.md. Tablas propias, creadas al final de
// migrarFuentes (migrarSeguimiento); no van en TenantTables por la misma razón que las de fuentes.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
	"unicode/utf8"
)

// Ajustes de esta parte (todos texto). Los valores por defecto los registra api/seguimiento.go.
const (
	SettingBienvenidaAnuncio   = "bienvenida_anuncio"
	SettingPrecioAlLlegar      = "precio_al_llegar_por_anuncio"
	SettingProspectos          = "prospectos_habilitado"
	SettingProspectoDesde      = "prospecto_desde_dias"
	SettingProspectoAviso      = "prospecto_aviso_dias"
	SettingPreguntarPresupuest = "preguntar_presupuesto"
	SettingSeguimientoHorario  = "seguimiento_horario"
	SettingTopePrincipal       = "tope_diario_principal"
)

var ErrSeguimientoInvalido = errors.New("dato inválido")

// ---------------------------------------------------------------------------
// Esquema

const schemaSeguimientoSQLite = `
CREATE TABLE IF NOT EXISTS contenido_producto (
	id             INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id      INTEGER NOT NULL DEFAULT 1,
	product_id     INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
	clave          TEXT NOT NULL,
	texto          TEXT NOT NULL DEFAULT '',
	media_url      TEXT NOT NULL DEFAULT '',
	origen         TEXT NOT NULL DEFAULT 'manual',
	actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	UNIQUE(tenant_id, product_id, clave)
);
CREATE TABLE IF NOT EXISTS secuencias (
	id               INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id        INTEGER NOT NULL DEFAULT 1,
	nombre           TEXT NOT NULL,
	activa           INTEGER NOT NULL DEFAULT 0,
	alcance          TEXT NOT NULL DEFAULT 'todas',
	alcance_valor    TEXT NOT NULL DEFAULT '',
	pasos            TEXT NOT NULL DEFAULT '[]',
	fuera_de_ventana TEXT NOT NULL DEFAULT 'saltar',
	al_responder     TEXT NOT NULL DEFAULT 'continuar',
	marcar_perdida   INTEGER NOT NULL DEFAULT 0,
	creada_en        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	actualizada_en   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_secuencias_tenant ON secuencias(tenant_id, activa);
CREATE TABLE IF NOT EXISTS seguimiento_en_curso (
	id              INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id       INTEGER NOT NULL DEFAULT 1,
	conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
	secuencia_id    INTEGER NOT NULL REFERENCES secuencias(id) ON DELETE CASCADE,
	paso            INTEGER NOT NULL DEFAULT 0,
	proximo_en      DATETIME,
	estado          TEXT NOT NULL DEFAULT 'activo',
	product_code    TEXT NOT NULL DEFAULT '',
	armado_en       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	actualizado_en  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	UNIQUE(tenant_id, conversation_id)
);
CREATE INDEX IF NOT EXISTS idx_seg_proximo ON seguimiento_en_curso(estado, proximo_en);
CREATE TABLE IF NOT EXISTS seguimiento_envios (
	id              INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id       INTEGER NOT NULL DEFAULT 1,
	conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
	secuencia_id    INTEGER NOT NULL DEFAULT 0,
	paso            INTEGER NOT NULL DEFAULT 0,
	numero_id       INTEGER NOT NULL DEFAULT 0,
	tipo            TEXT NOT NULL DEFAULT '',
	resultado       TEXT NOT NULL DEFAULT 'enviado',
	motivo          TEXT NOT NULL DEFAULT '',
	enviado_en      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_seg_envios ON seguimiento_envios(tenant_id, numero_id, enviado_en);
CREATE TABLE IF NOT EXISTS numeros_whatsapp (
	id              INTEGER PRIMARY KEY AUTOINCREMENT,
	tenant_id       INTEGER NOT NULL DEFAULT 1,
	nombre          TEXT NOT NULL,
	canal           TEXT NOT NULL DEFAULT 'evolution',
	numero          TEXT NOT NULL DEFAULT '',
	instancia       TEXT NOT NULL DEFAULT '',
	token           TEXT NOT NULL DEFAULT '',
	phone_number_id TEXT NOT NULL DEFAULT '',
	uso             TEXT NOT NULL DEFAULT 'salida',
	tope_diario     INTEGER NOT NULL DEFAULT 25,
	creado_en       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS origen_conversacion (
	tenant_id       INTEGER NOT NULL DEFAULT 1,
	conversation_id INTEGER NOT NULL PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
	canal           TEXT NOT NULL DEFAULT '',
	ad_id           TEXT NOT NULL DEFAULT '',
	titular         TEXT NOT NULL DEFAULT '',
	ctwa_clid       TEXT NOT NULL DEFAULT '',
	llego_en        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_origen_tenant ON origen_conversacion(tenant_id, llego_en);
`

var schemaSeguimientoMySQL = []string{
	`CREATE TABLE IF NOT EXISTS contenido_producto (
	id             BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id      BIGINT NOT NULL,
	product_id     BIGINT NOT NULL,
	clave          VARCHAR(20) NOT NULL,
	texto          TEXT NOT NULL,
	media_url      VARCHAR(1000) NOT NULL DEFAULT '',
	origen         VARCHAR(10) NOT NULL DEFAULT 'manual',
	actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	UNIQUE KEY uq_contenido (tenant_id, product_id, clave),
	FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS secuencias (
	id               BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id        BIGINT NOT NULL,
	nombre           VARCHAR(200) NOT NULL,
	activa           TINYINT(1) NOT NULL DEFAULT 0,
	alcance          VARCHAR(20) NOT NULL DEFAULT 'todas',
	alcance_valor    VARCHAR(1000) NOT NULL DEFAULT '',
	pasos            TEXT NOT NULL,
	fuera_de_ventana VARCHAR(20) NOT NULL DEFAULT 'saltar',
	al_responder     VARCHAR(20) NOT NULL DEFAULT 'continuar',
	marcar_perdida   TINYINT(1) NOT NULL DEFAULT 0,
	creada_en        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	actualizada_en   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	KEY idx_secuencias_tenant (tenant_id, activa),
	FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS seguimiento_en_curso (
	id              BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id       BIGINT NOT NULL,
	conversation_id BIGINT NOT NULL,
	secuencia_id    BIGINT NOT NULL,
	paso            INT NOT NULL DEFAULT 0,
	proximo_en      DATETIME NULL,
	estado          VARCHAR(20) NOT NULL DEFAULT 'activo',
	product_code    VARCHAR(40) NOT NULL DEFAULT '',
	armado_en       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	actualizado_en  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	UNIQUE KEY uq_seg_conv (tenant_id, conversation_id),
	KEY idx_seg_proximo (estado, proximo_en),
	FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
	FOREIGN KEY (secuencia_id) REFERENCES secuencias(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS seguimiento_envios (
	id              BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id       BIGINT NOT NULL,
	conversation_id BIGINT NOT NULL,
	secuencia_id    BIGINT NOT NULL DEFAULT 0,
	paso            INT NOT NULL DEFAULT 0,
	numero_id       BIGINT NOT NULL DEFAULT 0,
	tipo            VARCHAR(20) NOT NULL DEFAULT '',
	resultado       VARCHAR(20) NOT NULL DEFAULT 'enviado',
	motivo          VARCHAR(300) NOT NULL DEFAULT '',
	enviado_en      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	KEY idx_seg_envios (tenant_id, numero_id, enviado_en),
	FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS numeros_whatsapp (
	id              BIGINT AUTO_INCREMENT PRIMARY KEY,
	tenant_id       BIGINT NOT NULL,
	nombre          VARCHAR(100) NOT NULL,
	canal           VARCHAR(20) NOT NULL DEFAULT 'evolution',
	numero          VARCHAR(30) NOT NULL DEFAULT '',
	instancia       VARCHAR(100) NOT NULL DEFAULT '',
	token           VARCHAR(200) NOT NULL DEFAULT '',
	phone_number_id VARCHAR(60) NOT NULL DEFAULT '',
	uso             VARCHAR(10) NOT NULL DEFAULT 'salida',
	tope_diario     INT NOT NULL DEFAULT 25,
	creado_en       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
)`,
	`CREATE TABLE IF NOT EXISTS origen_conversacion (
	tenant_id       BIGINT NOT NULL,
	conversation_id BIGINT NOT NULL PRIMARY KEY,
	canal           VARCHAR(20) NOT NULL DEFAULT '',
	ad_id           VARCHAR(100) NOT NULL DEFAULT '',
	titular         VARCHAR(300) NOT NULL DEFAULT '',
	ctwa_clid       VARCHAR(300) NOT NULL DEFAULT '',
	llego_en        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	KEY idx_origen_tenant (tenant_id, llego_en),
	FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
)`,
}

func (s *Store) migrarSeguimiento() error {
	if !s.mysql() {
		if _, err := s.DB.Exec(schemaSeguimientoSQLite); err != nil {
			return err
		}
	} else {
		for _, q := range schemaSeguimientoMySQL {
			if _, err := s.DB.Exec(q + ` ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`); err != nil {
				return fmt.Errorf("%.60s…: %w", q, err)
			}
		}
	}
	// 17-10-2026 (flujos de Kommo de Luis): si la clienta responde a un paso, una tarea para el equipo («Negociación»).
	if _, err := s.DB.Exec(`SELECT tarea_al_responder FROM secuencias LIMIT 0`); err != nil {
		if _, err := s.DB.Exec(`ALTER TABLE secuencias ADD COLUMN tarea_al_responder INTEGER NOT NULL DEFAULT 0`); err != nil {
			return fmt.Errorf("secuencias.tarea_al_responder: %w", err)
		}
	}
	return s.migrarFlujos()
}

// ---------------------------------------------------------------------------
// Contenido de seguimiento por modelo

var ClavesContenido = []string{"beneficio", "testimonio", "tip"}

type Contenido struct {
	Clave         string    `json:"clave"`
	Texto         string    `json:"texto"`
	MediaURL      string    `json:"media_url"`
	Origen        string    `json:"origen"`
	ActualizadoEn time.Time `json:"actualizado_en"`
}

func (s *Store) productoDeEmpresa(ctx context.Context, id int64) error {
	var n int
	if err := s.DB.QueryRowContext(ctx, `SELECT COUNT(*) FROM products WHERE id=? AND tenant_id=?`, id, s.tid).Scan(&n); err != nil {
		return err
	}
	if n == 0 {
		return ErrNotFound
	}
	return nil
}

// ContenidoProducto: lo que hay para un modelo (beneficio, testimonio, tip).
func (s *Store) ContenidoProducto(ctx context.Context, productID int64) ([]Contenido, error) {
	if err := s.productoDeEmpresa(ctx, productID); err != nil {
		return nil, err
	}
	rows, err := s.DB.QueryContext(ctx, `SELECT clave, texto, media_url, origen, actualizado_en FROM contenido_producto
		WHERE tenant_id=? AND product_id=? ORDER BY clave`, s.tid, productID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Contenido{}
	for rows.Next() {
		var c Contenido
		if err := rows.Scan(&c.Clave, &c.Texto, &c.MediaURL, &c.Origen, &c.ActualizadoEn); err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

// ContenidoDe: un contenido o nil.
func (s *Store) ContenidoDe(ctx context.Context, productID int64, clave string) (*Contenido, error) {
	c := &Contenido{}
	err := s.DB.QueryRowContext(ctx, `SELECT clave, texto, media_url, origen, actualizado_en FROM contenido_producto
		WHERE tenant_id=? AND product_id=? AND clave=?`, s.tid, productID, clave).Scan(&c.Clave, &c.Texto, &c.MediaURL, &c.Origen, &c.ActualizadoEn)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return c, err
}

// GuardarContenido escribe (o borra, si llega vacío) un contenido. origen = «web» no pisa lo «manual».
func (s *Store) GuardarContenido(ctx context.Context, productID int64, clave, texto, media, origen string) (bool, error) {
	if !enLista(clave, ClavesContenido) {
		return false, fmt.Errorf("%w: clave %q", ErrSeguimientoInvalido, clave)
	}
	if err := s.productoDeEmpresa(ctx, productID); err != nil {
		return false, err
	}
	texto, media = strings.TrimSpace(texto), strings.TrimSpace(media)
	if utf8.RuneCountInString(texto) > 4000 || len(media) > 1000 {
		return false, fmt.Errorf("%w: el texto o el enlace son demasiado largos", ErrSeguimientoInvalido)
	}
	if media != "" && !strings.HasPrefix(media, "http://") && !strings.HasPrefix(media, "https://") && !strings.HasPrefix(media, "/") {
		return false, fmt.Errorf("%w: el enlace debe empezar con https://", ErrSeguimientoInvalido)
	}
	prev, err := s.ContenidoDe(ctx, productID, clave)
	if err != nil {
		return false, err
	}
	if origen == "web" && prev != nil && prev.Origen == "manual" {
		return false, nil
	}
	if texto == "" && media == "" {
		_, err := s.DB.ExecContext(ctx, `DELETE FROM contenido_producto WHERE tenant_id=? AND product_id=? AND clave=?`, s.tid, productID, clave)
		return err == nil, err
	}
	if prev == nil {
		_, err = s.DB.ExecContext(ctx, `INSERT INTO contenido_producto(tenant_id, product_id, clave, texto, media_url, origen, actualizado_en)
			VALUES(?,?,?,?,?,?,?)`, s.tid, productID, clave, texto, media, origen, now())
	} else {
		_, err = s.DB.ExecContext(ctx, `UPDATE contenido_producto SET texto=?, media_url=?, origen=?, actualizado_en=?
			WHERE tenant_id=? AND product_id=? AND clave=?`, texto, media, origen, now(), s.tid, productID, clave)
	}
	return err == nil, err
}

type ResumenContenido struct {
	ProductID  int64  `json:"product_id"`
	Codigo     string `json:"codigo"`
	Nombre     string `json:"nombre"`
	Beneficio  bool   `json:"beneficio"`
	Testimonio bool   `json:"testimonio"`
	Tip        bool   `json:"tip"`
}

// ResumenContenidos: qué modelo activo tiene cada pieza.
func (s *Store) ResumenContenidos(ctx context.Context) ([]ResumenContenido, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT p.id, p.code, p.name,
		SUM(CASE WHEN c.clave='beneficio' THEN 1 ELSE 0 END), SUM(CASE WHEN c.clave='testimonio' THEN 1 ELSE 0 END),
		SUM(CASE WHEN c.clave='tip' THEN 1 ELSE 0 END)
		FROM products p LEFT JOIN contenido_producto c ON c.product_id=p.id AND c.tenant_id=p.tenant_id
		WHERE p.tenant_id=? AND p.active=1 GROUP BY p.id, p.code, p.name ORDER BY p.code`, s.tid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []ResumenContenido{}
	for rows.Next() {
		var r ResumenContenido
		var b, t, tp int
		if err := rows.Scan(&r.ProductID, &r.Codigo, &r.Nombre, &b, &t, &tp); err != nil {
			return nil, err
		}
		r.Beneficio, r.Testimonio, r.Tip = b > 0, t > 0, tp > 0
		out = append(out, r)
	}
	return out, rows.Err()
}

// ---------------------------------------------------------------------------
// Secuencias

var (
	TiposPaso       = []string{"emoji", "texto", "beneficio", "testimonio", "tip", "oferta"}
	FueraDeVentana  = []string{"saltar", "enviar", "plantilla", "numero_secundario"}
	AlResponder     = []string{"continuar", "reiniciar", "detener"}
	AlcancesSecuenc = []string{"todas", "categoria", "productos"}
)

const (
	MaxPasos      = 12
	MaxEsperaMin  = 14 * 24 * 60
	VentanaLibre  = 24 * time.Hour // WhatsApp: mensajes libres hasta 24 h después del último de la clienta
	EstadoActivo  = "activo"
	EstadoPausado = "pausado"
)

type Paso struct {
	EsperaMin int    `json:"espera_min"`
	Tipo      string `json:"tipo"`
	Texto     string `json:"texto"`
	PromoID   int64  `json:"promo_id"`
	Plantilla string `json:"plantilla"`
	// MediaURL: imagen que acompaña a un paso emoji/texto (p. ej. el sticker «Esperando q me respondas»). Relativa a la
	// empresa («/media/seguimiento/x.png», la sube el panel) o https://.
	MediaURL string `json:"media_url,omitempty"`
}

type CifrasSecuencia struct {
	EnCurso      int `json:"en_curso"`
	EnviadosHoy  int `json:"enviados_hoy"`
	Respondieron int `json:"respondieron"`
	Terminados   int `json:"terminados"`
}

type Secuencia struct {
	ID             int64  `json:"id"`
	Nombre         string `json:"nombre"`
	Activa         bool   `json:"activa"`
	Alcance        string `json:"alcance"`
	AlcanceValor   string `json:"alcance_valor"`
	Pasos          []Paso `json:"pasos"`
	FueraDeVentana string `json:"fuera_de_ventana"`
	AlResponder    string `json:"al_responder"`
	MarcarPerdida  bool   `json:"marcar_perdida"`
	// TareaAlResponder: si ella contesta a un paso que sí salió, se crea una tarea para el equipo (en Kommo, Luis la
	// pasaba a la etapa «Negociación» para que una persona la atienda).
	TareaAlResponder bool `json:"tarea_al_responder"`
	// Grafo: el flujo del editor visual (store/flujos.go). Con grafo, Pasos se deriva de sus mensajes y el motor recorre
	// el grafo; sin grafo, la secuencia es la lista de pasos de siempre.
	Grafo    *Grafo          `json:"grafo,omitempty"`
	CreadaEn time.Time       `json:"creada_en"`
	Cifras   CifrasSecuencia `json:"cifras"`
}

func (q *Secuencia) normalizar() error {
	q.Nombre = strings.TrimSpace(q.Nombre)
	if q.Nombre == "" || utf8.RuneCountInString(q.Nombre) > 200 {
		return fmt.Errorf("%w: el nombre es obligatorio (hasta 200 caracteres)", ErrSeguimientoInvalido)
	}
	if q.Alcance == "" {
		q.Alcance = "todas"
	}
	if q.FueraDeVentana == "" {
		q.FueraDeVentana = "saltar"
	}
	if q.AlResponder == "" {
		q.AlResponder = "continuar"
	}
	if !enLista(q.Alcance, AlcancesSecuenc) || !enLista(q.FueraDeVentana, FueraDeVentana) || !enLista(q.AlResponder, AlResponder) {
		return fmt.Errorf("%w: alcance, fuera de ventana o al responder", ErrSeguimientoInvalido)
	}
	q.AlcanceValor = strings.TrimSpace(q.AlcanceValor)
	if q.Alcance == "todas" {
		q.AlcanceValor = ""
	} else if q.AlcanceValor == "" {
		return fmt.Errorf("%w: falta la categoría o los modelos del alcance", ErrSeguimientoInvalido)
	}
	if q.Alcance == "productos" {
		var cods []string
		for _, c := range strings.Split(q.AlcanceValor, ",") {
			if c = strings.ToUpper(strings.TrimSpace(c)); c != "" && !enLista(c, cods) {
				cods = append(cods, c)
			}
		}
		q.AlcanceValor = strings.Join(cods, ",")
		if q.AlcanceValor == "" {
			return fmt.Errorf("%w: falta al menos un modelo en el alcance", ErrSeguimientoInvalido)
		}
	}
	if q.Grafo != nil {
		if err := q.Grafo.normalizar(); err != nil {
			return err
		}
		q.Pasos = q.Grafo.PasosDerivados()
		return nil
	}
	if len(q.Pasos) == 0 || len(q.Pasos) > MaxPasos {
		return fmt.Errorf("%w: la secuencia lleva de 1 a %d pasos", ErrSeguimientoInvalido, MaxPasos)
	}
	for i := range q.Pasos {
		p := &q.Pasos[i]
		p.Texto, p.Plantilla, p.MediaURL = strings.TrimSpace(p.Texto), strings.TrimSpace(p.Plantilla), strings.TrimSpace(p.MediaURL)
		if p.MediaURL != "" && !strings.HasPrefix(p.MediaURL, "/media/") && !strings.HasPrefix(p.MediaURL, "https://") {
			return fmt.Errorf("%w: paso %d: la imagen debe subirse desde el panel o empezar con https://", ErrSeguimientoInvalido, i+1)
		}
		if p.EsperaMin < 1 || p.EsperaMin > MaxEsperaMin {
			return fmt.Errorf("%w: paso %d: la espera va de 1 minuto a 14 días", ErrSeguimientoInvalido, i+1)
		}
		if !enLista(p.Tipo, TiposPaso) {
			return fmt.Errorf("%w: paso %d: tipo %q", ErrSeguimientoInvalido, i+1, p.Tipo)
		}
		if (p.Tipo == "emoji" || p.Tipo == "texto") && p.Texto == "" && p.MediaURL == "" {
			return fmt.Errorf("%w: paso %d: falta el texto o la imagen", ErrSeguimientoInvalido, i+1)
		}
		if p.Tipo == "oferta" && p.PromoID <= 0 {
			return fmt.Errorf("%w: paso %d: la oferta necesita una promo", ErrSeguimientoInvalido, i+1)
		}
		if utf8.RuneCountInString(p.Texto) > 1000 {
			return fmt.Errorf("%w: paso %d: el texto es demasiado largo", ErrSeguimientoInvalido, i+1)
		}
	}
	return nil
}

const secuenciaCols = `id, nombre, activa, alcance, alcance_valor, pasos, fuera_de_ventana, al_responder, marcar_perdida, creada_en,
	tarea_al_responder, COALESCE(grafo, '')`

func scanSecuencia(sc interface{ Scan(...any) error }) (*Secuencia, error) {
	q := &Secuencia{}
	var activa, perdida, tarea int
	var pasos, grafo string
	if err := sc.Scan(&q.ID, &q.Nombre, &activa, &q.Alcance, &q.AlcanceValor, &pasos, &q.FueraDeVentana, &q.AlResponder,
		&perdida, &q.CreadaEn, &tarea, &grafo); err != nil {
		return nil, err
	}
	q.Grafo = leerGrafo(grafo)
	q.Activa, q.MarcarPerdida, q.TareaAlResponder = activa != 0, perdida != 0, tarea != 0
	_ = json.Unmarshal([]byte(pasos), &q.Pasos)
	if q.Pasos == nil {
		q.Pasos = []Paso{}
	}
	return q, nil
}

func (s *Store) ListarSecuencias(ctx context.Context) ([]*Secuencia, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT `+secuenciaCols+` FROM secuencias WHERE tenant_id=? ORDER BY id`, s.tid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*Secuencia{}
	for rows.Next() {
		q, err := scanSecuencia(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, q)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	for _, q := range out {
		q.Cifras = s.cifrasSecuencia(ctx, q.ID)
	}
	return out, nil
}

func (s *Store) cifrasSecuencia(ctx context.Context, id int64) CifrasSecuencia {
	var c CifrasSecuencia
	_ = s.DB.QueryRowContext(ctx, `SELECT
		COALESCE(SUM(CASE WHEN estado IN ('activo','pausado') THEN 1 ELSE 0 END),0),
		COALESCE(SUM(CASE WHEN estado='terminado' THEN 1 ELSE 0 END),0)
		FROM seguimiento_en_curso WHERE tenant_id=? AND secuencia_id=?`, s.tid, id).Scan(&c.EnCurso, &c.Terminados)
	_ = s.DB.QueryRowContext(ctx, `SELECT COUNT(*) FROM seguimiento_envios WHERE tenant_id=? AND secuencia_id=? AND resultado='enviado'
		AND enviado_en>=?`, s.tid, id, inicioDiaLima(time.Now())).Scan(&c.EnviadosHoy)
	_ = s.DB.QueryRowContext(ctx, `SELECT COUNT(*) FROM seguimiento_envios WHERE tenant_id=? AND secuencia_id=? AND resultado='respondio'`,
		s.tid, id).Scan(&c.Respondieron)
	return c
}

func (s *Store) GetSecuencia(ctx context.Context, id int64) (*Secuencia, error) {
	q, err := scanSecuencia(s.DB.QueryRowContext(ctx, `SELECT `+secuenciaCols+` FROM secuencias WHERE id=? AND tenant_id=?`, id, s.tid))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err == nil {
		q.Cifras = s.cifrasSecuencia(ctx, q.ID)
	}
	return q, err
}

func (s *Store) CrearSecuencia(ctx context.Context, q *Secuencia) error {
	if err := q.normalizar(); err != nil {
		return err
	}
	pasos, _ := json.Marshal(q.Pasos)
	t := now()
	res, err := s.DB.ExecContext(ctx, `INSERT INTO secuencias(tenant_id, nombre, activa, alcance, alcance_valor, pasos, fuera_de_ventana,
		al_responder, marcar_perdida, tarea_al_responder, creada_en, actualizada_en, grafo) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)`, s.tid, q.Nombre,
		b2i(q.Activa), q.Alcance, q.AlcanceValor, string(pasos), q.FueraDeVentana, q.AlResponder, b2i(q.MarcarPerdida),
		b2i(q.TareaAlResponder), t, t, grafoJSON(q.Grafo))
	if err != nil {
		return err
	}
	q.ID, _ = res.LastInsertId()
	q.CreadaEn = t
	return nil
}

func (s *Store) GuardarSecuencia(ctx context.Context, q *Secuencia) error {
	if _, err := s.GetSecuencia(ctx, q.ID); err != nil {
		return err
	}
	if err := q.normalizar(); err != nil {
		return err
	}
	pasos, _ := json.Marshal(q.Pasos)
	_, err := s.DB.ExecContext(ctx, `UPDATE secuencias SET nombre=?, activa=?, alcance=?, alcance_valor=?, pasos=?, fuera_de_ventana=?,
		al_responder=?, marcar_perdida=?, tarea_al_responder=?, actualizada_en=?, grafo=? WHERE id=? AND tenant_id=?`, q.Nombre, b2i(q.Activa),
		q.Alcance, q.AlcanceValor, string(pasos), q.FueraDeVentana, q.AlResponder, b2i(q.MarcarPerdida), b2i(q.TareaAlResponder),
		now(), grafoJSON(q.Grafo), q.ID, s.tid)
	if err == nil && !q.Activa {
		_, err = s.DB.ExecContext(ctx, `UPDATE seguimiento_en_curso SET estado='detenido', actualizado_en=? WHERE tenant_id=? AND secuencia_id=?
			AND estado IN ('activo','pausado')`, now(), s.tid, q.ID)
	}
	return err
}

func (s *Store) ActivarSecuencia(ctx context.Context, id int64, activa bool) error {
	q, err := s.GetSecuencia(ctx, id)
	if err != nil {
		return err
	}
	q.Activa = activa
	return s.GuardarSecuencia(ctx, q)
}

func (s *Store) BorrarSecuencia(ctx context.Context, id int64) error {
	res, err := s.DB.ExecContext(ctx, `DELETE FROM secuencias WHERE id=? AND tenant_id=?`, id, s.tid)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return ErrNotFound
	}
	_, err = s.DB.ExecContext(ctx, `DELETE FROM seguimiento_en_curso WHERE tenant_id=? AND secuencia_id=?`, s.tid, id)
	return err
}

// SecuenciaPara: la secuencia activa que corresponde a un modelo (productos > categoría > todas), o nil.
func (s *Store) SecuenciaPara(ctx context.Context, codigo, categoria string) (*Secuencia, error) {
	qs, err := s.ListarSecuencias(ctx)
	if err != nil {
		return nil, err
	}
	codigo, categoria = strings.ToUpper(strings.TrimSpace(codigo)), normCategoria(categoria)
	var porCat, todas *Secuencia
	for _, q := range qs {
		if !q.Activa {
			continue
		}
		switch q.Alcance {
		case "productos":
			if codigo != "" && enLista(codigo, strings.Split(q.AlcanceValor, ",")) {
				return q, nil
			}
		case "categoria":
			if porCat == nil && categoria != "" && normCategoria(q.AlcanceValor) == categoria {
				porCat = q
			}
		default:
			if todas == nil {
				todas = q
			}
		}
	}
	if porCat != nil {
		return porCat, nil
	}
	return todas, nil
}

func normCategoria(c string) string {
	c = strings.ToLower(strings.TrimSpace(c))
	return strings.TrimSuffix(strings.TrimSuffix(c, "s"), "e")
}

// ---------------------------------------------------------------------------
// Seguimiento en curso

type SeguimientoEnCurso struct {
	ID             int64      `json:"-"`
	ConversationID int64      `json:"conversation_id"`
	SecuenciaID    int64      `json:"secuencia_id"`
	Nombre         string     `json:"nombre"`
	Paso           int        `json:"paso"` // 1…total: el PRÓXIMO paso
	Total          int        `json:"total"`
	ProximoEn      *time.Time `json:"proximo_en"`
	Estado         string     `json:"estado"`
	ProductCode    string     `json:"-"`
	Nodo           string     `json:"nodo,omitempty"` // flujos: el próximo nodo a ejecutar
	Prueba         bool       `json:"prueba,omitempty"`
	EscalaMin      int        `json:"-"`
	indice         int
}

// Indice: el próximo paso (base 0).
func (e *SeguimientoEnCurso) Indice() int { return e.indice }

func (s *Store) scanEnCurso(row interface{ Scan(...any) error }) (*SeguimientoEnCurso, error) {
	e := &SeguimientoEnCurso{}
	var prox sql.NullTime
	var pasos string
	var prueba int
	if err := row.Scan(&e.ID, &e.ConversationID, &e.SecuenciaID, &e.indice, &prox, &e.Estado, &e.ProductCode, &e.Nombre, &pasos,
		&e.Nodo, &prueba, &e.EscalaMin); err != nil {
		return nil, err
	}
	e.Prueba = prueba != 0
	var ps []Paso
	_ = json.Unmarshal([]byte(pasos), &ps)
	e.Total, e.Paso = len(ps), e.indice+1
	if prox.Valid {
		t := prox.Time
		e.ProximoEn = &t
	}
	return e, nil
}

const enCursoCols = `e.id, e.conversation_id, e.secuencia_id, e.paso, e.proximo_en, e.estado, e.product_code, q.nombre, q.pasos,
	e.nodo, e.prueba, e.escala_min`

// SeguimientoDe: el de una conversación, o nil.
func (s *Store) SeguimientoDe(ctx context.Context, convID int64) (*SeguimientoEnCurso, error) {
	e, err := s.scanEnCurso(s.DB.QueryRowContext(ctx, `SELECT `+enCursoCols+` FROM seguimiento_en_curso e
		JOIN secuencias q ON q.id=e.secuencia_id AND q.tenant_id=e.tenant_id WHERE e.tenant_id=? AND e.conversation_id=?`, s.tid, convID))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return e, err
}

// ArmarSeguimiento: el bot dejó la conversación esperando a la clienta con un modelo en foco. Si ya había uno
// pausado (ella respondió), sigue según al_responder; si ya terminó o se detuvo, no se vuelve a armar.
func (s *Store) ArmarSeguimiento(ctx context.Context, convID int64, q *Secuencia, codigo string, desde time.Time) error {
	if q == nil || len(q.Pasos) == 0 {
		return nil
	}
	prev, err := s.SeguimientoDe(ctx, convID)
	if err != nil {
		return err
	}
	if prev != nil && prev.Prueba && prev.Estado != "terminado" && prev.Estado != "detenido" {
		return nil // una prueba del editor manda hasta que termine; «Probar» de nuevo la reinicia
	}
	if q.Grafo != nil || (prev != nil && prev.Nodo != "") {
		return s.armarFlujo(ctx, convID, q, prev, codigo, desde)
	}
	idx := 0
	if prev != nil {
		switch {
		case prev.Estado == EstadoActivo && prev.SecuenciaID == q.ID:
			return nil // ya corre: el reloj no se reinicia con cada mensaje del bot
		case prev.Estado == "terminado" || prev.Estado == "detenido":
			return nil
		case prev.Estado == EstadoPausado && prev.SecuenciaID == q.ID:
			switch q.AlResponder {
			case "detener":
				return nil
			case "continuar":
				idx = prev.indice
			}
		}
	}
	if idx >= len(q.Pasos) {
		return s.marcarEnCurso(ctx, convID, "terminado")
	}
	prox := desde.Add(time.Duration(q.Pasos[idx].EsperaMin) * time.Minute)
	t := now()
	if prev == nil {
		_, err = s.DB.ExecContext(ctx, `INSERT INTO seguimiento_en_curso(tenant_id, conversation_id, secuencia_id, paso, proximo_en, estado,
			product_code, armado_en, actualizado_en) VALUES(?,?,?,?,?,?,?,?,?)`, s.tid, convID, q.ID, idx, prox.UTC(), EstadoActivo,
			strings.ToUpper(codigo), t, t)
		return err
	}
	_, err = s.DB.ExecContext(ctx, `UPDATE seguimiento_en_curso SET secuencia_id=?, paso=?, proximo_en=?, estado=?, product_code=?,
		actualizado_en=? WHERE tenant_id=? AND conversation_id=?`, q.ID, idx, prox.UTC(), EstadoActivo, strings.ToUpper(codigo), t, s.tid, convID)
	return err
}

// PausarSeguimiento: la clienta escribió. Lo que estaba activo queda pausado y se anota que respondió.
func (s *Store) PausarSeguimiento(ctx context.Context, convID int64) error {
	_, err := s.PausarYVer(ctx, convID)
	return err
}

// PausarYVer pausa el seguimiento de la conversación y, si ella respondió a un paso que SÍ salió, devuelve el seguimiento
// (para que el bot avise al equipo); si no había nada en curso o aún no salía ningún paso, nil.
func (s *Store) PausarYVer(ctx context.Context, convID int64) (*SeguimientoEnCurso, error) {
	e, err := s.SeguimientoDe(ctx, convID)
	if err != nil || e == nil || e.Estado != EstadoActivo {
		return nil, err
	}
	if err := s.marcarEnCurso(ctx, convID, EstadoPausado); err != nil {
		return nil, err
	}
	if e.indice == 0 {
		return nil, nil
	}
	_ = s.AnotarEnvio(ctx, convID, e.SecuenciaID, e.indice, 0, "", "respondio", "")
	return e, nil
}

// FilaTablero: una conversación en el tablero de una secuencia (una columna por paso, como las etapas de Kommo).
type FilaTablero struct {
	ConversationID int64      `json:"conversation_id"`
	Nombre         string     `json:"nombre"`
	Telefono       string     `json:"telefono"`
	Producto       string     `json:"producto"`
	ProductoNombre string     `json:"producto_nombre"`
	Columna        string     `json:"columna"` // "paso-<n>" (el próximo a enviar) | "respondio" | "terminado" | "detenido"
	Paso           int        `json:"paso"`
	ProximoEn      *time.Time `json:"proximo_en"`
	Actualizado    time.Time  `json:"actualizado_en"`
}

// TableroSecuencia: conversaciones de una secuencia en los últimos `dias` días, para el tablero del panel.
func (s *Store) TableroSecuencia(ctx context.Context, secID int64, dias int) ([]FilaTablero, error) {
	if _, err := s.GetSecuencia(ctx, secID); err != nil {
		return nil, err
	}
	rows, err := s.DB.QueryContext(ctx, `SELECT e.conversation_id, COALESCE(cu.name,''), COALESCE(cu.phone,''), e.product_code,
		COALESCE(p.name,''), e.paso, e.estado, e.proximo_en, e.actualizado_en
		FROM seguimiento_en_curso e JOIN conversations cv ON cv.id=e.conversation_id AND cv.tenant_id=e.tenant_id
		LEFT JOIN customers cu ON cu.id=cv.customer_id AND cu.tenant_id=cv.tenant_id
		LEFT JOIN products p ON p.code=e.product_code AND p.tenant_id=e.tenant_id
		WHERE e.tenant_id=? AND e.secuencia_id=? AND e.actualizado_en>=? ORDER BY e.actualizado_en DESC LIMIT 500`,
		s.tid, secID, time.Now().AddDate(0, 0, -dias).UTC())
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []FilaTablero{}
	for rows.Next() {
		var f FilaTablero
		var estado string
		var prox sql.NullTime
		if err := rows.Scan(&f.ConversationID, &f.Nombre, &f.Telefono, &f.Producto, &f.ProductoNombre, &f.Paso, &estado, &prox,
			&f.Actualizado); err != nil {
			return nil, err
		}
		if prox.Valid {
			t := prox.Time
			f.ProximoEn = &t
		}
		switch estado {
		case EstadoActivo:
			f.Columna = fmt.Sprintf("paso-%d", f.Paso+1)
		case EstadoPausado:
			f.Columna = "respondio"
		default:
			f.Columna = estado
		}
		f.Paso++
		out = append(out, f)
	}
	return out, rows.Err()
}

// DetenerSeguimiento: una persona lo detuvo, la asesora tomó la conversación o se confirmó la compra.
func (s *Store) DetenerSeguimiento(ctx context.Context, convID int64) error {
	var n int
	if err := s.DB.QueryRowContext(ctx, `SELECT COUNT(*) FROM conversations WHERE id=? AND tenant_id=?`, convID, s.tid).Scan(&n); err != nil {
		return err
	}
	if n == 0 {
		return ErrNotFound
	}
	return s.marcarEnCurso(ctx, convID, "detenido")
}

func (s *Store) marcarEnCurso(ctx context.Context, convID int64, estado string) error {
	_, err := s.DB.ExecContext(ctx, `UPDATE seguimiento_en_curso SET estado=?, actualizado_en=? WHERE tenant_id=? AND conversation_id=?`,
		estado, now(), s.tid, convID)
	return err
}

// AvanzarSeguimiento: salió (o se saltó) el paso idx. Programa el siguiente o lo da por terminado.
func (s *Store) AvanzarSeguimiento(ctx context.Context, convID int64, q *Secuencia, idx int, desde time.Time) (terminado bool, err error) {
	next := idx + 1
	if next >= len(q.Pasos) {
		_, err = s.DB.ExecContext(ctx, `UPDATE seguimiento_en_curso SET paso=?, proximo_en=NULL, estado='terminado', actualizado_en=?
			WHERE tenant_id=? AND conversation_id=?`, next, now(), s.tid, convID)
		return true, err
	}
	prox := desde.Add(time.Duration(q.Pasos[next].EsperaMin) * time.Minute)
	_, err = s.DB.ExecContext(ctx, `UPDATE seguimiento_en_curso SET paso=?, proximo_en=?, actualizado_en=? WHERE tenant_id=? AND conversation_id=?`,
		next, prox.UTC(), now(), s.tid, convID)
	return false, err
}

// PosponerSeguimiento: el paso no puede salir ahora (horario, tope): se reintenta en `en`.
func (s *Store) PosponerSeguimiento(ctx context.Context, convID int64, en time.Time) error {
	_, err := s.DB.ExecContext(ctx, `UPDATE seguimiento_en_curso SET proximo_en=?, actualizado_en=? WHERE tenant_id=? AND conversation_id=?`,
		en.UTC(), now(), s.tid, convID)
	return err
}

// SeguimientosVencidos: los activos cuyo próximo paso ya toca, de esta empresa.
func (s *Store) SeguimientosVencidos(ctx context.Context, hasta time.Time, max int) ([]*SeguimientoEnCurso, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT `+enCursoCols+` FROM seguimiento_en_curso e JOIN secuencias q ON q.id=e.secuencia_id
		AND q.tenant_id=e.tenant_id WHERE e.tenant_id=? AND e.estado='activo' AND (q.activa=1 OR e.prueba=1) AND e.proximo_en<=?
		ORDER BY e.proximo_en LIMIT ?`,
		s.tid, hasta.UTC(), max)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*SeguimientoEnCurso
	for rows.Next() {
		e, err := s.scanEnCurso(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

// AnotarEnvio deja constancia de un paso (enviado, omitido o respondido).
func (s *Store) AnotarEnvio(ctx context.Context, convID, secID int64, paso int, numeroID int64, tipo, resultado, motivo string) error {
	if len([]rune(motivo)) > 300 {
		motivo = string([]rune(motivo)[:300])
	}
	_, err := s.DB.ExecContext(ctx, `INSERT INTO seguimiento_envios(tenant_id, conversation_id, secuencia_id, paso, numero_id, tipo, resultado,
		motivo, enviado_en) VALUES(?,?,?,?,?,?,?,?,?)`, s.tid, convID, secID, paso, numeroID, tipo, resultado, motivo, now())
	return err
}

// EnviadosHoy: mensajes iniciados por el bot hoy (Lima) por un número (0 = el principal).
func (s *Store) EnviadosHoy(ctx context.Context, numeroID int64) int {
	var n int
	_ = s.DB.QueryRowContext(ctx, `SELECT COUNT(*) FROM seguimiento_envios WHERE tenant_id=? AND numero_id=? AND resultado='enviado'
		AND enviado_en>=?`, s.tid, numeroID, inicioDiaLima(time.Now())).Scan(&n)
	return n
}

func inicioDiaLima(t time.Time) time.Time {
	l := t.In(Lima)
	return time.Date(l.Year(), l.Month(), l.Day(), 0, 0, 0, 0, Lima).UTC()
}

// UltimoEntrante: cuándo escribió la clienta por última vez (para la ventana de 24 h de WhatsApp).
func (s *Store) UltimoEntrante(ctx context.Context, convID int64) (time.Time, bool) {
	var t sql.NullString
	if err := s.DB.QueryRowContext(ctx, `SELECT MAX(created_at) FROM messages WHERE tenant_id=? AND conversation_id=? AND direction='in'`,
		s.tid, convID).Scan(&t); err != nil || !t.Valid || t.String == "" {
		return time.Time{}, false
	}
	pt := parseTiempo(t.String)
	return pt, !pt.IsZero()
}

// ---------------------------------------------------------------------------
// Números de WhatsApp

type NumeroWhatsApp struct {
	ID            int64  `json:"id"`
	Nombre        string `json:"nombre"`
	Canal         string `json:"canal"`
	Numero        string `json:"numero"`
	Instancia     string `json:"-"`
	Token         string `json:"-"`
	PhoneNumberID string `json:"phone_number_id,omitempty"`
	Uso           string `json:"uso"`
	TopeDiario    int    `json:"tope_diario"`
	EnviadosHoy   int    `json:"enviados_hoy"`
	Conectado     bool   `json:"conectado"`
	Principal     bool   `json:"principal"`
}

var (
	CanalesWhatsApp = []string{"evolution", "cloud"}
	UsosNumero      = []string{"entrada", "salida", "ambos"}
)

func (n *NumeroWhatsApp) normalizar() error {
	n.Nombre = strings.TrimSpace(n.Nombre)
	n.Numero = soloDigitos(n.Numero)
	if n.Nombre == "" || utf8.RuneCountInString(n.Nombre) > 100 {
		return fmt.Errorf("%w: el nombre es obligatorio", ErrSeguimientoInvalido)
	}
	if n.Canal == "" {
		n.Canal = "evolution"
	}
	if n.Uso == "" {
		n.Uso = "salida"
	}
	if !enLista(n.Canal, CanalesWhatsApp) || !enLista(n.Uso, UsosNumero) {
		return fmt.Errorf("%w: canal o uso", ErrSeguimientoInvalido)
	}
	if n.Numero != "" && (len(n.Numero) < 8 || len(n.Numero) > 15) {
		return fmt.Errorf("%w: el número lleva de 8 a 15 dígitos con el código de país", ErrSeguimientoInvalido)
	}
	if n.TopeDiario < 0 || n.TopeDiario > 1000 {
		return fmt.Errorf("%w: el tope diario va de 0 a 1000", ErrSeguimientoInvalido)
	}
	return nil
}

const numeroCols = `id, nombre, canal, numero, instancia, token, phone_number_id, uso, tope_diario`

func scanNumero(sc interface{ Scan(...any) error }) (*NumeroWhatsApp, error) {
	n := &NumeroWhatsApp{}
	err := sc.Scan(&n.ID, &n.Nombre, &n.Canal, &n.Numero, &n.Instancia, &n.Token, &n.PhoneNumberID, &n.Uso, &n.TopeDiario)
	return n, err
}

func (s *Store) ListarNumeros(ctx context.Context) ([]*NumeroWhatsApp, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT `+numeroCols+` FROM numeros_whatsapp WHERE tenant_id=? ORDER BY id`, s.tid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*NumeroWhatsApp{}
	for rows.Next() {
		n, err := scanNumero(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, n)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	for _, n := range out {
		n.EnviadosHoy = s.EnviadosHoy(ctx, n.ID)
	}
	return out, nil
}

func (s *Store) GetNumero(ctx context.Context, id int64) (*NumeroWhatsApp, error) {
	n, err := scanNumero(s.DB.QueryRowContext(ctx, `SELECT `+numeroCols+` FROM numeros_whatsapp WHERE id=? AND tenant_id=?`, id, s.tid))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err == nil {
		n.EnviadosHoy = s.EnviadosHoy(ctx, n.ID)
	}
	return n, err
}

// CrearNumero: instancia y token los pone quien llama (api), nunca el panel.
func (s *Store) CrearNumero(ctx context.Context, n *NumeroWhatsApp) error {
	if err := n.normalizar(); err != nil {
		return err
	}
	res, err := s.DB.ExecContext(ctx, `INSERT INTO numeros_whatsapp(tenant_id, nombre, canal, numero, instancia, token, phone_number_id, uso,
		tope_diario, creado_en) VALUES(?,?,?,?,?,?,?,?,?,?)`, s.tid, n.Nombre, n.Canal, n.Numero, n.Instancia, n.Token, n.PhoneNumberID,
		n.Uso, n.TopeDiario, now())
	if err != nil {
		return err
	}
	n.ID, _ = res.LastInsertId()
	return nil
}

// GuardarNumero cambia nombre, número, uso, tope y phone_number_id (no el canal, la instancia ni el token).
func (s *Store) GuardarNumero(ctx context.Context, n *NumeroWhatsApp) error {
	prev, err := s.GetNumero(ctx, n.ID)
	if err != nil {
		return err
	}
	n.Canal = prev.Canal
	if err := n.normalizar(); err != nil {
		return err
	}
	_, err = s.DB.ExecContext(ctx, `UPDATE numeros_whatsapp SET nombre=?, numero=?, uso=?, tope_diario=?, phone_number_id=? WHERE id=? AND tenant_id=?`,
		n.Nombre, n.Numero, n.Uso, n.TopeDiario, n.PhoneNumberID, n.ID, s.tid)
	return err
}

func (s *Store) BorrarNumero(ctx context.Context, id int64) error {
	res, err := s.DB.ExecContext(ctx, `DELETE FROM numeros_whatsapp WHERE id=? AND tenant_id=?`, id, s.tid)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return ErrNotFound
	}
	return nil
}

// NumeroPorInstancia: el número secundario cuya instancia de evolution es `inst` (para los mensajes que llegan a él).
func (s *Store) NumeroPorInstancia(ctx context.Context, inst string) (*NumeroWhatsApp, error) {
	n, err := scanNumero(s.DB.QueryRowContext(ctx, `SELECT `+numeroCols+` FROM numeros_whatsapp WHERE tenant_id=? AND instancia=?`, s.tid, inst))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	return n, err
}

// ---------------------------------------------------------------------------
// Origen de la conversación (anuncio)

type Origen struct {
	Canal    string    `json:"canal"`
	AdID     string    `json:"ad_id"`
	Titular  string    `json:"titular"`
	CtwaClid string    `json:"ctwa_clid"`
	LlegoEn  time.Time `json:"llego_en"`
}

// AnotarOrigen guarda de qué anuncio llegó la conversación. Solo el primero cuenta (la atribución es de quien la trajo),
// salvo que el nuevo traiga el id del anuncio y el guardado no.
func (s *Store) AnotarOrigen(ctx context.Context, convID int64, o Origen) error {
	o.AdID, o.Titular, o.CtwaClid = recorta(strings.TrimSpace(o.AdID), 100), recorta(strings.TrimSpace(o.Titular), 300),
		recorta(strings.TrimSpace(o.CtwaClid), 300)
	if o.AdID == "" && o.Titular == "" {
		return nil
	}
	prev, err := s.OrigenDe(ctx, convID)
	if err != nil {
		return err
	}
	if prev == nil {
		_, err = s.DB.ExecContext(ctx, `INSERT INTO origen_conversacion(tenant_id, conversation_id, canal, ad_id, titular, ctwa_clid, llego_en)
			SELECT ?, id, ?, ?, ?, ?, ? FROM conversations WHERE id=? AND tenant_id=?`, s.tid, o.Canal, o.AdID, o.Titular, o.CtwaClid, now(),
			convID, s.tid)
		return err
	}
	if prev.AdID == "" && o.AdID != "" {
		_, err = s.DB.ExecContext(ctx, `UPDATE origen_conversacion SET ad_id=?, ctwa_clid=?, titular=? WHERE tenant_id=? AND conversation_id=?`,
			o.AdID, o.CtwaClid, firstNonEmptyS(o.Titular, prev.Titular), s.tid, convID)
	}
	return err
}

func (s *Store) OrigenDe(ctx context.Context, convID int64) (*Origen, error) {
	o := &Origen{}
	err := s.DB.QueryRowContext(ctx, `SELECT canal, ad_id, titular, ctwa_clid, llego_en FROM origen_conversacion WHERE tenant_id=? AND conversation_id=?`,
		s.tid, convID).Scan(&o.Canal, &o.AdID, &o.Titular, &o.CtwaClid, &o.LlegoEn)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return o, err
}

type FilaCampana struct {
	Clave          string  `json:"clave"`
	AdID           string  `json:"ad_id"`
	Titular        string  `json:"titular"`
	Canal          string  `json:"canal"`
	Conversaciones int     `json:"conversaciones"`
	Calientes      int     `json:"calientes"`
	Pedidos        int     `json:"pedidos"`
	Confirmados    int     `json:"confirmados"`
	Monto          float64 `json:"monto"`
}

// Campanas: por anuncio, lo que trajo entre desde y hasta (fechas de Lima, inclusivas). «Sin anuncio» = clientas nuevas
// del periodo sin origen.
func (s *Store) Campanas(ctx context.Context, desde, hasta time.Time) ([]FilaCampana, FilaCampana, error) {
	ini, fin := inicioDiaLima(desde), inicioDiaLima(hasta).Add(24*time.Hour)
	q := `SELECT CASE WHEN o.ad_id<>'' THEN o.ad_id ELSE o.titular END AS clave, MAX(o.ad_id), MAX(o.titular), MAX(o.canal),
		COUNT(DISTINCT o.conversation_id),
		COUNT(DISTINCT CASE WHEN a.id IS NOT NULL THEN o.conversation_id END),
		COUNT(DISTINCT od.id),
		COUNT(DISTINCT CASE WHEN od.status IN ` + compraSQL + ` THEN od.id END),
		COALESCE(SUM(CASE WHEN od.status IN ` + compraSQL + ` THEN od.total ELSE 0 END),0)
		FROM origen_conversacion o
		JOIN conversations cv ON cv.id=o.conversation_id AND cv.tenant_id=o.tenant_id
		LEFT JOIN alertas a ON a.conversation_id=o.conversation_id AND a.tenant_id=o.tenant_id AND a.tipo='caliente'
		LEFT JOIN orders od ON od.customer_id=cv.customer_id AND od.tenant_id=o.tenant_id AND od.created_at>=o.llego_en
		WHERE o.tenant_id=? AND o.llego_en>=? AND o.llego_en<? GROUP BY clave ORDER BY 9 DESC, 5 DESC`
	rows, err := s.DB.QueryContext(ctx, q, s.tid, ini, fin)
	if err != nil {
		return nil, FilaCampana{}, err
	}
	defer rows.Close()
	out := []FilaCampana{}
	var tot FilaCampana
	for rows.Next() {
		var f FilaCampana
		if err := rows.Scan(&f.Clave, &f.AdID, &f.Titular, &f.Canal, &f.Conversaciones, &f.Calientes, &f.Pedidos, &f.Confirmados, &f.Monto); err != nil {
			return nil, tot, err
		}
		out = append(out, f)
	}
	if err := rows.Err(); err != nil {
		return nil, tot, err
	}
	// Orgánico: clientas nuevas del periodo cuya conversación no tiene origen.
	var org FilaCampana
	org.Clave, org.Titular = "", "Sin anuncio"
	if err := s.DB.QueryRowContext(ctx, `SELECT COUNT(DISTINCT cv.id),
		COUNT(DISTINCT CASE WHEN a.id IS NOT NULL THEN cv.id END),
		COUNT(DISTINCT od.id),
		COUNT(DISTINCT CASE WHEN od.status IN `+compraSQL+` THEN od.id END),
		COALESCE(SUM(CASE WHEN od.status IN `+compraSQL+` THEN od.total ELSE 0 END),0)
		FROM customers cu JOIN conversations cv ON cv.customer_id=cu.id AND cv.tenant_id=cu.tenant_id
		LEFT JOIN origen_conversacion o ON o.conversation_id=cv.id AND o.tenant_id=cv.tenant_id
		LEFT JOIN alertas a ON a.conversation_id=cv.id AND a.tenant_id=cv.tenant_id AND a.tipo='caliente'
		LEFT JOIN orders od ON od.customer_id=cu.id AND od.tenant_id=cu.tenant_id
		WHERE cu.tenant_id=? AND cu.created_at>=? AND cu.created_at<? AND o.conversation_id IS NULL`, s.tid, ini, fin).
		Scan(&org.Conversaciones, &org.Calientes, &org.Pedidos, &org.Confirmados, &org.Monto); err != nil {
		return nil, tot, err
	}
	if org.Conversaciones > 0 {
		out = append(out, org)
	}
	for _, f := range out {
		tot.Conversaciones += f.Conversaciones
		tot.Calientes += f.Calientes
		tot.Pedidos += f.Pedidos
		tot.Confirmados += f.Confirmados
		tot.Monto += f.Monto
	}
	tot.Clave, tot.Titular = "total", "Total"
	return out, tot, nil
}

// ---------------------------------------------------------------------------

func b2i(b bool) int {
	if b {
		return 1
	}
	return 0
}

func firstNonEmptyS(v ...string) string {
	for _, x := range v {
		if x != "" {
			return x
		}
	}
	return ""
}

// CubrePromo: si una promo vigente alcanza a un producto (para el paso «oferta» del seguimiento).
func CubrePromo(p PromoConocimiento, prod *Product) bool { return cubrePromo(p, prod) }

// SalirDeMasAdelante: la clienta que estaba en «Más adelante» volvió a escribir. Si nadie fijó la etapa a mano, la
// etapa vuelve a decidirla el bot (queda vacía hasta el próximo guardado del contexto).
func (s *Store) SalirDeMasAdelante(ctx context.Context, convID int64) error {
	res, err := s.DB.ExecContext(ctx, `UPDATE customers SET etapa='' WHERE tenant_id=? AND etapa='mas_adelante' AND etapa_fijada=0
		AND id=(SELECT customer_id FROM conversations WHERE id=? AND tenant_id=?)`, s.tid, convID, s.tid)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n > 0 {
		// Volvió a escribir: el seguimiento que se detuvo al pasarla a «Más adelante» puede volver a armarse.
		_, err = s.DB.ExecContext(ctx, `UPDATE seguimiento_en_curso SET estado='pausado', actualizado_en=? WHERE tenant_id=?
			AND conversation_id=? AND estado='detenido'`, now(), s.tid, convID)
	}
	return err
}

// EtapaPorSeguimiento: el bot pone una etapa propia de esta parte («mas_adelante», «perdida») sin fijarla: una persona la
// puede cambiar y el bot la vuelve a mover cuando la clienta escriba.
func (s *Store) EtapaPorSeguimiento(ctx context.Context, convID int64, etapa string) error {
	if !ValidEtapa(etapa) {
		return fmt.Errorf("etapa inválida: %s", etapa)
	}
	var custID int64
	var prev string
	if err := s.DB.QueryRowContext(ctx, `SELECT cu.id, cu.etapa FROM conversations cv JOIN customers cu ON cu.id=cv.customer_id
		AND cu.tenant_id=cv.tenant_id WHERE cv.id=? AND cv.tenant_id=? AND cu.etapa_fijada=0`, convID, s.tid).Scan(&custID, &prev); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil // fijada a mano: manda la persona
		}
		return err
	}
	if prev == etapa {
		return nil
	}
	if _, err := s.DB.ExecContext(ctx, `UPDATE customers SET etapa=? WHERE id=? AND tenant_id=? AND etapa_fijada=0`, etapa, custID, s.tid); err != nil {
		return err
	}
	return s.registrar(ConAutor(ctx, "bot"), s.DB, custID, "etapa", "Etapa: "+EtapaLabel[prev]+" → "+EtapaLabel[etapa], 0)
}

// CustomerDeConversacion: la clienta de una conversación de la empresa.
func (s *Store) CustomerDeConversacion(ctx context.Context, convID int64) (int64, error) {
	var id int64
	err := s.DB.QueryRowContext(ctx, `SELECT customer_id FROM conversations WHERE id=? AND tenant_id=?`, convID, s.tid).Scan(&id)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, ErrNotFound
	}
	return id, err
}
