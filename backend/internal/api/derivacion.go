package api

import "net/http"

// GET /api/derivadas: conversaciones en las que la clienta pidió hablar con una asesora y el bot sigue en pausa, la más
// antigua primero. Es la cola de atención de la tienda: la ve todo el equipo.
func (s *Server) listDerivadas(w http.ResponseWriter, r *http.Request) {
	out, err := s.st(r).ListDerivadas(r.Context())
	if err != nil {
		writeErr(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, out)
}
