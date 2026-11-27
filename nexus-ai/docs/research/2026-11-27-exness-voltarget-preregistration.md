# Exness: volatility control on gold and the NASDAQ-100 (pre-registration, 2026-11-27)

**Status: registered before any of this test's data was downloaded.** The owner wants
the Exness (forex broker) account to be effective.

* Every currency-pair strategy NEXUS tested failed after real Exness costs (G10 styles,
  trend following, NQS-1, the night scalper).
* Exness also offers gold (XAUUSD) and the NASDAQ-100 (USTEC) as CFDs.
* The NEXUS volatility-control rule passed on the NASDAQ-100 index itself (1990–2026,
  `2026-11-26-ndx-voltarget-preregistration.md`).
* This test asks whether that **unchanged rule** works on Exness's own gold and USTEC
  prices, with Exness spreads and with or without overnight financing (swaps).

## Data (Exness's public tick archive, Standard account)

* **Instruments and periods:**
  * `XAUUSDm`: complete months August 2015 to October 2026;
  * `USTECm`: August 2019 to October 2026.
* **Bars.** Bid and ask M5 bars are built by `nexus_backtest.exness_ticks`. Daily
  decisions use the last M5 bar of each UTC weekday.
* **Start.** The first 60 days are volatility warm-up only. The test starts on the first
  day after them.

## Rule (unchanged from the NASDAQ-100 test)

* **Weight.** min(1, 15% ÷ σ), where σ is the realised volatility of the last 30 daily
  returns, annualised with √252, from closes up to that day's close. The rest is cash.
* **Band.** Trade only when the held weight is more than 10 points away from the target.
* **Leverage.** Never more than 1× equity (NEXUS rule), whatever margin Exness allows.
* **Fills.** Buy at the ask and sell at the bid of the decision bar (the real Exness
  spread), plus 0.01% slippage. The cost stress doubles both.
* **Financing, two scenarios:**
  * **A, swap-free** (the primary run): Exness lists gold, and reportedly indices, as
    swap-free, under conditions.
  * **B, swaps charged:** long financing at (US 3-month T-bill + 2.5%) a year on the held
    notional, for every calendar day held. This is a conservative CFD financing
    assumption.
* **Portfolios, each tested:**
  * gold alone;
  * USTEC alone;
  * a 50/50 sleeve (each half runs the rule on its own) from USTEC's start.

## Pass conditions (the NASDAQ-100 test's gates plus one; each portfolio, scenario A)

1. Sharpe above holding the same instrument at 100% (bought once, same costs).
2. Maximum drawdown at least 10 points smaller than holding's.
3. Net return above zero.
4. Sharpe above holding in both halves of the period.
5. Gates 1 and 3 still hold at 2× costs.
6. **New for Exness:** net return still above zero in scenario B (swaps charged).

**Reported, not gated:**
* average weight and the number of trades;
* results by year;
* the scenario B figures for holding;
* NEXUS fit (the share of an account that keeps the 5% drawdown rule).

## Decision rule

* **A portfolio passes:** a paper forward test first, with no orders. Real money only
  after:
  * the owner confirms in the Exness Personal Area (contract specifications) that the
    instruments are swap-free on their account;
  * the owner gives explicit approval, within NEXUS's 1× leverage limit.
* **Nothing passes:** recorded as failed, with no re-tuning on this data.
* **A harness bug found after the run:** it is fixed and the test re-run. Both results
  are reported.

## Results (single run, 2026-11-27; harness commit 01c76fb)

* **Data.** Exness ticks, no month missing.
  * Gold: 135 months, median spread about USD 0.23.
  * USTEC: 87 months, median spread about 4 index points.
* **Verdict: all three portfolios FAILED, narrowly.** No re-tuning will follow.
* A later edit only made date sorting explicit in pandas. Re-run, the result was
  identical.

| Portfolio (swap-free) | Per year | Sharpe | Worst drop | Holding: per year / Sharpe / worst drop | With swaps charged | Failed gate |
|---|---|---|---|---|---|---|
| Gold, 2015-10 to 2026-10 | 11.6% | 0.87 | −20.9% | 11.6% / 0.74 / −29.7% | 7.0% | drawdown only 8.8 points smaller (10 needed); first-half Sharpe 0.60 vs 0.62 |
| NASDAQ-100, 2019-10 to 2026-10 | 15.6% | 0.97 | −22.7% | 20.9% / 0.89 / −35.6% | 10.9% | second-half Sharpe 1.16 vs 1.20 |
| Gold + NASDAQ-100 50/50 | 15.2% | **1.24** | **−18.2%** | 17.8% / 1.04 / −26.9% | 10.2% | drawdown only 8.7 points smaller (10 needed) |

**What held in all three:**
* a higher overall Sharpe than holding;
* smaller drawdowns;
* unchanged at 2× costs;
* positive with swaps charged.

**Why it failed.** Each portfolio missed one strict gate by a small margin. The 50/50
portfolio's worst year was −12% (2022), against −21% for holding.

**Caveat.** 2019–2026 was a strong period for both gold and the NASDAQ-100. Holding them
earned more than the rule did.

**NEXUS fit.** With the 5% drawdown rule:
* 50/50: about 27% of an account, about 4.2% a year;
* gold: about 24%, 2.8% a year;
* NASDAQ-100: about 22%, 3.4% a year.

**Decision.** Recorded as failed. Paper observation on new data is possible, clearly
labelled, as with the adaptive grid. No real-money path follows from this test.
