package bot

import (
	"context"
	"log"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/store"
)

// El envío a WhatsApp no bloquea al bot: cada respuesta se guarda al instante y un
// worker por chat la entrega en orden. Si evolution-go está reconectando (y reintenta
// con espera), sólo se atrasa la entrega, no la lectura de los siguientes mensajes.

type outJob struct {
	msgID   int64
	to      string
	text    string
	image   string // URL; si falla se envía sólo el texto
	caption string
}

const (
	sendTimeout = 45 * time.Second
	idleTimeout = 5 * time.Minute
)

func (b *Bot) enqueue(j outJob) {
	b.pending.Add(1)
	b.mu.Lock()
	defer b.mu.Unlock()
	ch, ok := b.outbox[j.to]
	if !ok {
		ch = make(chan outJob, 64)
		b.outbox[j.to] = ch
		go b.deliver(j.to, ch)
	}
	select {
	case ch <- j:
	default:
		log.Printf("bot: cola de salida llena para %s; mensaje %d descartado", j.to, j.msgID)
		go func() {
			b.markDelivery(j.msgID, "", "failed")
			b.pending.Done()
		}()
	}
}

func (b *Bot) deliver(to string, ch chan outJob) {
	idle := time.NewTimer(idleTimeout)
	defer idle.Stop()
	for {
		select {
		case j := <-ch:
			b.send(j)
			b.pending.Done()
			if !idle.Stop() {
				<-idle.C
			}
			idle.Reset(idleTimeout)
		case <-idle.C:
			b.mu.Lock()
			if len(ch) == 0 {
				delete(b.outbox, to)
				b.mu.Unlock()
				return
			}
			b.mu.Unlock()
			idle.Reset(idleTimeout)
		}
	}
}

func (b *Bot) send(j outJob) {
	ctx, cancel := context.WithTimeout(context.Background(), sendTimeout)
	defer cancel()
	var id string
	var err error
	if j.image != "" {
		id, err = b.evo.SendImage(ctx, j.to, j.image, j.caption)
		if err != nil {
			log.Printf("bot: imagen a %s falló (%v); se envía el texto", j.to, err)
			ctx2, cancel2 := context.WithTimeout(context.Background(), sendTimeout)
			id, err = b.evo.SendText(ctx2, j.to, j.caption)
			cancel2()
		}
	} else {
		id, err = b.evo.SendText(ctx, j.to, j.text)
	}
	status := "sent"
	if err != nil {
		log.Printf("bot: envío a %s falló: %v", j.to, err)
		status = "failed"
	}
	b.markDelivery(j.msgID, id, status)
}

func (b *Bot) markDelivery(msgID int64, waID, status string) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := b.store.SetMessageDelivery(ctx, msgID, waID, status); err != nil {
		log.Printf("bot: estado de envío %d: %v", msgID, err)
	}
	b.Notify("conversations")
}

// queueMessage guarda el mensaje saliente como pendiente y lo encola.
func (b *Bot) queueMessage(ctx context.Context, conv *store.Conversation, m *store.Message, j outJob) error {
	m.ConversationID, m.Direction, m.Status = conv.ID, "out", "pending"
	if err := b.store.AddMessage(ctx, m); err != nil {
		return err
	}
	j.msgID, j.to = m.ID, conv.Customer.JID
	b.enqueue(j)
	b.Notify("conversations")
	return nil
}

// Drain espera a que se entreguen los mensajes en cola (o a que venza timeout).
func (b *Bot) Drain(timeout time.Duration) bool {
	done := make(chan struct{})
	go func() {
		b.pending.Wait()
		close(done)
	}()
	select {
	case <-done:
		return true
	case <-time.After(timeout):
		return false
	}
}
