# TAMAGNE roadmap and status

_Last updated: 2026-11-07 (session 1, Phases 1–3)._

Legend: ✅ done and covered by automated tests · 🟡 done, partially verified ·
⛔ blocked on an external party or owner decision · ⬜ not started.

## Verified (Phase 3)

| Check | Result |
|---|---|
| `npm run typecheck` (core, db, api, web) | ✅ pass |
| `npm run lint` (typescript-eslint; Next.js rules for web) | ✅ 0 problems |
| `npm test`: core unit | ✅ 38 / 38 (incl. RFC 6238 and RFC 7636 test vectors) |
| `npm test`: API (config, HTTP, auth, sessions, CSRF, rate limits, sellers, consents, staff MFA, verify, QR) | ✅ 57 / 57 + Redis test (✅ with `REDIS_TEST_URL`) |
| `npm test`: web dictionaries (EN/AM parity, Ethiopic script) | ✅ 4 / 4 |
| `npm run test:db`: integrity rules on PostgreSQL 16 | ✅ 31 / 31 (3 migrations) |
| `npm run test:e2e`: Playwright, desktop + Pixel 7 | ✅ 14 / 14 |
| `npm run build` | ✅ pass |
| `npm audit --omit=dev` | ✅ 0 vulnerabilities (dev-only: moderate `minimatch` advisory via typescript-eslint) |
| GitHub Actions `.github/workflows/tamagne-ci.yml` | 🟡 updated (Redis service, lint, e2e); not yet run on GitHub |

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
- Staff role management UI/API (grant/revoke by a Super Admin) is Phase 6; only the bootstrap script exists.
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

## Next phases

### Phase 4: business document verification ⬜
### Phase 5: Fayda sandbox integration ⛔ (needs NIDP sandbox access)
### Phase 6: admin dashboard and fraud reporting ⬜
### Phase 7: security, privacy, compliance ⬜
### Phase 8: testing and production preparation ⬜

## Blockers and owner decisions

1. ⛔ Fayda: NIDP relying-party onboarding and sandbox credentials.
2. ⛔ Ethio telecom: confirm whether it is the SMS gateway, the production host, or both; then contract and credentials.
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
npm run db:test:down
```

Local manual run: `cp .env.example .env`, start Postgres/Redis (compose), `npm run db:migrate:deploy`,
`npm run dev -w @tamagne/api` (OTP codes appear in the API log) and `npm run dev -w @tamagne/web`.
