package ai

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
)

// CatalogEntry es lo que el modelo ve de cada producto para comparar.
type CatalogEntry struct {
	Code        string
	Name        string
	Color       string
	Category    string
	Description string
	Tags        string
}

type MatchResult struct {
	Code         string   `json:"code"`
	Confidence   float64  `json:"confidence"`
	Seen         string   `json:"seen"`
	Alternatives []string `json:"alternatives"`
	Model        string   `json:"-"`
}

// MatchProduct identifica qué producto del catálogo aparece en la foto del cliente.
func (c *Client) MatchProduct(ctx context.Context, img Image, catalog []CatalogEntry) (*MatchResult, error) {
	var sb strings.Builder
	for _, e := range catalog {
		fmt.Fprintf(&sb, "- %s | %s | color: %s | tipo: %s | %s | %s\n", e.Code, e.Name, e.Color, e.Category, e.Description, e.Tags)
	}
	prompt := `Eres asistente de una tienda de ropa femenina. Un cliente envió la foto adjunta por WhatsApp
preguntando por una prenda. Compárala con el catálogo y decide cuál producto es (mismo modelo: corte, escote,
mangas, largo, color y estampado). Si ninguno coincide claramente, usa code "" y baja confianza.

CATÁLOGO (código | nombre | color | tipo | descripción | etiquetas):
` + sb.String() + `
Responde SOLO un JSON con esta forma:
{"code":"<código o vacío>","confidence":<0 a 1>,"seen":"<descripción breve en español de la prenda de la foto>","alternatives":["<hasta 3 códigos parecidos>"]}`
	out, model, err := c.Generate(ctx, prompt, []Image{img}, true)
	if err != nil {
		return nil, err
	}
	var r MatchResult
	if err := json.Unmarshal([]byte(ExtractJSON(out)), &r); err != nil {
		return nil, fmt.Errorf("respuesta no es JSON: %w (%q)", err, out)
	}
	r.Code = strings.ToUpper(strings.TrimSpace(r.Code))
	r.Model = model
	return &r, nil
}

type Description struct {
	Name        string `json:"name"`
	Color       string `json:"color"`
	Category    string `json:"category"`
	Description string `json:"description"`
	Tags        string `json:"tags"`
}

// DescribeProduct genera nombre, color y etiquetas visuales para una foto de catálogo.
func (c *Client) DescribeProduct(ctx context.Context, img Image) (*Description, error) {
	prompt := `Describe la prenda principal de la foto para un catálogo de ropa femenina en español (Perú).
Responde SOLO JSON: {"name":"<nombre comercial corto>","color":"<color principal>","category":"<vestido corto|vestido midi|vestido largo|blusa|pantalón|conjunto|otro>",
"description":"<una frase atractiva>","tags":"<10 a 15 palabras clave visuales separadas por coma: corte, escote, mangas, tela, estampado, largo, detalles>"}`
	out, _, err := c.Generate(ctx, prompt, []Image{img}, true)
	if err != nil {
		return nil, err
	}
	var d Description
	if err := json.Unmarshal([]byte(ExtractJSON(out)), &d); err != nil {
		return nil, fmt.Errorf("respuesta no es JSON: %w", err)
	}
	return &d, nil
}

type Intent struct {
	Intent string `json:"intent"`
	Code   string `json:"code"`
	Size   string `json:"size"`
	Qty    int    `json:"qty"`
	Reply  string `json:"reply"`
}

// ClassifyIntent interpreta un texto libre del cliente cuando el menú no lo reconoce.
func (c *Client) ClassifyIntent(ctx context.Context, business, state, text string, sizes []string) (*Intent, error) {
	prompt := fmt.Sprintf(`Eres el asistente de WhatsApp de la tienda de ropa "%s" (Perú). Estado actual de la conversación: "%s".
Tallas válidas en este momento: %s.
Mensaje del cliente: %q

Clasifica la intención en una de: "catalogo" (quiere ver modelos/precios), "foto" (quiere consultar un modelo o dice que enviará foto),
"pedido_estado" (pregunta por su pedido/envío), "asesora" (quiere hablar con una persona), "saludo", "si", "no",
"talla" (indica una talla), "codigo" (menciona un código de producto como V05), "cantidad", "otro".
Si es "otro" escribe en "reply" una respuesta breve, cálida y en español peruano (máx. 2 frases) sin inventar precios ni stock.
Responde SOLO JSON: {"intent":"...","code":"","size":"","qty":0,"reply":""}`, business, state, strings.Join(sizes, ", "), text)
	out, _, err := c.Generate(ctx, prompt, nil, true)
	if err != nil {
		return nil, err
	}
	var in Intent
	if err := json.Unmarshal([]byte(ExtractJSON(out)), &in); err != nil {
		return nil, fmt.Errorf("respuesta no es JSON: %w", err)
	}
	in.Intent = strings.ToLower(strings.TrimSpace(in.Intent))
	return &in, nil
}
