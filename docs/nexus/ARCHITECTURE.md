# NEXUS AI: target architecture

## 1. System overview

```
 ┌──────────────┐     ┌──────────────────┐
 │ Web browser  │     │ Mobile (Expo RN) │
 └──────┬───────┘     └────────┬─────────┘
        │ cookie session       │ Authorization: Bearer <Supabase JWT>
        ▼                      ▼
 ┌─────────────────────────────────────────────────────┐
 │ Next.js 16 app (existing): the ONLY public API     │
 │  pages · /api/** route handlers · server actions   │
 │  auth guards · entitlements · CSRF · rate limits   │
 └──────┬──────────────────────────────────┬──────────┘
        │ user session client (RLS)        │ service client (jobs/webhooks/admin)
        ▼                                  ▼
 ┌─────────────────────────────────────────────────────┐
 │ Supabase: PostgreSQL + Auth + Storage               │
 │  RLS · SQL entitlement functions · append-only      │
 │  ledgers (signals, outcomes, audit) · job queue     │
 └──────▲──────────────────────────────────────────────┘
        │ direct Postgres (role nexus_engine, least privilege)
 ┌──────┴──────────────────────────────────────────────┐
 │ NEXUS engine (Python 3.12, new `engine/` directory)│
 │  FastAPI: /health + token-protected /internal/*    │
 │  worker loop: ingest → validate → features →       │
 │  signals → outcomes → paper fills → backtest queue │
 └──────┬──────────────────────────────────────────────┘
        │ HTTPS, public market-data endpoints only (no keys, no signing)
        ▼
  Exchange public REST (primary: Binance market-data API; secondary: one more venue)
```

Existing external integrations are unchanged and remain server-side only:
CoinGecko, Resend, Telegram and Anthropic.

## 2. Responsibilities

| Component | Owns | Never does |
|---|---|---|
| **Next.js app** | All user-facing HTTP: auth, entitlement checks, user CRUD (paper orders, preferences, alerts), reading signals/backtests through RLS, admin UI, billing webhooks | Market-data ingestion for signals, model inference, fills, backtest computation |
| **Engine (Python)** | Candle ingestion and validation, indicators, signal generation and explanation, outcome resolution, paper-order fills, backtests, walk-forward validation, model registry writes | Serve end users directly, hold user sessions, decide entitlements, call private exchange APIs |
| **PostgreSQL** | Source of truth, authorization (RLS), immutability (triggers), tier limits, job queue | — |
| **Mobile app** | UI. Talks only to the Next.js API and Supabase Auth | Hold secrets beyond the user's own session tokens |

## 3. Key data flows

1. **Candle ingestion** (engine, every timeframe close + small delay): fetch closed
   candles from the public endpoint, validate, then upsert on
   `(pair_id, timeframe, open_time)`. Detect gaps and enqueue backfills. Record each
   `ingest_runs` row.
2. **Signal evaluation** (engine, on each closed candle per pair × timeframe): load
   closed candles only, compute features, apply the strategy, the model and the risk
   gates. Every evaluation (including HOLD) goes into `signal_evaluations`.
   BUY/SELL that pass every gate go into `signals` (insert-only, hash-chained).
3. **Outcome resolution** (engine, on new candles): walk candles after publication.
   Append to `signal_events` (entered, TP hit, SL hit, expired). Insert the single
   final `signal_outcomes` row.
4. **Paper trading:** the user submits an order through the Next.js API (validated,
   entitlement-checked), which inserts `paper_orders`. The engine claims pending
   orders, fills them against the latest price (staleness-checked, with slippage and
   fees), and monitors SL/TP.
5. **Backtests (Premium):** the user requests one through the Next.js API (quota in
   SQL), which inserts `backtest_runs` with status `queued`. The engine claims it
   (`FOR UPDATE SKIP LOCKED`), runs it, and writes results.
6. **Model lifecycle:** training runs offline (developer machine or CI), never on
   the production worker. Artifacts and validation reports are uploaded and the
   `model_versions` row is created as `draft`. An admin (with MFA) approves it, the
   approval is audited, and only `approved` models publish user-visible signals.
   Unapproved models run in `shadow`.
7. **Notifications:** new signals create `notifications` through the existing
   pipeline. Mobile push is added as a delivery channel.

## 4. Repository layout (target)

```
/                     existing Next.js app (unchanged location)
├─ src/…              web + public API
├─ supabase/…         migrations (single schema for both services)
├─ engine/            NEW Python service
│  ├─ pyproject.toml, uv.lock, Dockerfile
│  ├─ src/nexus_engine/{config,db,marketdata,indicators,features,strategies,
│  │                    models,signals,risk,backtest,validation,paper,
│  │                    execution,api,workers}/
│  └─ tests/
├─ mobile/            NEW Expo app (Phase 8)
├─ packages/shared/   NEW (Phase 8): API types + Zod schemas shared by web and mobile
└─ docs/nexus/        this documentation
```

## 5. Architecture decision records

### ADR-001: Next.js stays the only public API; FastAPI is the engine's internal surface
**Context.** The spec names FastAPI as the backend. Auth, entitlements, CSRF,
rate limits, the response envelope and billing already exist and are tested in Next.js.
**Decision.** Users (web and mobile) call only the Next.js API. The Python engine
runs FastAPI for `/health` and token-protected `/internal/*` operations
(trigger an ingestion, a backfill or a resolver run), plus a worker loop. Services
coordinate through Postgres (queue tables, `SKIP LOCKED`), not service-to-service HTTP.
**Why.** There is one place for auth and entitlement logic instead of two, in two
languages. The engine has almost no inbound attack surface. It also works with one
small instance.
**Revisit when** latency-sensitive engine reads are needed by clients (e.g.
real-time scanner streaming). Then the engine gets a read-only public route behind the same JWT verification.

### ADR-002: Single Postgres schema; the engine connects with a least-privilege role
The engine uses a dedicated login role `nexus_engine` with grants only on the tables
it writes (candles, signals, outcomes, paper fills/positions, backtests, model
registry, engine queue). It does **not** use the Supabase service-role key. It
never bypasses RLS on user tables beyond the explicit policies written for it.
Connection is through the Supabase pooler.

### ADR-003: Indicators are implemented in-house (NumPy/pandas), cross-checked against TA-Lib in tests
EMA, RSI (Wilder), MACD, ATR (Wilder), Bollinger Bands and volume ratios are short,
well-specified formulas. Implementing them ourselves avoids a native C dependency
in the production image (TA-Lib). It also avoids depending on a library whose
maintenance status must be checked (pandas-ta). TA-Lib is a **test-only** dependency used to
produce reference values. A mismatch beyond tolerance fails CI.

### ADR-004: Minimal public-only market-data adapters; no general exchange SDK
We write small adapters for public kline endpoints instead of using CCXT. A
general SDK contains authenticated order and withdrawal code paths. Leaving it out
means production contains **no code capable of signing exchange requests**, which
CI enforces (SECURITY_RISKS.md, R-12). This also keeps the dependency surface small.

### ADR-005: Published signals are insert-only and hash-chained
`signals`, `signal_events`, `signal_outcomes` and `signal_annotations` reject
UPDATE/DELETE with triggers, which also apply to the service role. Each signal stores
`content_hash = sha256(canonical_json(payload) || prev_hash)`. Corrections are
new annotation rows, shown publicly. The chain head can be published externally
to make silent rewrites detectable. This implements "never delete losing signals" and
"never modify predictions after publication".

### ADR-006: Decisions at bar close, fills at the next bar open
In the backtester, outcome resolver and paper engine, decisions use only fully
closed candles. Simulated entries happen no earlier than the next bar. When SL and TP
both fall inside one candle, **SL is assumed to hit first** (conservative). One shared
`execution` module implements this for all three to keep results consistent.

### ADR-007: Training happens offline; production does inference only
Training needs CPU and RAM that a small host doesn't have, and it must be
reproducible. Training runs locally or in CI with fixed seeds, a locked dependency
set and a dataset hash. Artifacts are stored with SHA-256 in Supabase Storage. The
production worker only loads approved artifacts.

### ADR-008: Kill switches are environment variables, not database flags
`LIVE_EXECUTION_ENABLED`, `PAID_SIGNALS_ENABLED` and `SIGNALS_VISIBILITY` are
read from the environment and validated at startup. An admin with database
access alone cannot turn them on. A deploy by the owner is required. Database
feature flags remain for low-risk UI toggles.

### ADR-009: Money and quantities are decimal end to end
The TypeScript code keeps `decimal.js` + decimal strings (existing rule). Python
uses `decimal.Decimal` for ledgers (paper trading, outcomes). NumPy floats are
allowed only inside indicator/feature computation, never for balances or P/L.
Postgres uses `numeric`.

## 6. Environments and configuration

| Env | Web | Engine | Database | Market data | Billing |
|---|---|---|---|---|---|
| development | `npm run dev` | `uv run nexus-engine` | local Postgres (`scripts/db-test.sh`) or Supabase free project | mock or public endpoints | dev simulator |
| staging | Render (or equivalent) | 1 small instance | Supabase project (separate) | public endpoints | dev simulator |
| production | Render | 1 small instance | Supabase project (separate) | public endpoints, ToS reviewed | **none until legal approval** |

All configuration is environment-based and validated at startup: `src/lib/env.ts`
for the web app and `pydantic-settings` for the engine. New engine variables
(planned): `DATABASE_URL`, `ENGINE_INTERNAL_TOKEN`, `MARKET_SOURCE`,
`MARKET_PAIRS`, `SIGNALS_VISIBILITY` (`staff`|`beta`|`public`),
`PAID_SIGNALS_ENABLED=false`, `LIVE_EXECUTION_ENABLED=false`,
`MODEL_ARTIFACT_BUCKET`.

## 7. Hosting and budget (initial budget under $500)

The **prices below are indicative** and must be checked on the vendors' pricing
pages before committing.

| Item | When | Indicative cost |
|---|---|---|
| Supabase | Free tier for development; Pro for production (no project pausing, daily backups) | $0 → about $25/mo |
| Web (Render web service) | staging/production | about $7/mo (starter) |
| Engine (Render service, 1 instance; may need more than 512 MB RAM for pandas/XGBoost inference) | staging/production | about $7–25/mo |
| Render cron services (existing blueprint has 4) | can be **removed** by letting the engine scheduler call `/api/cron/*` | saves the cron charges |
| Apple Developer Program | before iOS release (Phase 8+) | $99/yr |
| Google Play developer account | before Android release | $25 one-time |
| Expo EAS | free tier is enough initially | $0 |
| Domain | before launch | about $10–20/yr |
| CoinGecko (demo), exchange public data | — | $0 |
| Anthropic API (optional rephrasing / assistant) | usage-based, capped by existing SQL quotas | small |

**Spending plan:** Phases 2–6 run at about **$0/mo** (local + Supabase Free).
Spending starts at staging (Phase 10). A minimal production stack is about $40–60/mo. App store
fees ($124) are paid only when mobile release is approved. This keeps the first
6 months under $500 with margin.
