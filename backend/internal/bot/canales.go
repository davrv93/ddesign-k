package bot

// Canales de WhatsApp (octubre de 2026). Una conversación llega y se contesta por UNO de estos:
//
//	""        el número principal: evolution-go (EVOLUTION_*), como siempre
//	"cloud"   el número oficial de la WhatsApp Cloud API (WA_CLOUD_*, internal/whatsapp/cloud)
//	"n:<id>"  un número secundario de la tienda (tabla numeros_whatsapp, evolution-go con su propia instancia)
//
// El canal de la conversación es el del último mensaje que mandó la clienta; se recuerda en memoria y en el contexto
// guardado (convContext.Canal), así que sobrevive a un reinicio del backend.

import (
	"context"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/evolution"
	"github.com/davrv93/ddesign-k/backend/internal/store"
	"github.com/davrv93/ddesign-k/backend/internal/whatsapp/cloud"
)

// Mensajero: lo que el bot necesita de un número de WhatsApp. *evolution.Client y *cloud.Client lo cumplen.
type Mensajero interface {
	SendText(ctx context.Context, to, text string) (string, error)
	SendImage(ctx context.Context, to, url, caption string) (string, error)
	SendPoll(ctx context.Context, to, question string, options []string) (string, error)
	Presence(ctx context.Context, to, state string) error
	DownloadMedia(ctx context.Context, message json.RawMessage) ([]byte, string, error)
}

// Plantillero: un mensajero que puede mandar plantillas aprobadas (solo la API oficial).
type Plantillero interface {
	SendTemplate(ctx context.Context, to, name, lang string, params []string) (string, error)
}

const CanalCloud = "cloud"

// CanalSecundario: el canal de un número secundario.
func CanalSecundario(id int64) string { return "n:" + strconv.FormatInt(id, 10) }

type canales struct {
	mu          sync.Mutex
	porChat     map[string]string     // JID → canal del último mensaje entrante
	secundarios map[int64]*secundario // id → cliente de evolution-go de ese número
}

type secundario struct {
	cli  *evolution.Client
	hora time.Time
}

// UsarCloud conecta el número oficial (lo llama main si WA_CLOUD_* está configurado).
func (b *Bot) UsarCloud(c *cloud.Client) {
	if c != nil && c.Configurado() {
		b.cloud = c
	}
}

// Cloud: el cliente de la API oficial, o nil.
func (b *Bot) Cloud() *cloud.Client { return b.cloud }

// mensajero devuelve el número por el que sale un canal, o nil si no existe o no está configurado.
func (b *Bot) mensajero(canal string) Mensajero {
	switch {
	case canal == CanalCloud:
		if b.cloud != nil {
			return b.cloud
		}
	case strings.HasPrefix(canal, "n:"):
		id, _ := strconv.ParseInt(strings.TrimPrefix(canal, "n:"), 10, 64)
		if c := b.clienteSecundario(id); c != nil {
			return c
		}
	}
	if b.evo != nil {
		return b.evo
	}
	if b.cloud != nil { // sin evolution-go: el único número es el oficial
		return b.cloud
	}
	return nil
}

// clienteSecundario: el cliente de evolution-go de un número secundario (se relee del store cada 5 min).
func (b *Bot) clienteSecundario(id int64) *evolution.Client {
	if id <= 0 || b.cfg == nil || b.cfg.EvolutionURL == "" {
		return nil
	}
	b.can.mu.Lock()
	if b.can.secundarios == nil {
		b.can.secundarios = map[int64]*secundario{}
	}
	if s, ok := b.can.secundarios[id]; ok && time.Since(s.hora) < 5*time.Minute {
		b.can.mu.Unlock()
		return s.cli
	}
	b.can.mu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	n, err := b.store.GetNumero(ctx, id)
	if err != nil || n.Canal != "evolution" || n.Instancia == "" || n.Token == "" {
		return nil
	}
	cli := evolution.New(b.cfg.EvolutionURL, b.cfg.EvolutionGlobalKey, n.Instancia, n.Token)
	b.can.mu.Lock()
	b.can.secundarios[id] = &secundario{cli: cli, hora: time.Now()}
	b.can.mu.Unlock()
	return cli
}

// OlvidarSecundario: el número cambió o se borró (lo llama la API).
func (b *Bot) OlvidarSecundario(id int64) {
	b.can.mu.Lock()
	delete(b.can.secundarios, id)
	b.can.mu.Unlock()
}

func (b *Bot) recordarCanal(chat, canal string) {
	b.can.mu.Lock()
	if b.can.porChat == nil {
		b.can.porChat = map[string]string{}
	}
	b.can.porChat[chat] = canal
	b.can.mu.Unlock()
}

// canalDe: por dónde se le contesta a esta conversación.
func (b *Bot) canalDe(conv *store.Conversation) string {
	if conv == nil || conv.Customer == nil {
		return ""
	}
	b.can.mu.Lock()
	c, ok := b.can.porChat[conv.Customer.JID]
	b.can.mu.Unlock()
	if ok {
		return c
	}
	var cc convContext
	_ = json.Unmarshal([]byte(conv.Context), &cc)
	return cc.Canal
}

// FueraDeVentana: pasaron más de 24 h desde el último mensaje de la clienta (o nunca escribió). En la API oficial,
// fuera de la ventana solo salen plantillas.
func (b *Bot) FueraDeVentana(ctx context.Context, convID int64, ahora time.Time) bool {
	ult, ok := b.store.UltimoEntrante(ctx, convID)
	return !ok || ahora.Sub(ult) > store.VentanaLibre
}

// enviarPlantilla: manda una plantilla aprobada por la API oficial y la deja en el historial.
func (b *Bot) enviarPlantilla(ctx context.Context, conv *store.Conversation, nombre string, params []string, resumen string) error {
	if b.cloud == nil {
		return fmt.Errorf("sin API oficial configurada")
	}
	m := &store.Message{ConversationID: conv.ID, Direction: "out", Status: "pending", Kind: "text",
		Body: "📨 " + firstNonEmpty(resumen, "Plantilla «"+nombre+"»"), Author: "bot"}
	if err := b.store.AddMessage(ctx, m); err != nil {
		return err
	}
	id, err := b.cloud.SendTemplate(ctx, conv.Customer.JID, nombre, "", params)
	status := "sent"
	if err != nil {
		status = "failed"
	}
	b.markDelivery(m.ID, id, status)
	return err
}

// DesdeCloud convierte un mensaje del webhook de la API oficial en un Incoming del bot (canal «cloud»). El JID se arma
// como el de evolution («51…@s.whatsapp.net») para que la clienta sea la misma si escribe por los dos números.
func DesdeCloud(m cloud.Mensaje) *Incoming {
	if m.From == "" || m.ID == "" {
		return nil
	}
	in := &Incoming{ID: m.ID, Chat: m.From + "@s.whatsapp.net", Phone: m.From, PushName: m.Nombre, Text: m.Texto, Canal: CanalCloud}
	switch {
	case m.MediaID != "":
		in.HasImage, in.ImageMime = true, m.MediaMime
		in.RawMessage, _ = json.Marshal(cloud.MediaRef{ID: m.MediaID})
	case m.Lat != 0 || m.Lng != 0:
		in.HasLocation, in.Lat, in.Lng, in.LocationTxt = true, m.Lat, m.Lng, m.LugarTxt
	}
	if m.Tipo != "text" && m.Tipo != "image" && m.Tipo != "interactive" && m.Tipo != "button" && m.Tipo != "location" && in.Text == "" {
		in.Text = "(" + m.Tipo + ")" // audio, video, sticker…: queda en el historial y el agente pide que escriba
	}
	if r := m.Referral; r != nil {
		in.FromAd = r.SourceType == "ad" || r.SourceID != ""
		in.AdTitle, in.AdID, in.CtwaClid = firstNonEmpty(r.Headline, r.Body), r.SourceID, r.CtwaClid
	}
	return in
}
