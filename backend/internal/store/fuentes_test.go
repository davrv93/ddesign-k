package store

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"
)

// Fuentes, promos, embudos y propuestas (docs/FUENTES_CONTRATO.md): CRUD con versiones, vigencia, lo que recibe el
// agente, uso por conversación, pedidos en el embudo de una promo, cifras y propuestas del robot. Todo con dos
// empresas: nada de una se ve ni se toca desde la otra.

// sufijo: slugs únicos por corrida (la MariaDB de pruebas guarda lo de corridas anteriores).
func sufijo() string { return "-" + strings.ReplaceAll(time.Now().Format("150405.000000"), ".", "") }

func promoDatos(t *testing.T, p PromoDatos) json.RawMessage {
	t.Helper()
	b, err := json.Marshal(p)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func TestFuentesCRUDVersionesYAislamiento(t *testing.T) {
	for nombre, base := range backends(t) {
		t.Run(nombre, func(t *testing.T) {
			ctx := ConAutor(context.Background(), "Ana")
			a := nuevaEmpresa(t, base, "fa-"+nombre+sufijo(), "Uno", 5)
			b := nuevaEmpresa(t, base, "fb-"+nombre+sufijo(), "Dos", 5)

			f := &Fuente{Tipo: "promo", Titulo: "Promo pelotera", Contenido: "20% en vestidos",
				Datos: promoDatos(t, PromoDatos{Mecanica: "porcentaje", Valor: 20, Alcance: PromoAlcance{Tipo: "categoria", Categoria: "vestido"}})}
			if err := a.st.CrearFuente(ctx, f); err != nil {
				t.Fatal(err)
			}
			if f.Estado != "borrador" || f.Version != 1 || f.ActualizadoPor != "Ana" {
				t.Fatalf("recién creada: %+v", f)
			}
			// Inválidas
			for _, mal := range []*Fuente{
				{Tipo: "x", Titulo: "t"},
				{Tipo: "faq", Titulo: "t", Datos: json.RawMessage(`{"pregunta":""}`)},
				{Tipo: "promo", Titulo: "t", Datos: promoDatos(t, PromoDatos{Mecanica: "porcentaje", Valor: 150})},
				{Tipo: "promo", Titulo: "t", Datos: promoDatos(t, PromoDatos{Mecanica: "monto", Alcance: PromoAlcance{Tipo: "prendas"}})},
				{Tipo: "politica", Titulo: "t", VigenteDesde: "2026-10-20", VigenteHasta: "2026-10-10"},
			} {
				if err := a.st.CrearFuente(ctx, mal); !errors.Is(err, ErrFuenteInvalida) {
					t.Fatalf("debía rechazar %+v: %v", mal, err)
				}
			}
			// Guardar sin cambios no sube la versión; con cambios sí y queda la anterior.
			f.Etiquetas = []string{"verano", "verano", " "}
			if err := a.st.GuardarFuente(ctx, f); err != nil {
				t.Fatal(err)
			}
			if f.Version != 1 || len(f.Etiquetas) != 1 {
				t.Fatalf("sin cambio de contenido: versión %d, etiquetas %v", f.Version, f.Etiquetas)
			}
			f.Contenido = "25% en vestidos"
			if err := a.st.GuardarFuente(ctx, f); err != nil {
				t.Fatal(err)
			}
			vs, err := a.st.VersionesFuente(ctx, f.ID)
			if err != nil || f.Version != 2 || len(vs) != 1 || vs[0].Contenido != "20% en vestidos" {
				t.Fatalf("versiones: v%d %+v %v", f.Version, vs, err)
			}
			// La otra empresa no la ve ni la toca.
			if _, err := b.st.GetFuente(ctx, f.ID); !errors.Is(err, ErrNotFound) {
				t.Fatalf("otra empresa leyó la fuente: %v", err)
			}
			if err := b.st.GuardarFuente(ctx, &Fuente{ID: f.ID, Titulo: "pisada", Tipo: "promo"}); !errors.Is(err, ErrNotFound) {
				t.Fatalf("otra empresa la guardó: %v", err)
			}
			if _, err := b.st.CambiarEstadoFuente(ctx, f.ID, "activo"); !errors.Is(err, ErrNotFound) {
				t.Fatalf("otra empresa la activó: %v", err)
			}
			if err := b.st.BorrarFuente(ctx, f.ID); !errors.Is(err, ErrNotFound) {
				t.Fatalf("otra empresa la borró: %v", err)
			}
			if _, err := b.st.VersionesFuente(ctx, f.ID); !errors.Is(err, ErrNotFound) {
				t.Fatalf("otra empresa leyó sus versiones: %v", err)
			}
			lb, _ := b.st.ListarFuentes(ctx, FiltroFuentes{})
			if len(lb) != 0 {
				t.Fatalf("la otra empresa lista %d fuentes", len(lb))
			}
			// Una FAQ sin respuesta no se activa.
			q := &Fuente{Tipo: "faq", Titulo: "¿Factura?", Datos: json.RawMessage(`{"pregunta":"¿Dan factura?"}`)}
			if err := a.st.CrearFuente(ctx, q); err != nil {
				t.Fatal(err)
			}
			if _, err := a.st.CambiarEstadoFuente(ctx, q.ID, "activo"); !errors.Is(err, ErrFuenteInvalida) {
				t.Fatalf("activó una FAQ sin respuesta: %v", err)
			}
			// Búsqueda
			l, _ := a.st.ListarFuentes(ctx, FiltroFuentes{Q: "25%"})
			if len(l) != 1 || l[0].ID != f.ID {
				t.Fatalf("búsqueda: %v", l)
			}
			if err := a.st.BorrarFuente(ctx, q.ID); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestConocimientoVigenteYVencer(t *testing.T) {
	for nombre, base := range backends(t) {
		t.Run(nombre, func(t *testing.T) {
			ctx := context.Background()
			a := nuevaEmpresa(t, base, "ca-"+nombre+sufijo(), "Uno", 5)
			b := nuevaEmpresa(t, base, "cb-"+nombre+sufijo(), "Dos", 5)
			ahora := time.Date(2026, 10, 15, 15, 0, 0, 0, time.UTC) // 10:00 en Lima
			hoy := HoyLima(ahora)
			mk := func(st *Store, f *Fuente, estado string) *Fuente {
				t.Helper()
				if err := st.CrearFuente(ctx, f); err != nil {
					t.Fatal(err)
				}
				if estado != "borrador" {
					if _, err := st.DB.Exec(`UPDATE fuentes SET estado=? WHERE id=?`, estado, f.ID); err != nil {
						t.Fatal(err)
					}
				}
				return f
			}
			promo := mk(a.st, &Fuente{Tipo: "promo", Titulo: "Pelotera", VigenteDesde: "2026-10-10", VigenteHasta: "2026-10-20",
				Datos: promoDatos(t, PromoDatos{Mecanica: "porcentaje", Valor: 20})}, "activo")
			mk(a.st, &Fuente{Tipo: "promo", Titulo: "Navidad", VigenteDesde: "2026-12-01",
				Datos: promoDatos(t, PromoDatos{Mecanica: "monto", Valor: 30})}, "activo") // programada: no viaja
			mk(a.st, &Fuente{Tipo: "promo", Titulo: "Vieja", VigenteHasta: "2026-10-01",
				Datos: promoDatos(t, PromoDatos{Mecanica: "monto", Valor: 30})}, "activo") // vencida por fecha
			mk(a.st, &Fuente{Tipo: "faq", Titulo: "Factura", Datos: json.RawMessage(`{"pregunta":"¿Dan factura?","respuesta":"Sí, con RUC."}`)}, "activo")
			mk(a.st, &Fuente{Tipo: "faq", Titulo: "Borrador", Datos: json.RawMessage(`{"pregunta":"¿x?","respuesta":"y"}`)}, "borrador")
			mk(a.st, &Fuente{Tipo: "politica", Titulo: "Cambios", Contenido: "7 días para cambios."}, "activo")
			g1 := mk(a.st, &Fuente{Tipo: "guion", Titulo: "Guion 1", Contenido: "## Paso 1"}, "activo")
			g2 := mk(a.st, &Fuente{Tipo: "guion", Titulo: "Guion 2", Contenido: "## Paso 1 nuevo"}, "activo")
			mk(b.st, &Fuente{Tipo: "faq", Titulo: "De la otra", Datos: json.RawMessage(`{"pregunta":"¿otra?","respuesta":"sí"}`)}, "activo")

			c, err := a.st.ConocimientoVigente(ctx, "uno", ahora)
			if err != nil {
				t.Fatal(err)
			}
			if c.Hoy != hoy || len(c.Promos) != 1 || c.Promos[0].ID != promo.ID || c.Promos[0].Valor != 20 || len(c.Faqs) != 1 ||
				len(c.Politicas) != 1 || c.Guion == nil || c.Guion.ID != g2.ID || c.Version == "" {
				t.Fatalf("conocimiento: %+v", c)
			}
			_ = g1
			c2, _ := a.st.ConocimientoVigente(ctx, "uno", ahora.Add(2*time.Hour))
			if c2.Version != c.Version {
				t.Fatalf("la versión cambió sin cambiar nada: %s → %s", c.Version, c2.Version)
			}
			promo.Contenido = "ahora 25%"
			if err := a.st.GuardarFuente(ctx, promo); err != nil {
				t.Fatal(err)
			}
			c3, _ := a.st.ConocimientoVigente(ctx, "uno", ahora)
			if c3.Version == c.Version {
				t.Fatal("la versión no cambió al editar una promo vigente")
			}
			cb, _ := b.st.ConocimientoVigente(ctx, "dos", ahora)
			if len(cb.Faqs) != 1 || cb.Faqs[0].Pregunta != "¿otra?" || len(cb.Promos) != 0 {
				t.Fatalf("la otra empresa recibió: %+v", cb)
			}
			// Vencer: solo la que pasó su fecha.
			n, err := a.st.VencerFuentes(ctx, ahora)
			if err != nil || n != 1 {
				t.Fatalf("vencer: %d %v", n, err)
			}
			l, _ := a.st.ListarFuentes(ctx, FiltroFuentes{Estado: "vencido"})
			if len(l) != 1 || l[0].Titulo != "Vieja" {
				t.Fatalf("vencidas: %v", l)
			}
			if n, _ := b.st.VencerFuentes(ctx, ahora.AddDate(1, 0, 0)); n != 0 {
				t.Fatalf("vencer de la otra empresa tocó %d", n)
			}
			pr, _ := a.st.ListarFuentes(ctx, FiltroFuentes{Estado: "programada"})
			if len(pr) != 1 || pr[0].Titulo != "Navidad" || !pr[0].Programada {
				t.Fatalf("programadas: %v", pr)
			}
		})
	}
}

func TestEmbudoDePromoUsoYCifras(t *testing.T) {
	for nombre, base := range backends(t) {
		t.Run(nombre, func(t *testing.T) {
			ctx := context.Background()
			a := nuevaEmpresa(t, base, "ea-"+nombre+sufijo(), "Uno", 5)
			b := nuevaEmpresa(t, base, "eb-"+nombre+sufijo(), "Dos", 5)
			promo := &Fuente{Tipo: "promo", Titulo: "Pelotera", Datos: promoDatos(t, PromoDatos{Mecanica: "porcentaje", Valor: 20})}
			if err := a.st.CrearFuente(ctx, promo); err != nil {
				t.Fatal(err)
			}
			if _, err := a.st.CambiarEstadoFuente(ctx, promo.ID, "activo"); err != nil {
				t.Fatal(err)
			}
			e := &Embudo{Nombre: "Pelotera", Etapas: []string{"Interesada", "Cotizada", "Pagada"}, PromoID: promo.ID}
			if err := a.st.CrearEmbudo(ctx, e); err != nil {
				t.Fatal(err)
			}
			if err := b.st.CrearEmbudo(ctx, &Embudo{Nombre: "x", Etapas: []string{"a", "b"}, PromoID: promo.ID}); !errors.Is(err, ErrNotFound) {
				t.Fatalf("otra empresa colgó un embudo de una promo ajena: %v", err)
			}
			// El robot respondió con la promo en la conversación de la clienta; luego ella pide.
			if err := a.st.AnotarUsoFuentes(ctx, a.conv.ID, []int64{promo.ID, promo.ID, 0}); err != nil {
				t.Fatal(err)
			}
			if err := b.st.AnotarUsoFuentes(ctx, b.conv.ID, []int64{promo.ID}); err != nil { // fuente ajena: no se anota
				t.Fatal(err)
			}
			usadas, _ := a.st.FuentesDeConversacion(ctx, a.conv.ID)
			if len(usadas) != 1 || usadas[0].Titulo != "Pelotera" {
				t.Fatalf("usadas: %+v", usadas)
			}
			if ub, _ := b.st.FuentesDeConversacion(ctx, b.conv.ID); len(ub) != 0 {
				t.Fatalf("la otra empresa anotó una fuente ajena: %+v", ub)
			}
			pid, _, _ := a.st.PromoDeConversacion(ctx, a.conv.ID)
			if pid != promo.ID {
				t.Fatalf("promo de la conversación: %d", pid)
			}
			// El pedido de nuevaEmpresa nació antes del uso: con la holgura de 1 h entra (mismo turno).
			_, ps, err := a.st.PedidosDeEmbudo(ctx, e.ID)
			if err != nil || len(ps) != 1 || ps[0].Etapa != "Interesada" || ps[0].ID != a.order.ID {
				t.Fatalf("pedidos del embudo: %+v %v", ps, err)
			}
			if err := a.st.MoverPedidoEmbudo(ctx, e.ID, a.order.ID, "Pagada"); err != nil {
				t.Fatal(err)
			}
			if err := a.st.MoverPedidoEmbudo(ctx, e.ID, a.order.ID, "Inventada"); !errors.Is(err, ErrFuenteInvalida) {
				t.Fatalf("etapa inválida: %v", err)
			}
			if err := b.st.MoverPedidoEmbudo(ctx, e.ID, a.order.ID, "Pagada"); !errors.Is(err, ErrNotFound) {
				t.Fatalf("otra empresa movió el pedido: %v", err)
			}
			_, ps, _ = a.st.PedidosDeEmbudo(ctx, e.ID)
			if len(ps) != 1 || ps[0].Etapa != "Pagada" {
				t.Fatalf("tras mover: %+v", ps)
			}
			res, err := a.st.ResumenPromos(ctx, time.Now())
			if err != nil || len(res) != 1 || res[0].Cifras.Conversaciones != 1 || res[0].Cifras.Pedidos != 1 ||
				res[0].Cifras.Confirmados != 1 || res[0].EmbudoID != e.ID {
				t.Fatalf("resumen: %+v %v", res, err)
			}
			// Sacarlo
			if err := a.st.MoverPedidoEmbudo(ctx, e.ID, a.order.ID, ""); err != nil {
				t.Fatal(err)
			}
			_, ps, _ = a.st.PedidosDeEmbudo(ctx, e.ID)
			if len(ps) != 0 {
				t.Fatalf("sacado sigue: %+v", ps)
			}
			res, _ = a.st.ResumenPromos(ctx, time.Now())
			if res[0].Cifras.Pedidos != 0 {
				t.Fatalf("cifras tras sacarlo: %+v", res[0].Cifras)
			}
			// Cambiar etapas: los que estaban en una que ya no existe pasan a la primera.
			_ = a.st.MoverPedidoEmbudo(ctx, e.ID, a.order.ID, "Cotizada")
			e.Etapas = []string{"Nueva", "Pagada"}
			if err := a.st.GuardarEmbudo(ctx, e); err != nil {
				t.Fatal(err)
			}
			_, ps, _ = a.st.PedidosDeEmbudo(ctx, e.ID)
			if len(ps) != 1 || ps[0].Etapa != "Nueva" {
				t.Fatalf("tras renombrar etapas: %+v", ps)
			}
			es, _ := b.st.ListarEmbudos(ctx)
			if len(es) != 1 || !es[0].General {
				t.Fatalf("la otra empresa ve embudos ajenos: %+v", es)
			}
			if err := b.st.BorrarEmbudo(ctx, e.ID); !errors.Is(err, ErrNotFound) {
				t.Fatalf("otra empresa borró el embudo: %v", err)
			}
			if err := a.st.BorrarEmbudo(ctx, e.ID); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestPropuestasDelRobot(t *testing.T) {
	for nombre, base := range backends(t) {
		t.Run(nombre, func(t *testing.T) {
			ctx := context.Background()
			a := nuevaEmpresa(t, base, "pa-"+nombre+sufijo(), "Uno", 5)
			b := nuevaEmpresa(t, base, "pb-"+nombre+sufijo(), "Dos", 5)
			desde := time.Now().Add(-time.Hour)
			// Tres clientas preguntan por factura y el robot deriva; una asesora contesta en la tercera.
			preguntas := []string{"¿Dan factura con RUC?", "hola, dan factura?", "¿me pueden dar factura? mi número es 987654321"}
			for i, p := range preguntas {
				_, cv, err := a.st.UpsertCustomer(ctx, "5199900000"+string(rune('1'+i))+"@s.whatsapp.net", "", "Lucía Pérez")
				if err != nil {
					t.Fatal(err)
				}
				msgs := []*Message{{ConversationID: cv.ID, Direction: "in", Author: "cliente", Body: p},
					{ConversationID: cv.ID, Direction: "out", Author: "bot", Body: "Ese dato te lo confirma una asesora: escribe *4* 😊"}}
				if i == 2 {
					msgs = append(msgs, &Message{ConversationID: cv.ID, Direction: "out", Author: "asesora", Body: "Sí Lucía, emitimos factura con tu RUC."})
				}
				for _, m := range msgs {
					m.Kind = "text"
					if err := a.st.AddMessage(ctx, m); err != nil {
						t.Fatal(err)
					}
				}
			}
			// Como contesta V3 cuando no lo sabe («no puedo confirmar…»): también cuenta como que no supo.
			for i, q := range []string{"¿Hacen entregas el mismo día en Lima?", "hola, ¿pueden entregar hoy mismo en Lima?", "¿la entrega en Lima puede ser el mismo día?"} {
				_, cv, err := a.st.UpsertCustomer(ctx, "5198800000"+string(rune('1'+i))+"@s.whatsapp.net", "", "")
				if err != nil {
					t.Fatal(err)
				}
				for _, m := range []*Message{{ConversationID: cv.ID, Direction: "in", Author: "cliente", Body: q},
					{ConversationID: cv.ID, Direction: "out", Author: "bot", Body: "En Lima el envío cuesta S/ 15; no puedo confirmar entrega para el mismo día."}} {
					m.Kind = "text"
					if err := a.st.AddMessage(ctx, m); err != nil {
						t.Fatal(err)
					}
				}
			}
			// Una pregunta suelta que el robot sí contestó: no se propone.
			_ = a.st.AddMessage(ctx, &Message{ConversationID: a.conv.ID, Direction: "in", Author: "cliente", Kind: "text", Body: "¿cuánto cuesta el envío?"})
			_ = a.st.AddMessage(ctx, &Message{ConversationID: a.conv.ID, Direction: "out", Author: "bot", Kind: "text", Body: "El envío a Lima es S/ 15."})

			n, err := a.st.ProponerPreguntas(ctx, desde)
			if err != nil || n != 2 {
				t.Fatalf("propuestas: %d %v", n, err)
			}
			l, _ := a.st.ListarFuentes(ctx, FiltroFuentes{Estado: "propuesta", Q: "factura"})
			if len(l) != 1 {
				t.Fatalf("propuestas guardadas: %v", l)
			}
			if m, _ := a.st.ListarFuentes(ctx, FiltroFuentes{Estado: "propuesta", Q: "mismo"}); len(m) != 1 {
				t.Fatalf("la propuesta del «mismo día» (no puedo confirmar): %v", m)
			}
			var q FaqDatos
			_ = json.Unmarshal(l[0].Datos, &q)
			todo := q.Pregunta + " " + strings.Join(q.Variantes, " ") + " " + q.Respuesta
			if q.Ocurrencias != 3 || !strings.Contains(q.Respuesta, "factura") || strings.Contains(todo, "987654321") ||
				strings.Contains(todo, "Lucía") || l[0].Origen != "propuesta_bot" {
				t.Fatalf("propuesta: %+v", q)
			}
			// No entra al robot sin aprobación.
			c, _ := a.st.ConocimientoVigente(ctx, "uno", time.Now())
			if len(c.Faqs) != 0 {
				t.Fatalf("una propuesta llegó al robot sin aprobar: %+v", c.Faqs)
			}
			// Otra pasada suma ocurrencias, no duplica.
			if n, _ := a.st.ProponerPreguntas(ctx, desde); n != 2 {
				t.Fatalf("segunda pasada: %d", n)
			}
			l, _ = a.st.ListarFuentes(ctx, FiltroFuentes{Estado: "propuesta", Q: "factura"})
			_ = json.Unmarshal(l[0].Datos, &q)
			if len(l) != 1 || q.Ocurrencias != 6 {
				t.Fatalf("segunda pasada: %d propuestas, %d ocurrencias", len(l), q.Ocurrencias)
			}
			// Descartada: no vuelve.
			if _, err := a.st.CambiarEstadoFuente(ctx, l[0].ID, "descartada"); err != nil {
				t.Fatal(err)
			}
			if n, _ := a.st.ProponerPreguntas(ctx, desde); n != 1 { // solo suma a la del «mismo día»
				t.Fatalf("volvió a proponer una descartada: %d", n)
			}
			// La otra empresa no recibe propuestas de la primera.
			if n, _ := b.st.ProponerPreguntas(ctx, desde); n != 0 {
				t.Fatalf("la otra empresa propuso %d", n)
			}
		})
	}
}
