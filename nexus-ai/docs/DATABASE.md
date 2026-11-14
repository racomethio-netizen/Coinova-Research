# Database

PostgreSQL 16. Schema source of truth: `migrations/versions/`. Models in
`packages/database/nexus_db/models.py` must match; `tests/integration/test_schema.py`
fails on drift (and `alembic check` reports it).

## Conventions

* Money/quantities: `NUMERIC(38,18)`; ratios/probabilities: `NUMERIC(20,10)`. No floats.
* All timestamps `timestamptz`, sessions run with `timezone=UTC`.
* Closed value sets: `text` + `CHECK`; constraint names follow `NAMING_CONVENTION`.
* Never edit an applied migration; add a new one. Never drop a real database.

## Tables

| Group | Tables |
|---|---|
| Identity & audit | `users`, `audit_logs` (append-only) |
| Exchanges | `exchanges` (seeded, disabled), `exchange_credentials` (encrypted), `trading_pairs` |
| Market data | `market_candles` (PK pair/timeframe/open_time), `orderbook_snapshots`, `indicators` |
| AI | `ai_models` (one active per name), `ai_predictions`, `model_evaluations` |
| Strategy | `strategies` (seeded, disabled), `trading_signals` |
| Trading (mode-scoped) | `portfolios`, `balances`, `orders`, `trades` (fills), `positions`, `performance_snapshots`, `risk_events` |
| Research & ops | `backtest_runs`, `system_events` |

## Invariants enforced by the database

| Invariant | Mechanism |
|---|---|
| Paper and live records never mix | composite FKs `(parent_id, mode)` + immutable `mode` trigger |
| An order's client ID is globally unique | `UNIQUE (client_order_id)` |
| No overfill, no non-positive amounts, limit orders have prices | CHECKs on `orders` |
| One open position per base asset per portfolio (no duplicate exposure across exchanges) | partial unique index `uq_positions_open_asset` |
| Spot long-only | `positions.quantity >= 0`; buy signals require stop < entry < target |
| No negative balances | CHECK on `balances` |
| OHLC consistent; allowed timeframes only; one candle per bar | CHECKs + PK |
| One active model per name | partial unique index |
| One active API credential per user/exchange/environment | partial unique index |
| Audit log immutable | triggers block UPDATE/DELETE/TRUNCATE |

## Backups

Daily encrypted `pg_dump` + restore drill: Phase 14.
