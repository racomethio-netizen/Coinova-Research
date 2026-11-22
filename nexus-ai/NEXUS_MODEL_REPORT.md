# NEXUS model report

Every model NEXUS has trained and how it did out of sample. Models are judged by NET
trading performance after costs, not by accuracy. Full tables:
docs/research/2026-11-23-ml-phase-d-preregistration.md.

## Current status

* **Active model: none.** No model has passed the promotion gates, so none can be
  activated (activation also needs a forward paper test and the owner).
* Production impact: none. The trader and the signal-fusion engine report "no validated
  model is active".

## Phase D study (pre-registered, 2026-11-23)

| Item | Setup |
|---|---|
| Target | Triple barrier on 15m bars: +2 ATR before -1 ATR within 12 h, entry at next open, costs 0.30% round trip |
| Features | f2: 83 (indicators on 15m/1h/4h, Phase C indicators, VWAP deviation, multi-timeframe scores, cross-asset BTC/ETH); f1: 47 for comparison |
| Models | Logistic regression (baseline), XGBoost, LightGBM, CatBoost; isotonic calibration; time-ordered fitting |
| Validation | Walk-forward 270/60 days; model choice by purged 3-fold CV with 24 h embargo on TRAIN only; abstain when nothing is positive after costs |
| Uncertainty | Model disagreement, Isolation Forest out-of-distribution flag, calibrated probabilities |
| Regimes | 3-state HMM fitted on train, forward-filtered (no look-ahead) |
| Data | Primary unseen 2022-11..2024-11; secondary 2024-11..2026-11 |

| Result | Primary (decides) | Secondary |
|---|---|---|
| System trades | 0 (abstained in all windows) | 0 |
| Best mean AUC of any family | 0.522 | 0.514 |
| Brier skill vs base rate | negative for every family | negative |
| Calibration error (ECE) | 0.03-0.04 | 0.03 |
| Volatility-expansion AUC (secondary task) | 0.76 | 0.81 |
| Verdict | **FAIL** | FAIL |

## Earlier

* 2026-11-14 ensemble (XGBoost + logistic, f1 features, triple barrier): built with the same
  promotion gates; no version passed; never activated.

## What would change this

* A target with a structural reason to be predictable (volatility, funding, liquidity),
  used for risk rather than direction.
* Cheaper execution (maker orders) or longer horizons, so a small edge survives costs.
* Each idea goes through its own pre-registration on data it has not seen.

## Tooling now available (Phase D)

Multi-target labels (forward returns, cost-band direction, triple-barrier outcome,
volatility); purged k-fold with embargo; four model families with time-ordered calibration;
Isolation Forest OOD; causal HMM regimes; evaluation (precision, recall, F1, ROC-AUC, PR-AUC,
Brier, ECE, expectancy, profit factor, drawdown, Sharpe, Sortino, by asset/regime/family);
registry with versions, sha256-verified artifacts, owner-only activation and rollback to the
previous validated version.
