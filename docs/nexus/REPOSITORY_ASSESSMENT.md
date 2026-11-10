# Repository assessment

_Inspected 2026-11-10 on branch `claude/cloud-environment-setup-9kee8o`
(base commit `e47d486`)._

## What the repository is

The repository contains **CryptoScope Research**, a working Next.js 16 + Supabase
crypto research web app built across three commits. It is TypeScript-only, with
no Python, no mobile app and no CI workflow.

| Area | Location | Notes |
|---|---|---|
| Web app (pages, API, server actions) | `src/app/**` | App Router, 34 API route handlers, `force-dynamic` root |
| Central config | `src/config/*` | brand, plans/limits, scoring weights, coin universe |
| Pure domain logic | `src/lib/**` | market providers (CoinGecko, mock), scoring, tokenomics, portfolio math (decimal.js), alerts, billing state machine, AI prompt building, HTTP envelope, rate limiter |
| DB-touching services | `src/server/**` | repositories, billing webhooks, jobs, Telegram, AI, admin |
| Schema + RLS | `supabase/migrations/*.sql` | 6 migrations, 32 tables, RLS on every table, entitlement functions, append-only audit log |
| Tests | `tests/{unit,db,integration,e2e}` | unit 124, RLS 52, integration 25, e2e 46 |
| Deploy | `render.yaml` | 1 web service + 4 cron services (Render), `autoDeploy: false` |
| Docs | `docs/*.md`, `CLAUDE.md` | architecture, DB, security, billing, testing, deployment, roadmap |

### Verified in this session (cloud container, 2026-11-10)

| Check | Result |
|---|---|
| `npm ci` | ✅ |
| `npm run typecheck`, `npm run lint`, `npm test` | ✅ 124 / 124 |
| `npm run db:test:up && npm run test:db` (PostgreSQL 16) | ✅ 52 / 52 |
| `npm run build` | ✅ |
| `npm run test:e2e` | ✅ 46 / 46, with Playwright pointed at the container's Chromium build. See the environment note in ROADMAP.md |
| `npm run test:integration` | ⚠️ Not run in this session: running the downloaded PostgREST binary was not permitted. It passed (25/25) in session 1 according to ROADMAP.md |

## Reusable infrastructure (keep and build on)

- **Auth and identity:** Supabase Auth, `getViewer()` / `require*Api()` / `require*Page()`, proxy route guard.
- **Authorization:** `user_roles` + `is_admin()` / `is_staff()`, RLS on every table, roles granted only by CLI.
- **Entitlements:** `current_membership_tier()` in SQL, tier-limit triggers, plan catalog in `src/config/plans.ts`.
- **Billing:** provider abstraction, verified idempotent webhooks, grace period, dev simulator. No live provider.
- **HTTP layer:** `ok`/`fail` envelope, `authed()` (CSRF origin check, rate limit), Zod parsing, `mapDbError`.
- **Jobs:** `acquire_job()` windowed locks, cron endpoint with Bearer `CRON_SECRET`, CLI runner.
- **Notifications:** in-app, email (Resend), and Telegram, with dedupe, hourly caps and bounded retries.
- **Audit:** `admin_audit_logs`, append-only and enforced by a trigger.
- **Security headers:** strict CSP, HSTS, frame denial; logger with secret redaction.
- **Charts:** `lightweight-charts` 5.2.1 already in use for price history.
- **AI:** Anthropic adapter with grounded prompts and quota in SQL. It can rephrase signal explanations (never decide signals).

## Gap analysis by NEXUS module

Legend: **Exists** = implemented and tested · **Partial** = foundation exists, NEXUS needs more · **New** = nothing yet.

| Module | State | What exists | What NEXUS needs |
|---|---|---|---|
| A. Authentication | Partial | Email/password, confirmation, reset, PKCE callback, rate limits | Admin MFA (TOTP, `aal2` enforced in RLS), bearer-token auth for mobile, account deletion, data export |
| B. User dashboard | Partial | Dashboard shell, portfolio, watchlist, alerts, notifications, settings | Signal feed, performance summary, paper-trading widgets |
| C. Market scanner | Partial | `/markets` universe table (CoinGecko snapshot data, search/sort/filter) | Pair × timeframe scanner from exchange OHLCV with indicator states |
| D. Market charts | Partial | Line chart of daily closes | Candlesticks, volume, EMA/BB overlays, RSI/MACD panes, signal markers |
| E. AI signal generator | New | (Research *score* exists; that is a fundamentals rating, not a signal) | Entire engine (Python) |
| F. Signal explanation | New | AI assistant infrastructure | Deterministic reason codes + feature contributions; optional AI rephrasing |
| G. Risk management | New | — | Per-signal R:R gates, position sizing, exposure and drawdown limits |
| H. Backtesting | New | — | Bar-by-bar simulator with fees, slippage, sizing, equity curve |
| I. Performance tracking | New | — | Immutable signal ledger, outcome resolver, metrics by asset/timeframe |
| J. Paper trading | New | Portfolio *tracker* (manual ledger) is a different feature and stays | Virtual accounts, orders, fills, positions, P/L |
| K. Subscriptions | Partial | Plans, entitlements, webhooks, simulator | Free / **Pro** / Premium tiers (today: free / premium / founding), feature-level entitlements, legal gate for paid signals |
| L. Notifications | Partial | In-app, email, Telegram, alert engine | Signal notifications, mobile push (Expo) |
| M. Admin dashboard | Partial | Users, research, scoring, coins, flags, audit, overview | Signals (annotate, never rewrite), model versions, backtests, system health, abuse reports, plan management |
| N. Audit logging | Partial | Append-only admin audit log | Model approvals, signal publications, flag/kill-switch changes, deletion/export events |
| O. Mobile app | New | — | Expo app with shared API types and Supabase auth |

### Existing features outside the NEXUS spec

These are research articles, tokenomics/unlocks, research scores, the AI research
assistant, watchlists and Telegram linking. They work and are tested. **Plan: keep them.**
They complement signals as research context. Whether they stay prominent in the
NEXUS navigation is a product decision (IMPLEMENTATION_PLAN.md, decision D6).

## Conflicts between the NEXUS spec and the current code

| # | Conflict | Proposed resolution | Decision owner |
|---|---|---|---|
| 1 | Tiers `free/premium/founding` vs `free/pro/premium` | Add `pro` between free and premium, retire `founding` (no paying subscribers exist because no live provider has been configured). New migration only | Owner (D1) |
| 2 | Brand "CryptoScope Research" vs "NEXUS AI" (repo is named Coinova) | Change `src/config` brand in Phase 7 after a trademark check. "Nexus" is a crowded mark in crypto, e.g. Nexus Mutual | Owner (D2) |
| 3 | Spec names a Python FastAPI backend, but the API today is Next.js route handlers | Keep Next.js as the **only public API** (auth, entitlements, CSRF, rate limit already there). The Python engine does compute and writes to Postgres. See ADR-001 | Proposed |
| 4 | Market data today is CoinGecko aggregates (daily). Signals need exchange OHLCV (15m–1d) | Add exchange public-endpoint candles alongside CoinGecko. CoinGecko remains for universe and fundamentals | Proposed |
| 5 | API auth is cookie + CSRF only, so mobile cannot use it | Add a bearer-token path to `authed()` (JWT verified server-side, no CSRF needed) | Proposed |
