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
| Owner approval in the append-only audit log (TOTP + exact phrase; expires after 30 days; revocable) | DB | ✅ |
| Kill switch clear (fails closed if unreadable); no `unknown` orders; balances markable | runtime | ✅ |
| Active production credential per exchange with withdrawals confirmed disabled | DB | ✅ (owner attestation; API verification for Binance/Coinbase still to add) |

`LiveBroker` can only be constructed by `live_gate.build_live_broker` (a private token
enforces it; a static test checks no other module constructs it).

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

## Authentication and API (Phase 13)

* Single owner account (`python -m nexus_api.cli create-owner`); scrypt hashes.
* Server-side sessions in Redis; HttpOnly + SameSite=Strict (+ Secure in staging/prod)
  cookie; 2 h idle / 12 h absolute expiry.
* Login: IP rate limit (20 / 15 min), account lockout (5 failures / 15 min), generic
  errors and timing equalization (no account enumeration), audit-logged.
* TOTP 2FA (RFC 6238, secret encrypted at rest); step-up 2FA with replay protection for
  clearing the kill switch, activating models, storing API keys and live approval.
  Engaging the kill switch never requires 2FA: stopping must always be easy.
* CSRF: custom header required on mutations (no CORS) + Origin check.
* Headers: CSP, X-Frame-Options DENY, nosniff, no-referrer, no-store, Permissions-Policy.
* All routes require a session; mutations are owner-only (viewer role is read-only).

## Database roles

Migrations run as the schema owner; services connect as `nexus_app`
(`infrastructure/docker/postgres-init/01-roles.sh`). Grants are re-applied after every
migration: DML only, no TRUNCATE/DDL, `audit_logs` INSERT/SELECT only, no writes to
reference tables, and, as a non-owner, the app cannot disable the audit triggers.
Tested in `tests/integration/test_db_roles.py`.

## Supply chain

`scripts/security-audit.sh`: `pip-audit` + `npm audit` + static checks. In Phase 13 the
audit found real advisories in the originally pinned versions (cryptography, starlette,
pytest, and a critical Next.js RCE in the image optimizer, a feature this static export
does not use). Everything was upgraded to the newest releases at least two weeks old.
Result: 0 known vulnerabilities (Python and npm) as of 2026-11-14.

## Static security tests (`tests/security`)

No committed secrets or `.env` files; no disabled TLS verification, `shell=True`, `eval`,
unsafe YAML or unverified pickle; no floats in money code; no withdraw/transfer/margin
calls; no secrets exposed to the browser; ports bound to localhost; capabilities dropped.

## Telegram

The bot token is part of the API URL: `httpx` request logging is silenced and errors are
reported without URLs. Tested.
