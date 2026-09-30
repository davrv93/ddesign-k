// Package config lee la configuración del backend desde variables de entorno.
package config

import (
	"log"
	"os"
	"strconv"
	"strings"
)

type Config struct {
	Port         string
	DataDir      string
	BusinessName string
	PublicURL    string // URL pública del panel (https://kddesign...), usada en mensajes del bot

	AdminUser     string
	AdminPassword string
	JWTSecret     string

	EvolutionURL           string // URL interna de evolution-go (http://evolution:8080)
	EvolutionGlobalKey     string
	EvolutionInstance      string
	EvolutionInstanceToken string
	WebhookBaseURL         string // URL con la que evolution-go alcanza a este backend (http://backend:8080)
	WebhookSecret          string
	MediaBaseURL           string // URL con la que evolution-go descarga imágenes del catálogo

	GeminiAPIKey        string
	GeminiModel         string
	GeminiFallbackModel string
	GeminiTimeoutSec    int

	WhatsAppNumber string // número público de la tienda (sólo dígitos) para los enlaces wa.me del catálogo

	MatchThreshold float64
	SeedCatalog    bool
	Currency       string
}

func Load() *Config {
	c := &Config{
		Port:         env("PORT", "8080"),
		DataDir:      env("DATA_DIR", "./data"),
		BusinessName: env("BUSINESS_NAME", "Baruka Design"),
		PublicURL:    strings.TrimRight(env("PUBLIC_URL", ""), "/"),

		AdminUser:     env("ADMIN_USER", "admin"),
		AdminPassword: env("ADMIN_PASSWORD", ""),
		JWTSecret:     env("JWT_SECRET", ""),

		EvolutionURL:           strings.TrimRight(env("EVOLUTION_URL", "http://evolution:8080"), "/"),
		EvolutionGlobalKey:     env("EVOLUTION_GLOBAL_API_KEY", ""),
		EvolutionInstance:      env("EVOLUTION_INSTANCE", "kddesign"),
		EvolutionInstanceToken: env("EVOLUTION_INSTANCE_TOKEN", ""),
		WebhookBaseURL:         strings.TrimRight(env("WEBHOOK_BASE_URL", "http://backend:8080"), "/"),
		WebhookSecret:          env("WEBHOOK_SECRET", ""),
		MediaBaseURL:           strings.TrimRight(env("MEDIA_BASE_URL", ""), "/"),

		GeminiAPIKey:        env("GEMINI_API_KEY", ""),
		GeminiModel:         env("GEMINI_MODEL", "gemini-3.5-flash-lite"),
		GeminiFallbackModel: env("GEMINI_FALLBACK_MODEL", "gemma-4-26b-a4b-it,gemma-4-31b-it"),
		GeminiTimeoutSec:    envInt("GEMINI_TIMEOUT_SECONDS", 40),

		WhatsAppNumber: strings.Trim(env("WHATSAPP_NUMBER", ""), "+ "),

		MatchThreshold: envFloat("MATCH_THRESHOLD", 0.6),
		SeedCatalog:    env("SEED_CATALOG", "true") == "true",
		Currency:       env("CURRENCY", "S/"),
	}
	if c.MediaBaseURL == "" {
		c.MediaBaseURL = c.WebhookBaseURL
	}
	if c.AdminPassword == "" {
		log.Fatal("ADMIN_PASSWORD es obligatorio")
	}
	if c.JWTSecret == "" {
		log.Fatal("JWT_SECRET es obligatorio")
	}
	if c.WebhookSecret == "" {
		log.Fatal("WEBHOOK_SECRET es obligatorio")
	}
	if c.GeminiAPIKey == "" {
		log.Println("aviso: GEMINI_API_KEY vacío; el reconocimiento de fotos queda desactivado")
	}
	return c
}

func env(k, def string) string {
	if v, ok := os.LookupEnv(k); ok && v != "" {
		return v
	}
	return def
}

func envInt(k string, def int) int {
	if n, err := strconv.Atoi(os.Getenv(k)); err == nil {
		return n
	}
	return def
}

func envFloat(k string, def float64) float64 {
	if n, err := strconv.ParseFloat(os.Getenv(k), 64); err == nil {
		return n
	}
	return def
}
