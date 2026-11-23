# Pre-registration: crypto time-series momentum grid (Han, Kang & Ryu design), risk-managed

_Committed before any run. Results are appended once below the line; nothing above it
changes afterwards._

## Source and what it claims

Han, Kang and Ryu, "Time-Series and Cross-Sectional Momentum in the Cryptocurrency
Market".
* They test lookbacks of 1–4 weeks and holding periods of 1 day to about 4 weeks on major
  coins, and report profitable time-series momentum (TSMOM) at short horizons.
* Its known limitations:
  * the sample ends before 2023;
  * costs and short-side frictions (funding, borrowing, liquidation) are only partly
    modelled;
  * results come from a period of extreme retail-driven trends.
* The paper's PDF was not accessible from this environment. The design below follows the
  owner's brief, which restates the paper's grid. This is recorded rather than claiming a
  reading of the full text.

Risk management follows the volatility-targeting literature (Barroso and Santa-Clara
2015; Moskowitz, Ooi and Pedersen 2012): scale exposure by target volatility divided by
realised volatility.

**Prior NEXUS evidence (2026-11-20).** A daily long/short TSMOM with 10–90 day lookbacks on
BTC/ETH perpetuals failed out of sample: −58.7%, Sharpe 0.005. This grid is a different
specification and is tested on its own.

## Universe and data

* **Assets.** Binance USD-M perpetuals: BTC, ETH, BNB, SOL, XRP, ADA, DOGE, LINK, AVAX,
  LTC (all against USDT).
* **Data.** Daily klines and actual funding rates from data.binance.vision, checksums
  verified when the archive offers them, from 2020-01 (or the first full month after
  listing) to 2026-10.
* **Gaps.** Missing archive months abort the run. Nothing is filled.

## Rules (no look-ahead)

* **Signal.** For coin i on day t: `s = sign(close_t / close_{t−L} − 1)`, using closes up to
  and including day t.
* **Holding.** Overlapping tranches (Jegadeesh–Titman). Each day a new tranche of 1/h of
  the book takes the day-t signals and holds them for h days. The position for day t+1 is
  the average of the h live tranches.
* **Direction variants.**
  * long-only: long when s = +1, else cash;
  * short-only: short when s = −1, else cash;
  * long-short: both.
* **Weights.** Equal weight across the coins available that day, gross exposure at most
  1 (no leverage).
* **Risk-managed variant.**
  * Portfolio exposure × min(1, 20% / realised volatility), where realised volatility is
    the portfolio's past 30 days, annualised, measured on the raw strategy's returns up
    to day t.
  * It never levers up above 1.
* **Costs.**
  * 5 bp taker fee + 2 bp slippage per side on every change of position (turnover).
  * Actual funding: a long pays a positive rate, a short receives it.
  * Sensitivity runs at 2× and 3× costs.
* **Liquidation.**
  * At 1× isolated, a position is liquidated only by an adverse move of about 100% minus
    maintenance margin.
  * The report gives the worst single-day adverse move per position side, against a 50%
    alarm level.

## Grid (all reported)

* Lookback L ∈ {7, 14, 28} plus ENS (the average of the three signals).
* Holding h ∈ {1, 5, 7, 14} days.
* Direction ∈ {long, short, long-short}.
* Raw or risk-managed.
* Total: 4 × 4 × 3 × 2 = **96 configurations**.

## Splits

* **In-sample (IS):** 2020-03-01 to 2022-12-31.
* **Out-of-sample (OOS):** 2023-01-01 to 2026-10-31.
* No parameter is chosen on OOS.
* The configuration with the best IS Sharpe is the primary candidate. Every configuration
  is also judged on OOS, with a multiple-testing correction.

## Gates (a configuration passes only if all hold on OOS)

1. Annualised Sharpe ≥ 0.5 net of costs and funding.
2. Mean daily return > 0 with a stationary-bootstrap p-value (block 10, 5,000 resamples)
   significant after a **Holm correction across all 96**.
3. Maximum drawdown ≤ 25%.
4. Positive net return in at least 3 of the 4 OOS calendar years (2023, 2024, 2025, 2026
   year to date).
5. Sharpe still > 0 at 2× costs.
6. Not one coin: Sharpe > 0 after removing the single best-contributing coin.

## Reported, not gated

* Probability of backtest overfitting (CSCV) across the grid, using IS and OOS ranks.
* Sortino, Calmar, exposure, turnover and funding paid.
* Monte Carlo (trade-day bootstrap) drawdown distribution.
* Equal-weight spot buy-and-hold of the same coins.
* BTC buy-and-hold.
* NEXUS's Crypto Trends result over the same OOS window, if available.

## Consequence

* **Pass.** A forward paper test (spot long side via the spot paper broker; short side
  only after a perps paper path exists). Live only with the owner's explicit approval,
  behind the futures activation switch, at 1×.
* **Fail.** Recorded; no re-tuning.

---

## Results

_(appended after the single run)_
