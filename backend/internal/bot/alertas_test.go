package bot

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
	"github.com/davrv93/ddesign-k/backend/internal/store"
)

func memTemp(temp, motivo, fecha string) json.RawMessage {
	return json.RawMessage(fmt.Sprintf(`{"etapa":"seguimiento","producto":"V01","temperatura":%q,"temperatura_motivo":%q,`+
		`"objeciones":["precio"],"sabemos":{"ocasion":"boda","fecha":"el sábado 24","fecha_iso":%q,"talla":"M","color":"verde",`+
		`"estilo":"elegante","horario":"noche"}}`, temp, motivo, fecha))
}

// Clienta caliente (15-10-2026): al PASAR a caliente se crea una alerta, se avisa al panel y sale un WhatsApp al número
// de Ajustes con el resumen. Seguir caliente no repite; volver a enfriarse y calentarse en la misma semana tampoco.
func TestAlertaClientaCaliente(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	ctx := context.Background()
	b.cfg.PublicURL = "https://panel.test/jmdventas/baruka"
	var mu sync.Mutex
	var topics []string
	b.Notify = func(s string) { mu.Lock(); topics = append(topics, s); mu.Unlock() }
	if err := st.SetSetting(ctx, store.SettingAlertaWhatsApp, "+51 999-888-777"); err != nil {
		t.Fatal(err)
	}
	en9 := time.Now().In(store.Lima).AddDate(0, 0, 9).Format("2006-01-02")
	en90 := time.Now().In(store.Lima).AddDate(0, 0, 90).Format("2006-01-02")
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"hola, busco vestido para una boda": {Accion: "responder", Respuesta: "¡Qué lindo! ¿Para cuándo es?", Memoria: memTemp("frio", "evento en 3 meses", en90)},
		"perdón, es el sábado 24":           {Accion: "responder", Respuesta: "¡Wooow, estamos muy pronto!", Memoria: memTemp("caliente", "evento en 9 días", en9)},
		"me gusta":                          {Accion: "responder", Respuesta: "¡Te va a quedar precioso!", Memoria: memTemp("caliente", "evento en 9 días", en9)},
		"mejor lo dejo para después":        {Accion: "responder", Respuesta: "¡Claro!", Memoria: memTemp("frio", "dijo que es para más adelante", en90)},
		"no, sí lo quiero":                  {Accion: "responder", Respuesta: "¡Genial!", Memoria: memTemp("caliente", "dijo que quiere comprarlo", en9)},
	}, &reqs)

	enviosA := func(num string) []map[string]any {
		fe.mu.Lock()
		defer fe.mu.Unlock()
		var out []map[string]any
		for _, m := range fe.sent {
			if m["number"] == num {
				out = append(out, m)
			}
		}
		return out
	}
	handle(b, text("hola, busco vestido para una boda"))
	if as, _ := st.ListarAlertas(ctx, true); len(as) != 0 {
		t.Fatalf("fría: sin alerta, hay %d", len(as))
	}
	handle(b, text("perdón, es el sábado 24"))
	as, err := st.ListarAlertas(ctx, true)
	if err != nil || len(as) != 1 {
		t.Fatalf("pasó a caliente: una alerta; hay %d (%v)", len(as), err)
	}
	a := as[0]
	if a.Nombre != "Ana López" || a.Telefono != "51987654321" || a.Motivo != "evento en 9 días" {
		t.Fatalf("alerta: %+v", a)
	}
	var res ResumenAlerta
	_ = json.Unmarshal(a.Resumen, &res)
	if d, _ := time.Parse("2006-01-02", en9); res.Fecha != diasLargos[d.Weekday()]+" "+fmt.Sprint(d.Day())+" de "+mesesLargos[d.Month()-1] {
		t.Fatalf("fecha legible: %q", res.Fecha)
	}
	if res.Evento != "boda" || res.Talla != "M" || res.Dias == nil || *res.Dias != 9 || res.PrendaNombre != "Vestido Esmeralda" ||
		len(res.Dudas) != 1 || res.Dudas[0] != "el precio" {
		t.Fatalf("resumen: %+v", res)
	}
	var wa []map[string]any
	for i := 0; i < 50 && len(wa) == 0; i++ {
		time.Sleep(20 * time.Millisecond)
		wa = enviosA("51999888777")
	}
	if len(wa) != 1 {
		t.Fatalf("un WhatsApp al número de Ajustes (solo dígitos); hubo %d", len(wa))
	}
	txt, _ := wa[0]["text"].(string)
	for _, s := range []string{"Clienta caliente", "Ana López", "+51987654321", "evento en 9 días", "boda", "(en 9 días)",
		"Vestido Esmeralda", "Talla: M", "Dudas: el precio", "https://panel.test/jmdventas/baruka/conversaciones/?c="} {
		if !strings.Contains(txt, s) {
			t.Fatalf("el aviso no dice %q:\n%s", s, txt)
		}
	}
	mu.Lock()
	hayTopic := strings.Contains(strings.Join(topics, ","), "alertas")
	mu.Unlock()
	if !hayTopic {
		t.Fatal("el panel debía recibir el tema «alertas»")
	}
	for i := 0; i < 50; i++ { // la marca de enviado va después del envío, en segundo plano
		if as, _ = st.ListarAlertas(ctx, true); as[0].WhatsappEnviado {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if !as[0].WhatsappEnviado {
		t.Fatal("la alerta debía quedar con whatsapp_enviado")
	}

	handle(b, text("me gusta"))
	handle(b, text("mejor lo dejo para después"))
	handle(b, text("no, sí lo quiero"))
	time.Sleep(100 * time.Millisecond)
	if as, _ := st.ListarAlertas(ctx, false); len(as) != 1 {
		t.Fatalf("una alerta por semana y conversación; hay %d", len(as))
	}
	if n := len(enviosA("51999888777")); n != 1 {
		t.Fatalf("un solo WhatsApp a la tienda; hubo %d", n)
	}

	// Atender: deja de estar pendiente y queda a nombre de quien la atendió.
	if err := st.AtenderAlerta(store.ConAutor(ctx, "Rosa"), a.ID); err != nil {
		t.Fatal(err)
	}
	if as, _ := st.ListarAlertas(ctx, true); len(as) != 0 {
		t.Fatalf("atendida: ya no es pendiente")
	}
	todas, _ := st.ListarAlertas(ctx, false)
	if todas[0].AtendidaPor != "Rosa" || todas[0].AtendidaEn == nil {
		t.Fatalf("atendida por: %+v", todas[0])
	}

	// La ficha del panel sale de la memoria guardada.
	conv, err := st.GetConversation(ctx, a.ConversationID)
	if err != nil {
		t.Fatal(err)
	}
	f := LeerFicha(conv.Context, time.Now(), func(c string) string { return map[string]string{"V01": "Vestido Esmeralda"}[c] })
	if f.Temperatura != "caliente" || f.Sabemos["ocasion"] != "boda" || f.Sabemos["estilo"] != "elegante" || f.DiasAlEvento == nil ||
		*f.DiasAlEvento != 9 || f.Producto == nil || f.Producto.Nombre != "Vestido Esmeralda" || len(f.Objeciones) != 1 {
		t.Fatalf("ficha: %+v", f)
	}
}

// Sin número en Ajustes: la alerta se crea y el panel se entera, pero no sale ningún WhatsApp a la tienda.
func TestAlertaSinNumeroNoMandaWhatsApp(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	ctx := context.Background()
	en5 := time.Now().In(store.Lima).AddDate(0, 0, 5).Format("2006-01-02")
	var reqs []agente.Request
	b.Agent = agenteGuion(t, map[string]agente.Reply{
		"es para el sábado": {Accion: "responder", Respuesta: "¡Estamos muy pronto!", Memoria: memTemp("caliente", "evento en 5 días", en5)},
	}, &reqs)
	handle(b, text("es para el sábado"))
	time.Sleep(100 * time.Millisecond)
	if as, _ := st.ListarAlertas(ctx, true); len(as) != 1 || as[0].WhatsappEnviado {
		t.Fatalf("alerta sin aviso: %+v", as)
	}
	fe.mu.Lock()
	defer fe.mu.Unlock()
	for _, m := range fe.sent {
		if m["number"] != "51987654321@s.whatsapp.net" && m["number"] != "51987654321" && m["_path"] != "/message/presence" {
			t.Fatalf("no debía salir nada fuera de la clienta: %+v", m)
		}
	}
}

func TestLeerFichaSinMemoria(t *testing.T) {
	f := LeerFicha(`{}`, time.Now(), func(string) string { return "" })
	if f.Temperatura != "frio" || f.Etapa != "prospeccion" || len(f.Sabemos) != 0 || f.Producto != nil || f.Pedido != nil || f.Objeciones == nil {
		t.Fatalf("ficha vacía: %+v", f)
	}
}
