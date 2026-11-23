#!/usr/bin/env bash
# Build the dashboard, serve it from the API on a DEDICATED e2e database, run Playwright.
# Requires: NEXUS_DATABASE_URL (a disposable db with migrations applied), NEXUS_REDIS_URL,
# NEXUS_E2E_EMAIL / NEXUS_E2E_PASSWORD for an existing owner account.
set -euo pipefail
cd "$(dirname "$0")/.."
(cd apps/dashboard && npx next build >/dev/null)
# The e2e Redis database is dedicated: reset kill switch/sessions/rate limits.
python -c "import os,redis; redis.from_url(os.environ['NEXUS_REDIS_URL']).flushdb()"
# The kill switch is durable (audit log, append-only): a previous run's engage would be
# restored. Start each run cleared by appending a clear record (e2e database only).
python - <<'PY'
import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

# This script writes fixtures: refuse any database that is not a dedicated e2e database.
if "e2e" not in (make_url(os.environ["NEXUS_DATABASE_URL"]).database or ""):
    sys.exit("refusing: NEXUS_DATABASE_URL must point at a dedicated *e2e* database")
with create_engine(os.environ["NEXUS_DATABASE_URL"]).begin() as c:
    c.execute(text("INSERT INTO audit_logs (actor, action, details) VALUES "
                   "('user:e2e-reset', 'kill_switch_cleared', '{\"reason\": \"e2e run start\"}')"))
    # Fixtures for "validation results are shown honestly" (e2e database only, labelled
    # as fixtures): one failed strategy validation and one rejected model.
    c.execute(text("""UPDATE strategies SET parameters = coalesce(parameters, '{}'::jsonb) ||
        '{"validation": {"passed": false, "failures": ["e2e fixture: not a real result"],
          "oos": {"n": 0}, "validated_at": "2026-01-01T00:00:00+00:00", "exchange": "binance"}}'::jsonb
        WHERE code = 'trend_following'"""))
    c.execute(text("""INSERT INTO ai_models (name, version, algorithm, status, feature_set,
        hyperparameters) VALUES ('e2e_fixture', '0', 'none', 'rejected', '{}', '{}')
        ON CONFLICT (name, version) DO NOTHING"""))
PY
export NEXUS_DASHBOARD_DIR="$PWD/apps/dashboard/out"
uvicorn --factory nexus_api.main:create_app --host 127.0.0.1 --port 8100 --log-level warning &
API=$!
trap 'kill $API' EXIT
for _ in $(seq 1 30); do curl -sf http://127.0.0.1:8100/health/live >/dev/null && break; sleep 0.5; done
(cd apps/dashboard && NEXUS_E2E_URL=http://127.0.0.1:8100 npx playwright test --trace off "$@")
