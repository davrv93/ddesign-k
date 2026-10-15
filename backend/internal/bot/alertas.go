package bot

// Clienta caliente (15-10-2026, pedido de la tienda): cuando la temperatura que devuelve el agente PASA a «caliente»
// (evento en 14 días o menos, quiere comprar, pidió cita o lo necesita con urgencia) se crea una alerta: el panel la
// abre como modal (tema SSE «alertas») y, si Ajustes → «Clienta caliente» tiene un número, se le manda un WhatsApp a
// ese número con el resumen. Una alerta por conversación y semana (store.AlertaVentana). El aviso sale en segundo plano:
// un WhatsApp caído nunca retrasa la respuesta a la clienta.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"strings"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// ResumenAlerta: lo que la asesora necesita saber de un vistazo (los datos del guion que ya se conocen).
type ResumenAlerta struct {
	Evento       string   `json:"evento"`
	Fecha        string   `json:"fecha"`
	Dias         *int     `json:"dias"`
	Horario      string   `json:"horario"`
	Talla        string   `json:"talla"`
	Color        string   `json:"color"`
	Estilo       string   `json:"estilo"`
	Presupuesto  string   `json:"presupuesto"`
	Dudas        []string `json:"dudas"`
	Prenda       string   `json:"prenda"`
	PrendaNombre string   `json:"prenda_nombre"`
}

// fichaMem: la memoria del agente tal como la necesitan la alerta y la ficha del panel.
type fichaMem struct {
	Etapa       string         `json:"etapa"`
	Producto    string         `json:"producto"`
	Temperatura string         `json:"temperatura"`
	Motivo      string         `json:"temperatura_motivo"`
	Sabemos     map[string]any `json:"sabemos"`
	Objeciones  []string       `json:"objeciones"`
	Pedido      *struct {
		Codigo     string `json:"codigo"`
		Talla      string `json:"talla"`
		Confirmado bool   `json:"confirmado"`
	} `json:"pedido"`
}

func leerFichaMem(raw json.RawMessage) fichaMem {
	var m fichaMem
	if len(raw) > 0 {
		_ = json.Unmarshal(raw, &m)
	}
	if m.Sabemos == nil {
		m.Sabemos = map[string]any{}
	}
	return m
}

// Sab: un dato de `sabemos` como texto ("" si no se sabe).
func (m fichaMem) Sab(k string) string {
	switch v := m.Sabemos[k].(type) {
	case string:
		return strings.TrimSpace(v)
	case float64:
		return strings.TrimRight(strings.TrimRight(fmt.Sprintf("%.2f", v), "0"), ".")
	}
	return ""
}

// DiasAlEvento: días que faltan (hoy en Lima) o nil si no hay fecha exacta.
func (m fichaMem) DiasAlEvento(hoy time.Time) *int {
	t, err := time.ParseInLocation("2006-01-02", m.Sab("fecha_iso"), store.Lima)
	if err != nil {
		return nil
	}
	h := hoy.In(store.Lima)
	d := int(t.Sub(time.Date(h.Year(), h.Month(), h.Day(), 0, 0, 0, 0, store.Lima)).Hours() / 24)
	return &d
}

var diasLargos = [...]string{"domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"}
var mesesLargos = [...]string{"enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
	"noviembre", "diciembre"}

// FechaLegible: «sábado 25 de octubre» si hay fecha exacta (el agente guarda el texto sin tildes: «el sabado 25…»);
// si no, lo que dijo ella; si tampoco, "".
func (m fichaMem) FechaLegible() string {
	if t, err := time.Parse("2006-01-02", m.Sab("fecha_iso")); err == nil {
		return fmt.Sprintf("%s %d de %s", diasLargos[t.Weekday()], t.Day(), mesesLargos[t.Month()-1])
	}
	return firstNonEmpty(m.Sab("fecha"), m.Sab("fecha_iso"))
}

// Las objeciones del agente son claves («precio», «envio»…): a la asesora se le dicen en palabras.
var dudaTexto = map[string]string{"precio": "el precio", "envio": "el envío", "talla": "la talla", "tiempo": "el tiempo de entrega",
	"confianza": "la confianza (pagar antes de recibir)", "pensarlo": "quiere pensarlo", "consultar": "lo consulta con alguien",
	"color": "el color", "tela": "la tela", "duda": "tiene una duda"}

func (m fichaMem) Dudas() []string {
	out := []string{}
	for _, o := range m.Objeciones {
		if t := dudaTexto[o]; t != "" {
			out = append(out, t)
		} else if o = strings.TrimSpace(o); o != "" {
			out = append(out, o)
		}
	}
	return out
}

func (b *Bot) resumenAlerta(ctx context.Context, m fichaMem) ResumenAlerta {
	r := ResumenAlerta{Evento: m.Sab("ocasion"), Fecha: m.FechaLegible(),
		Dias: m.DiasAlEvento(time.Now()), Horario: m.Sab("horario"), Talla: m.Sab("talla"), Color: m.Sab("color"),
		Estilo: m.Sab("estilo"), Presupuesto: m.Sab("presupuesto"), Dudas: m.Dudas()}
	cod := m.Producto
	if cod == "" && m.Pedido != nil {
		cod = m.Pedido.Codigo
	}
	if cod != "" {
		r.Prenda = cod
		if p, err := b.store.GetProductByCode(ctx, cod); err == nil {
			r.PrendaNombre = p.Name
		}
	}
	return r
}

// avisarSiCaliente: la memoria nueva trae «caliente» y la anterior no → alerta (una por semana y conversación).
func (b *Bot) avisarSiCaliente(ctx context.Context, conv *store.Conversation, antes, despues json.RawMessage) {
	if conv == nil || leerMemVenta(despues).Temperatura != "caliente" || leerMemVenta(antes).Temperatura == "caliente" {
		return
	}
	m := leerFichaMem(despues)
	res := b.resumenAlerta(ctx, m)
	raw, _ := json.Marshal(res)
	a := &store.Alerta{Tipo: "caliente", ConversationID: conv.ID, Motivo: m.Motivo, Resumen: raw}
	if err := b.store.CrearAlerta(ctx, a); err != nil {
		if !errors.Is(err, store.AlertaRepetida) {
			log.Printf("bot: alerta caliente: %v", err)
		}
		return
	}
	log.Printf("bot: clienta caliente (conversación %d): %s", conv.ID, m.Motivo)
	b.hito(ctx, conv, "🔥 Clienta caliente: "+firstNonEmpty(m.Motivo, "lista para comprar"))
	b.Notify("alertas")
	numero := soloDigitos(b.store.Setting(ctx, store.SettingAlertaWhatsApp, ""))
	if numero == "" || b.evo == nil {
		return
	}
	texto := textoAlerta(b.customerName(conv), conv.Customer.Phone, m.Motivo, res, b.enlaceConversacion(conv.ID))
	st, id := b.store, a.ID
	go func() {
		c, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		if _, err := b.evo.SendText(c, numero, texto); err != nil {
			log.Printf("bot: aviso de clienta caliente a •••%s: %v", ultimos(numero, 3), err)
			return
		}
		if err := st.MarcarAlertaEnviada(c, id); err != nil {
			log.Printf("bot: marcar aviso enviado: %v", err)
		}
		b.Notify("alertas")
	}()
}

// enlaceConversacion: el panel de la conversación. En JMD Ventas PUBLIC_URL es la raíz de todas las empresas
// («https://…/jmdventas»): se le suma la empresa del WhatsApp, si no la trae ya.
func (b *Bot) enlaceConversacion(id int64) string {
	base := strings.TrimRight(b.cfg.PublicURL, "/")
	if base == "" {
		return ""
	}
	if t := b.cfg.WhatsAppTenant; b.cfg.MultiTenant && t != "" && !strings.HasSuffix(base, "/"+t) {
		base += "/" + t
	}
	return fmt.Sprintf("%s/conversaciones/?c=%d", base, id)
}

// textoAlerta: el WhatsApp que recibe la tienda. Sin datos de pago; el teléfono de la clienta sí (es para llamarla).
func textoAlerta(nombre, telefono, motivo string, r ResumenAlerta, enlace string) string {
	var sb strings.Builder
	sb.WriteString("🔥 *Clienta caliente*")
	if nombre = strings.TrimSpace(nombre); nombre != "" {
		sb.WriteString(": " + nombre)
	}
	if tel := soloDigitos(telefono); tel != "" {
		sb.WriteString(" (+" + tel + ")")
	}
	if motivo != "" {
		sb.WriteString("\n" + motivo)
	}
	linea := func(et, v string) {
		if v != "" {
			sb.WriteString("\n• " + et + ": " + v)
		}
	}
	evento := r.Evento
	if r.Fecha != "" {
		evento = strings.TrimSpace(evento + " · " + r.Fecha)
	}
	if r.Dias != nil && *r.Dias >= 0 {
		evento += fmt.Sprintf(" (en %d día%s)", *r.Dias, map[bool]string{true: "", false: "s"}[*r.Dias == 1])
	}
	linea("Evento", strings.Trim(evento, " ·"))
	prenda := r.PrendaNombre
	if prenda == "" {
		prenda = r.Prenda
	}
	linea("Prenda", prenda)
	linea("Talla", r.Talla)
	linea("Color", r.Color)
	linea("Estilo", r.Estilo)
	linea("Presupuesto", r.Presupuesto)
	if len(r.Dudas) > 0 {
		linea("Dudas", strings.Join(r.Dudas, ", "))
	}
	if enlace != "" {
		sb.WriteString("\n👉 " + enlace)
	}
	return sb.String()
}

func ultimos(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[len(s)-n:]
}

// FichaConversacion: «Lo que sabemos» de una conversación para el panel (GET /api/conversations/{id}/ficha). Sale de la
// memoria del agente guardada en el contexto de la conversación; sin memoria, todo vacío y temperatura fría.
type FichaConversacion struct {
	Temperatura       string            `json:"temperatura"`
	TemperaturaMotivo string            `json:"temperatura_motivo"`
	DiasAlEvento      *int              `json:"dias_al_evento"`
	Etapa             string            `json:"etapa"`
	Sabemos           map[string]string `json:"sabemos"`
	Objeciones        []string          `json:"objeciones"`
	Producto          *PrendaFicha      `json:"producto"`
	Pedido            *PedidoFicha      `json:"pedido"`
}

type PrendaFicha struct {
	Codigo string `json:"codigo"`
	Nombre string `json:"nombre"`
}

type PedidoFicha struct {
	Codigo     string `json:"codigo"`
	Talla      string `json:"talla"`
	Confirmado bool   `json:"confirmado"`
}

// CamposFicha: los datos de `sabemos` que ve el panel (los 8 del guion y los que ayudan a cerrar).
var CamposFicha = []string{"nombre", "ocasion", "fecha", "fecha_iso", "horario", "talla", "estatura", "color", "estilo",
	"presupuesto", "envio", "ciudad", "para_quien"}

// LeerFicha arma la ficha desde el contexto guardado de la conversación. `nombreDe` da el nombre de una prenda por código.
func LeerFicha(contexto string, hoy time.Time, nombreDe func(codigo string) string) FichaConversacion {
	var cc convContext
	_ = json.Unmarshal([]byte(contexto), &cc)
	m := leerFichaMem(cc.Memoria)
	f := FichaConversacion{Temperatura: firstNonEmpty(m.Temperatura, "frio"), TemperaturaMotivo: m.Motivo,
		DiasAlEvento: m.DiasAlEvento(hoy), Etapa: firstNonEmpty(cc.Etapa, m.Etapa, "prospeccion"),
		Sabemos: map[string]string{}, Objeciones: m.Dudas()}
	if f.TemperaturaMotivo == "" && len(cc.Memoria) == 0 {
		f.TemperaturaMotivo = "sin datos todavía"
	}
	for _, k := range CamposFicha {
		if v := m.Sab(k); v != "" {
			f.Sabemos[k] = v
		}
	}
	if v := m.FechaLegible(); v != "" {
		f.Sabemos["fecha"] = v
	}
	if m.Producto != "" {
		f.Producto = &PrendaFicha{Codigo: m.Producto, Nombre: nombreDe(m.Producto)}
	}
	if m.Pedido != nil && m.Pedido.Codigo != "" {
		f.Pedido = &PedidoFicha{Codigo: m.Pedido.Codigo, Talla: m.Pedido.Talla, Confirmado: m.Pedido.Confirmado}
	}
	return f
}
