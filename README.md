# CryptoScope Research

A cryptocurrency research and analysis platform: public market pages, a transparent
research score, tokenomics and unlock analysis, personal portfolios with P&L,
watchlists and alerts (in-app, email, Telegram), free/premium memberships, an
AI research assistant grounded in retrieved data, and an admin dashboard.

"CryptoScope Research" is a placeholder brand. Rename it in `src/config/site.ts`.

> Research on this platform is informational. It is not financial advice.
> The research score is a transparent heuristic, not a prediction.

## Status at a glance

See **[docs/ROADMAP.md](docs/ROADMAP.md)** for the authoritative checklist of what
is implemented, tested, incomplete, or blocked on external credentials.

| Integration | State |
|---|---|
| Market data | CoinGecko adapter implemented and unit-tested with recorded/mocked responses. **Not yet verified against the live API.** A clearly labeled mock provider is used in development. |
| Supabase (DB, Auth, RLS) | Migrations, RLS, and server modules tested against real PostgreSQL + PostgREST locally. **Supabase Auth flows (sign-up/sign-in emails) need a real project to verify.** |
| Billing | Provider-neutral billing core with verified webhooks, tested end-to-end through a **development simulator**. No real payment provider is integrated yet (eligibility not confirmed). |
| Email | Resend adapter implemented, **unverified**; console adapter in development. |
| Telegram | Secure linking + delivery implemented and tested with a stubbed transport; **live Bot API not verified**. |
| AI | Anthropic adapter (official SDK) implemented; **not verified against the live API**; failure, quota, and prompt-injection handling tested. |

## Tech stack

Next.js 16 (App Router) · React 19 · TypeScript (strict) · Tailwind CSS 4 ·
Supabase (PostgreSQL, Auth, RLS) · Zod · decimal.js · TradingView Lightweight
Charts · Anthropic SDK · Vitest · Playwright.

## Prerequisites

- Node.js 20.9+ (22 recommended) and npm 10+
- For database and integration tests: PostgreSQL 15+ server binaries (`initdb`, `pg_ctl`)
  and a PostgREST binary (see [docs/TESTING.md](docs/TESTING.md))
- For real accounts: a Supabase project

## Local installation

```bash
npm ci                       # .npmrc sets legacy-peer-deps (see CLAUDE.md)
cp .env.example .env.local   # defaults run with mock market data and no database
npm run dev                  # http://localhost:3000
```

With no Supabase configured, public pages work with **mock data** (fictional
assets, clearly bannered). Accounts, dashboards, and research publishing need
Supabase: follow [docs/DATABASE.md](docs/DATABASE.md#applying-migrations-to-a-new-supabase-project),
then set `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`.

## Commands

| Command | Purpose |
|---|---|
| `npm run dev` | Development server |
| `npm run build` / `npm start` | Production build / server |
| `npm run typecheck` · `npm run lint` | Static checks |
| `npm test` | Unit tests (Vitest) |
| `npm run db:test:up` then `npm run test:db` | RLS/authorization tests on a disposable local Postgres |
| `npm run test:integration` | Server modules against Postgres + PostgREST |
| `npm run test:e2e` | Playwright (run `npm run build` first) |
| `npm run job -- <name>` | Trigger a background job via the cron endpoint |
| `npm run admin:grant -- <email> [admin\|editor]` | Controlled role assignment (service role) |

## Documentation

- [CLAUDE.md](CLAUDE.md): conventions and rules for continuing development
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): system design, data flow, jobs, P&L methodology
- [docs/DATABASE.md](docs/DATABASE.md): schema, RLS, migrations, retention
- [docs/SECURITY.md](docs/SECURITY.md): auth, authorization, secrets, incident response
- [docs/MARKET_DATA.md](docs/MARKET_DATA.md): providers, limits, freshness, attribution
- [docs/BILLING.md](docs/BILLING.md): entitlements, webhooks, provider eligibility
- [docs/TESTING.md](docs/TESTING.md): test suites and fixtures
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): Render + Supabase deployment, jobs, rollback
- [docs/ROADMAP.md](docs/ROADMAP.md): status and next steps

## Deployment overview

Render web service (`next start`) plus Render cron jobs that call the
authenticated `/api/cron/<job>` endpoint; Supabase hosts PostgreSQL and Auth.
See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). **Do not deploy to production
before completing its checklist.**
