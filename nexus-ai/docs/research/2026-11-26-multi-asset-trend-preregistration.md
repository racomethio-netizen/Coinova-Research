# Pre-registration: trend following on forex majors and a US stock index (multi-asset Stage 3)

_Committed before any run. Results are appended once below the line; nothing above it
changes afterwards._

## Why

The owner asked for a multi-asset platform. This is the first evidence test outside
crypto, using the most documented strategy family in traditional markets:
* time-series momentum: Moskowitz, Ooi & Pedersen (2012), "Time Series Momentum",
  *Journal of Financial Economics*;
* the 200-day moving-average trend filter: Faber (2007), "A Quantitative Approach to
  Tactical Asset Allocation".

## Data (free, official, checked)

FRED, Federal Reserve Bank of St. Louis.
* **FX spot, daily:** the noon buying rates in New York (H.10).
  * EUR (DEXUSEU), GBP (DEXUSUK), AUD (DEXUSAL), NZD (DEXUSNZ): USD per unit.
  * JPY (DEXJPUS), CAD (DEXCAUS), CHF (DEXSZUS): inverted to USD per unit.
* **Interest rates, for FX carry and the stock index's financing cost:**
  * USD: the daily 3-month T-bill (DTB3).
  * The other six: the OECD 3-month interbank rates, monthly (IR3TIB01**M156N for EZ, GB,
    AU, NZ, JP, CA, CH). Each monthly value applies to that whole month; the last value
    is carried forward where a series ends early.
* **Stock index:** NASDAQ-100 (NASDAQ100), daily close, price index without dividends.
  It is the index QQQ and NQ futures track.
* **Not tested:**
  * Commodities: free daily data here is spot oil and gas only, which is not tradable,
    and there is no gold. Futures roll costs cannot be modelled. Commodities wait for
    tradable history (Binance commodity perpetuals since late 2025; IBKR futures once
    connected).
  * The S&P 500: FRED has only 10 years of it.

**Instrument returns (excess returns, as for futures or forward contracts):**
* FX, long the foreign currency: the daily spot return plus (foreign rate − USD rate) ×
  calendar days / 365. A short position gets the negative.
* Stock index: the daily price return minus the USD rate × calendar days / 365.
  * Dividends are not included (about 1% a year on the NASDAQ-100).
  * This understates longs and overstates shorts by that much. Reported, not adjusted.

## Strategies (2, judged separately, Holm across both)

Common to both:
* **Signals:** computed on closes up to the previous trading day.
* **Rebalancing:** to target weights on the first trading day of each week.
* **Universe:** 8 instruments (7 FX pairs and the NASDAQ-100).
* **Volatility:** σ_i is the 60-day realised volatility, annualised with √252.
* **Size:** **gross exposure is capped at 1× equity** (NEXUS's leverage ceiling); weights
  are scaled down to fit.

**A. Time-series momentum (12 months).**
* Signal per instrument: the sign of its excess return over the past 252 trading days.
* Weight: signal × (1 / σ_i), scaled so that Σ|w| = 1.

**B. 200-day trend filter.**
* For each FX pair: long if spot is above its 200-day average, short if below.
* For the NASDAQ-100: long if above, otherwise **flat**, never short.
* Weights are inverse-volatility over the active positions, scaled so that Σ|w| = 1.

**Costs, per side on traded notional:**
* FX 0.02% (spread and commission at a low-cost broker);
* the stock index 0.03%;
* a stress test at 2×.

## Window

* **Test window:** 2004-01-01 to 2026-10-31, about 23 years.
* **Warm-up:** from 2002-04, when the Japanese 3-month rate series begins.

## Gates

Each strategy passes only if all hold.

1. Net return > 0 after costs.
2. Annualised Sharpe (daily, √252) ≥ 0.5.
3. Mean daily return > 0 with a stationary-bootstrap p-value (block 10, 5,000 resamples)
   significant after a **Holm correction across A and B**.
4. Maximum drawdown ≤ 20%.
5. Positive in both halves: 2004–2015 and 2016 to 2026-10.
6. Positive in at least 60% of the calendar years.
7. Still > 0 at 2× costs.
8. Still > 0 without the instrument that contributed most.

**Reported:**
* annual return (at the 1× cap);
* contribution per instrument and per year;
* turnover and average gross exposure;
* buy-and-hold of the NASDAQ-100 (price index), for context.

## Consequence

* **Pass:** RESEARCH signals for forex and stocks in NEXUS, then a paper forward test.
  Live only after a broker connector exists and the owner authorizes that market.
* **Fail:** recorded and shown in the app. Not re-tuned.

---

## Results

_(appended after the single run)_

### Results of the single run (2026-11-26, harness commit 06e359a)

Output: `research/backtesting/nexus_backtest/results/multi_asset_trend_2026-11.json`, also
on the dashboard page **Strategy test**.
* The FRED files' SHA-256 hashes and row counts are recorded in the output.
* Historical backtest only; no order was placed.

**FAILED: neither strategy passes.**

| 2004-01 to 2026-10, gross ≤ 1× | Net | Per year | Sharpe | Worst drop | p-value | 2004–2015 | 2016–2026 | Positive years |
|---|---|---|---|---|---|---|---|---|
| A: 12-month momentum | +1.5% | +0.07% | 0.04 | −19.6% | 0.42 | +7.8% | −5.8% | 12 of 23 |
| B: 200-day trend filter | +4.8% | +0.21% | 0.06 | −19.5% | 0.36 | +21.1% | −13.4% | 10 of 23 |
| NASDAQ-100 held (price only, for context) | +1,846% | +13.9% | 0.70 | −53.7% | | | | |

* **Gates.**
  * Both failed: Sharpe ≥ 0.5, significance, both halves, 60% of years, without the best
    instrument.
  * A also failed at 2× costs (−2.3%). B was +0.1% at 2× costs.
  * Both passed net > 0 and drawdown ≤ 20%.
* **Contributions.**
  * A's gains came from JPY (+11.1 points) and the index (+6.0); CHF (−5.5) and EUR
    (−3.5) lost.
  * B's gains came from the index (+13.6) and EUR (+7.5); CHF lost 8.3.
* **What it shows.**
  * Trend following on these 7 currencies and the NASDAQ-100 earned a little before 2016
    and lost it afterwards.
  * This matches published evidence that currency trend returns have faded since about
    2010.
  * At NEXUS's 1× leverage ceiling, even the good years were small (−9% to +14% a year).
* **Consequence.** Neither strategy is adopted, and neither is re-tuned. Stocks and forex
  get no trading strategy from this test.
