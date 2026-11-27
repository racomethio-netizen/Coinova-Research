# Night scalper: test report

Generated 2026-11-27T10:57:16.543942+00:00 (harness commit `c958dd9`). historical backtest (no orders placed). Simulation equity 10000 USD. Pre-registration: `docs/research/2026-11-27-night-scalper-preregistration.md`.

## Standard account (2015-08-10 to 2026-10-30)

**Verdict: FAILED**

| Gate | Result |
|---|---|
| at least 100 trades | pass |
| net positive after all costs | FAIL |
| profit factor ge 1 2 | FAIL |
| sharpe ge 0 5 | FAIL |
| max drawdown within 5pct | FAIL |
| mc prob drawdown over 5pct le 25pct | FAIL |
| positive in 60pct of half years | FAIL |
| positive at 2x costs | FAIL |
| significant after holm | FAIL |

* Trades 403, win rate 29.3%, expectancy -0.34 R, profit factor 0.59
* Average win 6.01 USD, average loss -4.26 USD
* Net -5.00%, Sharpe -1.10, max drawdown -5.00%, p-value 1.000
* At 2x costs: net -5.01%, expectancy -1.12 R
* Exits: {'stop': 271, 'target': 96, 'time': 34, 'stop_gap': 2}

| Pair | Trades | Expectancy R | Win rate |
|---|---|---|---|
| AUDCAD | 21 | -0.43 | 28.6% |
| AUDNZD | 49 | -0.10 | 38.8% |
| AUDUSD | 82 | -0.39 | 28.0% |
| EURAUD | 39 | -0.11 | 38.5% |
| EURCAD | 24 | -0.40 | 25.0% |
| EURCHF | 10 | -0.02 | 40.0% |
| EURGBP | 18 | 0.04 | 44.4% |
| EURUSD | 56 | -0.49 | 21.4% |
| GBPCAD | 14 | -0.16 | 42.9% |
| GBPUSD | 22 | -0.71 | 13.6% |
| USDCAD | 36 | -0.61 | 19.4% |
| USDCHF | 32 | -0.37 | 28.1% |

| Setting | Trades | Expectancy R | Net |
|---|---|---|---|
| bb_k=1.6 | 353 | -0.35 | -5.02% |
| bb_k=2.4 | 317 | -0.39 | -4.97% |
| stop_atr=1.6 | 386 | -0.38 | -5.02% |
| stop_atr=2.4 | 356 | -0.33 | -5.01% |
| rsi=34/66 | 384 | -0.39 | -4.96% |
| rsi=26/74 | 327 | -0.37 | -4.86% |
| adx_max=20 | 244 | -0.46 | -4.86% |
| adx_max=30 | 435 | -0.31 | -5.02% |
## Raw Spread account (2020-01-29 to 2026-10-30)

**Verdict: FAILED**

| Gate | Result |
|---|---|
| at least 100 trades | pass |
| net positive after all costs | FAIL |
| profit factor ge 1 2 | FAIL |
| sharpe ge 0 5 | FAIL |
| max drawdown within 5pct | FAIL |
| mc prob drawdown over 5pct le 25pct | FAIL |
| positive in 60pct of half years | FAIL |
| positive at 2x costs | FAIL |
| significant after holm | FAIL |

* Trades 1014, win rate 34.2%, expectancy -0.29 R, profit factor 0.80
* Average win 4.31 USD, average loss -2.80 USD
* Net -3.73%, Sharpe -0.86, max drawdown -5.01%, p-value 1.000
* At 2x costs: net -5.00%, expectancy -0.67 R
* Exits: {'stop': 633, 'target': 286, 'time': 87, 'stop_gap': 7, 'emergency': 1}

| Pair | Trades | Expectancy R | Win rate |
|---|---|---|---|
| AUDCAD | 63 | -0.00 | 47.6% |
| AUDNZD | 133 | -0.43 | 30.8% |
| AUDUSD | 180 | -0.18 | 37.2% |
| EURAUD | 78 | -0.23 | 35.9% |
| EURCAD | 42 | -0.03 | 42.9% |
| EURCHF | 37 | -0.53 | 35.1% |
| EURGBP | 69 | -0.46 | 30.4% |
| EURUSD | 116 | -0.21 | 35.3% |
| GBPCAD | 27 | -0.45 | 25.9% |
| GBPUSD | 121 | -0.20 | 34.7% |
| USDCAD | 59 | -0.30 | 33.9% |
| USDCHF | 89 | -0.68 | 21.3% |

| Setting | Trades | Expectancy R | Net |
|---|---|---|---|
| bb_k=1.6 | 1178 | -0.28 | -4.32% |
| bb_k=2.4 | 1229 | -0.26 | -4.69% |
| stop_atr=1.6 | 1136 | -0.35 | -3.83% |
| stop_atr=2.4 | 1070 | -0.22 | -4.18% |
| rsi=34/66 | 1062 | -0.27 | -4.78% |
| rsi=26/74 | 1160 | -0.20 | -4.88% |
| adx_max=20 | 1549 | -0.19 | -4.81% |
| adx_max=30 | 1132 | -0.28 | -4.33% |

## Diagnostic (not a gate): halts switched off

* Standard: 7540 trades, win rate 25.1%, -0.42 R a trade; every year negative: 2015 -0.33, 2016 -0.31, 2017 -0.35, 2018 -0.45, 2019 -0.57, 2020 -0.34, 2021 -0.39, 2022 -0.25, 2023 -0.42, 2024 -0.57, 2025 -0.42, 2026 -0.54
* Raw Spread: 4557 trades, win rate 35.5%, -0.30 R a trade; every year negative: 2020 -0.23, 2021 -0.34, 2022 -0.16, 2023 -0.27, 2024 -0.43, 2025 -0.26, 2026 -0.39

Historical simulation only: no orders were placed, and past results do not guarantee future results.
