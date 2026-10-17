package store

import (
	"context"
	"errors"
	"testing"
	"time"
)

// Seguimiento, contenido, números, origen y campañas: reglas y aislamiento entre empresas (SQLite y, con
// JMD_TEST_MYSQL_DSN, MariaDB).
func TestSeguimientoStore(t *testing.T) {
	for nombre, base := range backends(t) {
		t.Run(nombre, func(t *testing.T) {
			ctx := ConAutor(context.Background(), "Rosa")
			a := nuevaEmpresa(t, base, "sa-"+nombre+sufijo(), "Uno", 5)
			b := nuevaEmpresa(t, base, "sb-"+nombre+sufijo(), "Dos", 5)

			// Contenido: lo manual no lo pisa la importación de la web; otra empresa no lo ve.
			if _, err := a.st.GuardarContenido(ctx, a.prod.ID, "beneficio", "Tela que no se arruga", "", "manual"); err != nil {
				t.Fatal(err)
			}
			if ok, _ := a.st.GuardarContenido(ctx, a.prod.ID, "beneficio", "otro de la web", "", "web"); ok {
				t.Fatal("la web no pisa lo manual")
			}
			if ok, err := a.st.GuardarContenido(ctx, a.prod.ID, "testimonio", "¡Me encantó!", "https://x/t.jpg", "web"); !ok || err != nil {
				t.Fatal(err)
			}
			if _, err := a.st.GuardarContenido(ctx, a.prod.ID, "otra", "x", "", "manual"); !errors.Is(err, ErrSeguimientoInvalido) {
				t.Fatal("clave desconocida")
			}
			if _, err := a.st.GuardarContenido(ctx, a.prod.ID, "tip", "x", "javascript:alert(1)", "manual"); !errors.Is(err, ErrSeguimientoInvalido) {
				t.Fatal("enlace raro")
			}
			if _, err := b.st.GuardarContenido(ctx, a.prod.ID, "tip", "x", "", "manual"); !errors.Is(err, ErrNotFound) {
				t.Fatalf("producto de otra empresa: %v", err)
			}
			cs, _ := a.st.ContenidoProducto(ctx, a.prod.ID)
			if len(cs) != 2 || cs[0].Clave != "beneficio" || cs[0].Texto != "Tela que no se arruga" || cs[1].Origen != "web" {
				t.Fatalf("contenido: %+v", cs)
			}
			if rs, _ := a.st.ResumenContenidos(ctx); len(rs) != 1 || !rs[0].Beneficio || !rs[0].Testimonio || rs[0].Tip {
				t.Fatalf("resumen: %+v", rs)
			}
			if _, err := a.st.GuardarContenido(ctx, a.prod.ID, "testimonio", "", "", "manual"); err != nil {
				t.Fatal(err)
			}
			if c, _ := a.st.ContenidoDe(ctx, a.prod.ID, "testimonio"); c != nil {
				t.Fatal("vacío = borrado")
			}

			// Secuencias: validación, alcance y precedencia.
			for _, mal := range []*Secuencia{
				{Nombre: "", Pasos: []Paso{{EsperaMin: 60, Tipo: "emoji", Texto: "🥰"}}},
				{Nombre: "x"},
				{Nombre: "x", Pasos: []Paso{{EsperaMin: 0, Tipo: "emoji", Texto: "🥰"}}},
				{Nombre: "x", Pasos: []Paso{{EsperaMin: 60, Tipo: "raro"}}},
				{Nombre: "x", Pasos: []Paso{{EsperaMin: 60, Tipo: "oferta"}}},
				{Nombre: "x", Pasos: []Paso{{EsperaMin: 60, Tipo: "emoji"}}},
				{Nombre: "x", Alcance: "productos", AlcanceValor: " , ", Pasos: []Paso{{EsperaMin: 60, Tipo: "tip"}}},
			} {
				if err := a.st.CrearSecuencia(ctx, mal); !errors.Is(err, ErrSeguimientoInvalido) {
					t.Fatalf("debía rechazar %+v: %v", mal, err)
				}
			}
			todas := &Secuencia{Nombre: "General", Activa: true, Pasos: []Paso{{EsperaMin: 60, Tipo: "emoji", Texto: "🥰"},
				{EsperaMin: 300, Tipo: "beneficio"}, {EsperaMin: 300, Tipo: "tip"}}}
			propia := &Secuencia{Nombre: "Solo V21", Activa: true, Alcance: "productos", AlcanceValor: "v21, V21",
				Pasos: []Paso{{EsperaMin: 10, Tipo: "texto", Texto: "¿Te ayudo con algo?"}}}
			for _, q := range []*Secuencia{todas, propia} {
				if err := a.st.CrearSecuencia(ctx, q); err != nil {
					t.Fatal(err)
				}
			}
			if propia.AlcanceValor != "V21" {
				t.Fatalf("códigos normalizados: %q", propia.AlcanceValor)
			}
			if q, _ := a.st.SecuenciaPara(ctx, "V21", "vestido"); q == nil || q.ID != propia.ID {
				t.Fatalf("la del modelo gana: %+v", q)
			}
			if q, _ := a.st.SecuenciaPara(ctx, "V99", "vestido"); q == nil || q.ID != todas.ID {
				t.Fatalf("si no, la general: %+v", q)
			}
			if q, _ := b.st.SecuenciaPara(ctx, "V21", ""); q != nil {
				t.Fatal("otra empresa no ve las secuencias")
			}
			if _, err := b.st.GetSecuencia(ctx, todas.ID); !errors.Is(err, ErrNotFound) {
				t.Fatal("ni por id")
			}

			// En curso: armar, no reiniciar el reloj, pausar al responder, continuar, terminar, detener.
			t0 := time.Now().Add(-time.Hour)
			if err := a.st.ArmarSeguimiento(ctx, a.conv.ID, todas, "V21", t0); err != nil {
				t.Fatal(err)
			}
			e, _ := a.st.SeguimientoDe(ctx, a.conv.ID)
			if e == nil || e.Estado != "activo" || e.Paso != 1 || e.Total != 3 || e.ProximoEn == nil {
				t.Fatalf("armado: %+v", e)
			}
			_ = a.st.ArmarSeguimiento(ctx, a.conv.ID, todas, "V21", time.Now().Add(time.Hour))
			if e2, _ := a.st.SeguimientoDe(ctx, a.conv.ID); !e2.ProximoEn.Equal(*e.ProximoEn) {
				t.Fatal("con la secuencia corriendo, el reloj no se reinicia")
			}
			v, _ := a.st.SeguimientosVencidos(ctx, time.Now(), 10)
			if len(v) != 1 || v[0].Indice() != 0 {
				t.Fatalf("vencidos: %+v", v)
			}
			if vb, _ := b.st.SeguimientosVencidos(ctx, time.Now(), 10); len(vb) != 0 {
				t.Fatal("vencidos de otra empresa")
			}
			if fin, err := a.st.AvanzarSeguimiento(ctx, a.conv.ID, todas, 0, time.Now()); fin || err != nil {
				t.Fatal(err)
			}
			_ = a.st.AnotarEnvio(ctx, a.conv.ID, todas.ID, 1, 0, "emoji", "enviado", "")
			if a.st.EnviadosHoy(ctx, 0) != 1 || b.st.EnviadosHoy(ctx, 0) != 0 {
				t.Fatal("enviados hoy por empresa")
			}
			_ = a.st.PausarSeguimiento(ctx, a.conv.ID)
			if e, _ := a.st.SeguimientoDe(ctx, a.conv.ID); e.Estado != "pausado" || e.Paso != 2 {
				t.Fatalf("pausado: %+v", e)
			}
			_ = a.st.ArmarSeguimiento(ctx, a.conv.ID, todas, "V21", time.Now())
			if e, _ := a.st.SeguimientoDe(ctx, a.conv.ID); e.Estado != "activo" || e.Paso != 2 {
				t.Fatalf("continuar sigue desde el paso 2: %+v", e)
			}
			_, _ = a.st.AvanzarSeguimiento(ctx, a.conv.ID, todas, 1, time.Now())
			if fin, _ := a.st.AvanzarSeguimiento(ctx, a.conv.ID, todas, 2, time.Now()); !fin {
				t.Fatal("el último termina")
			}
			_ = a.st.ArmarSeguimiento(ctx, a.conv.ID, todas, "V21", time.Now())
			if e, _ := a.st.SeguimientoDe(ctx, a.conv.ID); e.Estado != "terminado" {
				t.Fatal("terminado no se vuelve a armar")
			}
			if err := b.st.DetenerSeguimiento(ctx, a.conv.ID); !errors.Is(err, ErrNotFound) {
				t.Fatal("detener desde otra empresa")
			}
			// Apagar la secuencia detiene lo que estaba en curso.
			_ = a.st.ArmarSeguimiento(ctx, b.conv.ID, todas, "V21", time.Now()) // conversación de otra empresa: no pasa nada visible
			if e, _ := b.st.SeguimientoDe(ctx, b.conv.ID); e != nil {
				t.Fatal("no se arma en una conversación ajena")
			}

			// Números.
			n := &NumeroWhatsApp{Nombre: "Avisos", Numero: "+51 999 888 777", Instancia: "x-n1", Token: "t", TopeDiario: 20}
			if err := a.st.CrearNumero(ctx, n); err != nil || n.Numero != "51999888777" || n.Uso != "salida" {
				t.Fatalf("número: %+v %v", n, err)
			}
			if err := a.st.CrearNumero(ctx, &NumeroWhatsApp{Nombre: "x", Numero: "123"}); !errors.Is(err, ErrSeguimientoInvalido) {
				t.Fatal("número corto")
			}
			if ns, _ := b.st.ListarNumeros(ctx); len(ns) != 0 {
				t.Fatal("números de otra empresa")
			}
			if _, err := a.st.NumeroPorInstancia(ctx, "x-n1"); err != nil {
				t.Fatal(err)
			}
			if err := b.st.BorrarNumero(ctx, n.ID); !errors.Is(err, ErrNotFound) {
				t.Fatal("borrar ajeno")
			}

			// Origen y campañas.
			if err := a.st.AnotarOrigen(ctx, a.conv.ID, Origen{Canal: "whatsapp", Titular: "Vestido Uno"}); err != nil {
				t.Fatal(err)
			}
			_ = a.st.AnotarOrigen(ctx, a.conv.ID, Origen{Canal: "cloud", AdID: "120210", Titular: "otro", CtwaClid: "AR1"})
			o, _ := a.st.OrigenDe(ctx, a.conv.ID)
			if o == nil || o.AdID != "120210" || o.Titular != "otro" || o.Canal != "whatsapp" {
				t.Fatalf("origen: %+v", o)
			}
			if err := b.st.AnotarOrigen(ctx, a.conv.ID, Origen{Titular: "ajeno"}); err != nil {
				t.Fatal(err)
			}
			if o, _ := b.st.OrigenDe(ctx, a.conv.ID); o != nil {
				t.Fatal("origen en conversación ajena")
			}
			// El pedido de la empresa se creó ANTES de anotar el origen: no cuenta para el anuncio.
			hoy := time.Now()
			filas, tot, err := a.st.Campanas(ctx, hoy.AddDate(0, 0, -1), hoy)
			if err != nil || len(filas) == 0 || filas[0].AdID != "120210" || filas[0].Conversaciones != 1 || tot.Conversaciones < 1 {
				t.Fatalf("campañas: %+v %+v %v", filas, tot, err)
			}
			if filas, _, _ := b.st.Campanas(ctx, hoy.AddDate(0, 0, -1), hoy); len(filas) != 1 || filas[0].Titular != "Sin anuncio" {
				t.Fatalf("la otra empresa solo ve lo suyo, orgánico: %+v", filas)
			}

			// «Más adelante»: el bot la pone, los guardados del contexto no la pisan, y se suelta al volver a escribir.
			if err := a.st.EtapaPorSeguimiento(ctx, a.conv.ID, "mas_adelante"); err != nil {
				t.Fatal(err)
			}
			_ = a.st.SetConversationState(ctx, a.conv.ID, "", `{"etapa":"seguimiento"}`)
			if c, _ := a.st.GetClienta(ctx, a.cust.ID); c.Etapa != "mas_adelante" {
				t.Fatalf("el contexto no pisa «Más adelante»: %q", c.Etapa)
			}
			_ = a.st.ArmarSeguimiento(ctx, a.conv.ID, propia, "V21", time.Now())
			_ = a.st.DetenerSeguimiento(ctx, a.conv.ID)
			_ = a.st.SalirDeMasAdelante(ctx, a.conv.ID)
			if e, _ := a.st.SeguimientoDe(ctx, a.conv.ID); e == nil || e.Estado == "detenido" {
				t.Fatalf("al volver de «Más adelante», el seguimiento se puede rearmar: %+v", e)
			}
			_ = a.st.SetConversationState(ctx, a.conv.ID, "", `{"etapa":"cierre"}`)
			if c, _ := a.st.GetClienta(ctx, a.cust.ID); c.Etapa != "cierre" {
				t.Fatalf("al volver, la etapa la decide el bot: %q", c.Etapa)
			}
		})
	}
}
