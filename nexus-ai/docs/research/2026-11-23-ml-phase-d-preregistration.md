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

## Results (single run per configuration, 2026-11-22 17:30-18:05 UTC, after this file was committed as d05b90e)

Rows: 139,646 labelled 15m bars (primary), 139,482 (secondary); 7 walk-forward windows each.
Base rate (target before stop): about 0.34. Unconditional net expectancy of entering every
bar: -0.30% (primary), -0.32% (secondary): costs dominate.

### Decision: all three configurations FAIL

| Run | System trades | Windows abstained | Failed gates |
|---|---|---|---|
| Primary, f2 (decides) | 0 | 7 of 7 | every gate (no trades) |
| Primary, f1 (comparison) | 0 | 6 of 7 (1 chosen, all its test candidates filtered) | AUC 0.517 < 0.53, Brier skill < 0, no trades |
| Secondary, f2 | 0 | 6 of 7 (1 chosen, filtered) | AUC 0.515 < 0.53, no trades |

Why the windows abstained: in the purged cross-validation on each TRAINING window, no
family reached a positive net expectancy with at least 30 trades (typical CV expectancy
-0.2% to -0.4% per trade; the few positive values came from 4-22 trades and did not
qualify). The system therefore abstained, as designed.

### Model comparison (test windows, every family fitted, no filters; reported, not used to choose)

| Run | Family | Mean AUC | PR-AUC | Brier skill | ECE | Trades | Expectancy | PF |
|---|---|---|---|---|---|---|---|---|
| Primary f2 | logit | 0.522 | 0.347 | -0.0042 | 0.037 | 96 | +0.07% | 1.09 |
| Primary f2 | xgb | 0.517 | 0.343 | -0.0094 | 0.038 | 134 | -0.19% | 0.72 |
| Primary f2 | lgbm | 0.515 | 0.344 | -0.0016 | 0.034 | 8 | -0.25% | 0.29 |
| Primary f2 | catboost | 0.519 | 0.344 | -0.0044 | 0.038 | 94 | -0.18% | 0.77 |
| Primary f1 | logit / xgb / lgbm / catboost | 0.514 / 0.514 / 0.512 / 0.514 | ~0.34 | < 0 | ~0.035 | 138 / 44 / 44 / 110 | -0.13% / -0.25% / -0.13% / -0.11% | 0.83 / 0.48 / 0.57 / 0.77 |
| Secondary f2 | logit / xgb / lgbm / catboost | 0.509 / 0.506 / 0.509 / 0.514 | ~0.33 | < 0 | ~0.033 | 56 / 56 / 20 / 32 | -0.56% / -0.24% / -0.33% / -0.30% | 0.04 / 0.25 / 0.28 / 0.49 |

Calibration is good (ECE ~0.03-0.04: stated probabilities match frequencies) but the
probabilities barely move away from the base rate, and the Brier skill is negative: the
models are not better than always predicting the base rate. The one positive line (primary
logit, +0.07% over 96 trades, PF 1.09) is within noise and was not chosen by the training
CV.

### Secondary tasks (skill only)

| Task | Primary f2 | Primary f1 | Secondary f2 |
|---|---|---|---|
| 3-class direction, log-loss vs class prior | 1.017 vs 1.046 | 1.017 vs 1.046 | 1.008 vs 1.032 |
| 3-class direction, accuracy vs majority class | 48.4% vs 48.6% | 48.4% vs 48.6% | 49.4% vs 49.6% |
| Volatility expansion, AUC | **0.762** | **0.767** | **0.806** |

### Conclusion

* No direction or trade-outcome model has an edge after costs on BTC/ETH 15m data with
  these features; nothing is registered as validated, nothing can be activated.
* Feature usefulness: the larger f2 set did not improve on f1 (AUC 0.515-0.522 vs
  0.512-0.514, both below the gate); more indicators are not the missing ingredient.
* Volatility IS forecastable (AUC 0.76-0.81 on both periods). That is useful for risk
  (sizing, regime, stop distances) and is the most promising use of models here; it does
  not by itself create a directional edge. Using it for risk would be a new, separately
  pre-registered study.
