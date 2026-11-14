#!/usr/bin/env bash
# Print fresh secrets for .env. Does not write any file; redirect or copy yourself.
set -euo pipefail

key=$(python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('='))")
cat <<EOF
POSTGRES_PASSWORD=$(openssl rand -hex 24)
REDIS_PASSWORD=$(openssl rand -hex 24)
NEXUS_APP_DB_PASSWORD=$(openssl rand -hex 24)
NEXUS_CREDENTIAL_ENCRYPTION_KEYS=k$(date -u +%Y%m%d):${key}
NEXUS_SESSION_SECRET=$(openssl rand -hex 32)
EOF
