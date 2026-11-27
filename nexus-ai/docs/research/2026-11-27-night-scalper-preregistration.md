# Night scalper (forex): pre-registration (2026-11-27)

**Status: registered before any of this test's data was downloaded.** The only Exness
ticks seen so far are the five majors used by NQS-1. NQS-1 never looked at night-session
mean reversion, and nothing here was tuned on them.

## Why this test

The owner asked whether the strategies of commercial forex bots can be used.

* Their code is closed (compiled MetaTrader files) and is not opened.
* Their **public descriptions** are used instead. The most common profitable-looking type
  on the MQL5 market is the "night scalper". It trades the quiet hours after the New York
  close, and fades small moves back towards the mean, one trade at a time with a stop.
* It is the only popular type allowed under NEXUS rules (no grid, no martingale, no
  averaging down).
* Its known weakness: spreads are widest at those hours.

**Question:** after real Exness spreads, does a plain night scalper make money?

## Data (Exness's public tick archive, `ticks.ex2archive.com`)

* **Traded pairs (12, fixed now):**
  * majors: EURUSD, GBPUSD, AUDUSD, USDCHF, USDCAD;
  * crosses: EURGBP, EURCHF, EURAUD, EURCAD, GBPCAD, AUDCAD, AUDNZD.

  NZDUSD is used only to convert NZD profits to USD.
* **Two account types, two tests:**
  * **Standard** (symbols ending in `m`): complete months August 2015 to October 2026.
    No commission.
  * **Raw Spread** (`_Raw_Spread`): January 2020 to October 2026 (the archive starts
    there). Commission of USD 7 per 100,000 base units round trip (Exness: up to USD
    3.50 each side per lot; the maximum is used).
* **Bars:** M5 bars of bid **and** ask, built from the ticks by
  `nexus_backtest.exness_ticks`, so every fill pays the real spread of that moment.
  * Times are UTC; the session rules use New York local time (`America/New_York`).
  * H1 bars (bid) are resampled from M5. An H1 bar is used only after it closes.

## Rules (fixed; no fitting, no walk-forward: all history is out of sample)

* **Indicators (M5, bid close):** Bollinger bands (20, 2.0), RSI 14, ATR 14.
  **Quiet-market filter:** ADX 14 of the last closed H1 bar below 25.
* **When:** a decision at an M5 bar's close is allowed only when that close falls
  between 17:30 and 21:00 New York time, Monday to Thursday evenings. This is after the
  17:00 rollover, during the Sydney and early Tokyo hours. Sunday and Friday evenings are
  excluded.
* **Entry:**
  * **Long:** close below the lower band and RSI below 30.
  * **Short:** close above the upper band and RSI above 70.
  * **Skip** if the room to the target after the spread is too small:
    |middle band − close| − mean spread of the bar < 0.5 ATR.
  * **Fill:** at the next bar's open, at the ask for a buy and the bid for a sell.
* **Exits:**
  * **Target:** the middle band at the signal bar, a fixed price, filled as a limit
    order.
  * **Stop:** 2.0 ATR from the fill. If price gaps through it, the fill is at the open.
  * **Time exit:** after 36 bars (3 hours) or at 23:00 New York time, whichever comes
    first.
  * When the stop and the target are both reachable in the same bar, the stop is assumed
    first.
  * Price checks use the side the position closes on: the bid for a long, the ask for a
    short.
  * No position is held through a rollover, so there is no swap.
* **Costs:**
  * the real bid/ask spread at every fill;
  * plus 0.2 pip slippage on stop and time-exit fills;
  * plus the Raw Spread commission.
* **Risk (NEXUS defaults):**
  * 0.25% of equity per trade, sized on the stop distance;
  * a 1% daily loss limit, a 3% weekly one, and a 5% drawdown halt that stays for the
    rest of the run;
  * at most 3 positions, and at most 1% aggregate open risk (pending orders count);
  * gross notional at most 1× equity, and net exposure in any one currency at most 0.6×;
  * one position per pair; lots of 1,000 units; profits converted to USD at the bid of
    the conversion pair at that time;
  * simulation equity of 10,000 USD.

## Pass conditions (each account test separately; all must hold)

1. At least 100 trades.
2. Net return above zero after all costs.
3. Profit factor at least 1.2.
4. Sharpe ratio (daily, annualised) at least 0.5.
5. Maximum drawdown better than −5%, and the drawdown halt never fires.
6. Monte Carlo (5,000 resamples of the trade R multiples at 0.25% per R): the
   probability of a drawdown of 5% or more is at most 25%.
7. At least 60% of calendar half-years are positive.
8. Net return above zero at **2× costs**: double the spread, the slippage and the
   commission, with the same decisions.
9. Stationary bootstrap p-value for a positive mean trade R (5,000 simulations, mean
   block 5) passes Holm's correction at 5% across the two account tests.

**Secondary results (reported, never gates):**
* results by pair, by year and by New York hour;
* the first and second half of the period;
* exit reasons, average win and average loss;
* ±20% sensitivity on the band width, the stop in ATR, the RSI levels and the ADX
  filter.

## Decision rule

* **An account test passes:** an Exness **demo** paper test (orders disabled), with at
  least 3 months and 100 trades, before anything else. Live trading needs the owner's
  explicit authorization for the forex market.
* **Nothing passes:** the night scalper is recorded as failed, and there is no re-tuning
  on this data.
* **A harness bug found after the run:** it is fixed and the test re-run. Both results
  are reported.

## Results (single run, 2026-11-27; harness commit c958dd9)

* **Data.** No month missing.
  * Standard: 13 symbols × 135 months.
  * Raw Spread: 13 × 82 months.
  * All-day median spreads, Standard: majors 1.0–2.2 pips, crosses 1.6–3.8. Raw Spread:
    0–1.1 pips, plus commission.
  * Files: `results/night_scalper_2026-11_*_quality.json`.
* **Verdict: FAILED on both accounts.** No re-tuning will follow.

  | Account | Trades | Win rate | Per trade | Profit factor | Net | Drawdown halt |
  |---|---|---|---|---|---|---|
  | Standard (2015–2026) | 403 | 29.3% | −0.34R | 0.59 | −5.0% | April 2016 |
  | Raw Spread (2020–2026) | 1,014 | 34.2% | −0.29R | 0.80 | −3.7% | September 2021 |

  Both failed every return, drawdown, consistency, cost-stress and significance gate.
  The p-value was 1.00 on both.
* **No ±20% sensitivity variant had a positive expectancy.**
* **Diagnostic (not a gate):** the same rules with the halts switched off, to see every
  year.
  * Standard: 7,540 trades at −0.42R, with **every year from 2015 to 2026 negative**.
  * Raw Spread: 4,557 trades at −0.30R, with every year from 2020 to 2026 negative.
* **Checks.** Sample trades were traced against the raw bars: fills, stops (plus
  slippage) and targets are correct.
* **Why it loses.** After a sharp evening move, the middle band is often far away (up to
  3–4× the stop distance in the traced trades), and the 2 ATR stop is hit first in most
  trades. Night moves that reach a band extreme tend to keep going rather than snap back.
* **Decision.** The night scalper is recorded as failed. It gets no demo or live order
  path.
