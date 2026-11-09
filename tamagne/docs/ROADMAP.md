# TAMAGNE roadmap and status

_Last updated: 2026-11-09 (session 1, Phases 1–8). Launch status: **NO-GO** until the external blockers in [LAUNCH_CHECKLIST.md](LAUNCH_CHECKLIST.md) are resolved._

Legend: ✅ done and covered by automated tests · 🟡 done, partially verified ·
⛔ blocked on an external party or owner decision · ⬜ not started.

## Verified (Phase 8)

| Check | Result |
|---|---|
| `npm run typecheck` (core, db, api, web) | ✅ pass |
| `npm run lint` (typescript-eslint; Next.js rules for web) | ✅ 0 problems |
| `npm test`: core unit | ✅ 42 / 42 (incl. RFC 6238 and RFC 7636 test vectors) |
| `npm test`: API | ✅ 141 / 141; 3 opt-in tests skipped by default (Redis ✅ 3/3 with `REDIS_TEST_URL`, real ClamAV ✅ 2/2 with `CLAMAV_TEST_HOST`) |
| `npm test`: web dictionaries (EN/AM parity, Ethiopic script) | ✅ 4 / 4 |
| `npm run test:db`: integrity rules on PostgreSQL 16 | ✅ 32 / 32 (7 migrations) |
| `npm run test:e2e`: Playwright, desktop + Pixel 7 | ✅ 34 / 34 |
| `npm run build` | ✅ pass |
| Restore drill (`npm run ops:restore-drill`) | ✅ [report](ops/restore-drill-2026-11-09.md) |
| Load test (`npm run ops:load-test`) | ✅ [report](ops/load-test-2026-11-09.md) |
| Compose smoke test (`bash ops/compose-smoke.sh`) | ✅ 23 / 23, [report](ops/compose-smoke-2026-11-09.md) |
| `npm audit --omit=dev` | ✅ 0 vulnerabilities (dev-only moderate advisories: `minimatch` via typescript-eslint, `uuid` via autocannon) |
| GitHub Actions `.github/workflows/tamagne-ci.yml` | 🟡 updated (restore drill, compose smoke, real-ClamAV job); not yet run on GitHub |

Phase 1 verification (Docker images, compose stack, mutation check) is recorded in git history.

## Phase 1: repository, architecture, database ✅

Architecture document, monorepo, Prisma schema (17 required models + supporting tables),
integrity migration, core primitives, API and web skeletons, Docker, compose, CI.

## Phase 2: authentication and seller registration ✅

- ✅ Phone OTP registration and sign-in (`/auth/register`, `/auth/login`, `/auth/otp/verify`), no account enumeration, codes stored only as HMAC, 5 attempts, single use, 5-minute expiry
- ✅ Ethiopian phone normalization (`09…`, `07…`, `+251…`), synthetic `+999` numbers only when allowed (never production)
- ✅ Sessions: hashed opaque tokens, seller/staff lifetimes, idle timeout, server-side logout, immediate effect of suspension
- ✅ CSRF: Origin check + double-submit token on every mutation (login CSRF included)
- ✅ Rate limiting: Redis store (memory store in dev/tests), fail closed, `Retry-After`
- ✅ Consent: ToS + privacy recorded at registration with policy version and locale; `GET/POST /consents`, `DELETE /consents/:purpose`; public-profile visibility follows consent; withdrawing Fayda consent revokes a Fayda badge
- ✅ Sellers: `POST /sellers`, `GET/PATCH /sellers/me`; Level 1 badge issued with OTP evidence; duplicate profile → 409; server-controlled fields rejected; suspended sellers cannot edit
- ✅ Staff: role guards, TOTP MFA (enrol/confirm/verify, encrypted secret, replay protection), `/admin/me`, role-specific `/admin/audit/verify` probe, first Super Admin bootstrap script
- ✅ Audit log entries for auth, MFA, seller and consent events (no personal data in metadata, enforced)
- ✅ SMS adapter: `disabled` (default), `console` (dev only), `test` (APP_ENV=test only). **No production SMS provider yet**
- ✅ Web: register, sign-in, OTP, profile setup, dashboard (verification status per level, public-profile toggle, sign out), EN/AM, shadcn-style components, accessible errors, same-origin API proxy
- ✅ ESLint

### Phase 2 limitations
- ⛔ Production SMS: Ethio telecom was named; waiting for confirmation of scope (SMS and/or hosting) and a contract/credentials. Until then production registration cannot work (SMS_PROVIDER=disabled returns 503).
- Phone number change and account deletion flows are not built yet (Phase 7 covers deletion).
- Staff role management: done in Phase 6 (bootstrap script only for the first Super Admin).
- Policy texts (ToS, privacy) are drafts; consent records carry version `2026-11-draft`.
- Docker: API and web images rebuilt successfully after the Phase 2 changes; the web container's `/api` proxy reached the API using an address set only at runtime.

## Phase 3: profiles and QR verification ✅

- ✅ `GET /api/v1/verify/:sellerId` by Tamagne ID (typing errors normalized) or QR token; exact public field allow-list; `no-store`; disclaimer included
- ✅ Private profiles and unknown IDs indistinguishable (404); withdrawing consent hides the seller immediately
- ✅ Suspended sellers shown as suspended with no current verification; expiry evaluated at read time
- ✅ `GET /api/v1/sellers/:sellerId/qr` (`me` or own Tamagne ID only; requires a public profile), SVG + verify URL
- ✅ QR rotation by the seller and revocation by staff (role + MFA + reason); revoked codes return 410 and can never be reactivated
- ✅ Lookup rate limit (120/min/IP, CGNAT-aware), visitor IP forwarded from the web server
- ✅ Web: lookup form on the home page and `/verify`, result page (EN/AM, noindex, safety tips), language-neutral QR redirect, dashboard QR card with download and replace
- Tests: 13 new API tests; 4 new e2e scenarios (desktop + mobile)

### Phase 3 limitations / decisions for the owner
- Suspended seller + withdrawn public consent → currently hidden from buyers (privacy-first). Confirm or change.
- QR download is SVG only (PNG/print sheet later if sellers ask).
- Lookups are not logged per request (no buyer tracking). Aggregate counts can be added later if needed.

## Phase 4: business document verification ✅

- ✅ Business details (one per seller), editable in draft/rejected only; `POST /businesses`, `GET/PATCH /businesses/me`
- ✅ `POST /api/v1/businesses/documents`: consent required; byte-level type check; active-content PDFs refused; malware scan before storage; infected files never stored; AES-256-GCM encryption at rest; server-generated keys; 10 MB / 30 per hour limits
- ✅ Submission (business licence required), lock while under review
- ✅ Reviewer API: queue, detail, audited sandboxed download (clean files only), approve / reject / request changes with reasons, second reviewer for HIGH risk (both must approve), no self-review, duplicate TIN protection, re-scan
- ✅ Level 3 badge issued only through a final approving review (DB-enforced), retention dates set, in-app notification rows
- ✅ Web: business verification page (details, consent, uploads, status, reasons, submit) in EN/AM; dashboard link
- Tests: 4 new core, 21 new API (incl. ClamAV protocol and encrypted storage), 1 new e2e scenario (seller → reviewer → public page)
- Docker: API image rebuilt; runs as `node`, only the document directory is writable

### Phase 4 limitations
- ⛔ Production object storage: needs an Ethiopia-hosted S3-compatible provider (Ethio telecom?); `STORAGE_DRIVER` stays `disabled` in production until then.
- 🟡 ClamAV client verified against a protocol-accurate fake daemon only; run once against real ClamAV (`docker compose --profile scanning up -d clamav`) before launch.
- Scanning is synchronous at upload (fine at current scale); a background queue and periodic re-scans are Phase 7/8.
- No reviewer UI yet (Phase 6 admin dashboard); reviewers use the API.
- Retention periods (1 year after badge expiry, 90 days after rejection) are proposals pending legal review; the purge job is Phase 7.
- Notifications are recorded but not yet delivered or shown (Phase 6).

## Phase 5: Fayda integration adapter + manual identity review 🟡

- ✅ eSignet/OIDC relying party: discovery with issuer pinning, consent, state/nonce (hashed), PKCE S256, private_key_jwt, JWKS signature checks (RS256/PS256/ES256 only), iss/aud/azp/exp/iat/nonce/acr, signed userinfo with subject match, single-use callback bound to the signed-in user
- ✅ Only HMAC of the subject stored; name encrypted; no biometrics or ID numbers requested (config refuses them); tokens never stored or logged
- ✅ Duplicate identity across sellers refused; re-verification supersedes; consent withdrawal revokes badge and result
- ✅ Sandbox results labelled "sandbox test" everywhere on non-production; never counted on production
- ✅ Every negative test from the brief (invalid/expired JWT, wrong issuer/audience, invalid state and nonce, alg none, HS256 confusion, unknown key, replay, cross-user callback, unsigned userinfo, …) automated against a TEST provider; mutation check (removing nonce or audience validation) makes the tests fail
- ✅ Manual identity review (interim): consented ID upload (scanned, encrypted, no ID numbers), reviewer approve/reject, second review for HIGH risk, separate badge never shown as Fayda, short image retention, seller withdrawal
- ✅ Web: identity page (Fayda start or an honest "not available yet", manual review), sandbox labels on dashboard and public page
- Fix found by e2e planning: the web proxy did not forward `Location`, which would have broken the OIDC callback redirect

### Phase 5 blockers / limitations
- ⛔ **Not connected to Fayda.** Needs NIDP relying-party onboarding and sandbox credentials. Then verify against the real eSignet: acr values, claim names, userinfo JWS vs JWE (JWE not implemented), error formats.
- Proposed retention for ID images (30 / 90 days) needs legal review; the purge job is Phase 7.
- No reviewer UI yet (Phase 6).
- OTP per-IP limit is now configurable (`OTP_SEND_LIMIT_PER_IP`, default 30) because of carrier-grade NAT.

## Phase 6: admin dashboard and fraud reporting ✅

- ✅ Staff console `/[lang]/admin`: server-side gate (role → TOTP enrolment with QR → MFA per session → page role), role-filtered navigation, overview
- ✅ Business and manual-identity review screens (audited downloads, per-document decisions, reasons, risk), second-review screen
- ✅ Fraud reports: public bilingual form (enumeration-safe, encrypted contact), investigator workflow with transitions and resolution notes
- ✅ Appeals: seller page (EN/AM) and staff decisions with conflict-of-interest rule; overturned suspensions → second-reviewer reactivation; overturned document rejections re-open the business
- ✅ Seller search (ID / phone / name) and detail (masked phone), suspension, reactivation as a high-risk override, badge revocation
- ✅ Expiry monitor + idempotent expiry job with 30-day reminders
- ✅ Audit log viewer with filters, paging and hash-chain verification
- ✅ Staff management (grant/revoke; no self-changes; never the last Super Admin; sessions end with the last role)
- ✅ Admin-configurable, versioned pricing; public plans endpoint; revenue from recorded payments
- ✅ In-app notifications (bilingual templates) on the seller dashboard
- Bugs found and fixed: audit chain false-break under concurrent writers (migration + regression test); review page lost its confirmation after a decision; OTP verify per-IP limit too tight for shared IPs (now 3 × the send limit)

### Phase 6 limitations
- Admin console is English only (seller and buyer pages are bilingual).
- No payment provider, so revenue shows only recorded payments (none yet) and there is no seller subscription checkout.
- Notifications are in-app only; SMS/email delivery waits for providers.
- Staff phone numbers are masked in the console; there is no "reveal" flow.
- Super Admin grants do not need a second approver (only Super Admins can grant, and they cannot grant to themselves).

## Phase 7: security, privacy, compliance ✅

- ✅ Right of access: self-service JSON export (reports by others excluded to protect reporters), recorded as a completed request
- ✅ Right to correction: request → staff queue with due dates → outcome shown to the seller
- ✅ Right to deletion: immediate lock/sign-out/hide/QR revoke; erasure job after a 14-day grace period (files deleted, contact data and names scrubbed, legal records kept without contact data); support can cancel; number re-registrable; staff cannot self-delete
- ✅ Retention job: expired document and ID files, stale OTP challenges (badge evidence kept), sessions, Fayda requests (verified evidence kept), notifications, reporter contacts
- ✅ Consent centre: view versions, give/withdraw withdrawable consents (required ones explained)
- ✅ Per-request nonce CSP (`strict-dynamic`, no inline scripts), COOP/CORP, HSTS in production
- ✅ Security events view with thresholds (MFA failures, malware, duplicate identities, role changes, document views, exports…)
- ✅ Documents: privacy policy + terms (public pages, drafts, linked from sign-up), DPIA pre-filled, retention schedule, incident response plan, SECURITY.md control map
- Tests: 7 new API tests (privacy, retention, security events), 3 new e2e scenarios (CSP, policy pages, privacy centre)

### Phase 7 limitations / owner actions
- ⛔ Legal review of every document; statutory periods (response deadline, breach notification, retention) to confirm; data protection authority details; Amharic legal translation.
- Privacy page copy states 14 / 30 days; if `DELETION_GRACE_DAYS` or `DSR_RESPONSE_DAYS` change, update the copy too.
- Inline styles still allowed by CSP; no external penetration test yet. (Key re-encryption job added in Phase 8; HMAC pepper rotation remains manual.)
- Fraud reports about a seller survive the seller's deletion (fraud prevention); counsel to confirm.

## Phase 8: testing and production preparation ✅

- ✅ Encrypted logical backups (`ops/backup.mjs`: pg_dump streamed through AES-256-GCM, manifest with row counts) and an automated restore drill that checks rows, migrations, audit chain, triggers, tamper rejection and API readiness
- ✅ Structured JSON logs (route templates only, digit-run masking, frame-only stack traces) and Prometheus metrics behind `METRICS_TOKEN`
- ✅ Key rotation job (`run-job.js rekey`): re-encrypts all encrypted columns and stored documents under the active key with conditional updates and atomic object replace
- ✅ Load test: public lookups, SSR page, concurrent audited writes; integrity checked after every run
- ✅ Container stack smoke test (23 checks) and a real ClamAV check; API image 523 → 449 MB
- ✅ Launch checklist with go/no-go rule ([LAUNCH_CHECKLIST.md](LAUNCH_CHECKLIST.md)); CI extended
- Bugs found and fixed by this phase's testing:
  - **Records without audit entries.** Fraud reports, appeals, Fayda start and consent, staff role
    grants, plan changes and correction requests wrote the record and its audit entry separately, so
    a failed audit write left an unaudited record. Found when the load test's counts disagreed. They
    now commit in one transaction.
  - **Retention job never audited.** Its metadata key `otpChallenges` matched the forbidden-key
    filter and `recordSafe` swallowed the error. Found by the compose smoke test; regression test added.
  - **Web image build broken since Phase 5.** E2e specs importing workspace packages were type-checked
    inside the image. Tests are now excluded from the Docker context.
  - **Business review queue hid new submissions.** It showed only the oldest 100 with no total. It now
    returns the total, and the console can filter by Tamagne ID.
  - **Key-rotation test collided with parallel test files.** It now runs in an isolated database.

### Phase 8 limitations
- Measurements come from one shared 4-vCPU container; repeat the restore drill and load test on production-like hardware.
- Logical backups only; WAL archiving / PITR needs the production host.
- freshclam could not reach the ClamAV signature servers from this environment; production needs that access or a mirror.
- Identity-review, fraud-report and appeal queues show the oldest 100–200 items without a total (the business queue now has both); add totals/paging before volumes grow.
- `HMAC_PEPPER` rotation is not automated (hashes are one-way; needs a planned migration).
- Load test sends synthetic fraud reports and deletes them afterwards; their audit entries remain (append-only).

## Next steps

1. Owner: resolve the external blockers below (hosting, SMS, Fayda, legal).
2. Dev, once a provider is chosen: production SMS adapter, S3-compatible storage adapter, then a staging deployment with synthetic data.
3. Run CI on GitHub (push triggers it) and fix anything environment-specific.
4. On staging: penetration test, restore drill and load test on real infrastructure, Amharic copy review.

## Blockers and owner decisions

1. ⛔ Fayda: NIDP relying-party onboarding and sandbox credentials.
2. ⛔ Ethio telecom: confirm whether it is the SMS gateway, the production host / object storage, or all of these; then contract and credentials.
3. ⛔ Legal review: retention, cross-border transfers, consent and policy texts, Amharic copy (native-speaker review).
4. ⛔ Payment provider selection.
5. Recommended: move `tamagne/` to its own repository.

## Commands to resume

```bash
cd tamagne
npm ci
npm run db:test:up                     # PostgreSQL 16 on :54339 with migrations
npm run check                          # generate, typecheck, lint, unit + API + DB tests
npm run build && npm run test:e2e -w @tamagne/web
REDIS_TEST_URL=redis://127.0.0.1:6379/0 npm run test:redis -w @tamagne/api   # needs a local Redis
# Phase 8 operations tooling (disposable databases only)
SOURCE_URL=postgresql://postgres@127.0.0.1:54339/tamagne_test DRILL_ADMIN_URL=postgresql://postgres@127.0.0.1:54339/postgres \
  BACKUP_KEY=$(openssl rand -base64 32) npm run ops:restore-drill
DATABASE_URL=postgresql://postgres@127.0.0.1:54339/tamagne_test REDIS_URL=redis://127.0.0.1:6379/0 npm run ops:load-test
TAMAGNE_TAG=dev bash ops/compose-smoke.sh   # needs Docker; builds images if missing
npm run db:test:down
```

Local manual run: `cp .env.example .env`, start Postgres/Redis (compose), `npm run db:migrate:deploy`,
`npm run dev -w @tamagne/api` (OTP codes appear in the API log) and `npm run dev -w @tamagne/web`.
