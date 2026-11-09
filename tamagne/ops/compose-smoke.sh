#!/usr/bin/env bash
# Full-stack smoke test of the container images with docker compose, in an isolated project
# (own volumes, ports 15432/16379/14000/13000) that is removed afterwards. Synthetic data only.
#
#   TAMAGNE_TAG=<tag> bash ops/compose-smoke.sh      # uses tamagne-{api,migrate,web}:<tag>, builds if missing
#
# Runs the API as APP_ENV=staging (production-like validation, JSON logs) with SMS, document
# storage and scanning disabled, because no production providers exist yet.
set -euo pipefail
cd "$(dirname "$0")/.."

export TAMAGNE_TAG="${TAMAGNE_TAG:-dev}"
export COMPOSE_PROJECT_NAME="tamagne-smoke"
export POSTGRES_HOST_PORT=15432 REDIS_HOST_PORT=16379 API_HOST_PORT=14000 WEB_HOST_PORT=13000
export POSTGRES_PASSWORD="smoke-$(head -c12 /dev/urandom | base64 | tr -dc a-zA-Z0-9)"
export REDIS_PASSWORD="smoke-$(head -c12 /dev/urandom | base64 | tr -dc a-zA-Z0-9)"
METRICS_TOKEN="$(head -c32 /dev/urandom | base64 | tr -dc a-zA-Z0-9)"
ENV_FILE="${TMPDIR:-/tmp}/tamagne-smoke.env"
cat >"$ENV_FILE" <<ENV
APP_ENV=staging
PUBLIC_WEB_URL=http://127.0.0.1:${WEB_HOST_PORT}
CORS_ORIGINS=http://127.0.0.1:${WEB_HOST_PORT}
OPENAPI_ENABLED=false
SMS_PROVIDER=disabled
STORAGE_DRIVER=disabled
SCANNER=disabled
ALLOW_TEST_PHONE_NUMBERS=false
LOG_FORMAT=json
METRICS_TOKEN=${METRICS_TOKEN}
FIELD_ENCRYPTION_KEYS=1:$(head -c32 /dev/urandom | base64)
FIELD_ENCRYPTION_ACTIVE_KEY_ID=1
HMAC_PEPPER=$(head -c32 /dev/urandom | base64)
ENV
export OVERRIDE="services: { api: { env_file: [\"$ENV_FILE\"] } }"
dc() { printf '%s\n' "$OVERRIDE" | docker compose --env-file /dev/null -f docker-compose.yml -f - "$@"; }
export -f dc # checks run in `bash -c` subshells

API="http://127.0.0.1:${API_HOST_PORT}"
WEB="http://127.0.0.1:${WEB_HOST_PORT}"
pass=0; fail=0
check() { # name, command...
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "  ✅ $name"; pass=$((pass+1)); else echo "  ❌ $name"; fail=$((fail+1)); fi
}
cleanup() { dc --profile tools down -v --remove-orphans >/dev/null 2>&1 || true; rm -f "$ENV_FILE"; }
# KEEP=1 leaves the stack running for inspection; the next run removes it.
[ "${KEEP:-0}" = 1 ] || trap cleanup EXIT

wait_for() { for _ in $(seq 1 60); do curl -fsS -o /dev/null "$1" && return 0; sleep 1; done; return 1; }

echo "▶ images tamagne-*:${TAMAGNE_TAG}"
cleanup_stack() { dc --profile tools down -v --remove-orphans >/dev/null 2>&1 || true; }
cleanup_stack # leftovers from an earlier KEEP=1 run
dc up -d postgres redis >/dev/null
check "migrations apply (migrate job)" dc --profile tools run --rm migrate
dc up -d api web >/dev/null
check "API ready" wait_for "$API/api/v1/health/ready"
check "web serves /en" wait_for "$WEB/en"

echo "▶ HTTP"
check "web → API proxy (/api/v1/health via web)" curl -fsS "$WEB/api/v1/health"
check "web sends nonce CSP" bash -c "curl -fsSI '$WEB/en' | grep -i '^content-security-policy:.*nonce-'"
check "web sends X-Content-Type-Options" bash -c "curl -fsSI '$WEB/en' | grep -i '^x-content-type-options: nosniff'"
check "unknown seller → 404 envelope with requestId" bash -c "curl -sS '$API/api/v1/verify/TMG-ZZZZZZZZZZ' | grep -q '\"requestId\"'"
check "OpenAPI UI disabled" bash -c "[ \"\$(curl -s -o /dev/null -w '%{http_code}' '$API/api/docs')\" = 404 ]"
check "Fayda integration reports disabled" bash -c "curl -fsS '$API/api/v1/fayda/status' | grep -q disabled"
check "metrics refuse missing token" bash -c "[ \"\$(curl -s -o /dev/null -w '%{http_code}' '$API/api/v1/metrics')\" = 401 ]"
check "metrics with token" bash -c "curl -fsS -H 'Authorization: Bearer $METRICS_TOKEN' '$API/api/v1/metrics' | grep -q tamagne_http_requests_total"
check "session cookies are Secure/__Host- (staging)" bash -c "curl -sSi '$API/api/v1/auth/csrf' | grep -i '^set-cookie: __Host-.*Secure'"

echo "▶ containers"
check "API runs as non-root (uid 1000)" bash -c "[ \"\$(dc exec -T api id -u)\" = 1000 ]"
check "web runs as non-root (uid 1000)" bash -c "[ \"\$(dc exec -T web id -u)\" = 1000 ]"
check "API filesystem is read-only" bash -c "dc exec -T api sh -c 'touch /app/x' 2>&1 | grep -q 'Read-only file system'"
check "API logs are JSON lines" bash -c "dc logs --no-log-prefix api | grep '^{' | head -5 | node -e 'let s=\"\";process.stdin.on(\"data\",d=>s+=d).on(\"end\",()=>{const l=s.trim().split(\"\\n\");if(!l.length)process.exit(1);for(const x of l){const j=JSON.parse(x);if(!j.ts||!j.level)process.exit(1)}})'"
check "access log carries route template, not the ID" bash -c "dc logs --no-log-prefix api | grep '\"route\":\"/api/v1/verify/:sellerId\"' && ! dc logs api | grep -q TMG-ZZZZZZZZZZ"
for job in expiry retention deletions rekey; do
  check "job: $job" bash -c "dc run --rm --no-deps -T api node apps/api/dist/jobs/run-job.js $job | grep -q '\"job\":\"$job\"'"
done
check "audit chain intact" bash -c "[ \"\$(dc exec -T postgres psql -U tamagne -d tamagne -Atc \"select coalesce(public.audit_log_verify_chain()::text, 'intact')\")\" = intact ]"
check "jobs wrote audit entries" bash -c "[ \"\$(dc exec -T postgres psql -U tamagne -d tamagne -Atc \"select count(distinct action) from audit_logs where action like 'job.%'\")\" = 4 ]"

echo "▶ result: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
