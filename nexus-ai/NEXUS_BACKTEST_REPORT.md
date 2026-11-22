# NEXUS backtest report

Every strategy NEXUS has evaluated, with the out-of-sample result. A backtest result is
never a promise of future returns; a pass only qualifies a strategy for a forward paper
test on new data. Details and full tables are in each pre-registration under
`docs/research/`.

## Method (all studies)

* Pre-registered rules, grids, data and gates, committed before the run; results appended
  once, unedited.
* Closed bars only, entries at the next bar's open, stop before target within a bar,
  fees 10 bps + slippage 5 bps per side (spot), funding and liquidation modelled for
  futures studies.
* Walk-forward: train 180 days / test 60 days, parameters chosen on train windows only,
  stitched out-of-sample results judged by fixed gates (>= 30 trades, expectancy > 0,
  profit factor >= 1.15, >= 55% positive windows, t-stat >= 1.0).
* Look-ahead protection is tested (indicator prefix-invariance, higher-timeframe alignment
  on closed bars, train trades must exit before the test window).

## Results

| Study | Strategies | Data | Out-of-sample result |
|---|---|---|---|
| Base library (2026-11-21) | trend_following, momentum, breakout, mean_reversion, pullback_support, liquidity_sweep_reversal | BTC/ETH spot 2024-11..2026-11 | all FAIL (expectancy -0.30% to -0.53% per trade; profit factor 0.31-0.57) |
| Daily time-series momentum (2026-11-20) | tsmom long/short perpetuals | BTC/ETH perpetuals | FAIL (Sharpe, positive windows, t-stat, drawdown) |
| Funding carry (2026-11-21) | spot long + perp short | BTC/ETH | FAIL (3.91%/yr < 4%; 43% positive quarters < 60%) |
| Phase 6 (2026-11-22) | vwap_reclaim, volatility_expansion, mtf_confluence, funding_squeeze, taker_flow | BTC/ETH 2024-11..2026-11 | all FAIL (-0.24% to -0.45% per trade) |
| Strategy modules A-G (2026-11-23) | trend_supertrend, breakout_retest, momentum_rv, mean_reversion_vwap, vwap_trend, sweep_reclaim, market_structure | **unseen** BTC/ETH 2022-11..2024-11 (primary); 2024-11..2026-11 (secondary) | all FAIL on the primary period (-0.08% to -0.30% per trade; D made no trades) |

Forward paper tests on new data (running, decided once at their stopping rule):

* Funding carry, always-on (started 2026-11-22, decision at day 90).
* Taker flow in range regimes (started 2026-11-22, decision at 30 trades or 18 months).

## What this means

* 20 strategies have been tested out of sample; none has a
  positive expectancy after costs that passes the gates. NEXUS therefore places no trades,
  in paper or live. That is the intended behaviour, not a malfunction.
* The round-trip cost (about 0.30% for a spot trade with taker fees and slippage) is larger
  than the gross edge of most short-horizon technical rules on BTC/ETH. Ideas that trade
  less often, use maker execution, or have a structural source of return (for example
  funding) are more promising than more indicator combinations.
* The next step that can change this is the machine-learning phase, judged by the same
  out-of-sample standard and by net trading performance, not by accuracy.
