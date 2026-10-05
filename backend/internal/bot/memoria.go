package bot

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"sort"
	"strings"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// Memoria de la conversación: la ficha que lleva el agente (agente/app/memoria.py) con lo que ya sabemos de
// la clienta (`sabemos`) y la pregunta que el bot dejó abierta (`pendiente`). El agente no guarda estado: la
// recibe en cada petición y la devuelve actualizada; el bot la guarda en convContext.Memoria, como la etapa.
//
// Go la trata casi como opaca (json.RawMessage, para no perder campos que el agente añada). Solo toca:
//   - `pendiente`, cuando la pregunta la hace el flujo fijo de Go (talla, confirmación, envío, dirección), y
//   - `sabemos.talla` y `producto`, cuando el pedido los fija.
//
// Y lee (sin tocarlos) `sabemos.cita`, `temperatura` y `temperatura_motivo`: cuando el agente agenda una cita
// para probarse, Go la deja en el tablero como consulta (registrarCita).

// pendientePorEstado: la pregunta que deja abierta cada estado del flujo de Go.
var pendientePorEstado = map[string]string{
	stSize:     "talla",
	stConfirm:  "confirmar",
	stPayment:  "lima_o_provincia", // confirmOrder pregunta «¿Lima o provincia?»; luego la lleva el agente
	stLocation: "direccion",
	stPhoto:    "foto",
}

// memEditar aplica fn sobre la memoria como mapa. Si no había memoria, empieza una vacía: el agente
// completa los campos que falten.
func memEditar(raw json.RawMessage, fn func(m map[string]any)) json.RawMessage {
	m := map[string]any{}
	if len(raw) > 0 {
		_ = json.Unmarshal(raw, &m)
		if m == nil {
			m = map[string]any{}
		}
	}
	fn(m)
	out, err := json.Marshal(m)
	if err != nil {
		return raw
	}
	return out
}

// memConPendiente fija la pregunta pendiente ("" la suelta) y la anota como ya hecha.
func memConPendiente(raw json.RawMessage, p string) json.RawMessage {
	return memEditar(raw, func(m map[string]any) {
		m["pendiente"] = p
		if p == "" {
			return
		}
		hechas, _ := m["preguntado"].([]any)
		for _, h := range hechas {
			if h == p {
				return
			}
		}
		m["preguntado"] = append(hechas, p)
	})
}

// memSabemos guarda un dato de la clienta (p. ej. la talla del pedido).
func memSabemos(raw json.RawMessage, campo, valor string) json.RawMessage {
	return memEditar(raw, func(m map[string]any) {
		sab, _ := m["sabemos"].(map[string]any)
		if sab == nil {
			sab = map[string]any{}
		}
		sab[campo] = valor
		m["sabemos"] = sab
	})
}

// memoriaActual: la memoria del contexto en curso o, si viene vacío (un convContext nuevo), la guardada.
// Sin esto, editar la memoria de un contexto recién creado la reemplazaría por una casi vacía.
func memoriaActual(conv *store.Conversation, cc *convContext) json.RawMessage {
	if len(cc.Memoria) > 0 {
		return cc.Memoria
	}
	var prev convContext
	_ = json.Unmarshal([]byte(conv.Context), &prev)
	return prev.Memoria
}

// fijarPendiente guarda la pregunta que el flujo de Go deja abierta sin cambiar de estado (p. ej. tras
// contestar una duda en pleno cierre se vuelve a esperar la talla o el SI).
func (b *Bot) fijarPendiente(ctx context.Context, conv *store.Conversation, cc *convContext, p string) {
	cc.Memoria = memConPendiente(memoriaActual(conv, cc), p)
	b.setState(ctx, conv, conv.State, *cc)
}

// perfil arma lo que sabemos de una clienta que vuelve: tallas y prendas de sus pedidos anteriores (los
// que llegaron a confirmarse), el más reciente primero. nil si nunca compró.
func (b *Bot) perfil(ctx context.Context, conv *store.Conversation, cc *convContext) *agente.Perfil {
	orders, err := b.store.ListOrders(ctx, conv.CustomerID)
	if err != nil || len(orders) == 0 {
		return nil
	}
	sort.SliceStable(orders, func(i, j int) bool { return orders[i].ID > orders[j].ID })
	p := &agente.Perfil{Nombre: b.customerName(conv)}
	vista := map[string]bool{}
	for _, o := range orders {
		if o.ID == cc.OrderID {
			continue // el pedido de hoy no es «de antes»
		}
		switch o.Status {
		case "confirmado", "preparando", "enviado", "entregado":
		default:
			continue
		}
		p.Pedidos++
		for _, it := range o.Items {
			if sz := strings.ToUpper(strings.TrimSpace(it.Size)); sz != "" && !vista["t"+sz] {
				vista["t"+sz] = true
				p.Tallas = append(p.Tallas, sz)
			}
			if it.ProductCode != "" && !vista["p"+it.ProductCode] {
				vista["p"+it.ProductCode] = true
				p.Productos = append(p.Productos, it.ProductCode)
			}
		}
	}
	if p.Pedidos == 0 {
		return nil
	}
	return p
}

// memVenta: lo que Go lee de la memoria para avisar a la asesora (cita y temperatura de la clienta).
type memVenta struct {
	Cita, Producto, Talla, Temperatura, Motivo string
	Envio, Ciudad                              string // «lima» | «provincia» y la ciudad, si ya lo dijo
}

func leerMemVenta(raw json.RawMessage) memVenta {
	var m struct {
		Producto    string         `json:"producto"`
		Temperatura string         `json:"temperatura"`
		Motivo      string         `json:"temperatura_motivo"`
		Sabemos     map[string]any `json:"sabemos"`
	}
	if len(raw) > 0 {
		_ = json.Unmarshal(raw, &m)
	}
	s := func(k string) string { v, _ := m.Sabemos[k].(string); return v }
	return memVenta{Cita: s("cita"), Producto: m.Producto, Talla: s("talla"), Temperatura: m.Temperatura, Motivo: m.Motivo,
		Envio: s("envio"), Ciudad: s("ciudad")}
}

var (
	tempTexto   = map[string]string{"frio": "fría", "tibio": "tibia", "caliente": "caliente"}
	diasCortos  = [...]string{"dom", "lun", "mar", "mié", "jue", "vie", "sáb"}
	mesesCortos = [...]string{"ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"}
)

// textoTemperatura: «clienta caliente: evento el 18-oct (en 6 días)», o "" si el agente no la mandó.
func textoTemperatura(v memVenta) string {
	t := tempTexto[v.Temperatura]
	if t == "" {
		return ""
	}
	if v.Motivo != "" {
		return "clienta " + t + ": " + v.Motivo
	}
	return "clienta " + t
}

// notaCita es la línea que lee la asesora en el pedido: «🗓️ Cita para probarse V42 talla M el sáb 11-oct 16:00 ·
// clienta caliente: evento el 18-oct (en 7 días)».
func notaCita(v memVenta) string {
	cuando := v.Cita
	if t, err := time.Parse("2006-01-02T15:04", v.Cita); err == nil {
		cuando = fmt.Sprintf("%s %d-%s %s", diasCortos[t.Weekday()], t.Day(), mesesCortos[t.Month()-1], t.Format("15:04"))
	}
	prenda := v.Producto
	if prenda == "" {
		prenda = "(sin prenda elegida)"
	}
	if v.Talla != "" {
		prenda += " talla " + v.Talla
	}
	nota := "🗓️ Cita para probarse " + prenda + " el " + cuando
	if t := textoTemperatura(v); t != "" {
		nota += " · " + t
	}
	return nota
}

// registrarCita: si la memoria que devolvió el agente trae una cita nueva (o cambiada) para probarse, se deja
// en el tablero un pedido en «consulta» con la prenda y una nota legible para la asesora, y se avisa al panel.
// No reserva stock: la cita no es una compra. Si la conversación ya tiene un pedido abierto (consulta o
// pendiente), se anota en ése; si no, se crea uno y queda como el pedido de la conversación, para que si luego
// compra se reutilice (draftFor).
func (b *Bot) registrarCita(ctx context.Context, conv *store.Conversation, cc *convContext, antes, despues json.RawMessage) {
	v := leerMemVenta(despues)
	if v.Cita == "" || v.Cita == leerMemVenta(antes).Cita {
		return
	}
	nota := notaCita(v)
	var items []store.OrderItem
	if v.Producto != "" {
		if p, err := b.store.GetProductByCode(ctx, v.Producto); err == nil {
			items = []store.OrderItem{{ProductID: &p.ID, ProductCode: p.Code, ProductName: p.Name, Image: p.Image, Size: v.Talla,
				Qty: 1, UnitPrice: p.Price}}
		}
	}
	if cc.OrderID > 0 {
		if o, err := b.store.GetOrder(ctx, cc.OrderID); err == nil && (o.Status == "consulta" || o.Status == "pendiente") {
			if o.Status == "consulta" && !o.StockReserved && len(items) > 0 {
				_ = b.store.ReplaceOrderItems(ctx, o.ID, items)
			}
			b.addOrderNote(ctx, o.ID, nota)
			b.Notify("orders")
			return
		}
	}
	o := &store.Order{CustomerID: conv.CustomerID, Status: "consulta", Source: "whatsapp", Notes: nota, Items: items}
	if err := b.store.CreateOrder(ctx, o); err != nil {
		log.Printf("bot: cita: %v", err)
		return
	}
	cc.OrderID = o.ID
	b.setState(ctx, conv, conv.State, *cc)
	b.Notify("orders")
}
