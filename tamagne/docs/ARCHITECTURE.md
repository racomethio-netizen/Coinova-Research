# TAMAGNE architecture

_Status: Phase 4 (business document verification), 2026-11-07._

TAMAGNE ("ታማኝ", trustworthy) is an independent private platform that lets Ethiopian
online sellers prove who they are, and lets buyers check that proof through a
Tamagne ID, a public profile or a QR code.

TAMAGNE is **not** a government institution and is not affiliated with the National
ID Program (NIDP). Verification confirms an identity or documents at a point in
time. It does **not** guarantee honesty, product authenticity or delivery. Every
public surface says so.

---

## 1. Business rules and where they are enforced

Each rule is enforced in at least two layers. The database layer holds even if
application code is wrong.

| # | Rule | Application layer | Database layer |
|---|------|-------------------|----------------|
| 1 | Fayda identity and business verification are separate | Separate levels in `@tamagne/core` `buildPublicVerificationSummary` | `VerificationLevel` enum; `badge_method_matches_level` CHECK |
| 2 | Phone verification is not identity verification | Summary reports `phone` and `faydaIdentity` independently (unit-tested) | Level 1 badge can only use method `PHONE_OTP` |
| 3 | Verification is not a guarantee | Disclaimer copy in EN/AM on every public page and in the OpenAPI description | n/a |
| 4 | No Fayda badge without a successful, validated Fayda response | Adapter (Phase 5) records `VERIFIED` only after full token validation | `iv_verified_requires_validation` CHECK; `seller_badge_guard` trigger requires a `VERIFIED`, signature-verified result for the same seller |
| 5 | No simulated production Fayda | `FAYDA_MODE` is `disabled` by default; production mode refuses sandbox issuers and non-production `APP_ENV` | With `tamagne.environment = 'production'` set on the database, sandbox requests/results are rejected and cannot back a badge |
| 6 | Manual workflow while awaiting Fayda authorization | Separate `IDENTITY_MANUAL_REVIEW` category, labelled "Identity reviewed manually" | Its badge needs a final approving `IDENTITY_MANUAL` review and can never be a `FAYDA_IDENTITY` badge |
| 7 | Never presented as government | Copy, brand, footer, OpenAPI text | n/a |
| 8 | Appeals and corrections | `Appeal` (incl. `DATA_CORRECTION`) and `DataSubjectRequest` | One open appeal per subject; submitter can never decide it |
| – | Payment never overrides verification | Billing module has no access to badge writes | `payments` has foreign keys only to `users` and `subscriptions` (tested) |
| – | Second reviewer for high-risk overrides | Review service (Phase 6) | `HIGH` risk ⇒ `requiresSecondReview`; FINAL needs a *different* second reviewer; FINAL is immutable |

## 2. System context

```
                 Buyers (web, QR scan)          Sellers (web, mobile web)        Staff (admin, MFA)
                          │                              │                              │
                          ▼                              ▼                              ▼
                ┌───────────────────────────── HTTPS (TLS 1.2+, HSTS) ─────────────────────────────┐
                │                       Reverse proxy / WAF (Ethiopia-hosted)                       │
                └───────────────┬───────────────────────────────────────┬──────────────────────────┘
                                ▼                                       ▼
                     ┌────────────────────┐                  ┌──────────────────────┐
                     │  apps/web          │  server-side     │  apps/api (NestJS)   │◄── Marketplace partners
                     │  Next.js (EN/AM)   │─────────────────►│  REST /api/v1, OAS   │    (API keys, scoped)
                     └────────────────────┘                  └──┬───┬───┬───┬───┬───┘
                                                                │   │   │   │   │
        ┌───────────────────────────────────────────────────────┘   │   │   │   └──────────────────┐
        ▼                         ▼                                 ▼   ▼                          ▼
 ┌──────────────┐        ┌────────────────┐               ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐
 │ PostgreSQL 16│        │ Redis          │               │ Object store │ │ Malware scan │ │ Worker (jobs)    │
 │ (primary +   │        │ rate limits,   │               │ private, SSE │ │ (ClamAV)     │ │ expiry, retention│
 │ replica, PITR│        │ OTP throttle,  │               │ Ethiopia     │ │              │ │ notifications    │
 │ backups)     │        │ job queue      │               └──────────────┘ └──────────────┘ └──────────────────┘
 └──────────────┘        └────────────────┘
        External, each behind an adapter with a disabled state:
        Fayda eSignet (OIDC, NIDP) · SMS gateway (OTP) · payment providers (e.g. Telebirr, Chapa) · email
```

## 3. Repository structure

```
tamagne/
├── apps/
│   ├── api/                 NestJS 12 (ESM) REST API
│   │   ├── src/config/      Zod-validated environment (Fayda disabled state, production guards)
│   │   ├── src/database/    PrismaService
│   │   ├── src/health/      liveness / readiness
│   │   ├── src/fayda/       Fayda status (Phase 1); OIDC adapter (Phase 5)
│   │   ├── src/http/        error envelope, request IDs, body-parser hardening
│   │   ├── test/            config + HTTP tests (supertest)
│   │   └── Dockerfile       runtime + migrate targets
│   │   ├── src/auth/        phone OTP, sessions, guards, staff TOTP MFA
│   │   ├── src/security/    CSRF, rate limiting (Redis), field crypto
│   │   ├── src/sellers/     seller profile + Level 1 badge;  src/consents/ consent records
│   │   ├── src/audit/       audit log writer;  src/sms/ SMS adapter (disabled/console/test)
│   │   └── scripts/         OpenAPI export, first Super Admin bootstrap
│   └── web/                 Next.js 16 App Router, Tailwind 4, shadcn-style UI, EN/AM, self-hosted fonts
│       └── src/app/api/[...path]  same-origin runtime proxy to the API
├── packages/
│   ├── core/                framework-free rules and security primitives (IDs, crypto, verification summary)
│   └── db/                  Prisma 7 schema, migrations, client factory, DB integrity tests
├── docs/                    architecture, roadmap, Fayda guide, deployment, OpenAPI export
├── docker-compose.yml       local development stack (synthetic data only)
└── .env.example
```

Planned additions: `apps/worker` (Phase 2: OTP SMS, notifications; Phase 4: scans, retention),
`apps/api/src/modules/*` per feature, `docs/legal/*` templates (Phase 7).

**Why a separate NestJS API instead of Next.js route handlers?** The API serves three
clients (web, admin, marketplace partners), needs a published OpenAPI contract, and
must be deployable and scaled independently of the public site. Next.js renders UI
and calls the API server-side.

## 4. Data model

The full schema is `packages/db/prisma/schema.prisma`; integrity rules are in
`packages/db/prisma/migrations/*_integrity_rules/migration.sql`.

### 4.1 Entities

| Area | Models |
|------|--------|
| Accounts | `User`, `Session`, `MfaFactor`, `PhoneOtpChallenge`, `StaffRoleAssignment` |
| Sellers | `SellerProfile`, `Business`, `BusinessDocument` |
| Identity | `OidcAuthRequest`, `IdentityVerification`, `ConsentRecord` |
| Verification state | `SellerBadge` (one row per level grant), `VerificationEvent` (append-only history), `ReviewRecord` |
| Public lookup | `QrToken` |
| Trust and safety | `FraudReport`, `Appeal` |
| Audit | `AuditLog` (append-only, hash-chained) |
| Billing | `Plan`, `PlanPrice` (versioned, admin-set), `Subscription`, `Payment` |
| Integrations | `ApiClient`, `Notification` |
| Privacy | `DataSubjectRequest` |

### 4.2 Identifiers

| Identifier | Form | Purpose |
|------------|------|---------|
| Primary keys | UUIDv4 from `gen_random_uuid()` | Internal only |
| Tamagne ID | `TMG-` + 10 Crockford base32 chars (50 random bits) | Public seller identifier for lookups. Not sequential; typing errors (I/L/O) are normalized |
| QR token | 256-bit random, base64url (43 chars) | Encoded in QR codes. One active per seller; revocable, rotatable, never reactivated |
| Identity subject | HMAC-SHA-256(pepper, eSignet `sub`) | Duplicate detection only. The raw subject, FIN and FAN are never stored, and never used as keys |
| Session / API key / OAuth state | Random ≥ 256-bit, stored as SHA-256 | Bearer secrets are never stored in clear |

### 4.3 Verification levels

| Level | Category | Method | Evidence (FK) | Shown publicly as |
|-------|----------|--------|---------------|-------------------|
| 1 | `PHONE` | `PHONE_OTP` | consumed `PhoneOtpChallenge` | "Phone verified" |
| 2 | `FAYDA_IDENTITY` | `FAYDA_ESIGNET_OIDC` | `VERIFIED` `IdentityVerification` | "Fayda identity verified" |
| interim | `IDENTITY_MANUAL_REVIEW` | `MANUAL_IDENTITY_REVIEW` | final approving `ReviewRecord` | "Identity reviewed manually" (never "Fayda") |
| 3 | `BUSINESS_DOCUMENTS` | `MANUAL_DOCUMENT_REVIEW` | final approving `BUSINESS` review | "Business documents verified" |
| 4 | `ENHANCED_REVIEW` | `ENHANCED_BUSINESS_REVIEW` | final approving `ENHANCED_REVIEW` review | "Enhanced business review" |

Each badge row carries status (`PENDING/ACTIVE/SUSPENDED/EXPIRED/REVOKED`), issue date,
expiry date, method, evidence reference and revocation fields; `VerificationEvent`
holds the history. `REVOKED` and `EXPIRED` are terminal: renewal creates a new row,
so history is never overwritten. Badges cannot be deleted. A badge can only be
activated for an `ACTIVE` seller. A Fayda badge cannot outlive its identity verification.

### 4.4 Protection of sensitive fields

| Data | Storage |
|------|---------|
| Phone number | Clear (needed for SMS), unique, private; never public; nulled on deletion |
| Password | Argon2id hash (Phase 2) |
| OTP code | HMAC only; ≤ 15 min lifetime; ≤ 10 attempts (DB-enforced) |
| Fayda subject / FIN / FAN | Not stored. HMAC of subject only |
| Verified legal name from Fayda | AES-256-GCM (`verifiedNameEnc`), AAD-bound to column and row, key ring with rotation |
| PKCE verifier | AES-256-GCM (`codeVerifierEnc`), single use, ≤ 15 min |
| Biometric data | Never requested, never stored. The `picture` claim is not requested |
| Business documents | Private object storage, server-generated keys, SHA-256, malware scan before review |
| IP addresses | HMAC (`ipHash`) for abuse controls; never stored raw |
| Anonymous reporter contact | AES-256-GCM |
| Audit metadata | IDs and codes only; never personal data or secrets |

### 4.5 Database-level integrity (selected)

- CHECK constraints for formats (E.164, Tamagne ID, QR token, SHA-256 hex, ISO currency, TIN), bounds and state consistency.
- Partial unique indexes: one live badge per level; one active QR per seller; one live consent per purpose; one live staff role grant; one `VERIFIED` identity per subject (duplicate sellers); a TIN or registration number backs one verified business; one open appeal per subject; one current price per plan/interval/currency; a Fayda authorization response or token `jti` is redeemable once.
- Triggers: badge issuance guard; consent records only `GRANTED → WITHDRAWN`; reviewers must hold a live staff role and may not review their own account; FINAL reviews immutable; staff role grants never deleted or re-activated; QR tokens never reactivated; `verification_events` and `audit_logs` append-only (including `TRUNCATE`).
- `audit_logs` is hash-chained; `audit_log_verify_chain()` returns the first tampered row. Tests prove tampering by a privileged user who disables the trigger is detected.
- All trigger functions use `SET search_path = ''`.

## 5. Verification flows

### 5.1 Level 1: phone OTP (implemented in Phase 2)
1. `POST /api/v1/auth/register` (phone, `acceptTerms`, `acceptPrivacy`, locale) or `POST /api/v1/auth/login` (phone).
   Ethiopian numbers are normalized to E.164 (`+2519…`/`+2517…`). Rate limits (Redis): 1 per phone per 60 s,
   3 per phone per 15 min, 10 per IP hash per 15 min.
2. A `PhoneOtpChallenge` is stored with HMAC(pepper, challengeId, code) and a 5-minute expiry. The SMS is sent only
   if the number can actually sign in; responses are identical either way, so the endpoint cannot be used to
   discover which numbers are registered.
3. `POST /api/v1/auth/otp/verify`: the attempt is counted atomically before checking (max 5), the code is compared
   in constant time, the challenge is consumed exactly once, the account is created on first registration
   (with ToS and privacy consent records), a session starts, and the event is audited.
4. `POST /api/v1/sellers` creates the profile and issues the Level 1 badge, evidenced by that consumed challenge.

### 5.2 Level 2: Fayda eSignet OIDC (Phase 5)

```
Seller            Web/API                                  Fayda eSignet
  │ consent ────► ConsentRecord(FAYDA_IDENTITY_VERIFICATION)
  │ start ──────► POST /api/v1/fayda/start
  │               state, nonce (random); PKCE verifier→S256 challenge
  │               OidcAuthRequest{stateHash, nonceHash, verifierEnc, ≤10 min}
  │ ◄──────────── 302 authorize?response_type=code&client_id&redirect_uri
  │                  &scope&state&nonce&code_challenge&acr_values&claims_locales
  │ authenticate at eSignet ───────────────────────────────►│
  │ ◄──────────────────────────── 302 callback?code&state ──┤
  │ GET /api/v1/fayda/callback
  │               1. look up by HMAC(state); unexpired; unconsumed; same session user
  │               2. mark consumed (single use, inside a transaction)
  │               3. token request: code + code_verifier + private_key_jwt assertion ──►
  │               4. id_token: verify JWS against issuer JWKS (pinned algs), iss, aud,
  │                  azp, exp/iat/nbf (small skew), nonce == stored nonce, acr if required
  │               5. userinfo (signed JWT): verify signature, iss, aud; sub matches id_token
  │               6. IdentityVerification VERIFIED {subjectHash, iss, aud, jti, acr, signatureVerified}
  │               7. badge FAYDA_IDENTITY (DB trigger re-checks evidence)
  │               8. VerificationEvent + AuditLog (no claims, no tokens)
```

Any failure records `FAILED` with a short code (e.g. `invalid_signature`, `nonce_mismatch`,
`issuer_mismatch`, `expired`), never the token. Tokens are discarded after validation.
See `docs/FAYDA_INTEGRATION.md`.

### 5.3 Manual identity review (interim)
Seller uploads an ID document image via the document pipeline (scan → private
storage). A Verification Officer compares and records a `ReviewRecord`
(`IDENTITY_MANUAL`). A `HIGH`-risk case requires a second officer. Result: an
`IDENTITY_MANUAL_REVIEW` badge with a shorter validity. The public page never calls
it Fayda verification. Source images are deleted per the retention schedule.

### 5.4 Level 3: business documents (implemented in Phase 4)

Seller side (`/api/v1/businesses…`, web `/[lang]/dashboard/business`):
1. Business details (one business per seller, DB-enforced): legal name, legal form, optional registration
   number, TIN (10 digits), region, city. Editable only in `DRAFT`/`REJECTED`.
2. `BUSINESS_DOCUMENT_PROCESSING` consent is required before any upload or submission.
3. `POST /api/v1/businesses/documents` (multipart, 1 file, ≤ 10 MB, 30 uploads/hour/user):
   type decided from the file's bytes (PDF/JPEG/PNG only; declared type must agree) → PDFs with
   JavaScript, launch actions, embedded files, RichMedia or XFA refused → malware scan →
   **infected files are never stored** (row kept as `INFECTED`, purged, audited with the signature
   name) → AES-256-GCM encryption bound to the storage key → private object storage under a
   server-generated key (`business-documents/<businessId>/<uuid>`). The original file name is not kept.
4. Submit needs a clean, non-rejected business licence. The business is then locked.

Reviewer side (`/api/v1/admin/…`, Super Admin / Verification Officer, MFA required):
- Queue (oldest first), business detail with document metadata and review history, document download
  (only `CLEAN` files; `Content-Disposition: attachment`, `CSP: sandbox`, every view audited).
- Decision: `APPROVE` (every document approved and the required types present), `REJECT` or
  `REQUEST_CHANGES`, each with a reason; non-approved documents need their own reason. Reviewers cannot
  review their own seller account.
- `HIGH` risk decisions wait for a different second reviewer (Super Admin, Verification Officer or
  Security Officer). The business is approved only if **both** approve; otherwise the more cautious outcome applies.
- Approval: business `VERIFIED`, any previous business badge superseded, Level 3 badge issued for 365 days
  (the DB trigger re-checks the final approving review), documents get `retainUntil` = badge expiry + 1 year,
  an in-app notification is recorded. Rejection: reason shown to the seller, documents retained 90 days for appeals.
- A TIN or registration number can back only one verified business: a duplicate approval returns 409.
- Scanner outage: documents stay `FAILED`, cannot be submitted, approved or downloaded; staff can re-scan.

Adapters: storage `disabled` | `local` (dev/test, encrypted files, 0600) — **production object storage in
Ethiopia is not implemented yet** (provider decision). Scanner `disabled` | `clamav` (clamd INSTREAM over TCP)
| `eicar-only` (dev/test). The ClamAV client is tested against a protocol-accurate fake daemon, **not yet
against a real ClamAV**.

### 5.5 Expiry, suspension, revocation, appeals
- Worker job marks `ACTIVE` badges past `expiresAt` as `EXPIRED` and notifies sellers before expiry. Public reads also evaluate expiry, so a lagging job cannot show a lapsed badge.
- Suspending an account (Support/Security Officer, with reason) makes every badge display as suspended immediately (rule in `buildPublicVerificationSummary`).
- Revocation requires a reason and is final for that badge row.
- Sellers appeal badge decisions, suspensions, fraud outcomes and document rejections; reviewers other than the submitter decide, with a written note.

## 6. Public verification (implemented in Phase 3)

`GET /api/v1/verify/:sellerId` (public, no sign-in), where `sellerId` is a Tamagne ID
(case, missing dash and I/L/O look-alikes are normalized) or a QR token. Web pages:
`/[lang]/verify` (lookup form, works without JavaScript) and `/[lang]/verify/[id]`
(result). QR codes encode the language-neutral `/verify/<token>`, which redirects to
Amharic or English from the browser's `Accept-Language`.

- **Exposed (exact allow-list, tested):** `tamagneId`, `displayName`, `displayNameAm`,
  `accountStatus`, and for each of phone / Fayda identity / manual identity review /
  business / enhanced review: status, verification date, expiry date; plus `matchedBy`
  and `checkedAt`. The response also carries the "not a government institution / not a
  guarantee" disclaimer.
- **Never exposed:** phone number, internal IDs, region/city, seller type, Fayda numbers
  or subject, documents, verified legal name, tokens, risk levels, reviewers, suspension
  reasons, fraud reports.
- **Consent:** only profiles with `PUBLIC_PROFILE_DISPLAY` consent are returned. Unknown
  IDs and private profiles get the identical 404. A seller needs a public profile to get
  a QR code. _Owner decision pending:_ whether a suspended seller who withdraws public
  consent should still show as suspended to buyers (currently: hidden, privacy-first).
- **QR tokens:** 256-bit random, one active per seller (DB-enforced), stable until the
  seller replaces it (`POST /sellers/me/qr/rotate`) or staff revoke it with a reason
  (`POST /admin/sellers/:tamagneId/qr/revoke`, Super Admin / Verification Officer /
  Security Officer + MFA). A revoked token returns **410 "no longer valid"** so buyers are
  warned about stale or misused printed codes; this reveals nothing enumerable. Revoked
  tokens can never be reactivated (DB trigger).
- **Freshness:** `Cache-Control: no-store`; status, expiry and suspension are evaluated
  at request time.
- **Abuse:** 120 lookups per minute per IP (high because mobile carriers share IPs via
  CGNAT); the web server forwards the visitor's `X-Forwarded-For` so the limit applies
  per buyer. Result pages are `noindex`. The QR image is shown as an `<img>` data URI
  (the SVG is never injected into the page).

## 7. Security architecture

**Authentication (implemented).** Phone OTP; no passwords for now (fewer secrets to
leak; a password option can be added later with Argon2id). Opaque 256-bit session token
in an `HttpOnly`, `SameSite=Lax` cookie (`__Host-` prefix and `Secure` in staging and
production); only its SHA-256 is stored. Seller sessions: 30 days absolute, 7 days idle.
Staff sessions: 12 hours absolute, 30 minutes idle. Every sign-in issues a new token
(no fixation). Logout revokes server-side. Suspended or deactivated users' sessions stop
working immediately.

**Staff MFA (implemented).** TOTP (RFC 6238). The secret is AES-256-GCM encrypted,
shown once at enrolment; each time step is accepted once (`lastUsedStep`, updated
conditionally so concurrent replays fail). Admin endpoints require a staff role **and**
an MFA-verified session. Roles are re-read on every request, so revocation is immediate.
The first Super Admin is created with `npm run staff:bootstrap` (only when none exists).

**Authorization (RBAC, server-side only).**

| Capability | Super Admin | Verification Officer | Support Officer | Security Officer | Finance Officer |
|---|:-:|:-:|:-:|:-:|:-:|
| Seller search / view | ✓ | ✓ | ✓ | ✓ | – |
| Verification queue, approve/reject | ✓ | ✓ | – | – | – |
| Second review of high-risk overrides | ✓ | ✓ | – | ✓ | – |
| Suspend / reactivate account | ✓ | – | ✓ (suspend) | ✓ | – |
| Revoke badges / QR tokens | ✓ | ✓ | – | ✓ | – |
| Fraud investigation | ✓ | – | ✓ | ✓ | – |
| Appeals | ✓ | ✓ | ✓ | – | – |
| Pricing, revenue, payments | ✓ | – | – | – | ✓ |
| Audit log viewer | ✓ | – | – | ✓ | – |
| Grant/revoke staff roles | ✓ (never self) | – | – | – | – |

Roles are rows in `StaffRoleAssignment` (attributable, never deleted). Payment status
never confers verification. The DB independently requires reviewers to hold a role.

**Web protections.** CSRF on every state-changing request, including sign-in: the
Origin (or Referer) must be the web app's, and the `X-CSRF-Token` header must equal the
CSRF cookie (double-submit, rotated at sign-in); `SameSite=Lax` is a third layer. The
browser only talks to the web origin: `/api/*` is a runtime proxy route that forwards an
allow-list of headers to the API (the target is read at runtime, so one image works in
every environment). XSS: React escaping, strict CSP (nonce-based
in Phase 7), no `dangerouslySetInnerHTML`. SQL injection: Prisma parameterization;
raw SQL only via tagged templates. Input validation with Zod at every boundary.
Helmet headers on the API (`default-src 'none'`), 100 kB JSON limit, sanitized error envelope.

**Rate limiting.** Redis fixed windows (atomic `INCR` + `EXPIRE NX`) keyed by rule and an
HMAC of the phone, IP or user, never the raw value. Implemented: OTP send (per phone,
per IP, resend gap), OTP verify (per IP), MFA verify (per user). If Redis is unreachable
these routes fail closed (503). Production requires `REDIS_URL`; development may use an
in-memory store. Planned: public lookups, fraud reports, marketplace API.

**Secrets.** Environment variables populated by a secrets manager; config validation
rejects dev placeholders in production and never echoes values. Separate keys and
credentials per environment.

**Encryption.** TLS everywhere, including to PostgreSQL (`sslmode=verify-full`
required in production). Disk/volume encryption at rest, plus application-level
AES-256-GCM for the most sensitive fields with a rotatable key ring.

**Logging and monitoring.** Structured logs with request IDs; no bodies, tokens, OTPs,
identity data or phone numbers in logs. Security events (failed logins, MFA failures,
role changes, revocations, chain verification failures) go to the audit log and alerting.

## 8. Privacy and compliance (Proclamation No. 1321/2024)

- **Data residency.** Production personal data, especially identity data and documents, is stored and processed on infrastructure physically located in Ethiopia. Foreign-hosted managed services (e.g. Supabase, Render) are not assumed compliant and are not used for production personal data. Any cross-border transfer (including backups, logs, email/SMS vendors and error tracking) needs a documented legal basis confirmed by counsel and the supervisory authority's requirements. See `docs/DEPLOYMENT.md`.
- **Consent.** `ConsentRecord` stores purpose, policy version, locale, timestamp and withdrawal; withdrawal stops the processing it covers (e.g. withdrawing `PUBLIC_PROFILE_DISPLAY` hides the profile; withdrawing `FAYDA_IDENTITY_VERIFICATION` revokes the Fayda badge and schedules identity data deletion).
- **Minimization.** No FIN/FAN, no biometrics, no raw IPs; only the claims needed.
- **Data-subject rights.** `DataSubjectRequest` (access, correction, deletion) with due dates.
- **Deletion workflow.** `PENDING_DELETION` → grace period → scrub (phone, email, password nulled; DB-enforced for `DELETED`), purge documents from storage, keep only records legally required (audit, payments) without contact data.
- **Proposed retention defaults** (counsel must confirm): OTP challenges 30 days; sessions 90 days after expiry; failed/abandoned OIDC requests 30 days; manual-review ID images 90 days after decision; business documents until the badge expires + 1 year; payment records per tax law; audit log 5 years.
- **DPIA template**: Phase 7 (`docs/legal/`).

## 9. Environments

| | Development | Staging | Production |
|---|---|---|---|
| Data | Synthetic only (+999 phone numbers, "(Fixture)" names) | Synthetic only | Real |
| Fayda | `disabled` or `sandbox` | `sandbox` | `production` (authorized credentials only) |
| Hosting | Local Docker | Ethiopia-hosted or isolated | Ethiopia-hosted, compliant |
| DB guard | – | – | `ALTER DATABASE … SET tamagne.environment='production'` |
| Secrets | `.env` (dev placeholders) | Secrets manager | Secrets manager, separate keys |

Development and test fixtures never contain real identity data. The database test
harness refuses to run against anything but a local `tamagne_test` database.

## 10. API conventions

- URI versioning: `/api/v1/...`; breaking changes go to `/api/v2`.
- Success: `{ "data": ... }`; errors: `{ "error": { "code", "message", "requestId", "details?" } }`. 5xx never leak internals.
- OpenAPI generated from code (`npm run openapi -w @tamagne/api` → `docs/api/openapi.json`); the Swagger UI is disabled in production.
- Marketplace API: `X-Api-Key` (SHA-256 stored, prefix shown), scopes `verify:read`, `sellers:read`, `reports:write` (DB-constrained), per-client rate limit and optional IP allow-list.

## 11. Technology decisions

| Decision | Choice | Reason |
|---|---|---|
| Monorepo | npm workspaces | One lockfile; shared `core` rules used by API, jobs and tests |
| API | NestJS 12 (ESM), Express 5 | Requested stack; modules, guards and OpenAPI generation |
| ORM | Prisma 7 + `@prisma/adapter-pg` | Requested; typed client. Rules Prisma can't model live in SQL migrations |
| Tests | Vitest (+ SWC for decorator metadata), supertest, real PostgreSQL | Fast; DB rules tested against the real engine, not mocks |
| Web | Next.js 16, Tailwind 4, self-hosted Inter + Noto Sans Ethiopic | No third-party font requests (privacy, residency); proper Ethiopic rendering |
| UI kit | shadcn/ui from Phase 2 | Added when the first interactive forms are built |
| Migrations image | Full `node:22-bookworm` | Prisma's schema engine needs OpenSSL, absent from the slim runtime image |

## 12. Open questions and blockers

1. **Fayda production authorization** (NIDP relying-party onboarding, sandbox credentials, client key registration, allowed scopes/claims, `acr` values). Integration stays `disabled` until then.
2. **Ethiopian hosting provider** for production (DB, object storage, backups, KMS/HSM). Needs owner decision and counsel review.
3. **Legal review** of retention periods, cross-border transfer basis, consent text, and Amharic copy.
4. **SMS gateway** and **payment providers** selection and contracts.
5. Whether Tamagne IDs should carry a check character (currently relies on normalization + lookup).
