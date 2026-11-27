# Binance Spot Grid bot: test report

Generated 2026-11-27T12:38:08.129303+00:00 (harness commit `24b15ba`). historical backtest (no orders placed). 10 coins x 1,000 USDT, Binance 5-minute data 2021-01 to 2026-10. Pre-registration: `docs/research/2026-11-27-binance-spot-grid-preregistration.md`.

**Holding the same coins:** net +1048.7%, Sharpe 0.90, max drawdown -87.6%, Calmar 0.59.

| Configuration | Net | Sharpe | Max drawdown | Calmar | 2x fees | Trades | Fees (USDT) | Verdict |
|---|---|---|---|---|---|---|---|---|
| ±10% / 40 grids | -63.9% | -0.22 | -78.6% | -0.20 | -93.9% | 350724 | 15885 | FAILED |
| ±10% / 40 grids + stop | -72.5% | -0.99 | -78.7% | -0.25 | -93.8% | 275115 | 10572 | FAILED |
| ±20% / 40 grids | +12.1% | 0.24 | -62.1% | 0.03 | -51.8% | 148301 | 12045 | FAILED |
| ±20% / 40 grids + stop | -26.1% | -0.07 | -59.2% | -0.09 | -64.8% | 129718 | 8176 | FAILED |
| ±30% / 30 grids | +101.6% | 0.50 | -57.4% | 0.22 | +31.2% | 45852 | 7652 | FAILED |
| ±30% / 30 grids + stop | +8.1% | 0.19 | -55.2% | 0.02 | -27.5% | 42307 | 4895 | FAILED |

## Original pre-registered run (before the fill correction)

* ±10% / 40 grids: net -78.3%, Sharpe -0.43, FAILED
* ±10% / 40 grids + stop: net -80.6%, Sharpe -1.27, FAILED
* ±20% / 40 grids: net -10.9%, Sharpe 0.14, FAILED
* ±20% / 40 grids + stop: net -36.7%, Sharpe -0.17, FAILED
* ±30% / 30 grids: net +80.4%, Sharpe 0.45, FAILED
* ±30% / 30 grids + stop: net +1.1%, Sharpe 0.15, FAILED

After the pre-registered run (commit 29dfdfc), a fair-game sanity check led to one fix: an order that is already through the market when it becomes active now fills at the bar's open, like a real limit order, instead of at its worse line price. The check's remaining bias was traced to the synthetic generator's price jumps (it shrinks with a near-continuous path). Re-run at commit 24b15ba. Both runs fail every configuration; the figures above are the corrected run.

## Failed checks

* **±10% / 40 grids:** net positive after fees, sharpe ge 0 5, sharpe above holding, calmar at least holding, positive in 60pct of half years, positive at 2x fees, positive without best coin, significant after holm
* **±10% / 40 grids + stop:** net positive after fees, sharpe ge 0 5, sharpe above holding, calmar at least holding, positive in 60pct of half years, positive at 2x fees, positive without best coin, significant after holm
* **±20% / 40 grids:** sharpe ge 0 5, sharpe above holding, calmar at least holding, positive at 2x fees, significant after holm
* **±20% / 40 grids + stop:** net positive after fees, sharpe ge 0 5, sharpe above holding, calmar at least holding, positive in 60pct of half years, positive at 2x fees, positive without best coin, significant after holm
* **±30% / 30 grids:** sharpe above holding, calmar at least holding, significant after holm
* **±30% / 30 grids + stop:** sharpe ge 0 5, sharpe above holding, calmar at least holding, positive at 2x fees, positive without best coin, significant after holm

## Each year (grid net return by calendar year)

| Configuration | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| ±10% / 40 grids | +49.7% | -60.6% | +2.5% | +10.7% | -27.2% | -26.0% |
| ±10% / 40 grids + stop | -6.9% | -28.3% | +0.1% | -19.4% | -28.8% | -28.3% |
| ±20% / 40 grids | +102.2% | -51.2% | +20.0% | +29.9% | -13.8% | -15.4% |
| ±20% / 40 grids + stop | +25.5% | -27.4% | +7.8% | +11.1% | -9.8% | -24.9% |
| ±30% / 30 grids | +136.0% | -44.8% | +30.0% | +38.9% | -4.2% | -10.5% |
| ±30% / 30 grids + stop | +58.6% | -40.3% | +21.0% | +19.7% | -11.8% | -10.6% |
| Holding | +2073.3% | -82.6% | +216.2% | +107.1% | -37.9% | -25.2% |

Historical simulation only: no orders were placed, and past results do not guarantee future results.
