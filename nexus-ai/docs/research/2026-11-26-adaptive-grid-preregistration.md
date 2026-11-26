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

_Single run on 2026-11-26 after the pre-registration commit cf01860.
* Before the batch, one smoke run of the default set on BTC spot timed the harness. Its
  result (−20.7%) is part of this same specification, and no parameter or rule was
  changed after it.
* Report: `research/backtesting/nexus_backtest/results/adaptive_grid_2026-11.json`.
* Runner: `python -m nexus_backtest.adaptive_grid_bt`, then `report_main`._

**FAILED: 0 of 6 gates pass. The parameters are not re-tuned. The paper system stays
paper-only and continues as a forward test.**

### Walk-forward out-of-sample (2024-07 to 2026-10, 12 markets pooled, 14,000 USDT)

| Measure | Result |
|---|---|
| Net return after costs | **−24.6%** (14,000 → 10,550 USDT) |
| Maximum drawdown | −24.7% |
| Profit factor of closed trips | 0.65 |
| Win rate | 89.4% (25,597 trips, 2,712 of them stop-out exits) |
| Average trip duration | 20.4 h |
| Periods | 2024 H2 −6.1%, 2025 −15.3%, 2026 YTD −5.3% |
| Sharpe / Sortino (daily) | −2.27 / −1.65 |
| At 2× fees (default set) | −43.8% |
| Without the best market (BTC futures) | −28.5% |

### Out-of-sample result by market

* Futures:
  * BTC −1.2% (the best);
  * ETH −13.6%.
* Spot:
  * BTC −14.6%;
  * ETH −17.8%;
  * BNB −25.0%;
  * LTC −26.9%;
  * LINK −28.8%;
  * AVAX −31.0%;
  * SOL −39.2%;
  * DOGE −42.0%;
  * ADA −44.2%;
  * XRP −45.1%.

### Parameter sensitivity (full window): every set loses

* Returns range from −27.6% (spacing 0.9 × ATR, range 12 × ATR) to −36.5% (0.4 / 6).
* Profit factor ranges from 0.57 to 0.67.
* Wider spacing loses less, because fewer trips pay fewer fees, but none comes near
  zero. The loss is structural, not a matter of settings.
* Default set (0.6 / 8): −32.5%, 36,942 trips, 2,175 USDT in fees, 3,584 stop-out exits.

### Where the money is made and lost (out of sample, by regime at exit)

| Regime at exit | Trips | P&L (USDT) | Stop exits |
|---|---|---|---|
| SIDEWAYS | 14,341 | **+2,734.8** | 296 |
| WEAK_BULL | 5,487 | **+767.3** | 100 |
| WEAK_BEAR | 2,803 | −2,226.9 | 936 |
| STRONG_BEAR | 865 | −2,358.8 | 721 |
| HIGH_VOLATILITY | 1,236 | −1,792.8 | 544 |
| EXTREME | 89 | −274.2 | 51 |
| LOW_LIQUIDITY | 351 | −62.3 | 45 |
| STRONG_BULL | 425 | −8.3 | 19 |

### What the run shows

1. **The grid does earn in the markets it is built for**: +3,502 USDT in sideways and
   weak-bull markets.
2. **It gives that back, and more, when the market turns.**
   * When a range breaks downward, the grid is suspended: no new buys. But it still
     holds the coins it bought on the way down.
   * Those lots ride the decline to the stop.
   * Exits in bearish or high-volatility regimes lost −6,653 USDT, almost all of it at
     stops.
   * This is the textbook grid failure: many small wins, a few large losses (89% win
     rate, profit factor 0.65).
3. **Settings do not fix it.** All 9 parameter sets lose, so it is not a tuning problem.
4. **Benchmarks over the same window:** BTC hold +83.5%; BNB +125.7%, XRP +103.4%;
   ADA −67.5%, AVAX −77.8%.

### Hypothesis for a NEW pre-registration (not tested here, not adopted)

* **Rule:** when the regime leaves the grid's allowed set, close the held inventory at
  the book immediately, instead of only suspending new entries.
* **Why:** the breakdown above suggests it would cut the regime-break losses.
* **Caution:** this hypothesis was formed by looking at this data, so testing it on the
  same data would be in-sample. It must be judged on data the system has not seen: the
  paper forward test from now on, or a fresh period.
