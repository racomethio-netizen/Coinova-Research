#!/usr/bin/env bash
# Build the dashboard, serve it from the API on a DEDICATED e2e database, run Playwright.
# Requires: NEXUS_DATABASE_URL (a disposable db with migrations applied), NEXUS_REDIS_URL,
# NEXUS_E2E_EMAIL / NEXUS_E2E_PASSWORD for an existing owner account.
set -euo pipefail
cd "$(dirname "$0")/.."
(cd apps/dashboard && npx next build >/dev/null)
# The e2e Redis database is dedicated: reset kill switch/sessions/rate limits.
python -c "import os,redis; redis.from_url(os.environ['NEXUS_REDIS_URL']).flushdb()"
export NEXUS_DASHBOARD_DIR="$PWD/apps/dashboard/out"
uvicorn --factory nexus_api.main:create_app --host 127.0.0.1 --port 8100 --log-level warning &
API=$!
trap 'kill $API' EXIT
for _ in $(seq 1 30); do curl -sf http://127.0.0.1:8100/health/live >/dev/null && break; sleep 0.5; done
(cd apps/dashboard && NEXUS_E2E_URL=http://127.0.0.1:8100 npx playwright test --trace off "$@")
