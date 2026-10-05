package store

import (
	"context"
	"database/sql"
	"errors"
	"time"
)

type Customer struct {
	ID        int64     `json:"id"`
	JID       string    `json:"jid"`
	Phone     string    `json:"phone"`
	Name      string    `json:"name"`
	CreatedAt time.Time `json:"created_at"`
}

type Conversation struct {
	ID            int64     `json:"id"`
	CustomerID    int64     `json:"customer_id"`
	State         string    `json:"state"`
	Context       string    `json:"-"`
	BotPaused     bool      `json:"bot_paused"`
	Unread        int       `json:"unread"`
	LastMessage   string    `json:"last_message"`
	LastMessageAt time.Time `json:"last_message_at"`
	Customer      *Customer `json:"customer,omitempty"`
}

type Message struct {
	ID             int64     `json:"id"`
	ConversationID int64     `json:"conversation_id"`
	WAID           string    `json:"wa_id"`
	Direction      string    `json:"direction"` // in | out
	Kind           string    `json:"kind"`      // text | image | location | other
	Body           string    `json:"body"`
	Media          string    `json:"media"`
	Author         string    `json:"author"` // cliente | bot | asesora
	Status         string    `json:"status"` // salientes: pending | sent | failed
	CreatedAt      time.Time `json:"created_at"`
}

// UpsertCustomer crea el cliente (y su conversación) si no existe y actualiza su nombre.
func (s *Store) UpsertCustomer(ctx context.Context, jid, phone, name string) (*Customer, *Conversation, error) {
	c := &Customer{}
	err := s.DB.QueryRowContext(ctx, `INSERT INTO customers(jid, phone, name, created_at) VALUES(?,?,?,?)
		ON CONFLICT(jid) DO UPDATE SET
			name = CASE WHEN excluded.name <> '' THEN excluded.name ELSE customers.name END,
			phone = CASE WHEN excluded.phone <> '' THEN excluded.phone ELSE customers.phone END
		RETURNING id, jid, phone, name, created_at`, jid, phone, name, now()).
		Scan(&c.ID, &c.JID, &c.Phone, &c.Name, &c.CreatedAt)
	if err != nil {
		return nil, nil, err
	}
	if _, err := s.DB.ExecContext(ctx, `INSERT INTO conversations(customer_id, last_message_at) VALUES(?,?)
		ON CONFLICT(customer_id) DO NOTHING`, c.ID, now()); err != nil {
		return nil, nil, err
	}
	conv, err := s.conversationWhere(ctx, `c.customer_id=?`, c.ID)
	return c, conv, err
}

const convSelect = `SELECT c.id, c.customer_id, c.state, c.context, c.bot_paused, c.unread, c.last_message, c.last_message_at,
	cu.id, cu.jid, cu.phone, cu.name, cu.created_at
	FROM conversations c JOIN customers cu ON cu.id=c.customer_id`

func scanConv(sc interface{ Scan(...any) error }) (*Conversation, error) {
	c := &Conversation{Customer: &Customer{}}
	var paused int
	err := sc.Scan(&c.ID, &c.CustomerID, &c.State, &c.Context, &paused, &c.Unread, &c.LastMessage, &c.LastMessageAt,
		&c.Customer.ID, &c.Customer.JID, &c.Customer.Phone, &c.Customer.Name, &c.Customer.CreatedAt)
	c.BotPaused = paused == 1
	return c, err
}

func (s *Store) conversationWhere(ctx context.Context, where string, args ...any) (*Conversation, error) {
	c, err := scanConv(s.DB.QueryRowContext(ctx, convSelect+` WHERE `+where, args...))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	return c, err
}

func (s *Store) GetConversation(ctx context.Context, id int64) (*Conversation, error) {
	return s.conversationWhere(ctx, `c.id=?`, id)
}

func (s *Store) ConversationByCustomer(ctx context.Context, customerID int64) (*Conversation, error) {
	return s.conversationWhere(ctx, `c.customer_id=?`, customerID)
}

func (s *Store) ListConversations(ctx context.Context, limit int) ([]*Conversation, error) {
	// Sin mensajes (p. ej. cliente creado por un pedido manual) no se listan en la bandeja.
	rows, err := s.DB.QueryContext(ctx, convSelect+` WHERE c.last_message <> '' ORDER BY c.last_message_at DESC LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*Conversation
	for rows.Next() {
		c, err := scanConv(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

func (s *Store) SetConversationState(ctx context.Context, id int64, state, contextJSON string) error {
	_, err := s.DB.ExecContext(ctx, `UPDATE conversations SET state=?, context=? WHERE id=?`, state, contextJSON, id)
	return err
}

func (s *Store) SetBotPaused(ctx context.Context, id int64, paused bool) error {
	if paused {
		_, err := s.DB.ExecContext(ctx, `UPDATE conversations SET bot_paused=1, paused_at=? WHERE id=?`, now(), id)
		return err
	}
	_, err := s.DB.ExecContext(ctx, `UPDATE conversations SET bot_paused=0, paused_at=NULL, state='', context='{}' WHERE id=?`, id)
	return err
}

// ResumeStalePaused reactiva el bot en conversaciones pausadas hace más de hours horas.
func (s *Store) ResumeStalePaused(ctx context.Context, hours int) (int64, error) {
	res, err := s.DB.ExecContext(ctx, `UPDATE conversations SET bot_paused=0, paused_at=NULL, state='', context='{}'
		WHERE bot_paused=1 AND paused_at IS NOT NULL AND paused_at < ?`, now().Add(-time.Duration(hours)*time.Hour))
	if err != nil {
		return 0, err
	}
	return res.RowsAffected()
}

func (s *Store) MarkRead(ctx context.Context, id int64) error {
	_, err := s.DB.ExecContext(ctx, `UPDATE conversations SET unread=0 WHERE id=?`, id)
	return err
}

// MessageExists evita procesar dos veces el mismo mensaje (reintentos del webhook).
func (s *Store) MessageExists(ctx context.Context, waID string) bool {
	if waID == "" {
		return false
	}
	var n int
	_ = s.DB.QueryRowContext(ctx, `SELECT count(*) FROM messages WHERE wa_id=?`, waID).Scan(&n)
	return n > 0
}

func (s *Store) AddMessage(ctx context.Context, m *Message) error {
	if m.CreatedAt.IsZero() {
		m.CreatedAt = now()
	}
	res, err := s.DB.ExecContext(ctx, `INSERT INTO messages(conversation_id, wa_id, direction, kind, body, media, author, status, created_at)
		VALUES(?,?,?,?,?,?,?,?,?)`, m.ConversationID, m.WAID, m.Direction, m.Kind, m.Body, m.Media, m.Author, m.Status, m.CreatedAt)
	if err != nil {
		return err
	}
	m.ID, _ = res.LastInsertId()
	preview := m.Body
	if preview == "" {
		preview = map[string]string{"image": "📷 Foto", "location": "📍 Ubicación"}[m.Kind]
	}
	if len([]rune(preview)) > 120 {
		preview = string([]rune(preview)[:120]) + "…"
	}
	unread := 0
	if m.Direction == "in" {
		unread = 1
		// La clienta volvió: se reinicia el contador de recordatorios de seguimiento.
		_, _ = s.DB.ExecContext(ctx, `DELETE FROM followups WHERE conversation_id=?`, m.ConversationID)
	}
	_, err = s.DB.ExecContext(ctx, `UPDATE conversations SET last_message=?, last_message_at=?, unread=unread+? WHERE id=?`,
		preview, m.CreatedAt, unread, m.ConversationID)
	return err
}

// SetMessageDelivery registra el resultado del envío por WhatsApp.
func (s *Store) SetMessageDelivery(ctx context.Context, id int64, waID, status string) error {
	_, err := s.DB.ExecContext(ctx, `UPDATE messages SET wa_id=?, status=? WHERE id=?`, waID, status, id)
	return err
}

func (s *Store) ListMessages(ctx context.Context, convID int64, limit int) ([]*Message, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT * FROM (
		SELECT id, conversation_id, wa_id, direction, kind, body, media, author, status, created_at
		FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?) ORDER BY id`, convID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*Message
	for rows.Next() {
		m := &Message{}
		if err := rows.Scan(&m.ID, &m.ConversationID, &m.WAID, &m.Direction, &m.Kind, &m.Body, &m.Media, &m.Author, &m.Status, &m.CreatedAt); err != nil {
			return nil, err
		}
		out = append(out, m)
	}
	return out, rows.Err()
}
