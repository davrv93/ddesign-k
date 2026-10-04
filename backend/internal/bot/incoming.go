package bot

import (
	"encoding/json"
	"strings"
)

// Incoming es un mensaje de WhatsApp ya normalizado desde el webhook de evolution-go.
type Incoming struct {
	ID       string
	Chat     string // JID del chat (a donde se responde)
	Phone    string
	PushName string
	FromMe   bool
	IsGroup  bool

	Text string

	HasImage   bool
	ImageB64   string // presente si evolution-go corre con WEBHOOK_FILES=true
	ImageMime  string
	RawMessage json.RawMessage // para /message/downloadmedia

	HasLocation bool
	Lat, Lng    float64
	LocationTxt string

	// FromAd: el mensaje llegó desde un anuncio de clic a WhatsApp (Facebook/Instagram). AdTitle es el
	// título del anuncio, que suele nombrar la prenda. Sin anuncio, «este vestido» no dice cuál es.
	FromAd  bool
	AdTitle string
}

// webhook es el sobre que manda evolution-go: {"event":"Message","data":{"Info":{...},"Message":{...}}}
type webhook struct {
	Event string `json:"event"`
	Data  struct {
		Info struct {
			ID        string `json:"ID"`
			Chat      string `json:"Chat"`
			Sender    string `json:"Sender"`
			SenderAlt string `json:"SenderAlt"`
			IsFromMe  bool   `json:"IsFromMe"`
			IsGroup   bool   `json:"IsGroup"`
			PushName  string `json:"PushName"`
		} `json:"Info"`
		Message json.RawMessage `json:"Message"`
	} `json:"data"`
}

type waMessage struct {
	Conversation        string `json:"conversation"`
	ExtendedTextMessage *struct {
		Text        string       `json:"text"`
		ContextInfo *contextInfo `json:"contextInfo"`
	} `json:"extendedTextMessage"`
	ImageMessage *struct {
		Caption     string       `json:"caption"`
		Mimetype    string       `json:"mimetype"`
		ContextInfo *contextInfo `json:"contextInfo"`
	} `json:"imageMessage"`
	LocationMessage     *location `json:"locationMessage"`
	LiveLocationMessage *location `json:"liveLocationMessage"`
	ButtonsResponse     *struct {
		SelectedDisplayText string `json:"selectedDisplayText"`
		SelectedButtonID    string `json:"selectedButtonID"`
	} `json:"buttonsResponseMessage"`
	ListResponse *struct {
		Title             string `json:"title"`
		SingleSelectReply *struct {
			SelectedRowID string `json:"selectedRowID"`
		} `json:"singleSelectReply"`
	} `json:"listResponseMessage"`
	Base64 string `json:"base64"`
}

// contextInfo trae, en los mensajes que llegan desde un anuncio de clic a WhatsApp, la referencia al
// anuncio (externalAdReply) y el origen de la conversión.
type contextInfo struct {
	ExternalAdReply *struct {
		Title      string `json:"title"`
		Body       string `json:"body"`
		SourceType string `json:"sourceType"`
		SourceURL  string `json:"sourceURL"`
	} `json:"externalAdReply"`
	ConversionSource           string `json:"conversionSource"`
	EntryPointConversionSource string `json:"entryPointConversionSource"`
}

func (c *contextInfo) fromAd() (bool, string) {
	if c == nil {
		return false, ""
	}
	if c.ExternalAdReply != nil {
		return true, strings.TrimSpace(firstNonEmpty(c.ExternalAdReply.Title, c.ExternalAdReply.Body))
	}
	src := strings.ToLower(c.ConversionSource + " " + c.EntryPointConversionSource)
	return strings.Contains(src, "ad"), ""
}

type location struct {
	Lat     float64 `json:"degreesLatitude"`
	Lng     float64 `json:"degreesLongitude"`
	Name    string  `json:"name"`
	Address string  `json:"address"`
}

// ParseWebhook devuelve nil si el evento no es un mensaje que el bot deba procesar.
func ParseWebhook(body []byte) (*Incoming, error) {
	var w webhook
	if err := json.Unmarshal(body, &w); err != nil {
		return nil, err
	}
	if w.Event != "Message" || w.Data.Info.ID == "" {
		return nil, nil
	}
	info := w.Data.Info
	chat := info.Chat
	if strings.HasSuffix(chat, "@lid") && strings.HasSuffix(info.SenderAlt, "@s.whatsapp.net") {
		chat = info.SenderAlt
	}
	in := &Incoming{
		ID:       info.ID,
		Chat:     chat,
		PushName: info.PushName,
		FromMe:   info.IsFromMe,
		IsGroup:  info.IsGroup || strings.HasSuffix(chat, "@g.us"),
	}
	if strings.HasSuffix(chat, "@broadcast") || strings.HasSuffix(chat, "@newsletter") {
		in.IsGroup = true
	}
	if strings.HasSuffix(chat, "@s.whatsapp.net") {
		in.Phone = strings.SplitN(strings.TrimSuffix(chat, "@s.whatsapp.net"), ":", 2)[0]
	}
	if len(w.Data.Message) == 0 {
		return in, nil
	}
	var m waMessage
	if err := json.Unmarshal(w.Data.Message, &m); err != nil {
		return nil, err
	}
	switch {
	case m.Conversation != "":
		in.Text = m.Conversation
	case m.ExtendedTextMessage != nil:
		in.Text = m.ExtendedTextMessage.Text
	case m.ButtonsResponse != nil:
		in.Text = firstNonEmpty(m.ButtonsResponse.SelectedButtonID, m.ButtonsResponse.SelectedDisplayText)
	case m.ListResponse != nil:
		if m.ListResponse.SingleSelectReply != nil {
			in.Text = m.ListResponse.SingleSelectReply.SelectedRowID
		}
		in.Text = firstNonEmpty(in.Text, m.ListResponse.Title)
	}
	if m.ImageMessage != nil {
		in.HasImage = true
		in.Text = m.ImageMessage.Caption
		in.ImageMime = m.ImageMessage.Mimetype
		in.ImageB64 = m.Base64
		// Sin el base64 (que puede pesar MB) para mandarlo a /message/downloadmedia.
		var raw map[string]json.RawMessage
		if json.Unmarshal(w.Data.Message, &raw) == nil {
			delete(raw, "base64")
			delete(raw, "mediaUrl")
			delete(raw, "mimetype")
			in.RawMessage, _ = json.Marshal(raw)
		}
	}
	for _, ci := range []*contextInfo{ctxOf(m.ExtendedTextMessage != nil, func() *contextInfo { return m.ExtendedTextMessage.ContextInfo }),
		ctxOf(m.ImageMessage != nil, func() *contextInfo { return m.ImageMessage.ContextInfo })} {
		if ok, title := ci.fromAd(); ok {
			in.FromAd, in.AdTitle = true, firstNonEmpty(in.AdTitle, title)
		}
	}
	loc := m.LocationMessage
	if loc == nil {
		loc = m.LiveLocationMessage
	}
	if loc != nil {
		in.HasLocation = true
		in.Lat, in.Lng = loc.Lat, loc.Lng
		in.LocationTxt = strings.TrimSpace(strings.Join(nonEmpty(loc.Name, loc.Address), " - "))
	}
	in.Text = strings.TrimSpace(in.Text)
	return in, nil
}

func ctxOf(ok bool, f func() *contextInfo) *contextInfo {
	if !ok {
		return nil
	}
	return f()
}

func firstNonEmpty(v ...string) string {
	for _, s := range v {
		if s != "" {
			return s
		}
	}
	return ""
}

func nonEmpty(v ...string) []string {
	var out []string
	for _, s := range v {
		if strings.TrimSpace(s) != "" {
			out = append(out, s)
		}
	}
	return out
}
