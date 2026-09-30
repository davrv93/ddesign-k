// aicheck prueba el reconocimiento de fotos contra el catálogo semilla con la API real.
// Uso: GEMINI_API_KEY=... go run ./cmd/aicheck foto1.jpg [foto2.jpg ...]
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/ai"
	"github.com/davrv93/ddesign-k/backend/internal/seed"
)

func main() {
	model := getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
	fallback := getenv("GEMINI_FALLBACK_MODEL", "gemma-4-26b-a4b-it,gemma-4-31b-it")
	c := ai.New(os.Getenv("GEMINI_API_KEY"), model, fallback, 60*time.Second)
	var catalog []ai.CatalogEntry
	for _, it := range seed.Catalog() {
		catalog = append(catalog, ai.CatalogEntry{Code: it.Code, Name: it.Name, Color: it.Color, Category: it.Category, Description: it.Description, Tags: it.Tags})
	}
	for _, f := range os.Args[1:] {
		data, err := os.ReadFile(f)
		if err != nil {
			log.Fatal(err)
		}
		start := time.Now()
		r, err := c.MatchProduct(context.Background(), ai.Image{Mime: http.DetectContentType(data), Data: data}, catalog)
		if err != nil {
			fmt.Printf("%s: ERROR %v\n", f, err)
			continue
		}
		out, _ := json.Marshal(r)
		fmt.Printf("%s: [%s %s] %s\n", f, r.Model, time.Since(start).Round(time.Millisecond), out)
	}
}

func getenv(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}
