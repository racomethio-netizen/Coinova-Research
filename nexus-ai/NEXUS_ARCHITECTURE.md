# NEXUS architecture (current state)

Detailed design and history: docs/ARCHITECTURE.md. This page is the up-to-date map.

## Processes (production: one Render web service, free plan)

```
FastAPI API + static dashboard
 ├─ market-data worker   candles (REST), books (REST + WebSocket), derived timeframes,
 │                       retention, venue health
 ├─ derivatives worker   funding, mark/index price, open interest, long/short ratios,
 │                       liquidation stream (Binance USD-M public data)
 ├─ trader               order maintenance -> exits -> valuation -> balance check (live)
 │                       -> risk state -> operating mode -> entries
 ├─ notifier             Telegram
 ├─ news, X sentiment    context only (can pause entries, never trigger them)
 └─ forward paper tests  funding carry, taker flow (pre-registered research)
Postgres (Supabase): durable state, ledgers, audit log.  Redis: cache, flags, streams.
```

## Market data coverage

| Data | Source | Stored | Freshness check |
|---|---|---|---|
| Candles 5m, 15m, 1h, 4h, 1d | Binance spot REST (closed bars only) | yes, 120 days backfill | Data quality page; 5m staleness stops entries |
| Candles 1m | Binance spot REST, filled gradually (3 pages per poll) | yes, 30-day rolling | Data quality page |
| Candles 3m, 30m, 2h, 6h, 12h | Derived from stored closed bars (1m, 15m, 1h); only complete groups, epoch-aligned like Binance | yes (source `derived:<tf>`) | Data quality page |
| Order books, spread, depth, imbalance | Binance REST snapshots + WebSocket | snapshots every 5 min; live book in Redis | books older than 10-15 s are refused |
| Funding, mark/index price, open interest, long/short ratios | Binance USD-M public REST | Redis cache | shown with timestamps; missing = "unavailable" |
| Liquidations | Binance `!forceOrder@arr` stream (at most one per symbol per second: a sample, not a total) | Redis rolling window | stream state reported |
| Exchange filters, fees | ccxt market metadata (tick size, step, min qty/notional) | trading_pairs | refreshed at start |
| Live trades (tick stream) | not collected yet | - | planned (memory budget on the free plan) |

Rules: closed bars only; duplicates impossible (one row per pair, timeframe and open
time); gaps detected and logged, never filled; Binance back-off and request-weight budget
shared by every caller; no synthetic data in production.

## Decision path for an entry

strategy signal (validated strategies only) -> news / X / regime / cooldown gates ->
risk snapshot and per-trade checks -> risk state (must allow entries) -> loss-streak
multiplier -> market analysis gate -> sizing (risk budget / stop distance, fees,
slippage, exchange rules) -> correlation gate -> allocator -> operating mode (paper or
live orders only in the matching mode) -> order manager (intent persisted first, one
submission, idempotent fills, unknown orders resolved by client order ID).

## Safety layers

Kill switch (durable), risk states (NEXUS_RISK_POLICY.md), live gates, operating modes,
balance reconciliation, Binance back-off and weight budget, owner-only API with 2FA for
risky actions, append-only audit log.
