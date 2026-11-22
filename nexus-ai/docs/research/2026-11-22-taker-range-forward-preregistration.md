# Pre-registration: forward paper test of taker flow in range (sideways) markets

Committed BEFORE the test starts. Nothing below changes during the test. Interim results
are shown, but the decision is made once, when the stopping rule below is reached.

## Why this test

In the Phase 6 run (docs/research/2026-11-22-phase6-preregistration.md) the taker_flow
strategy failed overall (-0.24% per trade), but its range-regime slice was positive
(+0.22% per trade, 45 trades). With 15 regime slices tested, one positive slice is about
what chance alone produces, so this is a NEW hypothesis that can only be judged on data
nobody has seen: "taker-flow continuation, traded only in range regimes, has a positive
edge after costs."

## Rules (frozen)

* Code: `nexus_strategies.library_v2.TakerFlow` and `nexus_backtest.signals.study_signals`,
  exactly as used in the Phase 6 run; no changes.
* Parameters (the most recent walk-forward window's selection, fixed): ratio_min 0.55,
  stop_atr 2.5, target_atr 3.0, use_exit 0. Max hold 48 hours.
* Data, hourly: Binance SPOT klines (5m execution, 1h base, 4h confirmation) from the public
  market-data API; perpetual taker-buy share from Binance USD-M 1h klines (taker buy
  volume / volume). BTCUSDT and ETHUSDT.
* Only signals whose regime is `range` count. Signals must close after the start time.
* Costs as in research: fee 0.10% and slippage 0.05% per side. Entry at the next 5m open
  after the signal bar closes; exit at stop (priority), target, or 48 h, as in
  `study_signals`. Paper only: nothing is executed.
* A trade is recorded once, when it has completed. Data failures are recorded as gaps;
  nothing is estimated.

## Stopping rule and decision

* Evaluate once, when 30 completed range trades exist (expected after roughly 10 to 12
  months at the historical rate of about 2.8 per month across both symbols), or after
  18 months, whichever comes first. Fewer than 30 trades at 18 months = INCONCLUSIVE.
* PASS only if all hold: mean net return per trade > 0; profit factor >= 1.15; t-statistic
  >= 1.0.
* A PASS means "may be considered for the regular paper trader"; it is not a promise of
  future returns. A FAIL ends this idea.
