package store

import (
	"context"
	"database/sql"
	"fmt"
	"strings"
	"time"
)

// Derivación a asesora: la clienta pidió hablar con una persona (o hubo un reclamo) y el bot se pausó. Para que la tienda
// lo vea en su CRM y no solo en el log (14-10-2026): queda en la línea de tiempo de la clienta, abre una tarea «Atender por
// WhatsApp» para su asesora (o sin asignar) y la conversación entra en la cola de /api/derivadas hasta que alguien le
// responde o reactiva el bot.

// TituloDerivacion es el título de la tarea que abre cada derivación; sirve para no abrir dos por la misma clienta.
const TituloDerivacion = "Atender por WhatsApp: pidió hablar con una asesora"

// Derivar anota la derivación en la actividad de la clienta y abre la tarea si no hay una pendiente igual. Devuelve el
// id de la tarea (0 si ya existía una pendiente).
func (s *Store) Derivar(ctx context.Context, conv *Conversation, motivo string) (int64, error) {
	if conv == nil || conv.CustomerID == 0 {
		return 0, fmt.Errorf("derivar: conversación sin clienta")
	}
	texto := "🙋‍♀️ Pidió hablar con una asesora por WhatsApp; el bot se pausó en ese chat"
	if m := strings.TrimSpace(motivo); m != "" {
		texto += " (" + m + ")"
	}
	if err := s.registrar(ConAutor(ctx, "bot"), s.DB, conv.CustomerID, "derivacion", texto, conv.ID); err != nil {
		return 0, err
	}
	var pendiente int64
	err := s.DB.QueryRowContext(ctx, `SELECT id FROM tareas WHERE tenant_id=? AND customer_id=? AND hecha=0 AND titulo=? LIMIT 1`,
		s.tid, conv.CustomerID, TituloDerivacion).Scan(&pendiente)
	if err == nil {
		return 0, nil
	}
	if err != sql.ErrNoRows {
		return 0, err
	}
	var asesora sql.NullInt64
	_ = s.DB.QueryRowContext(ctx, `SELECT asesora_id FROM customers WHERE id=? AND tenant_id=?`, conv.CustomerID, s.tid).Scan(&asesora)
	vence := now()
	t := &Tarea{CustomerID: conv.CustomerID, Titulo: TituloDerivacion, Vence: &vence, ResponsableID: asesora.Int64, CreadaPor: "bot"}
	if err := s.AddTarea(ConAutor(ctx, "bot"), t); err != nil {
		return 0, err
	}
	return t.ID, nil
}

// Derivada: una conversación que espera a una asesora.
type Derivada struct {
	ConversationID int64     `json:"conversation_id"`
	CustomerID     int64     `json:"customer_id"`
	Nombre         string    `json:"nombre"`
	Phone          string    `json:"phone"`
	AsesoraID      int64     `json:"asesora_id"`
	Asesora        string    `json:"asesora"`
	Desde          time.Time `json:"desde"` // cuándo se pausó el bot
	UltimoMensaje  string    `json:"ultimo_mensaje"`
}

// ListDerivadas: conversaciones con el bot pausado por «asesora» (estado del bot), la más antigua primero. No mira
// deleted_at (papelera, en curso en otra rama): una conversación borrada deja de estar pausada al restaurarse o se filtra allí.
func (s *Store) ListDerivadas(ctx context.Context) ([]Derivada, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT c.id, cu.id, cu.name, cu.phone, coalesce(cu.asesora_id, 0), coalesce(u.name, ''),
		c.paused_at, c.last_message_at, c.last_message
		FROM conversations c JOIN customers cu ON cu.id=c.customer_id AND cu.tenant_id=c.tenant_id
		LEFT JOIN users u ON u.id=cu.asesora_id AND u.tenant_id=cu.tenant_id
		WHERE c.tenant_id=? AND c.bot_paused=1 AND c.state='asesora'
		ORDER BY coalesce(c.paused_at, c.last_message_at), c.id`, s.tid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Derivada{}
	for rows.Next() {
		var d Derivada
		var pausado sql.NullTime
		if err := rows.Scan(&d.ConversationID, &d.CustomerID, &d.Nombre, &d.Phone, &d.AsesoraID, &d.Asesora, &pausado, &d.Desde, &d.UltimoMensaje); err != nil {
			return nil, err
		}
		if pausado.Valid { // sqlite devuelve coalesce() como texto: se elige aquí
			d.Desde = pausado.Time
		}
		out = append(out, d)
	}
	return out, rows.Err()
}
