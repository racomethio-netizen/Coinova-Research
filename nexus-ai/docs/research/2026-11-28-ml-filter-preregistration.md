# Pre-registration: machine-learning filter on strategy-module signals (unified brief, Phase 4)

Committed BEFORE any run of this filter on market data. The code was tested only on
synthetic data:
* no look-ahead in the features;
* training only on trades closed before each test year;
* the filter finds a real edge and finds nothing in noise.

The results are appended once, unedited. Nothing below changes after the run.

Owner request: the "PROJECT NEXUS" unified brief, section D (LightGBM, XGBoost, CatBoost,
probability calibration, model drift detection), then "Phase 4".

## Question

Can a machine-learning model, used only to **block** some of a strategy module's
signals, turn a module that lost money after costs (Phase 3) into one that passes the
same gates? This is meta-labelling: the strategy proposes, the model only vetoes.

## What is tested

* **Module 1, forex scalping momentum pullback:** 11,276 signals 2016-2026 (Phase 3).
* **Module 2, forex intraday trend breakout:** 4,447 signals.

Holm's correction runs over these 2 tests.

Not tested (too few signals to train before the first test year, or not a stream of
trades): forex swing (933 signals), crypto scalping (288), crypto breakout retest (94),
crypto swing trend (a portfolio rule).

## Data and costs

Exactly as Phase 3 (`docs/research/2026-11-27-strategy-modules-preregistration.md`):
* Exness Standard M5 bid bars with each bar's mean spread, 5 pairs;
* 0.1 pip slippage, swap at the 21:00 UTC rollover;
* the same rules and the same simulator (`nexus_strategies.modules.sim`).

Each signal is simulated on its own. Its label is 1 if its net result (in R, after all
costs) is above zero.

## Features (`nexus_ai.meta_filter`)

All values are at the signal bar's close, from that bar or earlier ones. Higher
timeframes contribute closed bars only. There are 20 features:

* **Signal timeframe:**
  * ATR14 as a share of price;
  * ADX14;
  * RSI14;
  * distance to EMA50 in ATR;
  * EMA20 slope over 5 bars in ATR;
  * bar range in ATR;
  * one-day realised volatility;
  * hour of day (sine and cosine);
  * day of week.
* **H1:** ADX14, RSI14, distance to EMA50 in ATR, and the ATR percentile over 720 hours.
* **FX day (D1):** distance to EMA50 in ATR, and ATR as a share of price.
* **The signal itself:**
  * side;
  * stop distance in ATR;
  * spread as a share of the risk;
  * pair code.

## Model

* The average probability of three classifiers, with fixed settings (no tuning) and
  seed 7:
  * LightGBM: 300 trees, learning rate 0.03, 15 leaves, at least 50 samples per leaf,
    80% row and column sampling;
  * XGBoost: 300 trees, learning rate 0.03, depth 3, 80% row and column sampling;
  * CatBoost: 300 iterations, learning rate 0.03, depth 4.
* **Calibration:** isotonic regression, fitted on the latest 20% of each training
  window (by entry time). Trades still open when that 20% starts are dropped from the
  fitting part.
* **Decision:** allow a signal only if its calibrated probability of a positive net
  result is **≥ 0.40**.
  * For a 2:1 bracket that loses about 1.05R and wins about 1.9R after costs, the
    break-even probability is about 0.36.

## Walk-forward

* Test years 2020 to 2026 (2026 to the end of October), one model per year.
* Each model is trained on all of the module's signals whose trades had **closed before
  1 January** of the test year.
* A year with fewer than 1,500 training trades is skipped and reported.
* The allowed signals of all test years then go through the Phase 3 book: one position
  per symbol, three at a time, 0.25% risk per trade.

## Gates (all required; the same as Phase 3, on 2020-01-01 to the data end)

1. At least 200 trades.
2. Mean net R > 0.
3. Significant after Holm's correction across the 2 tests (stationary bootstrap,
   one-sided, 5,000 simulations, block 10, seed 0).
4. Profit factor ≥ 1.15.
5. Mean net R > 0 in both halves of the period.
6. Mean net R > 0 at 2× costs (same trades).
7. Maximum drawdown ≤ 10% at 0.25% risk per trade.

Reported but not deciding:
* the unfiltered module over the same years;
* AUC, Brier skill and calibration error of the probabilities;
* each model's own AUC;
* per-year folds;
* feature drift (population stability index, PSI) between each year and its training
  data.

## What a pass allows

A pass allows only:
* the filter attached to its module's forward OBSERVATION record;
* after Phases 5 to 7, a separately pre-registered forward paper test.

It never allows real-money trading.

A fail is recorded. No filter is attached and no model is registered for use.
