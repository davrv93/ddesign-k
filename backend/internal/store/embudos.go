package store

// Embudos por promo (15-10-2026, docs/FUENTES_CONTRATO.md §2). El embudo general es el tablero de pedidos de siempre y
// no se guarda (id 0). Un embudo propio tiene etapas libres y NO cambia el estado del pedido: el stock depende de
// orders.status, así que la etapa del embudo va aparte, en pedido_embudo.
//
// Un pedido entra solo al embudo de una promo cuando nace de una conversación en la que el robot respondió con esa promo
// (uso_fuentes): no hace falta enganchar la creación de pedidos del bot, se deduce al leer. Una persona puede moverlo
// de etapa (fila explícita), meterlo a mano o sacarlo (fila con etapa vacía = «sacado»).

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"
	"unicode/utf8"
)

type Embudo struct {
	ID              int64     `json:"id"`
	Nombre          string    `json:"nombre"`
	Etapas          []string  `json:"etapas"`
	PromoID         int64     `json:"promo_id"`
	General         bool      `json:"general"`
	KommoPipelineID int64     `json:"kommo_pipeline_id"`
	CreadoEn        time.Time `json:"creado_en"`
}

// EtapasSugeridas: las de un embudo de promo recién creado (el asistente las ofrece editables).
var EtapasSugeridas = []string{"Interesada", "Cotizada", "Separada", "Pagada"}

// EmbudoGeneral es el tablero de pedidos de siempre.
func EmbudoGeneral() Embudo {
	return Embudo{ID: 0, Nombre: "General", Etapas: append([]string(nil), OrderStatuses...), General: true}
}

func (e *Embudo) normalizar() error {
	e.Nombre = strings.TrimSpace(e.Nombre)
	if e.Nombre == "" || utf8.RuneCountInString(e.Nombre) > 200 {
		return fmt.Errorf("%w: el embudo necesita un nombre (hasta 200 caracteres)", ErrFuenteInvalida)
	}
	var et []string
	for _, x := range e.Etapas {
		if x = strings.TrimSpace(x); x != "" && !enLista(x, et) {
			if utf8.RuneCountInString(x) > 100 {
				return fmt.Errorf("%w: etapa demasiado larga", ErrFuenteInvalida)
			}
			et = append(et, x)
		}
	}
	if len(et) < 2 || len(et) > 12 {
		return fmt.Errorf("%w: un embudo lleva entre 2 y 12 etapas", ErrFuenteInvalida)
	}
	e.Etapas = et
	return nil
}

func scanEmbudo(sc interface{ Scan(...any) error }) (*Embudo, error) {
	e := &Embudo{}
	var et string
	if err := sc.Scan(&e.ID, &e.Nombre, &et, &e.PromoID, &e.KommoPipelineID, &e.CreadoEn); err != nil {
		return nil, err
	}
	_ = json.Unmarshal([]byte(et), &e.Etapas)
	if e.Etapas == nil {
		e.Etapas = []string{}
	}
	return e, nil
}

const embudoCols = `id, nombre, etapas, promo_id, kommo_pipeline_id, creado_en`

// ListarEmbudos: el general primero y luego los propios, del más nuevo al más viejo.
func (s *Store) ListarEmbudos(ctx context.Context) ([]Embudo, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT `+embudoCols+` FROM embudos WHERE tenant_id=? ORDER BY id DESC`, s.tid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Embudo{EmbudoGeneral()}
	for rows.Next() {
		e, err := scanEmbudo(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, *e)
	}
	return out, rows.Err()
}

func (s *Store) GetEmbudo(ctx context.Context, id int64) (*Embudo, error) {
	if id == 0 {
		g := EmbudoGeneral()
		return &g, nil
	}
	e, err := scanEmbudo(s.DB.QueryRowContext(ctx, `SELECT `+embudoCols+` FROM embudos WHERE id=? AND tenant_id=?`, id, s.tid))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	return e, err
}

// EmbudoDePromo: el embudo propio de una promo (nil si usa el general).
func (s *Store) EmbudoDePromo(ctx context.Context, promoID int64) (*Embudo, error) {
	e, err := scanEmbudo(s.DB.QueryRowContext(ctx, `SELECT `+embudoCols+` FROM embudos WHERE promo_id=? AND tenant_id=?
		ORDER BY id DESC LIMIT 1`, promoID, s.tid))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return e, err
}

// CrearEmbudo guarda un embudo propio. Si es de una promo, la promo tiene que ser de esta empresa.
func (s *Store) CrearEmbudo(ctx context.Context, e *Embudo) error {
	if err := e.normalizar(); err != nil {
		return err
	}
	if e.PromoID > 0 {
		f, err := s.GetFuente(ctx, e.PromoID)
		if err != nil {
			return fmt.Errorf("promo %d: %w", e.PromoID, err)
		}
		if f.Tipo != "promo" {
			return fmt.Errorf("%w: la fuente %d no es una promo", ErrFuenteInvalida, e.PromoID)
		}
	}
	et, _ := json.Marshal(e.Etapas)
	t := now()
	res, err := s.DB.ExecContext(ctx, `INSERT INTO embudos(tenant_id, nombre, etapas, promo_id, kommo_pipeline_id, creado_en)
		VALUES(?,?,?,?,0,?)`, s.tid, e.Nombre, string(et), e.PromoID, t)
	if err != nil {
		return err
	}
	e.ID, _ = res.LastInsertId()
	e.CreadoEn = t
	return nil
}

// GuardarEmbudo cambia nombre y etapas. Los pedidos que estaban en una etapa que ya no existe pasan a la primera.
func (s *Store) GuardarEmbudo(ctx context.Context, e *Embudo) error {
	prev, err := s.GetEmbudo(ctx, e.ID)
	if err != nil {
		return err
	}
	if prev.General {
		return fmt.Errorf("%w: el embudo general no se edita aquí", ErrFuenteInvalida)
	}
	if err := e.normalizar(); err != nil {
		return err
	}
	et, _ := json.Marshal(e.Etapas)
	if _, err := s.DB.ExecContext(ctx, `UPDATE embudos SET nombre=?, etapas=? WHERE id=? AND tenant_id=?`,
		e.Nombre, string(et), e.ID, s.tid); err != nil {
		return err
	}
	ph := strings.TrimSuffix(strings.Repeat("?,", len(e.Etapas)), ",")
	args := []any{e.Etapas[0], e.ID, s.tid}
	for _, x := range e.Etapas {
		args = append(args, x)
	}
	_, err = s.DB.ExecContext(ctx, `UPDATE pedido_embudo SET etapa=? WHERE embudo_id=? AND tenant_id=? AND etapa<>'' AND etapa NOT IN (`+ph+`)`, args...)
	e.PromoID, e.KommoPipelineID, e.CreadoEn = prev.PromoID, prev.KommoPipelineID, prev.CreadoEn
	return err
}

func (s *Store) GuardarPipelineKommo(ctx context.Context, embudoID, pipelineID int64) error {
	_, err := s.DB.ExecContext(ctx, `UPDATE embudos SET kommo_pipeline_id=? WHERE id=? AND tenant_id=?`, pipelineID, embudoID, s.tid)
	return err
}

func (s *Store) BorrarEmbudo(ctx context.Context, id int64) error {
	if id == 0 {
		return fmt.Errorf("%w: el embudo general no se borra", ErrFuenteInvalida)
	}
	res, err := s.DB.ExecContext(ctx, `DELETE FROM embudos WHERE id=? AND tenant_id=?`, id, s.tid)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return ErrNotFound
	}
	_, _ = s.DB.ExecContext(ctx, `DELETE FROM pedido_embudo WHERE embudo_id=? AND tenant_id=?`, id, s.tid)
	return nil
}

// PedidoEnEmbudo: un pedido del tablero con su etapa en el embudo.
type PedidoEnEmbudo struct {
	*Order
	Etapa string `json:"etapa"`
}

// Holgura para atribuir un pedido a una promo: el pedido de la cita o del flujo fijo puede nacer en el mismo turno en
// que el robot nombró la promo, un instante antes de que se anote el uso.
const holguraPromo = time.Hour

// pedidosDePromo: ids de pedidos nacidos de una conversación en la que el robot respondió con la promo.
func (s *Store) pedidosDePromo(ctx context.Context, promoID int64) ([]int64, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT o.id, o.created_at, min(u.creado_en) FROM orders o
		JOIN conversations cv ON cv.customer_id=o.customer_id AND cv.tenant_id=o.tenant_id
		JOIN uso_fuentes u ON u.conversation_id=cv.id AND u.tenant_id=o.tenant_id AND u.fuente_id=?
		WHERE o.tenant_id=? GROUP BY o.id, o.created_at`, promoID, s.tid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []int64
	for rows.Next() {
		var id int64
		var creado time.Time
		var primerUso any
		if err := rows.Scan(&id, &creado, &primerUso); err != nil {
			return nil, err
		}
		if u := aTiempo(primerUso); !u.IsZero() && !creado.Before(u.Add(-holguraPromo)) {
			out = append(out, id)
		}
	}
	return out, rows.Err()
}

// PedidosDeEmbudo: los pedidos de un embudo propio con su etapa (los explícitos y los de su promo).
func (s *Store) PedidosDeEmbudo(ctx context.Context, embudoID int64) (*Embudo, []PedidoEnEmbudo, error) {
	e, err := s.GetEmbudo(ctx, embudoID)
	if err != nil {
		return nil, nil, err
	}
	if e.General {
		return nil, nil, fmt.Errorf("%w: el embudo general es el tablero de pedidos", ErrFuenteInvalida)
	}
	etapa := map[int64]string{}
	var orden []int64
	if e.PromoID > 0 {
		ids, err := s.pedidosDePromo(ctx, e.PromoID)
		if err != nil {
			return nil, nil, err
		}
		for _, id := range ids {
			etapa[id] = e.Etapas[0]
			orden = append(orden, id)
		}
	}
	rows, err := s.DB.QueryContext(ctx, `SELECT order_id, etapa FROM pedido_embudo WHERE embudo_id=? AND tenant_id=?`, e.ID, s.tid)
	if err != nil {
		return nil, nil, err
	}
	for rows.Next() {
		var id int64
		var et string
		if err := rows.Scan(&id, &et); err != nil {
			rows.Close()
			return nil, nil, err
		}
		if _, ya := etapa[id]; !ya {
			orden = append(orden, id)
		}
		etapa[id] = et // "" = sacado del embudo
	}
	rows.Close()
	out := []PedidoEnEmbudo{}
	for _, id := range orden {
		if etapa[id] == "" {
			continue
		}
		o, err := s.GetOrder(ctx, id)
		if errors.Is(err, ErrNotFound) {
			continue
		}
		if err != nil {
			return nil, nil, err
		}
		et := etapa[id]
		if !enLista(et, e.Etapas) {
			et = e.Etapas[0]
		}
		out = append(out, PedidoEnEmbudo{Order: o, Etapa: et})
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].CreatedAt.After(out[j].CreatedAt) })
	return e, out, nil
}

// MoverPedidoEmbudo mete o mueve un pedido de esta empresa a una etapa del embudo. etapa "" lo saca.
func (s *Store) MoverPedidoEmbudo(ctx context.Context, embudoID, orderID int64, etapa string) error {
	e, err := s.GetEmbudo(ctx, embudoID)
	if err != nil {
		return err
	}
	if e.General {
		return fmt.Errorf("%w: el embudo general se mueve con el estado del pedido", ErrFuenteInvalida)
	}
	if etapa != "" && !enLista(etapa, e.Etapas) {
		return fmt.Errorf("%w: la etapa %q no es de este embudo", ErrFuenteInvalida, etapa)
	}
	if _, err := s.GetOrder(ctx, orderID); err != nil {
		return err
	}
	q := `INSERT INTO pedido_embudo(tenant_id, order_id, embudo_id, etapa, actualizado_en) VALUES(?,?,?,?,?)
		ON CONFLICT(order_id, embudo_id) DO UPDATE SET etapa=excluded.etapa, actualizado_en=excluded.actualizado_en`
	if s.mysql() {
		q = `INSERT INTO pedido_embudo(tenant_id, order_id, embudo_id, etapa, actualizado_en) VALUES(?,?,?,?,?)
		ON DUPLICATE KEY UPDATE etapa=VALUES(etapa), actualizado_en=VALUES(actualizado_en)`
	}
	_, err = s.DB.ExecContext(ctx, q, s.tid, orderID, embudoID, etapa, now())
	return err
}

// ---------------------------------------------------------------------------
// Cifras por promo

type CifrasPromo struct {
	Conversaciones int     `json:"conversaciones"`
	Pedidos        int     `json:"pedidos"`
	Confirmados    int     `json:"confirmados"`
	Pagados        int     `json:"pagados"`
	Monto          float64 `json:"monto"`
}

type ResumenPromo struct {
	ID            int64       `json:"id"`
	Titulo        string      `json:"titulo"`
	Estado        string      `json:"estado"`
	Vigente       bool        `json:"vigente"`
	Programada    bool        `json:"programada"`
	VigenteDesde  string      `json:"vigente_desde"`
	VigenteHasta  string      `json:"vigente_hasta"`
	DiasRestantes *int        `json:"dias_restantes"`
	EmbudoID      int64       `json:"embudo_id"`
	Cifras        CifrasPromo `json:"cifras"`
	Avisos        []string    `json:"avisos"`
}

// ResumenPromos: las promos activas (vigentes y programadas) y las vencidas en los últimos 30 días, con sus cifras.
func (s *Store) ResumenPromos(ctx context.Context, ahora time.Time) ([]ResumenPromo, error) {
	fs, err := s.ListarFuentes(ctx, FiltroFuentes{Tipo: "promo"})
	if err != nil {
		return nil, err
	}
	hoy := HoyLima(ahora)
	hace30 := HoyLima(ahora.AddDate(0, 0, -30))
	out := []ResumenPromo{}
	for _, f := range fs {
		if f.Estado != "activo" && !(f.Estado == "vencido" && f.VigenteHasta >= hace30) {
			continue
		}
		var p PromoDatos
		_ = json.Unmarshal(f.Datos, &p)
		r := ResumenPromo{ID: f.ID, Titulo: f.Titulo, Estado: f.Estado, Vigente: f.Vigente, Programada: f.Programada,
			VigenteDesde: f.VigenteDesde, VigenteHasta: f.VigenteHasta, EmbudoID: p.EmbudoID, Avisos: []string{}}
		if e, err := s.EmbudoDePromo(ctx, f.ID); err == nil && e != nil && r.EmbudoID == 0 {
			r.EmbudoID = e.ID
		}
		if r.Cifras, err = s.cifrasPromo(ctx, f.ID, r.EmbudoID); err != nil {
			return nil, err
		}
		if f.VigenteHasta != "" {
			if fin, err := time.ParseInLocation("2006-01-02", f.VigenteHasta, Lima); err == nil {
				hoyT, _ := time.ParseInLocation("2006-01-02", hoy, Lima)
				d := int(fin.Sub(hoyT).Hours() / 24)
				r.DiasRestantes = &d
				switch {
				case f.Vigente && d == 0:
					r.Avisos = append(r.Avisos, "Vence hoy")
				case f.Vigente && d == 1:
					r.Avisos = append(r.Avisos, "Vence mañana")
				case f.Vigente && d <= 3:
					r.Avisos = append(r.Avisos, fmt.Sprintf("Vence en %d días", d))
				}
			}
		}
		if f.Programada {
			r.Avisos = append(r.Avisos, "Empieza el "+f.VigenteDesde)
		}
		// Vigente hace una semana o más y nadie preguntó en los últimos 7 días.
		if f.Vigente && (f.VigenteDesde == "" || f.VigenteDesde <= HoyLima(ahora.AddDate(0, 0, -7))) {
			var n int
			if err := s.DB.QueryRowContext(ctx, `SELECT count(*) FROM uso_fuentes WHERE tenant_id=? AND fuente_id=? AND creado_en>?`,
				s.tid, f.ID, ahora.UTC().AddDate(0, 0, -7)).Scan(&n); err == nil && n == 0 {
				r.Avisos = append(r.Avisos, "Sin conversaciones en 7 días")
			}
		}
		out = append(out, r)
	}
	return out, nil
}

func (s *Store) cifrasPromo(ctx context.Context, promoID, embudoID int64) (CifrasPromo, error) {
	var c CifrasPromo
	if err := s.DB.QueryRowContext(ctx, `SELECT count(DISTINCT conversation_id) FROM uso_fuentes WHERE tenant_id=? AND fuente_id=?`,
		s.tid, promoID).Scan(&c.Conversaciones); err != nil {
		return c, err
	}
	ids, err := s.pedidosDePromo(ctx, promoID)
	if err != nil {
		return c, err
	}
	set := map[int64]bool{}
	for _, id := range ids {
		set[id] = true
	}
	if embudoID > 0 { // los que una persona metió o sacó a mano del embudo de la promo
		rows, err := s.DB.QueryContext(ctx, `SELECT order_id, etapa FROM pedido_embudo WHERE embudo_id=? AND tenant_id=?`, embudoID, s.tid)
		if err != nil {
			return c, err
		}
		for rows.Next() {
			var id int64
			var et string
			if err := rows.Scan(&id, &et); err == nil {
				set[id] = et != ""
			}
		}
		rows.Close()
	}
	for id, dentro := range set {
		if !dentro {
			continue
		}
		var estado string
		var total float64
		if err := s.DB.QueryRowContext(ctx, `SELECT status, total FROM orders WHERE id=? AND tenant_id=?`, id, s.tid).Scan(&estado, &total); err != nil {
			continue
		}
		c.Pedidos++
		if reservedStatuses[estado] {
			c.Confirmados++
			c.Monto += total
		}
		if estado == "preparando" || estado == "enviado" || estado == "entregado" {
			c.Pagados++
		}
	}
	return c, nil
}
