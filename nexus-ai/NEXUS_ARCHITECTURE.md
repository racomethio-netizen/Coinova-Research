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
 ├─ decision log         records the fusion decision per pair after each closed 15m bar
 └─ forward paper tests  funding carry, taker flow (pre-registered research)
Postgres (Supabase): durable state, ledgers, audit log.  Redis: cache, flags, streams.
```

## Market data coverage

| Data | Source | Stored | Freshness check |
|---|---|---|---|
| Candles 1m, 5m, 15m, 1h, 4h, 1d | Binance WebSocket closed klines first; REST only for bars the stream missed (90 s grace) and for history | yes, 120 days backfill (1m: 30-day rolling) | Data quality page; 5m staleness stops entries |
| Gap repair | every 5 min, the oldest hole in each series is refilled from REST in pages | - | logged `gap_repaired` |
| Candles 3m, 30m, 2h, 6h, 12h | Derived from stored closed bars (1m, 15m, 1h); only complete groups, epoch-aligned like Binance | yes (source `derived:<tf>`) | Data quality page |
| Order books, spread, depth, imbalance | Binance REST snapshots + WebSocket | snapshots every 5 min; live book in Redis | books older than 10-15 s are refused |
| Funding, mark/index price, open interest, long/short ratios | Binance USD-M public REST | Redis cache | shown with timestamps; missing = "unavailable" |
| Liquidations | Binance `!forceOrder@arr` stream (at most one per symbol per second: a sample, not a total) | Redis rolling window | stream state reported |
| Exchange filters, fees | ccxt market metadata (tick size, step, min qty/notional) | trading_pairs | refreshed at start |
| Live trades (tick stream) | not collected yet | - | planned (memory budget on the free plan) |

Rules: closed bars only; duplicates impossible (one row per pair, timeframe and open
time); gaps detected and logged, never filled; Binance back-off and request-weight budget
shared by every caller; no synthetic data in production.

## Technical analysis (Phase C)

* `nexus_strategies/indicators.py` (base) and `nexus_strategies/ta.py` (extended):
  EMA 9/20/21/50/100/200, SMA, ADX/DMI, Supertrend, Ichimoku (no Chikou: look-ahead),
  RSI, RSI divergence, MACD, ROC, momentum, Stochastic RSI, CCI, ATR, historical
  volatility, Bollinger (+width), Keltner, ATR percentile, expansion/contraction, OBV,
  MFI, relative volume, volume SMA and z-score, volume-price divergence, candle body/wick
  shares, pivots (previous day), range/consolidation, breakout, retest, failed breakout,
  equal highs/lows, wick rejection.
* `nexus_analysis`: swings, HH/HL/LH/LL structure, BOS/CHoCH, support/resistance,
  supply/demand zones, liquidity sweeps, correlation (existing); `technical.py` snapshot;
  `mtf.py` multi-timeframe states (1m-1d) aligned on closed bars with agreement scores,
  higher-timeframe confirmation and conflict flag.
* Order-book analytics (spread, depth, imbalance, fill estimates):
  `nexus_market/analytics.py`.
* API `/analysis/technical`, `/analysis/mtf`; dashboard page Technical analysis.
* All analysis is causal and tested for it; none of it trades by itself.

## Signal fusion and strategy selection (Phase E)

* `nexus_decision/selector.py`: a strategy may act in a regime only with out-of-sample
  evidence: passed validation, regime in its allowed regimes, >= 30 trades in that regime,
  profit factor >= 1.15, and a positive ROBUST expectancy (mean - 1 standard error). Recent
  forward results can only remove a strategy. Used by the trader (regime gate) and the
  fusion engine.
* `nexus_decision/fusion.py`: one LONG / SHORT / WAIT / NO_TRADE per pair. Only eligible
  strategies or an activated validated model create a trade; indicators, timeframes, order
  book, funding, BTC and news are context (reasons, conflicts, blockers), never extra votes.
  Probability = measured out-of-sample win rate (with n) or a calibrated model; no
  arbitrary scores. Structured fields: strategy, timeframe, entry type, entry, stop, target,
  reward/risk, probability and its source, expected return, regime, expiry, model and
  feature version, reasons, conflicts, blockers, and every source considered.
* API `/decision/fusion`; dashboard page Trade candidates.

## Decision log (Phase H)

* `nexus_api/decision_log.py` (worker `decision-log`): after each closed 15m bar it runs
  the fusion engine once and stores a `decision_logs` row per pair. A row is written when
  something material changed, and at least hourly.
* Each row holds:
  * the market data snapshot identifier (exchange, pair, bar and order-book update);
  * the context inputs, multi-timeframe scores and regimes by timeframe;
  * the model state;
  * every strategy's selector verdict and the setups on that bar;
  * the risk checks (state, blockers, conflicts);
  * the decision and the reason it was not traded.
* The API `/decision/log` joins each row with the trader's signal and order for the same
  pair and bar, to show the execution result. Dashboard page: Decision log.
* The table is append-only: a trigger refuses updates, and deletion of rows younger than
  180 days. Older rows are pruned daily (about 2.5 KB per row, under 25 MB at most).

## Order types (Phase F1)

* Limit (GTC / IOC), post-only (Binance `LIMIT_MAKER`), and stop-limit (Binance
  `STOP_LOSS_LIMIT`), in the order model, the Binance adapter and the paper simulator.
* Paper mirrors Binance's refusals:
  * a post-only order that would take liquidity is refused;
  * a stop that would trigger immediately is refused.
* A stop-limit's trigger state is stored on the order row, so it survives restarts.
* The trader still exits with its own software stops and IOC orders.
* Exchange-side protective stops and OCO orders are F2. They need a demo-key run and an
  order-list design, because the database allows one active order per pair and side.

## Backtesting realism (Phase G1)

* Slippage can follow `SlippageModel`: half the spread plus square-root market impact on
  trailing volatility and volume. Inputs come only from data before the fill.
* Reports include recovery factor, average R, slippage, funding (0 for spot), and results
  per coin, side, hour and weekday. Walk-forward out-of-sample trades are broken down by
  regime, coin, 4-hour block, weekday and exit.
* `nexus_backtest.stability`: parameter-stability diagnostics (training-vs-unseen rank
  correlation, probability of backtest overfitting, how often each choice is made,
  neighbour drop).

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
