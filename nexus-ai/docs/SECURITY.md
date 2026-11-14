# Security model

## Live-trading activation (never automatic)

Live orders require **all** of the following. Each is independent; none is set by
any code path, migration or default.

| Gate | Where | Status |
|---|---|---|
| `NEXUS_TRADING_MODE=live` | env | ✅ enforced (Phase 1) |
| `NEXUS_LIVE_TRADING_ENABLED=true` (rejected unless mode is live) | env | ✅ |
| `NEXUS_LIVE_TRADING_ACK` equals exactly `I accept that live trading can lose real money` | env | ✅ |
| `NEXUS_APP_ENV=production`, no sandbox endpoints | env | ✅ |
| Owner approval recorded in the DB (authenticated, TOTP-confirmed, audit-logged) | DB | ⬜ Phase 8/13 |
| Kill switch cleared; no unreconciled orders; balances reconciled | runtime | ⬜ Phase 7/8 |
| Exchange key verified to have withdrawals disabled (or owner-confirmed where the exchange can't report it) | DB | ⬜ Phase 3/13 |

`Settings.live_trading_config_problems()` lists every unmet configuration gate; the
API exposes only the boolean.

## Credentials

* Exchange keys are entered via the dashboard (Phase 11/13), never in env files.
* Encrypted with AES-256-GCM (`nexus_security.crypto`), random nonce, key ID in the
  ciphertext, and associated data binding `{user_id, exchange, credential_id, field}`,
  so a ciphertext moved to another row/field fails authentication.
* Master keys: `NEXUS_CREDENTIAL_ENCRYPTION_KEYS` (`id:base64,...`); first encrypts,
  all decrypt. Rotation: prepend a new key, re-encrypt rows where
  `needs_rotation()` is true, then remove the old key.
* Required key permissions: **read + spot trade only**. No withdrawal, no transfer, no
  margin/futures. Use exchange IP allowlisting to the VPS address.
* Only a 12-hex-char SHA-256 fingerprint of the API key is shown/logged.

## Logging

JSON logs; a redaction processor replaces values of keys that look sensitive
(`secret`, `token`, `api_key`, `password`, `authorization`, …) and bearer tokens/PEM
blocks inside strings. Verified in tests and against live container logs.

## Database

* App connects as a non-superuser; Postgres is not published beyond 127.0.0.1.
* `audit_logs` is append-only (UPDATE/DELETE/TRUNCATE blocked by triggers).
* Mode isolation and invariants enforced by constraints (see ARCHITECTURE.md).

## Containers

Non-root user (uid 10001), `no-new-privileges`, all capabilities dropped, read-only
root filesystem for the API, memory limits, ports bound to localhost; Nginx terminates
HTTPS in production (Phase 14). Docs/OpenAPI are disabled in production.

## Still to do (tracked in ROADMAP.md)

Authentication (Argon2id + TOTP, session cookies, CSRF), authorization on every
endpoint, rate limiting, firewall (ufw: 22/80/443 only), fail2ban, backup encryption,
dependency scanning, and the Phase 13 security test suite.
