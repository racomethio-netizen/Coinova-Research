#!/bin/sh
# Runs once when the Postgres volume is first initialized. Creates the least-privilege
# application role. The schema owner (POSTGRES_USER) is used only by migrations.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE nexus_app LOGIN PASSWORD '${NEXUS_APP_DB_PASSWORD}'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO nexus_app;
SQL
