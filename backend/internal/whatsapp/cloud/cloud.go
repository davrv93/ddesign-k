// Package cloud habla con la WhatsApp Cloud API oficial de Meta (Graph API): enviar texto, imagen, botones, listas y
// plantillas; descargar medios; leer el webhook (con su firma) y listar las plantillas aprobadas.
//
// Se usa para el número oficial de una tienda (octubre de 2026, pedido de Luis de Baruka). Las credenciales vienen del
// entorno: WA_CLOUD_TOKEN (usuario del sistema del portafolio de la tienda, permanente), WA_CLOUD_PHONE_NUMBER_ID,
// WA_CLOUD_WABA_ID, WA_CLOUD_APP_SECRET (firma del webhook) y WA_CLOUD_VERIFY_TOKEN (verificación del webhook).
//
// Reglas de WhatsApp que este paquete NO decide (las decide el bot): fuera de las 24 h desde el último mensaje de la
// clienta solo se puede escribir con plantilla aprobada.
package cloud

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
	"unicode/utf8"
)

const VersionPorDefecto = "v23.0"

type Client struct {
	BaseURL       string // https://graph.facebook.com (las pruebas usan un servidor simulado)
	Version       string
	PhoneNumberID string
	WABAID        string
	Token         string
	AppSecret     string
	VerifyToken   string
	Idioma        string // de las plantillas, p. ej. "es"
	HTTP          *http.Client
}

func New(baseURL, version, phoneNumberID, wabaID, token, appSecret, verifyToken string) *Client {
	if baseURL == "" {
		baseURL = "https://graph.facebook.com"
	}
	if version == "" {
		version = VersionPorDefecto
	}
	return &Client{BaseURL: strings.TrimRight(baseURL, "/"), Version: version, PhoneNumberID: phoneNumberID, WABAID: wabaID,
		Token: token, AppSecret: appSecret, VerifyToken: verifyToken, Idioma: "es", HTTP: &http.Client{Timeout: 45 * time.Second}}
}

// Configurado: hay número y token.
func (c *Client) Configurado() bool { return c != nil && c.PhoneNumberID != "" && c.Token != "" }

// Error de la Graph API.
type Error struct {
	Status  int
	Code    int    `json:"code"`
	Message string `json:"message"`
	Type    string `json:"type"`
}

func (e *Error) Error() string {
	return fmt.Sprintf("whatsapp cloud %d: %s (código %d)", e.Status, e.Message, e.Code)
}

// FueraDeVentana: Meta rechazó un mensaje libre porque pasaron más de 24 h (código 131047).
func FueraDeVentana(err error) bool {
	var e *Error
	return errors.As(err, &e) && e.Code == 131047
}

func (c *Client) url(path string) string { return c.BaseURL + "/" + c.Version + path }

func (c *Client) do(ctx context.Context, method, url string, body any, out any) error {
	var rd io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return err
		}
		rd = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, url, rd)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+c.Token)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if resp.StatusCode >= 300 {
		var env struct {
			Error *Error `json:"error"`
		}
		if json.Unmarshal(data, &env) == nil && env.Error != nil {
			env.Error.Status = resp.StatusCode
			return env.Error
		}
		return &Error{Status: resp.StatusCode, Message: strings.TrimSpace(string(data))}
	}
	if out != nil && len(data) > 0 {
		return json.Unmarshal(data, out)
	}
	return nil
}

// Numero: «51987654321@s.whatsapp.net» o «+51 987 654 321» → «51987654321».
func Numero(to string) string {
	if i := strings.IndexByte(to, '@'); i >= 0 {
		to = to[:i]
	}
	return strings.Map(func(r rune) rune {
		if r >= '0' && r <= '9' {
			return r
		}
		return -1
	}, to)
}

type sendResp struct {
	Messages []struct {
		ID string `json:"id"`
	} `json:"messages"`
}

func (c *Client) send(ctx context.Context, to string, msg map[string]any) (string, error) {
	msg["messaging_product"] = "whatsapp"
	msg["recipient_type"] = "individual"
	msg["to"] = Numero(to)
	var out sendResp
	if err := c.do(ctx, http.MethodPost, c.url("/"+c.PhoneNumberID+"/messages"), msg, &out); err != nil {
		return "", err
	}
	if len(out.Messages) == 0 {
		return "", nil
	}
	return out.Messages[0].ID, nil
}

func (c *Client) SendText(ctx context.Context, to, text string) (string, error) {
	return c.send(ctx, to, map[string]any{"type": "text", "text": map[string]any{"body": recorta(text, 4096), "preview_url": true}})
}

func (c *Client) SendImage(ctx context.Context, to, link, caption string) (string, error) {
	img := map[string]any{"link": link}
	if caption != "" {
		img["caption"] = recorta(caption, 1024)
	}
	return c.send(ctx, to, map[string]any{"type": "image", "image": img})
}

// SendPoll: en la API oficial los botones SÍ se ven. Hasta 3 opciones = botones de respuesta; hasta 10 = lista.
func (c *Client) SendPoll(ctx context.Context, to, question string, options []string) (string, error) {
	if len(options) == 0 {
		return c.SendText(ctx, to, question)
	}
	body := map[string]any{"text": recorta(question, 1024)}
	if len(options) <= 3 {
		var bs []map[string]any
		for i, o := range options {
			bs = append(bs, map[string]any{"type": "reply", "reply": map[string]any{"id": fmt.Sprintf("op_%d", i+1), "title": recorta(o, 20)}})
		}
		return c.send(ctx, to, map[string]any{"type": "interactive", "interactive": map[string]any{
			"type": "button", "body": body, "action": map[string]any{"buttons": bs}}})
	}
	if len(options) > 10 {
		options = options[:10]
	}
	var rows []map[string]any
	for i, o := range options {
		rows = append(rows, map[string]any{"id": fmt.Sprintf("op_%d", i+1), "title": recorta(o, 24)})
	}
	return c.send(ctx, to, map[string]any{"type": "interactive", "interactive": map[string]any{
		"type": "list", "body": body,
		"action": map[string]any{"button": "Ver opciones", "sections": []map[string]any{{"title": "Opciones", "rows": rows}}}}})
}

// SendTemplate: plantilla aprobada, con los parámetros del cuerpo en orden ({{1}}, {{2}}…).
func (c *Client) SendTemplate(ctx context.Context, to, name, lang string, params []string) (string, error) {
	if lang == "" {
		lang = c.Idioma
	}
	tpl := map[string]any{"name": name, "language": map[string]any{"code": lang}}
	if len(params) > 0 {
		var ps []map[string]any
		for _, p := range params {
			ps = append(ps, map[string]any{"type": "text", "text": recorta(p, 1024)})
		}
		tpl["components"] = []map[string]any{{"type": "body", "parameters": ps}}
	}
	return c.send(ctx, to, map[string]any{"type": "template", "template": tpl})
}

// Presence: la API oficial no tiene «escribiendo…» sin un mensaje que marcar como leído; no hace nada.
func (c *Client) Presence(ctx context.Context, to, state string) error { return nil }

// MarcarLeido marca un mensaje entrante como leído (los dos ✓ azules).
func (c *Client) MarcarLeido(ctx context.Context, messageID string) error {
	return c.do(ctx, http.MethodPost, c.url("/"+c.PhoneNumberID+"/messages"), map[string]any{
		"messaging_product": "whatsapp", "status": "read", "message_id": messageID}, nil)
}

// MediaRef es lo que el bot guarda en Incoming.RawMessage para descargar una imagen después.
type MediaRef struct {
	ID string `json:"cloud_media_id"`
}

// DownloadMedia recibe el RawMessage de un entrante ({"cloud_media_id": "..."}) y baja el archivo.
func (c *Client) DownloadMedia(ctx context.Context, raw json.RawMessage) ([]byte, string, error) {
	var ref MediaRef
	if err := json.Unmarshal(raw, &ref); err != nil || ref.ID == "" {
		return nil, "", fmt.Errorf("whatsapp cloud: sin id de medio")
	}
	var meta struct {
		URL      string `json:"url"`
		MimeType string `json:"mime_type"`
	}
	if err := c.do(ctx, http.MethodGet, c.url("/"+ref.ID), nil, &meta); err != nil {
		return nil, "", err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, meta.URL, nil)
	if err != nil {
		return nil, "", err
	}
	req.Header.Set("Authorization", "Bearer "+c.Token)
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		return nil, "", fmt.Errorf("whatsapp cloud: descarga %d", resp.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	return data, meta.MimeType, err
}

// Plantilla aprobada (o en revisión) de la cuenta.
type Plantilla struct {
	Name     string `json:"name"`
	Language string `json:"language"`
	Status   string `json:"status"`
	Category string `json:"category"`
	Body     string `json:"body"`
}

func (c *Client) Plantillas(ctx context.Context) ([]Plantilla, error) {
	if c.WABAID == "" {
		return []Plantilla{}, nil
	}
	var out struct {
		Data []struct {
			Name       string `json:"name"`
			Language   string `json:"language"`
			Status     string `json:"status"`
			Category   string `json:"category"`
			Components []struct {
				Type string `json:"type"`
				Text string `json:"text"`
			} `json:"components"`
		} `json:"data"`
	}
	if err := c.do(ctx, http.MethodGet, c.url("/"+c.WABAID+"/message_templates?limit=200&fields=name,language,status,category,components"), nil, &out); err != nil {
		return nil, err
	}
	ps := []Plantilla{}
	for _, d := range out.Data {
		p := Plantilla{Name: d.Name, Language: d.Language, Status: d.Status, Category: d.Category}
		for _, c := range d.Components {
			if strings.EqualFold(c.Type, "BODY") {
				p.Body = c.Text
			}
		}
		ps = append(ps, p)
	}
	return ps, nil
}

// FirmaValida comprueba X-Hub-Signature-256 («sha256=<hex>») con el secreto de la app. Sin secreto configurado, NADA
// es válido: un webhook sin firma comprobable no se procesa.
func (c *Client) FirmaValida(body []byte, header string) bool {
	if c == nil || c.AppSecret == "" || !strings.HasPrefix(header, "sha256=") {
		return false
	}
	want, err := hex.DecodeString(strings.TrimPrefix(header, "sha256="))
	if err != nil {
		return false
	}
	m := hmac.New(sha256.New, []byte(c.AppSecret))
	m.Write(body)
	return hmac.Equal(m.Sum(nil), want)
}

// Firmar: la cabecera X-Hub-Signature-256 de un cuerpo (para las pruebas y el simulado).
func Firmar(secret string, body []byte) string {
	m := hmac.New(sha256.New, []byte(secret))
	m.Write(body)
	return "sha256=" + hex.EncodeToString(m.Sum(nil))
}

// ---------------------------------------------------------------------------
// Webhook

// Referral: de qué anuncio de clic a WhatsApp llegó el mensaje.
type Referral struct {
	SourceURL  string `json:"source_url"`
	SourceID   string `json:"source_id"`
	SourceType string `json:"source_type"` // "ad" | "post"
	Headline   string `json:"headline"`
	Body       string `json:"body"`
	CtwaClid   string `json:"ctwa_clid"`
}

// Mensaje entrante ya leído del webhook.
type Mensaje struct {
	ID        string
	From      string // número, solo dígitos
	Nombre    string
	Tipo      string // text | image | interactive | button | location | audio | …
	Texto     string // texto, título del botón elegido o pie de la imagen
	MediaID   string
	MediaMime string
	Lat, Lng  float64
	LugarTxt  string
	Referral  *Referral
	Timestamp time.Time
}

type webhook struct {
	Entry []struct {
		Changes []struct {
			Field string `json:"field"`
			Value struct {
				Metadata struct {
					PhoneNumberID string `json:"phone_number_id"`
				} `json:"metadata"`
				Contacts []struct {
					WaID    string `json:"wa_id"`
					Profile struct {
						Name string `json:"name"`
					} `json:"profile"`
				} `json:"contacts"`
				Messages []struct {
					ID        string    `json:"id"`
					From      string    `json:"from"`
					Timestamp string    `json:"timestamp"`
					Type      string    `json:"type"`
					Referral  *Referral `json:"referral"`
					Text      *struct {
						Body string `json:"body"`
					} `json:"text"`
					Image *struct {
						ID       string `json:"id"`
						MimeType string `json:"mime_type"`
						Caption  string `json:"caption"`
					} `json:"image"`
					Interactive *struct {
						Type        string `json:"type"`
						ButtonReply *struct {
							ID    string `json:"id"`
							Title string `json:"title"`
						} `json:"button_reply"`
						ListReply *struct {
							ID    string `json:"id"`
							Title string `json:"title"`
						} `json:"list_reply"`
					} `json:"interactive"`
					Button *struct {
						Text    string `json:"text"`
						Payload string `json:"payload"`
					} `json:"button"`
					Location *struct {
						Latitude  float64 `json:"latitude"`
						Longitude float64 `json:"longitude"`
						Name      string  `json:"name"`
						Address   string  `json:"address"`
					} `json:"location"`
				} `json:"messages"`
			} `json:"value"`
		} `json:"changes"`
	} `json:"entry"`
}

// LeerWebhook devuelve los mensajes entrantes de un POST del webhook (los estados de entrega se ignoran). Solo los del
// número configurado: otro número de la misma cuenta no es asunto de este backend.
func (c *Client) LeerWebhook(body []byte) ([]Mensaje, error) {
	var w webhook
	if err := json.Unmarshal(body, &w); err != nil {
		return nil, err
	}
	var out []Mensaje
	for _, e := range w.Entry {
		for _, ch := range e.Changes {
			if ch.Field != "" && ch.Field != "messages" {
				continue
			}
			if c != nil && c.PhoneNumberID != "" && ch.Value.Metadata.PhoneNumberID != "" && ch.Value.Metadata.PhoneNumberID != c.PhoneNumberID {
				continue
			}
			nombres := map[string]string{}
			for _, ct := range ch.Value.Contacts {
				nombres[ct.WaID] = ct.Profile.Name
			}
			for _, m := range ch.Value.Messages {
				x := Mensaje{ID: m.ID, From: Numero(m.From), Nombre: nombres[m.From], Tipo: m.Type, Referral: m.Referral}
				var ts int64
				_, _ = fmt.Sscan(m.Timestamp, &ts)
				if ts > 0 {
					x.Timestamp = time.Unix(ts, 0).UTC()
				}
				switch {
				case m.Text != nil:
					x.Texto = m.Text.Body
				case m.Image != nil:
					x.MediaID, x.MediaMime, x.Texto = m.Image.ID, m.Image.MimeType, m.Image.Caption
				case m.Interactive != nil && m.Interactive.ButtonReply != nil:
					x.Texto = m.Interactive.ButtonReply.Title
				case m.Interactive != nil && m.Interactive.ListReply != nil:
					x.Texto = m.Interactive.ListReply.Title
				case m.Button != nil:
					x.Texto = m.Button.Text
				case m.Location != nil:
					x.Lat, x.Lng = m.Location.Latitude, m.Location.Longitude
					x.LugarTxt = strings.TrimSpace(m.Location.Name + " " + m.Location.Address)
				}
				out = append(out, x)
			}
		}
	}
	return out, nil
}

func recorta(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	r := []rune(s)
	return string(r[:n-1]) + "…"
}
