package store

// Flujos visuales de seguimiento (20-10-2026, pedido del usuario: «editor de flujos con zoom, mover, guardar y conectar,
// que funcione en la emisión y con un botón de probar»). Contrato en docs/SEGUIMIENTO_CONTRATO.md §4b.
//
// Un flujo es el grafo de una secuencia: nodos (inicio, esperar, mensaje, condición, tarea, etapa, ir a otro flujo, fin)
// unidos por aristas que salen de una salida con nombre («siguiente», o «si»/«no» en las condiciones). Se guarda en
// secuencias.grafo; si está vacío la secuencia es la lista de pasos de siempre y nada cambia para ella. Los pasos de una
// secuencia con grafo se DERIVAN de sus nodos de mensaje (tablero, vista previa y cifras siguen funcionando).

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"
	"time"
	"unicode/utf8"
)

// Tipos de nodo y salidas.
var (
	TiposNodo        = []string{"inicio", "esperar", "mensaje", "condicion", "tarea", "etapa", "ir_a", "fin"}
	CondicionesFlujo = []string{"respondio", "temperatura", "categoria", "tiene_contenido"}
)

const (
	MaxNodosFlujo    = 80
	MaxMensajesFlujo = 40
	SalidaSiguiente  = "siguiente"
	SalidaSi         = "si"
	SalidaNo         = "no"
)

// Nodo de un flujo. Los campos que no usa su tipo van vacíos.
type Nodo struct {
	ID   string  `json:"id"`
	Tipo string  `json:"tipo"`
	X    float64 `json:"x"`
	Y    float64 `json:"y"`
	// esperar
	Minutos int `json:"minutos,omitempty"`
	// mensaje: lo mismo que un paso de la lista (emoji, texto, beneficio, testimonio, tip, oferta)
	Mensaje   string `json:"mensaje,omitempty"`
	Texto     string `json:"texto,omitempty"`
	MediaURL  string `json:"media_url,omitempty"`
	PromoID   int64  `json:"promo_id,omitempty"`
	Plantilla string `json:"plantilla,omitempty"`
	// condicion: «respondio» (escribió desde el último mensaje del flujo), «temperatura» (valor: caliente|tibia|fria),
	// «categoria» (valor: la categoría del modelo en foco), «tiene_contenido» (valor: beneficio|testimonio|tip)
	Condicion string `json:"condicion,omitempty"`
	Valor     string `json:"valor,omitempty"`
	// tarea: título de la tarea para el equipo; etapa: la etapa del embudo; ir_a: la secuencia (flujo) destino
	Titulo  string `json:"titulo,omitempty"`
	Etapa   string `json:"etapa,omitempty"`
	Destino int64  `json:"destino,omitempty"`
	// Nota libre del editor (no se envía).
	Nota string `json:"nota,omitempty"`
}

// Arista: de la salida `Salida` del nodo `Desde` al nodo `Hasta`.
type Arista struct {
	Desde  string `json:"desde"`
	Salida string `json:"salida"`
	Hasta  string `json:"hasta"`
}

// Vista: dónde dejó el lienzo la última persona que lo editó (desplazamiento y zoom).
type Vista struct {
	X    float64 `json:"x"`
	Y    float64 `json:"y"`
	Zoom float64 `json:"zoom"`
}

type Grafo struct {
	Nodos   []Nodo   `json:"nodos"`
	Aristas []Arista `json:"aristas"`
	Vista   *Vista   `json:"vista,omitempty"`
}

// Nodo devuelve el nodo con ese id, o nil.
func (g *Grafo) Nodo(id string) *Nodo {
	for i := range g.Nodos {
		if g.Nodos[i].ID == id {
			return &g.Nodos[i]
		}
	}
	return nil
}

// Inicio: el id del nodo de inicio ("" si no hay).
func (g *Grafo) Inicio() string {
	for _, n := range g.Nodos {
		if n.Tipo == "inicio" {
			return n.ID
		}
	}
	return ""
}

// Siguiente: a qué nodo lleva la salida `salida` de `desde` ("" = ninguna: el flujo termina ahí).
func (g *Grafo) Siguiente(desde, salida string) string {
	for _, a := range g.Aristas {
		if a.Desde == desde && a.Salida == salida {
			return a.Hasta
		}
	}
	return ""
}

func salidasDe(tipo string) []string {
	switch tipo {
	case "condicion":
		return []string{SalidaSi, SalidaNo}
	case "fin":
		return nil
	}
	return []string{SalidaSiguiente}
}

// PasoDe: el nodo de mensaje como paso (para reusar ContenidoPaso y el envío de la lista).
func (n *Nodo) PasoDe() Paso {
	return Paso{EsperaMin: 1, Tipo: n.Mensaje, Texto: n.Texto, PromoID: n.PromoID, Plantilla: n.Plantilla, MediaURL: n.MediaURL}
}

// normalizar valida el grafo. Devuelve error con ErrSeguimientoInvalido y un texto para la persona que lo edita.
func (g *Grafo) normalizar() error {
	mal := func(f string, a ...any) error {
		return fmt.Errorf("%w: %s", ErrSeguimientoInvalido, fmt.Sprintf(f, a...))
	}
	if len(g.Nodos) == 0 {
		return mal("el flujo no tiene nodos")
	}
	if len(g.Nodos) > MaxNodosFlujo {
		return mal("el flujo lleva como máximo %d nodos", MaxNodosFlujo)
	}
	ids := map[string]*Nodo{}
	inicios, mensajes := 0, 0
	for i := range g.Nodos {
		n := &g.Nodos[i]
		n.ID = strings.TrimSpace(n.ID)
		if n.ID == "" || len(n.ID) > 40 {
			return mal("nodo %d sin id válido", i+1)
		}
		if ids[n.ID] != nil {
			return mal("hay dos nodos con el id %q", n.ID)
		}
		ids[n.ID] = n
		if !enLista(n.Tipo, TiposNodo) {
			return mal("nodo %q: tipo %q", n.ID, n.Tipo)
		}
		n.Texto, n.Titulo, n.Nota = strings.TrimSpace(n.Texto), strings.TrimSpace(n.Titulo), strings.TrimSpace(n.Nota)
		n.MediaURL, n.Plantilla, n.Valor = strings.TrimSpace(n.MediaURL), strings.TrimSpace(n.Plantilla), strings.TrimSpace(n.Valor)
		etiqueta := nombreNodo(n)
		switch n.Tipo {
		case "inicio":
			inicios++
		case "esperar":
			if n.Minutos < 1 || n.Minutos > MaxEsperaMin {
				return mal("%s: la espera va de 1 minuto a 14 días", etiqueta)
			}
		case "mensaje":
			mensajes++
			p := n.PasoDe()
			if !enLista(p.Tipo, TiposPaso) {
				return mal("%s: elige qué mensaje se envía", etiqueta)
			}
			if p.MediaURL != "" && !strings.HasPrefix(p.MediaURL, "/media/") && !strings.HasPrefix(p.MediaURL, "https://") {
				return mal("%s: la imagen debe subirse desde el panel o empezar con https://", etiqueta)
			}
			if (p.Tipo == "emoji" || p.Tipo == "texto") && p.Texto == "" && p.MediaURL == "" {
				return mal("%s: falta el texto o la imagen", etiqueta)
			}
			if p.Tipo == "oferta" && p.PromoID <= 0 {
				return mal("%s: la oferta necesita una promo", etiqueta)
			}
			if utf8.RuneCountInString(p.Texto) > 1000 {
				return mal("%s: el texto es demasiado largo", etiqueta)
			}
		case "condicion":
			if !enLista(n.Condicion, CondicionesFlujo) {
				return mal("%s: elige qué se pregunta", etiqueta)
			}
			if n.Condicion != "respondio" && n.Valor == "" {
				return mal("%s: falta el valor de la condición", etiqueta)
			}
		case "tarea":
			if n.Titulo == "" || utf8.RuneCountInString(n.Titulo) > 300 {
				return mal("%s: escribe el título de la tarea (hasta 300 caracteres)", etiqueta)
			}
		case "etapa":
			if !ValidEtapa(n.Etapa) {
				return mal("%s: elige la etapa del embudo", etiqueta)
			}
		case "ir_a":
			if n.Destino <= 0 {
				return mal("%s: elige a qué flujo pasa", etiqueta)
			}
		}
	}
	if inicios != 1 {
		return mal("el flujo necesita exactamente un nodo de inicio")
	}
	if mensajes == 0 {
		return mal("el flujo necesita al menos un mensaje")
	}
	if mensajes > MaxMensajesFlujo {
		return mal("el flujo lleva como máximo %d mensajes", MaxMensajesFlujo)
	}
	usadas := map[string]bool{}
	limpias := g.Aristas[:0]
	for _, a := range g.Aristas {
		d, h := ids[a.Desde], ids[a.Hasta]
		if d == nil || h == nil {
			return mal("una conexión apunta a un nodo que no existe")
		}
		if a.Desde == a.Hasta {
			return mal("%s: un nodo no se puede conectar consigo mismo", nombreNodo(d))
		}
		if h.Tipo == "inicio" {
			return mal("nada puede llevar al inicio")
		}
		if !enLista(a.Salida, salidasDe(d.Tipo)) {
			return mal("%s: salida %q no existe", nombreNodo(d), a.Salida)
		}
		k := a.Desde + "\x00" + a.Salida
		if usadas[k] {
			return mal("%s: cada salida va a un solo nodo", nombreNodo(d))
		}
		usadas[k] = true
		limpias = append(limpias, a)
	}
	g.Aristas = limpias
	if g.Siguiente(g.Inicio(), SalidaSiguiente) == "" {
		return mal("conecta el inicio con el primer paso")
	}
	if c := g.cicloSinEspera(); c != "" {
		return mal("%s: forma un ciclo sin ninguna espera (se repetiría sin parar)", c)
	}
	if g.Vista != nil && (g.Vista.Zoom <= 0 || g.Vista.Zoom > 4) {
		g.Vista.Zoom = 1
	}
	return nil
}

// ValidarGrafo: las reglas de guardado, para la simulación del editor (que prueba un flujo sin guardarlo).
func ValidarGrafo(g *Grafo) error {
	if g == nil {
		return fmt.Errorf("%w: falta el flujo", ErrSeguimientoInvalido)
	}
	return g.normalizar()
}

// cicloSinEspera: el nombre de un nodo que está en un ciclo sin nodos de espera, o "".
func (g *Grafo) cicloSinEspera() string {
	estado := map[string]int{} // 0 sin ver, 1 en la pila, 2 listo
	var visitar func(id string) string
	visitar = func(id string) string {
		n := g.Nodo(id)
		if n == nil || n.Tipo == "esperar" {
			return ""
		}
		switch estado[id] {
		case 1:
			return nombreNodo(n)
		case 2:
			return ""
		}
		estado[id] = 1
		for _, a := range g.Aristas {
			if a.Desde == id {
				if c := visitar(a.Hasta); c != "" {
					return c
				}
			}
		}
		estado[id] = 2
		return ""
	}
	for _, n := range g.Nodos {
		if c := visitar(n.ID); c != "" {
			return c
		}
	}
	return ""
}

func nombreNodo(n *Nodo) string {
	nombres := map[string]string{"inicio": "Inicio", "esperar": "Esperar", "mensaje": "Mensaje", "condicion": "Condición",
		"tarea": "Tarea", "etapa": "Etapa", "ir_a": "Ir a otro flujo", "fin": "Fin"}
	return fmt.Sprintf("«%s» (%s)", nombres[n.Tipo], n.ID)
}

// PasosDerivados: los mensajes del flujo en el orden en que se alcanzan desde el inicio (anchura primero, «sí» antes que
// «no»), con la espera acumulada desde el mensaje anterior de ese camino. Sirven para el tablero, la vista previa y el
// resumen; el motor recorre el grafo, no esta lista.
func (g *Grafo) PasosDerivados() []Paso {
	type item struct {
		id     string
		espera int
	}
	visto := map[string]bool{}
	cola := []item{{g.Inicio(), 0}}
	var out []Paso
	for len(cola) > 0 && len(out) < MaxMensajesFlujo {
		it := cola[0]
		cola = cola[1:]
		if it.id == "" || visto[it.id] {
			continue
		}
		visto[it.id] = true
		n := g.Nodo(it.id)
		if n == nil {
			continue
		}
		espera := it.espera
		switch n.Tipo {
		case "esperar":
			espera += n.Minutos
		case "mensaje":
			p := n.PasoDe()
			p.EsperaMin = espera
			if p.EsperaMin < 1 {
				p.EsperaMin = 1
			}
			out = append(out, p)
			espera = 0
		}
		for _, s := range salidasDe(n.Tipo) {
			if h := g.Siguiente(n.ID, s); h != "" {
				cola = append(cola, item{h, espera})
			}
		}
	}
	return out
}

// esperaAntesDeMensaje: el nodo desde el que retomar el mensaje número i (base 0) de PasosDerivados: la espera que lo
// precede si la hay, si no el propio mensaje; "" si no existe.
func (g *Grafo) esperaAntesDeMensaje(i int) string {
	for _, n := range g.Nodos {
		if n.Tipo != "mensaje" || g.IndiceMensaje(n.ID) != i {
			continue
		}
		for _, a := range g.Aristas {
			if a.Hasta == n.ID {
				if d := g.Nodo(a.Desde); d != nil && d.Tipo == "esperar" {
					return d.ID
				}
			}
		}
		return n.ID
	}
	return ""
}

// IndiceMensaje: la posición del nodo de mensaje `id` en PasosDerivados (para la columna del tablero), o -1.
func (g *Grafo) IndiceMensaje(id string) int {
	visto := map[string]bool{}
	cola := []string{g.Inicio()}
	i := 0
	for len(cola) > 0 {
		x := cola[0]
		cola = cola[1:]
		if x == "" || visto[x] {
			continue
		}
		visto[x] = true
		n := g.Nodo(x)
		if n == nil {
			continue
		}
		if n.Tipo == "mensaje" {
			if n.ID == id {
				return i
			}
			i++
		}
		for _, s := range salidasDe(n.Tipo) {
			if h := g.Siguiente(n.ID, s); h != "" {
				cola = append(cola, h)
			}
		}
	}
	return -1
}

// GrafoDesdePasos: la lista de pasos de siempre como flujo (inicio → esperar → mensaje → … → fin), para abrir una
// secuencia vieja en el editor visual.
func GrafoDesdePasos(pasos []Paso) *Grafo {
	g := &Grafo{Nodos: []Nodo{{ID: "inicio", Tipo: "inicio", X: 40, Y: 160}}}
	prev, x := "inicio", 300.0
	for i, p := range pasos {
		e := fmt.Sprintf("espera-%d", i+1)
		m := fmt.Sprintf("mensaje-%d", i+1)
		g.Nodos = append(g.Nodos, Nodo{ID: e, Tipo: "esperar", X: x, Y: 160, Minutos: p.EsperaMin},
			Nodo{ID: m, Tipo: "mensaje", X: x + 260, Y: 160, Mensaje: p.Tipo, Texto: p.Texto, MediaURL: p.MediaURL, PromoID: p.PromoID,
				Plantilla: p.Plantilla})
		g.Aristas = append(g.Aristas, Arista{Desde: prev, Salida: SalidaSiguiente, Hasta: e}, Arista{Desde: e, Salida: SalidaSiguiente, Hasta: m})
		prev, x = m, x+520
	}
	g.Nodos = append(g.Nodos, Nodo{ID: "fin", Tipo: "fin", X: x, Y: 160})
	g.Aristas = append(g.Aristas, Arista{Desde: prev, Salida: SalidaSiguiente, Hasta: "fin"})
	return g
}

// ---------------------------------------------------------------------------
// Estado del flujo por conversación (seguimiento_en_curso.nodo / prueba / escala_min)

// migrarFlujos agrega las columnas de los flujos (20-10-2026). Idempotente.
func (s *Store) migrarFlujos() error {
	cols := []struct{ tabla, col, def string }{
		{"secuencias", "grafo", "TEXT NULL"},
		{"seguimiento_en_curso", "nodo", "VARCHAR(40) NOT NULL DEFAULT ''"},
		{"seguimiento_en_curso", "prueba", "INTEGER NOT NULL DEFAULT 0"},
		{"seguimiento_en_curso", "escala_min", "INTEGER NOT NULL DEFAULT 0"},
	}
	for _, c := range cols {
		if _, err := s.DB.Exec(`SELECT ` + c.col + ` FROM ` + c.tabla + ` LIMIT 0`); err == nil {
			continue
		}
		if _, err := s.DB.Exec(`ALTER TABLE ` + c.tabla + ` ADD COLUMN ` + c.col + ` ` + c.def); err != nil {
			return fmt.Errorf("%s.%s: %w", c.tabla, c.col, err)
		}
	}
	return nil
}

// armarFlujo: ArmarSeguimiento para flujos. El estado apunta al nodo que toca: al armar, el inicio (el motor lo recorre
// en el siguiente minuto y se detiene en la primera espera). Si estaba pausado porque ella respondió: «continuar» sigue
// en el mismo nodo (si era una espera, vuelve a contar desde ahora), «reiniciar» vuelve al inicio, «detener» no sigue.
// Un flujo que pasó a otro con «ir a» sigue en ese otro.
func (s *Store) armarFlujo(ctx context.Context, convID int64, q *Secuencia, prev *SeguimientoEnCurso, codigo string, desde time.Time) error {
	actual := q
	nodo := ""
	if prev != nil {
		switch {
		case prev.Estado == "terminado" || prev.Estado == "detenido":
			return nil
		case prev.Estado == EstadoActivo && (prev.SecuenciaID == q.ID || prev.Nodo != ""):
			return nil // ya corre: el reloj no se reinicia con cada mensaje del bot
		case prev.Estado == EstadoPausado && prev.Nodo == "" && prev.SecuenciaID == q.ID && q.AlResponder == "continuar":
			// Estaba en la lista de pasos y la secuencia pasó a flujo: sigue en la espera antes del mensaje que tocaba.
			nodo = q.Grafo.esperaAntesDeMensaje(prev.indice)
		case prev.Estado == EstadoPausado && prev.SecuenciaID == q.ID && prev.Nodo == "" && q.AlResponder == "detener":
			return nil
		case prev.Estado == EstadoPausado && prev.Nodo != "":
			if p, err := s.GetSecuencia(ctx, prev.SecuenciaID); err == nil && p.Grafo != nil && p.Grafo.Nodo(prev.Nodo) != nil {
				actual = p
				switch p.AlResponder {
				case "detener":
					return nil
				case "continuar":
					nodo = prev.Nodo
				}
			}
		}
	}
	if actual.Grafo == nil {
		return nil // la secuencia dejó de ser un flujo: se arma la próxima vez como lista
	}
	if nodo == "" {
		nodo = actual.Grafo.Inicio()
	}
	prox := desde.Add(time.Minute)
	if n := actual.Grafo.Nodo(nodo); n != nil && n.Tipo == "esperar" {
		prox = desde.Add(time.Duration(n.Minutos) * time.Minute)
	}
	t := now()
	if prev == nil {
		_, err := s.DB.ExecContext(ctx, `INSERT INTO seguimiento_en_curso(tenant_id, conversation_id, secuencia_id, paso, proximo_en, estado,
			product_code, armado_en, actualizado_en, nodo) VALUES(?,?,?,?,?,?,?,?,?,?)`, s.tid, convID, actual.ID, 0, prox.UTC(), EstadoActivo,
			strings.ToUpper(codigo), t, t, nodo)
		return err
	}
	_, err := s.DB.ExecContext(ctx, `UPDATE seguimiento_en_curso SET secuencia_id=?, nodo=?, proximo_en=?, estado=?, product_code=?,
		prueba=0, escala_min=0, actualizado_en=? WHERE tenant_id=? AND conversation_id=?`, actual.ID, nodo, prox.UTC(), EstadoActivo,
		strings.ToUpper(codigo), t, s.tid, convID)
	return err
}

// PonerNodo: el flujo de la conversación queda en el nodo `nodo` (de la secuencia `secID`), que toca en `en`.
func (s *Store) PonerNodo(ctx context.Context, convID, secID int64, nodo string, paso int, en time.Time) error {
	_, err := s.DB.ExecContext(ctx, `UPDATE seguimiento_en_curso SET secuencia_id=?, nodo=?, paso=?, proximo_en=?, actualizado_en=?
		WHERE tenant_id=? AND conversation_id=?`, secID, nodo, paso, en.UTC(), now(), s.tid, convID)
	return err
}

// TerminarFlujo: llegó a un fin (o a una salida sin conectar) en la secuencia `secID` (la de «ir a» si pasó a otra).
func (s *Store) TerminarFlujo(ctx context.Context, convID, secID int64) error {
	_, err := s.DB.ExecContext(ctx, `UPDATE seguimiento_en_curso SET secuencia_id=?, proximo_en=NULL, estado='terminado', actualizado_en=?
		WHERE tenant_id=? AND conversation_id=?`, secID, now(), s.tid, convID)
	return err
}

// ArmarPrueba: «Probar» del editor. La conversación del número de prueba corre el flujo desde el inicio, ya, con cada
// espera reducida a `escalaMin` minutos (0 = las esperas reales), sin horario, tope ni ventana de 24 h, aunque la
// secuencia esté apagada. Pisa el seguimiento que tuviera esa conversación.
func (s *Store) ArmarPrueba(ctx context.Context, convID int64, q *Secuencia, codigo string, escalaMin int) error {
	if q.Grafo == nil {
		return fmt.Errorf("%w: la prueba es para flujos del editor visual", ErrSeguimientoInvalido)
	}
	t := now()
	if _, err := s.DB.ExecContext(ctx, `DELETE FROM seguimiento_en_curso WHERE tenant_id=? AND conversation_id=?`, s.tid, convID); err != nil {
		return err
	}
	_, err := s.DB.ExecContext(ctx, `INSERT INTO seguimiento_en_curso(tenant_id, conversation_id, secuencia_id, paso, proximo_en, estado,
		product_code, armado_en, actualizado_en, nodo, prueba, escala_min) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)`, s.tid, convID, q.ID, 0, t.UTC(),
		EstadoActivo, strings.ToUpper(codigo), t, t, q.Grafo.Inicio(), 1, escalaMin)
	return err
}

// UltimoEnvioFlujo: cuándo salió el último mensaje del seguimiento a esta conversación (para «¿respondió?»).
func (s *Store) UltimoEnvioFlujo(ctx context.Context, convID int64) (time.Time, bool) {
	var t sql.NullString
	if err := s.DB.QueryRowContext(ctx, `SELECT MAX(enviado_en) FROM seguimiento_envios WHERE tenant_id=? AND conversation_id=?
		AND resultado IN ('enviado','prueba')`, s.tid, convID).Scan(&t); err != nil || !t.Valid || t.String == "" {
		return time.Time{}, false
	}
	pt := parseTiempo(t.String)
	return pt, !pt.IsZero()
}

// grafoJSON / leerGrafo: la columna secuencias.grafo ("" o NULL = sin flujo).
func grafoJSON(g *Grafo) any {
	if g == nil {
		return nil
	}
	b, _ := json.Marshal(g)
	return string(b)
}

func leerGrafo(v string) *Grafo {
	if strings.TrimSpace(v) == "" {
		return nil
	}
	var g Grafo
	if json.Unmarshal([]byte(v), &g) != nil || len(g.Nodos) == 0 {
		return nil
	}
	return &g
}
