package bot

// Seguimiento por modelo (octubre de 2026, pedido de Luis, dueño de Baruka). Contrato: docs/SEGUIMIENTO_CONTRATO.md §4.
//
// Cuando el bot deja la conversación esperando a la clienta con un modelo en foco, se arma la secuencia que corresponde
// a ese modelo (store.SecuenciaPara). Si ella no contesta, TickSeguimiento manda los pasos a su hora: emoji, beneficio,
// testimonio, tip, oferta… Si escribe, el seguimiento se pausa; según la secuencia, sigue desde el paso siguiente, vuelve
// a empezar o se detiene la próxima vez que se calle. Una persona que escribe (panel o celular) lo detiene.
//
// Nunca es un envío masivo: solo conversaciones que la clienta empezó, con tope diario por número, horario de la tienda
// y la ventana de 24 h de WhatsApp (fuera de ella, lo que diga la secuencia: saltar, plantilla o número secundario).

import (
	"context"
	"fmt"
	"log"
	"strings"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// alEntrar: lo que hace cada mensaje de la clienta antes del turno del bot.
func (b *Bot) alEntrar(ctx context.Context, conv *store.Conversation, in *Incoming) {
	if e, err := b.store.PausarYVer(ctx, conv.ID); err != nil {
		log.Printf("bot: pausar seguimiento %d: %v", conv.ID, err)
	} else if e != nil {
		b.respondioAlSeguimiento(ctx, conv, e)
	}
	_ = b.store.SalirDeMasAdelante(ctx, conv.ID) // volvió antes de tiempo: la etapa la vuelve a decidir el bot
	if in.FromAd || in.AdID != "" {
		canal := in.Canal
		if canal == "" {
			canal = "whatsapp"
		}
		if err := b.store.AnotarOrigen(ctx, conv.ID, store.Origen{Canal: canal, AdID: in.AdID, Titular: in.AdTitle, CtwaClid: in.CtwaClid}); err != nil {
			log.Printf("bot: origen %d: %v", conv.ID, err)
		}
	}
}

// respondioAlSeguimiento: contestó a un paso que sí salió. Con tarea_al_responder, una tarea para el equipo (en los
// flujos de Kommo de Luis, la clienta pasaba a «Negociación» para que una persona la atienda).
func (b *Bot) respondioAlSeguimiento(ctx context.Context, conv *store.Conversation, e *store.SeguimientoEnCurso) {
	q, err := b.store.GetSecuencia(ctx, e.SecuenciaID)
	if err != nil {
		return
	}
	paso := ""
	if i := e.Indice() - 1; i >= 0 && i < len(q.Pasos) {
		paso = q.Pasos[i].Tipo
	}
	b.hito(ctx, conv, fmt.Sprintf("💬 Respondió al seguimiento «%s» (paso %d: %s)", q.Nombre, e.Indice(), paso))
	if !q.TareaAlResponder {
		return
	}
	custID, err := b.store.CustomerDeConversacion(ctx, conv.ID)
	if err != nil {
		return
	}
	titulo := fmt.Sprintf("Atender a %s: respondió al seguimiento (%s)", firstNonEmpty(primerNombre(b.customerName(conv)), "la clienta"),
		firstNonEmpty(paso, "paso "+fmt.Sprint(e.Indice())))
	if p, err := b.store.GetProductByCode(ctx, e.ProductCode); err == nil {
		titulo += " · " + p.Name
	}
	ahora := time.Now()
	if err := b.store.AddTarea(ctx, &store.Tarea{CustomerID: custID, Titulo: titulo, Vence: &ahora, CreadaPor: "bot"}); err != nil {
		log.Printf("bot: tarea de seguimiento %d: %v", conv.ID, err)
		return
	}
	b.Notify("tareas")
}

// armarSeguimiento: al terminar el turno, si la conversación quedó esperando a la clienta con un modelo en foco.
func (b *Bot) armarSeguimiento(ctx context.Context, conv *store.Conversation) {
	fresca, err := b.store.GetConversation(ctx, conv.ID)
	if err != nil || fresca.BotPaused {
		return
	}
	switch fresca.State {
	case stPayment, stLocation, stHumanAsked:
		return
	}
	var cc convContext
	_ = b.decodeCtx(fresca.Context, &cc)
	m := leerFichaMem(cc.Memoria)
	if m.Pedido != nil && m.Pedido.Confirmado {
		return
	}
	codigo := m.Producto
	if codigo == "" && m.Pedido != nil {
		codigo = m.Pedido.Codigo
	}
	if codigo == "" {
		return
	}
	p, err := b.store.GetProductByCode(ctx, codigo)
	if err != nil {
		return
	}
	q, err := b.store.SecuenciaPara(ctx, p.Code, p.Category)
	if err != nil || q == nil {
		return
	}
	if err := b.store.ArmarSeguimiento(ctx, conv.ID, q, p.Code, time.Now()); err != nil {
		log.Printf("bot: armar seguimiento %d: %v", conv.ID, err)
	}
}

// ---------------------------------------------------------------------------
// Horario

// horarioSeguimiento: «08:00-21:00» (hora de Lima) → minutos desde medianoche. Si no se entiende, 8–21.
func horarioSeguimiento(v string) (ini, fin int) {
	ini, fin = 8*60, 21*60
	var h1, m1, h2, m2 int
	if n, _ := fmt.Sscanf(strings.TrimSpace(v), "%d:%d-%d:%d", &h1, &m1, &h2, &m2); n == 4 &&
		h1 >= 0 && h1 < 24 && h2 >= 0 && h2 <= 24 && m1 >= 0 && m1 < 60 && m2 >= 0 && m2 < 60 && h1*60+m1 < h2*60+m2 {
		ini, fin = h1*60+m1, h2*60+m2
	}
	return
}

// siguienteApertura: si t cae fuera del horario, la próxima hora de apertura (Lima); si no, t.
func siguienteApertura(t time.Time, ini, fin int) time.Time {
	l := t.In(store.Lima)
	min := l.Hour()*60 + l.Minute()
	base := time.Date(l.Year(), l.Month(), l.Day(), 0, 0, 0, 0, store.Lima)
	switch {
	case min < ini:
		return base.Add(time.Duration(ini) * time.Minute)
	case min >= fin:
		return base.AddDate(0, 0, 1).Add(time.Duration(ini) * time.Minute)
	}
	return t
}

// ---------------------------------------------------------------------------
// Contenido de un paso

// PasoListo: lo que saldría en un paso (texto y, si hay, imagen) o por qué se salta.
type PasoListo struct {
	Texto    string
	MediaURL string
	Omitido  bool
	Motivo   string
}

// ContenidoPaso arma el mensaje de un paso para el modelo `codigo`. Es puro respecto del envío: lo usan el trabajador y
// la vista previa del panel (con el store de la empresa que mira). mediaBase convierte «/media/…» en URL absoluta.
func ContenidoPaso(ctx context.Context, st *store.Store, conoc *store.Conocimiento, mediaBase string, paso store.Paso, codigo, cliente string) PasoListo {
	var prod *store.Product
	if codigo != "" {
		prod, _ = st.GetProductByCode(ctx, codigo)
	}
	nombre := strings.Fields(strings.TrimSpace(cliente))
	reemplazar := func(t string) string {
		n := ""
		if len(nombre) > 0 && !strings.HasPrefix(nombre[0], "+") {
			n = nombre[0]
		}
		t = strings.ReplaceAll(t, "{nombre}", n)
		if prod != nil {
			t = strings.ReplaceAll(t, "{modelo}", prod.Name)
		}
		t = strings.ReplaceAll(strings.ReplaceAll(t, " ,", ","), "  ", " ")
		return strings.TrimSpace(t)
	}
	abs := func(u string) string {
		if strings.HasPrefix(u, "/") {
			return mediaBase + u
		}
		return u
	}
	switch paso.Tipo {
	case "emoji", "texto":
		l := PasoListo{Texto: reemplazar(paso.Texto), MediaURL: abs(paso.MediaURL)}
		if l.Texto == "" && l.MediaURL == "" {
			return PasoListo{Omitido: true, Motivo: "paso vacío"}
		}
		return l
	case "beneficio", "testimonio", "tip":
		if prod == nil {
			return PasoListo{Omitido: true, Motivo: "no hay modelo en foco"}
		}
		c, _ := st.ContenidoDe(ctx, prod.ID, paso.Tipo)
		if c == nil {
			return PasoListo{Omitido: true, Motivo: "el " + prod.Name + " no tiene " + paso.Tipo + " cargado"}
		}
		// «{contenido}» en el texto del paso dice dónde va el contenido del modelo, para cerrar con una pregunta como en
		// los flujos de Kommo («…revisa la imagen. {contenido} ¿Qué opinas?»). Sin el marcador, el texto va antes.
		texto := reemplazar(paso.Texto)
		if strings.Contains(texto, "{contenido}") {
			texto = strings.TrimSpace(strings.ReplaceAll(texto, "{contenido}", c.Texto))
		} else {
			texto = strings.TrimSpace(texto + "\n\n" + c.Texto)
		}
		media := abs(c.MediaURL)
		if media != "" && !esImagen(media) { // un video o una página: va como enlace en el texto
			texto, media = strings.TrimSpace(texto+"\n"+media), ""
		}
		if texto == "" && media == "" {
			return PasoListo{Omitido: true, Motivo: "contenido vacío"}
		}
		return PasoListo{Texto: texto, MediaURL: media}
	case "oferta":
		if conoc == nil {
			return PasoListo{Omitido: true, Motivo: "no hay promos vigentes"}
		}
		var promo *store.PromoConocimiento
		for i := range conoc.Promos {
			if conoc.Promos[i].ID == paso.PromoID {
				promo = &conoc.Promos[i]
			}
		}
		if promo == nil {
			return PasoListo{Omitido: true, Motivo: "la promo elegida no está vigente"}
		}
		if prod != nil && !store.CubrePromo(*promo, prod) {
			return PasoListo{Omitido: true, Motivo: "la promo no cubre el " + prod.Name}
		}
		partes := []string{}
		if t := reemplazar(paso.Texto); t != "" {
			partes = append(partes, t)
		} else if promo.MensajeApertura != "" {
			partes = append(partes, reemplazar(promo.MensajeApertura))
		} else {
			partes = append(partes, "Tengo algo especial para ti 🎁")
		}
		if prod != nil {
			if precio, p := conoc.PrecioConPromo(prod); p != nil && p.ID == promo.ID && precio < prod.Price {
				partes = append(partes, fmt.Sprintf("Con la promo «%s», el %s queda en *S/ %.2f* (antes S/ %.2f).", promo.Titulo, prod.Name, precio, prod.Price))
			} else {
				partes = append(partes, fmt.Sprintf("Con la promo «%s» para el %s.", promo.Titulo, prod.Name))
			}
		} else {
			partes = append(partes, "Promo «"+promo.Titulo+"».")
		}
		if promo.VigenteHasta != "" {
			if t, err := time.Parse("2006-01-02", promo.VigenteHasta); err == nil {
				partes = append(partes, fmt.Sprintf("Vale hasta el %d de %s.", t.Day(), mesesLargos[t.Month()-1]))
			}
		}
		if promo.Condiciones != "" {
			partes = append(partes, promo.Condiciones)
		}
		return PasoListo{Texto: strings.Join(partes, " ")}
	}
	return PasoListo{Omitido: true, Motivo: "tipo de paso desconocido"}
}

func esImagen(u string) bool {
	l := strings.ToLower(strings.SplitN(u, "?", 2)[0])
	for _, ext := range []string{".jpg", ".jpeg", ".png", ".webp", ".gif"} {
		if strings.HasSuffix(l, ext) {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------------------
// Trabajador

// TickSeguimiento manda los pasos que ya tocan (lo llama cmd/server cada minuto). Devuelve cuántos salieron.
func (b *Bot) TickSeguimiento(ctx context.Context, ahora time.Time) int {
	// Un solo trabajador a la vez: «Probar» del editor lo corre fuera del minuto y no debe mandar dos veces el mismo paso.
	tickSeguimientoMu.Lock()
	defer tickSeguimientoMu.Unlock()
	vencidos, err := b.store.SeguimientosVencidos(ctx, ahora, 50)
	if err != nil {
		log.Printf("seguimiento: %v", err)
		return 0
	}
	if len(vencidos) == 0 {
		return 0
	}
	ini, fin := horarioSeguimiento(b.store.Setting(ctx, store.SettingSeguimientoHorario, "08:00-21:00"))
	if ap := siguienteApertura(ahora, ini, fin); !ap.Equal(ahora) {
		quedan := vencidos[:0]
		for _, e := range vencidos {
			if e.Prueba { // «Probar» del editor: sale ya, a cualquier hora
				quedan = append(quedan, e)
				continue
			}
			_ = b.store.PosponerSeguimiento(ctx, e.ConversationID, ap)
		}
		if vencidos = quedan; len(vencidos) == 0 {
			return 0
		}
	}
	n := 0
	for _, e := range vencidos {
		if b.pasoSeguimiento(ctx, e, ahora, ini) {
			n++
		}
	}
	if n > 0 {
		b.Notify("seguimiento")
	}
	return n
}

// pasoSeguimiento ejecuta el próximo paso de una conversación. true = se envió algo.
func (b *Bot) pasoSeguimiento(ctx context.Context, e *store.SeguimientoEnCurso, ahora time.Time, ini int) bool {
	conv, err := b.store.GetConversation(ctx, e.ConversationID)
	if err != nil {
		return false
	}
	var cc convContext
	_ = b.decodeCtx(conv.Context, &cc)
	m := leerFichaMem(cc.Memoria)
	if conv.BotPaused || conv.State == stPayment || conv.State == stLocation || conv.State == stHumanAsked ||
		(m.Pedido != nil && m.Pedido.Confirmado) || b.store.Setting(ctx, "bot_enabled", "true") != "true" {
		_ = b.store.DetenerSeguimiento(ctx, conv.ID)
		return false
	}
	q, err := b.store.GetSecuencia(ctx, e.SecuenciaID)
	if err != nil || (!q.Activa && !e.Prueba) {
		return false
	}
	if q.Grafo != nil && (e.Nodo != "" || e.Prueba) {
		return b.pasoFlujo(ctx, conv, q, e, ahora, ini)
	}
	idx := e.Indice()
	if idx >= len(q.Pasos) {
		_, _ = b.store.AvanzarSeguimiento(ctx, conv.ID, q, idx, ahora)
		return false
	}
	paso := q.Pasos[idx]
	saltar := func(motivo string) {
		_ = b.store.AnotarEnvio(ctx, conv.ID, q.ID, idx+1, 0, paso.Tipo, "omitido", motivo)
		b.hito(ctx, conv, fmt.Sprintf("⏭️ Seguimiento «%s», paso %d (%s) no salió: %s", q.Nombre, idx+1, paso.Tipo, motivo))
		b.terminarPaso(ctx, conv, q, idx, ahora)
	}
	res, motivo, numeroID := b.emitirPaso(ctx, conv, q, paso, e.ProductCode, ahora, ini, false)
	switch res {
	case emitidoOmitido:
		saltar(motivo)
		return false
	case emitidoEnviado:
	default: // pospuesto (tope) o error de envío: se reintenta
		return false
	}
	_ = b.store.AnotarEnvio(ctx, conv.ID, q.ID, idx+1, numeroID, paso.Tipo, "enviado", "")
	b.hito(ctx, conv, fmt.Sprintf("🔁 Seguimiento «%s», paso %d de %d (%s)", q.Nombre, idx+1, len(q.Pasos), paso.Tipo))
	b.terminarPaso(ctx, conv, q, idx, ahora)
	return true
}

// Resultado de emitirPaso.
const (
	emitidoEnviado   = "enviado"
	emitidoOmitido   = "omitido"
	emitidoPospuesto = "pospuesto"
	emitidoError     = "error"
)

// emitirPaso manda UN mensaje de seguimiento (paso de la lista o nodo de mensaje de un flujo) respetando la ventana de 24
// h, el número de salida y el tope diario. Devuelve el resultado, el motivo (si se omitió) y el número por el que salió.
// En una prueba del editor (`prueba`) no mira ventana ni tope: la conversación es el número de prueba de la tienda.
func (b *Bot) emitirPaso(ctx context.Context, conv *store.Conversation, q *store.Secuencia, paso store.Paso, codigo string,
	ahora time.Time, ini int, prueba bool) (string, string, int64) {
	canal := b.canalDe(conv)
	numeroID := int64(0)
	plantilla := ""
	if !prueba && b.FueraDeVentana(ctx, conv.ID, ahora) {
		switch q.FueraDeVentana {
		case "enviar":
			if canal == CanalCloud {
				return emitidoOmitido, "la API oficial no deja escribir libre pasadas 24 h; usa una plantilla", 0
			}
		case "plantilla":
			if canal != CanalCloud || b.cloud == nil || paso.Plantilla == "" {
				return emitidoOmitido, "pasaron más de 24 h y el paso no tiene plantilla de la API oficial", 0
			}
			plantilla = paso.Plantilla
		case "numero_secundario":
			n := b.numeroSalida(ctx)
			if n == nil {
				return emitidoOmitido, "pasaron más de 24 h y no hay número secundario con cupo", 0
			}
			numeroID, canal = n.ID, CanalSecundario(n.ID)
		default:
			return emitidoOmitido, "pasaron más de 24 h desde su último mensaje", 0
		}
	}
	if numeroID == 0 && strings.HasPrefix(canal, "n:") {
		fmt.Sscanf(strings.TrimPrefix(canal, "n:"), "%d", &numeroID)
	}
	if tope := b.topeNumero(ctx, numeroID); !prueba && b.store.EnviadosHoy(ctx, numeroID) >= tope {
		manana := siguienteApertura(time.Date(ahora.In(store.Lima).Year(), ahora.In(store.Lima).Month(), ahora.In(store.Lima).Day(),
			23, 59, 0, 0, store.Lima), ini, 24*60)
		_ = b.store.PosponerSeguimiento(ctx, conv.ID, manana.Add(time.Minute))
		log.Printf("seguimiento: tope diario de %d alcanzado (número %d); la conversación %d sigue mañana", tope, numeroID, conv.ID)
		return emitidoPospuesto, "", 0
	}

	listo := ContenidoPaso(ctx, b.store, b.conocimiento(ctx), b.cfg.MediaBaseURL, paso, codigo, b.customerName(conv))
	if listo.Omitido {
		return emitidoOmitido, listo.Motivo, 0
	}
	if plantilla != "" {
		params := []string{firstNonEmpty(primerNombre(b.customerName(conv)), "hola")}
		if err := b.enviarPlantilla(ctx, conv, plantilla, params, listo.Texto); err != nil {
			return emitidoOmitido, "la plantilla «" + plantilla + "» falló: " + err.Error(), 0
		}
		return emitidoEnviado, "", numeroID
	}
	msg := &store.Message{Kind: "text", Body: listo.Texto, Author: "bot"}
	j := outJob{text: listo.Texto, canal: canal}
	if listo.MediaURL != "" {
		msg.Kind, msg.Media = "image", strings.TrimPrefix(listo.MediaURL, b.cfg.MediaBaseURL)
		j = outJob{image: listo.MediaURL, caption: listo.Texto, canal: canal}
	}
	if err := b.queueMessage(ctx, conv, msg, j); err != nil {
		log.Printf("seguimiento: enviar %d: %v", conv.ID, err)
		return emitidoError, err.Error(), 0
	}
	return emitidoEnviado, "", numeroID
}

// terminarPaso programa el siguiente; con el último, si la secuencia lo pide, la clienta pasa a «perdida».
func (b *Bot) terminarPaso(ctx context.Context, conv *store.Conversation, q *store.Secuencia, idx int, ahora time.Time) {
	fin, err := b.store.AvanzarSeguimiento(ctx, conv.ID, q, idx, ahora)
	if err != nil || !fin || !q.MarcarPerdida {
		return
	}
	if err := b.store.EtapaPorSeguimiento(ctx, conv.ID, "perdida"); err != nil {
		log.Printf("seguimiento: marcar perdida %d: %v", conv.ID, err)
	}
}

// numeroSalida: un número secundario de uso salida/ambos que aún tiene cupo hoy.
func (b *Bot) numeroSalida(ctx context.Context) *store.NumeroWhatsApp {
	ns, err := b.store.ListarNumeros(ctx)
	if err != nil {
		return nil
	}
	for _, n := range ns {
		if n.Canal == "evolution" && (n.Uso == "salida" || n.Uso == "ambos") && n.EnviadosHoy < n.TopeDiario && b.clienteSecundario(n.ID) != nil {
			return n
		}
	}
	return nil
}

func (b *Bot) topeNumero(ctx context.Context, id int64) int {
	if id > 0 {
		if n, err := b.store.GetNumero(ctx, id); err == nil {
			return n.TopeDiario
		}
		return 0
	}
	var t int
	if _, err := fmt.Sscan(b.store.Setting(ctx, store.SettingTopePrincipal, "25"), &t); err != nil || t < 0 {
		t = 25
	}
	return t
}

// mensajeroAvisos: por dónde avisar a la tienda (clienta caliente). El principal de evolution escribe a cualquiera; si
// no hay, un secundario de salida. La API oficial no: el número de la tienda nunca le escribió (ventana cerrada).
func (b *Bot) mensajeroAvisos(ctx context.Context) Mensajero {
	if b.evo != nil {
		return b.evo
	}
	if n := b.numeroSalida(ctx); n != nil {
		if c := b.clienteSecundario(n.ID); c != nil {
			return c
		}
	}
	return nil
}

func primerNombre(n string) string {
	f := strings.Fields(strings.TrimSpace(n))
	if len(f) == 0 || strings.HasPrefix(f[0], "+") {
		return ""
	}
	return f[0]
}
