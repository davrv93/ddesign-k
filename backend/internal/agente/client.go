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
	// Etapa comercial en la que venía la conversación: prospeccion | seguimiento | cierre | venta_confirmada.
	Etapa        string `json:"etapa,omitempty"`
	Conversacion string `json:"conversacion,omitempty"` // id para el registro de decisiones del agente
	// Producto y Talla del pedido en curso (estados de talla, confirmación, pago y dirección).
	Producto string `json:"producto,omitempty"`
	Talla    string `json:"talla,omitempty"`
}

type Reply struct {
	Intencion string  `json:"intencion"`
	Confianza float64 `json:"confianza"`
	// Accion: responder | catalogo | foto | pedido_estado | asesora | codigo | pedido
	Accion string `json:"accion"`
	Codigo string `json:"codigo"`
	// Talla viene con accion «pedido»: la clienta ya eligió modelo y talla («el Kabanova rojo en L»).
	Talla     string `json:"talla"`
	Respuesta string `json:"respuesta"`
	ModeloLLM string `json:"modelo_llm"`
	// Sugerencias son prendas para ofrecer con foto después del texto.
	Sugerencias []Sugerencia `json:"sugerencias"`
	// Etapa comercial en la que queda la conversación; se devuelve tal cual en el siguiente mensaje.
	Etapa string `json:"etapa"`
	// Foto sólo viene en las respuestas de /foto.
	Foto *PhotoResult `json:"foto"`
}

type Sugerencia struct {
	Codigo string `json:"codigo"`
	Fuente string `json:"fuente"` // seed (tienda) | catalogo100
	Imagen string `json:"imagen"` // /media/products/... (backend) o /media/catalogo/... (agente)
	Pie    string `json:"pie"`
}

// PhotoRequest pide buscar en el catálogo la prenda de una foto.
type PhotoRequest struct {
	ImagenB64 string `json:"imagen_b64"`
	Mensaje   string `json:"mensaje"`
	Historial []Turn `json:"historial"`
	Cliente   string `json:"cliente"`
	Estado    string `json:"estado"`
	Negocio   string `json:"negocio"`
	Etapa     string `json:"etapa,omitempty"`
}

// PhotoResult dice qué tan seguro está el agente de haber encontrado la prenda.
type PhotoResult struct {
	Nivel     string  `json:"nivel"` // exacto | parecido | ninguno
	Caso      string  `json:"caso"`  // online | sucursal | agotado | parecido | ninguno
	Codigo    string  `json:"codigo"`
	Similitud float64 `json:"similitud"`
}

func (c *Client) Chat(ctx context.Context, req Request) (*Reply, error) {
	return c.post(ctx, "/chat", req)
}

func (c *Client) Photo(ctx context.Context, req PhotoRequest) (*Reply, error) {
	return c.post(ctx, "/foto", req)
}

func (c *Client) post(ctx context.Context, path string, req any) (*Reply, error) {
	body, _ := json.Marshal(req)
	hreq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+path, bytes.NewReader(body))
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
