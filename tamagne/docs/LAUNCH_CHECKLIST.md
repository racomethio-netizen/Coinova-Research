# Launch checklist (go / no-go)

_Status 2026-11-09, end of Phase 8: **NO-GO.** The software is ready for a staging deployment.
A public launch is blocked by the external items in section 1. Nothing here has been deployed. No
service has been bought, and no real personal data has been loaded. Each of those steps needs the
owner's explicit approval._

Legend: ✅ done and verified · ⛔ blocked on an external party or owner decision · ☐ to do (needs
the production environment).

## 1. External blockers (decide or obtain before launch)

| # | Item | Owner | Status |
|---|------|-------|--------|
| 1.1 | **Hosting in Ethiopia**: provider and contract for PostgreSQL, object storage, backups, logs and keys with encryption at rest and audited access. Ethio telecom was mentioned; its scope (SMS / hosting / storage) is unconfirmed. | Owner | ⛔ |
| 1.2 | **SMS gateway** for phone OTP: provider, contract, sender ID. No production SMS adapter exists yet: phone sign-in cannot work in production until one is added. | Owner → dev | ⛔ |
| 1.3 | **Object storage adapter** (S3-compatible, in Ethiopia). Until it exists, document upload and manual ID review stay disabled (`STORAGE_DRIVER=disabled`); the app says so to sellers. | Dev, after 1.1 | ⛔ |
| 1.4 | **Fayda eSignet** relying-party onboarding (NIDP) and sandbox credentials, then production authorization. Until then the Fayda level stays disabled and manual review is the only identity path. | Owner | ⛔ |
| 1.5 | **Legal review**: privacy policy, terms, retention periods, DSR deadlines, breach notification, cross-border transfers, DPIA, Amharic legal translation. All documents in `docs/legal/` are drafts. | Counsel | ⛔ |
| 1.6 | **Payment provider**, if paid plans launch at the same time. Payments never affect verification; launch is possible without them. | Owner | ⛔ (optional) |
| 1.7 | **Independent penetration test** of the staging environment. | Owner | ☐ |
| 1.8 | **Native-speaker review** of all Amharic UI copy. | Owner | ☐ |

## 2. Production environment (do once 1.1 exists)

| Item | Status |
|------|--------|
| TLS 1.2+ reverse proxy/WAF with HSTS; `/api/v1/metrics` and `/api/docs` not reachable from the internet | ☐ |
| PostgreSQL 16 with `sslmode=verify-full`, roles `tamagne_owner` / `tamagne_app` / `tamagne_readonly` ([DEPLOYMENT §3](DEPLOYMENT.md)); `ALTER DATABASE tamagne SET tamagne.environment = 'production'` | ☐ |
| Redis with AUTH + TLS on a private network | ☐ |
| Secrets manager: `FIELD_ENCRYPTION_KEYS`, `HMAC_PEPPER`, `BACKUP_KEY` (separate), `METRICS_TOKEN`, Fayda private JWK; none in images or repos | ☐ |
| ClamAV with **outbound access to signature updates** (or a local mirror) and an alert on signature age (freshclam could not update in the test environment) | ☐ |
| Scheduled jobs: expiry, deletions, retention (daily); alert if not run in 48 h ([DEPLOYMENT](DEPLOYMENT.md)) | ☐ |
| WAL archiving + PITR; nightly encrypted logical backup to a second Ethiopian location; **restore drill on production-sized data** (`npm run ops:restore-drill`) | ☐ |
| Log shipping of JSON logs; alerts per [DEPLOYMENT → Observability](DEPLOYMENT.md#observability) | ☐ |
| Load test on production-like hardware (`npm run ops:load-test` against a disposable copy, never production) | ☐ |
| Bootstrap the first Super Admin (`apps/api/scripts/bootstrap-super-admin.ts`); every staff member enrols TOTP | ☐ |
| Incident response contacts filled in ([docs/legal/INCIDENT_RESPONSE.md](legal/INCIDENT_RESPONSE.md)) | ☐ |
| `.env` production values pass startup validation (the API refuses dev placeholders, `sslmode` other than verify-full, console SMS, local storage, eicar scanner, test phone numbers, OpenAPI on) | ✅ enforced in code |

## 3. Verified in the codebase (Phase 8)

| Area | Evidence |
|------|----------|
| Unit, API, DB and e2e test suites | `npm run check`, `npm run test:e2e` (see [ROADMAP](ROADMAP.md)) |
| Encrypted backup + automated restore drill | [ops/restore-drill-2026-11-09.md](ops/restore-drill-2026-11-09.md): 27 tables / 3,286 rows identical, chain intact, tamper rejected, API ready |
| Load and concurrency | [ops/load-test-2026-11-09.md](ops/load-test-2026-11-09.md): ~2,600 lookups/s with p99 33 ms, and ~500 audited writes/s, all on one shared 4-vCPU machine; every write has exactly one audit entry; chain intact |
| Container stack | [ops/compose-smoke-2026-11-09.md](ops/compose-smoke-2026-11-09.md): 23/23 checks (non-root, read-only FS, nonce CSP, Secure cookies, metrics auth, JSON logs, all jobs, audit chain) |
| Real malware scanner | Same report: ClamAV 1.4.6 detects EICAR, passes clean PDFs, scans 8 MB in ~0.8 s |
| Key rotation | `run-job.js rekey` tested end-to-end in an isolated database ([DEPLOYMENT → Key rotation](DEPLOYMENT.md#key-rotation-field-and-document-encryption)) |
| Observability | JSON logs with masking, Prometheus metrics behind a token |
| CI | `.github/workflows/tamagne-ci.yml`: tests, restore drill, e2e, image builds, compose smoke, real-ClamAV job (**not yet run on GitHub**) |

## 4. Go / no-go rule

Launch publicly only when all of these hold:

- Every item in section 1 is resolved, except 1.6 if payments are deferred.
- Section 2 is complete.
- The restore drill and load test have passed on production-like infrastructure.
- CI is green on the release commit.
- The owner has approved the release in writing.

Until 1.2 and 1.3 are done, a staging deployment can show public verification and the staff console
only, with synthetic data.
