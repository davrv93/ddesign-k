package bot

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
	"github.com/davrv93/ddesign-k/backend/internal/kommo"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// CRM recibe, al final de cada turno, lo que el bot sabe de la conversación (internal/kommo lo lleva a Kommo).
// Encolar no debe bloquear: el envío a Kommo va en segundo plano y nunca retrasa ni cambia la respuesta a la clienta.
type CRM interface {
	Encolar(kommo.Evento)
}

// turno junta lo que pasa mientras se atiende un mensaje (hitos, intención, fotos enviadas, pedido tocado). Al
// terminar, Handle manda un solo evento con todo y con el estado final de la conversación.
type turno struct {
	hitos     []string
	intencion string
	mostrados []string
	pedido    int64 // pedido tocado en el turno (algunos pasos vacían el contexto: confirmar + dirección, comprobante)
	pagado    bool
	desdeMsg  int64 // primer mensaje del turno (el de la clienta): desde ahí va la transcripción
}

type claveTurno struct{}

func turnoDe(ctx context.Context) *turno {
	t, _ := ctx.Value(claveTurno{}).(*turno)
	return t
}

// hito anota algo legible para la asesora («Pidió hablar con una asesora»). Fuera de un turno (p. ej. un cambio
// desde el panel) se manda al momento.
func (b *Bot) hito(ctx context.Context, conv *store.Conversation, texto string) {
	if b.CRM == nil {
		return
	}
	if t := turnoDe(ctx); t != nil {
		t.hitos = append(t.hitos, texto)
		return
	}
	b.crmEnviar(ctx, conv, &turno{hitos: []string{texto}}, nil)
}

func (b *Bot) crmPedido(ctx context.Context, orderID int64) {
	if t := turnoDe(ctx); t != nil && orderID > 0 {
		t.pedido = orderID
	}
}

func (b *Bot) crmPagado(ctx context.Context, orderID int64) {
	if t := turnoDe(ctx); t != nil {
		t.pagado = true
		if orderID > 0 {
			t.pedido = orderID
		}
	}
}

// crmRespuesta guarda la intención comercial y las prendas que el agente mandó a mostrar.
func (b *Bot) crmRespuesta(ctx context.Context, r *agente.Reply) {
	t := turnoDe(ctx)
	if t == nil || r == nil {
		return
	}
	if r.Comercial != nil && r.Comercial.Intent != "" {
		t.intencion = r.Comercial.Intent
	}
}

func (b *Bot) crmMostrado(ctx context.Context, codigo string) {
	if t := turnoDe(ctx); t != nil && codigo != "" && !contiene(t.mostrados, codigo) {
		t.mostrados = append(t.mostrados, codigo)
	}
}

func contiene(xs []string, x string) bool {
	for _, y := range xs {
		if strings.EqualFold(y, x) {
			return true
		}
	}
	return false
}

// conTurno abre el turno de un mensaje entrante. Devuelve el contexto con el turno y la función que lo cierra.
func (b *Bot) conTurno(ctx context.Context) (context.Context, *turno) {
	if b.CRM == nil {
		return ctx, nil
	}
	t := &turno{}
	return context.WithValue(ctx, claveTurno{}, t), t
}

// cerrarTurno manda el evento del turno con el estado final de la conversación.
func (b *Bot) cerrarTurno(ctx context.Context, convID int64, t *turno) {
	if b.CRM == nil || t == nil {
		return
	}
	conv, err := b.store.GetConversation(ctx, convID)
	if err != nil {
		return
	}
	b.crmEnviar(ctx, conv, t, nil)
}

// PedidoCambio: el pedido cambió de columna en el tablero (preparando, enviado, cancelado…). Para Kommo es el paso a
// «Venta pagada» o a «Venta perdida».
func (b *Bot) PedidoCambio(ctx context.Context, o *store.Order) {
	if b.CRM == nil || o == nil || o.ConversationID == 0 {
		return
	}
	conv, err := b.store.GetConversation(ctx, o.ConversationID)
	if err != nil {
		return
	}
	b.crmEnviar(ctx, conv, &turno{pedido: o.ID, hitos: []string{"🗂️ En el tablero de kddesign el pedido #" +
		fmt.Sprint(o.ID) + " pasó a «" + o.Status + "»"}}, o)
}

// EventoDeConversacion arma el evento de una conversación tal como está ahora, para volcar al CRM lo ya capturado
// (kommo-seed --desde-base): etapa y memoria del contexto, su pedido más reciente si el contexto ya no lo trae, y los
// últimos 30 mensajes como transcripción (solo se mandan si KOMMO_SYNC_TRANSCRIPT=1).
func EventoDeConversacion(ctx context.Context, st *store.Store, conv *store.Conversation) kommo.Evento {
	t := &turno{desdeMsg: 1}
	var cc convContext
	_ = json.Unmarshal([]byte(conv.Context), &cc)
	var o *store.Order
	if cc.OrderID == 0 {
		if orders, err := st.ListOrders(ctx, conv.CustomerID); err == nil {
			for _, x := range orders {
				if len(x.Items) > 0 && (o == nil || x.ID > o.ID) {
					o = x
				}
			}
		}
	}
	ev := EventoDe(ctx, st, conv, t, o)
	n := len(ev.Turno)
	ev.Hitos = append(ev.Hitos, fmt.Sprintf("📥 Importado de kddesign: conversación del %s (%d mensajes recientes)",
		conv.LastMessageAt.In(limaTZ).Format("02-01-2006 15:04"), n))
	return ev
}

func (b *Bot) crmEnviar(ctx context.Context, conv *store.Conversation, t *turno, o *store.Order) {
	b.CRM.Encolar(EventoDe(ctx, b.store, conv, t, o))
}

// EventoDe arma el evento con el estado de la conversación en la SQLite: etapa, memoria, anuncio, pedido y el último
// intercambio.
func EventoDe(ctx context.Context, st *store.Store, conv *store.Conversation, t *turno, o *store.Order) kommo.Evento {
	var cc convContext
	_ = json.Unmarshal([]byte(conv.Context), &cc)
	ev := kommo.Evento{Canal: "whatsapp", Clave: fmt.Sprintf("wa:%d", conv.ID), ConversationID: conv.ID, Sesion: cc.Desde,
		Etapa: cc.Etapa, Memoria: cc.Memoria, Anuncio: cc.Anuncio, AnuncioTitulo: cc.AnuncioTitle,
		Intencion: t.intencion, Mostrados: t.mostrados, Hitos: t.hitos, Pagado: t.pagado}
	if conv.Customer != nil {
		ev.Nombre = strings.TrimSpace(conv.Customer.Name)
		ev.Telefono = soloDigitos(conv.Customer.Phone)
		if ev.Telefono == "" && strings.HasSuffix(conv.Customer.JID, "@s.whatsapp.net") {
			ev.Telefono = soloDigitos(strings.TrimSuffix(conv.Customer.JID, "@s.whatsapp.net"))
		}
	}
	if o == nil {
		id := cc.OrderID
		if id == 0 {
			id = t.pedido
		}
		if id > 0 {
			o, _ = st.GetOrder(ctx, id)
		}
	}
	if o != nil {
		p := &kommo.Pedido{ID: o.ID, Estado: o.Status, Total: o.Total}
		if len(o.Items) > 0 {
			it := o.Items[0]
			p.Codigo, p.Nombre, p.Talla, p.Cantidad = it.ProductCode, it.ProductName, it.Size, it.Qty
		}
		ev.Pedido = p
		if strings.Contains(o.Notes, "Comprobante de pago recibido") {
			ev.Pagado = true
		}
	}
	if t.desdeMsg > 0 {
		if msgs, err := st.ListMessages(ctx, conv.ID, 30); err == nil {
			for _, m := range msgs {
				if m.ID < t.desdeMsg || (m.Kind != "text" && m.Kind != "image") {
					continue
				}
				texto := strings.TrimSpace(m.Body)
				if m.Kind == "image" && m.Direction == "in" {
					texto = strings.TrimSpace("[foto] " + texto)
				}
				if texto == "" {
					continue
				}
				rol := m.Author
				if m.Direction == "in" {
					rol = "cliente"
				}
				ev.Turno = append(ev.Turno, kommo.Linea{Rol: rol, Texto: texto})
			}
		}
	}
	return ev
}

var limaTZ = time.FixedZone("America/Lima", -5*3600)

func soloDigitos(s string) string {
	return strings.Map(func(r rune) rune {
		if r >= '0' && r <= '9' {
			return r
		}
		return -1
	}, s)
}
