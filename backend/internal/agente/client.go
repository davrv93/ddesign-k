// Package agente habla con el servicio agente (Python): clasifica el texto libre con
// embeddings locales y redacta la respuesta con DeepSeek apoyándose en el catálogo.
package agente

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"
)

type Client struct {
	BaseURL string
	HTTP    *http.Client
}

// New devuelve nil si no hay URL: el bot sigue sólo con Gemini.
func New(baseURL string, timeout time.Duration) *Client {
	baseURL = strings.TrimRight(baseURL, "/")
	if baseURL == "" {
		return nil
	}
	return &Client{BaseURL: baseURL, HTTP: &http.Client{Timeout: timeout}}
}

type Turn struct {
	Rol   string `json:"rol"` // cliente | bot | asesora
	Texto string `json:"texto"`
}

type Request struct {
	Mensaje   string `json:"mensaje"`
	Historial []Turn `json:"historial"`
	Cliente   string `json:"cliente"`
	Estado    string `json:"estado"`
	Negocio   string `json:"negocio"`
}

type Reply struct {
	Intencion string  `json:"intencion"`
	Confianza float64 `json:"confianza"`
	// Accion: responder | catalogo | foto | pedido_estado | asesora | codigo
	Accion    string `json:"accion"`
	Codigo    string `json:"codigo"`
	Respuesta string `json:"respuesta"`
	ModeloLLM string `json:"modelo_llm"`
	// Sugerencias son prendas para ofrecer con foto después del texto.
	Sugerencias []Sugerencia `json:"sugerencias"`
}

type Sugerencia struct {
	Codigo string `json:"codigo"`
	Fuente string `json:"fuente"` // seed (tienda) | catalogo100
	Imagen string `json:"imagen"` // /media/products/... (backend) o /media/catalogo/... (agente)
	Pie    string `json:"pie"`
}

func (c *Client) Chat(ctx context.Context, req Request) (*Reply, error) {
	body, _ := json.Marshal(req)
	hreq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+"/chat", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	hreq.Header.Set("Content-Type", "application/json")
	res, err := c.HTTP.Do(hreq)
	if err != nil {
		return nil, err
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("agente: HTTP %d", res.StatusCode)
	}
	var r Reply
	if err := json.NewDecoder(res.Body).Decode(&r); err != nil {
		return nil, fmt.Errorf("agente: %w", err)
	}
	return &r, nil
}
