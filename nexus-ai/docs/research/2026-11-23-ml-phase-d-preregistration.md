# Pre-registration: Phase D machine-learning models

Committed BEFORE any of these models is trained on market data (they were only run on
synthetic data in unit tests). Results are appended once, unedited. Nothing below may
change after the run.

## Question

Can a calibrated model, chosen and trained only on past data, pick BTC/ETH spot long
entries whose net expectancy after costs is positive out of sample, reliably enough to
pass fixed gates? Accuracy alone does not count: the model must make money after costs.

## Data

* Primary (decides): 2022-11-14 to 2024-11-13, Binance spot BTCUSDT/ETHUSDT 15m/1h/4h
  (public archive, checksums verified, never used to build or tune any NEXUS model).
* Secondary (reported only): 2024-11-14 to 2026-11-14 (used by earlier strategy studies).

## Target and costs

* Triple barrier on 15m bars, long entered at the next bar's open: target 2 x ATR(14),
  stop 1 x ATR(14), time barrier 48 bars (12 h); stop counts first when both are touched
  in one bar. Fee 10 bps + slippage 5 bps per side included in every return.
* Secondary tasks, scored for skill only (never traded): 3-class direction over 16 bars
  with a neutral band equal to the round-trip cost; volatility expansion over the next
  48 bars (> 1.2x the trailing 96-bar volatility).

## Features

* f2 (the hypothesis, 83 columns at registration): f1 (returns, RSI, ATR, ADX, EMA
  distances, Bollinger, MACD, volume z, realized volatility and its percentile, range
  position, rule regime, time of day/week, 1h and 4h blocks) + Phase C indicators on 15m and
  1h (Supertrend direction, DMI spread, Stochastic RSI, CCI, ROC, MFI, relative volume,
  Keltner position, candle body/wicks, EMA200 distance, volume-price divergence), session
  VWAP deviation, multi-timeframe agreement scores, and cross-asset context (the other
  coin's 1h/4h returns, 24 h rolling correlation).
* f1 alone is run as a comparison (is the bigger feature set useful?). It does not decide.
* Not available historically, stated: order-book, open-interest and liquidation features.

## Procedure (`services/ai-engine/nexus_ai/research.py`, `phase_d.py`)

* Walk-forward: train 270 days, test the next 60 days, rolling; training rows whose label
  ends after the split are purged.
* Model families: logistic regression (baseline), XGBoost, LightGBM, CatBoost; each
  calibrated with isotonic regression on the last 20% of its training window (rows whose
  label overlaps the calibration period are purged); missing values filled with training
  medians.
* Model choice on TRAIN only: purged 3-fold cross-validation with a 24 h embargo; the
  family with the highest cross-validated net expectancy of the trading rule (>= 30 CV
  trades) is chosen. If no family is positive after costs, the window abstains.
* Trading rule on TEST: probability-weighted expected value after costs > 0.05% (win and
  loss sizes estimated on train), families agree (std of probabilities <= 0.10), input not
  out of distribution (Isolation Forest, 1% training quantile); trades on a symbol never
  overlap.
* HMM regimes (3 states, fitted on train, forward-filtered without look-ahead) are reported
  for performance by regime only.

## Gates (all required, `validation.AiGates`)

Mean AUC >= 0.53; AUC > 0.5 in >= 70% of windows; Brier skill > 0 (better than the base
rate); >= 50 out-of-sample trades; expectancy > 0; profit factor >= 1.1; t-stat >= 1.5;
positive windows >= 55%.

A pass on the primary period with f2 would allow registering the model as "validated"
(never active). Activation would still need a separately pre-registered forward paper test
on new data and the owner's explicit action.

## Results

Appended below after the single run.
