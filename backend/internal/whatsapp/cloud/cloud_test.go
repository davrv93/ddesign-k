package cloud_test

import (
	"context"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/whatsapp/cloud"
	"github.com/davrv93/ddesign-k/backend/internal/whatsapp/cloud/simulado"
)

func cliente(t *testing.T) (*cloud.Client, *simulado.Servidor) {
	t.Helper()
	sim := simulado.New("tok", "111", "999")
	t.Cleanup(sim.Close)
	return cloud.New(sim.URL, "", "111", "999", "tok", "secreto", "verif"), sim
}

func TestEnviosYVentana(t *testing.T) {
	c, sim := cliente(t)
	ctx := context.Background()
	if _, err := c.SendText(ctx, "51987654321@s.whatsapp.net", "hola"); !cloud.FueraDeVentana(err) {
		t.Fatalf("sin mensaje de la clienta, el texto libre debía rechazarse por ventana: %v", err)
	}
	sim.Entrante("51987654321", time.Now())
	if _, err := c.SendText(ctx, "+51 987 654 321", "hola"); err != nil {
		t.Fatal(err)
	}
	if _, err := c.SendImage(ctx, "51987654321", "https://x/y.jpg", "pie"); err != nil {
		t.Fatal(err)
	}
	if _, err := c.SendPoll(ctx, "51987654321", "¿Qué quieres ver?", []string{"Ver colores", "Ver tallas"}); err != nil {
		t.Fatal(err)
	}
	if _, err := c.SendPoll(ctx, "51987654321", "¿Cuál?", []string{"a", "b", "c", "d", "un título larguísimo de más de veinticuatro"}); err != nil {
		t.Fatal(err)
	}
	if _, err := c.SendTemplate(ctx, "51987654321", "oferta", "es", []string{"Ana"}); err == nil {
		t.Fatal("una plantilla no aprobada debía fallar")
	}
	sim.Plantilla("oferta", "APPROVED")
	sim.Entrante("51987654321", time.Now().Add(-48*time.Hour))
	if _, err := c.SendTemplate(ctx, "51987654321", "oferta", "es", []string{"Ana"}); err != nil {
		t.Fatalf("la plantilla sale fuera de ventana: %v", err)
	}
	env := sim.Enviados()
	if len(env) != 5 || env[2].Tipo != "interactive" || len(env[2].Opciones) != 2 || len(env[3].Opciones) != 5 || env[4].Params[0] != "Ana" {
		t.Fatalf("enviados: %+v", env)
	}
	if len(sim.Errores()) != 1 { // solo la plantilla no aprobada
		t.Fatalf("errores del simulado: %v", sim.Errores())
	}
	ps, err := c.Plantillas(ctx)
	if err != nil || len(ps) != 1 || ps[0].Status != "APPROVED" || ps[0].Body == "" {
		t.Fatalf("plantillas: %+v %v", ps, err)
	}
}

func TestFirmaYWebhook(t *testing.T) {
	c, _ := cliente(t)
	body := []byte(`{"object":"whatsapp_business_account","entry":[{"changes":[{"field":"messages","value":{
	 "metadata":{"phone_number_id":"111"},
	 "contacts":[{"wa_id":"51987654321","profile":{"name":"Ana"}}],
	 "messages":[
	  {"id":"wamid.1","from":"51987654321","timestamp":"1792080000","type":"text","text":{"body":"hola, info del vestido"},
	   "referral":{"source_type":"ad","source_id":"120210","headline":"Vestido Salome","ctwa_clid":"ARxyz"}},
	  {"id":"wamid.2","from":"51987654321","timestamp":"1792080005","type":"interactive","interactive":{"type":"button_reply","button_reply":{"id":"op_1","title":"Ver colores"}}},
	  {"id":"wamid.3","from":"51987654321","timestamp":"1792080009","type":"image","image":{"id":"MED1","mime_type":"image/jpeg","caption":"este"}}]}},
	 {"field":"messages","value":{"metadata":{"phone_number_id":"OTRO"},"messages":[{"id":"x","from":"1","type":"text","text":{"body":"no"}}]}}]}]}`)
	if c.FirmaValida(body, "sha256=00") || !c.FirmaValida(body, cloud.Firmar("secreto", body)) {
		t.Fatal("firma")
	}
	if (&cloud.Client{}).FirmaValida(body, cloud.Firmar("", body)) {
		t.Fatal("sin secreto configurado nada es válido")
	}
	ms, err := c.LeerWebhook(body)
	if err != nil || len(ms) != 3 {
		t.Fatalf("mensajes: %+v %v", ms, err)
	}
	if ms[0].Nombre != "Ana" || ms[0].Referral == nil || ms[0].Referral.SourceID != "120210" || ms[0].Referral.CtwaClid != "ARxyz" {
		t.Fatalf("referral: %+v", ms[0])
	}
	if ms[1].Texto != "Ver colores" || ms[2].MediaID != "MED1" || ms[2].Texto != "este" {
		t.Fatalf("botón o imagen: %+v %+v", ms[1], ms[2])
	}
}
