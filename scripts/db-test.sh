#!/usr/bin/env bash
# Start/stop a disposable local PostgreSQL cluster for database tests.
#
#   scripts/db-test.sh up     # init + start cluster on port 54329
#   scripts/db-test.sh down   # stop and delete it
#
# Requires PostgreSQL 15+ server binaries (initdb, pg_ctl). Data lives in
# .tmp/pgdata and is destroyed by `down`. Never point this at a real database.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="$ROOT/.tmp/pgdata"
PORT="${TEST_DB_PORT:-54329}"
PGBIN="${PGBIN:-$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)}"
PGBIN="${PGBIN:-$(dirname "$(command -v pg_ctl)")}"

as_pg() {
  if [ "$(id -u)" = "0" ]; then
    # Postgres refuses to run as root; use the postgres system user.
    su postgres -s /bin/bash -c "$*"
  else
    bash -c "$*"
  fi
}

case "${1:-}" in
  up)
    mkdir -p "$ROOT/.tmp"
    if [ ! -d "$DATA" ]; then
      mkdir -p "$DATA"
      [ "$(id -u)" = "0" ] && chown -R postgres:postgres "$ROOT/.tmp"
      as_pg "'$PGBIN/initdb' -D '$DATA' -U postgres --auth=trust -E UTF8 >/dev/null"
    fi
    as_pg "'$PGBIN/pg_ctl' -D '$DATA' -o '-p $PORT -k /tmp -c listen_addresses=127.0.0.1' -l '$DATA/server.log' -w start >/dev/null"
    echo "Test database running on postgres://postgres@127.0.0.1:$PORT"
    ;;
  down)
    if [ -d "$DATA" ]; then
      as_pg "'$PGBIN/pg_ctl' -D '$DATA' -m fast stop >/dev/null" || true
      rm -rf "$DATA"
    fi
    echo "Test database stopped"
    ;;
  *)
    echo "usage: $0 up|down" >&2
    exit 1
    ;;
esac
