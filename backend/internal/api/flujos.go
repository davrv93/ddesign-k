package api

// Editor visual de flujos (20-10-2026): simular un flujo sin enviarlo y probarlo de verdad en un número de WhatsApp.
// Contrato en docs/SEGUIMIENTO_CONTRATO.md §4b.

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/bot"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// pasoSimulado: lo que haría el flujo en cada nodo, en orden.
type pasoSimulado struct {
	Nodo     string `json:"nodo"`
	Tipo     string `json:"tipo"`
	EnMin    int    `json:"en_min"` // minutos desde que se armó el seguimiento
	Mensaje  string `json:"mensaje,omitempty"`
	Texto    string `json:"texto,omitempty"`
	MediaURL string `json:"media_url,omitempty"`
	Omitido  bool   `json:"omitido,omitempty"`
	Motivo   string `json:"motivo,omitempty"`
	Detalle  string `json:"detalle,omitempty"` // condición: qué salida tomó; tarea/etapa/ir a: qué haría
	Flujo    string `json:"flujo,omitempty"`   // nombre del flujo (cambia con «ir a otro flujo»)
}

// POST /api/flujos/simular {grafo, producto, cliente, responde, temperatura} → {pasos: [...], fin: "…"}.
// Recorre el flujo como lo haría el motor, sin enviar ni guardar nada: «responde» decide las condiciones «¿respondió?» y
// «temperatura» las de temperatura; categoría y contenido salen del modelo elegido.
func (s *Server) simularFlujo(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Grafo       *store.Grafo `json:"grafo"`
		Nombre      string       `json:"nombre"`
		Producto    string       `json:"producto"`
		Cliente     string       `json:"cliente"`
		Responde    bool         `json:"responde"`
		Temperatura string       `json:"temperatura"`
	}
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	if err := store.ValidarGrafo(in.Grafo); err != nil {
		errSeg(w, err)
		return
	}
	st := s.st(r)
	ctx := r.Context()
	conoc, _ := st.ConocimientoVigente(ctx, "", time.Now())
	media := s.mediaDeEmpresa(r)
	cliente := firstNonEmptyAPI(strings.TrimSpace(in.Cliente), "Ana")
	codigo := strings.ToUpper(strings.TrimSpace(in.Producto))
	nombre := firstNonEmptyAPI(strings.TrimSpace(in.Nombre), "Este flujo")

	g := in.Grafo
	out := []pasoSimulado{}
	min, saltos := 0, 0
	id := g.Inicio()
	fin := "llega al final"
	for i := 0; i < 120; i++ {
		n := g.Nodo(id)
		if n == nil {
			break
		}
		ps := pasoSimulado{Nodo: n.ID, Tipo: n.Tipo, EnMin: min, Flujo: nombre}
		next := store.SalidaSiguiente
		switch n.Tipo {
		case "inicio":
			id = g.Siguiente(n.ID, next)
			continue
		case "esperar":
			min += n.Minutos
			ps.EnMin, ps.Detalle = min, fmt.Sprintf("espera %d min", n.Minutos)
		case "mensaje":
			l := bot.ContenidoPaso(ctx, st, conoc, media, n.PasoDe(), codigo, cliente)
			ps.Mensaje, ps.Texto, ps.MediaURL, ps.Omitido, ps.Motivo = n.Mensaje, l.Texto, l.MediaURL, l.Omitido, l.Motivo
		case "condicion":
			si := false
			switch n.Condicion {
			case "respondio":
				si = in.Responde
			case "temperatura":
				si = strings.EqualFold(strings.TrimSpace(in.Temperatura), strings.TrimSpace(n.Valor))
			default:
				si = bot.EvaluarCondicion(ctx, st, nil, n, codigo)
			}
			if si {
				next, ps.Detalle = store.SalidaSi, "sí"
			} else {
				next, ps.Detalle = store.SalidaNo, "no"
			}
		case "tarea":
			ps.Detalle = "crea la tarea «" + n.Titulo + "»"
		case "etapa":
			ps.Detalle = "pasa a la etapa «" + store.EtapaLabel[n.Etapa] + "»"
		case "ir_a":
			dest, err := st.GetSecuencia(ctx, n.Destino)
			if err != nil || dest.Grafo == nil || saltos >= 3 {
				ps.Omitido, ps.Motivo = true, "el flujo destino no existe o no es un flujo visual"
				break
			}
			saltos++
			ps.Detalle = "pasa al flujo «" + dest.Nombre + "»"
			out = append(out, ps)
			g, nombre, id = dest.Grafo, dest.Nombre, dest.Grafo.Inicio()
			continue
		case "fin":
			out = append(out, ps)
			id = ""
		}
		if n.Tipo == "fin" {
			break
		}
		out = append(out, ps)
		if id = g.Siguiente(n.ID, next); id == "" {
			fin = "termina: la salida «" + next + "» no está conectada"
			break
		}
	}
	if id != "" && len(out) >= 120 {
		fin = "se cortó la simulación: el flujo da vueltas"
	}
	writeJSON(w, 200, map[string]any{"pasos": out, "fin": fin})
}

// límite de pruebas reales: 10 por empresa y hora (cada prueba manda WhatsApp de verdad).
var (
	pruebasMu sync.Mutex
	pruebas   = map[int64][]time.Time{}
)

func pruebaPermitida(tid int64) bool {
	pruebasMu.Lock()
	defer pruebasMu.Unlock()
	hace := time.Now().Add(-time.Hour)
	var vigentes []time.Time
	for _, t := range pruebas[tid] {
		if t.After(hace) {
			vigentes = append(vigentes, t)
		}
	}
	if len(vigentes) >= 10 {
		pruebas[tid] = vigentes
		return false
	}
	pruebas[tid] = append(vigentes, time.Now())
	return true
}

// POST /api/secuencias/{id}/probar {telefono, producto, escala_min} (admin) → {conversation_id, telefono, escala_min}.
// Corre el flujo GUARDADO en la conversación de ese número, ya, con cada espera de `escala_min` minutos (1 por defecto;
// 0 = las esperas reales), sin horario, tope ni ventana de 24 h, aunque la secuencia esté apagada. Manda WhatsApp de
// verdad: el panel lo pide con confirmación y solo para el número que escribe la persona.
func (s *Server) probarFlujo(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	var in struct {
		Telefono  string `json:"telefono"`
		Producto  string `json:"producto"`
		EscalaMin *int   `json:"escala_min"`
	}
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	tel := strings.Map(func(c rune) rune {
		if c >= '0' && c <= '9' {
			return c
		}
		return -1
	}, in.Telefono)
	if len(tel) == 9 && strings.HasPrefix(tel, "9") { // celular de Perú sin código de país
		tel = "51" + tel
	}
	if len(tel) < 10 || len(tel) > 15 {
		writeErr(w, 400, "teléfono inválido: escribe el número con código de país (51 987 654 321)")
		return
	}
	escala := 1
	if in.EscalaMin != nil {
		escala = *in.EscalaMin
	}
	if escala < 0 || escala > 60 {
		writeErr(w, 400, "las esperas de la prueba van de 0 (reales) a 60 minutos")
		return
	}
	if !s.waEnabled(r) && !(s.cfg.WACloudListo() && s.isBotTenant(r)) {
		writeErr(w, 409, "esta empresa no tiene WhatsApp conectado: no se puede enviar la prueba")
		return
	}
	st := s.st(r)
	ctx := r.Context()
	q, err := st.GetSecuencia(ctx, id)
	if err != nil {
		errSeg(w, err)
		return
	}
	if q.Grafo == nil {
		writeErr(w, 400, "guarda la secuencia como flujo (editor visual) antes de probarla")
		return
	}
	codigo := strings.ToUpper(strings.TrimSpace(in.Producto))
	if codigo != "" {
		if _, err := st.GetProductByCode(ctx, codigo); err != nil {
			writeErr(w, 400, "no existe el modelo "+codigo)
			return
		}
	}
	if !pruebaPermitida(s.tenantID(r)) {
		writeErr(w, 429, "ya hiciste 10 pruebas en la última hora; espera un poco")
		return
	}
	_, conv, err := st.UpsertCustomer(ctx, tel+"@s.whatsapp.net", tel, "")
	if err != nil {
		errStore(w, err)
		return
	}
	if err := st.ArmarPrueba(ctx, conv.ID, q, codigo, escala); err != nil {
		errSeg(w, err)
		return
	}
	go s.tickAhora() // la primera parte sale ya, sin esperar al minuto del trabajador
	s.hub.PublishTo(s.tenantID(r), "seguimiento")
	writeJSON(w, 200, map[string]any{"conversation_id": conv.ID, "telefono": tel, "escala_min": escala})
}

// tickAhora corre el trabajador de seguimiento una vez (lo usa «Probar»). Sin bot (pruebas), no hace nada.
func (s *Server) tickAhora() {
	if s.bot == nil {
		return
	}
	defer func() { _ = recover() }()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	s.bot.TickSeguimiento(ctx, time.Now())
}

// mediaDeEmpresa: la base de las URLs de /media de la empresa de la petición (igual que la vista previa).
func (s *Server) mediaDeEmpresa(r *http.Request) string {
	media := s.cfg.MediaBaseURL
	if s.cfg.MultiTenant {
		if slug := s.slugDe(r); slug != "" {
			media = strings.TrimRight(s.cfg.PublicURL, "/")
			if !strings.HasSuffix(media, "/"+slug) {
				media += "/" + slug
			}
		}
	}
	return media
}
