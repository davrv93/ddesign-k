package api

import "net/http"

// deleteConversation: DELETE /api/conversations/{id} (solo admin, ruta con soloAdmin). Borra la conversación y sus
// mensajes de la empresa; el cliente y sus pedidos quedan, con el registro en su línea de tiempo.
func (s *Server) deleteConversation(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	cust, err := s.st(r).DeleteConversation(r.Context(), id)
	if err != nil {
		errStore(w, err)
		return
	}
	s.pub(r, "conversations")
	s.pub(r, "clientas")
	writeJSON(w, 200, map[string]any{"ok": true, "customer_id": cust})
}
