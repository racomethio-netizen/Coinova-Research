# Pre-registration: strategy modules A-G (deep-upgrade brief, Phase 4)

Committed BEFORE any of these strategies is run on market data (they were only run on
synthetic random-walk data in unit tests). Results are appended below once, unedited. No
rule, grid, gate or data period may change after the run; a changed idea is a new
hypothesis that needs new, unseen data.

## Data

* **Primary (decides pass/fail): 2022-11-14 to 2024-11-13**, Binance SPOT BTCUSDT and
  ETHUSDT, 5m/15m/1h/4h klines from Binance's public archive (data.binance.vision). This
  period has never been used for any NEXUS research or parameter choice.
* **Secondary (reported, does not decide): 2024-11-14 to 2026-11-14**, the research
  database already used for 11 earlier strategies. Because it has been mined, a result
  there is weak evidence either way.

## Common protocol (identical to the earlier studies)

* Long only (spot). Costs: fee 10 bps + slippage 5 bps per side (platform `CostModel`).
  Entry at the next bar's open; stops/targets as defined per strategy; stop has priority
  when both are touched in the same 5m bar.
* Walk-forward: train 180 days, test 60 days, rolling; parameters chosen on TRAIN only
  (highest train expectancy with at least 15 train trades); stitched out-of-sample (OOS).
* Gates, all required (`walkforward.Gates`): OOS trades >= 30, expectancy > 0, profit
  factor >= 1.15, positive test windows >= 55%, t-statistic >= 1.0.
* Per-regime breakdown reported. A strategy that passes may only trade regimes that pass
  the same gates on their own.
* Multiple testing: 7 strategies here, 18 in total on BTC/ETH. Even a pass only qualifies
  a strategy for a separately pre-registered forward paper test on NEW data. No strategy
  goes to real money from a backtest.

## Strategies (code: `services/strategies/nexus_strategies/library_v3.py`)

| | Code | Base / confirm | Rule summary | Grid |
|---|---|---|---|---|
| A | trend_supertrend | 1h / 4h | 4h close > EMA50 and EMA20 > EMA50; 1h EMA9 > EMA21 > EMA50; ADX >= adx_min; Supertrend up; previous low touched EMA21, close back above, bullish; RSI 50-70; MACD hist > 0. ATR stop/target. Exit: Supertrend flips down | adx_min {18, 25}, stop_atr {1.5, 2.5}, target_atr {3, 5}, use_exit {0, 1} |
| B | breakout_retest | 1h / 4h | Donchian(channel) breakout after a consolidation (range narrow in ATRs with low ADX within 10 bars before), relative volume >= rv_min, ATR rising; entry on the first retest within 5 bars that closes above the level, no failed-breakout close in between; 4h close > EMA50. Stop below retest low. Exit: close < EMA20 | channel {20, 40}, rv_min {1.2, 2.0}, stop_atr {0.5, 1.0}, target_atr {3, 5}, use_exit {0, 1} |
| C | momentum_rv | 15m / 1h | Regime trend_up; RSI crosses above rsi_cross; MACD hist > 0 and rising; ROC(12) > 0; relative volume >= rv_min; 1h EMA20 > EMA50, 1h RSI > 50. Exit: RSI < 45 or hist < 0 | rsi_cross {55, 60}, rv_min {1.5, 2.5}, stop_atr {1.5, 2.5}, target_atr {3, 4.5}, use_exit {0, 1} |
| D | mean_reversion_vwap | 15m / 1h | Previous close below lower Bollinger(20, 2); RSI < rsi_max; VWAP-deviation z-score (previous 96 bars) <= -z_min; bullish close; 1h ADX < h_adx_max and 15m ADX < 25 (no strong trends). Target: session VWAP. Exit: close >= Bollinger mid | rsi_max {25, 30}, z_min {1.5, 2.5}, h_adx_max {20, 28}, stop_atr {1.5, 2.5}, use_exit {0, 1} |
| E | vwap_trend | 15m / 1h | >= 4 of previous 6 closes above session VWAP; low within touch_atr ATR of VWAP; close above VWAP and open; relative volume >= 1; RSI > 50; 1h EMA20 > EMA50; no entries 00:00-02:00 UTC. Exit: close < VWAP | touch_atr {0.2, 0.5}, stop_atr {1, 2}, target_atr {2.5, 4}, use_exit {0, 1} |
| F | sweep_reclaim | 15m / 1h | Sweep of the last confirmed swing low with a close back above it; lower wick >= wick_min of range; relative volume >= rv_min; bullish; 1h EMA20 >= EMA50; optionally the swept level is an equal-lows pool. Stop below the sweep wick. Exit: close below the previous bar's low | wick_min {0.4, 0.6}, rv_min {1.0, 1.5}, require_equal {0, 1}, stop_atr {0.2, 0.5}, target_atr {2, 3} |
| G | market_structure | 1h / 4h | BOS up (mode 0) or BOS/CHoCH up (mode 1) from confirmed swings; entry on a retest of the broken swing high within `window` bars that closes above it; structure trend up; 4h EMA20 > EMA50. Stop: last swing low - 0.2 ATR (skipped if > 3 ATR away). Target: entry + rr x risk. Exit: CHoCH down | mode {0, 1}, window {5, 10}, rr {2, 3}, use_exit {0, 1} |

Common to all: no entry in the `high_vol` regime; a target must be at least 3x the
round-trip cost away (`Strategy.compute`).

## Not built, stated

* Short versions of A-G: spot is long-only and futures are disabled.
* "Mean reversion probability" (D): needs a model (Phase D); not approximated by a rule.

## Results

Appended below after the single run.
