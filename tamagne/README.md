# TAMAGNE

**Trust. Verify. Trade with Confidence.** · **እመኑ። ያረጋግጡ። በልበ ሙሉነት ይገበያዩ።**

Independent seller identity and business verification for Ethiopian online commerce.
Sellers verify their phone, identity (Fayda eSignet) and business; buyers check the
result by Tamagne ID, public profile or QR code.

TAMAGNE is an independent private platform, not a government institution.
Verification does not guarantee honesty, product authenticity or delivery.

> **Status: Phase 7 complete.** Seller verification (phone, business, manual identity; Fayda
> adapter built but unconnected), public lookup and QR, staff console with MFA, fraud reports,
> appeals, privacy centre (export, correction, deletion), retention jobs, nonce CSP and draft legal
> documents. Not yet connected: Fayda, production SMS, production document storage, payments.
> See [docs/ROADMAP.md](docs/ROADMAP.md).

## Layout

| Path | What |
|------|------|
| `apps/api` | NestJS REST API (`/api/v1`), OpenAPI |
| `apps/web` | Next.js web app, English and Amharic |
| `packages/db` | Prisma schema, migrations, DB integrity tests |
| `packages/core` | Shared rules and security primitives |
| `docs/` | [Security](docs/SECURITY.md) · [Legal drafts](docs/legal/README.md) · [Architecture](docs/ARCHITECTURE.md) · [Roadmap](docs/ROADMAP.md) · [Fayda guide](docs/FAYDA_INTEGRATION.md) · [Deployment](docs/DEPLOYMENT.md) · [OpenAPI](docs/api/openapi.json) |

## Quick start

Requires Node.js ≥ 22.12 and either local PostgreSQL 16 server binaries or Docker.

```bash
npm ci
npm run db:test:up     # disposable PostgreSQL on 127.0.0.1:54339, migrations applied
npm run check          # generate client, typecheck, all tests
npm run build
```

Run locally:

```bash
cp .env.example .env   # development placeholders only
docker compose up -d postgres redis
docker compose run --rm migrate
npm run dev -w @tamagne/api    # http://localhost:4000/api/docs; OTP codes print to this log (SMS_PROVIDER=console)
npm run dev -w @tamagne/web    # http://localhost:3000
```

Never use real personal data in development or tests.
