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

---

## Results

_Single run on 2026-11-26 after the pre-registration commit fe36bff.
* Output: `research/backtesting/nexus_backtest/results/rebalancing_xsmom_2026-11.json`,
  also shown on the dashboard page **Strategy test**.
* One correction before the run, with no results seen: the equal-weight benchmark was
  changed from daily-rebalanced to a true buy-and-hold, to match the text above._

**Rebalancing: basket R3 PASSES. Cross-sectional momentum: 0 of 6 pass.**

Benchmarks (2024-01 to 2026-10):
* BTC hold: +75.6%, worst drop −53.0%, Sharpe 0.66.
* Equal-weight hold of the 10 coins: +6.5%, worst drop −66.2%.

### A. Rebalancing bot vs holding the same basket

| Basket : rule | Rebalanced | Held | Worst drop (held) | Rebalances | Excess 2024 / 2025 / 2026 | Gates |
|---|---|---|---|---|---|---|
| R1 BTC/ETH 50/50 : band | +36.8% | +35.6% | −60.1% (−58.5%) | 9 | −1.3 / +2.9 / −1.2 pts | fail (1 of 3 years) |
| R2 10 coins equal : band | +11.2% | +6.4% | −68.4% (−66.2%) | 9 | +17.2 / −2.7 / −0.3 pts | fail (1 of 3 years) |
| **R3 BTC 40 / ETH 40 / USDT 20 : band** | **+38.4%** | **+28.4%** | **−51.0% (−53.0%)** | **15** | **−1.1 / +5.7 / +1.9 pts** | **PASS** |

Weekly variants are reported, not gated:
* R1 +35.8%;
* R2 +9.8%;
* R3 +37.3%.

**What R3 shows.**
* The cash leg (20% USDT) is rebalanced into coins after falls and taken out after rises.
  That added about 10 points over holding the same mix, with a slightly smaller worst
  drop. It held at 2× costs.
* **It is an investing strategy, not a protection strategy.** Its worst drop was still
  −51%, and BTC alone returned more (+75.6%) with a similar drop.
* **Consequence:** it starts a 180-day paper forward test on 2026-11-27 (worker
  `rebalance-paper`). It goes live only with the owner's approval.

### B. Cross-sectional momentum (top 3 of 10, weekly, long only)

| Variant | Net | Sharpe | Worst drop | p-value | Failed gates |
|---|---|---|---|---|---|
| 1 week | −49.7% | −0.06 | −72.1% | 0.52 | all 6 |
| 1 week + BTC filter | −15.3% | 0.15 | −63.1% | 0.38 | Sharpe, Holm, drawdown, vs equal weight |
| 2 weeks | +20.0% | 0.43 | −72.4% | 0.23 | Sharpe, Holm, drawdown, years |
| **2 weeks + BTC filter** | **+63.1%** | **0.58** | **−58.6%** | **0.18** | **Holm, drawdown** |
| 4 weeks | −35.6% | 0.08 | −76.3% | 0.42 | 5 of 6 |
| 4 weeks + BTC filter | −16.1% | 0.15 | −55.0% | 0.39 | 5 of 6 |

**Result: FAILED, not re-tuned.**
* Every variant made money in 2024 (+15% to +133%) and lost in 2025–2026.
* This is the same pattern as time-series momentum (2026-11-23): crypto momentum worked
  in 2024 and faded afterwards.
* None is statistically significant, and none keeps drawdowns within 35%.

