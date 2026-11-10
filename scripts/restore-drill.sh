#!/usr/bin/env bash
# Full-database restore drill (R-17): dump SOURCE, restore it into a scratch database on a
# LOCAL PostgreSQL, compare row counts of critical tables, and re-verify the signal hash chain.
#
#   scripts/restore-drill.sh <source-url> [--keep]
#
#   SOURCE  any reachable database, read-only use (pg_dump). For Supabase, use the
#           direct connection string from the dashboard (never commit it).
#   TARGET  RESTORE_ADMIN_URL (default: the local test cluster on port 54329). Must be local.
#           For a Supabase dump the target cluster needs the Supabase roles; the local test
#           cluster has them once tests/db/supabase-shim.sql has run (npm run test:db).
#
# The scratch database is dropped afterwards unless --keep is given. Nothing is written
# to SOURCE. Record each run (date, source, result) in docs/nexus/RUNBOOKS.md "Restore drill log".
set -euo pipefail

SOURCE="${1:?usage: scripts/restore-drill.sh <source-url> [--keep]}"
KEEP="${2:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADMIN="${RESTORE_ADMIN_URL:-postgres://postgres@127.0.0.1:${TEST_DB_PORT:-54329}/postgres}"

host="$(python3 -c 'import sys,urllib.parse as u; print(u.urlparse(sys.argv[1]).hostname or "")' "$ADMIN")"
case "$host" in 127.0.0.1|localhost|::1) ;; *) echo "Refusing: restore target must be local (got $host)" >&2; exit 2 ;; esac

SCRATCH="restore_drill_$(date -u +%Y%m%d%H%M%S)"
TARGET="$(python3 -c 'import sys,urllib.parse as u; p=u.urlparse(sys.argv[1]); print(p._replace(path="/"+sys.argv[2]).geturl())' "$ADMIN" "$SCRATCH")"
DUMP="$(mktemp -d)/drill.dump"
cleanup() {
  rm -rf "$(dirname "$DUMP")"
  if [ "$KEEP" != "--keep" ]; then psql "$ADMIN" -qAtc "drop database if exists $SCRATCH with (force)" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

TABLES="public.signals public.signal_events public.signal_outcomes public.signal_annotations public.signal_evaluations
public.market_candles public.profiles public.subscriptions public.billing_events public.paper_accounts public.paper_orders
public.admin_audit_logs public.membership_plans public.plan_features"

count_all() {
  for t in $TABLES; do
    if [ "$(psql "$1" -qAtc "select to_regclass('$t') is not null")" = "t" ]; then
      printf '%s %s\n' "$t" "$(psql "$1" -qAtc "select count(*) from $t")"
    else
      printf '%s missing\n' "$t"
    fi
  done
}

echo "1/4 dump source"
started=$(date +%s)
pg_dump --format=custom --no-owner --no-privileges --schema=public --file="$DUMP" "$SOURCE"
echo "    $(du -h "$DUMP" | cut -f1) in $(( $(date +%s) - started ))s"

echo "2/4 restore into $SCRATCH (local)"
psql "$ADMIN" -qAtc "create database $SCRATCH" >/dev/null
# Supabase-provided schemas (auth, extensions, roles) come from the same shim the DB tests use.
psql "$TARGET" -qAt -v ON_ERROR_STOP=1 -f "$ROOT/tests/db/supabase-shim.sql" >/dev/null
# Only user ids are copied from auth.users (foreign keys need them; no emails or metadata).
psql "$SOURCE" -qAtc "copy (select id from auth.users) to stdout" | psql "$TARGET" -qAtc "copy auth.users (id) from stdin"
errors="$(mktemp)"
pg_restore --no-owner --no-privileges --dbname="$TARGET" "$DUMP" 2>"$errors" || true
# `public` exists in every new database, so that one error is expected; anything else fails the drill.
if grep -E '^pg_restore: error' "$errors" | grep -vq 'schema "public" already exists'; then
  grep -E 'error|Command was' "$errors" | head -20 >&2
  echo "Restore drill FAILED: pg_restore reported errors" >&2
  exit 1
fi
echo "    restored in $(( $(date +%s) - started ))s total"

echo "3/4 compare row counts"
diff <(count_all "$SOURCE") <(count_all "$TARGET") && echo "    row counts match"

echo "4/4 verify the signal hash chain on the restored copy"
(cd "$ROOT/engine" && DATABASE_URL="$TARGET" APP_ENV=test uv run --quiet nexus-engine verify-chain)
echo "Restore drill passed: $SCRATCH ($( [ "$KEEP" = "--keep" ] && echo kept || echo dropped ))"
