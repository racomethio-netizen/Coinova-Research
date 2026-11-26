# Pre-registration: grid v2, volatility-targeted holding, BTC/ETH pairs

_Committed before any run. Results are appended once below the line; nothing above it
changes afterwards._

Three tests the owner asked for ("Test next three"). Costs and gates are fixed here.
Nothing is tuned after the run. A strategy goes to paper only if it passes; live only
with the owner's approval.

## 1. Grid v2: close the inventory when the regime breaks

**Hypothesis.** It comes from the adaptive-grid backtest
(docs/research/2026-11-26-adaptive-grid-preregistration.md).
* There the grid earned in SIDEWAYS and WEAK_BULL regimes and lost when the regime turned
  while it held coins.
* Rule change: when the regime leaves the grid's allowed set, close all held lots at
  once (taker fee plus slippage), instead of only suspending new entries.
* A new grid may start as soon as the regime allows again.
* Everything else is unchanged: the engine, the regime and the default parameters
  (spacing 0.6 × ATR, range 8 × ATR), with no tuning.

**Why this data.** The hypothesis was formed on 2024–2026 data, so testing it there would
be in-sample. It is tested on **2020-07-01 to 2023-12-31**, which no grid test has used:
* spot 15m klines from data.binance.vision, checksums verified;
* 2020-01 to 2020-06 is warm-up;
* SOL and AVAX start after their listing (August and September 2020).

**Setup.** Spot, 10 coins, 1,000 USDT each, as in the adaptive-grid backtest:
* 0.1% fees, 0.05% stop slippage;
* trade-through fills on the 15m OHLC path;
* a 7-day pause after a stop-loss;
* v1 (the original rule) is run on the same data for comparison.

**Gates** (v2, all 10 coins pooled):
1. Net return after costs > 0.
2. Profit factor ≥ 1.1.
3. Maximum drawdown ≤ 15%.
4. Positive in at least 2 of the 4 periods (2020 H2, 2021, 2022, 2023).
5. Still > 0 at 2× fees.
6. Still > 0 without the best coin.
7. Better than v1 on the same data.

## 2. Volatility-targeted holding (Moreira & Muir 2017, "Volatility-Managed Portfolios")

**Rule.**
* Hold BTC, ETH, or a 50/50 mix, with weight = min(1, 40% / realised volatility). The
  volatility is annualised from the last 30 daily returns, using closes up to the
  previous day.
* The rest is cash (USDT).
* The weight is changed only when it moves by more than 10 points, at the daily close.

**Data.** 2020-07-01 to 2026-10-31, daily closes from verified Binance klines.

**Costs.** 0.15% per side on traded notional; stress at 2×.

**Comparison.** The same asset held at 100%.

**Gates** (per asset: BTC, ETH, 50/50):
1. Sharpe higher than holding over the full window.
2. Maximum drawdown at least 10 points smaller than holding's.
3. Net return > 0.
4. Sharpe higher than holding in BOTH halves (2020-07 to 2023-06 and 2023-07 to
   2026-10).
5. Gates 1 and 3 still hold at 2× costs.

**Reported, not gated:** 30% and 50% volatility targets.

## 3. BTC/ETH pairs trade (statistical arbitrage) on perpetual futures

**Rule** (`nexus_strategies.stat_arb`, unchanged defaults):
* Hourly closes. Every 168 hours, refit Engle-Granger on the previous 720 hours.
* Trade only when cointegrated at 5%, with a half-life of 1–30 bars and the expected
  reversion above 2× round-trip costs.
* Enter at |z| ≥ 2, exit at |z| ≤ 0.5, stop at |z| ≥ 4 (60-bar z-score window).

**Positions and costs.**
* Long the cheap leg, short the rich leg; beta-weighted; 1x gross on 10,000 USDT.
* 0.05% taker fee + 0.05% slippage per leg per side.
* Actual funding on both legs at every funding time.

**Data.** Binance USD-M perpetuals, 15m klines and funding (verified), 2024-01-01 to
2026-10-31. 2023-06 to 2023-12 is formation and warm-up.

**Gates:**
1. Net return > 0 after costs and funding.
2. Annualised Sharpe ≥ 0.5.
3. Maximum drawdown ≤ 15%.
4. Mean daily return > 0 with a stationary-bootstrap p-value < 0.05 (block 10, 5,000
   resamples).
5. Positive in at least 2 of 3 years.
6. Still > 0 at 2× costs.

**Note.** Even on passing, live trading would need futures, which NEXUS keeps disabled.
A pass means a paper test first.

---

## Results

_(appended after the single run)_
