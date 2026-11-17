# NEXUS AI architecture

Personal, multi-exchange, **spot-only** crypto trading system. Paper trading first;
live trading only after explicit owner activation (see SECURITY.md).

## Guiding principles

1. **Capital protection beats opportunity.** Every order passes the risk engine; any
   uncertainty (unknown order state, stale data, unreconciled balance) blocks trading.
2. **Nothing fabricated.** No invented prices, fills or metrics. Paper results are
   always labeled paper. Missing data is reported, never interpolated silently.
3. **Spot only by construction.** No leverage, margin, futures, shorting or
   martingale code paths exist; adapters expose no such methods.
4. **The database enforces invariants** (paper/live isolation, one open position per
   asset, append-only audit log) so a code bug cannot silently violate them.
5. **Small, testable modules.** Pure logic (sizing, precision, metrics, features) is
   separated from I/O and unit-tested; I/O is integration-tested on real Postgres/Redis.

## Components

```
                     ┌──────────────────────────────────────────────┐
  Binance ─┐         │ exchange-gateway  (common ExchangeAdapter)   │
  Kraken  ─┼─REST/WS─┤   CCXT adapters + native calls where needed  │
  Coinbase─┘         └──────┬──────────────────────┬────────────────┘
                            │ candles / books      │ orders / balances / fills
                   ┌────────▼────────┐     ┌───────▼────────────┐
                   │  market-data    │     │ execution-engine   │◄──── kill switch
                   │ ingest, gaps,   │     │ order state machine│      (Redis + DB)
                   │ normalization   │     │ reconciliation     │
                   └────────┬────────┘     └───────▲────────────┘
                 PostgreSQL │ (closed candles)     │ approved orders only
                   ┌────────▼────────┐     ┌───────┴────────────┐
                   │   strategies    │────►│    risk-engine     │
                   │ 4 strategies,   │ sig │ sizing, limits,    │
                   │ MTF confirm     │     │ exposure, allocator│
                   └────────▲────────┘     └───────┬────────────┘
                   ┌────────┴────────┐             │ events
                   │   ai-engine     │     ┌───────▼────────────┐
                   │ XGBoost, regime │     │ notification-engine│──► Telegram
                   │ calibration,    │     └────────────────────┘
                   │ drift           │
                   └─────────────────┘
   research/backtesting + research/training: offline, same strategy/risk code
   apps/api (FastAPI) ◄── apps/dashboard (Next.js) behind Nginx/HTTPS
```

| Directory | Responsibility | Status |
|---|---|---|
| `packages/configuration` (`nexus_config`) | Typed settings from env; hard risk ceilings; live-trading gate | ✅ Phase 1 |
| `packages/common` (`nexus_common`) | Decimal math, UTC time, JSON logging with redaction | ✅ Phase 1 |
| `packages/security` (`nexus_security`) | AES-256-GCM credential encryption, key rotation | ✅ Phase 1 |
| `packages/database` (`nexus_db`) | SQLAlchemy models, sessions, transactions | ✅ Phase 1–2 |
| `migrations/` | Alembic migrations (schema source of truth) | ✅ Phase 1–2 |
| `services/exchange-gateway` (`nexus_exchange`) | Common adapter interface, CCXT spot adapter, precision | ✅ read-only verified live; orders need keys |
| `services/market-data` (`nexus_market`) | REST backfill, WebSocket streams, gap detection, health | ✅ |
| `services/strategies` | Trend, momentum, breakout, mean reversion; MTF | ✅ (all rejected by validation) |
| `services/ai-engine` | Features, XGBoost, regimes, calibration, drift | ✅ (model rejected) |
| `services/risk-engine` | Sizing, loss limits, drawdown shutdown, allocator | ✅ |
| `services/execution-engine` | Order lifecycle, reconciliation, slippage | ✅ |
| `research/backtesting` | Event-driven backtester, walk-forward | ✅ |
| paper trading | Simulated fills on live prices (execution-engine mode) | ✅ |
| `apps/dashboard` | Next.js + Tailwind + Lightweight Charts | ✅ |
| `services/notification-engine` | Telegram | ✅ |
| `infrastructure/` | Docker, Nginx, monitoring, backups | ✅ verified locally; VPS pending |

All Python services share one image; each Compose service runs a different command.
Services communicate through PostgreSQL (durable state) and Redis (streams for
market events, the kill-switch flag, locks). There is no direct service-to-service RPC
on the order path, so a crashed service never leaves a half-sent request in memory
only: intent is always in Postgres first.

## Order lifecycle (Phase 8 contract, interface built in Phase 1)

```
signal ─► risk check ─► prepare_order (round to precision, validate)
       ─► INSERT orders(status='pending_submit', client_order_id=UUIDv4)  [commit]
       ─► submit_order (exactly one attempt)
            ├─ ack ──────────────► status from exchange (open/filled/...)
            ├─ definitive reject ─► 'rejected' (+ reason)
            └─ timeout/ambiguous ─► 'unknown' ─► find_order_by_client_id
                                       ├─ found ─► adopt exchange state
                                       ├─ not found (after settle delay) ─► 'rejected'
                                       └─ lookup failed ─► stay 'unknown', block new
                                          orders on that exchange, alert owner
```

The adapter enforces "never retry a submission": `OrderStateUnknown` is raised for
every transport-level failure during submit, and the adapter never calls
`create_order` twice. Unit tests assert exactly one call in every failure mode.

## Paper vs live isolation

`portfolios`, `balances`, `orders`, `trades`, `positions`, `trading_signals`,
`performance_snapshots` and `risk_events` carry `mode` ∈ {paper, live}. Children
reference parents through composite foreign keys `(parent_id, mode)`, and a trigger
makes `mode` immutable. PostgreSQL therefore rejects a live order in a paper
portfolio, a paper fill on a live order, or flipping a portfolio's mode.

## Data correctness and leakage prevention

* Only **closed** candles are stored as features/backtest input
  (`fetch_closed_candles` drops the forming bar; `market_candles.is_closed`).
* `ai_predictions.as_of` records the last closed candle used; `realized_outcome` is
  filled later and never used as a feature.
* Backtests and training will use the same feature code as live, with walk-forward
  splits and purging/embargo around split boundaries (Phase 6/9).

## Key decisions

| Decision | Why |
|---|---|
| CCXT for all three exchanges, native calls where CCXT is wrong/missing | One tested normalization layer; gaps documented in EXCHANGES.md |
| `Decimal` + `NUMERIC(38,18)` everywhere | No float rounding in money/quantities |
| Text + CHECK instead of PG enums | Easier, transactional evolution of value sets |
| Client order ID = UUIDv4 | Accepted by all three exchanges (Binance ≤36 chars, Kraken `cl_ord_id`, Coinbase) |
| Sync SQLAlchemy in services, thread offload in the API | Simpler transactions; DB is not the bottleneck at this scale |
| One Python image | Smaller attack surface and identical dependencies across services |

---

## Mandate v2 (owner-approved 2026-11-14)

This section supersedes the earlier spot-only mandate where they differ.

| Item | Previous | Now |
|---|---|---|
| Markets | Spot only | Spot (Binance, Kraken, Coinbase) **and** futures (Binance only) |
| Futures leverage | Not allowed | Up to **1x** (hard ceiling in code: `MAX_FUTURES_LEVERAGE`) |
| Short selling | Disabled | Allowed on Binance futures only (`futures_allow_short`, off by default) |
| Futures status | n/a | **Disabled by default**; must pass the same out-of-sample gates as spot |
| Risk per trade | 0.25% | **1%** (hard ceiling `MAX_RISK_PER_TRADE`) |
| Daily loss / weekly loss / max drawdown | 1% / 2% / 5% | unchanged |
| Minimum risk/reward | not enforced | **1:2** required for every trade (to be enforced in Phase 5 / no-trade module) |
| Timeframes | 5m, 15m, 1h, 4h | + **1d** (Phase 2: needs a migration to widen CHECK constraints) |
| Martingale / averaging down | Disabled | Disabled (never configurable) |
| LIVE trading | Off until approved | Unchanged: off until every gate in `docs/SECURITY.md` passes |

Not in scope unless the owner approves later: funding-rate-driven strategies on other
exchanges, leverage above 1x, and any automatic change to live strategy parameters.

### Phase 1 deliverables (this document and `packages/configuration`)

* Owner-approved limits above, enforced by `nexus_config.Settings` with ceilings and tests.
* Futures settings: `futures_enabled` (default false), `futures_exchanges` (Binance only),
  `futures_max_leverage` (ceiling 1), `futures_allow_short` (default false). Any config
  that asks for an unsupported exchange or more than 1x leverage fails at startup.
* `safe_summary()` reports `spot_enabled`, `futures_enabled`, `max_futures_leverage`,
  `short_selling_enabled` and `martingale_enabled` (always false).

### Planned architecture additions (built in later phases)

| Package | Responsibility | Phase |
|---|---|---|
| `services/analysis` | Market structure (swing highs/lows, BOS, CHoCH), support/resistance, supply/demand zones, liquidity sweeps, volume profile, ATR/volatility, order-book imbalance, BTC correlation, regime classification (trend, range, high volatility) | 3 |
| `services/no_trade` | Explicit NO TRADE decisions with reasons: abnormal volatility, insufficient liquidity, wide spread, stale data, conflicting signals, risk/reward below 1:2, sideways market without a setup, daily risk limit reached, news pause | 5 |
| `services/strategies` (extend) | Pullback, liquidity-sweep reversal (new); a regime-to-strategy performance matrix so each strategy is used only where it validated | 6 |
| `services/ai-engine` (extend) | 0–100 trade score built from independent confirmations, with a plain-language explanation for each decision. Language models never execute trades | 4 |
| `services/risk-engine` (extend) | Trailing stops, partial profit taking, consecutive-loss protection, correlated-exposure limits | 7 |
| `services/exchange-gateway` (extend) | Binance USD-M futures adapter (1x, isolated margin), order types for long and short | 10 |
| `services/news` (new) | Free public feeds (Federal Reserve, BLS, SEC, exchange status pages), news-risk score, automatic trading pause during high-risk windows | 9 |

### Gaps this plan does not cover (called out explicitly)

* Monte Carlo analysis is planned for Phase 8 (backtesting).
* Online self-modification of live strategies is never allowed; parameter changes go
  through offline evaluation and validation only.
* Dashboard START / PAUSE / STOP / EMERGENCY KILL SWITCH controls need explicit
  confirmation dialogs; the kill switch already exists in the API.
