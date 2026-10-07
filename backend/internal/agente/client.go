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
	// DesdeAnuncio: la conversación empezó en un anuncio de clic a WhatsApp. Solo entonces «este vestido»
	// es el del anuncio. Anuncio es el título del anuncio, si lo trae.
	DesdeAnuncio bool   `json:"desde_anuncio,omitempty"`
	Anuncio      string `json:"anuncio,omitempty"`
	// Memoria es la ficha de la conversación (lo que ya sabemos de la clienta y la pregunta pendiente). La
	// guarda el bot con la conversación y la devuelve tal cual; el agente la actualiza en cada mensaje.
	Memoria json.RawMessage `json:"memoria,omitempty"`
	// Perfil: lo que sabemos de la clienta por sus pedidos anteriores (clienta que vuelve).
	Perfil *Perfil `json:"perfil,omitempty"`
	// Version del agente para este turno ("v1" | "v2") y, con V2, su Modo ("sombra" | "activo"). Los decide el bot por
	// conversación (ajustes del panel); vacío = lo que arrancó el agente.
	Version string `json:"version,omitempty"`
	Modo    string `json:"modo,omitempty"`
}

// Perfil de una clienta que ya compró: el agente prellena la talla y puede mencionarlo con naturalidad.
type Perfil struct {
	Nombre    string   `json:"nombre,omitempty"`
	Tallas    []string `json:"tallas,omitempty"`    // la más reciente primero
	Productos []string `json:"productos,omitempty"` // códigos, el más reciente primero
	Pedidos   int      `json:"pedidos"`
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
	// Sentimiento (-1..1) y urgencia (0..1) del mensaje, por reglas (agente/app/animo.py). Alimentan la
	// Capa de Juicio del bot. Cero si el agente no los calculó (llamadas viejas).
	Sentimiento float64 `json:"sentimiento"`
	Urgencia    float64 `json:"urgencia"`
	// Memoria actualizada: se guarda y se manda tal cual en el siguiente mensaje.
	Memoria json.RawMessage `json:"memoria,omitempty"`
	// Foto sólo viene en las respuestas de /foto.
	Foto *PhotoResult `json:"foto"`
	// Comercial: la intención comercial del turno (consulta_material, objecion_precio…). La usa el CRM (Kommo) para
	// anotar el hito; no cambia lo que hace el bot.
	Comercial *Comercial `json:"comercial,omitempty"`
	// Version con la que contestó el agente y, si fue V2, lo que hizo (agente/app/v2).
	Version string  `json:"version,omitempty"`
	V2      *V2Info `json:"v2,omitempty"`
}

// V2Info es lo mínimo de la traza de V2 que le sirve al bot: en qué modo corrió y quién escribió el texto.
type V2Info struct {
	Modo    string `json:"modo"`
	Enviado string `json:"enviado"` // "v2" = habló V2; "v1" = V2 miró y habló V1
}

type Comercial struct {
	Intent    string  `json:"intent"`
	Confianza float64 `json:"confianza"`
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
	// Igual que en Request: sin anuncio, el agente no asume prenda.
	DesdeAnuncio bool            `json:"desde_anuncio,omitempty"`
	Anuncio      string          `json:"anuncio,omitempty"`
	Memoria      json.RawMessage `json:"memoria,omitempty"`
	Perfil       *Perfil         `json:"perfil,omitempty"`
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

// Metricas devuelve las métricas por versión del agente (`versiones` de GET /metricas): turnos, latencia, acuerdo con V1…
func (c *Client) Metricas(ctx context.Context) (json.RawMessage, error) {
	hreq, err := http.NewRequestWithContext(ctx, http.MethodGet, c.BaseURL+"/metricas", nil)
	if err != nil {
		return nil, err
	}
	res, err := c.HTTP.Do(hreq)
	if err != nil {
		return nil, err
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("agente: HTTP %d", res.StatusCode)
	}
	var m struct {
		Versiones json.RawMessage `json:"versiones"`
	}
	if err := json.NewDecoder(res.Body).Decode(&m); err != nil {
		return nil, fmt.Errorf("agente: %w", err)
	}
	if len(m.Versiones) == 0 {
		return json.RawMessage(`{}`), nil
	}
	return m.Versiones, nil
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
