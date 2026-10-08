package store

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
)

// DeleteConversation borra una conversación de la empresa con sus mensajes y lo que cuelga de ella (recordatorios,
// decisiones, pares DPO y vínculos con Kommo). La ficha de la persona y sus pedidos se conservan, y su línea de
// tiempo registra «Conversación eliminada por <autor>» (store.ConAutor). Devuelve el id del cliente.
func (s *Store) DeleteConversation(ctx context.Context, id int64) (int64, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	var custID int64
	var mensajes int
	err = tx.QueryRowContext(ctx, `SELECT customer_id FROM conversations WHERE id=? AND tenant_id=?`, id, s.tid).Scan(&custID)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, ErrNotFound
	} else if err != nil {
		return 0, err
	}
	_ = tx.QueryRowContext(ctx, `SELECT count(*) FROM messages WHERE conversation_id=? AND tenant_id=?`, id, s.tid).Scan(&mensajes)
	for _, q := range []string{
		`DELETE FROM messages WHERE conversation_id=? AND tenant_id=?`,
		`DELETE FROM followups WHERE conversation_id=? AND tenant_id=?`,
		`DELETE FROM decisiones WHERE conversation_id=? AND tenant_id=?`,
		`DELETE FROM pares_dpo WHERE conversation_id=? AND tenant_id=?`,
		`DELETE FROM kommo_vinculos WHERE conversation_id=? AND tenant_id=?`,
		`DELETE FROM conversations WHERE id=? AND tenant_id=?`,
	} {
		if _, err := tx.ExecContext(ctx, q, id, s.tid); err != nil {
			return 0, err
		}
	}
	texto := "Conversación eliminada por " + autorDe(ctx)
	if mensajes > 0 {
		texto += " (" + plural(mensajes, "mensaje", "mensajes") + ")"
	}
	if err := s.registrar(ctx, tx, custID, "conversacion", texto, id); err != nil {
		return 0, err
	}
	return custID, tx.Commit()
}

func plural(n int, uno, varios string) string {
	if n == 1 {
		return "1 " + uno
	}
	return strconv.Itoa(n) + " " + varios
}
