package api

// Prospectos, bienvenida, seguimiento por modelo, números de WhatsApp, API oficial y campañas (octubre de 2026). Contrato:
// docs/SEGUIMIENTO_CONTRATO.md.

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/bot"
	"github.com/davrv93/ddesign-k/backend/internal/evolution"
	"github.com/davrv93/ddesign-k/backend/internal/store"
	"github.com/davrv93/ddesign-k/backend/internal/whatsapp/cloud"
)

func init() {
	for k, v := range map[string]string{
		store.SettingBienvenidaAnuncio:   "false",
		store.SettingPrecioAlLlegar:      "false",
		store.SettingProspectos:          "false",
		store.SettingProspectoDesde:      "45",
		store.SettingProspectoAviso:      "30",
		store.SettingPreguntarPresupuest: "false",
		store.SettingSeguimientoHorario:  "08:00-21:00",
		store.SettingTopePrincipal:       "25",
		"plantilla_estado_pedido":        "",
	} {
		settingDefaults[k] = v
	}
}

func (s *Server) rutasSeguimiento(p, a func(pattern string, h http.HandlerFunc)) {
	p("GET /api/productos/{id}/contenido", s.getContenido)
	a("PUT /api/productos/{id}/contenido/{clave}", s.putContenido)
	a("POST /api/contenido/importar", s.importarContenido)
	p("GET /api/contenido/resumen", s.resumenContenido)

	p("GET /api/secuencias", s.listSecuencias)
	a("POST /api/secuencias", s.crearSecuencia)
	a("PUT /api/secuencias/{id}", s.putSecuencia)
	a("DELETE /api/secuencias/{id}", s.borrarSecuencia)
	a("POST /api/secuencias/{id}/activa", s.activarSecuencia)
	p("GET /api/secuencias/{id}/vista-previa", s.vistaPreviaSecuencia)
	p("GET /api/conversations/{id}/seguimiento", s.seguimientoConv)
	p("POST /api/conversations/{id}/seguimiento/detener", s.detenerSeguimiento)
	p("GET /api/conversations/{id}/origen", s.origenConv)

	p("GET /api/numeros", s.listNumeros)
	a("POST /api/numeros", s.crearNumero)
	a("PUT /api/numeros/{id}", s.putNumero)
	a("DELETE /api/numeros/{id}", s.borrarNumero)
	a("GET /api/numeros/{id}/qr", s.qrNumero)
	a("POST /api/numeros/{id}/vincular", s.vincularNumero)
	p("GET /api/whatsapp-cloud/plantillas", s.plantillasCloud)

	p("GET /api/campanas", s.campanas)
}

// rutasPublicasSeguimiento: webhooks (sin sesión; cada uno se autentica a su manera).
func (s *Server) rutasPublicasSeguimiento(mux *http.ServeMux) {
	mux.HandleFunc("GET /webhook/whatsapp-cloud", s.cloudVerificar)
	mux.HandleFunc("POST /webhook/whatsapp-cloud", s.cloudWebhook)
	mux.HandleFunc("POST /webhook/evolution/{secret}/n/{id}", s.webhookSecundario)
}

func errSeg(w http.ResponseWriter, err error) {
	if errors.Is(err, store.ErrSeguimientoInvalido) {
		writeErr(w, 400, err.Error())
		return
	}
	errStore(w, err)
}

// ---------------------------------------------------------------------------
// Contenido por modelo

func (s *Server) getContenido(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	cs, err := s.st(r).ContenidoProducto(r.Context(), id)
	if err != nil {
		errSeg(w, err)
		return
	}
	writeJSON(w, 200, cs)
}

func (s *Server) putContenido(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	var in struct {
		Texto    string `json:"texto"`
		MediaURL string `json:"media_url"`
	}
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "json inválido")
		return
	}
	if _, err := s.st(r).GuardarContenido(r.Context(), id, r.PathValue("clave"), in.Texto, in.MediaURL, "manual"); err != nil {
		errSeg(w, err)
		return
	}
	cs, _ := s.st(r).ContenidoProducto(r.Context(), id)
	writeJSON(w, 200, cs)
}

func (s *Server) importarContenido(w http.ResponseWriter, r *http.Request) {
	var in []struct {
		Codigo   string `json:"codigo"`
		Clave    string `json:"clave"`
		Texto    string `json:"texto"`
		MediaURL string `json:"media_url"`
	}
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "json inválido: se espera una lista")
		return
	}
	st, imp, omit := s.st(r), 0, 0
	for _, x := range in {
		p, err := st.GetProductByCode(r.Context(), x.Codigo)
		if err != nil {
			omit++
			continue
		}
		ok, err := st.GuardarContenido(r.Context(), p.ID, x.Clave, x.Texto, x.MediaURL, "web")
		if err != nil || !ok {
			omit++
			continue
		}
		imp++
	}
	writeJSON(w, 200, map[string]int{"importados": imp, "omitidos": omit})
}

func (s *Server) resumenContenido(w http.ResponseWriter, r *http.Request) {
	rs, err := s.st(r).ResumenContenidos(r.Context())
	if err != nil {
		errSeg(w, err)
		return
	}
	writeJSON(w, 200, rs)
}

// ---------------------------------------------------------------------------
// Secuencias

func (s *Server) listSecuencias(w http.ResponseWriter, r *http.Request) {
	qs, err := s.st(r).ListarSecuencias(r.Context())
	if err != nil {
		errSeg(w, err)
		return
	}
	writeJSON(w, 200, qs)
}

func (s *Server) crearSecuencia(w http.ResponseWriter, r *http.Request) {
	var q store.Secuencia
	if err := readJSON(r, &q); err != nil {
		writeErr(w, 400, "json inválido")
		return
	}
	if err := s.st(r).CrearSecuencia(r.Context(), &q); err != nil {
		errSeg(w, err)
		return
	}
	s.pub(r, "seguimiento")
	writeJSON(w, 201, q)
}

func (s *Server) putSecuencia(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	var q store.Secuencia
	if err := readJSON(r, &q); err != nil {
		writeErr(w, 400, "json inválido")
		return
	}
	q.ID = id
	if err := s.st(r).GuardarSecuencia(r.Context(), &q); err != nil {
		errSeg(w, err)
		return
	}
	s.pub(r, "seguimiento")
	out, _ := s.st(r).GetSecuencia(r.Context(), id)
	writeJSON(w, 200, out)
}

func (s *Server) borrarSecuencia(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	if err := s.st(r).BorrarSecuencia(r.Context(), id); err != nil {
		errSeg(w, err)
		return
	}
	s.pub(r, "seguimiento")
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func (s *Server) activarSecuencia(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	var in struct {
		Activa bool `json:"activa"`
	}
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "json inválido")
		return
	}
	if err := s.st(r).ActivarSecuencia(r.Context(), id, in.Activa); err != nil {
		errSeg(w, err)
		return
	}
	s.pub(r, "seguimiento")
	out, _ := s.st(r).GetSecuencia(r.Context(), id)
	writeJSON(w, 200, out)
}

type pasoPrevia struct {
	Paso      int    `json:"paso"`
	EsperaMin int    `json:"espera_min"`
	Tipo      string `json:"tipo"`
	Texto     string `json:"texto"`
	MediaURL  string `json:"media_url"`
	Omitido   bool   `json:"omitido"`
	Motivo    string `json:"motivo"`
}

func (s *Server) vistaPreviaSecuencia(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	st := s.st(r)
	q, err := st.GetSecuencia(r.Context(), id)
	if err != nil {
		errSeg(w, err)
		return
	}
	conoc, _ := st.ConocimientoVigente(r.Context(), "", time.Now())
	media := s.cfg.MediaBaseURL
	if s.cfg.MultiTenant {
		if slug := s.slugDe(r); slug != "" {
			media = strings.TrimRight(s.cfg.PublicURL, "/")
			if !strings.HasSuffix(media, "/"+slug) {
				media += "/" + slug
			}
		}
	}
	out := []pasoPrevia{}
	for i, p := range q.Pasos {
		l := bot.ContenidoPaso(r.Context(), st, conoc, media, p, r.URL.Query().Get("producto"), "Ana")
		out = append(out, pasoPrevia{Paso: i + 1, EsperaMin: p.EsperaMin, Tipo: p.Tipo, Texto: l.Texto, MediaURL: l.MediaURL,
			Omitido: l.Omitido, Motivo: l.Motivo})
	}
	writeJSON(w, 200, out)
}

func (s *Server) seguimientoConv(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	e, err := s.st(r).SeguimientoDe(r.Context(), id)
	if err != nil {
		errSeg(w, err)
		return
	}
	if e == nil {
		writeJSON(w, 200, nil)
		return
	}
	writeJSON(w, 200, e)
}

func (s *Server) detenerSeguimiento(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	if err := s.st(r).DetenerSeguimiento(r.Context(), id); err != nil {
		errSeg(w, err)
		return
	}
	s.pub(r, "seguimiento")
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func (s *Server) origenConv(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	o, err := s.st(r).OrigenDe(r.Context(), id)
	if err != nil {
		errSeg(w, err)
		return
	}
	if o == nil {
		writeJSON(w, 200, nil)
		return
	}
	writeJSON(w, 200, o)
}

// ---------------------------------------------------------------------------
// Números de WhatsApp

// principal: el número del .env (evolution o la API oficial) como fila de la lista; no se borra.
func (s *Server) principal(r *http.Request) *store.NumeroWhatsApp {
	if !s.waEnabled(r) && !(s.cfg.WACloudListo() && s.isBotTenant(r)) {
		return nil
	}
	st := s.st(r)
	n := &store.NumeroWhatsApp{ID: 0, Nombre: "Principal", Canal: "evolution", Uso: "ambos", Principal: true,
		EnviadosHoy: st.EnviadosHoy(r.Context(), 0)}
	fmt.Sscan(st.Setting(r.Context(), store.SettingTopePrincipal, "25"), &n.TopeDiario)
	if s.cfg.EvolutionURL != "" && s.evo != nil {
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		if stt, err := s.evo.Status(ctx); err == nil {
			n.Conectado = stt.LoggedIn
		}
	} else if s.cfg.WACloudListo() {
		n.Nombre, n.Canal, n.Conectado, n.PhoneNumberID = "Principal (API oficial)", "cloud", true, s.cfg.WACloudPhoneNumberID
	}
	return n
}

func (s *Server) listNumeros(w http.ResponseWriter, r *http.Request) {
	ns, err := s.st(r).ListarNumeros(r.Context())
	if err != nil {
		errSeg(w, err)
		return
	}
	out := []*store.NumeroWhatsApp{}
	if p := s.principal(r); p != nil {
		out = append(out, p)
	}
	if s.cfg.WACloudListo() && s.isBotTenant(r) && s.cfg.EvolutionURL != "" {
		out = append(out, &store.NumeroWhatsApp{ID: -1, Nombre: "API oficial de WhatsApp", Canal: "cloud", Uso: "ambos",
			Conectado: true, PhoneNumberID: s.cfg.WACloudPhoneNumberID, TopeDiario: 0})
	}
	for _, n := range ns {
		if n.Canal == "evolution" && n.Instancia != "" && s.cfg.EvolutionURL != "" {
			ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
			if stt, err := s.cliNumero(n).Status(ctx); err == nil {
				n.Conectado = stt.LoggedIn
			}
			cancel()
		}
		out = append(out, n)
	}
	writeJSON(w, 200, out)
}

func (s *Server) cliNumero(n *store.NumeroWhatsApp) *evolution.Client {
	return evolution.New(s.cfg.EvolutionURL, s.cfg.EvolutionGlobalKey, n.Instancia, n.Token)
}

func tokenAleatorio() string {
	b := make([]byte, 24)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func (s *Server) crearNumero(w http.ResponseWriter, r *http.Request) {
	var n store.NumeroWhatsApp
	if err := readJSON(r, &n); err != nil {
		writeErr(w, 400, "json inválido")
		return
	}
	if n.Canal == "" {
		n.Canal = "evolution"
	}
	if n.Canal == "evolution" {
		if !s.waEnabled(r) {
			writeErr(w, http.StatusConflict, "WhatsApp no está habilitado para esta empresa")
			return
		}
		n.Instancia = fmt.Sprintf("%s-n%d", firstNonEmptyAPI(s.slugDe(r), "tienda"), time.Now().UnixNano()%1_000_000)
		n.Token = tokenAleatorio()
	}
	if n.Canal == "cloud" {
		writeErr(w, 400, "el número de la API oficial se configura en el servidor (WA_CLOUD_*), no aquí")
		return
	}
	if err := s.st(r).CrearNumero(r.Context(), &n); err != nil {
		errSeg(w, err)
		return
	}
	writeJSON(w, 201, n)
}

func (s *Server) putNumero(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	var n store.NumeroWhatsApp
	if err := readJSON(r, &n); err != nil {
		writeErr(w, 400, "json inválido")
		return
	}
	if id == 0 { // el principal: solo su tope
		if n.TopeDiario < 0 || n.TopeDiario > 1000 {
			writeErr(w, 400, "el tope diario va de 0 a 1000")
			return
		}
		if err := s.st(r).SetSetting(r.Context(), store.SettingTopePrincipal, strconv.Itoa(n.TopeDiario)); err != nil {
			errSeg(w, err)
			return
		}
		writeJSON(w, 200, s.principal(r))
		return
	}
	n.ID = id
	if err := s.st(r).GuardarNumero(r.Context(), &n); err != nil {
		errSeg(w, err)
		return
	}
	s.bot.OlvidarSecundario(id)
	out, _ := s.st(r).GetNumero(r.Context(), id)
	writeJSON(w, 200, out)
}

func (s *Server) borrarNumero(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil || id <= 0 {
		writeErr(w, 400, "el número principal no se borra")
		return
	}
	n, err := s.st(r).GetNumero(r.Context(), id)
	if err != nil {
		errSeg(w, err)
		return
	}
	if n.Canal == "evolution" && n.Instancia != "" && s.cfg.EvolutionURL != "" {
		ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
		_ = s.cliNumero(n).Logout(ctx) // que el teléfono deje de estar vinculado
		cancel()
	}
	if err := s.st(r).BorrarNumero(r.Context(), id); err != nil {
		errSeg(w, err)
		return
	}
	s.bot.OlvidarSecundario(id)
	writeJSON(w, 200, map[string]bool{"ok": true})
}

// numeroEvolution: el número secundario listo para conectarse (instancia creada y webhook registrado).
func (s *Server) numeroEvolution(w http.ResponseWriter, r *http.Request) (*store.NumeroWhatsApp, *evolution.Client, bool) {
	id, err := pathID(r)
	if err != nil || id <= 0 {
		writeErr(w, 400, "id inválido")
		return nil, nil, false
	}
	if s.noWA(w, r) {
		return nil, nil, false
	}
	n, err := s.st(r).GetNumero(r.Context(), id)
	if err != nil {
		errSeg(w, err)
		return nil, nil, false
	}
	if n.Canal != "evolution" || n.Instancia == "" {
		writeErr(w, 400, "este número no se conecta por QR")
		return nil, nil, false
	}
	cli := s.cliNumero(n)
	if _, err := cli.EnsureInstance(r.Context()); err != nil {
		writeErr(w, 502, "evolution: "+err.Error())
		return nil, nil, false
	}
	if err := cli.Connect(r.Context(), s.webhookSecundarioURL(n.ID)); err != nil {
		log.Printf("whatsapp: connect número %d: %v", n.ID, err)
	}
	return n, cli, true
}

func (s *Server) webhookSecundarioURL(id int64) string {
	return strings.TrimSuffix(s.WebhookURL(), "/") + fmt.Sprintf("/n/%d", id)
}

func (s *Server) qrNumero(w http.ResponseWriter, r *http.Request) {
	_, cli, ok := s.numeroEvolution(w, r)
	if !ok {
		return
	}
	if st, err := cli.Status(r.Context()); err == nil && st.LoggedIn {
		writeJSON(w, 200, map[string]any{"qr": "", "conectado": true})
		return
	}
	qr, err := cli.QR(r.Context())
	if err != nil {
		writeJSON(w, 200, map[string]any{"qr": "", "conectado": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"qr": qr.Image, "conectado": false})
}

func (s *Server) vincularNumero(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Telefono string `json:"telefono"`
	}
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "json inválido")
		return
	}
	tel := strings.Map(func(c rune) rune {
		if c >= '0' && c <= '9' {
			return c
		}
		return -1
	}, in.Telefono)
	if len(tel) < 8 || len(tel) > 15 {
		writeErr(w, 400, "el número lleva de 8 a 15 dígitos con el código de país")
		return
	}
	_, cli, ok := s.numeroEvolution(w, r)
	if !ok {
		return
	}
	code, err := cli.Pair(r.Context(), tel)
	if err != nil {
		writeErr(w, 502, "no se pudo pedir el código: "+err.Error())
		return
	}
	writeJSON(w, 200, map[string]string{"codigo": code})
}

func (s *Server) plantillasCloud(w http.ResponseWriter, r *http.Request) {
	c := s.bot.Cloud()
	if c == nil || !s.isBotTenant(r) {
		writeJSON(w, 200, []cloud.Plantilla{})
		return
	}
	ps, err := c.Plantillas(r.Context())
	if err != nil {
		writeErr(w, 502, "Meta: "+err.Error())
		return
	}
	writeJSON(w, 200, ps)
}

// ---------------------------------------------------------------------------
// Campañas

func (s *Server) campanas(w http.ResponseWriter, r *http.Request) {
	hoy := time.Now().In(store.Lima)
	desde, hasta := hoy.AddDate(0, 0, -29), hoy
	if v := r.URL.Query().Get("desde"); v != "" {
		t, err := time.ParseInLocation("2006-01-02", v, store.Lima)
		if err != nil {
			writeErr(w, 400, "desde: AAAA-MM-DD")
			return
		}
		desde = t
	}
	if v := r.URL.Query().Get("hasta"); v != "" {
		t, err := time.ParseInLocation("2006-01-02", v, store.Lima)
		if err != nil {
			writeErr(w, 400, "hasta: AAAA-MM-DD")
			return
		}
		hasta = t
	}
	if hasta.Before(desde) {
		writeErr(w, 400, "«hasta» es anterior a «desde»")
		return
	}
	filas, tot, err := s.st(r).Campanas(r.Context(), desde, hasta)
	if err != nil {
		errSeg(w, err)
		return
	}
	writeJSON(w, 200, map[string]any{"filas": filas, "total": tot})
}

// ---------------------------------------------------------------------------
// Webhooks

// cloudVerificar: Meta comprueba el webhook (hub.mode=subscribe, hub.verify_token, hub.challenge).
func (s *Server) cloudVerificar(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	tok := s.cfg.WACloudVerifyToken
	if tok == "" || q.Get("hub.mode") != "subscribe" || subtle.ConstantTimeCompare([]byte(q.Get("hub.verify_token")), []byte(tok)) != 1 {
		writeErr(w, 403, "forbidden")
		return
	}
	w.Header().Set("Content-Type", "text/plain")
	_, _ = io.WriteString(w, q.Get("hub.challenge"))
}

// cloudWebhook: mensajes del número oficial. Firma obligatoria (X-Hub-Signature-256); se contesta 200 de inmediato.
func (s *Server) cloudWebhook(w http.ResponseWriter, r *http.Request) {
	c := s.bot.Cloud()
	if c == nil || !s.isBotTenant(r) {
		http.NotFound(w, r)
		return
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 8<<20))
	if err != nil {
		writeErr(w, 400, "body")
		return
	}
	if !c.FirmaValida(body, r.Header.Get("X-Hub-Signature-256")) {
		writeErr(w, 403, "firma inválida")
		return
	}
	ms, err := c.LeerWebhook(body)
	if err != nil {
		log.Printf("whatsapp cloud: webhook ilegible: %v", err)
	}
	writeJSON(w, 200, map[string]bool{"received": true})
	for _, m := range ms {
		in := bot.DesdeCloud(m)
		if in == nil {
			continue
		}
		go func(in *bot.Incoming, id string) {
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
			defer cancel()
			_ = c.MarcarLeido(ctx, id)
			s.bot.Handle(ctx, in)
		}(in, m.ID)
	}
}

// webhookSecundario: mensajes que llegan a un número secundario (evolution-go con su propia instancia).
func (s *Server) webhookSecundario(w http.ResponseWriter, r *http.Request) {
	if subtle.ConstantTimeCompare([]byte(r.PathValue("secret")), []byte(s.cfg.WebhookSecret)) != 1 {
		writeErr(w, 403, "forbidden")
		return
	}
	if !s.waEnabled(r) {
		http.NotFound(w, r)
		return
	}
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil || id <= 0 {
		http.NotFound(w, r)
		return
	}
	if _, err := s.st(r).GetNumero(r.Context(), id); err != nil {
		http.NotFound(w, r)
		return
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 64<<20))
	if err != nil {
		writeErr(w, 400, "body")
		return
	}
	in, err := bot.ParseWebhook(body)
	if err != nil {
		log.Printf("webhook número %d: payload no reconocido: %v", id, err)
	}
	writeJSON(w, 200, map[string]bool{"received": true})
	if in != nil {
		in.Canal = bot.CanalSecundario(id)
		go func() {
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
			defer cancel()
			s.bot.Handle(ctx, in)
		}()
	}
}

func firstNonEmptyAPI(v ...string) string {
	for _, x := range v {
		if x != "" {
			return x
		}
	}
	return ""
}
