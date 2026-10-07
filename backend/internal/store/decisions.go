package store

import (
	"context"
	"time"
)

// Decision es una fila de la memoria de criterio: qué se hizo en un turno, por qué y con qué resultado.
// La decisión puede ser de la Capa de Juicio (autor «bot») o de una persona (autor «asesora»).
type Decision struct {
	ID             int64     `json:"id"`
	ConversationID int64     `json:"conversation_id"`
	Etapa          string    `json:"etapa"`
	Intent         string    `json:"intent"`
	Caso           string    `json:"caso"`
	Decision       string    `json:"decision"`
	Razon          string    `json:"razon"`
	Resultado      string    `json:"resultado"`
	Autor          string    `json:"autor"`
	CreatedAt      time.Time `json:"created_at"`
}

// SaveDecision registra una decisión. No falla la conversación si no se puede guardar (es un registro).
func (s *Store) SaveDecision(ctx context.Context, d *Decision) error {
	var one int
	if err := s.DB.QueryRowContext(ctx, `SELECT 1 FROM conversations WHERE id=? AND tenant_id=?`, d.ConversationID, s.tid).Scan(&one); err != nil {
		return ErrNotFound
	}
	_, err := s.DB.ExecContext(ctx, `INSERT INTO decisiones(tenant_id, conversation_id, etapa, intent, caso, decision, razon, resultado, autor, created_at)
		VALUES(?,?,?,?,?,?,?,?,?,?)`, s.tid, d.ConversationID, d.Etapa, d.Intent, d.Caso, d.Decision, d.Razon, d.Resultado, d.Autor, now())
	return err
}

// DecisionsFor devuelve las decisiones más recientes de la misma intención: son los «casos parecidos»
// (memoria de criterio) con los que el bot y el panel pueden comparar la decisión de hoy.
func (s *Store) DecisionsFor(ctx context.Context, intent string, limit int) ([]*Decision, error) {
	if limit <= 0 {
		limit = 5
	}
	rows, err := s.DB.QueryContext(ctx, `SELECT id, conversation_id, etapa, intent, caso, decision, razon, resultado, autor, created_at
		FROM decisiones WHERE tenant_id=? AND intent=? ORDER BY id DESC LIMIT ?`, s.tid, intent, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*Decision
	for rows.Next() {
		d := &Decision{}
		if err := rows.Scan(&d.ID, &d.ConversationID, &d.Etapa, &d.Intent, &d.Caso, &d.Decision, &d.Razon, &d.Resultado, &d.Autor, &d.CreatedAt); err != nil {
			return nil, err
		}
		out = append(out, d)
	}
	return out, rows.Err()
}

// IntervencionHumana marca que una asesora respondió en esa conversación (criterio humano a imitar).
func (s *Store) IntervencionHumana(ctx context.Context, conversationID int64, etapa, razon string) error {
	return s.SaveDecision(ctx, &Decision{ConversationID: conversationID, Etapa: etapa, Decision: "humano_responde", Razon: razon, Autor: "asesora"})
}

// ParDPO es un ejemplo para alinear al bot con el criterio humano: el turno de la clienta, lo que el bot
// había propuesto y lo que escribió la persona.
type ParDPO struct {
	Contexto        string `json:"contexto"`
	RespuestaBot    string `json:"respuesta_bot"`
	RespuestaHumana string `json:"respuesta_humana"`
}

// SaveParDPO guarda un par de alineación si hay algo que aprender (el bot había propuesto algo).
func (s *Store) SaveParDPO(ctx context.Context, conversationID int64, p *ParDPO) error {
	if p == nil || p.RespuestaBot == "" || p.RespuestaHumana == "" {
		return nil
	}
	var one int
	if err := s.DB.QueryRowContext(ctx, `SELECT 1 FROM conversations WHERE id=? AND tenant_id=?`, conversationID, s.tid).Scan(&one); err != nil {
		return ErrNotFound
	}
	_, err := s.DB.ExecContext(ctx, `INSERT INTO pares_dpo(tenant_id, conversation_id, contexto, respuesta_bot, respuesta_humana, created_at)
		VALUES(?,?,?,?,?,?)`, s.tid, conversationID, p.Contexto, p.RespuestaBot, p.RespuestaHumana, now())
	return err
}
