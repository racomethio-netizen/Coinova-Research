#!/usr/bin/env bash
# Deploy / update the production stack on the VPS (run from the repo's nexus-ai dir).
#   scripts/deploy.sh            # build, back up, migrate, restart
# Requires .env with production values (see docs/DEPLOYMENT.md). Never enables live
# trading: that stays governed by the live gates.
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.yml -f infrastructure/docker/compose.prod.yml"
set -a; . ./.env; set +a
: "${NEXUS_DOMAIN:?}" "${BACKUP_PASSPHRASE_FILE:?}"
[[ "${NEXUS_APP_ENV:-}" == "production" ]] || { echo "NEXUS_APP_ENV must be production"; exit 1; }

$COMPOSE build
if $COMPOSE ps --status running postgres | grep -q postgres; then
  echo "Pre-deploy backup..."
  $COMPOSE run --rm --no-deps -T -e BACKUP_NOW=1 backup
fi
$COMPOSE up -d postgres redis
$COMPOSE run --rm migrate
$COMPOSE up -d --remove-orphans
$COMPOSE ps
echo "Deployed. Check: curl -fsS https://$NEXUS_DOMAIN/health/live"
