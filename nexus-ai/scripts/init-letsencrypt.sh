#!/usr/bin/env bash
# First-time TLS certificate: Nginx needs a certificate to start and certbot needs Nginx
# for the HTTP-01 challenge. Bootstrap with a short-lived self-signed certificate, start
# Nginx, obtain the real certificate, then reload.
#   scripts/init-letsencrypt.sh <admin-email>
set -euo pipefail
cd "$(dirname "$0")/.."
EMAIL="${1:?usage: init-letsencrypt.sh <admin-email>}"
COMPOSE="docker compose -f docker-compose.yml -f infrastructure/docker/compose.prod.yml"
set -a; . ./.env; set +a
: "${NEXUS_DOMAIN:?}"
LIVE="/etc/letsencrypt/live/$NEXUS_DOMAIN"

$COMPOSE run --rm --no-deps --entrypoint sh certbot -c "
  [ -f $LIVE/fullchain.pem ] && exit 0
  mkdir -p $LIVE && apk add --no-cache openssl >/dev/null 2>&1 || true
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 -subj '/CN=$NEXUS_DOMAIN' \
    -keyout $LIVE/privkey.pem -out $LIVE/fullchain.pem"
$COMPOSE up -d nginx
$COMPOSE run --rm --no-deps --entrypoint sh certbot -c "
  rm -rf $LIVE /etc/letsencrypt/archive/$NEXUS_DOMAIN /etc/letsencrypt/renewal/$NEXUS_DOMAIN.conf
  certbot certonly --webroot -w /var/www/certbot -d $NEXUS_DOMAIN --email $EMAIL \
    --agree-tos --no-eff-email --non-interactive"
$COMPOSE exec nginx nginx -s reload
echo "Certificate installed for $NEXUS_DOMAIN (renewed automatically by the certbot service)."
