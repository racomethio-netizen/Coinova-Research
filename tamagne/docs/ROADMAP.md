# TAMAGNE roadmap and status

_Last updated: 2026-11-07 (session 1, Phase 1)._

Legend: ✅ done and covered by automated tests · 🟡 done, partially verified ·
⛔ blocked on an external party or owner decision · ⬜ not started.

## Verified this session

| Check | Result |
|---|---|
| `npm run typecheck` (core, db, api, web) | ✅ pass |
| `npm test`: core unit | ✅ 16 / 16 |
| `npm test`: API config + HTTP (supertest, real PostgreSQL) | ✅ 18 / 18 |
| `npm test`: web dictionaries | ✅ 4 / 4 |
| `npm run test:db`: integrity rules on PostgreSQL 16 | ✅ 31 / 31 |
| Mutation check: dropping the badge-issuance trigger | ✅ 7 DB tests fail as expected, restored → 31 / 31 |
| `npm run build` (core, db, api, web) | ✅ pass |
| `npm audit` | ✅ 0 vulnerabilities (multer pinned to 2.5.0 via override for GHSA-q7qp-rjg7-q26m) |
| Docker: API runtime, migrate and web images build | ✅ API image runs as non-root on read-only files; readiness OK |
| `docker compose`: postgres + redis healthy → migrate job → API ready | ✅ |
| Web at 360 px and 1280 px (Amharic) | ✅ no horizontal overflow; Ethiopic renders with self-hosted font |
| Lint | ⬜ no ESLint config yet (Phase 2) |
| GitHub Actions workflow `.github/workflows/tamagne-ci.yml` | 🟡 written; not yet run on GitHub |

## Phase 1: repository, architecture, database ✅

- ✅ `docs/ARCHITECTURE.md` (rules → enforcement map, context, data model, flows, security, privacy, environments)
- ✅ Monorepo: `packages/core`, `packages/db`, `apps/api`, `apps/web`
- ✅ Prisma schema: all 17 required models + Session, MfaFactor, PhoneOtpChallenge, StaffRoleAssignment, OidcAuthRequest, Plan, PlanPrice, DataSubjectRequest
- ✅ Migrations: `20261107000000_init` (generated) and `20261107000100_integrity_rules` (CHECKs, partial unique indexes, triggers, hash-chained audit log)
- ✅ `@tamagne/core`: Tamagne ID / QR token generation, AES-256-GCM field cipher with key rotation, HMAC, PKCE, public verification summary rules
- ✅ API skeleton: Zod config with Fayda disabled state and production guards, health/readiness, Fayda status, error envelope, request IDs, Helmet, CORS allow-list, body limits, OpenAPI (exported to `docs/api/openapi.json`)
- ✅ Web shell: EN/AM landing page with honest disclaimers, brand tokens, security headers
- ✅ Docker (API runtime + migrate, web), `docker-compose.yml`, `.env.example`
- ✅ `docs/FAYDA_INTEGRATION.md`, `docs/DEPLOYMENT.md`

## Next phases

### Phase 2: authentication and seller registration ⬜
- Session module (opaque cookie, hashed store), CSRF (Origin + double-submit), Redis rate limiter
- `POST /api/v1/auth/register`, `/auth/login`, OTP send/verify with an SMS adapter (disabled/console in dev)
- `POST/GET/PATCH /api/v1/sellers(/me)`; Level 1 badge issuance; consent capture (ToS, privacy)
- Staff TOTP MFA; role guards; audit log service
- ESLint (typescript-eslint), shadcn/ui setup, web sign-up/login pages
- Tests: registration, OTP, duplicate seller, unauthorized access, rate limiting

### Phase 3: profiles and QR verification ⬜
- `GET /api/v1/verify/:sellerId`, `GET /api/v1/sellers/:sellerId/qr`, web `/verify/[id]`
- QR rotation/revocation, enumeration-safe responses, public profile consent

### Phase 4: business documents ⬜
- Object storage adapter (Ethiopia-hosted target), multipart upload, magic-byte checks, ClamAV, review queue, Level 3 badge

### Phase 5: Fayda sandbox integration ⛔ (needs NIDP sandbox access)
- OIDC adapter per `docs/FAYDA_INTEGRATION.md` §5; manual identity review workflow can ship before it

### Phase 6: admin dashboard and fraud reporting ⬜
### Phase 7: security, privacy, compliance ⬜ (legal templates, DPIA, retention jobs, deletion workflow, nonce CSP)
### Phase 8: testing and production preparation ⬜ (Playwright e2e, load tests, backup/restore drill)

## Blockers and owner decisions

1. ⛔ Fayda: NIDP relying-party onboarding and sandbox credentials.
2. ⛔ Ethiopian production hosting provider (DB, object storage, KMS, backups).
3. ⛔ Legal review: retention periods, cross-border transfers, consent and Amharic copy (needs native-speaker review).
4. ⛔ SMS gateway and payment provider selection.
5. Repository: TAMAGNE currently lives in `tamagne/` inside the Coinova-Research repository. Moving it to its own repository is recommended before Phase 2 (the directory is self-contained).

## Known limitations

- API image is ~517 MB; `npm prune` keeps more than needed. Optimize in Phase 8.
- Next.js CSP allows `'unsafe-inline'` scripts until the nonce-based CSP lands (Phase 7).
- Amharic copy was written without native-speaker review.

## Commands to resume

```bash
cd tamagne
npm ci
npm run db:test:up
npm run check          # db:generate, typecheck, unit + API + DB tests
npm run build
npm run db:test:down   # when finished
```
