package store

import (
	"context"
	"time"
)

// Seguimiento (stopping agent): recordatorios a conversaciones que quedaron en silencio. La política
// (cuándo sí y cuándo callar) vive en la Capa de Juicio; aquí solo se guarda cuántos van y se listan
// las conversaciones candidatas.

// FollowupCount es cuántos recordatorios se han enviado a esa conversación sin que la clienta responda.
func (s *Store) FollowupCount(ctx context.Context, convID int64) int {
	var n int
	_ = s.DB.QueryRowContext(ctx, "SELECT `count` FROM followups WHERE conversation_id=? AND tenant_id=?", convID, s.tid).Scan(&n)
	return n
}

// MarkFollowUp anota un recordatorio más en la conversación.
func (s *Store) MarkFollowUp(ctx context.Context, convID int64) error {
	var one int
	if err := s.DB.QueryRowContext(ctx, `SELECT 1 FROM conversations WHERE id=? AND tenant_id=?`, convID, s.tid).Scan(&one); err != nil {
		return ErrNotFound
	}
	q := "INSERT INTO followups(conversation_id, tenant_id, `count`, last_at) VALUES(?, ?, 1, ?)\n" +
		"\t\tON CONFLICT DO UPDATE SET `count`=`count`+1, last_at=excluded.last_at"
	if s.mysql() {
		q = "INSERT INTO followups(conversation_id, tenant_id, `count`, last_at) VALUES(?, ?, 1, ?)\n" +
			"\t\tON DUPLICATE KEY UPDATE `count`=`count`+1, last_at=VALUES(last_at)"
	}
	_, err := s.DB.ExecContext(ctx, q, convID, s.tid, now())
	return err
}

// ConversationsToFollowUp devuelve las conversaciones atendidas por el bot (no pausadas) cuya última
// entrada de la clienta fue hace al menos `silence`, y que llevan menos de `maxCount` recordatorios.
func (s *Store) ConversationsToFollowUp(ctx context.Context, silence time.Duration, maxCount int) ([]*Conversation, error) {
	rows, err := s.DB.QueryContext(ctx, convSelect+`
		JOIN (SELECT conversation_id, MAX(created_at) AS last_in FROM messages WHERE tenant_id=? AND direction='in' GROUP BY conversation_id) mi
			ON mi.conversation_id = c.id
		LEFT JOIN followups f ON f.conversation_id = c.id AND f.tenant_id = c.tenant_id
		WHERE c.tenant_id=? AND c.bot_paused=0 AND mi.last_in < ? AND COALESCE(f.`+"`count`"+`, 0) < ?
		ORDER BY mi.last_in ASC LIMIT 50`, s.tid, s.tid, now().Add(-silence), maxCount)
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
