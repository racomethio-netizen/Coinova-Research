# Deployment and operations

_Phase 1 status: container images and a local stack exist and were verified. No
staging or production environment exists. Do not deploy, purchase services or load
real identity data without the owner's explicit approval._

## 1. Data residency first

Ethiopia's Personal Data Protection Proclamation No. 1321/2024 guides hosting. The
working assumption, pending legal confirmation:

- Production databases, object storage, backups, logs containing personal data and
  encryption keys are hosted **in Ethiopia**, with a provider under contract that
  supports encryption at rest, network isolation and audited access.
- Foreign-hosted managed platforms (Supabase, Render, Vercel, etc.) are **not**
  assumed compliant and are not used for production personal data.
- Third parties that would receive personal data (SMS, email, payments, error
  tracking, uptime monitoring) are reviewed individually; send them the minimum
  (e.g. SMS gets the phone number and message, nothing else).

## 2. Production topology (target)

- Reverse proxy/WAF with TLS 1.2+ and HSTS → `web` and `api` containers (≥ 2 replicas each).
- PostgreSQL 16 primary + synchronous standby, encrypted volumes, PITR (WAL archiving), `sslmode=verify-full`.
- Redis (private network, AUTH, TLS).
- S3-compatible private object storage in Ethiopia, server-side encryption, no public buckets.
- ClamAV (or equivalent) scanning service, private network only.
- Secrets manager / KMS (or HSM) for app secrets and the Fayda client key.
- Monitoring: metrics, structured logs (redacted), alerting on readiness, error rate,
  audit-chain verification failures, backup failures.

## 3. Database roles

| Role | Rights |
|------|--------|
| `tamagne_owner` | Owns schema; used only by the migration job |
| `tamagne_app` | `SELECT/INSERT/UPDATE` on app tables; no `DELETE`/`TRUNCATE` on history tables; not superuser; cannot disable triggers |
| `tamagne_readonly` | Reporting replica access, no identity tables |

Run once on production: `ALTER DATABASE tamagne SET tamagne.environment = 'production';`

Grant the app role `EXECUTE` on `public.audit_log_verify_chain()` (it is revoked from PUBLIC) so the
console's "Verify hash chain" works.

## Scheduled jobs

| Job | Command | Schedule |
|-----|---------|----------|
| Verification expiry + 30-day reminders | API image: `node --enable-source-maps apps/api/dist/jobs/run-job.js expiry` (dev: `npm run job -w @tamagne/api -- expiry`) | daily, e.g. 01:00 Africa/Addis_Ababa |
| Account deletions after the grace period | `… run-job.js deletions` | daily, 01:15 |
| Retention purge (files, OTP challenges, sessions, notifications, reporter contacts) | `… run-job.js retention` | daily, 01:30 |
| Re-encrypt fields and documents with the active key | `… run-job.js rekey` | on demand, after a key rotation |

All jobs are idempotent and write a `job.*` audit entry; alert if a job has not run for 48 hours.

## Key rotation (field and document encryption)

1. Generate a key (`openssl rand -base64 32`) in the secrets manager.
2. Add it to the ring and make it active: `FIELD_ENCRYPTION_KEYS=1:<old>,2:<new>`,
   `FIELD_ENCRYPTION_ACTIVE_KEY_ID=2`. Roll the API. New writes use key 2; old data stays readable.
3. Run `run-job.js rekey`. It re-encrypts MFA secrets, OIDC verifiers, verified names, reporter
   contacts and every stored document (atomic replace), and writes a `job.rekey` audit entry.
4. Remove key 1 from the ring **only** when the job reports `remaining: 0` (rows that fail to
   decrypt are counted there and left untouched; investigate them first).

`HMAC_PEPPER` cannot be rotated this way (hashes are one-way); rotating it invalidates sessions,
rate-limit buckets and identity-subject deduplication and needs a planned migration.

## Observability

- **Logs:** `LOG_FORMAT=json` (default outside development/test) writes one JSON object per line:
  `ts`, `level`, `context`, `msg`, and for each request `method`, `route` (template, e.g.
  `/api/v1/verify/:sellerId`), `status`, `durationMs`, `requestId`. No URLs, query strings, IPs,
  user agents or bodies. Digit runs that look like phone/ID numbers or 6-digit codes are masked as
  a safety net. Stack traces keep frames only, never error messages.
- **Metrics:** `GET /api/v1/metrics` (Prometheus text) when `METRICS_TOKEN` is set, with
  `Authorization: Bearer <token>`; otherwise 404. Block it at the reverse proxy from the public
  internet as well. Series: `tamagne_http_requests_total{method,route,status}`,
  `tamagne_http_request_duration_seconds`, `tamagne_rate_limited_total{rule}`,
  `tamagne_rate_limit_store_errors_total`, `tamagne_audit_write_failures_total`, plus process metrics.
- **Alert on:** readiness failing; 5xx rate; any `audit_write_failures`; `rate_limit_store_errors`
  (requests fail closed); a scheduled job not run in 48 h; `audit_log_verify_chain()` non-NULL;
  restore drill failure.

## 4. Releasing

```bash
# Build (from tamagne/)
docker build -f apps/api/Dockerfile --target runtime -t tamagne-api:<sha> .
docker build -f apps/api/Dockerfile --target migrate -t tamagne-migrate:<sha> .
docker build -f apps/web/Dockerfile -t tamagne-web:<sha> .
# Behind a TLS-intercepting proxy, add: --secret id=npm_ca,src=<proxy-ca.pem>

# Deploy: run migrations as a one-off job with the owner role, then roll the API and web.
```

Migrations are forward-only; never edit an applied migration; add a new one.

## 5. Backups and restore

Target: nightly base backups + continuous WAL (PITR), encrypted, retained per policy, stored in a
second Ethiopian location. That requires the production hosting provider and is **not set up**.

What exists and is verified (`ops/`):

- `ops/backup.mjs` — logical backup: `pg_dump -Fc` piped straight into AES-256-GCM (format
  `TMGBK1 | iv | ciphertext | tag`), so no plaintext dump touches disk. Writes a manifest
  (`<file>.json`: sha256, per-table row counts, migration count). Key: `BACKUP_KEY` (32 bytes,
  base64) from the secrets manager, **different** from `FIELD_ENCRYPTION_KEYS`.
  ```bash
  DATABASE_URL=... BACKUP_KEY=... npm run ops:backup -- /secure/path/tamagne-<date>.tmgbk
  DATABASE_URL=... TARGET_URL=... BACKUP_KEY=... node ops/backup.mjs restore --in <file>
  ```
- `ops/restore-drill.mjs` — backs up `SOURCE_URL`, restores into a scratch database whose name
  must end in `_drill`, then checks: identical row counts in every table, all migrations present,
  `audit_log_verify_chain()` intact, integrity triggers present, a tampered backup is rejected, and
  the built API reaches readiness against the restored database. Writes
  `docs/ops/restore-drill-<date>.md` and drops the scratch database.
  ```bash
  npm run build -w @tamagne/api
  SOURCE_URL=... DRILL_ADMIN_URL=... BACKUP_KEY=... npm run ops:restore-drill
  ```
  Last run: [docs/ops/restore-drill-2026-11-09.md](ops/restore-drill-2026-11-09.md) (test database, PASSED).

Run the drill at least monthly and after every schema change; alert on failure. Logical backups do
not replace PITR: with WAL archiving configured, also rehearse a point-in-time recovery.

## 6. Local development

```bash
cd tamagne
npm ci
npm run db:test:up            # disposable PostgreSQL 16 on :54339 with migrations applied
npm run check                 # generate, typecheck, unit + API + DB tests
npm run build
# or the Docker stack:
cp .env.example .env && docker compose up -d postgres redis && docker compose run --rm migrate && docker compose up -d api web
```
