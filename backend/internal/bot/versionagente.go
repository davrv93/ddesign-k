package bot

import (
	"context"
	"hash/fnv"
	"strconv"
	"strings"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// Qué versión del agente contesta a cada conversación (V1 de siempre o la V2). Se decide aquí, no en el agente, para que
// el mismo número de WhatsApp vea siempre la misma versión mientras dure la prueba A/B, y para que una asesora pueda
// fijarla a mano desde el panel.
//
// Orden de decisión:
//  1. la versión que una persona fijó en esa conversación (panel);
//  2. el número está en la lista de pruebas (`agent_v2_phones`): V2;
//  3. el ajuste global (`agent_version`): v1 | v2 | ab. En «ab», `agent_v2_percent` % de las clientas (por su número) va a V2.

const (
	AjusteVersion     = "agent_version"    // v1 | v2 | ab
	AjustePorcentaje  = "agent_v2_percent" // 0–100, solo con «ab»
	AjusteNumeros     = "agent_v2_phones"  // números separados por coma que siempre van a V2
	AjusteModoV2      = "agent_v2_modo"    // sombra | activo
	VersionPorDefecto = "v1"
)

// Politica es lo que dicen los ajustes del panel sobre las versiones del agente.
type Politica struct {
	Global  string   // v1 | v2 | ab
	Percent int      // 0–100
	Numeros []string // solo dígitos
	Modo    string   // sombra | activo
}

// PoliticaAgente lee los ajustes (con valores seguros si faltan o están mal: V1, 0 %, sombra).
func PoliticaAgente(ctx context.Context, st *store.Store) Politica {
	p := Politica{
		Global:  Normalizar(st.Setting(ctx, AjusteVersion, VersionPorDefecto), "v1", "v2", "ab"),
		Modo:    Normalizar(st.Setting(ctx, AjusteModoV2, "sombra"), "sombra", "activo"),
		Numeros: SoloDigitos(strings.Split(st.Setting(ctx, AjusteNumeros, ""), ",")),
	}
	if n, err := strconv.Atoi(strings.TrimSpace(st.Setting(ctx, AjustePorcentaje, "0"))); err == nil {
		p.Percent = max(0, min(100, n))
	}
	return p
}

// Normalizar devuelve v en minúsculas si es una de las opciones; si no, la primera (la segura).
func Normalizar(v string, opciones ...string) string {
	v = strings.ToLower(strings.TrimSpace(v))
	for _, o := range opciones {
		if v == o {
			return v
		}
	}
	return opciones[0]
}

// SoloDigitos limpia una lista de números: deja solo los dígitos y descarta los vacíos y los repetidos.
func SoloDigitos(lista []string) []string {
	var out []string
	vistos := map[string]bool{}
	for _, n := range lista {
		var b strings.Builder
		for _, r := range n {
			if r >= '0' && r <= '9' {
				b.WriteRune(r)
			}
		}
		if d := b.String(); d != "" && !vistos[d] {
			vistos[d] = true
			out = append(out, d)
		}
	}
	return out
}

// Cubeta es un número estable de 0 a 99 para una clave (el número de la clienta): la misma clienta cae siempre en la
// misma cubeta, así que sube o baja de porcentaje sin cambiar de versión a las que ya estaban dentro.
func Cubeta(clave string) int {
	h := fnv.New32a()
	_, _ = h.Write([]byte(clave))
	return int(h.Sum32() % 100)
}

// mismoNumero compara por los últimos 9 dígitos: «51987654321», «987654321» y «+51 987 654 321» son el mismo número.
func mismoNumero(a, b string) bool {
	cola := func(s string) string {
		if len(s) > 9 {
			return s[len(s)-9:]
		}
		return s
	}
	return a != "" && b != "" && cola(a) == cola(b)
}

// Resolver devuelve la versión que contesta a esta clienta y por qué («conversación», «número», «global», «ab»).
func (p Politica) Resolver(fijada, telefono string, conversacion int64) (version, porque string) {
	if fijada == "v1" || fijada == "v2" {
		return fijada, "conversación"
	}
	tel := SoloDigitos([]string{telefono})
	for _, n := range p.Numeros {
		if len(tel) == 1 && mismoNumero(n, tel[0]) {
			return "v2", "número"
		}
	}
	switch p.Global {
	case "v2":
		return "v2", "global"
	case "ab":
		clave := "c" + strconv.FormatInt(conversacion, 10)
		if len(tel) == 1 {
			clave = tel[0][max(0, len(tel[0])-9):]
		}
		if Cubeta(clave) < p.Percent {
			return "v2", "ab"
		}
		return "v1", "ab"
	}
	return "v1", "global"
}

// etiquetaAgente resume quién escribió el último turno: «v1», «v2» (habló V2) o «v2→v1» (V2 miró y habló V1).
func etiquetaAgente(r *agente.Reply) string {
	if r == nil || r.Version != "v2" {
		return "v1"
	}
	if r.V2 != nil && r.V2.Enviado == "v2" {
		return "v2"
	}
	if r.V2 != nil && r.V2.Modo == "activo" {
		return "v2→v1"
	}
	return "v2 (sombra)"
}
