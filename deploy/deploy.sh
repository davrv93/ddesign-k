#!/usr/bin/env bash
# Despliegue del CRM kddesign en el EC2. Idempotente: se puede volver a correr para actualizar.
#   ./deploy/deploy.sh                 # genera .env si falta, construye y levanta
#   ./deploy/deploy.sh --nginx         # además instala el sitio en el nginx del host y pide el certificado
set -euo pipefail
cd "$(dirname "$0")/.."

DOMAIN="${DOMAIN:-kddesign.pjgfactsalud.com.pe}"

if [ ! -f .env ]; then
  echo "» Creando .env con secretos aleatorios (revisa ADMIN_PASSWORD y GEMINI_API_KEY)"
  cp .env.example .env
  for k in JWT_SECRET WEBHOOK_SECRET EVOLUTION_DB_PASSWORD EVOLUTION_GLOBAL_API_KEY EVOLUTION_INSTANCE_TOKEN; do
    sed -i "s|^$k=.*|$k=$(openssl rand -hex 24)|" .env
  done
  sed -i "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$(openssl rand -base64 12 | tr -d '/+=')|" .env
  sed -i "s|^PUBLIC_URL=.*|PUBLIC_URL=https://$DOMAIN|" .env
  echo "  Usuario del panel: admin / $(grep ^ADMIN_PASSWORD= .env | cut -d= -f2)"
fi

if ! grep -q '^GEMINI_API_KEY=.\+' .env; then
  echo "⚠  GEMINI_API_KEY está vacío en .env: el bot no podrá reconocer fotos." >&2
fi

echo "» Construyendo y levantando contenedores (proyecto 'kddesign')"
docker compose up -d --build
docker compose ps

if [ "${1:-}" = "--nginx" ]; then
  echo "» Instalando sitio nginx para $DOMAIN"
  sudo sed "s/kddesign.pjgfactsalud.com.pe/$DOMAIN/g" deploy/nginx-kddesign.conf | sudo tee /etc/nginx/sites-available/kddesign >/dev/null
  sudo ln -sf /etc/nginx/sites-available/kddesign /etc/nginx/sites-enabled/kddesign
  sudo nginx -t
  sudo systemctl reload nginx
  sudo certbot --nginx -d "$DOMAIN" --redirect --non-interactive --agree-tos -m "${CERTBOT_EMAIL:?define CERTBOT_EMAIL}" || \
    echo "⚠  certbot falló: revisa que el DNS de $DOMAIN apunte a este servidor."
fi

echo "✓ Listo. Abre https://$DOMAIN y ve a WhatsApp → Generar QR."
