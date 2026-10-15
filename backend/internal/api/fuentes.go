package api

// Fuentes de información, promos y embudos (15-10-2026, docs/FUENTES_CONTRATO.md). Las rutas se registran en
// rutasFuentes (extra.go). Leer es para cualquiera del equipo; crear, cambiar, activar y borrar, solo admin.

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/davrv93/ddesign-k/backend/internal/kommo"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

func (s *Server) rutasFuentes(p, a func(pattern string, h http.HandlerFunc)) {
	p("GET /api/fuentes", s.listFuentes)
	a("POST /api/fuentes", s.crearFuente)
	a("POST /api/fuentes/subir", s.subirFuente)
	a("POST /api/fuentes/proponer", s.proponerAhora)
	p("GET /api/fuentes/{id}", s.getFuente)
	a("PUT /api/fuentes/{id}", s.putFuente)
	a("DELETE /api/fuentes/{id}", s.borrarFuente)
	a("POST /api/fuentes/{id}/estado", s.estadoFuente)
	p("GET /api/fuentes/{id}/versiones", s.versionesFuente)
	p("GET /api/conocimiento", s.verConocimiento)
	p("GET /api/promos/resumen", s.resumenPromos)
	p("GET /api/conversations/{id}/fuentes", s.fuentesConversacion)

	p("GET /api/embudos", s.listEmbudos)
	a("POST /api/embudos", s.crearEmbudo)
	a("PUT /api/embudos/{id}", s.putEmbudo)
	a("DELETE /api/embudos/{id}", s.borrarEmbudo)
	p("GET /api/embudos/{id}/pedidos", s.pedidosEmbudo)
	p("PUT /api/embudos/{id}/pedidos/{order}", s.moverPedidoEmbudo)
	p("DELETE /api/embudos/{id}/pedidos/{order}", s.sacarPedidoEmbudo)
}

// errFuente: los errores de validación son 400; el resto como errStore.
func errFuente(w http.ResponseWriter, err error) {
	if errors.Is(err, store.ErrFuenteInvalida) {
		writeErr(w, 400, strings.TrimPrefix(err.Error(), store.ErrFuenteInvalida.Error()+": "))
		return
	}
	errStore(w, err)
}

// fuenteIn: lo que el panel puede escribir de una fuente.
type fuenteIn struct {
	Tipo         string          `json:"tipo"`
	Titulo       string          `json:"titulo"`
	Contenido    string          `json:"contenido"`
	Estado       string          `json:"estado"`
	VigenteDesde string          `json:"vigente_desde"`
	VigenteHasta string          `json:"vigente_hasta"`
	Etiquetas    []string        `json:"etiquetas"`
	Datos        json.RawMessage `json:"datos"`
}

func (in fuenteIn) fuente() *store.Fuente {
	return &store.Fuente{Tipo: in.Tipo, Titulo: in.Titulo, Contenido: in.Contenido, Estado: in.Estado, VigenteDesde: in.VigenteDesde,
		VigenteHasta: in.VigenteHasta, Etiquetas: in.Etiquetas, Datos: in.Datos}
}

func (s *Server) listFuentes(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	fs, err := s.st(r).ListarFuentes(r.Context(), store.FiltroFuentes{Tipo: q.Get("tipo"), Estado: q.Get("estado"), Q: q.Get("q")})
	if err != nil {
		errStore(w, err)
		return
	}
	for _, f := range fs { // en la lista basta un resumen
		f.Resumen = recortarTexto(f.Contenido, 300)
		f.Contenido = ""
	}
	writeJSON(w, 200, fs)
}

func recortarTexto(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	return string([]rune(s)[:n]) + "…"
}

func (s *Server) getFuente(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	f, err := s.st(r).GetFuente(r.Context(), id)
	if err != nil {
		errStore(w, err)
		return
	}
	writeJSON(w, 200, f)
}

func (s *Server) crearFuente(w http.ResponseWriter, r *http.Request) {
	var in fuenteIn
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	f := in.fuente()
	if f.Estado == "" || f.Estado == "propuesta" || f.Estado == "descartada" || f.Estado == "vencido" {
		f.Estado = "borrador"
	}
	if err := s.st(r).CrearFuente(r.Context(), f); err != nil {
		errFuente(w, err)
		return
	}
	s.pub(r, "fuentes")
	writeJSON(w, 201, f)
}

func (s *Server) putFuente(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	var in fuenteIn
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	f := in.fuente()
	f.ID = id
	f.Estado = "" // el estado se cambia con POST /estado (activar exige revisar)
	if err := s.st(r).GuardarFuente(r.Context(), f); err != nil {
		errFuente(w, err)
		return
	}
	s.pub(r, "fuentes")
	writeJSON(w, 200, f)
}

func (s *Server) estadoFuente(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	var in struct {
		Estado string `json:"estado"`
	}
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	f, err := s.st(r).CambiarEstadoFuente(r.Context(), id, in.Estado)
	if err != nil {
		errFuente(w, err)
		return
	}
	s.pub(r, "fuentes")
	writeJSON(w, 200, f)
}

func (s *Server) borrarFuente(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	if err := s.st(r).BorrarFuente(r.Context(), id); err != nil {
		errStore(w, err)
		return
	}
	s.pub(r, "fuentes")
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func (s *Server) versionesFuente(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	vs, err := s.st(r).VersionesFuente(r.Context(), id)
	if err != nil {
		errStore(w, err)
		return
	}
	writeJSON(w, 200, vs)
}

// slugDe: el slug de la empresa de la petición (o «tienda» sin multiempresa).
func (s *Server) slugDe(r *http.Request) string {
	if t := TenantOf(r.Context()); t != nil {
		return t.Slug
	}
	return "tienda"
}

func (s *Server) verConocimiento(w http.ResponseWriter, r *http.Request) {
	c, err := s.st(r).ConocimientoVigente(r.Context(), s.slugDe(r), time.Now())
	if err != nil {
		errStore(w, err)
		return
	}
	writeJSON(w, 200, c)
}

func (s *Server) resumenPromos(w http.ResponseWriter, r *http.Request) {
	res, err := s.st(r).ResumenPromos(r.Context(), time.Now())
	if err != nil {
		errStore(w, err)
		return
	}
	writeJSON(w, 200, res)
}

func (s *Server) fuentesConversacion(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	us, err := s.st(r).FuentesDeConversacion(r.Context(), id)
	if err != nil {
		errStore(w, err)
		return
	}
	writeJSON(w, 200, us)
}

// proponerAhora corre la pasada de propuestas del robot sobre las últimas 72 horas (la nocturna mira 24 h).
func (s *Server) proponerAhora(w http.ResponseWriter, r *http.Request) {
	n, err := s.st(r).ProponerPreguntas(r.Context(), time.Now().Add(-72*time.Hour))
	if err != nil {
		errStore(w, err)
		return
	}
	if n > 0 {
		s.pub(r, "fuentes")
	}
	writeJSON(w, 200, map[string]int{"propuestas": n})
}

// ---------------------------------------------------------------------------
// Subir material: el texto se extrae aquí y queda como borrador editable; el original se guarda junto a las fotos.

const maxArchivo = 15 << 20

var reEspacios = regexp.MustCompile(`[ \t]+\n|\n{3,}`)

func (s *Server) subirFuente(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, maxArchivo+1<<20)
	if err := r.ParseMultipartForm(maxArchivo); err != nil {
		writeErr(w, 400, "archivo demasiado grande (máximo 15 MB) o formulario inválido")
		return
	}
	file, hdr, err := r.FormFile("archivo")
	if err != nil {
		writeErr(w, 400, "falta el archivo")
		return
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, maxArchivo+1))
	if err != nil || len(data) > maxArchivo {
		writeErr(w, 400, "archivo demasiado grande (máximo 15 MB)")
		return
	}
	nombre := filepath.Base(hdr.Filename)
	ext := strings.ToLower(filepath.Ext(nombre))
	texto, err := extraerTexto(r.Context(), ext, data)
	if err != nil {
		writeErr(w, 400, err.Error())
		return
	}
	tipo := r.FormValue("tipo")
	if tipo == "" {
		tipo = "material"
	}
	titulo := strings.TrimSpace(r.FormValue("titulo"))
	if titulo == "" {
		titulo = strings.TrimSuffix(nombre, filepath.Ext(nombre))
	}
	dir := filepath.Join(s.mediaRoot(r), "..", "fuentes")
	_ = os.MkdirAll(dir, 0o755)
	guardado := strconv.FormatInt(time.Now().Unix(), 36) + "-" + regexp.MustCompile(`[^\w.\-]+`).ReplaceAllString(nombre, "_")
	if err := os.WriteFile(filepath.Join(dir, guardado), data, 0o644); err != nil {
		log.Printf("fuentes: no se guardó el original %s: %v", guardado, err)
		guardado = ""
	}
	f := &store.Fuente{Tipo: tipo, Titulo: titulo, Contenido: texto, Estado: "borrador", Origen: "archivo:" + nombre, Archivo: guardado}
	if tipo == "faq" { // una FAQ necesita pregunta: el título hace de pregunta y el texto de respuesta
		f.Datos, _ = json.Marshal(store.FaqDatos{Pregunta: titulo, Respuesta: texto})
	}
	if err := s.st(r).CrearFuente(r.Context(), f); err != nil {
		errFuente(w, err)
		return
	}
	s.pub(r, "fuentes")
	writeJSON(w, 201, f)
}

// extraerTexto: pdf (pdftotext de poppler, en la imagen), docx (el XML del documento), txt, md y csv.
func extraerTexto(ctx context.Context, ext string, data []byte) (string, error) {
	var t string
	switch ext {
	case ".txt", ".md", ".csv":
		if !utf8.Valid(data) {
			return "", fmt.Errorf("el archivo no está en UTF-8")
		}
		t = string(data)
	case ".docx":
		zr, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
		if err != nil {
			return "", fmt.Errorf("no se pudo abrir el docx")
		}
		for _, f := range zr.File {
			if f.Name != "word/document.xml" {
				continue
			}
			rc, err := f.Open()
			if err != nil {
				return "", fmt.Errorf("no se pudo leer el docx")
			}
			t, err = textoDocx(io.LimitReader(rc, 50<<20))
			rc.Close()
			if err != nil {
				return "", fmt.Errorf("no se pudo leer el docx")
			}
		}
	case ".pdf":
		if _, err := exec.LookPath("pdftotext"); err != nil {
			return "", fmt.Errorf("este servidor no lee PDF (falta pdftotext): pega el texto a mano")
		}
		cctx, cancel := context.WithTimeout(ctx, 30*time.Second)
		defer cancel()
		cmd := exec.CommandContext(cctx, "pdftotext", "-layout", "-enc", "UTF-8", "-", "-")
		cmd.Stdin = bytes.NewReader(data)
		out, err := cmd.Output()
		if err != nil {
			return "", fmt.Errorf("no se pudo leer el PDF")
		}
		t = string(out)
	default:
		return "", fmt.Errorf("formato no admitido: sube pdf, docx, txt, md o csv")
	}
	t = strings.ReplaceAll(t, "\r\n", "\n")
	t = reEspacios.ReplaceAllStringFunc(t, func(m string) string {
		if strings.HasPrefix(m, "\n\n") {
			return "\n\n"
		}
		return "\n"
	})
	t = strings.TrimSpace(t)
	if t == "" {
		return "", fmt.Errorf("el archivo no trae texto (¿es una imagen escaneada?): pega el texto a mano")
	}
	return t, nil
}

// textoDocx: el texto de word/document.xml, un párrafo por línea.
func textoDocx(r io.Reader) (string, error) {
	dec := xml.NewDecoder(r)
	var b strings.Builder
	for {
		tok, err := dec.Token()
		if err == io.EOF {
			break
		}
		if err != nil {
			return "", err
		}
		switch e := tok.(type) {
		case xml.StartElement:
			if e.Name.Local == "tab" {
				b.WriteString("\t")
			}
			if e.Name.Local == "br" {
				b.WriteString("\n")
			}
		case xml.EndElement:
			if e.Name.Local == "p" {
				b.WriteString("\n")
			}
		case xml.CharData:
			b.Write(e)
		}
	}
	return b.String(), nil
}

// ---------------------------------------------------------------------------
// Embudos

func (s *Server) listEmbudos(w http.ResponseWriter, r *http.Request) {
	es, err := s.st(r).ListarEmbudos(r.Context())
	if err != nil {
		errStore(w, err)
		return
	}
	writeJSON(w, 200, es)
}

type embudoIn struct {
	Nombre  string   `json:"nombre"`
	Etapas  []string `json:"etapas"`
	PromoID int64    `json:"promo_id"`
}

func (s *Server) crearEmbudo(w http.ResponseWriter, r *http.Request) {
	var in embudoIn
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	if len(in.Etapas) == 0 {
		in.Etapas = store.EtapasSugeridas
	}
	st := s.st(r)
	e := &store.Embudo{Nombre: in.Nombre, Etapas: in.Etapas, PromoID: in.PromoID}
	if err := st.CrearEmbudo(r.Context(), e); err != nil {
		errFuente(w, err)
		return
	}
	// La promo apunta a su embudo (datos.embudo_id), así el resumen y el panel lo encuentran.
	if e.PromoID > 0 {
		if f, err := st.GetFuente(r.Context(), e.PromoID); err == nil {
			var p store.PromoDatos
			if json.Unmarshal(f.Datos, &p) == nil && p.EmbudoID != e.ID {
				p.EmbudoID = e.ID
				f.Datos, _ = json.Marshal(p)
				f.Estado = ""
				_ = st.GuardarFuente(r.Context(), f)
			}
		}
	}
	s.espejoKommo(r, e)
	s.pub(r, "fuentes")
	writeJSON(w, 201, e)
}

// espejoKommo crea en Kommo el embudo de la promo (con las etapas del bot, que son las que mueve el agente) cuando Kommo
// está encendido para la empresa de esta petición. En segundo plano: si Kommo falla, el embudo del panel queda igual.
func (s *Server) espejoKommo(r *http.Request, e *store.Embudo) {
	if s.Kommo == nil || !s.isBotTenant(r) {
		return
	}
	st, nombre, id := s.st(r), "Promo · "+e.Nombre, e.ID
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
		defer cancel()
		esq, err := kommo.Asegurar(ctx, s.Kommo.Cliente(), nombre)
		if err != nil {
			log.Printf("kommo: embudo de la promo %q: %v", nombre, err)
			return
		}
		if err := st.GuardarPipelineKommo(ctx, id, esq.PipelineID); err != nil {
			log.Printf("kommo: guardar el embudo de la promo: %v", err)
		}
		s.Kommo.RegistrarEsquema(nombre, esq)
	}()
}

func (s *Server) putEmbudo(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	var in embudoIn
	if err := readJSON(r, &in); err != nil {
		writeErr(w, 400, "datos inválidos")
		return
	}
	e := &store.Embudo{ID: id, Nombre: in.Nombre, Etapas: in.Etapas}
	if err := s.st(r).GuardarEmbudo(r.Context(), e); err != nil {
		errFuente(w, err)
		return
	}
	s.pub(r, "fuentes")
	writeJSON(w, 200, e)
}

func (s *Server) borrarEmbudo(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	if err := s.st(r).BorrarEmbudo(r.Context(), id); err != nil {
		errFuente(w, err)
		return
	}
	s.pub(r, "fuentes")
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func (s *Server) pedidosEmbudo(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	e, ps, err := s.st(r).PedidosDeEmbudo(r.Context(), id)
	if err != nil {
		errFuente(w, err)
		return
	}
	writeJSON(w, 200, map[string]any{"embudo": e, "pedidos": ps})
}

func (s *Server) moverPedidoEmbudo(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	oid, err := strconv.ParseInt(r.PathValue("order"), 10, 64)
	if err != nil {
		writeErr(w, 400, "pedido inválido")
		return
	}
	var in struct {
		Etapa string `json:"etapa"`
	}
	if err := readJSON(r, &in); err != nil || strings.TrimSpace(in.Etapa) == "" {
		writeErr(w, 400, "falta la etapa")
		return
	}
	if err := s.st(r).MoverPedidoEmbudo(r.Context(), id, oid, in.Etapa); err != nil {
		errFuente(w, err)
		return
	}
	s.pub(r, "orders")
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func (s *Server) sacarPedidoEmbudo(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		writeErr(w, 400, "id inválido")
		return
	}
	oid, err := strconv.ParseInt(r.PathValue("order"), 10, 64)
	if err != nil {
		writeErr(w, 400, "pedido inválido")
		return
	}
	if err := s.st(r).MoverPedidoEmbudo(r.Context(), id, oid, ""); err != nil {
		errFuente(w, err)
		return
	}
	s.pub(r, "orders")
	writeJSON(w, 200, map[string]bool{"ok": true})
}
