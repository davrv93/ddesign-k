package api

// Clienta caliente (15-10-2026): alertas que el bot crea al detectar una clienta caliente (bot/alertas.go) y la ficha
// «Lo que sabemos» de cada conversación. Contrato con el panel en docs/FUENTES_CONTRATO.md §6.

import (
	"net/http"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/bot"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

func init() {
	settingDefaults[store.SettingAlertaWhatsApp] = ""
	settingDefaults[store.SettingAlertaPanel] = "true"
}

func (s *Server) rutasAlertas(p, a func(pattern string, h http.HandlerFunc)) {
	p("GET /api/alertas", s.listAlertas)
	p("POST /api/alertas/{id}/atendida", s.atenderAlerta)
	p("GET /api/conversations/{id}/ficha", s.fichaConversacion)
}

// listAlertas: ?estado=pendiente (por defecto) o todas.
func (s *Server) listAlertas(w http.ResponseWriter, r *http.Request) {
	as, err := s.st(r).ListarAlertas(r.Context(), r.URL.Query().Get("estado") != "todas")
	if err != nil {
		errStore(w, err)
		return
	}
	writeJSON(w, 200, as)
}

func (s *Server) atenderAlerta(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	if err := s.st(r).AtenderAlerta(r.Context(), id); err != nil {
		errStore(w, err)
		return
	}
	s.pub(r, "alertas")
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func (s *Server) fichaConversacion(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	st := s.st(r)
	conv, err := st.GetConversation(r.Context(), id)
	if err != nil {
		errStore(w, err)
		return
	}
	nombre := func(c string) string {
		if p, err := st.GetProductByCode(r.Context(), c); err == nil {
			return p.Name
		}
		return ""
	}
	writeJSON(w, 200, bot.LeerFicha(conv.Context, time.Now(), nombre))
}
