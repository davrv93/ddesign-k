// Package auth emite y verifica tokens firmados (HMAC-SHA256) para el panel.
package auth

import (
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"strings"
	"time"
)

type Claims struct {
	User string `json:"u"`
	Exp  int64  `json:"exp"`
}

type Auth struct {
	secret   []byte
	user     string
	password string
	TTL      time.Duration
}

func New(secret, user, password string) *Auth {
	return &Auth{secret: []byte(secret), user: user, password: password, TTL: 7 * 24 * time.Hour}
}

// Login compara en tiempo constante y devuelve un token.
func (a *Auth) Login(user, password string) (string, error) {
	okU := subtle.ConstantTimeCompare([]byte(user), []byte(a.user)) == 1
	okP := subtle.ConstantTimeCompare([]byte(password), []byte(a.password)) == 1
	if !okU || !okP {
		return "", errors.New("usuario o contraseña incorrectos")
	}
	return a.sign(Claims{User: user, Exp: time.Now().Add(a.TTL).Unix()}), nil
}

func (a *Auth) sign(c Claims) string {
	payload, _ := json.Marshal(c)
	p := base64.RawURLEncoding.EncodeToString(payload)
	m := hmac.New(sha256.New, a.secret)
	m.Write([]byte(p))
	return p + "." + base64.RawURLEncoding.EncodeToString(m.Sum(nil))
}

func (a *Auth) Verify(token string) (*Claims, error) {
	p, sig, ok := strings.Cut(token, ".")
	if !ok {
		return nil, errors.New("token inválido")
	}
	m := hmac.New(sha256.New, a.secret)
	m.Write([]byte(p))
	want := m.Sum(nil)
	got, err := base64.RawURLEncoding.DecodeString(sig)
	if err != nil || !hmac.Equal(got, want) {
		return nil, errors.New("token inválido")
	}
	raw, err := base64.RawURLEncoding.DecodeString(p)
	if err != nil {
		return nil, errors.New("token inválido")
	}
	var c Claims
	if err := json.Unmarshal(raw, &c); err != nil {
		return nil, errors.New("token inválido")
	}
	if time.Now().Unix() > c.Exp {
		return nil, errors.New("sesión expirada")
	}
	return &c, nil
}
