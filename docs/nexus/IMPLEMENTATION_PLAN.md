# NEXUS AI: implementation plan

Principles: build on the existing tested code (REPOSITORY_ASSESSMENT.md). Work in
small verified increments. Add new migrations and never edit applied ones. Nothing
is "done" until its tests pass. Gates in COMPLIANCE.md stay closed by default.
Each phase ends with a report (template at the bottom) and an update to `docs/ROADMAP.md`.

## Owner decisions needed

| ID | Decision | Recommendation | Needed by |
|---|---|---|---|
| D1 | Tier model | Add `pro` (rank between free and premium) and retire `founding` (no paying subscribers exist) | Phase 2 |
| D2 | Brand and domain | Run a trademark search for "NEXUS AI" in target markets before buying a domain or building store listings ("Nexus" is crowded in crypto) | Phase 7 |
| D3 | Signal access per tier | Free: closed signals older than 7 days + aggregate track record. Pro: live signals, full history, alerts, reports. Premium: + model analytics, backtests, custom strategies. All subject to G-1/G-2 | Phase 4 |
| D4 | Paper trading direction | Long-only spot in v1; shorts later with explicit funding assumptions | Phase 6 |
| D5 | Hosting region + primary data source | Choose after the ToS and geo-restriction review (G-4) | Phase 3 |
| D6 | Existing research features (articles, scores, assistant, watchlists, Telegram) | Keep; place under "Research" in the NEXUS navigation | Phase 7 |
| D7 | Mobile monetization | v1 mobile app without in-app purchase UI. Confirm against current store rules, because offering subscriptions in-app may require the stores' billing | Phase 8 |
| D8 | Retention of HOLD evaluations | Keep 1 year detailed, then daily aggregates. Published signals are kept forever | Phase 4 |
| D9 | Payment provider | Already blocked in ROADMAP. Needs an eligibility review for subscription signals | Phase 9 |

## Phase 1: Architecture and documentation ✅
Deliverables: this folder (assessment, plan, architecture + ADRs, ERD, signal
engine spec, dependencies, security risks, compliance), plus ROADMAP and CLAUDE.md pointers.
Code changes: none.

## Phase 2: Database and authentication ✅
_Done in session 2 with D1 as recommended (pro added, founding retired). Existing
paid features (all scores, portfolios, alerts, export) start at Pro. Next.js was
upgraded to 16.4.3 and zod to 4.6.7 for security advisories found by the new audit step._
1. **CI baseline:** a GitHub Actions workflow running typecheck, lint, unit, DB (Postgres
   service) and build. Integration and e2e are added once the runner can fetch PostgREST and Chromium.
   Add Dependabot and gitleaks.
2. **Tiers (D1):** migration adding `pro`; update `src/config/plans.ts`, SQL
   entitlement functions, `TIER_RANK`, limits, and tests. Run `sync-plans`.
3. **Feature entitlements:** `plan_features` table + `has_feature(key)` SQL function,
   synced from `src/config/plans.ts`. TS `hasFeature()` mirrors it.
4. **Kill switches:** add `SIGNALS_VISIBILITY`, `PAID_SIGNALS_ENABLED`,
   `LIVE_EXECUTION_ENABLED` to `src/lib/env.ts`, with startup refusal of
   `LIVE_EXECUTION_ENABLED=true`.
5. **Admin MFA:** TOTP enrollment/challenge pages. `is_admin_mfa()` checks
   `auth.jwt()->>'aal' = 'aal2'`. Admin write policies and admin pages require it.
6. **Account deletion and data export:** settings UI, routes, a deletion function
   with a retention policy for billing records, and audit entries.
7. **Bearer-token API auth** in `authed()` for mobile (no CSRF on that path; JWT verified).
8. **`nexus_engine` DB role** with no grants yet (grants are added per table in later phases).
9. **Abuse reports** table and the user-side report action.

Tests: RLS tests for tiers/features/MFA/deletion. Unit tests for the bearer path and
env gates. Integration tests for deletion and export. Note: MFA and auth flows need
real GoTrue. Use the Supabase CLI local stack (Docker) or keep them as manual checks, documented.
**Exit:** all existing suites are green plus the new tests, and CI is green on the branch.

## Phase 3: Market-data ingestion 🟡
_Session 2: items 1–5 are built and tested (engine, migration 0012, Binance public
adapter, validation, gaps, archive import, scheduler, gate G-4). The engine scheduler
does not call `/api/cron/*` yet (item 6). **Exit criteria not yet met**: the ≥3-year
backfill and the 24 h live run need decision D5 and network access to the source
hosts, which this cloud environment currently blocks._
1. `engine/` scaffold: uv project, pydantic-settings config, psycopg pool, FastAPI
   `/health`, worker loop, structured logging with redaction, Dockerfile, pytest/ruff/mypy, CI job.
2. Migrations: `market_sources`, `market_pairs`, `market_candles`, `candle_gaps`,
   `candle_revisions`, `ingest_runs`, `latest_prices`, `engine_jobs`,
   `engine_heartbeats`, with RLS and `nexus_engine` grants.
3. Public kline + ticker adapter (primary source per D5), token-bucket limiter, retries.
4. Validation, upsert, gap detection, backfill from the archive files (checksums verified).
5. Seed pairs BTC/USDT and ETH/USDT on 15m/1h/4h/1d. Admin "pairs" API (audited).
6. Engine scheduler also calls `/api/cron/*` so the Render cron services can be removed later.

Tests: adapters against recorded fixtures (no network in unit tests), validation
property tests, gap/backfill tests on the DB, and an opt-in live smoke test.
Network: the cloud environment needs the data-source hosts allowed (requested when this phase starts).
**Exit:** a backfill of ≥ 3 years for both pairs and all timeframes, with gaps reported
honestly, plus a live incremental ingest running locally for 24 h without errors.

## Phase 4: Technical analysis and signal engine ✅
_Session 2: done. Model registry tables are in place (`strategies`, `model_versions`).
`experiments` and `validation_reports` move to Phase 5, where they are first written.
Model SHAP contributions arrive with the first model (Phase 5). Signals run in
shadow (`SIGNALS_VISIBILITY=staff`) with confidence "not calibrated"._
1. Indicators + golden tests (TA-Lib test-only), support/resistance with confirmation time.
2. Feature builder and truncation-invariance test harness.
3. Strategy `trend_pullback_v1` (rules only), multi-timeframe confirmation, risk gates, HOLD reasons.
4. Migrations: `strategies`, `model_versions`, `experiments`, `validation_reports`,
   `signal_evaluations`, `signals`, `signal_events`, `signal_outcomes`,
   `signal_annotations`, with insert-only triggers, a hash chain, and RLS by visibility/tier (D3).
5. Explanation engine (reason codes; SHAP added when a model exists).
6. Outcome resolver (shared execution module, ADR-006).
7. The static check that live execution stays disabled (R-12), plus `DisabledExecutionAdapter`.

Signals run with `SIGNALS_VISIBILITY=staff` and `confidence = null` ("not calibrated")
until Phase 5 approves a model.
**Exit:** signals and outcomes accumulate in shadow mode, and all immutability and leakage tests pass.

## Phase 5: Backtesting and performance tracking 🟡
_Session 2: items 1–4, 6 and 7 are built and tested (backtester, metrics, walk-forward
with purge/embargo/lockbox, logistic + XGBoost with calibration, experiment registry,
Premium backtest queue + API, `signal_performance` view). **Item 5 is blocked**: the
validation report for `trend_pullback` needs real history (decision D5 + network access).
No model is approved and no performance figure exists yet. Run
`nexus-engine validate --symbol BTCUSDT --tf 1h --start … --end …` once data is ingested.
Model artifacts (upload with SHA-256) arrive with the first model worth registering._
1. Backtest engine (fees, slippage, sizing, intrabar rules, equity/drawdown, comparison).
2. Metrics module with bootstrap CIs and the minimum-sample rule.
3. Walk-forward with purge/embargo, calibration, baselines, lockbox, experiment
   registry, reproducibility record.
4. Training pipeline (logistic baseline → XGBoost), artifact upload with SHA-256.
5. Validation report for `trend_pullback_v1`, published internally **whatever it shows**.
   If the strategy has no out-of-sample edge after costs, no model is approved and
   signals stay staff-only. This is an acceptable outcome.
6. Backtest queue (`backtest_runs/results/trades`) consumed by the engine.
7. Performance aggregation (SQL views) for signals by asset and timeframe.

Tests: hand-computed backtest scenarios, purge/embargo tests, reproducibility test,
and metric unit tests.
**Exit:** a reproducible validation report exists, and the performance views match the
resolver output on fixtures.

## Phase 6: Paper trading ✅
_Session 2: done as specified, long-only (D4). Available to every signed-in user (no tier
gate; revisit in Phase 9). Fills need fresh prices, so they happen once Phase 3
ingestion runs. The UI is Phase 7; the API is `/api/paper/*`._
Migrations (`paper_accounts`, `paper_orders`, `paper_fills`, `paper_positions`) and SQL
order-placement functions (validation, per-user rate limits). Engine fill loop with
price staleness checks. SL/TP monitoring. Decimal P/L. Next.js API routes. Account reset (archive).
Tests: RLS isolation, fill math, staleness rejection, SL/TP, rate limits.
**Exit:** an end-to-end paper trade (place → fill → SL/TP → realized P/L) passes in integration tests.

## Phase 7: Web dashboard ✅
_Session 2: done, except the **brand change**, which waits on D2 (trademark search) and
is a one-line change in `src/config/site.ts`. Built: navy tokens (WCAG AA), scanner,
candlestick charts with EMA/volume/signal markers, signals list and detail,
performance (forward only, sample rules), paper trading, backtests, and admin screens
(signals annotate, models, market data and terms review, health, abuse reports). The
pricing gate is G-2. Plans and notification management stay config-driven (`plans.ts` +
`sync-plans`; existing notification settings)._
Navy institutional design tokens, brand change (after D2), and pages: landing,
login/register (restyle), dashboard, market scanner, signal details (chart with
markers, explanation, outcome, annotations), performance dashboard (forward vs
backtest, with n/period/assumptions), paper trading, pricing (G-2 aware),
settings (MFA, export, delete). Admin: signals (annotate only), model versions
(approve with MFA), backtests, system health (heartbeats, freshness, gaps),
notifications, abuse reports, plans, pairs, audit logs. Candlestick + indicator charts.
Tests: e2e desktop + mobile, accessibility checks, no horizontal overflow at 360 px,
and claims-policy copy review.

## Phase 8: Mobile application ✅
_Session 2: done (see docs/nexus/MOBILE.md). D7 is decided as no in-app purchase UI.
Charts open the web chart. Account deletion is in the app. Push alerts work end to
end. Release builds (EAS, store accounts) await owner approval and the G-7 review._
`packages/shared` (Zod schemas, API types, client). Expo app: auth (secure store),
dashboard, scanner, signal detail, performance, paper trading, notifications (push
tokens), settings. Chart approach decided (DEPENDENCIES.md). Store policy review (G-7, D7).
Tests: type checks, unit tests, API contract tests against shared schemas, internal EAS builds.

## Phase 9: Subscription architecture 🟡
_Session 2: entitlements are done and tested. Every feature in `plans.ts` is enforced
in SQL or the API (migration 0018), with entitlement-matrix tests at unit and RLS level.
Backtest quotas are per tier. A provider conformance suite exists for webhooks. **Blocked**:
the real payment adapter needs D9; mobile stays without purchase UI (D7)._
Feature entitlements enforced across API and RLS for all NEXUS features. Backtest
quotas per tier. Pricing driven by `plans.ts` and gates. Payment provider adapter after D9
(signature-verified webhooks only, existing rules). Mobile purchase approach per D7.
Tests: entitlement matrix tests (every feature × tier, at API and RLS level) and webhook tests.

## Phase 10: Testing and deployment
Complete CI (integration, e2e, engine, mobile). Security review against
SECURITY_RISKS.md. Dependency audit. Load test on the target instance size.
Backups + nightly ledger export + restore drill. Staging on Render (web + engine),
`render.yaml` updated (engine service, cron consolidation). Monitoring and alerts
(heartbeats, ingestion freshness, error rates). Runbooks and a go-live checklist
covering every gate in COMPLIANCE.md. **No deployment, purchase or live billing
without owner approval.**

## Phase report template
1. Files created
2. Files changed
3. Features completed (only implemented **and** tested)
4. Tests executed (command → result)
5. Remaining issues / blocked items
6. Deployment instructions (or "none: no deployable change")
