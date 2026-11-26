# Pre-registration: institutional forex strategies on the G10 currencies

_Committed before any run. Results are appended once below the line; nothing above it
changes afterwards._

## Why, and what the research says

The owner asked for the methods banks and institutions use in forex.

**What banks mostly do is not a strategy a retail account can copy.**
* Dealer banks earn from making markets and internalising client flow: they match client
  orders on their own books, over 80% overall and over 90% in some major pairs
  ([BIS](https://www.bis.org/publ/qtrpdf/r_qt1612v.htm),
  [BIS 2025 Triennial](https://www.bis.org/publ/qtrpdf/r_qt2512v.htm)).
* They execute with algorithms
  ([BIS Markets Committee](https://www.bis.org/press/p201030.htm)).
* That edge comes from client flow, speed and inventory.

**What institutions run as systematic strategies is published and testable.** Deutsche
Bank's Currency Returns index combines three styles in equal parts:
* carry;
* momentum (12 months);
* valuation (PPP).

The academic evidence for each:
* **Carry.** Menkhoff, Sarno, Schmeling & Schrimpf (2012, *Journal of Finance*): more
  than 5% a year after costs in broad samples, but crashes when global FX volatility
  jumps ([CEPR](https://cepr.org/publications/DP8291)). Performance has declined since
  2008 ([NBER w32900](https://www.nber.org/papers/w32900)).
* **Dollar carry.** Lustig, Roussanov & Verdelhan (2014, *Journal of Financial
  Economics*): long all foreign currencies when foreign rates exceed US rates on
  average, otherwise short ([NBER](https://www.nber.org/papers/w16427)).
* **Momentum.** Menkhoff et al. (2012, *JFE*): up to 10% a year, but transaction costs
  take a large part, and it is concentrated in less liquid currencies
  ([BIS WP 366](https://www.bis.org/publ/work366.pdf)).
* **Value.** Asness, Moskowitz & Pedersen (2013): value premia exist in currencies
  ([paper](https://pages.stern.nyu.edu/~lpederse/papers/ValMomEverywhere.pdf)).
* **Combining styles.** Kroencke, Schindler & Schrimpf (2014, *Review of Finance*):
  combining the styles improves diversified portfolios out of sample
  ([ZEW](https://www.zew.de/en/publications/international-diversification-benefits-with-foreign-exchange-investment-styles-2)).

**Not testable here:**
* The month-end London 4pm fix flows
  ([Melvin & Prins](https://ecb.eu/events/pdf/conferences/131216/Third_FX_Workshop_MELVIN_PRINS_Equity%20hedging%20and%20exchange%20rates%20Nov%202013.pdf))
  need intraday data. They can be tested later with Exness hourly bars from the bridge.
* Options volatility premia: MT5 spot accounts have no FX options.

## Data (FRED, Federal Reserve Bank of St. Louis; hashes recorded in the output)

* **Universe:** the 9 G10 currencies against USD: EUR, GBP, JPY, CHF, CAD, AUD, NZD, SEK,
  NOK.
* **Spot:** the H.10 daily noon rates. JPY, CHF, CAD, SEK and NOK are quoted per USD and
  inverted.
* **Interest rates:**
  * the USD 3-month T-bill (DTB3);
  * the OECD 3-month interbank rate for each other currency (monthly), carried forward
    where a series ends early (the euro area and UK end 2026-01).
* **Excess return of holding a foreign currency:** the spot return plus (foreign rate −
  USD rate) × calendar days / 365.
* **Value proxy:** FRED's inflation indices end early (Japan in 2021, most others in
  2025). Value therefore uses the **5-year nominal reversal** (minus the log change in
  spot over the past 60 months), a common proxy for the real-exchange-rate signal over
  long horizons in G10.

## Strategies (5; Holm correction across all 5)

All five share these rules:
* Monthly. Signals use data up to the last trading day of the previous month; positions
  are set on the first trading day of each month and drift within the month.
* Gross exposure is 1× equity.

The strategies:
1. **Carry.**
   * Rank by (foreign rate − USD rate).
   * Long the top 3 currencies, short the bottom 3 (1/6 each).
2. **Dollar carry.**
   * If the average (foreign rate − USD rate) over the 9 is positive, long an
     equal-weighted basket of the 9 against USD; otherwise short it (1/9 each).
3. **Momentum.**
   * Rank by the excess return over the past 3 months.
   * Long the top 3, short the bottom 3.
4. **Value.**
   * Rank by the 5-year reversal (the largest 60-month fall against USD is "cheapest").
   * Long the 3 cheapest, short the 3 dearest.
5. **Combined (Currency Returns style).** One third each of carry, momentum and value
   weights, rescaled to 1× gross.

**Costs, per side on traded notional:**
* 0.02% for EUR, JPY, GBP, CHF, CAD, AUD;
* 0.04% for NZD, SEK, NOK;
* a stress test at 2×.

## Window

* **Test window:** 2004-01-01 to 2026-10-31.
* **Warm-up:** from 1999 (the euro's start). Value needs 5 years.

## Gates (per strategy; all must hold)

1. Net return > 0 after costs.
2. Annualised Sharpe (daily, √252) ≥ 0.5.
3. Mean daily return > 0 with a stationary-bootstrap p-value (block 10, 5,000 resamples)
   significant after **Holm across the 5 strategies**.
4. Maximum drawdown ≤ 25%.
5. Positive in both halves: 2004–2015 and 2016 to 2026-10.
6. Positive in at least 55% of calendar years.
7. Still > 0 at 2× costs.
8. Still > 0 without the currency that contributed most.

**Reported:**
* annual return;
* turnover;
* contribution per currency and per year;
* the worst month;
* the correlation between styles.

## Consequence

* **Pass:**
  * the strategy becomes a RESEARCH signal for forex in NEXUS;
  * a paper forward test (monthly);
  * later a demo-only test on Exness through the bridge, with Exness's real spreads and
    swaps.
  * Live only with the owner's authorization for forex.
* **Fail:** recorded and shown in the app. Not re-tuned.

---

## Results

_(appended after the single run)_

### Results of the single run (2026-11-26, harness commit 56e4b52)

Output: `research/backtesting/nexus_backtest/results/fx_styles_2026-11.json`, with the FRED
file hashes. Historical backtest only; no order was placed.

**FAILED: none of the 5 passes. Carry came closest.**

| G10, 2004-01 to 2026-10, 1× gross | Per year | Sharpe | Worst drop | p-value | 2004–2015 | 2016–2026 | Positive years | Failed gates |
|---|---|---|---|---|---|---|---|---|
| **Carry** | **+1.13%** | **0.30** | −16.2% | 0.07 | +13.4% | +14.0% | 16 of 23 | Sharpe ≥ 0.5, significance |
| Dollar carry | +0.07% | 0.05 | −31.5% | 0.39 | −7.3% | +9.7% | 12 of 23 | 5 of 8 |
| Momentum (3 months) | −1.18% | −0.28 | −34.5% | 0.93 | −2.1% | −22.0% | 5 of 23 | all 8 |
| Value (5-year reversal) | +0.30% | 0.10 | −10.4% | 0.32 | +4.2% | +2.7% | 12 of 23 | Sharpe, significance, years |
| Combined (carry + momentum + value) | +0.18% | 0.07 | −17.5% | 0.38 | +7.8% | −3.4% | 12 of 23 | 6 of 8 |

**Carry in detail.**
* Its worst year was 2008 (−10.9%); its best years were 2009 (+8.9%) and 2026 to date
  (+5.6%).
* Gains came from AUD (+10.2 points), JPY (+8.9, the funding side) and NZD (+8.1).
* It held at 2× costs (+28.5% over the window) and without the best currency (+17.3%).
* Turnover was low: about 0.8× a year.

**Other details.**
* Momentum lost mainly after 2016: Sharpe −0.74 in 2016–2026.
* Correlations between styles were low (−0.21 to +0.24), as the literature says, but the
  combination still earned almost nothing because momentum lost.

**What it means.**
* The institutional styles are real but **small** in G10 at NEXUS's 1× leverage ceiling.
* Carry, the best of them, earned about 1.1% a year on top of the cash return, with a
  2008-style crash risk.
* Institutions run these styles with several times more leverage for a 5–10% volatility
  target. NEXUS does not, by mandate.
* **At Exness, carry depends on Exness's swap rates, not interbank rates.** Swap-free
  accounts earn no carry at all. The read-only bridge already reports each symbol's swap
  long/short, so the next honest step is to measure whether Exness's swaps pay positive
  carry before testing anything on Exness.
* Momentum, which most retail "trend" bots use, lost money in G10 since 2016.

**Consequence.** Nothing is adopted and nothing is re-tuned. Forex stays RESEARCH.
