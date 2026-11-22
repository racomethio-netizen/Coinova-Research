# Pre-registration: Phase 6 strategy library (5 new strategies)

Committed BEFORE any of these strategies is run on data. Results are appended below once,
unedited. No rule, grid or gate may change after the run; a changed idea is a NEW
hypothesis that needs new, unseen data.

## Common protocol (identical to the existing six strategies)

* Data: Binance SPOT candles BTC/USDT and ETH/USDT, 2024-11-14 to 2026-11-14 (local
  research database, same data as the 2026-11-21 validation). D and E also use Binance
  USD-M perpetual data from data.binance.vision (funding history; 5m klines with taker
  buy volume, aggregated to 1h); spot prices are what is traded.
* Long only (spot). Fees and slippage: the platform's paper cost model (`CostModel`,
  fee 10 bps + slippage 5 bps per side). Entries on the next bar; stops/targets from ATR.
* Walk-forward: train 180 days, test 60 days, rolling; parameters chosen on TRAIN only
  (highest train expectancy, minimum train trades), stitched out-of-sample (OOS) results.
* Gates (all must pass, unchanged `Gates`): OOS trades >= 30, expectancy > 0, profit factor
  >= 1.15, positive windows >= 55%, t-stat >= 1.0. Per-regime breakdown reported; a strategy
  may only trade regimes that pass the same gates.
* Multiple testing: 11 strategies tested in total on the same two years. Even a pass is
  weak evidence; it would go to paper trading on NEW data first, never straight to money.

## Strategies

**A. vwap_reclaim** (15m, confirm 1h). UTC-day anchored VWAP. Entry: the previous
`below_bars` closes were all below VWAP, this bar closes above VWAP and above its open,
15m volume z-score >= `vol_z_min`, 1h close > 1h EMA50. Stop close - stop_atr x ATR,
target close + target_atr x ATR. Exit: close below VWAP. Grid: below_bars {4, 8},
vol_z_min {0.5, 1.5}, stop_atr {1.5, 2.5}, target_atr {3, 4.5}, use_exit {0, 1}.

**B. volatility_expansion** (1h, confirm 4h). Bollinger(20, 2) bandwidth percentile over
200 bars was <= `squeeze_pct` in at least one of the last 5 bars; this bar closes above the
upper band; ATR(14) above its value 3 bars ago; 4h EMA20 > EMA50. Exit: close below the
middle band. Grid: squeeze_pct {0.1, 0.2}, stop_atr {1.5, 2.5}, target_atr {3, 5}, use_exit {0, 1}.

**C. mtf_confluence** (1h, confirm 4h). 4h close > EMA20 > EMA50 and 4h RSI between 50
and `h_rsi_max`; 1h close > EMA20 > EMA50, 1h MACD histogram crosses above 0, 1h RSI < 70.
Exit: MACD histogram < 0. Grid: h_rsi_max {65, 75}, stop_atr {1.5, 2.5},
target_atr {3, 4.5}, use_exit {0, 1}.

**D. funding_squeeze** (1h). Mean perpetual funding of the last 24 h (3 settlements, only
settlements before the bar) <= `funding_max` (shorts crowded), and the 1h close crosses
above EMA20. Exit: close below EMA50. Grid: funding_max {-0.00005, -0.0001},
stop_atr {1.5, 2.5}, target_atr {3, 4.5}, use_exit {0, 1}.

**E. taker_flow** (1h; order-flow imbalance proxy). Perpetual taker-buy share of volume,
3-hour mean >= `ratio_min`, 1h close > EMA20, 1h volume z-score >= 1. Exit: close below
EMA20. Grid: ratio_min {0.55, 0.60}, stop_atr {1.5, 2.5}, target_atr {3, 4.5}, use_exit {0, 1}.

## Not testable now (stated, not faked)

* **Order-book imbalance**: no historical order-book data; E (taker flow) is the testable
  order-flow proxy. Live book imbalance is already used as a confirmation in the decision score.
* **Liquidation events**: data.binance.vision returns 404 for liquidation snapshots
  (checked 2023-06-01, 2024-06-01, 2025-06-01). NEXUS collects live liquidations since
  2026-11-22 (24 h retention); a test needs months of stored history first.
* **Statistical strategies**: none proposed until a hypothesis exists with its own data.

## Results

(appended after the single run)
