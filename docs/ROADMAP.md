# Roadmap and status

_Last updated: 2026-11-10 (session 2)._

## NEXUS AI program (current direction)

The product is being extended into **NEXUS AI** (signals, backtesting, paper
trading, mobile). The plan, architecture, ERD, risks and compliance gates are in
[`docs/nexus/`](nexus/README.md). **Phases 1, 2, 4, 6, 7 and 8 are done; Phases 3 and 5 are built and
tested on synthetic data** (🟡); Phase 9 entitlements are done, and its payment adapter waits on D9. Phase 10 (ops readiness)
is built and tested; deployment itself waits on owner approval (docs/nexus/GO_LIVE.md). Its exit criteria (≥3-year backfill, 24 h live run)
are blocked on:
1. **Owner decision D5:** approve the data source + hosting region
   (docs/nexus/MARKET_DATA_SOURCES.md) and record the terms review (gate G-4).
2. **Network access** to `data-api.binance.vision` and `data.binance.vision` in the cloud
   environment. Both are currently refused by the proxy.

### Phase 10 (session 2): what was built
- Engine: nightly ledger export (JSONL + manifest, SHA-256, chain head) with continuity
  check against the previous export, `ledger-export|verify|restore-drill` commands, and a
  web-cron caller that replaces the four Render cron services.
- `scripts/restore-drill.sh` (pg_dump → scratch DB → row counts + chain), also run in CI.
- Migration 0019: `ops_health()` + `raise_ops_alerts()`; `check-health` job on an
  independent Render cron (watchdog); checks shown on `/admin/health`.
- `scripts/load-test.ts` with thresholds; local baseline recorded in RUNBOOKS.md.
- Dependabot for the engine (uv) and its Docker image; weekly CI run.
- Docs: SECURITY_REVIEW.md, RUNBOOKS.md, GO_LIVE.md; DEPLOYMENT.md updated.
- Not done (owner approval): staging deploy, load test on the target instance, Supabase
  backups/PITR plan, off-site storage for ledger exports.

### Phase 9 (session 2): what was built
- Migration 0018 enforces every plan feature: scanner advanced indicators (Pro), model
  analytics RLS (Premium), custom backtest parameters (Premium), per-tier `backtestsPerDay`,
  opt-in signal notifications for public signals (Pro). Removed `charts.basic` and
  `research.advanced_tools` (not real entitlements).
- `GET /api/performance/export` (CSV, `performance.reports`); signal-notification opt-in in
  settings; locked advanced columns on web and mobile scanner.
- Tests: entitlement matrix (unit + RLS), billing provider conformance suite.
- Not done: real payment adapter (**D9**). Mobile purchase stays off (D7).

### Phase 8 (session 2): what was built
- `mobile/` Expo app (SDK 58): SecureStore sessions, a bearer-token API client validated
  by `packages/shared`, Home/Scanner/Signals/Performance/Paper/Settings, in-app account
  deletion, push opt-in. No purchase UI (D7).
- Web: `/api/pairs|scanner|signals|signals/[id]|performance`, `/api/devices`,
  `DELETE /api/account`; `push` channel with an Expo sender (migration 0017).
- Not done: store builds/submission (EAS, Apple/Google accounts need owner approval; G-7 checklist in MOBILE.md).

### Phase 7 (session 2): what was built
- Navy design tokens (AA contrast); nav for Scanner, Signals, Performance; dashboard
  Paper trading and Backtests.
- Public: `/scanner` (view without decisions), `/charts/[symbol]` (candles, volume, EMAs,
  signal markers), `/signals` and `/signals/[id]` (gate-aware, explanation, lifecycle,
  annotations, hash), `/performance` (forward only, no rate under 100 trades).
- Dashboard: `/dashboard/paper`, `/dashboard/backtests` (+ detail).
- Admin: `/admin/signals`, `/admin/models`, `/admin/market` (G-4 terms review),
  `/admin/health`, `/admin/reports`; audited API routes.
- **Brand:** still "CryptoScope Research" until D2 (trademark) clears; change `src/config/site.ts`.

### Phase 6 (session 2): what was built
- **Paper trading** (virtual USDT, long-only): SQL functions for accounts and orders
  (validated, rate-limited), engine fills with fees/slippage and fresh prices only,
  SL/TP, expiry, hourly equity snapshots. `/api/paper` (summary, valued in decimal),
  `/api/paper/account`, `/api/paper/orders`, `/api/paper/positions/[pairId]`.
  Paper data is included in the account export.

### Phase 5 (session 2): what was built
- **Backtester** on the production decision window and the shared execution model,
  with portfolio risk rules, metrics with bootstrap CIs, and a minimum-sample flag.
  Baselines: buy-and-hold and random entries.
- **Walk-forward validation:** purged/embargoed expanding folds, calibration slice,
  lockbox, logistic + XGBoost (research dependency group), Brier/ECE/AUC, model vs
  rules-only. **Experiment registry** with git SHA, lockfile hash, dataset hash and seed.
- **Premium backtests:** `request_backtest()` (quota/bounds), engine job runner,
  `POST/GET /api/backtests`. `signal_performance` view.
- **Not done (needs real data):** the validation report for `trend_pullback` and any
  model approval. No performance numbers exist or are claimed.

### Phase 4 (session 2): what was built
- **Indicators** in NumPy (EMA, RSI, MACD, ATR, Bollinger, volume ratio, confirmed
  swing levels), matching TA-Lib, and provably causal.
- **Strategy `trend_pullback` 1.0.0** (rules only): multi-timeframe confirmation, ATR and
  level-based entries, stops and targets, net reward/risk ≥ 1.5 after fees and slippage,
  HOLD with reason codes otherwise. Confidence is "not calibrated" (no model yet).
- **Signal ledger** (migration 0013): insert-only signals/events/outcomes/annotations,
  hash chain, DB-time publication, visibility by gate G-1 + tier (D3), model lifecycle,
  beta testers.
- **Execution model + outcome resolver** (conservative, shared with future
  backtests/paper trading). Worker: ingest → evaluate → resolve. CLI `verify-chain`.
- Signals stay staff-only (shadow) until legal review and Phase 5 validation.

### Phase 3 (session 2): what was built
- `engine/` Python service (uv, pydantic-settings, psycopg as `nexus_engine`, httpx,
  FastAPI): Binance public adapter (GET-only allow-list, no keys, rate limiter,
  backoff, stop on 418/451), validation, gap tracking, immutable finals with revision
  records, checksum-verified archive import, worker scheduler, `/health`, CLI, Dockerfile.
- Migration 0012: market sources/pairs/candles/gaps/revisions/runs/prices/heartbeats/jobs.
- Gate G-4 enforced: production refuses sources without a recorded terms review.
- 58 engine tests (8 against real Postgres) + 8 new RLS tests; CI engine job.

### Phase 2 (session 2): what changed
- Tiers **free < pro < premium** (D1). `founding` retired. Existing paid features
  start at Pro. `plan_features` + `has_feature()` for NEXUS feature entitlements.
- Kill switches `SIGNALS_VISIBILITY` / `PAID_SIGNALS_ENABLED` / `LIVE_EXECUTION_ENABLED`,
  validated at server start.
- **Admin/staff privileges require MFA (aal2)**, with a TOTP security page.
- Data export (`/api/account/export`) and deletion retention + audit. Fixed the bug
  where an account that had written an audit entry could not be deleted.
- Bearer-token API auth for mobile, `nexus_engine` DB role, abuse reports.
- CI workflow, Dependabot, gitleaks. **Security upgrade:** next 16.3.8 → 16.4.3
  (critical advisories incl. RCE) and zod 4.6.5 → 4.6.7.

Next commands (Phase 3 completion + Phase 5 validation report, then Phase 9):
```bash
npm ci && npm run typecheck && npm run lint && npm test
npm run db:test:up && npm run test:db
cd engine && uv sync && uv run pytest && uv run ruff check . && uv run mypy
# after D5 + network access (local DB, dev only):
DATABASE_URL='postgres://postgres@127.0.0.1:54329/nexus_engine_test?options=-c%20role%3Dnexus_engine' \
  uv run nexus-engine archive-backfill --symbol BTCUSDT --tf 1h --from-month 2023-01 --to-month 2026-10
# once data exists: uv run nexus-engine validate --symbol BTCUSDT --tf 1h --start 2023-01-01 --end 2026-06-30
# Phase 9 remainder: implement the BillingProvider adapter after D9 and run tests/support/billing-conformance.ts against it
# Phase 10 remainder (owner approval): follow docs/nexus/GO_LIVE.md stage A (staging), then
scripts/restore-drill.sh '<staging url>'
npx tsx scripts/load-test.ts --base https://<staging> --allow-remote --duration 60 --concurrency 20
```

Cloud-container note (session 2): the repo pins `@playwright/test` 1.56.1
(Chromium build 1194), but the container image ships build 1243. e2e passed (46/46)
after linking the 1194 paths to the installed 1243 binaries. `test:integration` needs
the PostgREST binary at `.tmp/postgrest` (docs/TESTING.md).

Legend: ✅ implemented and covered by automated tests · 🟡 implemented, partially
verified or needs a real external service · ⛔ blocked on credentials or owner
decision · ⬜ not started.

## Verified in session 2 (after Phase 2)

| Check | Result |
|---|---|
| `npm run typecheck`, `npm run lint` | ✅ pass |
| `npm test` (unit) | ✅ 137 / 137 |
| `npm run test:db` (RLS on PostgreSQL 16) | ✅ 74 / 74 |
| `npm run build` | ✅ pass (Next.js 16.4.3) |
| `npm run test:e2e` | ✅ 46 / 46 |
| `npm run test:db` after Phase 3 | ✅ 82 / 82 |
| Engine: `uv run pytest` / ruff / mypy --strict / pip-audit | ✅ 112 / 112 after Phase 6 (23 DB tests as `nexus_engine`) / clean / clean / 0 known vulnerabilities |
| `npm run test:db` after Phase 8 | ✅ 105 / 105 |
| `npm test` after Phase 8 | ✅ 154 / 154 |
| Mobile: typecheck / `npm test` / `bundle:check` | ✅ clean / 9 / 9 / iOS + Android bundles |
| `npm run test:e2e` after Phase 7 | ✅ 56 / 56 |
| After Phase 9: unit / DB / integration / e2e | ✅ 164 / 111 / 29 / 56 |
| After Phase 10: unit / DB / integration / e2e / engine / mobile | ✅ 165 / 115 / 30 / 56 / 125 / 9; restore drill ✅; load test (local) ✅ |
| `npm audit --omit=dev` | ✅ 0 vulnerabilities (after the next/zod upgrade) |
| `npm run test:integration` | ✅ passed in GitHub Actions (not runnable in this container: executing the downloaded PostgREST binary was not permitted) |
| GitHub Actions CI (run #3) | ✅ all 4 jobs green: checks + audit, DB + integration, build + e2e (ubuntu-24.04), gitleaks |

Session 1 results (before NEXUS): unit 124, DB 52, integration 25/25, e2e 46.

Bugs found and fixed by these suites: Postgres timestamp offsets rejected by the
scoring schema; bulk upsert sending NULL for missing keys; watchlist re-add hitting
the limit trigger; mobile page overflow from sr-only labels in tables; the chart
failing to render under POSIX locales; the not-found page returning HTTP 200.

## Feature status

### Phase 1: Foundation ✅
- ✅ Next.js 16 App Router, strict TS, Tailwind 4, ESLint, Vitest, Playwright
- ✅ Design system (dark default, light theme), shared navigation, loading/empty/error states, accessible forms
- ✅ Public pages: `/`, `/markets`, `/coins/[id]`, `/research`, `/research/[slug]`, `/pricing`, `/about`, auth pages
- ✅ Centralized config (brand, plans, scoring, coin universe); validated env; `.env.example`

### Phase 2: Database and authentication
- ✅ 6 migrations, RLS on every table, triggers, entitlement functions, audit log
- ✅ Cross-user isolation, role escalation, self-granted membership, and premium gating tests
- 🟡 Supabase Auth flows (register/confirm/login/logout/reset/callback) are implemented and validation-tested, but **not run against a real Supabase Auth instance** (manual checks in TESTING.md)

### Phase 3: Market data
- ✅ Provider interface, mock provider, CoinGecko adapter (validation, retries, batching, errors), stale-if-error cache, freshness badges, charts, markets search/sort/filter/pagination, public APIs
- 🟡 **CoinGecko live API not verified.** api.coingecko.com was unreachable from the build environment. Some universe IDs may need correction; the refresh job logs them.

### Phase 4: Research engine ✅
- ✅ Deterministic versioned scoring, insufficient-data handling, score history, configurable weights (admin), tokenomics analytics with provenance, unlock summaries
- ⬜ Volatility/momentum charts of score history (data exists; only a list is shown)

### Phase 5: Portfolio
- ✅ Multiple portfolios; buy/sell/transfer in/out; edit and delete with ledger validation; average-cost P&L (decimal); allocation; CSV export; RLS isolation
- ⬜ Portfolio value-over-time chart (needs per-day valuation from `historical_prices`)

### Phase 6: Membership and billing
- ✅ Plans, entitlements (SQL and TS), premium research gating in the DB, billing abstraction, verified idempotent out-of-order-safe webhooks, payment history, cancellation, grace period, refunds, dev simulator
- ⛔ **No real payment provider**: blocked on the owner confirming provider eligibility and payout arrangements (checklist in BILLING.md)

### Phase 7: Alerts and Telegram
- ✅ Alert CRUD and validation, edge-triggered evaluation job, dedupe, hourly notification limits, delivery tracking with bounded retries, in-app notifications, secure Telegram linking
- 🟡 Email via Resend and the Telegram Bot API are implemented but **unverified live** (need credentials and network access)
- 🟡 `telegram_notifications` and `maintenance_banner` feature flags exist but are not yet read by the UI (`public_signups` and `ai_assistant` are enforced)

### Phase 8: AI research
- ✅ Provider interface, Anthropic adapter (official SDK, refusal fallback), grounded prompts with untrusted-content handling, per-day quota in SQL, usage metadata, assistant UI with sources and timestamps
- 🟡 **Not verified against the live Anthropic API** (no key configured). Daily/weekly market summary generation runs on demand only; no scheduled digest yet.

### Phase 9: Administration ✅
- ✅ Admin/editor roles via CLI only, overview (users, subscribers, MRR estimate, jobs, deliveries, billing events, AI usage), article draft/preview/publish/unpublish/archive, scoring config publishing, coin metadata, cited unlock entry, feature flags, user list, audit log

### Phase 10: Production readiness
- ✅ Full check suite green; security headers; health check; structured logs; deployment/rollback docs; `render.yaml`
- ✅ CI pipeline (GitHub Actions: checks, audit, DB, integration, build, e2e, gitleaks)
- ⬜ Shared-store rate limiter for multi-instance deployments
- ⬜ Load testing on the target instance size
- ⬜ Legal pages (terms, privacy, refund policy): owner/legal input required

## Blocked / needs the owner

1. **Supabase project** (staging, then production): needed for real accounts.
2. **CoinGecko API key** (demo or pro), plus a live verification run.
3. **Payment provider** choice after eligibility review (BILLING.md checklist).
4. **Resend** account and verified sending domain; **Telegram bot** token; **Anthropic API key**.
5. Pricing review, legal entity name, terms/privacy/refund policies.
6. Approval before any deployment or paid plan.

## Next steps (exact)

1. Create a Supabase **staging** project; then
   `npx supabase link --project-ref <ref> && npx supabase db push`; run `supabase/seed.sql` (staging only).
2. Put staging credentials in `.env.local`, plus `MARKET_DATA_PROVIDER=coingecko`
   and a CoinGecko demo key. Run `npm run dev`, register, confirm the email, and sign in.
3. With `CRON_SECRET` set: `npm run job -- sync-plans`, `refresh-markets`,
   `refresh-history`, `refresh-fundamentals`, `compute-scores`. Review `/admin` job
   history and fix any universe IDs CoinGecko didn't return.
4. `npm run admin:grant -- <your-email>`; publish a first article; enable
   `BILLING_PROVIDER=dev_simulator` on staging and walk the lifecycle in TESTING.md.
5. ✅ Done in session 2: `.github/workflows/ci.yml` (check the first runs on GitHub).
6. Implement the portfolio value chart and score-history chart; wire the remaining feature flags.
7. Decide on a payment provider and implement its `BillingProvider` adapter with tests.

## Session log

**Session 1:** initialized the repository and built every phase's core with tests.
See git history on branch `claude/cryptoscope-platform`.

**Session 2:** NEXUS AI Phase 1, which covers the repository assessment, implementation plan,
target architecture with ADRs, ERD, signal-engine specification, dependency list,
security risk register and compliance gates (`docs/nexus/`). No application code changed.
Re-verified: typecheck, lint, unit 124/124, DB 52/52, build, e2e 46/46.
NEXUS Phase 2 was done in the same session: tiers, feature entitlements, kill
switches, admin MFA, export/deletion, bearer auth, engine role, abuse reports,
CI, and the next/zod security upgrade (see "Phase 2 (session 2)" above).
Phase 3 was then built in the same session (engine + market-data schema). Its live
exit criteria wait on D5 and network access (see the top of this file).
Phase 4 followed in the same session (indicators, strategy, signal ledger, resolver).
Phase 5 followed (backtester, walk-forward validation, Premium backtest queue). Its real validation report awaits data.
Phase 6 followed (paper trading: schema, engine fills, API).
Phase 7 followed (web dashboard: public NEXUS pages, paper/backtest dashboards, admin).
Phase 8 followed (mobile app, shared API contract, push channel).
Phase 9 followed (entitlements enforced in SQL/API, conformance suite). The payment adapter waits on D9.
Phase 10 followed (ledger export + restore drill, cron consolidation, watchdog alerts, load test,
security review, runbooks, go-live checklist). Deployment waits on owner approval.
