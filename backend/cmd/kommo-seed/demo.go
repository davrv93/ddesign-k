package main

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/kommo"
)

// fila de la demo: una clienta inventada, en una etapa y con una temperatura. Las prendas salen del catálogo real.
type fila struct {
	nombre, canal, etapa, temp string
	ocasion                    string
	dias                       int    // días hasta el evento (0 = no lo dijo)
	horario                    string // dia | noche
	talla                      string
	intencion                  string
	citaDias                   int // cita para probarse dentro de N días (0 = sin cita)
	citaHora                   string
	envio, ciudad              string
	final                      string // "" | ganado | perdido
	anuncio                    bool
	prenda                     int // -1 = todavía sin prenda
}

// Clientas obviamente ficticias: apellidos «Demo», «Prueba», «Ejemplo», «Ficticia». Teléfonos +51 900 000 0xx
// (rango sin asignar a ninguna clienta real de la tienda).
var filas = []fila{
	{nombre: "Ana Demo", canal: "whatsapp", etapa: "prospeccion", temp: "frio", prenda: -1},
	{nombre: "Beatriz Prueba", canal: "web", etapa: "prospeccion", temp: "frio", ocasion: "cumpleaños", prenda: -1},
	{nombre: "Carla Ejemplo", canal: "whatsapp", etapa: "prospeccion", temp: "tibio", ocasion: "graduación", dias: 25, horario: "noche", anuncio: true, prenda: -1},
	{nombre: "Diana Ficticia", canal: "whatsapp", etapa: "prospeccion", temp: "frio", ocasion: "cena de aniversario", dias: 40, horario: "noche", prenda: -1},
	{nombre: "Elena Demo", canal: "web", etapa: "prospeccion", temp: "tibio", ocasion: "bautizo", dias: 18, horario: "dia", prenda: -1},
	{nombre: "Fiorella Prueba", canal: "whatsapp", etapa: "prospeccion", temp: "frio", intencion: "consulta_ubicacion", prenda: -1},
	{nombre: "Gabriela Demo", canal: "whatsapp", etapa: "seguimiento", temp: "tibio", ocasion: "boda", dias: 20, horario: "noche", intencion: "consulta_material", prenda: 0},
	{nombre: "Hilda Prueba", canal: "web", etapa: "seguimiento", temp: "caliente", ocasion: "quinceañero", dias: 6, horario: "noche", intencion: "consulta_precio", prenda: 1},
	{nombre: "Irene Ejemplo", canal: "whatsapp", etapa: "seguimiento", temp: "frio", ocasion: "cóctel de empresa", dias: 45, horario: "noche", intencion: "consulta_color", prenda: 2},
	{nombre: "Julia Demo", canal: "whatsapp", etapa: "seguimiento", temp: "tibio", ocasion: "baby shower", dias: 15, horario: "dia", talla: "S", intencion: "consulta_talla", prenda: 3},
	{nombre: "Karen Prueba", canal: "whatsapp", etapa: "seguimiento", temp: "caliente", ocasion: "boda", dias: 4, horario: "noche", intencion: "consulta_disponibilidad", anuncio: true, prenda: 99},
	{nombre: "Laura Ficticia", canal: "web", etapa: "seguimiento", temp: "tibio", ocasion: "graduación", dias: 12, horario: "noche", intencion: "objecion_precio", prenda: 4},
	{nombre: "Mónica Demo", canal: "whatsapp", etapa: "cierre", temp: "caliente", ocasion: "boda", dias: 7, horario: "noche", talla: "M", citaDias: 2, citaHora: "17:00", intencion: "intencion_compra", prenda: 0},
	{nombre: "Nadia Prueba", canal: "whatsapp", etapa: "cierre", temp: "caliente", ocasion: "cumpleaños", dias: 5, horario: "noche", talla: "L", citaDias: 1, citaHora: "11:00", intencion: "consulta_ubicacion", prenda: 5},
	{nombre: "Olga Ejemplo", canal: "web", etapa: "cierre", temp: "tibio", ocasion: "cena de aniversario", dias: 14, horario: "noche", talla: "M", intencion: "intencion_compra", prenda: 6},
	{nombre: "Paola Demo", canal: "whatsapp", etapa: "cierre", temp: "caliente", ocasion: "graduación", dias: 3, horario: "noche", talla: "S", citaDias: 1, citaHora: "16:00", anuncio: true, prenda: 99},
	{nombre: "Rocío Prueba", canal: "whatsapp", etapa: "cierre", temp: "tibio", ocasion: "bautizo", dias: 10, horario: "dia", talla: "M", intencion: "objecion", prenda: 7},
	{nombre: "Sandra Demo", canal: "whatsapp", etapa: "venta_confirmada", temp: "caliente", ocasion: "boda", dias: 8, horario: "noche", talla: "M", envio: "lima", ciudad: "lima", prenda: 1},
	{nombre: "Tania Prueba", canal: "whatsapp", etapa: "venta_confirmada", temp: "tibio", ocasion: "cumpleaños", dias: 12, horario: "noche", talla: "L", envio: "provincia", ciudad: "arequipa", intencion: "consulta_pago", prenda: 8},
	{nombre: "Úrsula Ejemplo", canal: "web", etapa: "venta_confirmada", temp: "caliente", ocasion: "graduación", dias: 5, horario: "noche", talla: "S", envio: "lima", prenda: 2},
	{nombre: "Valeria Demo", canal: "whatsapp", etapa: "venta_confirmada", temp: "caliente", ocasion: "quinceañero", dias: 9, horario: "noche", talla: "M", envio: "provincia", ciudad: "trujillo", anuncio: true, prenda: 99},
	{nombre: "Wendy Prueba", canal: "whatsapp", etapa: "venta_confirmada", temp: "caliente", ocasion: "boda", dias: 6, horario: "noche", talla: "M", envio: "lima", final: "ganado", prenda: 3},
	{nombre: "Ximena Ejemplo", canal: "whatsapp", etapa: "venta_confirmada", temp: "tibio", ocasion: "cena de aniversario", dias: 11, horario: "noche", talla: "S", envio: "provincia", ciudad: "cusco", final: "ganado", prenda: 4},
	{nombre: "Yolanda Demo", canal: "whatsapp", etapa: "venta_confirmada", temp: "frio", ocasion: "cóctel de empresa", dias: 30, horario: "noche", talla: "L", envio: "lima", final: "perdido", prenda: 5},
}

var meses = [...]string{"ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"}

// Demo arma los eventos de demostración. La prenda «99» es el vestido del anuncio (V42) si está en el catálogo.
// Una venta perdida son dos eventos: confirmada y luego cancelada desde el tablero.
func Demo(cat map[string]producto, hoy time.Time) []kommo.Evento {
	lima := time.FixedZone("America/Lima", -5*3600)
	hoy = hoy.In(lima)
	var codigos []string
	for c := range cat {
		if c != "V42" {
			codigos = append(codigos, c)
		}
	}
	sort.Strings(codigos)
	var out []kommo.Evento
	for i, f := range filas {
		n := i + 1
		ev := kommo.Evento{Canal: f.canal, Clave: fmt.Sprintf("demo:%02d", n), Nombre: f.nombre, Etapa: f.etapa,
			Intencion: f.intencion, Anuncio: f.anuncio, Demo: true, Sesion: 1}
		if f.canal == "whatsapp" {
			ev.Telefono = fmt.Sprintf("519000000%02d", n)
		}
		if f.anuncio {
			ev.AnuncioTitulo = "Vestido Gala Capa Azul V42"
		}
		mem := map[string]any{"temperatura": f.temp}
		sab := map[string]any{}
		if f.ocasion != "" {
			sab["ocasion"] = f.ocasion
		}
		if f.dias > 0 {
			ev := hoy.AddDate(0, 0, f.dias)
			sab["fecha_iso"] = ev.Format("2006-01-02")
			mem["temperatura_motivo"] = fmt.Sprintf("evento el %d-%s (en %d días)", ev.Day(), meses[ev.Month()-1], f.dias)
		}
		if f.horario != "" {
			sab["horario"] = f.horario
		}
		if f.talla != "" {
			sab["talla"] = f.talla
		}
		if f.envio != "" {
			sab["envio"] = f.envio
		}
		if f.ciudad != "" {
			sab["ciudad"] = f.ciudad
		}
		if f.citaDias > 0 {
			sab["cita"] = hoy.AddDate(0, 0, f.citaDias).Format("2006-01-02") + "T" + f.citaHora
		}
		var p producto
		hay := false
		switch {
		case f.prenda == 99:
			p, hay = cat["V42"]
		case f.prenda >= 0 && len(codigos) > 0:
			p, hay = cat[codigos[(f.prenda*3+i)%len(codigos)]], true
		}
		if hay {
			mem["producto"] = p.Code
			ev.Mostrados = []string{p.Code}
		}
		if f.anuncio {
			mem["llego_por"] = "anuncio V42"
			ev.Hitos = append(ev.Hitos, "📣 Llegó por el anuncio de clic a WhatsApp del vestido V42")
		}
		if f.etapa == "prospeccion" && f.ocasion == "" {
			ev.Hitos = append(ev.Hitos, "👋 Escribió por primera vez; aún no cuenta qué busca")
		} else if f.etapa == "prospeccion" {
			ev.Hitos = append(ev.Hitos, "💬 Contó que busca algo para su "+f.ocasion+"; todavía no se le muestra ninguna prenda")
		}
		mem["sabemos"] = sab
		ev.Memoria, _ = json.Marshal(mem)
		if f.etapa == "venta_confirmada" && f.canal == "whatsapp" && hay {
			ev.Pedido = &kommo.Pedido{ID: int64(9000 + n), Estado: "confirmado", Total: p.Price, Codigo: p.Code, Nombre: p.Name,
				Talla: f.talla, Cantidad: 1}
		}
		switch f.final {
		case "ganado":
			ev.Pagado = true
			ev.Hitos = append(ev.Hitos, "📍 Dirección de envío registrada")
		case "perdido":
			if ev.Pedido == nil {
				break
			}
			out = append(out, ev)
			ev.Hitos = []string{"😕 Escribió que ya compró en otra tienda"}
			ped := *ev.Pedido
			ped.Estado = "cancelado"
			ev.Pedido = &ped
		}
		out = append(out, ev)
	}
	return out
}

// resumen de la demo por etapa (para la salida del comando y las pruebas).
func resumen(evs []kommo.Evento) string {
	n, visto := map[string]int{}, map[string]bool{}
	for _, ev := range evs {
		if !visto[ev.Clave] {
			visto[ev.Clave] = true
			n[ev.Etapa]++
		}
	}
	var b strings.Builder
	for _, et := range kommo.Etapas {
		fmt.Fprintf(&b, "%s %d · ", et.Nombre, n[et.Clave])
	}
	return strings.TrimSuffix(b.String(), " · ")
}
