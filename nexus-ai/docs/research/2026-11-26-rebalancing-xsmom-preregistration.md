# Pre-registration: rebalancing bot and cross-sectional momentum

_Committed before any run. Results are appended once below the line; nothing above it
changes afterwards._

## Why

The owner asked for other strategies that popular bots sell worldwide. Two have not yet
been tested in NEXUS:
* the **rebalancing bot** (Pionex, Binance portfolio and auto-invest);
* academic **cross-sectional momentum**: Liu, Tsyvinski & Wu (2022), "Common Risk
  Factors in Cryptocurrency", *Journal of Finance*, which reports a momentum factor in
  coin returns.

## Data

* Binance spot, 10 coins against USDT: BTC, ETH, BNB, SOL, XRP, ADA, DOGE, LINK, AVAX,
  LTC.
* Official 5m klines (checksums verified, 0 gaps), aggregated to daily closes at
  00:00 UTC.
* Window: 2024-01-01 to 2026-10-31. Data from 2023-06 to 2023-12 is used only for
  lookbacks.
* **Known bias, stated up front:** these 10 coins were chosen with today's knowledge (all
  still large and listed). That survivorship bias flatters BOTH strategies and their
  benchmarks.

## Costs

* 0.10% fee + 0.05% slippage per side on every traded notional.
* Stress test: 2× costs.
* Spot only, no leverage, no shorting (futures stay disabled).

## A. Rebalancing bot

* **Baskets** (fixed now, no tuning):
  * R1: BTC 50% / ETH 50%;
  * R2: the 10 coins at equal weight;
  * R3: BTC 40% / ETH 40% / USDT 20% (cash leg).
* **Rule.** Rebalance every coin back to its target weight whenever any weight drifts more
  than 5 percentage points from target, checked at each daily close. A calendar variant
  rebalances every Monday instead.
* **Comparison.** The SAME basket bought on day one and never rebalanced (buy-and-hold).
* **Claim tested:** rebalancing beats holding the same basket, after costs.
* **Gates** (per basket, threshold variant):
  1. Net return minus buy-and-hold return > 0 over the window.
  2. The excess is positive in at least 2 of 3 years.
  3. The excess is still > 0 at 2× costs.
  4. Maximum drawdown no worse than buy-and-hold's plus 5 points.

## B. Cross-sectional momentum (long-only)

* **Each Monday 00:00 UTC:**
  * rank the 10 coins by their return over the past L weeks;
  * hold the top 3 at equal weight for one week;
  * pay costs on turnover.
* **Variants:**
  * L ∈ {1, 2, 4} weeks;
  * with or without a market filter (hold cash when BTC's close is below its 20-week
    average);
  * 6 variants in total, all reported.
* **Gates** (a variant passes only if all hold):
  1. Annualised Sharpe ≥ 0.5 (daily returns).
  2. Mean daily return > 0 with a stationary-bootstrap p-value (block 10, 5,000
     resamples) significant after a **Holm correction across the 6 variants**.
  3. Maximum drawdown ≤ 35%.
  4. Positive in at least 2 of 3 years.
  5. Sharpe still > 0 at 2× costs.
  6. Sharpe above the equal-weight hold of the same 10 coins (risk-adjusted improvement).
* **Reported:** turnover, exposure, best and worst coin contribution, and BTC hold.

## Consequence

* **Pass.** A paper forward test in NEXUS. Live only with the owner's approval.
* **Fail.** Recorded and shown in the app. No re-tuning.
