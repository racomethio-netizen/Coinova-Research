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

## 5. Backups and restore (Phase 8 deliverable)

Nightly base backups + continuous WAL, encrypted, retained per policy, stored in a
second Ethiopian location. A scheduled restore test into an isolated environment
must pass (row counts, `audit_log_verify_chain()` returns NULL, app readiness).

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
