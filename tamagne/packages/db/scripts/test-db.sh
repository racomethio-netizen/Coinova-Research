#!/usr/bin/env bash
# Disposable local PostgreSQL 16 cluster for database tests. NEVER point at real data.
#
#   bash packages/db/scripts/test-db.sh up     # init + start on :54339, create DB, apply migrations
#   bash packages/db/scripts/test-db.sh down   # stop and delete everything
#
# Uses local PostgreSQL server binaries (initdb/pg_ctl). With Docker available you can
# instead run `docker compose up -d postgres` and set DATABASE_URL accordingly.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
DATA="$ROOT/.tmp/pgdata"
PORT="${TEST_DB_PORT:-54339}"
DB="tamagne_test"
PGBIN="${PGBIN:-$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)}"
PGBIN="${PGBIN:-$(dirname "$(command -v pg_ctl)")}"
URL="postgresql://postgres@127.0.0.1:$PORT/$DB"

as_pg() {
  if [ "$(id -u)" = "0" ]; then su postgres -s /bin/bash -c "$*"; else bash -c "$*"; fi
}

case "${1:-}" in
  up)
    mkdir -p "$ROOT/.tmp"
    if [ ! -d "$DATA" ]; then
      mkdir -p "$DATA"
      if [ "$(id -u)" = "0" ]; then chown -R postgres:postgres "$ROOT/.tmp"; fi
      as_pg "'$PGBIN/initdb' -D '$DATA' -U postgres --auth=trust -E UTF8 >/dev/null"
    fi
    as_pg "'$PGBIN/pg_ctl' -D '$DATA' status >/dev/null" || as_pg "'$PGBIN/pg_ctl' -D '$DATA' -o '-p $PORT -k /tmp -c listen_addresses=127.0.0.1' -l '$DATA/server.log' -w start >/dev/null"
    "$PGBIN/psql" -h 127.0.0.1 -p "$PORT" -U postgres -tAc "select 1 from pg_database where datname='$DB'" | grep -q 1 \
      || "$PGBIN/psql" -h 127.0.0.1 -p "$PORT" -U postgres -qc "create database $DB"
    (cd "$ROOT/packages/db" && DATABASE_URL="$URL" npx prisma migrate deploy >/dev/null)
    echo "Test database ready: TEST_DATABASE_URL=$URL"
    ;;
  down)
    if [ -d "$DATA" ]; then
      as_pg "'$PGBIN/pg_ctl' -D '$DATA' -m fast stop >/dev/null" || true
      rm -rf "$DATA"
    fi
    echo "Test database stopped and deleted"
    ;;
  *) echo "usage: $0 up|down" >&2; exit 1 ;;
esac
