package store

import (
	"context"
	"database/sql"
	"errors"
)

// VinculoKommo une una conversación del bot con su lead y su contacto en Kommo CRM.
type VinculoKommo struct {
	Clave          string // «wa:<conversation_id>» o «web:<sesión>»
	Canal          string // whatsapp | web
	ConversationID int64  // 0 en el chat web (no vive en la SQLite)
	LeadID         int64
	ContactID      int64
	Estado         string // JSON con lo último que se mandó a Kommo (ver kommo.estadoLead)
}

// VinculoKommo devuelve el vínculo de esa clave, o ErrNotFound.
func (s *Store) VinculoKommo(ctx context.Context, clave string) (*VinculoKommo, error) {
	v := &VinculoKommo{}
	err := s.DB.QueryRowContext(ctx, `SELECT clave, canal, conversation_id, lead_id, contact_id, estado FROM kommo_vinculos WHERE tenant_id=? AND clave=?`, s.tid, clave).
		Scan(&v.Clave, &v.Canal, &v.ConversationID, &v.LeadID, &v.ContactID, &v.Estado)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	return v, err
}

// GuardarVinculoKommo crea o reemplaza el vínculo.
func (s *Store) GuardarVinculoKommo(ctx context.Context, v *VinculoKommo) error {
	if v.Estado == "" {
		v.Estado = "{}"
	}
	q := `INSERT INTO kommo_vinculos(tenant_id, clave, canal, conversation_id, lead_id, contact_id, estado, updated_at)
		VALUES(?,?,?,?,?,?,?,?)
		ON CONFLICT DO UPDATE SET canal=excluded.canal, conversation_id=excluded.conversation_id, lead_id=excluded.lead_id,
			contact_id=excluded.contact_id, estado=excluded.estado, updated_at=excluded.updated_at`
	if s.mysql() {
		q = `INSERT INTO kommo_vinculos(tenant_id, clave, canal, conversation_id, lead_id, contact_id, estado, updated_at)
		VALUES(?,?,?,?,?,?,?,?)
		ON DUPLICATE KEY UPDATE canal=VALUES(canal), conversation_id=VALUES(conversation_id), lead_id=VALUES(lead_id),
			contact_id=VALUES(contact_id), estado=VALUES(estado), updated_at=VALUES(updated_at)`
	}
	_, err := s.DB.ExecContext(ctx, q, s.tid,
		v.Clave, v.Canal, v.ConversationID, v.LeadID, v.ContactID, v.Estado, now())
	return err
}

// BorrarVinculosKommo borra los vínculos cuya clave empieza por prefijo (p. ej. «demo:» tras limpiar el seed).
func (s *Store) BorrarVinculosKommo(ctx context.Context, prefijo string) (int64, error) {
	res, err := s.DB.ExecContext(ctx, `DELETE FROM kommo_vinculos WHERE tenant_id=? AND substr(clave, 1, ?) = ?`, s.tid, len(prefijo), prefijo)
	if err != nil {
		return 0, err
	}
	return res.RowsAffected()
}

// LeadKommoDeConversacion: el lead de Kommo de una conversación de WhatsApp (0 si no tiene). Lo usa el panel para
// el enlace «Ver en Kommo».
func (s *Store) LeadKommoDeConversacion(ctx context.Context, conversationID int64) int64 {
	var id int64
	_ = s.DB.QueryRowContext(ctx, `SELECT lead_id FROM kommo_vinculos WHERE tenant_id=? AND conversation_id=? AND lead_id>0
		ORDER BY updated_at DESC LIMIT 1`, s.tid, conversationID).Scan(&id)
	return id
}
