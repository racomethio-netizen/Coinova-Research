#!/usr/bin/env bash
# Restore an encrypted backup into a NEW database and verify it against the live one.
#
#   scripts/restore-backup.sh <backup-file-name> [target-db] [compose args...]
#
# Example (production overlay):
#   COMPOSE="docker compose -f docker-compose.yml -f infrastructure/docker/compose.prod.yml"
#   scripts/restore-backup.sh nexus-20261114T023000Z.dump.gpg nexus_restore_check
#
# It never touches the live database: it creates <target-db> (refusing if it exists),
# restores into it and prints per-table row counts for live vs restored. To actually
# switch to a restored database, stop services and rename databases deliberately.
set -euo pipefail
FILE="${1:?backup file name (inside the backups volume)}"
TARGET="${2:-nexus_restore_check}"
COMPOSE="${COMPOSE:-docker compose -f docker-compose.yml -f infrastructure/docker/compose.prod.yml}"
[[ "$TARGET" =~ ^[a-z_][a-z0-9_]*$ ]] || { echo "invalid target db name"; exit 2; }
[[ "$TARGET" != "nexus" ]] || { echo "refusing to restore over the live database"; exit 2; }

$COMPOSE run --rm --no-deps -T --entrypoint sh backup -c "
set -eu
cd /backups
sha256sum -c '$FILE.sha256'
if psql -d postgres -Atc \"select 1 from pg_database where datname='$TARGET'\" | grep -q 1; then
  echo 'target database exists; choose another name' >&2; exit 3
fi
createdb '$TARGET'
gpg --batch --pinentry-mode loopback --passphrase-file /run/secrets/backup_passphrase \
    --decrypt '$FILE' | pg_restore --no-owner --exit-on-error -d '$TARGET'
q=\"select relname, n_live_tup from pg_stat_user_tables order by relname\"
for db in nexus '$TARGET'; do psql -d \$db -c 'analyze' >/dev/null; done
echo 'table | live rows | restored rows'
for t in \$(psql -d '$TARGET' -Atc \"select tablename from pg_tables where schemaname='public' order by 1\"); do
  a=\$(psql -d nexus -Atc \"select count(*) from public.\$t\")
  b=\$(psql -d '$TARGET' -Atc \"select count(*) from public.\$t\")
  printf '%s | %s | %s %s\n' \$t \$a \$b \$( [ \"\$a\" = \"\$b\" ] && echo OK || echo DIFF )
done
"
