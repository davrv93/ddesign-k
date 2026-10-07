package store

import (
	"context"
	"errors"
	"fmt"
	"os"
	"testing"
	"time"
)

// Aislamiento entre empresas: dos empresas con el mismo código de producto, la misma clienta (mismo jid) y pedidos
// propios. Ninguna operación de una empresa lee, cambia ni borra filas de la otra, ni siquiera conociendo sus ids.
// Corre siempre sobre SQLite y, con JMD_TEST_MYSQL_DSN, también sobre MariaDB.

func backends(t *testing.T) map[string]*Store {
	t.Helper()
	out := map[string]*Store{}
	sq, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { sq.DB.Close() })
	out["sqlite"] = sq
	if dsn := os.Getenv("JMD_TEST_MYSQL_DSN"); dsn != "" {
		my, err := OpenMySQL(dsn)
		if err != nil {
			t.Fatalf("mariadb: %v", err)
		}
		t.Cleanup(func() { my.DB.Close() })
		out["mariadb"] = my
	}
	return out
}

type empresa struct {
	st      *Store
	prod    *Product
	cust    *Customer
	conv    *Conversation
	order   *Order
	tenant  *Tenant
	variant int64
}

func nuevaEmpresa(t *testing.T, base *Store, slug, nombre string, stock int) *empresa {
	t.Helper()
	ctx := context.Background()
	tn := &Tenant{Slug: slug, Name: nombre}
	if err := base.UpsertTenant(ctx, tn); err != nil {
		t.Fatal(err)
	}
	st := base.ForTenant(tn.ID)
	p := &Product{Code: "V21", Name: "Vestido de " + nombre, Price: 100, Active: true,
		Variants: []Variant{{Size: "M", Stock: stock}, {Size: "L", Stock: stock}}}
	if err := st.SaveProduct(ctx, p); err != nil {
		t.Fatal(err)
	}
	cu, cv, err := st.UpsertCustomer(ctx, "51900000001@s.whatsapp.net", "51900000001", "Ana de "+nombre)
	if err != nil {
		t.Fatal(err)
	}
	if err := st.AddMessage(ctx, &Message{ConversationID: cv.ID, Direction: "in", Body: "hola " + nombre, WAID: "wa-1"}); err != nil {
		t.Fatal(err)
	}
	v := p.Variants[0]
	o := &Order{CustomerID: cu.ID, Status: "confirmado", Items: []OrderItem{{ProductID: &p.ID, VariantID: &v.ID,
		ProductCode: p.Code, ProductName: p.Name, Size: v.Size, Qty: 1, UnitPrice: p.Price}}}
	if err := st.CreateOrder(ctx, o); err != nil {
		t.Fatal(err)
	}
	if err := st.SetSetting(ctx, "bot_enabled", "true-"+slug); err != nil {
		t.Fatal(err)
	}
	if err := st.GuardarVinculoKommo(ctx, &VinculoKommo{Clave: "wa:1", Canal: "whatsapp", ConversationID: cv.ID, LeadID: tn.ID * 100}); err != nil {
		t.Fatal(err)
	}
	if err := st.UpsertUser(ctx, "admin", "clave-"+slug, "Admin", "admin"); err != nil {
		t.Fatal(err)
	}
	return &empresa{st: st, prod: p, cust: cu, conv: cv, order: o, tenant: tn, variant: v.ID}
}

func TestAislamientoEntreEmpresas(t *testing.T) {
	for nombre, base := range backends(t) {
		t.Run(nombre, func(t *testing.T) {
			ctx := context.Background()
			sfx := fmt.Sprintf("%d", time.Now().UnixNano()%1_000_000)
			a := nuevaEmpresa(t, base, "alfa-"+sfx, "Alfa", 5)
			b := nuevaEmpresa(t, base, "beta-"+sfx, "Beta", 7)

			// Lecturas: cada una ve solo lo suyo, aunque el código y el jid coincidan.
			for _, e := range []*empresa{a, b} {
				ps, err := e.st.ListProducts(ctx, false)
				if err != nil {
					t.Fatal(err)
				}
				if len(ps) != 1 || ps[0].ID != e.prod.ID {
					t.Fatalf("empresa %s ve %d productos (esperaba solo el suyo)", e.tenant.Slug, len(ps))
				}
				if len(ps[0].Variants) != 2 {
					t.Fatalf("empresa %s: %d tallas", e.tenant.Slug, len(ps[0].Variants))
				}
				p, err := e.st.GetProductByCode(ctx, "v21")
				if err != nil || p.ID != e.prod.ID {
					t.Fatalf("GetProductByCode cruzó empresas: %v %v", p, err)
				}
				ords, err := e.st.ListOrders(ctx, 0)
				if err != nil || len(ords) != 1 || ords[0].ID != e.order.ID {
					t.Fatalf("empresa %s ve pedidos ajenos: %d %v", e.tenant.Slug, len(ords), err)
				}
				cs, err := e.st.ListConversations(ctx, 50)
				if err != nil || len(cs) != 1 || cs[0].ID != e.conv.ID {
					t.Fatalf("empresa %s ve conversaciones ajenas: %d %v", e.tenant.Slug, len(cs), err)
				}
				ms, err := e.st.ListMessages(ctx, e.conv.ID, 50)
				if err != nil || len(ms) != 1 {
					t.Fatalf("mensajes: %d %v", len(ms), err)
				}
				stt, err := e.st.Stats(ctx)
				if err != nil || stt.Customers != 1 || stt.ByStatus["confirmado"] != 1 {
					t.Fatalf("stats de %s mezclan empresas: %+v %v", e.tenant.Slug, stt, err)
				}
				if v := e.st.Setting(ctx, "bot_enabled", ""); v != "true-"+e.tenant.Slug {
					t.Fatalf("ajuste de %s = %q", e.tenant.Slug, v)
				}
				if !e.st.MessageExists(ctx, "wa-1") {
					t.Fatal("MessageExists no ve el propio")
				}
				vk, err := e.st.VinculoKommo(ctx, "wa:1")
				if err != nil || vk.LeadID != e.tenant.ID*100 {
					t.Fatalf("vínculo Kommo cruzado: %+v %v", vk, err)
				}
				if n, _ := e.st.CountProducts(ctx); n != 1 {
					t.Fatalf("CountProducts = %d", n)
				}
			}

			// Con los ids de la otra empresa: nada se encuentra.
			if _, err := a.st.GetProduct(ctx, b.prod.ID); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A leyó el producto de B: %v", err)
			}
			if _, err := a.st.GetOrder(ctx, b.order.ID); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A leyó el pedido de B: %v", err)
			}
			if _, err := a.st.GetConversation(ctx, b.conv.ID); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A leyó la conversación de B: %v", err)
			}
			if ms, _ := a.st.ListMessages(ctx, b.conv.ID, 50); len(ms) != 0 {
				t.Fatalf("A leyó %d mensajes de B", len(ms))
			}
			if ords, _ := a.st.ListOrders(ctx, b.cust.ID); len(ords) != 0 {
				t.Fatalf("A listó %d pedidos de la clienta de B", len(ords))
			}

			// Escrituras con ids ajenos: no tocan a B.
			if _, err := a.st.UpdateOrderStatus(ctx, b.order.ID, "cancelado", nil); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A cambió el estado del pedido de B: %v", err)
			}
			_ = a.st.UpdateOrderNotes(ctx, b.order.ID, "hackeado")
			_ = a.st.SetBotPaused(ctx, b.conv.ID, true)
			_ = a.st.SetConversationState(ctx, b.conv.ID, "x", `{"x":1}`)
			_ = a.st.MarkRead(ctx, b.conv.ID)
			_ = a.st.SetProductImage(ctx, b.prod.ID, "/media/x.jpg", "x")
			if err := a.st.AddMessage(ctx, &Message{ConversationID: b.conv.ID, Direction: "out", Body: "intruso"}); err == nil {
				t.Fatal("A escribió un mensaje en la conversación de B")
			}
			if err := a.st.SaveProduct(ctx, &Product{ID: b.prod.ID, Code: "V21", Name: "pisado", Active: true}); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A pisó el producto de B: %v", err)
			}
			if err := a.st.CreateOrder(ctx, &Order{CustomerID: b.cust.ID, Status: "consulta"}); err == nil {
				t.Fatal("A creó un pedido para la clienta de B")
			}
			bid := b.prod.ID
			if err := a.st.CreateOrder(ctx, &Order{CustomerID: a.cust.ID, Status: "consulta",
				Items: []OrderItem{{ProductID: &bid, VariantID: &b.variant, Qty: 1}}}); err == nil {
				t.Fatal("A creó un pedido con el producto de B")
			}
			if _, err := a.st.Reserve(ctx, a.order.ID, b.variant, 1, time.Minute); err == nil {
				t.Fatal("A reservó stock de B")
			}
			if err := a.st.DeleteOrder(ctx, b.order.ID); !errors.Is(err, ErrNotFound) {
				t.Fatalf("A borró el pedido de B: %v", err)
			}
			_ = a.st.DeleteProduct(ctx, b.prod.ID)

			// B sigue intacta.
			bo, err := b.st.GetOrder(ctx, b.order.ID)
			if err != nil || bo.Status != "confirmado" || bo.Notes != "" {
				t.Fatalf("pedido de B alterado: %+v %v", bo, err)
			}
			bp, err := b.st.GetProduct(ctx, b.prod.ID)
			if err != nil || bp.Name != "Vestido de Beta" || bp.Image != "" {
				t.Fatalf("producto de B alterado: %+v %v", bp, err)
			}
			if v := bp.VariantBySize("M"); v == nil || v.Stock != 6 { // 7 − 1 del pedido confirmado
				t.Fatalf("stock de B alterado: %+v", v)
			}
			bc, err := b.st.GetConversation(ctx, b.conv.ID)
			if err != nil || bc.BotPaused || bc.State != "" || bc.Unread != 1 {
				t.Fatalf("conversación de B alterada: %+v %v", bc, err)
			}
			if ms, _ := b.st.ListMessages(ctx, b.conv.ID, 50); len(ms) != 1 {
				t.Fatalf("B tiene %d mensajes", len(ms))
			}

			// Usuarios: la clave de A no abre B (mismo nombre de usuario).
			if _, err := a.st.CheckUser(ctx, "admin", "clave-"+b.tenant.Slug); err == nil {
				t.Fatal("la clave de B abrió A")
			}
			if u, err := a.st.CheckUser(ctx, "admin", "clave-"+a.tenant.Slug); err != nil || u.TenantID != a.tenant.ID {
				t.Fatalf("login de A: %+v %v", u, err)
			}

			// Un Store sin empresa (tid 0) no ve nada.
			if base.Dialect == DialectMySQL {
				if ps, _ := base.ListProducts(ctx, false); len(ps) != 0 {
					t.Fatalf("el Store sin empresa ve %d productos", len(ps))
				}
			}
		})
	}
}

func TestSlugsReservados(t *testing.T) {
	for _, s := range []string{"api", "media", "build", "A", "x", "con espacio", "default"} {
		if ValidSlug(s) {
			t.Errorf("%q no debería valer como slug", s)
		}
	}
	for _, s := range []string{"baruka", "jmd-ventas", "tienda2"} {
		if !ValidSlug(s) {
			t.Errorf("%q debería valer", s)
		}
	}
}

func TestClave(t *testing.T) {
	h, err := HashPassword("una-clave-larga")
	if err != nil {
		t.Fatal(err)
	}
	if !CheckPassword(h, "una-clave-larga") || CheckPassword(h, "otra") {
		t.Fatal("CheckPassword")
	}
}

// Una SQLite de una sola tienda, anterior a la multiempresa, se abre y queda como empresa 1.
func TestSQLiteAnteriorSeMigra(t *testing.T) {
	dir := t.TempDir()
	viejo, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	viejo.DB.Close()
	// Simula el esquema viejo: sin tenant_id en products.
	db, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	if _, err := db.DB.Exec(`DROP TABLE product_variants; DROP TABLE order_items; DROP TABLE products;
		CREATE TABLE products (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
		description TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '', color TEXT NOT NULL DEFAULT '',
		price REAL NOT NULL DEFAULT 0, image TEXT NOT NULL DEFAULT '', ai_tags TEXT NOT NULL DEFAULT '',
		active INTEGER NOT NULL DEFAULT 1, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
		updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP);
		INSERT INTO products(code, name) VALUES('V21', 'Viejo');`); err != nil {
		t.Fatal(err)
	}
	db.DB.Close()
	st, err := Open(dir)
	if err != nil {
		t.Fatalf("abrir la base vieja: %v", err)
	}
	defer st.DB.Close()
	p, err := st.GetProductByCode(ctx, "V21")
	if err != nil || p.Name != "Viejo" {
		t.Fatalf("el producto viejo no quedó en la empresa 1: %v %v", p, err)
	}
	if err := st.SaveProduct(ctx, &Product{Code: "V22", Name: "Nuevo", Active: true, Variants: []Variant{{Size: "M", Stock: 1}}}); err != nil {
		t.Fatal(err)
	}
}
