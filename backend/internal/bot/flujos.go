package bot

// Motor de los flujos visuales de seguimiento (20-10-2026). El grafo vive en la secuencia (store/flujos.go); el estado de
// cada conversación es el nodo que toca (seguimiento_en_curso.nodo) y cuándo toca (proximo_en). TickSeguimiento llama a
// pasoFlujo con las conversaciones vencidas; pasoFlujo recorre nodos hasta llegar a una espera, a un fin o al tope de
// nodos por minuto, mandando los mensajes con las mismas reglas que la lista de pasos (emitirPaso: ventana de 24 h,
// número de salida, tope diario; el horario lo mira TickSeguimiento).
//
// «Probar» del editor (store.ArmarPrueba): corre el flujo en la conversación del número de prueba con las esperas
// reducidas, sin horario, tope ni ventana, aunque la secuencia esté apagada; los envíos se anotan como «prueba» (no
// cuentan para las cifras ni para el tope).

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"sync"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/store"
)

var tickSeguimientoMu sync.Mutex

const (
	maxNodosPorTick = 30 // un flujo sin esperas entre sus mensajes no los manda todos de golpe
	maxSaltosFlujo  = 3  // «ir a otro flujo» encadenados en un mismo minuto
)

// pasoFlujo ejecuta el flujo de una conversación desde su nodo actual. true = salió al menos un mensaje.
func (b *Bot) pasoFlujo(ctx context.Context, conv *store.Conversation, q *store.Secuencia, e *store.SeguimientoEnCurso,
	ahora time.Time, ini int) bool {
	sec := q
	id := e.Nodo
	if id == "" {
		id = sec.Grafo.Inicio()
	}
	enviados := e.Indice() // mensajes que ya salieron en esta corrida (la columna del tablero)
	salio, saltos := false, 0
	etiqueta := "Seguimiento"
	if e.Prueba {
		etiqueta = "🧪 Prueba del flujo"
	}
	for i := 0; i < maxNodosPorTick; i++ {
		n := sec.Grafo.Nodo(id)
		if n == nil {
			b.finFlujo(ctx, conv, sec, e)
			return salio
		}
		next := func(salida string) string { return sec.Grafo.Siguiente(n.ID, salida) }
		switch n.Tipo {
		case "inicio":
			id = next(store.SalidaSiguiente)

		case "esperar":
			if i == 0 && n.ID == e.Nodo { // la espera ya se cumplió: sigue
				id = next(store.SalidaSiguiente)
				break
			}
			min := n.Minutos
			if e.Prueba && e.EscalaMin > 0 {
				min = e.EscalaMin
			}
			if err := b.store.PonerNodo(ctx, conv.ID, sec.ID, n.ID, enviados, ahora.Add(time.Duration(min)*time.Minute)); err != nil {
				log.Printf("flujo: poner nodo %d: %v", conv.ID, err)
			}
			return salio

		case "mensaje":
			paso := n.PasoDe()
			num := sec.Grafo.IndiceMensaje(n.ID) + 1
			res, motivo, numeroID := b.emitirPaso(ctx, conv, sec, paso, e.ProductCode, ahora, ini, e.Prueba)
			switch res {
			case emitidoPospuesto: // tope del día: emitirPaso ya dejó la hora; queda en este nodo
				_ = b.store.PonerNodo(ctx, conv.ID, sec.ID, n.ID, enviados, b.proximoDe(ctx, conv.ID, ahora))
				return salio
			case emitidoError: // el envío falló: se reintenta en el próximo minuto
				_ = b.store.PonerNodo(ctx, conv.ID, sec.ID, n.ID, enviados, ahora.Add(time.Minute))
				return salio
			case emitidoOmitido:
				_ = b.store.AnotarEnvio(ctx, conv.ID, sec.ID, num, 0, paso.Tipo, "omitido", motivo)
				b.hito(ctx, conv, fmt.Sprintf("⏭️ %s «%s», mensaje %d (%s) no salió: %s", etiqueta, sec.Nombre, num, paso.Tipo, motivo))
			default:
				resultado := "enviado"
				if e.Prueba {
					resultado = "prueba"
				}
				enviados++
				salio = true
				_ = b.store.AnotarEnvio(ctx, conv.ID, sec.ID, num, numeroID, paso.Tipo, resultado, "")
				b.hito(ctx, conv, fmt.Sprintf("🔁 %s «%s», mensaje %d (%s)", etiqueta, sec.Nombre, num, paso.Tipo))
			}
			id = next(store.SalidaSiguiente)

		case "condicion":
			si := b.condicionFlujo(ctx, conv, n, e.ProductCode)
			if si {
				id = next(store.SalidaSi)
			} else {
				id = next(store.SalidaNo)
			}

		case "tarea":
			b.tareaFlujo(ctx, conv, n, e.ProductCode, e.Prueba)
			id = next(store.SalidaSiguiente)

		case "etapa":
			if e.Prueba {
				b.hito(ctx, conv, fmt.Sprintf("%s: aquí la clienta pasaría a la etapa «%s» (en la prueba no se cambia)", etiqueta, store.EtapaLabel[n.Etapa]))
			} else if err := b.store.EtapaPorSeguimiento(ctx, conv.ID, n.Etapa); err != nil {
				log.Printf("flujo: etapa %d: %v", conv.ID, err)
			}
			id = next(store.SalidaSiguiente)

		case "ir_a":
			dest, err := b.store.GetSecuencia(ctx, n.Destino)
			switch {
			case err != nil || dest.Grafo == nil:
				b.hito(ctx, conv, fmt.Sprintf("⏭️ %s «%s»: el flujo destino ya no existe o no es un flujo visual", etiqueta, sec.Nombre))
				id = next(store.SalidaSiguiente)
			case !dest.Activa && !e.Prueba:
				b.hito(ctx, conv, fmt.Sprintf("⏭️ %s «%s»: el flujo «%s» está apagado", etiqueta, sec.Nombre, dest.Nombre))
				id = next(store.SalidaSiguiente)
			case saltos >= maxSaltosFlujo:
				_ = b.store.PonerNodo(ctx, conv.ID, sec.ID, n.ID, enviados, ahora.Add(time.Minute))
				return salio
			default:
				saltos++
				b.hito(ctx, conv, fmt.Sprintf("↪️ %s: pasa del flujo «%s» al flujo «%s»", etiqueta, sec.Nombre, dest.Nombre))
				sec, id, enviados = dest, dest.Grafo.Inicio(), 0
			}

		case "fin":
			b.finFlujo(ctx, conv, sec, e)
			return salio
		}
		if id == "" { // una salida sin conectar termina el flujo
			b.finFlujo(ctx, conv, sec, e)
			return salio
		}
	}
	// Tope de nodos por minuto: sigue en el próximo.
	_ = b.store.PonerNodo(ctx, conv.ID, sec.ID, id, enviados, ahora.Add(time.Minute))
	return salio
}

// proximoDe: la hora que dejó PosponerSeguimiento (o dentro de un minuto si no se puede leer).
func (b *Bot) proximoDe(ctx context.Context, convID int64, ahora time.Time) time.Time {
	if e, err := b.store.SeguimientoDe(ctx, convID); err == nil && e != nil && e.ProximoEn != nil {
		return *e.ProximoEn
	}
	return ahora.Add(time.Minute)
}

func (b *Bot) finFlujo(ctx context.Context, conv *store.Conversation, sec *store.Secuencia, e *store.SeguimientoEnCurso) {
	if err := b.store.TerminarFlujo(ctx, conv.ID, sec.ID); err != nil {
		log.Printf("flujo: terminar %d: %v", conv.ID, err)
	}
	if e.Prueba {
		b.hito(ctx, conv, fmt.Sprintf("🧪 Prueba del flujo «%s» terminada", sec.Nombre))
		b.Notify("seguimiento")
		return
	}
	if sec.MarcarPerdida {
		if err := b.store.EtapaPorSeguimiento(ctx, conv.ID, "perdida"); err != nil {
			log.Printf("flujo: marcar perdida %d: %v", conv.ID, err)
		}
	}
}

// condicionFlujo evalúa un nodo de condición: true = salida «sí».
func (b *Bot) condicionFlujo(ctx context.Context, conv *store.Conversation, n *store.Nodo, codigo string) bool {
	return EvaluarCondicion(ctx, b.store, conv, n, codigo)
}

// EvaluarCondicion es pura respecto del envío: la usan el motor y la simulación del editor.
func EvaluarCondicion(ctx context.Context, st *store.Store, conv *store.Conversation, n *store.Nodo, codigo string) bool {
	valor := strings.ToLower(strings.TrimSpace(n.Valor))
	switch n.Condicion {
	case "respondio":
		if conv == nil {
			return false
		}
		envio, ok := st.UltimoEnvioFlujo(ctx, conv.ID)
		entro, ok2 := st.UltimoEntrante(ctx, conv.ID)
		return ok && ok2 && entro.After(envio)
	case "temperatura":
		if conv == nil {
			return false
		}
		var cc convContext
		_ = json.Unmarshal([]byte(conv.Context), &cc)
		return strings.EqualFold(leerFichaMem(cc.Memoria).Temperatura, sinTilde(valor))
	case "categoria":
		p, err := st.GetProductByCode(ctx, codigo)
		return err == nil && normalizeCat(p.Category) == normalizeCat(valor)
	case "tiene_contenido":
		p, err := st.GetProductByCode(ctx, codigo)
		if err != nil {
			return false
		}
		c, _ := st.ContenidoDe(ctx, p.ID, valor)
		return c != nil && (strings.TrimSpace(c.Texto) != "" || c.MediaURL != "")
	}
	return false
}

func normalizeCat(c string) string {
	c = strings.ToLower(sinTilde(strings.TrimSpace(c)))
	return strings.TrimSuffix(strings.TrimSuffix(c, "s"), "e")
}

func sinTilde(s string) string {
	return strings.NewReplacer("á", "a", "é", "e", "í", "i", "ó", "o", "ú", "u").Replace(s)
}

// tareaFlujo crea la tarea de un nodo «tarea» ({nombre} y {modelo} se reemplazan).
func (b *Bot) tareaFlujo(ctx context.Context, conv *store.Conversation, n *store.Nodo, codigo string, prueba bool) {
	titulo := strings.ReplaceAll(n.Titulo, "{nombre}", firstNonEmpty(primerNombre(b.customerName(conv)), "la clienta"))
	if p, err := b.store.GetProductByCode(ctx, codigo); err == nil {
		titulo = strings.ReplaceAll(titulo, "{modelo}", p.Name)
	}
	titulo = strings.TrimSpace(strings.ReplaceAll(titulo, "{modelo}", "el modelo"))
	if prueba {
		titulo = "🧪 Prueba: " + titulo
	}
	custID, err := b.store.CustomerDeConversacion(ctx, conv.ID)
	if err != nil {
		return
	}
	ahora := time.Now()
	if err := b.store.AddTarea(ctx, &store.Tarea{CustomerID: custID, Titulo: titulo, Vence: &ahora, CreadaPor: "bot"}); err != nil {
		log.Printf("flujo: tarea %d: %v", conv.ID, err)
		return
	}
	b.Notify("tareas")
}
