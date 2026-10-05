// kommo-seed prepara la cuenta de Kommo para Baruka y la llena:
//
//	kommo-seed --verificar             crea o verifica el embudo, sus estados y los campos del lead, e imprime los ids
//	kommo-seed --demo                  además carga 24 leads de demostración (etiqueta «demo»), uno por etapa y temperatura
//	kommo-seed --limpiar               cierra como «Venta perdida» los leads etiquetados «demo» (la API v4 no borra leads)
//	kommo-seed --desde-base            vuelca al CRM las conversaciones ya capturadas por el bot (SQLite de DATA_DIR)
//	kommo-seed --desde-base --dry-run  muestra lo que haría, sin llamar a Kommo (no necesita credenciales)
//
// Variables: KOMMO_SUBDOMAIN, KOMMO_TOKEN, KOMMO_PIPELINE_NAME, KOMMO_SYNC_TRANSCRIPT, DATA_DIR, PUBLIC_URL y
// CATALOG_URL (catálogo público de producción, solo lectura, para nombres y precios reales de las prendas). No mira
// KOMMO_ENABLED: correrlo ya es pedirlo. El token nunca se imprime.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/bot"
	"github.com/davrv93/ddesign-k/backend/internal/kommo"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

func env(k, def string) string {
	if v := strings.TrimSpace(os.Getenv(k)); v != "" {
		return v
	}
	return def
}

func main() {
	verificar := flag.Bool("verificar", false, "crear o verificar el embudo y los campos")
	demo := flag.Bool("demo", false, "cargar los leads de demostración")
	limpiar := flag.Bool("limpiar", false, "cerrar como perdidos los leads etiquetados «demo»")
	desdeBase := flag.Bool("desde-base", false, "volcar las conversaciones de la SQLite")
	dry := flag.Bool("dry-run", false, "mostrar lo que haría sin llamar a Kommo")
	limite := flag.Int("limite", 0, "con --desde-base: solo las N conversaciones más recientes (0 = todas)")
	flag.Parse()
	if !*verificar && !*demo && !*limpiar && !*desdeBase {
		flag.Usage()
		os.Exit(2)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()

	st, err := store.Open(env("DATA_DIR", "./data"))
	if err != nil {
		log.Fatalf("sqlite: %v", err)
	}
	defer st.DB.Close()

	sub, tok := env("KOMMO_SUBDOMAIN", ""), env("KOMMO_TOKEN", "")
	if !*dry && (sub == "" || tok == "") {
		log.Fatal("faltan KOMMO_SUBDOMAIN o KOMMO_TOKEN (con --dry-run no hacen falta)")
	}
	cat := leerCatalogo(ctx, env("CATALOG_URL", "https://proyectopostventa.site/baruka/api/public/catalog"))
	local := kommo.ProductoDe(st)
	cli := kommo.Nuevo(sub, tok)
	// El límite (7/s) es de la cuenta: el backend en marcha también sincroniza. El seed va a 4/s para dejarle sitio.
	cli.Intervalo = time.Second / 4
	s := kommo.NuevoSincronizador(cli, st, kommo.Opciones{
		Embudo:        env("KOMMO_PIPELINE_NAME", "Baruka · Ventas por WhatsApp"),
		Transcripcion: env("KOMMO_SYNC_TRANSCRIPT", "0") == "1",
		PanelURL:      strings.TrimRight(env("PUBLIC_URL", ""), "/"),
		Moneda:        env("CURRENCY", "S/"),
		Producto: func(ctx context.Context, c string) (string, float64, bool) {
			if n, p, ok := local(ctx, c); ok {
				return n, p, true
			}
			x, ok := cat[strings.ToUpper(c)]
			return x.Name, x.Price, ok
		},
		Envios: kommo.EnviosDelAgente(env("AGENT_URL", "")),
	})

	if !*dry {
		e, err := s.Esquema(ctx)
		if err != nil {
			log.Fatalf("embudo y campos: %v", err)
		}
		fmt.Printf("Embudo %q: id %d\n", env("KOMMO_PIPELINE_NAME", "Baruka · Ventas por WhatsApp"), e.PipelineID)
		for _, et := range kommo.Etapas {
			fmt.Printf("  %-17s → estado %d\n", et.Nombre, e.Estados[et.Clave])
		}
		fmt.Printf("  %-17s → estado %d\n  %-17s → estado %d\n", kommo.NombreGanado, kommo.EstadoGanado, kommo.NombrePerdido, kommo.EstadoPerdido)
		for _, d := range kommo.Campos {
			fmt.Printf("  campo %-26s %-9s id %d\n", d.Nombre, d.Tipo, e.Campos[d.Clave].ID)
		}
	}
	if *limpiar {
		if *dry {
			fmt.Println("--limpiar --dry-run: buscaría los leads etiquetados «demo» del embudo y los pasaría a «Venta perdida».")
		} else if err := limpiarDemo(ctx, s); err != nil {
			log.Fatal(err)
		}
	}
	if *demo {
		if len(cat) == 0 {
			log.Fatal("no se pudo leer el catálogo (CATALOG_URL): sin prendas reales no se arma la demo")
		}
		evs := Demo(cat, time.Now())
		fmt.Printf("Demo: %d leads (%s; 2 pasan a «%s» y 1 a «%s»)\n", len(filas), resumen(evs), kommo.NombreGanado, kommo.NombrePerdido)
		if err := aplicarTodos(ctx, s, evs, *dry); err != nil {
			log.Fatal(err)
		}
	}
	if *desdeBase {
		convs, err := st.ListConversations(ctx, 5000)
		if err != nil {
			log.Fatal(err)
		}
		if *limite > 0 && len(convs) > *limite {
			convs = convs[:*limite]
		}
		var evs []kommo.Evento
		for _, cv := range convs {
			ev := bot.EventoDeConversacion(ctx, st, cv)
			if _, err := st.VinculoKommo(ctx, ev.Clave); err == nil {
				ev.Hitos = nil // ya se volcó antes: no se repite la nota de importación
			}
			evs = append(evs, ev)
		}
		fmt.Printf("%d conversaciones en %s\n", len(evs), env("DATA_DIR", "./data"))
		if err := aplicarTodos(ctx, s, evs, *dry); err != nil {
			log.Fatal(err)
		}
	}
}

// aplicarTodos aplica los eventos de uno en uno (el cliente respeta el límite de tasa). Con dry-run, solo los muestra.
func aplicarTodos(ctx context.Context, s *kommo.Sincronizador, evs []kommo.Evento, dry bool) error {
	ok, mal := 0, 0
	for _, ev := range evs {
		if dry {
			fmt.Print(s.Simular(ctx, ev).String())
			continue
		}
		if err := s.Aplicar(ctx, ev); err != nil {
			mal++
			fmt.Printf("✗ %s: %v\n", ev.Clave, err)
			if errors.Is(err, context.Canceled) {
				return err
			}
			continue
		}
		ok++
		fmt.Printf("✓ %s\n", ev.Clave)
	}
	if !dry {
		fmt.Printf("%d aplicados, %d con error\n", ok, mal)
	}
	if mal > 0 {
		return fmt.Errorf("%d eventos no se aplicaron", mal)
	}
	return nil
}

// limpiarDemo: la API v4 de Kommo no tiene un método para borrar leads ni contactos. Los leads con la etiqueta
// «demo» se cierran como «Venta perdida» (salen del embudo activo) y se olvida su vínculo local; para borrarlos del
// todo, en Kommo: Leads → lista → filtro por etiqueta «demo» → seleccionar todo → Eliminar.
func limpiarDemo(ctx context.Context, s *kommo.Sincronizador) error {
	e, err := s.Esquema(ctx)
	if err != nil {
		return err
	}
	c := s.Cliente()
	var cerrar []kommo.Lead
	for pag := 1; pag <= 40; pag++ {
		ls, mas, err := c.BuscarLeads(ctx, "DEMO", e.PipelineID, pag)
		if err != nil {
			return err
		}
		for _, l := range ls {
			demo := false
			for _, t := range l.Embedded.Tags {
				demo = demo || strings.EqualFold(t.Name, "demo")
			}
			if demo && l.StatusID != kommo.EstadoPerdido {
				cerrar = append(cerrar, kommo.Lead{ID: l.ID, StatusID: kommo.EstadoPerdido, PipelineID: e.PipelineID})
			}
		}
		if !mas {
			break
		}
	}
	for i := 0; i < len(cerrar); i += 50 {
		if err := c.ActualizarLeads(ctx, cerrar[i:min(i+50, len(cerrar))]); err != nil {
			return err
		}
	}
	n, err := s.Store().BorrarVinculosKommo(ctx, "demo:")
	if err != nil {
		return err
	}
	fmt.Printf("%d leads «demo» pasados a «%s»; %d vínculos locales olvidados.\n", len(cerrar), kommo.NombrePerdido, n)
	fmt.Println("La API de Kommo no borra leads: para eliminarlos, en Kommo filtra la lista de leads por la etiqueta «demo»,")
	fmt.Println("selecciona todos y pulsa Eliminar (igual con los contactos «DEMO · …»).")
	return nil
}

type producto struct {
	Code  string   `json:"code"`
	Name  string   `json:"name"`
	Price float64  `json:"price"`
	Sizes []string `json:"sizes"`
}

// leerCatalogo lee el catálogo público (GET, solo lectura). Vacío si no responde.
func leerCatalogo(ctx context.Context, url string) map[string]producto {
	out := map[string]producto{}
	if url == "" {
		return out
	}
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return out
	}
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		log.Printf("catálogo: %v", err)
		return out
	}
	defer res.Body.Close()
	var js struct {
		Products []producto `json:"products"`
	}
	if err := json.NewDecoder(res.Body).Decode(&js); err != nil {
		log.Printf("catálogo: %v", err)
		return out
	}
	for _, p := range js.Products {
		if p.Code != "" && p.Price > 0 {
			out[strings.ToUpper(p.Code)] = p
		}
	}
	return out
}
