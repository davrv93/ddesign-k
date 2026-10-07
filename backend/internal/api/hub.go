package api

import (
	"fmt"
	"net/http"
	"sync"
	"time"
)

// Hub reparte avisos de cambios a los paneles abiertos (Server-Sent Events). Cada panel escucha solo a su
// empresa: un cambio de una empresa no llega a los paneles de otra.
type Hub struct {
	mu   sync.Mutex
	subs map[chan string]int64 // canal → empresa
}

func NewHub() *Hub { return &Hub{subs: map[chan string]int64{}} }

// Publish avisa a todos los paneles (despliegue de una sola tienda).
func (h *Hub) Publish(topic string) { h.publish(0, topic) }

// PublishTo avisa solo a los paneles de la empresa tid.
func (h *Hub) PublishTo(tid int64, topic string) { h.publish(tid, topic) }

func (h *Hub) publish(tid int64, topic string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	for ch, t := range h.subs {
		if tid != 0 && t != tid {
			continue
		}
		select {
		case ch <- topic:
		default: // cliente lento: se salta el aviso, igual refresca en el siguiente
		}
	}
}

// ServeHTTP atiende un panel sin empresa (pruebas y despliegue de una tienda).
func (h *Hub) ServeHTTP(w http.ResponseWriter, r *http.Request) { h.Serve(w, r, 0) }

// Serve abre el stream de avisos de la empresa tid.
func (h *Hub) Serve(w http.ResponseWriter, r *http.Request, tid int64) {
	fl, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming no soportado", http.StatusInternalServerError)
		return
	}
	ch := make(chan string, 16)
	h.mu.Lock()
	h.subs[ch] = tid
	h.mu.Unlock()
	defer func() {
		h.mu.Lock()
		delete(h.subs, ch)
		h.mu.Unlock()
	}()
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("X-Accel-Buffering", "no")
	fmt.Fprint(w, "retry: 3000\n\n")
	fl.Flush()
	ping := time.NewTicker(25 * time.Second)
	defer ping.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case t := <-ch:
			fmt.Fprintf(w, "event: change\ndata: %s\n\n", t)
			fl.Flush()
		case <-ping.C:
			fmt.Fprint(w, ": ping\n\n")
			fl.Flush()
		}
	}
}
