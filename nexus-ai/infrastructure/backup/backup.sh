#!/bin/sh
# Daily encrypted PostgreSQL backups (custom format, AES-256 via gpg symmetric).
# Runs once at start if today's backup is missing, then daily at BACKUP_HOUR_UTC.
# Retention: files in /backups matching nexus-*.dump.gpg older than BACKUP_KEEP_DAYS.
set -eu
: "${PGHOST:?}" "${PGUSER:?}" "${PGDATABASE:?}"
PASSFILE=/run/secrets/backup_passphrase
DIR=/backups
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
HOUR="${BACKUP_HOUR_UTC:-02}"

prune() {
  for f in "$DIR"/nexus-*.dump.gpg "$DIR"/nexus-*.dump.gpg.sha256; do
    [ -f "$f" ] || continue
    age_days=$(( ( $(date -u +%s) - $(stat -c %Y "$f") ) / 86400 ))
    if [ "$age_days" -gt "$KEEP_DAYS" ]; then rm -f -- "$f"; fi
  done
}

backup() {
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  out="$DIR/nexus-$ts.dump.gpg"
  pg_dump -Fc --no-owner | gpg --batch --yes --pinentry-mode loopback \
    --passphrase-file "$PASSFILE" --symmetric --cipher-algo AES256 -o "$out.tmp"
  mv "$out.tmp" "$out"
  (cd "$DIR" && sha256sum "$(basename "$out")" > "$(basename "$out").sha256")
  prune
  echo "{\"event\":\"backup_complete\",\"file\":\"$(basename "$out")\",\"bytes\":$(wc -c < "$out")}"
}

[ -s "$PASSFILE" ] || { echo "backup passphrase missing" >&2; exit 1; }
# On-demand backup (e.g. before a deployment): BACKUP_NOW=1 runs one and exits.
if [ "${BACKUP_NOW:-0}" = "1" ]; then backup; exit 0; fi
ls "$DIR"/nexus-"$(date -u +%Y%m%d)"T*.dump.gpg >/dev/null 2>&1 || backup
[ "${BACKUP_ONCE:-0}" = "1" ] && exit 0
while true; do
  if [ "$(date -u +%H)" = "$HOUR" ] && ! ls "$DIR"/nexus-"$(date -u +%Y%m%d)"T"$HOUR"*.dump.gpg >/dev/null 2>&1; then
    backup
  fi
  sleep 300
done
