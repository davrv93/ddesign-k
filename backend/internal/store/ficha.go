package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
)

// Ficha técnica de una prenda: el mismo esquema que `agente/seed/fichas_producto.json` (rama feat/agente-v2,
// agente/app/ficha_producto.py). Cada atributo trae su valor y de dónde sale: «diners» (texto de la tienda), «foto»,
// «ambas», o «tienda» (lo escribió una persona en el panel). Lo que no se sabe va como null, nunca adivinado.
type Ficha struct {
	Codigo          string           `json:"codigo,omitempty"`
	Piezas          []string         `json:"piezas"`
	Atributos       map[string]*Dato `json:"atributos"`
	Detalles        []Dato           `json:"detalles"`
	ComoQueda       []Dato           `json:"como_queda"`
	Cuidados        *string          `json:"cuidados"`
	Resumen         string           `json:"resumen"`
	Discrepancias   []string         `json:"discrepancias"`
	PendienteTienda []string         `json:"pendiente_tienda"`
}

type Dato struct {
	Valor  string `json:"valor"`
	Fuente string `json:"fuente"`
}

// AtributosFicha en el orden en que se muestran.
var AtributosFicha = []string{"silueta", "largo", "escote", "mangas", "cintura", "espalda", "cierre", "tela", "forro",
	"transparencias", "estampado", "color_visto"}

// FuenteManual es la fuente de lo que se escribe a mano en el panel.
const FuenteManual = "tienda"

var fuentesValidas = map[string]bool{"diners": true, "foto": true, "ambas": true, FuenteManual: true}

// Datos cuenta cuántos de los 13 datos de la ficha (12 atributos + cuidados) están completos.
func (f *Ficha) Datos() (tiene, total int) {
	total = len(AtributosFicha) + 1
	for _, k := range AtributosFicha {
		if d := f.Atributos[k]; d != nil && strings.TrimSpace(d.Valor) != "" {
			tiene++
		}
	}
	if f.Cuidados != nil && strings.TrimSpace(*f.Cuidados) != "" {
		tiene++
	}
	return
}

const maxTexto = 600

func limpio(s string) string { return strings.TrimSpace(s) }

// NormalizarFicha valida la ficha que llega del panel y decide las fuentes comparando con la guardada (prev, puede
// ser nil): lo que no cambió conserva su fuente; lo nuevo o editado queda con fuente «tienda». La fuente que mande el
// navegador no cuenta.
func NormalizarFicha(prev, nueva *Ficha) (*Ficha, error) {
	if nueva == nil {
		return nil, errors.New("ficha vacía")
	}
	if prev == nil {
		prev = &Ficha{}
	}
	out := &Ficha{Codigo: prev.Codigo, Atributos: map[string]*Dato{}}
	for k := range nueva.Atributos {
		if !esAtributo(k) {
			return nil, fmt.Errorf("atributo desconocido %q", k)
		}
	}
	for _, k := range AtributosFicha {
		d := nueva.Atributos[k]
		if d == nil || limpio(d.Valor) == "" {
			out.Atributos[k] = nil
			continue
		}
		v := limpio(d.Valor)
		if len(v) > maxTexto {
			return nil, fmt.Errorf("%s: demasiado largo", k)
		}
		fuente := FuenteManual
		if old := prev.Atributos[k]; old != nil && limpio(old.Valor) == v && fuentesValidas[old.Fuente] {
			fuente = old.Fuente
		}
		out.Atributos[k] = &Dato{Valor: v, Fuente: fuente}
	}
	var err error
	if out.Detalles, err = datos("detalles", prev.Detalles, nueva.Detalles); err != nil {
		return nil, err
	}
	if out.ComoQueda, err = datos("cómo queda", prev.ComoQueda, nueva.ComoQueda); err != nil {
		return nil, err
	}
	if out.Piezas, err = textos("piezas", nueva.Piezas); err != nil {
		return nil, err
	}
	if out.Discrepancias, err = textos("contradicciones", nueva.Discrepancias); err != nil {
		return nil, err
	}
	if out.PendienteTienda, err = textos("pendientes", nueva.PendienteTienda); err != nil {
		return nil, err
	}
	if nueva.Cuidados != nil && limpio(*nueva.Cuidados) != "" {
		c := limpio(*nueva.Cuidados)
		if len(c) > maxTexto {
			return nil, errors.New("cuidados: demasiado largo")
		}
		out.Cuidados = &c
	}
	out.Resumen = limpio(nueva.Resumen)
	if len(out.Resumen) > 2*maxTexto {
		return nil, errors.New("resumen: demasiado largo")
	}
	return out, nil
}

func esAtributo(k string) bool {
	for _, a := range AtributosFicha {
		if a == k {
			return true
		}
	}
	return false
}

func datos(campo string, prev, nuevos []Dato) ([]Dato, error) {
	if len(nuevos) > 30 {
		return nil, fmt.Errorf("%s: máximo 30", campo)
	}
	antes := map[string]string{}
	for _, d := range prev {
		antes[limpio(d.Valor)] = d.Fuente
	}
	out := []Dato{}
	for _, d := range nuevos {
		v := limpio(d.Valor)
		if v == "" {
			continue
		}
		if len(v) > maxTexto {
			return nil, fmt.Errorf("%s: demasiado largo", campo)
		}
		f, ok := antes[v]
		if !ok || !fuentesValidas[f] {
			f = FuenteManual
		}
		out = append(out, Dato{Valor: v, Fuente: f})
	}
	return out, nil
}

func textos(campo string, xs []string) ([]string, error) {
	if len(xs) > 30 {
		return nil, fmt.Errorf("%s: máximo 30", campo)
	}
	out := []string{}
	for _, x := range xs {
		if x = limpio(x); x != "" {
			if len(x) > maxTexto {
				return nil, fmt.Errorf("%s: demasiado largo", campo)
			}
			out = append(out, x)
		}
	}
	return out, nil
}

func parseFicha(raw string) *Ficha {
	if strings.TrimSpace(raw) == "" {
		return nil
	}
	f := &Ficha{}
	if err := json.Unmarshal([]byte(raw), f); err != nil {
		return nil
	}
	return f
}

// GetFicha devuelve la ficha del producto de la empresa (nil si no tiene) o ErrNotFound si el producto no es suyo.
func (s *Store) GetFicha(ctx context.Context, productID int64) (*Ficha, error) {
	var raw string
	err := s.DB.QueryRowContext(ctx, `SELECT ficha FROM products WHERE id=? AND tenant_id=?`, productID, s.tid).Scan(&raw)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return parseFicha(raw), nil
}

// SetFicha guarda la ficha tal cual (ya normalizada) en un producto de la empresa.
func (s *Store) SetFicha(ctx context.Context, productID int64, f *Ficha) error {
	raw := ""
	if f != nil {
		b, err := json.Marshal(f)
		if err != nil {
			return err
		}
		raw = string(b)
	}
	var one int
	if err := s.DB.QueryRowContext(ctx, `SELECT 1 FROM products WHERE id=? AND tenant_id=?`, productID, s.tid).Scan(&one); err != nil {
		return ErrNotFound
	}
	_, err := s.DB.ExecContext(ctx, `UPDATE products SET ficha=?, updated_at=? WHERE id=? AND tenant_id=?`, raw, now(), productID, s.tid)
	return err
}

// SetFichaPorCodigo carga una ficha (del JSON del agente) en el producto de ese código; ErrNotFound si no existe.
func (s *Store) SetFichaPorCodigo(ctx context.Context, code string, f *Ficha) error {
	p, err := s.GetProductByCode(ctx, code)
	if err != nil {
		return err
	}
	return s.SetFicha(ctx, p.ID, f)
}
