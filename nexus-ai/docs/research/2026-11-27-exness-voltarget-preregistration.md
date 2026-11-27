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
