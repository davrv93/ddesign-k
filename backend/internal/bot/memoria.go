package bot

import (
	"context"
	"encoding/json"
	"sort"
	"strings"

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
