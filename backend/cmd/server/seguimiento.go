package main

import (
	"context"
	"log"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/bot"
	"github.com/davrv93/ddesign-k/backend/internal/config"
	"github.com/davrv93/ddesign-k/backend/internal/whatsapp/cloud"
)

// seguimientos: cada minuto manda los pasos de seguimiento que ya tocan (bot.TickSeguimiento). Solo hay pasos si una
// persona activó una secuencia en el panel; apagado, no escribe a nadie.
func seguimientos(ctx context.Context, b *bot.Bot) {
	t := time.NewTicker(time.Minute)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case ahora := <-t.C:
			if n := b.TickSeguimiento(ctx, ahora); n > 0 {
				log.Printf("seguimiento: %d pasos enviados", n)
			}
		}
	}
}

// usarCloud conecta el número oficial si WA_CLOUD_TOKEN y WA_CLOUD_PHONE_NUMBER_ID están puestos. Sin el secreto de la
// app, el webhook no acepta nada (la firma no se puede comprobar): se avisa en el log.
func usarCloud(cfg *config.Config, b *bot.Bot) {
	if !cfg.WACloudListo() {
		return
	}
	c := cloud.New(cfg.WACloudURL, cfg.WACloudVersion, cfg.WACloudPhoneNumberID, cfg.WACloudWABAID, cfg.WACloudToken,
		cfg.WACloudAppSecret, cfg.WACloudVerifyToken)
	b.UsarCloud(c)
	log.Printf("whatsapp cloud: número oficial %s activo (plantillas: %v, webhook firmado: %v)", cfg.WACloudPhoneNumberID,
		cfg.WACloudWABAID != "", cfg.WACloudAppSecret != "")
}
