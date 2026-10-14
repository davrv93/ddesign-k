package api

import (
	"encoding/json"
	"net/http"
	"sort"
	"strings"

	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// Ajustes → Colores: qué colores son «sobrios» y cuáles «vibrantes» (guion de noche). La lógica y el valor por defecto
// viven en store/colores.go; aquí solo la lista para el panel y el guardado.

func init() { settingDefaults[store.SettingColores] = "" }

type colorTienda struct {
	Color     string `json:"color"`     // como lo escribe la tienda en sus productos
	Clave     string `json:"clave"`     // normalizado: la clave del ajuste
	Grupo     string `json:"grupo"`     // "" | sobrio | vibrante
	Productos int    `json:"productos"` // cuántos productos activos lo llevan
	Propio    bool   `json:"propio"`    // true: lo fijó la tienda; false: propuesta por defecto
}

// GET /api/colores: los colores de los productos activos con su grupo, los más usados primero.
func (s *Server) listColores(w http.ResponseWriter, r *http.Request) {
	st := s.st(r)
	prods, err := st.ListProducts(r.Context(), true)
	if err != nil {
		writeErr(w, 500, err.Error())
		return
	}
	guardado := st.Setting(r.Context(), store.SettingColores, "")
	propio, efectivo := store.ColoresGruposPropios(guardado), store.ColoresGrupos(guardado)
	porClave := map[string]*colorTienda{}
	for _, p := range prods {
		for _, c := range strings.Split(p.Color, ",") {
			k := store.NormalizaColor(c)
			if k == "" {
				continue
			}
			if porClave[k] == nil {
				_, fijado := propio[k]
				porClave[k] = &colorTienda{Color: strings.TrimSpace(c), Clave: k, Grupo: efectivo[k], Propio: fijado}
			}
			porClave[k].Productos++
		}
	}
	out := make([]colorTienda, 0, len(porClave))
	for _, c := range porClave {
		out = append(out, *c)
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Productos != out[j].Productos {
			return out[i].Productos > out[j].Productos
		}
		return out[i].Clave < out[j].Clave
	})
	writeJSON(w, 200, out)
}

// PUT /api/colores {"grupos": {color: "sobrio"|"vibrante"|""}} (solo admin): fija el grupo de esos colores y devuelve la lista.
func (s *Server) putColores(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Grupos map[string]string `json:"grupos"`
	}
	if err := readJSON(r, &in); err != nil || in.Grupos == nil {
		writeErr(w, 400, "datos inválidos: se espera {\"grupos\": {color: sobrio | vibrante | \"\"}}")
		return
	}
	st := s.st(r)
	actual := store.ColoresGruposPropios(st.Setting(r.Context(), store.SettingColores, ""))
	for k, v := range in.Grupos {
		if !store.GruposDeColor[v] {
			writeErr(w, 400, "grupo inválido: "+v)
			return
		}
		if k = store.NormalizaColor(k); k != "" {
			actual[k] = v
		}
	}
	b, _ := json.Marshal(actual)
	if err := st.SetSetting(r.Context(), store.SettingColores, string(b)); err != nil {
		writeErr(w, 500, err.Error())
		return
	}
	s.hub.PublishTo(s.tenantID(r), "settings")
	s.listColores(w, r)
}
