package api

import "net/http"

// rutasExtra: rutas añadidas el 14-10-2026 (colores del guion de noche y derivaciones a asesora). Van aparte para que
// server.go, que otra sesión edita en paralelo, cambie en una sola línea.
func (s *Server) rutasExtra(p, a func(pattern string, h http.HandlerFunc)) {
	p("GET /api/colores", s.listColores)
	a("PUT /api/colores", s.putColores)
	p("GET /api/derivadas", s.listDerivadas)
	s.rutasFuentes(p, a) // fuentes, promos y embudos (fuentes.go, 15-10-2026)
}
