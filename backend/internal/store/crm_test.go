package store

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"
)

// CRM por empresa: ficha de la clienta, etiquetas, etapa, asignación, notas, tareas, línea de tiempo, equipo y panel
// de inicio. Dos empresas con la misma clienta (mismo jid) y el mismo usuario «admin»: con los ids de la otra no se lee,
// cambia ni borra nada. Corre en SQLite y, con JMD_TEST_MYSQL_DSN, también en MariaDB.
func TestCRMAisladoEntreEmpresas(t *testing.T) {
	for nombre, base := range backends(t) {
		t.Run(nombre, func(t *testing.T) {
			ctx := context.Background()
			sfx := fmt.Sprintf("%d", time.Now().UnixNano()%1_000_000)
			a := nuevaEmpresa(t, base, "crma-"+sfx, "Alfa", 5)
			b := nuevaEmpresa(t, base, "crmb-"+sfx, "Beta", 7)
			yo := func(e *empresa) context.Context { return ConAutor(ctx, "admin de "+e.tenant.Slug) }

			for _, e := range []*empresa{a, b} {
				asesora, err := e.st.CrearMiembro(ctx, "lucia", "clave-larga-1", "Lucía "+e.tenant.Slug, RolAsesora)
				if err != nil {
					t.Fatal(err)
				}
				if _, err := e.st.CrearMiembro(ctx, "lucia", "otra-clave-1", "", RolAsesora); !errors.Is(err, ErrExiste) {
					t.Fatalf("usuario repetido: %v", err)
				}
				nm, ciudad := "Ana "+e.tenant.Slug, "Lima"
				if err := e.st.UpdateClienta(yo(e), e.cust.ID, DatosClienta{Name: &nm, Ciudad: &ciudad}); err != nil {
					t.Fatal(err)
				}
				if err := e.st.SetEtiquetas(ctx, e.cust.ID, []string{"VIP", " vip ", "Novia", ""}); err != nil {
					t.Fatal(err)
				}
				if err := e.st.SetEtapa(yo(e), e.cust.ID, "cierre", false); err != nil {
					t.Fatal(err)
				}
				if err := e.st.AsignarClienta(yo(e), e.cust.ID, asesora.ID); err != nil {
					t.Fatal(err)
				}
				if err := e.st.AsignarPedido(yo(e), e.order.ID, asesora.ID); err != nil {
					t.Fatal(err)
				}
				if err := e.st.AddNota(ctx, &Nota{CustomerID: e.cust.ID, Texto: "Prefiere entrega el sábado", Autor: "admin"}); err != nil {
					t.Fatal(err)
				}
				if err := e.st.AddNota(ctx, &Nota{OrderID: e.order.ID, Texto: "Pagó con Yape", Autor: "admin"}); err != nil {
					t.Fatal(err)
				}
				ayer := time.Now().Add(-26 * time.Hour)
				if err := e.st.AddTarea(ctx, &Tarea{CustomerID: e.cust.ID, Titulo: "Llamar a Ana", Vence: &ayer,
					ResponsableID: asesora.ID, CreadaPor: "admin"}); err != nil {
					t.Fatal(err)
				}
				if _, err := e.st.UpdateOrderStatus(yo(e), e.order.ID, "preparando", nil); err != nil {
					t.Fatal(err)
				}
			}
			ua, _ := a.st.MiembroPorUsuario(ctx, "lucia")
			ub, _ := b.st.MiembroPorUsuario(ctx, "lucia")
			ta, _ := a.st.ListTareas(ctx, FiltroTareas{Vista: "todas"})
			tb, _ := b.st.ListTareas(ctx, FiltroTareas{Vista: "todas"})
			na, _ := a.st.ListNotas(ctx, a.cust.ID, 0)
			nb, _ := b.st.ListNotas(ctx, b.cust.ID, 0)
			if len(ta) != 1 || len(tb) != 1 || len(na) != 2 || len(nb) != 2 {
				t.Fatalf("tareas %d/%d, notas %d/%d (esperaba 1/1 y 2/2)", len(ta), len(tb), len(na), len(nb))
			}

			// Lecturas: cada empresa ve solo lo suyo.
			for _, e := range []*empresa{a, b} {
				cs, err := e.st.ListClientas(ctx, FiltroClientas{})
				if err != nil || len(cs) != 1 || cs[0].ID != e.cust.ID {
					t.Fatalf("%s: clientas %d %v", e.tenant.Slug, len(cs), err)
				}
				c := cs[0]
				if c.Etapa != "cierre" || !c.EtapaFijada || c.Ciudad != "Lima" || c.Pedidos != 1 || c.TotalComprado != 100 ||
					c.UltimaCompra == nil || c.Conversacion != e.conv.ID || !strings.HasPrefix(c.Asesora, "Lucía "+e.tenant.Slug) {
					t.Fatalf("%s: ficha %+v", e.tenant.Slug, c)
				}
				if strings.Join(c.Etiquetas, ",") != "novia,vip" {
					t.Fatalf("%s: etiquetas %v", e.tenant.Slug, c.Etiquetas)
				}
				for _, f := range []FiltroClientas{{Q: "ana"}, {Etapa: "cierre"}, {Etiqueta: "VIP"}, {Compra: "con"}, {Q: "900000"}} {
					if cs, _ := e.st.ListClientas(ctx, f); len(cs) != 1 {
						t.Fatalf("%s: filtro %+v → %d", e.tenant.Slug, f, len(cs))
					}
				}
				for _, f := range []FiltroClientas{{Q: "zzz"}, {Etapa: "sin"}, {Etiqueta: "otra"}, {Compra: "sin"}, {Asesora: -1}} {
					if cs, _ := e.st.ListClientas(ctx, f); len(cs) != 0 {
						t.Fatalf("%s: filtro %+v → %d (esperaba 0)", e.tenant.Slug, f, len(cs))
					}
				}
				if es, _ := e.st.Etiquetas(ctx); len(es) != 2 || es[0].Clientas != 1 {
					t.Fatalf("%s: etiquetas %+v", e.tenant.Slug, es)
				}
				o, _ := e.st.GetOrder(ctx, e.order.ID)
				if o.AsesoraID == 0 {
					t.Fatalf("%s: pedido sin asesora", e.tenant.Slug)
				}
				if ns, _ := e.st.ListNotas(ctx, 0, e.order.ID); len(ns) != 1 || ns[0].Texto != "Pagó con Yape" {
					t.Fatalf("%s: notas del pedido %+v", e.tenant.Slug, ns)
				}
				ev, err := e.st.Actividad(ctx, e.cust.ID, 100)
				if err != nil {
					t.Fatal(err)
				}
				tipos := map[string]int{}
				for _, x := range ev {
					tipos[x.Tipo]++
					if strings.Contains(x.Texto, "Beta") && e == a || strings.Contains(x.Texto, "Alfa") && e == b {
						t.Fatalf("%s: actividad ajena %+v", e.tenant.Slug, x)
					}
				}
				for _, tipo := range []string{"mensaje", "pedido", "estado", "etapa", "asignacion", "edicion", "nota", "tarea"} {
					if tipos[tipo] == 0 {
						t.Fatalf("%s: la línea de tiempo no trae %q: %v", e.tenant.Slug, tipo, tipos)
					}
				}
				in, err := e.st.Inicio(ctx, 0)
				if err != nil {
					t.Fatal(err)
				}
				if in.ClientasTotal != 1 || in.Embudo["cierre"] != 1 || in.TareasVencidas != 1 || in.VentasMes != 100 || in.PorEstado["preparando"] != 1 {
					t.Fatalf("%s: inicio %+v", e.tenant.Slug, in)
				}
				if vs, _ := e.st.ListTareas(ctx, FiltroTareas{Vista: "vencidas"}); len(vs) != 1 {
					t.Fatalf("%s: vencidas %d", e.tenant.Slug, len(vs))
				}
				if ms, _ := e.st.ListMiembros(ctx); len(ms) != 2 {
					t.Fatalf("%s: equipo de %d", e.tenant.Slug, len(ms))
				}
			}

			// Con los ids de B, A no encuentra nada.
			if _, err := a.st.GetClienta(ctx, b.cust.ID); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A leyó la clienta de B: %v", err)
			}
			if _, err := a.st.Actividad(ctx, b.cust.ID, 50); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A leyó la actividad de B: %v", err)
			}
			if ns, _ := a.st.ListNotas(ctx, b.cust.ID, 0); len(ns) != 0 {
				t.Fatalf("A leyó %d notas de B", len(ns))
			}
			if ns, _ := a.st.ListNotas(ctx, 0, b.order.ID); len(ns) != 0 {
				t.Fatalf("A leyó %d notas del pedido de B", len(ns))
			}
			if _, err := a.st.GetTarea(ctx, tb[0].ID); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A leyó la tarea de B: %v", err)
			}
			if _, err := a.st.GetMiembro(ctx, ub.ID); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A leyó el usuario de B: %v", err)
			}
			if ts, _ := a.st.ListTareas(ctx, FiltroTareas{Vista: "todas", CustomerID: b.cust.ID}); len(ts) != 0 {
				t.Fatalf("A listó %d tareas de la clienta de B", len(ts))
			}

			// Escrituras con ids de B (o apuntando a cosas de B): fallan y no tocan a B.
			hack := "hackeada"
			if err := a.st.UpdateClienta(ctx, b.cust.ID, DatosClienta{Name: &hack}); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A editó la clienta de B: %v", err)
			}
			if err := a.st.SetEtiquetas(ctx, b.cust.ID, []string{"hack"}); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A etiquetó a la clienta de B: %v", err)
			}
			if err := a.st.SetEtapa(ctx, b.cust.ID, "perdida", false); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A cambió la etapa de B: %v", err)
			}
			if err := a.st.AsignarClienta(ctx, b.cust.ID, ua.ID); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A asignó la clienta de B: %v", err)
			}
			if err := a.st.AsignarClienta(ctx, a.cust.ID, ub.ID); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A asignó su clienta a la asesora de B: %v", err)
			}
			if err := a.st.AsignarPedido(ctx, b.order.ID, ua.ID); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A asignó el pedido de B: %v", err)
			}
			if err := a.st.AddNota(ctx, &Nota{CustomerID: b.cust.ID, Texto: "intrusa"}); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A anotó en la clienta de B: %v", err)
			}
			if err := a.st.AddNota(ctx, &Nota{OrderID: b.order.ID, Texto: "intrusa"}); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A anotó en el pedido de B: %v", err)
			}
			if err := a.st.DeleteNota(ctx, nb[0].ID, 0, true); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A borró la nota de B: %v", err)
			}
			for _, tr := range []*Tarea{{Titulo: "x", CustomerID: b.cust.ID}, {Titulo: "x", OrderID: b.order.ID}, {Titulo: "x", ResponsableID: ub.ID}} {
				if err := a.st.AddTarea(ctx, tr); !errors.Is(err, ErrNotFound) {
					t.Fatalf("A creó una tarea con datos de B (%+v): %v", tr, err)
				}
			}
			hecha := true
			if err := a.st.UpdateTarea(ctx, tb[0].ID, CambioTarea{Hecha: &hecha}); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A cerró la tarea de B: %v", err)
			}
			if err := a.st.DeleteTarea(ctx, tb[0].ID); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A borró la tarea de B: %v", err)
			}
			rol := RolAdmin
			if _, err := a.st.UpdateMiembro(ctx, ub.ID, CambioMiembro{Role: &rol}); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A cambió el rol de la asesora de B: %v", err)
			}

			// B sigue intacta.
			cb, err := b.st.GetClienta(ctx, b.cust.ID)
			if err != nil || cb.Name != "Ana crmb-"+sfx || cb.Etapa != "cierre" || cb.AsesoraID != ub.ID || strings.Join(cb.Etiquetas, ",") != "novia,vip" {
				t.Fatalf("clienta de B alterada: %+v %v", cb, err)
			}
			if tr, _ := b.st.GetTarea(ctx, tb[0].ID); tr == nil || tr.Hecha {
				t.Fatalf("tarea de B alterada: %+v", tr)
			}
			if ns, _ := b.st.ListNotas(ctx, b.cust.ID, 0); len(ns) != 2 {
				t.Fatalf("B tiene %d notas", len(ns))
			}
			if m, _ := b.st.GetMiembro(ctx, ub.ID); m == nil || m.Role != RolAsesora {
				t.Fatalf("asesora de B alterada: %+v", m)
			}
		})
	}
}

// La etapa del bot llega a la clienta (y a su línea de tiempo), salvo que una persona la haya fijado a mano.
func TestEtapaDelBotYManual(t *testing.T) {
	for nombre, base := range backends(t) {
		t.Run(nombre, func(t *testing.T) {
			ctx := context.Background()
			e := nuevaEmpresa(t, base, fmt.Sprintf("etapa-%d", time.Now().UnixNano()%1_000_000), "Etapa", 3)
			etapa := func() (string, bool) {
				c, err := e.st.GetClienta(ctx, e.cust.ID)
				if err != nil {
					t.Fatal(err)
				}
				return c.Etapa, c.EtapaFijada
			}
			_ = e.st.SetConversationState(ctx, e.conv.ID, "", `{"etapa":"seguimiento","memoria":{"x":1}}`)
			if et, _ := etapa(); et != "seguimiento" {
				t.Fatalf("el bot no movió la etapa: %q", et)
			}
			_ = e.st.SetConversationState(ctx, e.conv.ID, "", `{"etapa":"inventada"}`)
			if et, _ := etapa(); et != "seguimiento" {
				t.Fatalf("una etapa desconocida cambió la clienta: %q", et)
			}
			if err := e.st.SetEtapa(ConAutor(ctx, "admin"), e.cust.ID, "perdida", false); err != nil {
				t.Fatal(err)
			}
			_ = e.st.SetConversationState(ctx, e.conv.ID, "", `{"etapa":"cierre"}`)
			if et, fij := etapa(); et != "perdida" || !fij {
				t.Fatalf("el bot pisó la etapa fijada a mano: %q %v", et, fij)
			}
			if err := e.st.SetEtapa(ConAutor(ctx, "admin"), e.cust.ID, "", true); err != nil {
				t.Fatal(err)
			}
			_ = e.st.SetConversationState(ctx, e.conv.ID, "", `{"etapa":"cierre"}`)
			if et, fij := etapa(); et != "cierre" || fij {
				t.Fatalf("en automático el bot no la movió: %q %v", et, fij)
			}
			if err := e.st.SetEtapa(ctx, e.cust.ID, "rara", false); err == nil {
				t.Fatal("aceptó una etapa inválida")
			}
			ev, _ := e.st.Actividad(ctx, e.cust.ID, 50)
			var bot, manual int
			for _, x := range ev {
				if x.Tipo == "etapa" && x.Autor == "bot" {
					bot++
				}
				if x.Tipo == "etapa" && x.Autor == "admin" {
					manual++
				}
			}
			if bot != 2 || manual != 2 {
				t.Fatalf("cambios de etapa en la línea de tiempo: bot %d, manual %d (%+v)", bot, manual, ev)
			}
		})
	}
}

// Una SQLite anterior al CRM (sin las columnas nuevas) se abre, conserva sus datos y toma la etapa que el bot ya había
// dejado en la conversación.
func TestSQLiteAnteriorAlCRM(t *testing.T) {
	dir := t.TempDir()
	st, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	cu, cv, err := st.UpsertCustomer(ctx, "51911111111@s.whatsapp.net", "51911111111", "Rosa")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := st.DB.Exec(`UPDATE conversations SET context='{"etapa":"cierre"}' WHERE id=?`, cv.ID); err != nil {
		t.Fatal(err)
	}
	// Vuelve al esquema de antes: customers sin las columnas del CRM y sin las tablas nuevas.
	if _, err := st.DB.Exec(`PRAGMA foreign_keys=OFF;
		CREATE TABLE customers_v (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL DEFAULT 1, jid TEXT NOT NULL,
			phone TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
			UNIQUE(tenant_id, jid));
		INSERT INTO customers_v SELECT id, tenant_id, jid, phone, name, created_at FROM customers;
		DROP TABLE customers; ALTER TABLE customers_v RENAME TO customers;
		DROP TABLE notas; DROP TABLE tareas; DROP TABLE actividad; DROP TABLE cliente_etiquetas;
		ALTER TABLE orders DROP COLUMN asesora_id;`); err != nil {
		t.Fatal(err)
	}
	st.DB.Close()
	st, err = Open(dir)
	if err != nil {
		t.Fatalf("abrir la base anterior al CRM: %v", err)
	}
	defer st.DB.Close()
	c, err := st.GetClienta(ctx, cu.ID)
	if err != nil || c.Name != "Rosa" || c.Etapa != "cierre" {
		t.Fatalf("clienta tras migrar: %+v %v", c, err)
	}
	if err := st.AddNota(ctx, &Nota{CustomerID: cu.ID, Texto: "hola"}); err != nil {
		t.Fatal(err)
	}
}

// Equipo: no se puede quedar sin admin; una asesora no es admin.
func TestEquipoUltimaAdmin(t *testing.T) {
	for nombre, base := range backends(t) {
		t.Run(nombre, func(t *testing.T) {
			ctx := context.Background()
			e := nuevaEmpresa(t, base, fmt.Sprintf("equipo-%d", time.Now().UnixNano()%1_000_000), "Equipo", 1)
			admin, _ := e.st.MiembroPorUsuario(ctx, "admin")
			no := false
			ases := RolAsesora
			if _, err := e.st.UpdateMiembro(ctx, admin.ID, CambioMiembro{Active: &no}); !errors.Is(err, ErrUltimaAdmin) {
				t.Fatalf("desactivó a la única admin: %v", err)
			}
			if _, err := e.st.UpdateMiembro(ctx, admin.ID, CambioMiembro{Role: &ases}); !errors.Is(err, ErrUltimaAdmin) {
				t.Fatalf("degradó a la única admin: %v", err)
			}
			otra, err := e.st.CrearMiembro(ctx, "rosa", "clave-de-rosa", "Rosa", RolAdmin)
			if err != nil {
				t.Fatal(err)
			}
			if m, err := e.st.UpdateMiembro(ctx, admin.ID, CambioMiembro{Role: &ases}); err != nil || m.EsAdmin() {
				t.Fatalf("con otra admin sí se puede: %+v %v", m, err)
			}
			if _, err := e.st.UpdateMiembro(ctx, otra.ID, CambioMiembro{Active: &no}); !errors.Is(err, ErrUltimaAdmin) {
				t.Fatalf("desactivó a la última admin: %v", err)
			}
			corta := "corta"
			if _, err := e.st.UpdateMiembro(ctx, otra.ID, CambioMiembro{Password: &corta}); err == nil {
				t.Fatal("aceptó una clave corta")
			}
			if _, err := e.st.CrearMiembro(ctx, "x", "clave-larga", "", "jefa"); err == nil {
				t.Fatal("aceptó un rol desconocido")
			}
		})
	}
}
