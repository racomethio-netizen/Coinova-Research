# Signal engine: design specification

This is the contract for Phases 3–6. Parameter values marked *(default)* are
starting points stored in versioned config. They are **not** claims about
performance. Any performance number comes only from the validation pipeline
in §6 and is shown with its sample size, period and assumptions.

## 1. Market data

### Sources
- **Primary:** Binance public market-data REST (`GET /api/v3/klines`,
  `GET /api/v3/ticker/price`), keyless. Bulk history comes from the published monthly
  kline archives (with checksums) for backfill.
- **Secondary (cross-check / failover):** one additional venue with a public
  USDT-quoted kline endpoint, chosen in Phase 3 after a terms-of-use review.
- **Before any production use:** review each source's API terms for commercial
  display and redistribution, and check geographic restrictions on the hosting
  region (COMPLIANCE.md, G-4). Pick a hosting region where the endpoint is permitted.
- **Never:** API keys, signed requests, account endpoints (ADR-004).

### Pairs and timeframes
- Initial pairs: `BTC/USDT`, `ETH/USDT`. More pairs are added in `market_pairs`
  (admin-managed, audited) and are validated against the source's symbol list.
- Timeframes: `15m`, `1h`, `4h`, `1d`, each fetched natively from the source.
  Aggregating 1h into 4h/1d is used only as a validation cross-check.

### Candle record
`(pair_id, timeframe, open_time)` is the primary key, which prevents duplicates
because writes are upserts. Fields: OHLC, base volume, quote volume, trade count,
`close_time`, `is_final`, `source`, `ingested_at`, `ingest_run_id`. All times are UTC.

### Validation (reject + log, never "repair" by guessing)
- `low ≤ min(open, close) ≤ max(open, close) ≤ high`; all prices > 0; volumes ≥ 0.
- `open_time` aligned to the timeframe boundary; `close_time = open_time + tf − 1ms`.
- Not in the future. Only `is_final` candles (closed) are used by signals.
- Revisions: if a stored final candle differs from a re-fetch, keep the original,
  record a `candle_revisions` row and raise an alert. Signals already published
  keep their inputs (immutability).

### Gap handling
After each ingest, scan the expected sequence. Missing intervals become `candle_gaps`
rows, followed by bounded backfill attempts. If an evaluation's lookback window
contains an unresolved gap, the evaluation result is **HOLD** with reason
`data_quality_gap`. Gaps are never filled with interpolated prices.

### Rate limits and retries
- Token-bucket limiter per source, set below the documented limit. Honor `Retry-After`
  and the source's weight headers.
- Exponential backoff with jitter (max 5 attempts) on 429/5xx/timeouts. No retry on 4xx validation errors.
- On 418/451 or ban signals, stop the source and alert the admin. **Do not rotate
  IPs or evade blocks.**

## 2. Indicators (in-house, see ADR-003)

| Indicator | Definition |
|---|---|
| EMA 20/50/200 | α = 2/(n+1), seeded with SMA(n); undefined for the first n−1 bars |
| RSI 14 | Wilder smoothing of gains/losses |
| MACD | EMA12 − EMA26, signal EMA9, histogram |
| ATR 14 | Wilder smoothing of true range |
| Bollinger | SMA20 ± 2σ (population σ), %B, bandwidth |
| Volume | volume / SMA20(volume), OBV slope |
| Support/resistance | confirmed swing highs/lows (fractal, k bars each side, available only k bars **after** the pivot), clustered within 0.5·ATR, ranked by touches |

Undefined values are `NaN`, which makes the strategy return HOLD. Values are never
backfilled from the future. Support/resistance pivots carry their **confirmation
time**, so they cannot be used before they are knowable.

## 3. Signal generation

### Pipeline per (pair, timeframe, bar close t)
1. Load final candles with `close_time ≤ t` (lookback ≥ 300 bars).
2. Run data-quality gates (gaps, staleness, revision flags). Failure → HOLD.
3. Compute features from data ≤ t only.
4. **Multi-timeframe confirmation:** use the next higher timeframe
   (15m→1h, 1h→4h, 4h→1d, 1d→1d-only rules), using **only higher-timeframe candles closed at or
   before t**.
5. The **strategy** proposes a direction and levels (e.g. `trend_pullback_v1`:
   higher-timeframe trend up (EMA50 > EMA200), pullback toward EMA20/support, RSI recovering, MACD
   histogram turning positive, volume confirmation).
6. The **model** (if the strategy has an approved model) estimates a calibrated
   probability `p` that TP1 is reached before SL within the horizon.
7. **Risk gates** (§4) decide whether to publish. Failure → HOLD with reason codes.
8. Persist: always a `signal_evaluations` row. If published, a `signals` row
   (insert-only, hash-chained) plus an explanation.

### Decision rule: when uncertain, HOLD
Publish BUY/SELL only if **all** of the following hold:
- The strategy conditions are met.
- `p ≥ p_min` *(default 0.55)*.
- Expected value net of fees and slippage > 0:
  `p·reward_net − (1−p)·risk_net > 0`.
- R:R to TP1 ≥ `rr_min` *(default 1.5)*.
- The volatility regime is within bounds.
- There is no open signal for the same pair/timeframe/direction.

There are no quotas and no minimum signal counts. A day with zero signals is a valid outcome.

### Signal fields
| Field | Notes |
|---|---|
| `id` | UUID v7 (time-ordered) |
| `pair_id` / asset, `timeframe` | |
| `as_of` | close time of the decision candle |
| `published_at` | DB time of insert (must be ≥ `as_of`; the lag is stored) |
| `direction` | `BUY` or `SELL` (HOLD lives in evaluations) |
| `entry_low`, `entry_high` | *(default)* BUY: close − 0.25·ATR … close + 0.10·ATR (mirrored for SELL) |
| `stop_loss` | beyond the nearest confirmed support/resistance or 1.5·ATR, whichever is farther |
| `take_profits` | TP1, TP2 at R-multiples or nearest opposing level (≤ 3 targets) |
| `risk_reward` | to TP1 from entry midpoint, **net** of assumed fees and slippage |
| `confidence` | calibrated `p` + band (Low/Medium/High) + calibration reference; `null` = "not calibrated" |
| `model_version_id`, `strategy_version` | |
| `explanation` | structured JSON (§5) |
| `expires_at` | *(default)* as_of + 6 bars for entry; position horizon 24 bars |
| `status` | derived from events: `pending → active → closed` / `expired` |
| `final outcome` | from `signal_outcomes`: `win`, `loss`, `breakeven`, `expired_no_entry`, `expired_closed` (marked to market at horizon), with net R and net % |
| `prev_hash`, `content_hash` | tamper evidence (ADR-005) |

Confidence is always displayed as *"model estimate, calibrated on out-of-sample
data from … to …, n = …; not a guarantee"*. It is never shown as "chance to win".

## 4. Risk management engine

| Level | Rule *(defaults, configurable, versioned)* |
|---|---|
| Signal | R:R ≥ 1.5 net; stop distance between 0.5 and 4 ATR; no publish if spread/volatility regime is extreme |
| Position sizing (backtest + paper) | fixed-fractional: risk 1% of equity per trade, `qty = equity·risk / |entry − stop|`, capped at 25% of equity notional |
| Portfolio | max 3 concurrent positions; max 1 per pair; correlated BTC/ETH exposure counted together |
| Circuit breakers | daily loss ≥ 3% → no new entries that day; drawdown ≥ 15% → pause and require user reset (paper) |
| Shorts | backtests may simulate SELL as short with an explicit borrow/funding cost assumption. Paper trading v1 is **long-only spot**; SELL closes or avoids longs (decision D4) |

## 5. Explanation engine

- **Deterministic and stored with the signal:** reason codes with the exact values used,
  for example `htf_trend_up(EMA50_4h=…>EMA200_4h=…)`, `rsi_recovery(RSI14: 33.8→41.2)`,
  `macd_hist_turned_positive`, `volume_confirm(1.8× avg)`,
  `nearest_resistance(64 210, 2.1 ATR)`, plus risks and invalidation (`close below stop`).
- **Model contributions:** XGBoost native SHAP values (`pred_contribs=True`), top 5 features.
- **Optional plain-language text:** existing Anthropic integration, given only the
  structured facts, labeled AI-generated. It can never change direction, levels or confidence.

## 6. Model validation pipeline

### Labels
Triple-barrier: for a candidate at bar t, the label is 1 if TP1 is hit before SL within H
bars, and 0 if SL is hit first. Timeouts are labeled by the net return sign, with the share
reported. Labels use bars t+1…t+H.

### Leakage controls
- Features use only data ≤ t (enforced by the **truncation-invariance test** below).
- Scalers, encoders and calibrators are fit inside the training fold only (sklearn `Pipeline`).
- **Purging:** drop training samples whose label window [t, t+H] overlaps the test fold.
  **Embargo:** H bars after each test fold.
- Higher-timeframe features use only closed higher-timeframe candles.
- Hyperparameters are tuned within the training folds (nested). The **lockbox**
  (most recent 6 months) is evaluated once, at approval time.

### Walk-forward
Expanding window. Train on [start, T_k), calibrate on the last 20% of the training
window (chronologically), then test on [T_k, T_k + step). Each fold records its date
ranges, n, metrics and calibration curve.

### Calibration
Isotonic regression if the calibration sample is ≥ 1 000; otherwise Platt (sigmoid).
Reported with reliability diagrams, Brier score and expected calibration error
on out-of-sample data.

### Baselines (every report)
- No-trade.
- Buy-and-hold.
- Random entry with the same SL/TP and frequency (Monte Carlo, 1 000 runs).
- Strategy rules without the model.

A model is approvable only if it beats the rules-only baseline out-of-sample after costs.

### Metrics (definitions fixed in code and shown in the UI)
Win rate, net return, profit factor, max drawdown, Sharpe (period and
annualization stated, risk-free 0), average win, average loss, expectancy in R,
exposure, and number of trades. Each is broken down by asset and timeframe, with 95%
bootstrap confidence intervals.

**Minimum sample:** an aggregate with fewer than 100 closed trades is shown as
"insufficient sample", with no percentages headlined. Every experiment (including failed
ones) is recorded, and reports state how many configurations were tried.

### Reproducibility record
For each experiment, record: git SHA, engine version, `uv.lock` hash, dataset hash
(pair, timeframe, range, content hash of candles), config JSON, seeds, metrics and
artifact SHA-256. Re-running the same record must reproduce the metrics exactly
(this is a CI test on a small fixture).

### Model lifecycle
`draft → shadow → approved → retired`. Approval requires an admin with MFA, a
validation report on file, a lockbox result, and an audit-log entry. Approved versions
are immutable.

## 7. Backtesting engine

- Bar-by-bar event loop over final candles. Decide at close; fill at the next bar's open
  ± slippage (ADR-006).
- Stops and targets are checked intrabar with OHLC. If both hit in the same bar,
  SL is assumed first. If a bar gaps through the stop, the fill is at the open.
- Fees *(default)* 0.10% per side (taker). Slippage *(default)* 0.05% per side for
  BTC/ETH, configurable per pair. Short borrow/funding cost is an explicit assumption.
- Position sizing and portfolio rules come from §4. The equity curve is per bar, plus
  the drawdown series.
- Strategy comparison runs on an identical dataset hash and assumptions. Results
  show the full **assumptions block**.
- User-defined ("custom") strategies are **parameter sets of built-in strategies**
  validated against a JSON schema. **Arbitrary user code is never executed.**

## 8. Signal performance tracking

- Outcome resolver: the same execution module as the backtester, applied to candles
  after `published_at` (not `as_of`, so publication lag is honest).
- The performance dashboard aggregates `signal_outcomes` over **all** published
  signals, including losses, expirations and retractions (annotated, not excluded).
- Live (forward) results and backtest results are always shown **separately**
  and labeled.

## 9. Paper trading

- Virtual account per user *(default)* 10 000 USDT, clearly labeled "virtual".
  Resetting archives the account; history is kept.
- Orders: market and limit, with optional SL/TP. Market fills use the latest ticker
  price (rejected if older than 60 s) ± slippage + fee. Limit/SL/TP are monitored on
  each price update and 1m checks.
- Positions, realized and unrealized P/L (decimal), trade history and an equity curve.
- No connection to any exchange account. No transfers. No custody (COMPLIANCE.md).

## 10. Future automated execution (disabled)

- `ExecutionAdapter` interface with a single implementation, `DisabledExecutionAdapter`,
  which raises `LiveExecutionDisabled`.
- `LIVE_EXECUTION_ENABLED` must be `false`. The engine refuses to start if it is `true`
  until the activation checklist in COMPLIANCE.md §3 is complete and code for it exists.
- There is no schema for exchange credentials, no order/withdrawal endpoints, and CI checks
  that enforce this (SECURITY_RISKS.md, R-12).

## 11. Required automated tests (engine)

| Test | Proves |
|---|---|
| Indicator golden tests vs TA-Lib reference (test-only dep) | formulas correct |
| **Truncation invariance:** evaluation at t on data[:t] == evaluation at t on full data | no look-ahead |
| Purge/embargo unit tests on synthetic timelines | no label leakage |
| Backtest hand-computed scenarios (gap through stop, SL+TP same bar, fees) | execution correctness |
| Hash-chain verification + DB trigger tests (UPDATE/DELETE rejected) | immutability |
| Candle validation property tests (Hypothesis) | data integrity |
| Reproducibility run on a fixture | deterministic experiments |
| Static check: no signing/order/withdraw code paths | execution stays disabled |
