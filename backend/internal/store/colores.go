package store

import (
	"encoding/json"
	"strings"
)

// Colores de la tienda agrupados para el guion de noche («¿Prefieres llamar un poco más la atención, o verte elegante,
// sofisticada y más discreta?»): «vibrante» o «sobrio». La admin los cambia en Ajustes → Colores (api/colores.go); el bot
// manda el mapa al agente en cada turno (agente.Request.ColoresGrupos) y el agente ofrece prendas en esos colores.
//
// Se guarda como ajuste SettingColores (JSON {color normalizado: grupo}). Lo que la tienda no fijó vale la propuesta por
// defecto (coloresDefecto, la misma que agente/seed/colores_grupos.json: deploy/catalogo-baruka-pe/colores_grupos.py --go).

const SettingColores = "colores_grupos"

// GruposDeColor: los valores válidos ("" = sin grupo).
var GruposDeColor = map[string]bool{"": true, "sobrio": true, "vibrante": true}

// coloresDefecto: generado con `python3 colores_grupos.py --go` (deploy/catalogo-baruka-pe). No editar a mano.
var coloresDefecto = map[string]string{
	"a rayas":             "vibrante",
	"acero":               "sobrio",
	"amarillo":            "vibrante",
	"animal":              "vibrante",
	"azul cobalto":        "vibrante",
	"azul noche":          "sobrio",
	"azul petroleo":       "sobrio",
	"azul petrolio":       "sobrio",
	"azulino":             "vibrante",
	"barney":              "vibrante",
	"beige":               "sobrio",
	"beige oscuro":        "sobrio",
	"black":               "sobrio",
	"blanco":              "sobrio",
	"camel":               "sobrio",
	"camell":              "sobrio",
	"celeste":             "vibrante",
	"celeste bebe":        "vibrante",
	"celeste oscuro":      "vibrante",
	"champagne":           "sobrio",
	"chicle":              "vibrante",
	"clara":               "",
	"color personalizado": "",
	"dalia":               "vibrante",
	"dorado":              "vibrante",
	"fucsia":              "vibrante",
	"geometrico":          "vibrante",
	"green":               "vibrante",
	"gris":                "sobrio",
	"italia":              "",
	"italiano":            "",
	"lila":                "vibrante",
	"maiz":                "vibrante",
	"marron":              "sobrio",
	"mostaza":             "vibrante",
	"negro":               "sobrio",
	"nude":                "sobrio",
	"palo rosa":           "vibrante",
	"plateado":            "sobrio",
	"rojo":                "vibrante",
	"rosa":                "vibrante",
	"terracota":           "sobrio",
	"turquesa":            "vibrante",
	"turqueza":            "vibrante",
	"verde":               "vibrante",
	"verde agua":          "vibrante",
	"verde botella":       "sobrio",
	"verde cobalto":       "sobrio",
	"verde jade":          "vibrante",
	"verde olivo":         "sobrio",
	"vino":                "sobrio",
}

// NormalizaColor: «Azul Noche» → «azul noche», «Marrón.» → «marron» (misma regla que app/colores.py del agente).
func NormalizaColor(c string) string {
	c = strings.ToLower(strings.TrimSpace(c))
	c = strings.NewReplacer("á", "a", "é", "e", "í", "i", "ó", "o", "ú", "u", "ü", "u", "ñ", "n").Replace(c)
	return strings.Trim(strings.Join(strings.Fields(c), " "), ". ")
}

// ColoresGrupos devuelve el mapa que ve el agente: la propuesta por defecto pisada por lo que guardó la tienda.
func ColoresGrupos(guardado string) map[string]string {
	out := make(map[string]string, len(coloresDefecto))
	for k, v := range coloresDefecto {
		out[k] = v
	}
	var propio map[string]string
	if guardado != "" && json.Unmarshal([]byte(guardado), &propio) == nil {
		for k, v := range propio {
			if GruposDeColor[v] {
				out[NormalizaColor(k)] = v
			}
		}
	}
	return out
}

// ColoresGruposPropios: solo lo que la tienda fijó (clave normalizada → grupo); vacío si nunca guardó nada.
func ColoresGruposPropios(guardado string) map[string]string {
	out := map[string]string{}
	if guardado != "" {
		_ = json.Unmarshal([]byte(guardado), &out)
	}
	return out
}
