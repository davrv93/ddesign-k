package store

// Fase 2 del plan de fuentes (15-10-2026): el robot PROPONE preguntas frecuentes a partir de conversaciones reales y una
// persona las aprueba. Nada entra al robot sin un clic: las propuestas quedan como fuentes tipo «faq» en estado
// «propuesta» y solo al activarlas viajan al agente.
//
// De dónde salen (mensajes de las últimas horas, por conversación):
//   - la clienta pregunta y el robot no supo (derivó a una asesora, «te lo confirma una asesora», «escribe *4*»…);
//   - la clienta pregunta y una persona del equipo contestó a mano: esa respuesta es la sugerida;
//   - la clienta repite casi la misma pregunta: la primera respuesta no le sirvió.
// Las preguntas se agrupan por parecido (palabras en común, sin tildes ni palabras vacías: `parecido`) y solo se propone un grupo con
// al menos `propuestas_minimo` apariciones (3 por defecto; 2 si una persona ya dio la respuesta). Antes de guardar se
// tapan teléfonos, correos y el nombre de la clienta. Lo ya propuesto suma ocurrencias; lo descartado no vuelve.

import (
	"context"
	"encoding/json"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"
)

const (
	SettingPropuestasMinimo = "propuestas_minimo"
	SettingPropuestasUltima = "propuestas_ultima" // RFC3339 de la última pasada
)

var (
	reNoSupo = regexp.MustCompile(`(?i)asesora|te lo confirm|te la confirm|no (tengo|cuento con) ese dato|no lo tengo a la mano|` +
		`no la tengo a la mano|escribe \*?4\*?|no te s[eé] decir|no estoy segura|no tengo esa informaci|` +
		// Como contesta V3 cuando no lo sabe (15-10-2026): «no puedo confirmar entrega para hoy», «no tengo confirmado…»,
		// «no tengo un plazo confirmado».
		`no (tengo|puedo|te puedo|podr[ií]a)\b[^.?!\n]{0,30}confirm|no (tengo|cuento con) (un |una |ese |esa )?(dato|plazo|informaci)`)
	reInterrogativa = regexp.MustCompile(`(?i)^\s*¿|\?|^\s*(qu[eé]|c[oó]mo|cu[aá]nto|cu[aá]ndo|d[oó]nde|cu[aá]l|tienen|tienes|hacen|haces|` +
		`aceptan|puedo|se puede|hay|venden|env[ií]an|trabajan|dan)\b`)
	reCorreo   = regexp.MustCompile(`[\w.+-]+@[\w-]+\.[\w.]+`)
	reNumLargo = regexp.MustCompile(`\d[\d \-]{6,}\d`)
	vacias     = map[string]bool{}
)

func init() {
	for _, w := range strings.Fields(`a al algo algun alguna alguno ante con de del el en es esa ese esta este esto
		hola la las le les lo los me mi mis muy no o para pero por que quiero se si sin su sus te tu tus un una uno unos unas y ya yo
		buenas buenos dias tardes noches gracias porfa porfavor favor ok oki seria sería tiene tienen hay
		puede pueden puedo puedes dar dan das dame hacen hacer haces saber quisiera consulta pregunta numero cual como cuando donde
		tambien bueno pues amiga senorita señorita`) {
		vacias[w] = true
	}
}

// sinTildes: las vocales con tilde o diéresis del español, sin dependencias.
var sinTildes = strings.NewReplacer("á", "a", "é", "e", "í", "i", "ó", "o", "ú", "u", "ü", "u", "à", "a", "è", "e", "ì", "i", "ò", "o", "ù", "u")

// tokens: palabras con significado, sin tildes ni mayúsculas.
func tokens(s string) map[string]bool {
	t := sinTildes.Replace(strings.ToLower(s))
	var b strings.Builder
	for _, r := range t {
		switch {
		case unicode.IsLetter(r) || unicode.IsDigit(r):
			b.WriteRune(r)
		default:
			b.WriteRune(' ')
		}
	}
	out := map[string]bool{}
	for _, w := range strings.Fields(b.String()) {
		if len(w) >= 3 && !vacias[w] {
			out[w] = true
		}
	}
	return out
}

// parecido: palabras en común sobre las de la frase más corta (coeficiente de solapamiento). Las preguntas de chat son
// cortas: «¿dan factura?» y «¿me pueden dar factura con RUC?» comparten la única palabra que importa (con Jaccard
// quedaban en grupos distintos, 15-10-2026).
func parecido(a, b map[string]bool) float64 {
	if len(a) == 0 || len(b) == 0 {
		return 0
	}
	in := 0
	for w := range a {
		if b[w] {
			in++
		}
	}
	m := len(a)
	if len(b) < m {
		m = len(b)
	}
	return float64(in) / float64(m)
}

const umbralParecido = 0.66 // 2 de 3 palabras (0,666…) cuenta como parecida

// anonimizar tapa lo que identifica a una persona: números largos, correos y su nombre.
func anonimizar(texto, nombre string) string {
	t := reCorreo.ReplaceAllString(texto, "•••")
	t = reNumLargo.ReplaceAllString(t, "•••")
	for _, w := range strings.Fields(nombre) {
		if len([]rune(w)) >= 3 {
			t = regexp.MustCompile(`(?i)\b`+regexp.QuoteMeta(w)+`\b`).ReplaceAllString(t, "•••")
		}
	}
	return strings.TrimSpace(t)
}

type candidata struct {
	texto     string
	toks      map[string]bool
	respuesta string // de una persona del equipo, si contestó
	conv      int64
}

type grupo struct {
	rep       map[string]bool
	miembros  []candidata
	respuesta string
}

// ProponerPreguntas revisa los mensajes desde `desde` y guarda propuestas nuevas (o suma ocurrencias a las que ya
// estaban propuestas). Devuelve cuántas propuestas creó o actualizó.
func (s *Store) ProponerPreguntas(ctx context.Context, desde time.Time) (int, error) {
	minimo, _ := strconv.Atoi(s.Setting(ctx, SettingPropuestasMinimo, "3"))
	if minimo < 1 {
		minimo = 3
	}
	rows, err := s.DB.QueryContext(ctx, `SELECT m.conversation_id, m.author, m.direction, m.body, coalesce(cu.name, '')
		FROM messages m JOIN conversations cv ON cv.id=m.conversation_id AND cv.tenant_id=m.tenant_id
		JOIN customers cu ON cu.id=cv.customer_id AND cu.tenant_id=m.tenant_id
		WHERE m.tenant_id=? AND m.created_at>? AND m.kind='text' ORDER BY m.conversation_id, m.id`, s.tid, desde.UTC())
	if err != nil {
		return 0, err
	}
	type msg struct {
		conv           int64
		autor, dir, tx string
		nombre         string
	}
	var ms []msg
	for rows.Next() {
		var m msg
		if err := rows.Scan(&m.conv, &m.autor, &m.dir, &m.tx, &m.nombre); err != nil {
			rows.Close()
			return 0, err
		}
		ms = append(ms, m)
	}
	rows.Close()
	esCliente := func(m msg) bool { return m.dir == "in" || m.autor == "cliente" }

	var cands []candidata
	for i := 0; i < len(ms); i++ {
		m := ms[i]
		if !esCliente(m) || !reInterrogativa.MatchString(m.tx) {
			continue
		}
		q := anonimizar(m.tx, m.nombre)
		toks := tokens(q)
		if len(toks) < 1 || len([]rune(q)) > 300 {
			continue
		}
		noSupo, humana, repite := false, "", false
		// Lo que vino después, hasta la siguiente pregunta de la clienta (y un vistazo a sus dos mensajes siguientes).
		clienta := 0
		for j := i + 1; j < len(ms) && ms[j].conv == m.conv; j++ {
			n := ms[j]
			if esCliente(n) {
				clienta++
				if parecido(toks, tokens(n.tx)) >= umbralParecido {
					repite = true
				}
				if clienta >= 2 {
					break
				}
				continue
			}
			if clienta > 0 {
				continue
			}
			switch n.autor {
			case "bot":
				if reNoSupo.MatchString(n.tx) {
					noSupo = true
				}
			case "asesora":
				if humana == "" {
					humana = anonimizar(n.tx, m.nombre)
				}
			}
		}
		if noSupo || humana != "" || repite {
			cands = append(cands, candidata{texto: q, toks: toks, respuesta: humana, conv: m.conv})
		}
	}
	if len(cands) == 0 {
		return 0, nil
	}

	// Agrupar por parecido con el primer miembro de cada grupo.
	var gs []*grupo
	for _, c := range cands {
		var g *grupo
		for _, x := range gs {
			if parecido(c.toks, x.rep) >= umbralParecido {
				g = x
				break
			}
		}
		if g == nil {
			g = &grupo{rep: c.toks}
			gs = append(gs, g)
		}
		g.miembros = append(g.miembros, c)
		if c.respuesta != "" {
			g.respuesta = c.respuesta // la más reciente
		}
	}

	existentes, err := s.ListarFuentes(ctx, FiltroFuentes{Tipo: "faq", Estado: ""})
	if err != nil {
		return 0, err
	}
	descartadas, err := s.ListarFuentes(ctx, FiltroFuentes{Tipo: "faq", Estado: "descartada"})
	if err != nil {
		return 0, err
	}
	existentes = append(existentes, descartadas...)

	hechas := 0
	for _, g := range gs {
		convs := map[int64]bool{}
		for _, m := range g.miembros {
			convs[m.conv] = true
		}
		n := len(g.miembros)
		if n < minimo && !(g.respuesta != "" && n >= 2 && minimo > 1) {
			continue
		}
		textos := make([]string, 0, n)
		for _, m := range g.miembros {
			if !enLista(m.texto, textos) {
				textos = append(textos, m.texto)
			}
		}
		sort.SliceStable(textos, func(i, j int) bool { return len([]rune(textos[i])) < len([]rune(textos[j])) })
		pregunta := textos[0]

		// ¿Ya existe una pregunta así (en cualquier estado)?
		var igual *Fuente
		var igualDatos FaqDatos
		for _, f := range existentes {
			var q FaqDatos
			if json.Unmarshal(f.Datos, &q) != nil {
				continue
			}
			for _, t := range append([]string{q.Pregunta}, q.Variantes...) {
				if parecido(g.rep, tokens(t)) >= umbralParecido {
					igual, igualDatos = f, q
					break
				}
			}
			if igual != nil {
				break
			}
		}
		if igual != nil {
			if igual.Estado != "propuesta" { // ya la tiene (o la descartó): no se vuelve a proponer
				continue
			}
			igualDatos.Ocurrencias += n
			for _, t := range textos {
				if len(igualDatos.Variantes) < 8 && t != igualDatos.Pregunta && !enLista(t, igualDatos.Variantes) {
					igualDatos.Variantes = append(igualDatos.Variantes, t)
				}
			}
			if igualDatos.Respuesta == "" {
				igualDatos.Respuesta = g.respuesta
			}
			igual.Datos, _ = json.Marshal(igualDatos)
			if err := s.GuardarFuente(ConAutor(ctx, "robot"), igual); err != nil {
				return hechas, err
			}
			hechas++
			continue
		}
		variantes := []string{}
		for _, t := range textos[1:] {
			if len(variantes) < 5 {
				variantes = append(variantes, t)
			}
		}
		datos, _ := json.Marshal(FaqDatos{Pregunta: pregunta, Variantes: variantes, Respuesta: g.respuesta, Ocurrencias: n})
		f := &Fuente{Tipo: "faq", Titulo: recortar(pregunta, 280), Estado: "propuesta", Origen: "propuesta_bot", Datos: datos,
			Contenido: propuestaNota(n, len(convs), g.respuesta != "")}
		if err := s.CrearFuente(ConAutor(ctx, "robot"), f); err != nil {
			return hechas, err
		}
		existentes = append(existentes, f)
		hechas++
	}
	return hechas, nil
}

func propuestaNota(n, convs int, humana bool) string {
	t := "Propuesta del robot: la preguntaron " + strconv.Itoa(n) + " veces en " + strconv.Itoa(convs) + " conversaciones y no supo responder."
	if humana {
		t = "Propuesta del robot: la preguntaron " + strconv.Itoa(n) + " veces en " + strconv.Itoa(convs) +
			" conversaciones; la respuesta sugerida es la que dio una persona del equipo. Revísala antes de aprobar."
	}
	return t
}
