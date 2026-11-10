# Entity-relationship diagram

Existing tables are in `supabase/migrations/` (see `docs/DATABASE.md`). Planned
tables are introduced by **new** migrations in the phase shown. Existing
migrations are never edited.

## Planned NEXUS tables (with the existing tables they connect to)

```mermaid
erDiagram
  profiles ||--o{ user_roles : has
  profiles ||--o{ subscriptions : has
  membership_plans ||--o{ plan_features : grants
  profiles ||--o{ paper_accounts : owns
  profiles ||--o{ backtest_runs : requests
  profiles ||--o{ user_strategies : defines
  profiles ||--o{ device_push_tokens : registers
  profiles ||--o{ abuse_reports : files
  profiles ||--o{ account_deletion_requests : requests
  profiles ||--o{ notifications : receives
  cryptocurrencies |o--o{ market_pairs : "base asset"

  market_sources ||--o{ market_pairs : lists
  market_sources ||--o{ ingest_runs : runs
  market_pairs ||--o{ market_candles : has
  market_pairs ||--o{ candle_gaps : has
  market_pairs ||--o{ candle_revisions : has
  market_pairs ||--o| latest_prices : has
  ingest_runs ||--o{ market_candles : wrote

  strategies ||--o{ model_versions : "trained as"
  strategies ||--o{ user_strategies : "parameterized by"
  experiments ||--o{ validation_reports : produces
  validation_reports |o--o{ model_versions : supports

  market_pairs ||--o{ signal_evaluations : evaluated
  model_versions ||--o{ signal_evaluations : used
  signal_evaluations ||--o| signals : "published as"
  signals ||--o{ signal_events : lifecycle
  signals ||--o| signal_outcomes : "final outcome"
  signals ||--o{ signal_annotations : annotated
  signals ||--o{ notifications : announces

  backtest_runs ||--o| backtest_results : yields
  backtest_runs ||--o{ backtest_trades : contains
  strategies ||--o{ backtest_runs : tested
  user_strategies |o--o{ backtest_runs : tested

  paper_accounts ||--o{ paper_orders : places
  paper_orders ||--o{ paper_fills : "filled by"
  paper_accounts ||--o{ paper_positions : holds
  market_pairs ||--o{ paper_orders : for
  signals |o--o{ paper_orders : "optionally from"

  market_pairs {
    bigint id PK
    bigint source_id FK
    text symbol "e.g. BTCUSDT"
    text base_asset
    text quote_asset
    text coin_id FK "nullable, cryptocurrencies"
    boolean enabled
    text timeframes "array: 15m,1h,4h,1d"
  }
  market_candles {
    bigint pair_id PK
    text timeframe PK
    timestamptz open_time PK
    timestamptz close_time
    numeric open
    numeric high
    numeric low
    numeric close
    numeric volume
    numeric quote_volume
    integer trade_count
    boolean is_final
    bigint ingest_run_id FK
    timestamptz ingested_at
  }
  candle_gaps {
    bigint id PK
    bigint pair_id FK
    text timeframe
    timestamptz gap_start
    timestamptz gap_end
    text status "open, backfilled, unrecoverable"
    integer attempts
  }
  model_versions {
    uuid id PK
    bigint strategy_id FK
    text version
    text model_type
    text artifact_uri
    text artifact_sha256
    text status "draft, shadow, approved, retired"
    uuid validation_report_id FK
    text git_sha
    uuid approved_by FK
    timestamptz approved_at
  }
  experiments {
    uuid id PK
    text kind "walk_forward, backtest, ablation"
    jsonb config
    text dataset_hash
    text git_sha
    text lockfile_hash
    integer seed
    jsonb metrics
    text status
  }
  signal_evaluations {
    uuid id PK
    bigint pair_id FK
    text timeframe
    timestamptz as_of
    uuid model_version_id FK
    text decision "BUY, SELL, HOLD"
    numeric probability "nullable"
    text reason_codes "array"
    jsonb features
  }
  signals {
    uuid id PK
    uuid evaluation_id FK
    bigint pair_id FK
    text timeframe
    text direction "BUY, SELL"
    timestamptz as_of
    timestamptz published_at
    numeric entry_low
    numeric entry_high
    numeric stop_loss
    numeric take_profits "array, max 3"
    numeric risk_reward
    numeric confidence "nullable"
    text confidence_band
    uuid model_version_id FK
    jsonb explanation
    timestamptz expires_at
    text visibility "staff, beta, public"
    text prev_hash
    text content_hash UK
  }
  signal_events {
    bigint id PK
    uuid signal_id FK
    text event_type "entered, tp_hit, sl_hit, expired"
    numeric price
    timestamptz occurred_at
  }
  signal_outcomes {
    uuid signal_id PK
    text outcome
    numeric exit_price
    numeric net_return
    numeric r_multiple
    text resolver_version
    timestamptz resolved_at
  }
  signal_annotations {
    bigint id PK
    uuid signal_id FK
    text kind "note, correction, retraction"
    text body
    uuid author_id FK
    timestamptz created_at
  }
  backtest_runs {
    uuid id PK
    uuid user_id FK "nullable for system runs"
    bigint strategy_id FK
    jsonb params
    jsonb assumptions
    text dataset_hash
    text status "queued, running, succeeded, failed"
    text git_sha
  }
  paper_accounts {
    uuid id PK
    uuid user_id FK
    numeric starting_balance
    numeric cash_balance
    text status "active, archived"
  }
  paper_orders {
    uuid id PK
    uuid account_id FK
    uuid user_id FK
    bigint pair_id FK
    text side "buy, sell"
    text order_type "market, limit"
    numeric quantity
    numeric limit_price
    numeric stop_loss
    numeric take_profit
    text status "pending, filled, cancelled, rejected"
    uuid signal_id FK "nullable"
  }
  paper_fills {
    uuid id PK
    uuid order_id FK
    numeric price
    numeric quantity
    numeric fee
    numeric slippage
    timestamptz price_time
  }
  paper_positions {
    uuid id PK
    uuid account_id FK
    bigint pair_id FK
    numeric quantity
    numeric avg_entry
    numeric realized_pnl
    text status "open, closed"
  }
```

Also planned, but omitted from the diagram for readability:
- `market_sources`, `ingest_runs`, `candle_revisions`, `latest_prices`
- `strategies`, `user_strategies`, `validation_reports`
- `backtest_results` (metrics and downsampled equity curve as jsonb), `backtest_trades`
- `plan_features` (tier × feature key → value)
- `device_push_tokens`, `abuse_reports`, `account_deletion_requests`
- `engine_jobs` (queue: kind, payload, status, run_after, attempts, locked_until)
- `engine_heartbeats` (component, last_seen, details)

## RLS and write intent (planned)

| Table group | anon | user | staff/admin | `nexus_engine` | service role |
|---|---|---|---|---|---|
| sources, pairs, candles, latest_prices | read | read | pairs: admin write (audited, MFA) | write | full |
| gaps, revisions, ingest_runs, heartbeats | — | — | read | write | full |
| strategies, model_versions, validation_reports, experiments | — | approved/summary read | read; approve (MFA) | write drafts | full |
| signal_evaluations | — | — | read | **insert only** | insert only |
| signals, events, outcomes | per `visibility` and tier (D3) | per `visibility` and tier | read | **insert only** | insert only |
| signal_annotations | read (public signals) | read | **insert only** (MFA) | — | insert only |
| backtest_runs/results/trades | — | own; create via quota function (Premium) | read | claim, update status, write results | full |
| user_strategies | — | own CRUD (Premium, schema-validated) | read | read | full |
| paper_accounts/orders | — | own; orders inserted via function (validated) | read | claim/fill/update | full |
| paper_fills, paper_positions | — | read own | read | write | full |
| plan_features | read | read | — | — | write (sync job) |
| device_push_tokens | — | own | — | — | full |
| abuse_reports | — | create; read own | read/update | — | full |
| account_deletion_requests | — | create/read own | read | — | process |
| engine_jobs | — | — | read; enqueue via function (MFA) | claim/update | full |

"Insert only" is enforced by `BEFORE UPDATE OR DELETE` triggers that raise. These
triggers also apply to the service role. RLS tests are added to `tests/db/rls.test.ts`
for each group (CLAUDE.md rule).

## Storage estimate (initial scope)

- Candles: 2 pairs × (35 040 + 8 760 + 2 190 + 365) rows/yr ≈ 93 k rows/yr. A 5-year
  backfill is about 0.5 M rows, under 150 MB with indexes. This fits the Supabase Free tier for development.
- Evaluations: 2 pairs × 127 closes/day ≈ 93 k rows/yr. `features` jsonb is limited
  to the model's feature vector. HOLD evaluations older than 1 year can be aggregated
  (decision before Phase 4 ships; published signals are never aggregated or deleted).
