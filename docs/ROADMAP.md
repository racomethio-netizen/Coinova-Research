# Roadmap and status

_Last updated: 2026-10-21 (session 1)._

Legend: ✅ implemented and covered by automated tests · 🟡 implemented, partially
verified or needs a real external service · ⛔ blocked on credentials or owner
decision · ⬜ not started.

## Verified in this session

| Check | Result |
|---|---|
| `npm run typecheck` | ✅ pass |
| `npm run lint` | ✅ pass (0 problems) |
| `npm test` (unit) | ✅ 124 / 124 |
| `npm run test:db` (RLS on real PostgreSQL 16) | ✅ 52 / 52, plus a mutation check proving the suite catches a broken policy |
| `npm run test:integration` (supabase-js → PostgREST 12 → Postgres) | ✅ 25 / 25 |
| `npm run build` | ✅ pass (Next.js 16.3.8) |
| `npm run test:e2e` (Playwright, desktop + mobile) | ✅ 46 / 46 |
| `npm audit --omit=dev` | ✅ 0 vulnerabilities (dev-only `braces` advisory via eslint-config-next remains) |

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
- ⬜ CI pipeline (GitHub Actions running the suites)
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
5. Add a GitHub Actions workflow running typecheck, lint, unit, DB, integration, build, and e2e.
6. Implement the portfolio value chart and score-history chart; wire the remaining feature flags.
7. Decide on a payment provider and implement its `BillingProvider` adapter with tests.

## Session log

**Session 1:** initialized the repository and built every phase's core with tests.
See git history on branch `claude/cryptoscope-platform`.
