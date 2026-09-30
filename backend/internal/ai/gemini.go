// Package ai usa la API de Gemini (capa gratuita) con Gemma como respaldo.
package ai

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"time"
)

const defaultEndpoint = "https://generativelanguage.googleapis.com/v1beta/models/"

type Client struct {
	Endpoint string
	APIKey   string
	Model    string
	Fallback string
	HTTP     *http.Client
}

func New(apiKey, model, fallback string, timeout time.Duration) *Client {
	return &Client{Endpoint: defaultEndpoint, APIKey: apiKey, Model: model, Fallback: fallback, HTTP: &http.Client{Timeout: timeout}}
}

func (c *Client) Enabled() bool { return c != nil && c.APIKey != "" }

type Image struct {
	Mime string
	Data []byte
}

type part struct {
	Text       string      `json:"text,omitempty"`
	InlineData *inlineData `json:"inline_data,omitempty"`
}

type inlineData struct {
	MimeType string `json:"mime_type"`
	Data     string `json:"data"`
}

// isGemma: los modelos Gemma no aceptan responseMimeType JSON ni system_instruction.
func isGemma(model string) bool { return strings.HasPrefix(model, "gemma") }

// Generate llama al modelo principal y, si falla (cuota, 5xx, timeout), al de respaldo.
// Devuelve el texto y el modelo que respondió.
func (c *Client) Generate(ctx context.Context, prompt string, images []Image, wantJSON bool) (string, string, error) {
	if !c.Enabled() {
		return "", "", errors.New("IA desactivada: falta GEMINI_API_KEY")
	}
	// Fallback admite varios modelos separados por coma, en orden de preferencia.
	models := []string{c.Model}
	for _, m := range strings.Split(c.Fallback, ",") {
		if m = strings.TrimSpace(m); m != "" && m != c.Model {
			models = append(models, m)
		}
	}
	var lastErr error
	for _, m := range models {
		out, err := c.call(ctx, m, prompt, images, wantJSON)
		if err == nil && strings.TrimSpace(out) != "" {
			return out, m, nil
		}
		if err == nil {
			err = errors.New("respuesta vacía")
		}
		log.Printf("ia: modelo %s falló: %v", m, err)
		lastErr = err
		if ctx.Err() != nil {
			break
		}
	}
	return "", "", lastErr
}

func (c *Client) call(ctx context.Context, model, prompt string, images []Image, wantJSON bool) (string, error) {
	parts := []part{}
	for _, img := range images {
		parts = append(parts, part{InlineData: &inlineData{MimeType: img.Mime, Data: base64.StdEncoding.EncodeToString(img.Data)}})
	}
	parts = append(parts, part{Text: prompt})
	// Gemma razona antes de responder y esos tokens cuentan contra el tope.
	gen := map[string]any{"temperature": 0.2, "maxOutputTokens": 4096}
	if wantJSON && !isGemma(model) {
		gen["responseMimeType"] = "application/json"
	}
	body, _ := json.Marshal(map[string]any{
		"contents":         []map[string]any{{"role": "user", "parts": parts}},
		"generationConfig": gen,
	})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.Endpoint+model+":generateContent", bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-goog-api-key", c.APIKey)
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if resp.StatusCode != http.StatusOK {
		msg := string(raw)
		if len(msg) > 300 {
			msg = msg[:300]
		}
		return "", fmt.Errorf("HTTP %d: %s", resp.StatusCode, msg)
	}
	var out struct {
		Candidates []struct {
			Content struct {
				Parts []struct {
					Text    string `json:"text"`
					Thought bool   `json:"thought"`
				} `json:"parts"`
			} `json:"content"`
		} `json:"candidates"`
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		return "", err
	}
	var sb strings.Builder
	for _, cand := range out.Candidates {
		for _, p := range cand.Content.Parts {
			if !p.Thought {
				sb.WriteString(p.Text)
			}
		}
		break
	}
	return sb.String(), nil
}

// ExtractJSON recorta el primer objeto JSON de un texto (Gemma suele envolverlo en ```json).
func ExtractJSON(s string) string {
	start := strings.Index(s, "{")
	end := strings.LastIndex(s, "}")
	if start < 0 || end < start {
		return s
	}
	return s[start : end+1]
}
