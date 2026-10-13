package bot

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/agente"
)

// fakeAgente responde según el mensaje y guarda la última petición.
func fakeAgente(t *testing.T, last *agente.Request) *agente.Client {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req agente.Request
		_ = json.NewDecoder(r.Body).Decode(&req)
		*last = req
		var rep agente.Reply
		switch req.Mensaje {
		case "estoy triste no encuentro vestido":
			rep = agente.Reply{Intencion: "estado_animo", Accion: "responder", Respuesta: "Ánimo, te ayudo a encontrarlo 💖",
				Sugerencias: []agente.Sugerencia{
					{Codigo: "V01", Fuente: "seed", Imagen: "/media/products/v01.jpg", Pie: "*V01* Vestido Esmeralda"},
					{Codigo: "VES-003", Fuente: "catalogo100", Imagen: "/media/catalogo/VES-003.jpg", Pie: "*VES-003* Línea A terracota"},
					{Codigo: "X", Imagen: "http://evil.example/x.jpg", Pie: "no debe salir"},
				}}
		case "quiero ver su catalogo":
			rep = agente.Reply{Intencion: "catalogo", Accion: "responder", Respuesta: "¡Claro! Mira estos 😍\n\n¿Para qué ocasión buscas?",
				Sugerencias: []agente.Sugerencia{{Codigo: "V01", Fuente: "seed", Imagen: "/media/products/v01.jpg", Pie: "*V01* Vestido Esmeralda"}}}
		case "hola":
			rep = agente.Reply{Intencion: "saludo", Accion: "responder", Respuesta: "¡Hola Ana! Bienvenida 😊\n\n¿Qué estás buscando hoy?"}
		case "como hago mi pedido":
			rep = agente.Reply{Intencion: "como_comprar", Accion: "responder", Respuesta: "Escríbeme el código del modelo y te pido la talla 😊"}
		case "kiero ablar con una persona":
			rep = agente.Reply{Intencion: "asesora", Accion: "asesora"}
		case "el vestido esmeralda en M":
			rep = agente.Reply{Intencion: "consulta_stock", Accion: "pedido", Codigo: "V01", Talla: "M"}
		case "cuanto cuesta el vestido esmeralda":
			rep = agente.Reply{Intencion: "consulta_precio", Accion: "codigo", Codigo: "V01"}
		default:
			http.Error(w, "caído", http.StatusBadGateway)
			return
		}
		_ = json.NewEncoder(w).Encode(rep)
	}))
	t.Cleanup(srv.Close)
	return agente.New(srv.URL, 5*time.Second)
}

func TestAgenteAtiendeTextoLibre(t *testing.T) {
	b, _, fe := setup(t, `{"intent":"otro","reply":"respuesta de gemini"}`, false)
	var last agente.Request
	b.Agent = fakeAgente(t, &last)

	handle(b, text("hola"))
	n := len(fe.sent)
	handle(b, text("estoy triste no encuentro vestido"))
	if got := len(fe.sent) - n; got != 3 {
		t.Fatalf("se esperaban texto + 2 fotos, salieron %d", got)
	}
	mustContain(t, fe.sent[n]["text"].(string), "te ayudo a encontrarlo")
	mustContain(t, fe.sent[n+2]["caption"].(string), "VES-003")
	if u, _ := fe.sent[n+1]["url"].(string); !strings.HasSuffix(u, "/media/products/v01.jpg") || strings.HasPrefix(u, b.Agent.BaseURL) {
		t.Fatalf("la foto de la tienda debe salir del backend: %q", u)
	}
	if u, _ := fe.sent[n+2]["url"].(string); u != b.Agent.BaseURL+"/media/catalogo/VES-003.jpg" {
		t.Fatalf("la foto del catálogo de 100 debe salir del agente: %q", u)
	}
	if last.Cliente != "Ana López" || len(last.Historial) == 0 || last.Historial[0].Rol != "cliente" {
		t.Fatalf("el agente debía recibir cliente e historial: %+v", last)
	}
	for _, h := range last.Historial {
		if h.Texto == "estoy triste no encuentro vestido" {
			t.Fatal("el mensaje actual no debe repetirse en el historial")
		}
	}

	handle(b, text("kiero ablar con una persona"))
	mustContain(t, fe.lastText(), "asesora")
}

// El historial funde el texto del bot y los pies de las fotos que manda detrás en UN solo turno:
// el modelo ve conversación completa, no un mensaje por párrafo.
func TestAgenteHistorialFundeTextoYPies(t *testing.T) {
	b, _, _ := setup(t, `{"intent":"otro","reply":"respuesta de gemini"}`, false)
	var last agente.Request
	b.Agent = fakeAgente(t, &last)
	handle(b, text("estoy triste no encuentro vestido")) // responde texto + 2 fotos
	handle(b, text("hola"))                              // ya con el turno del bot en el historial
	var bots []agente.Turn
	for _, h := range last.Historial {
		if h.Rol != "cliente" {
			bots = append(bots, h)
		}
	}
	if len(bots) != 1 {
		t.Fatalf("texto y fotos del bot debían ser un solo turno, salieron %d: %+v", len(bots), bots)
	}
	mustContain(t, bots[0].Texto, "ayudo a encontrarlo", "VES-003")
}

func TestAgenteOfreceProducto(t *testing.T) {
	b, _, fe := setup(t, `{}`, false)
	var last agente.Request
	b.Agent = fakeAgente(t, &last)
	handle(b, text("cuanto cuesta el vestido esmeralda"))
	mustContain(t, fe.lastText(), "Vestido Esmeralda")
}

func TestAgentePresentaCatalogo(t *testing.T) {
	b, _, fe := setup(t, `{}`, false)
	var last agente.Request
	b.Agent = fakeAgente(t, &last)
	handle(b, text("hola"))
	n := len(fe.sent)
	handle(b, text("quiero ver su catalogo"))
	if got := len(fe.sent) - n; got != 2 {
		t.Fatalf("se esperaba 1 mensaje + 1 foto, salieron %d: %+v", got, fe.sent[n:])
	}
	// Un mensaje por turno: los párrafos van juntos en un solo envío; la foto va aparte.
	mustContain(t, fe.sent[n]["text"].(string), "Mira estos", "ocasión")
	mustContain(t, fe.sent[n+1]["caption"].(string), "V01")
}

func TestAgenteCaidoVuelveAGemini(t *testing.T) {
	b, _, fe := setup(t, `{"intent":"otro","reply":"respuesta de gemini"}`, false)
	var last agente.Request
	b.Agent = fakeAgente(t, &last)
	handle(b, text("algo que el agente no sabe"))
	mustContain(t, fe.lastText(), "respuesta de gemini")
}

// fakeAgenteFoto responde a /foto con la respuesta indicada y guarda la petición.
func fakeAgenteFoto(t *testing.T, rep agente.Reply, last *agente.PhotoRequest) *agente.Client {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/foto" {
			http.Error(w, "no", http.StatusNotFound)
			return
		}
		_ = json.NewDecoder(r.Body).Decode(last)
		_ = json.NewEncoder(w).Encode(rep)
	}))
	t.Cleanup(srv.Close)
	return agente.New(srv.URL, 5*time.Second)
}

func TestAgenteFotoOfreceSiHayStock(t *testing.T) {
	b, _, fe := setup(t, `{}`, false)
	var last agente.PhotoRequest
	b.Agent = fakeAgenteFoto(t, agente.Reply{Accion: "codigo", Codigo: "V01", Respuesta: "¡Sí lo tenemos!",
		Foto: &agente.PhotoResult{Nivel: "exacto", Caso: "online", Codigo: "V01", Similitud: 0.95}}, &last)
	handle(b, photo())
	if last.ImagenB64 == "" {
		t.Fatal("el agente debía recibir la foto")
	}
	mustContain(t, fe.lastText(), "Encontré tu modelo", "V01")
}

func TestAgenteFotoSucursalDejaConsulta(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	var last agente.PhotoRequest
	b.Agent = fakeAgenteFoto(t, agente.Reply{Accion: "responder", Respuesta: "Lo tenemos en Miraflores 👇",
		Sugerencias: []agente.Sugerencia{{Codigo: "BLU-006", Imagen: "/media/catalogo/BLU-006.jpg", Pie: "*BLU-006* 📍 Sucursal Miraflores"}},
		Foto:        &agente.PhotoResult{Nivel: "exacto", Caso: "sucursal", Codigo: "BLU-006", Similitud: 0.88}}, &last)
	handle(b, photo())
	mustContain(t, fe.sent[len(fe.sent)-2]["text"].(string), "Miraflores")
	mustContain(t, fe.lastText(), "BLU-006")
	conv, _ := st.ConversationByCustomer(context.Background(), 1)
	orders, _ := st.ListOrders(context.Background(), conv.CustomerID)
	if len(orders) != 1 || orders[0].Status != "consulta" || !strings.Contains(orders[0].Notes, "sucursal") {
		t.Fatalf("debía quedar una consulta en el tablero: %+v", orders)
	}
}

// Con agente, el saludo y las frases que nombran el menú las contesta el agente, no el menú fijo:
// «cómo hago mi pedido» no es «estado de mi pedido». Los números y «menu» siguen yendo al flujo del bot.
func TestAgenteContestaSaludoYFrases(t *testing.T) {
	b, _, fe := setup(t, `{"intent":"otro","reply":"respuesta de gemini"}`, false)
	var last agente.Request
	b.Agent = fakeAgente(t, &last)

	handle(b, text("hola"))
	mustContain(t, fe.lastText(), "¿Qué estás buscando hoy?")
	if strings.Contains(fe.sent[0]["text"].(string), "1️⃣") {
		t.Fatal("con agente, el saludo no debe mandar el menú numerado")
	}
	handle(b, text("como hago mi pedido"))
	mustContain(t, fe.lastText(), "te pido la talla")
	if last.Mensaje != "como hago mi pedido" {
		t.Fatalf("la frase debía llegar al agente: %+v", last)
	}
	handle(b, text("menu"))
	mustContain(t, fe.lastText(), "1️⃣")
}

// «el vestido esmeralda en M»: el agente ya trae modelo y talla, así que el bot no vuelve a preguntar la
// talla y va directo al resumen del pedido (estado de confirmación).
func TestAgentePedidoConTalla(t *testing.T) {
	b, st, fe := setup(t, `{}`, false)
	var last agente.Request
	b.Agent = fakeAgente(t, &last)
	handle(b, text("el vestido esmeralda en M"))
	mustContain(t, fe.lastText(), "Vestido Esmeralda", "M")
	convs, err := st.ListConversations(context.Background(), 10)
	if err != nil || len(convs) == 0 {
		t.Fatalf("sin conversación: %v", err)
	}
	if convs[0].State != stConfirm {
		t.Fatalf("debía quedar esperando la confirmación, quedó en %q", convs[0].State)
	}
	for _, m := range fe.sent {
		if s, _ := m["text"].(string); strings.Contains(s, "¿Qué talla deseas?") {
			t.Fatal("no debía volver a preguntar la talla")
		}
	}
}

// «reiniciar» empieza de cero: el agente recibe «hola» sin historial y la conversación vuelve a una sesión nueva.
func TestReiniciarEmpiezaDeCero(t *testing.T) {
	b, st, _ := setup(t, `{}`, false)
	var last agente.Request
	b.Agent = fakeAgente(t, &last)
	handle(b, text("hola"))
	handle(b, text("quiero ver su catalogo"))
	if len(last.Historial) == 0 {
		t.Fatal("antes de reiniciar el agente debía ver historial")
	}
	handle(b, text("reiniciar"))
	if last.Mensaje != "hola" || len(last.Historial) != 0 {
		t.Fatalf("tras «reiniciar» el agente debía recibir «hola» sin historial: %q %d turnos", last.Mensaje, len(last.Historial))
	}
	conv, _ := st.ConversationByCustomer(context.Background(), 1)
	var cc convContext
	_ = json.Unmarshal([]byte(conv.Context), &cc)
	if cc.Desde == 0 || conv.State != stIdle {
		t.Fatalf("debía quedar en sesión nueva: desde=%d estado=%q", cc.Desde, conv.State)
	}
}
