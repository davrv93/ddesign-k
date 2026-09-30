// Package evolution habla con el fork davrv93/evolution-go (WhatsApp vía whatsmeow).
package evolution

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

type Client struct {
	BaseURL       string
	GlobalKey     string
	InstanceName  string
	InstanceToken string
	HTTP          *http.Client
}

func New(baseURL, globalKey, instance, token string) *Client {
	return &Client{
		BaseURL:       baseURL,
		GlobalKey:     globalKey,
		InstanceName:  instance,
		InstanceToken: token,
		HTTP:          &http.Client{Timeout: 60 * time.Second},
	}
}

type envelope struct {
	Message string          `json:"message"`
	Error   string          `json:"error"`
	Data    json.RawMessage `json:"data"`
}

func (c *Client) do(ctx context.Context, method, path, apikey string, body any, out any) error {
	var rd io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return err
		}
		rd = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.BaseURL+path, rd)
	if err != nil {
		return err
	}
	req.Header.Set("apikey", apikey)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 64<<20))
	if err != nil {
		return err
	}
	var env envelope
	_ = json.Unmarshal(raw, &env)
	if resp.StatusCode >= 300 {
		msg := env.Error
		if msg == "" {
			msg = strings.TrimSpace(string(raw))
		}
		return fmt.Errorf("evolution %s %s: %d %s", method, path, resp.StatusCode, msg)
	}
	if out != nil && len(env.Data) > 0 {
		return json.Unmarshal(env.Data, out)
	}
	return nil
}

type Instance struct {
	ID        string `json:"id"`
	Name      string `json:"name"`
	Token     string `json:"token"`
	Webhook   string `json:"webhook"`
	Jid       string `json:"jid"`
	Connected bool   `json:"connected"`
	Events    string `json:"events"`
}

// EnsureInstance crea la instancia si no existe. Devuelve la instancia existente o nueva.
func (c *Client) EnsureInstance(ctx context.Context) (*Instance, error) {
	var all []Instance
	if err := c.do(ctx, http.MethodGet, "/instance/all", c.GlobalKey, nil, &all); err != nil {
		return nil, err
	}
	for i := range all {
		if all[i].Name == c.InstanceName {
			if all[i].Token != c.InstanceToken {
				return nil, fmt.Errorf("la instancia %q ya existe con otro token; ajusta EVOLUTION_INSTANCE_TOKEN", c.InstanceName)
			}
			return &all[i], nil
		}
	}
	var created Instance
	err := c.do(ctx, http.MethodPost, "/instance/create", c.GlobalKey, map[string]any{
		"name":  c.InstanceName,
		"token": c.InstanceToken,
		"advancedSettings": map[string]any{
			"ignoreGroups": true,
			"ignoreStatus": true,
		},
	}, &created)
	return &created, err
}

// Connect registra el webhook y arranca el cliente de WhatsApp (genera QR si no hay sesión).
func (c *Client) Connect(ctx context.Context, webhookURL string) error {
	return c.do(ctx, http.MethodPost, "/instance/connect", c.InstanceToken, map[string]any{
		"webhookUrl": webhookURL,
		"subscribe":  []string{"MESSAGE"},
		"immediate":  true,
	}, nil)
}

type Status struct {
	Connected bool   `json:"Connected"`
	LoggedIn  bool   `json:"LoggedIn"`
	Name      string `json:"Name"`
}

func (c *Client) Status(ctx context.Context) (*Status, error) {
	var st Status
	err := c.do(ctx, http.MethodGet, "/instance/status", c.InstanceToken, nil, &st)
	return &st, err
}

type QR struct {
	Image        string `json:"qrcode"` // data:image/png;base64,...
	Code         string `json:"code"`
	PasskeyStage string `json:"passkeyStage,omitempty"`
	PasskeyURL   string `json:"passkeyOpenUrl,omitempty"`
	PasskeyCode  string `json:"passkeyCode,omitempty"`
}

func (c *Client) QR(ctx context.Context) (*QR, error) {
	var qr QR
	err := c.do(ctx, http.MethodGet, "/instance/qr", c.InstanceToken, nil, &qr)
	return &qr, err
}

func (c *Client) Pair(ctx context.Context, phone string) (string, error) {
	var out struct {
		PairingCode string `json:"PairingCode"`
	}
	err := c.do(ctx, http.MethodPost, "/instance/pair", c.InstanceToken, map[string]any{"phone": phone, "subscribe": []string{"MESSAGE"}}, &out)
	return out.PairingCode, err
}

func (c *Client) Logout(ctx context.Context) error {
	return c.do(ctx, http.MethodDelete, "/instance/logout", c.InstanceToken, nil, nil)
}

func (c *Client) Reconnect(ctx context.Context) error {
	return c.do(ctx, http.MethodPost, "/instance/reconnect", c.InstanceToken, nil, nil)
}

type sendResult struct {
	Info struct {
		ID string `json:"ID"`
	} `json:"Info"`
}

// numberField arma el campo "number": JID completo sin normalizar, o sólo dígitos.
func numberField(to string) map[string]any {
	m := map[string]any{"number": to}
	if strings.Contains(to, "@") {
		m["formatJid"] = false
	}
	return m
}

func (c *Client) SendText(ctx context.Context, to, text string) (string, error) {
	body := numberField(to)
	body["text"] = text
	var out sendResult
	err := c.do(ctx, http.MethodPost, "/send/text", c.InstanceToken, body, &out)
	return out.Info.ID, err
}

// SendImage envía una imagen por URL (debe ser accesible desde el contenedor de evolution-go).
func (c *Client) SendImage(ctx context.Context, to, url, caption string) (string, error) {
	body := numberField(to)
	body["type"] = "image"
	body["url"] = url
	body["caption"] = caption
	var out sendResult
	err := c.do(ctx, http.MethodPost, "/send/media", c.InstanceToken, body, &out)
	return out.Info.ID, err
}

// DownloadMedia descarga y descifra el medio de un mensaje recibido.
func (c *Client) DownloadMedia(ctx context.Context, message json.RawMessage) ([]byte, string, error) {
	var out struct {
		Base64 string `json:"base64"`
	}
	if err := c.do(ctx, http.MethodPost, "/message/downloadmedia", c.InstanceToken,
		map[string]json.RawMessage{"message": message}, &out); err != nil {
		return nil, "", err
	}
	return DecodeDataURL(out.Base64)
}

// DecodeDataURL acepta "data:mime;base64,xxx" o base64 plano.
func DecodeDataURL(s string) ([]byte, string, error) {
	mime := ""
	if strings.HasPrefix(s, "data:") {
		if i := strings.Index(s, ","); i > 0 {
			mime = strings.TrimSuffix(strings.TrimPrefix(s[:i], "data:"), ";base64")
			s = s[i+1:]
		}
	}
	b, err := base64.StdEncoding.DecodeString(s)
	return b, mime, err
}
