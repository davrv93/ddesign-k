// Package seed carga el catálogo inicial (fotos enviadas por la tienda) la primera vez que arranca.
package seed

import (
	"context"
	"embed"
	"encoding/json"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/davrv93/ddesign-k/backend/internal/store"
)

//go:embed catalog.json images/*.jpg
var files embed.FS

type item = Item

// Item es un producto del catálogo semilla.
type Item struct {
	Code        string         `json:"code"`
	Name        string         `json:"name"`
	Category    string         `json:"category"`
	Color       string         `json:"color"`
	Price       float64        `json:"price"`
	Description string         `json:"description"`
	Tags        string         `json:"tags"`
	Sizes       map[string]int `json:"sizes"`
}

var sizeOrder = map[string]int{"XS": 0, "S": 1, "M": 2, "L": 3, "XL": 4, "XXL": 5}

// Catalog devuelve el catálogo semilla (lo usa también cmd/aicheck).
func Catalog() []Item {
	raw, _ := files.ReadFile("catalog.json")
	var items []Item
	_ = json.Unmarshal(raw, &items)
	return items
}

// Run inserta el catálogo si la tabla de productos está vacía.
func Run(ctx context.Context, st *store.Store, dataDir string) error {
	n, err := st.CountProducts(ctx)
	if err != nil || n > 0 {
		return err
	}
	items := Catalog()
	dir := filepath.Join(dataDir, "media", "products")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	for _, it := range items {
		name := strings.ToLower(it.Code) + ".jpg"
		img, err := files.ReadFile("images/" + name)
		image := ""
		if err == nil {
			if err := os.WriteFile(filepath.Join(dir, name), img, 0o644); err == nil {
				image = "/media/products/" + name
			}
		}
		p := &store.Product{Code: it.Code, Name: it.Name, Category: it.Category, Color: it.Color, Price: it.Price,
			Description: it.Description, AITags: it.Tags, Image: image, Active: true}
		for size, stock := range it.Sizes {
			p.Variants = append(p.Variants, store.Variant{Size: size, Stock: stock})
		}
		sort.Slice(p.Variants, func(i, j int) bool { return sizeOrder[p.Variants[i].Size] < sizeOrder[p.Variants[j].Size] })
		if err := st.SaveProduct(ctx, p); err != nil {
			return err
		}
	}
	log.Printf("seed: %d productos cargados", len(items))
	return nil
}
