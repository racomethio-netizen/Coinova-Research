# Pre-registration: adaptive grid backtest (spot and USD-M futures)

_Committed before any run. Results are appended once below the line; nothing above it
changes afterwards._

## What is tested

The adaptive grid engine exactly as it runs in paper trading:
* `nexus_strategies.adaptive_grid` handles design, arming, fills, stops and rebuilds;
* `nexus_strategies.grid_regime` decides when a grid may run and in which direction.

The backtester only replaces the live order book with historical bars.

## Data

* **Spot.** The 10 Binance spot pairs of the Freqtrade test: BTC, ETH, BNB, SOL, XRP,
  ADA, DOGE, LINK, AVAX and LTC against USDT. Official 5m klines, checksums verified,
  aggregated to 15m bars for the fill simulation and to 1h bars for the regime.
* **Futures.** BTCUSDT and ETHUSDT perpetuals. Official 15m klines plus actual
  8-hourly funding rates from data.binance.vision, checksums verified where published.
* **Window.** 2024-01-01 to 2026-10-31. 2023-06 to 2023-12 is warm-up only (regime
  indicators). A missing month aborts the run.

## Simulation (no look-ahead)

* **Regime.** The label of the last CLOSED 1h bar applies to the next 15m bars.
* **Path inside each 15m bar:**
  * open → low → high → close when the bar closed up;
  * open → high → low → close when it closed down.
  * Each point is a book snapshot: bid = price, ask = price + 1 bp.
* **Fills.** Trade-through only (strictly beyond the level). Partial fills are capped at
  25% of the bar's volume per path point. Orders armed at a bar's close can fill only
  from the next bar (execution delay).
* **Fees.**
  * Spot: 0.1% maker and taker, 0.05% stop slippage.
  * Futures: 0.02% maker, 0.05% taker, 0.05% slippage, plus actual funding on the net
    position at each funding time.
* **Allocation.** 1,000 USDT per spot symbol and 2,000 USDT per futures symbol
  (Binance's 100 USDT futures minimum needs a larger slot). Total equity for the 0.25%
  per-order risk cap = the sum of all allocations.
* **Stop-loss.** In live paper trading a stopped grid waits for the owner. The backtest
  has no owner, so a stopped symbol stays out for 7 days, then may start again (stated
  assumption).
* **Not simulated here** (covered by unit tests instead):
  * exchange disconnections;
  * restarts;
  * portfolio-level daily and weekly halts across symbols;
  * correlation limits.

## Parameters and walk-forward

* **Grid.** `spacing_atr` ∈ {0.4, 0.6, 0.9} × `range_atr` ∈ {6, 8, 12}: 9 sets. Every
  other parameter is the engine default.
* **Walk-forward:**
  * each set runs continuously over the window;
  * for each test quarter (2024-Q3 to 2026-Q4), the set with the best pooled median net
    return over the previous two quarters is selected;
  * its results in that quarter are stitched into the out-of-sample (OOS) curve;
  * 2024-Q1 and 2024-Q2 are training only.
* **Also reported:**
  * the default set (0.6 / 8) over the whole window, with no selection;
  * all 9 sets over the whole window (parameter sensitivity);
  * the default set at 2× fees (cost stress).

## Gates (on the walk-forward OOS result, all symbols pooled, equal weight)

1. Net return after all costs > 0.
2. Profit factor of closed grid trips ≥ 1.1.
3. Maximum drawdown of the pooled equity ≤ 15%.
4. Positive in at least 2 of 3 years (2024 H2, 2025, 2026 YTD).
5. Still > 0 at 2× fees (default set).
6. Not one symbol: still > 0 without the best symbol.

**Reported, not gated:**
* win rate and average trade duration;
* fees, funding and exposure;
* daily Sharpe and Sortino where more than 90 trading days;
* results by symbol, by regime at the time of exit, and by market condition (bull, bear,
  sideways, high volatility, crash days, using `market_conditions.label_days` on BTC);
* benchmarks: holding each coin, and cash.

## Consequence

The system is paper-only either way.
* **Pass.** The paper run continues as the forward test.
* **Fail.** The result is recorded and shown, the parameters are not re-tuned, and the
  paper run continues so the live-data behaviour can be compared.

Nothing in this test can enable live trading.

---

## Results

_(appended after the single run)_
