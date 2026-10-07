package juicio

import "testing"

func TestEstimarConversion(t *testing.T) {
	casos := []struct {
		nombre string
		in     Entrada
		min    float64
		max    float64
	}{
		{"prospección fría", Entrada{Etapa: "prospeccion"}, 0.10, 0.20},
		{"cierre con urgencia y reincidente", Entrada{Etapa: "cierre", Urgencia: 1, Reincidente: true}, 0.75, 1.0},
		{"ánimo negativo resta", Entrada{Etapa: "seguimiento", Sentimiento: -1}, 0.15, 0.25},
		{"objeción resta", Entrada{Etapa: "seguimiento", Intent: "objecion_precio"}, 0.15, 0.25},
		{"compra sube", Entrada{Etapa: "seguimiento", Intent: "intencion_compra"}, 0.40, 0.50},
		{"venta confirmada", Entrada{Etapa: "venta_confirmada"}, 0.90, 1.0},
	}
	for _, c := range casos {
		got := EstimarConversion(c.in)
		if got < c.min || got > c.max {
			t.Errorf("%s: EstimarConversion=%.3f, esperaba [%.2f,%.2f]", c.nombre, got, c.min, c.max)
		}
	}
}

func TestRiesgoDe(t *testing.T) {
	casos := []struct {
		accion, intent string
		quiero         Riesgo
	}{
		{"pedido", "consulta_stock", RiesgoAlto},
		{"codigo", "consulta_precio", RiesgoAlto},
		{"asesora", "asesora", RiesgoAlto},
		{"responder", "intencion_compra", RiesgoAlto},
		{"responder", "objecion_precio", RiesgoAlto},
		{"foto", "foto", RiesgoMedio},
		{"pedido_estado", "", RiesgoMedio},
		{"responder", "consulta_disponibilidad", RiesgoMedio},
		{"responder", "saludo", RiesgoBajo},
		{"responder", "consulta_color", RiesgoBajo},
	}
	for _, c := range casos {
		if got := RiesgoDe(c.accion, c.intent); got != c.quiero {
			t.Errorf("RiesgoDe(%q,%q)=%v, quería %v", c.accion, c.intent, got, c.quiero)
		}
	}
}

func TestDecidir(t *testing.T) {
	casos := []struct {
		nombre string
		in     Entrada
		quiero Accion
		riesgo Riesgo
	}{
		{"asesora pedida por la clienta", Entrada{Accion: "asesora", Confianza: 0.9}, Derivar, RiesgoAlto},
		{"queja", Entrada{Intent: "censura", Confianza: 0.9}, Derivar, RiesgoAlto},
		{"regateo no se deriva: no hay descuentos y el agente lo contesta", Entrada{Intent: "objecion_precio", Confianza: 0.95}, Enviar, RiesgoAlto},
		{"regateo con poca confianza se revisa", Entrada{Intent: "objecion_precio", Confianza: 0.4}, Sugerir, RiesgoAlto},
		{"asesora según el clasificador de la tienda", Entrada{Intent: "otro", Confianza: 0.9, IntentTienda: "asesora", ConfianzaTienda: 0.95}, Derivar, RiesgoBajo},
		{"asesora dudosa del clasificador de la tienda no deriva", Entrada{Intent: "otro", Confianza: 0.9, IntentTienda: "asesora", ConfianzaTienda: 0.4}, Enviar, RiesgoBajo},
		{"grosería según el clasificador de la tienda", Entrada{Intent: "otro", Confianza: 0.9, IntentTienda: "censura", ConfianzaTienda: 0.9}, Derivar, RiesgoBajo},
		{"«pásamelos al toque» leído como grosería dudosa no deriva", Entrada{Intent: "consulta_pago", Confianza: 0.9, IntentTienda: "censura", ConfianzaTienda: 0.5}, Enviar, RiesgoAlto},
		{"intención de compra con confianza", Entrada{Intent: "intencion_compra", Confianza: 0.9}, Enviar, RiesgoAlto},
		{"intención de compra dudosa se revisa", Entrada{Intent: "intencion_compra", Confianza: 0.5}, Sugerir, RiesgoAlto},
		{"sentimiento negativo", Entrada{Intent: "consulta_talla", Confianza: 0.9, Sentimiento: -0.7}, Derivar, RiesgoMedio},
		{"venta confirmada no se deriva por sentimiento", Entrada{Etapa: "venta_confirmada", Intent: "consulta_talla", Confianza: 0.9, Sentimiento: -0.7}, Enviar, RiesgoMedio},
		{"riesgo alto y poca confianza", Entrada{Accion: "pedido", Confianza: 0.4}, Sugerir, RiesgoAlto},
		{"riesgo alto con confianza suficiente", Entrada{Accion: "pedido", Confianza: 0.9}, Enviar, RiesgoAlto},
		{"dos intentos sin respuesta", Entrada{Intent: "consulta_color", Confianza: 0.9, IntentosSinRespuesta: 2}, Callar, RiesgoBajo},
		{"un intento todavía responde", Entrada{Intent: "consulta_color", Confianza: 0.9, IntentosSinRespuesta: 1}, Enviar, RiesgoBajo},
		{"conversión baja no empuja", Entrada{Intent: "consulta_color", Confianza: 0.9, Conversion: 0.1}, Callar, RiesgoBajo},
		{"conversión desconocida", Entrada{Intent: "consulta_color", Confianza: 0.9, Conversion: -1}, Enviar, RiesgoBajo},
		{"conversión baja en cierre sí cierra", Entrada{Etapa: "cierre", Intent: "consulta_talla", Confianza: 0.9, Conversion: 0.1}, Enviar, RiesgoMedio},
		{"default", Entrada{Intent: "saludo", Confianza: 0.9}, Enviar, RiesgoBajo},
	}
	for _, c := range casos {
		got := Decidir(c.in)
		if got.Accion != c.quiero || got.Riesgo != c.riesgo {
			t.Errorf("%s: Decidir=%+v, quería %v/%v", c.nombre, got, c.quiero, c.riesgo)
		}
		if got.Motivo == "" {
			t.Errorf("%s: decisión sin motivo", c.nombre)
		}
	}
}
