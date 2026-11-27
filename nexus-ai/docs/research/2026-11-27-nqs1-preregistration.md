# NQS-1 adaptive forex strategy: pre-registration (2026-11-27)

**Status: registered before any intraday forex data was received.** No NQS-1 backtest
has been run on real prices. The strategy code (`services/strategies/nexus_strategies/nqs1/`),
the harness (`research/backtesting/nexus_backtest/nqs1_bt.py`) and everything below are
frozen at the commit that adds this file. Any later change to the strategy, its
parameters, the gates or the harness needs a new pre-registration, and both results are
reported.

## Question

Does NQS-1 make money out of sample, after costs and carry, on EURUSD, GBPUSD, USDJPY,
AUDUSD and USDCHF?

NQS-1 is an adaptive combination. A regime classifier chooses among four setups, each
entry needs at least two confirmations, and every entry passes cost and expected-value
checks under NEXUS risk limits.

## What we already know (prior evidence)

* Our G10 FX styles test (2026-11-26, `2026-11-26-fx-styles-preregistration.md`): none
  passed.
  * Carry was the closest at +1.13% a year (Sharpe 0.30, p = 0.07).
  * Time-series momentum lost 1.18% a year.
* Our multi-asset trend test (2026-11-26): trend following on FX majors failed.
* Published research finds that simple technical trading rules in major currencies were
  profitable in the 1970s and 1980s, and that these profits largely disappeared later:
  * Olson (2004), *Journal of Banking & Finance* 28(1);
  * Neely, Weller and Ulrich (2009), *Journal of Financial and Quantitative Analysis*
    44(2).
* **Expectation: a pass is unlikely.** The test exists to decide honestly, not to
  confirm a hope.

## Data

* **Source:** the owner's Exness MetaTrader 5 terminal, through the read-only bridge
  (`py -3 nexus_mt5_bridge.py --export-history`).
  * Coverage: M5 for the last 3 years, H1 for the last 12 years, and all D1 history.
  * Prices are the broker's bid bars, converted from server time to UTC with the
    measured offset.
  * M15, H4 and W1 bars are resampled from M5, H1 and D1 (W1 = Monday to Friday).
  * Nothing is generated or filled in.
* **Checks before the run.** These are reported and never used to tune anything:
  * bars per pair;
  * gaps longer than 3 days outside weekends;
  * the UTC hour of each week's first H1 bar, by month.

  If the week's opening hour shifts with daylight saving time in a way the UTC
  conversion did not handle, the run is postponed until the conversion is fixed. That is
  a data fix, not a strategy change.
* **Carry:** the same FRED series as the FX-styles study.
  * Rates: 3-month interbank rates (`IR3TIB01xxM156N`) and the US 3-month bill
    (`DTB3`), as the base rate minus the quote rate.
  * Each value is used for at most 92 days. After that, carry is unknown and not charged.
  * A broker markup of 0.5% a year is charged on both sides, at each 21:00 UTC rollover
    held through. Wednesday counts three days.
* **Minimum history:** a profile runs only with at least 33 months of data (3 months of
  warm-up, 24 of training and 6 of testing). Skipped profiles are reported as skipped.

## Strategy (frozen defaults)

* **Profiles (signal / trend timeframe, maximum hold):**

  | Profile | Signal | Trend | Max hold | Momentum lookback |
  |---|---|---|---|---|
  | Scalping | M5 | M15 | 36 bars | 96 bars |
  | Intraday | H1 | H4 | 48 bars | 120 bars |
  | Swing | D1 | W1 | 30 bars | 126 bars |

* **Indicators:**
  * EMA 20, 50 and 200; RSI 14; MACD 12/26/9; ADX 14; ATR 14;
  * Donchian 20 (previous bars only); Bollinger 20 with 2 standard deviations;
  * 60-bar momentum;
  * 250-bar percentiles of ATR% and of the band width.
* **Regimes** (first match wins; uncertain means no trade):
  1. UNSTABLE: ATR% at or above the 97th percentile, or a bar range of at least 3 ATR.
     No trades.
  2. VOL_EXPANSION: ATR% at or above the 80th percentile, ATR up 15% over 5 bars, and
     ADX at least 18. Setup: breakout.
  3. STRONG_BULL / STRONG_BEAR: ADX at least 25, an aligned EMA stack, price on the
     trend side of EMA 20, and the MACD histogram agreeing. Setup: trend, in the trend's
     direction only.
  4. VOL_CONTRACTION: band width in its lowest 15%. Setup: breakout, needing 3
     confirmations.
  5. SIDEWAYS: ADX below 18 and price inside the channel. Setup: mean reversion.
  6. WEAK_TREND: everything else. No trade.

  INSUFFICIENT_DATA applies during warm-up.
* **Setups:**

  | Setup | Entry | Stop |
  |---|---|---|
  | Time-series momentum | flip of the lookback return's sign | 2.0 ATR |
  | Multi-timeframe trend | higher-timeframe EMA 50/200 with ADX at least 20, a pullback to EMA 20, RSI back through 50 | 1.5 ATR |
  | Breakout | close beyond Donchian by 0.25 ATR after a squeeze | 1.5 ATR |
  | Mean reversion | ADX below 18, channel at most 4 ATR, close outside the band, RSI below 30 or above 70; target the Bollinger middle | 1.0 ATR |

  Time-series momentum, or the momentum sign, adds one confirmation in adaptive mode.
* **Entry checks:**
  * decided at a bar's close, filled at the next bar's open;
  * spread at most 15% of ATR;
  * round-trip cost at most 15% of the stop distance;
  * reward at least 1.5R (mean reversion: at least 1R to the middle);
  * expected value positive after costs, using the training-window win rate of the setup
    shrunk towards 1/2 (Laplace).
* **Intraday session filter:** no new trades decided after Friday 19:00 UTC, at
  weekends, or between 20:00 and 23:59 UTC.
* **Exits** (the stop is assumed first when the stop and the target are both reachable
  in a bar):
  * the ATR stop, filled at the open when a gap jumps it;
  * take profit at 2R, or the Bollinger middle known before the bar for mean reversion;
  * break-even after +1R;
  * a 2.5 ATR trailing stop after +1.5R;
  * the stop tightened to 1 ATR in an UNSTABLE regime;
  * an exit on an opposite signal;
  * a time exit;
  * emergency closure of everything at the drawdown halt.
* **Risk (NEXUS defaults, read from `nexus_config.settings`):**
  * 0.25% of equity per trade; a 1% daily and 3% weekly loss limit;
  * a 5% drawdown halt that stays for the rest of the run;
  * at most 3 positions and at most 1% aggregate open risk (pending orders count);
  * gross notional at most 1× equity, and net notional per currency at most 0.6× equity;
  * size rounded down to 1,000-unit lots.

  There is no martingale, no averaging down and no grid. Simulation equity is 10,000
  USD.
* **Costs** (in pips):
  * spread: EURUSD 0.8, GBPUSD 1.2, USDJPY 1.0, AUDUSD 1.0, USDCHF 1.4;
  * commission: 0.7 round trip;
  * slippage: 0.2 per fill.

  Costs are charged once per trade. The report also lists the spreads the bridge
  observed live.
* **ML filter (secondary only):**
  * logistic regression on entry features;
  * trained only on the training window's trades, and only with at least 150;
  * it can only refuse a trade, when its win probability is below the break-even
    probability plus 2 points.

## Walk-forward

* Rolling windows: 24 months of training, then the next 6 months of testing. The test
  periods are consecutive and never overlap.
* For each window, a grid of 27 points (stop scale 0.8 / 1.0 / 1.25 × take profit
  1.5R / 2R / 3R × ADX strong threshold 22 / 25 / 28) is fitted on the training period.
* The chosen point has the best training expectancy with at least 30 training trades;
  otherwise the defaults are used.
* All test periods are traded as **one continuous book** with each period's chosen
  settings, so halts and open positions carry over.

## Pass conditions (all must hold, per profile, adaptive mode, out of sample)

1. At least 100 trades.
2. Net return above zero after costs and carry.
3. Profit factor at least 1.2.
4. Sharpe ratio (daily, annualised) at least 0.5.
5. Maximum drawdown better than −5%, and the drawdown halt never fires.
6. Monte Carlo (5,000 resamples of the trade R multiples at 0.25% per R): the
   probability of a drawdown of 5% or more is at most 25%.
7. At least 60% of the 6-month test windows are positive.
8. Net return above zero at **2× costs** (same decisions, double the charge).
9. Stationary bootstrap p-value for a positive mean trade R (5,000 simulations, mean
   block 5) passes Holm's correction at 5% across the profiles actually run.

## Secondary results (reported, never gates)

* Each setup alone and the adaptive combination, at default parameters, on the same
  out-of-sample span.
* The walk-forward with the ML filter.
* Sensitivity of ±20% on each tuned parameter.
* Results by regime, setup, pair and year.
* Exit reasons and rejection counts.

## Decision rule

* **A profile passes:** it may start a forward test on an **Exness demo account**, paper
  only, with live orders disabled, for at least 3 months and 100 trades.
  * Live trading needs the owner's explicit per-market authorization, and a separate
    review. NEXUS never enables it automatically.
* **Nothing passes:** NQS-1 stays in RESEARCH. There is no re-tuning on the same data,
  and the result is recorded as it is.
* **ML filter:** it can be adopted only if the base strategy passes and the filter
  improves out-of-sample expectancy.
* **A harness bug found after the run:** it is fixed and the test re-run. Both results
  and the bug are reported.

The output is `results/nqs1_<date>.json` and a generated markdown comparison report.

## Amendment 1 (2026-11-27, before any NQS-1 run on real data)

**Data source changed.** The owner asked for the test to be done without their PC.
Exness publishes its own historical ticks at `https://ticks.ex2archive.com`: bid and
ask, UTC timestamps, one file per symbol and month. The test now uses that archive
instead of the bridge export. The broker is the same, and the history is longer.

* **Symbols:** the Standard-account symbols EURUSDm, GBPUSDm, USDJPYm, AUDUSDm and
  USDCHFm, for the complete months August 2015 to October 2026.
* **Bars:** built by `nexus_backtest.exness_ticks` (committed with this amendment).
  * M5 bars of the bid price; slots without ticks have no bar.
  * H1 and D1 bars are resampled from the M5 bars. D1 days are UTC days, as on Exness's
    GMT+0 MT5 server.
  * M15, H4 and W1 bars are resampled by the harness, as before.
* **Coverage:** all three profiles use the full span. The "3 years of M5" limit was the
  bridge export's limit, not a test choice.
* **Costs: unchanged.** The pre-registered spread, commission and slippage table stays
  the primary assumption.
  * Spreads observed in the ticks are reported in `quality.json`.
  * Only one month (EURUSD, January 2024) was opened to check the format: median
    spread 1.0 pip on the Standard account, which has no commission.
* **Data checks:** as pre-registered (`quality.json`).
* **Unchanged:** the strategy, its parameters, the walk-forward, the gates and the
  decision rule.

## Amendment 2 (2026-11-27, memory only; no change to any decision)

* **Change.** Before the scalping (M5) profile ran, the simulator was changed to keep
  per bar only the 15 columns it reads (`ROW_COLS` in `engine.py`), not all 45.
* **Why.** One 2-year M5 job needed 3.3 GB, and the 9-year out-of-sample book would have
  needed about 11 GB of the container's 15 GB.
* **Check.** The swing and intraday profiles had already run. Both were re-run after the
  change, and both result files are identical (every field except the generation time).

## Results (single run, 2026-11-27; harness commits cbbaf2d / 6fac6ff)

* **Data.** Exness's tick archive: 675 month files (5.2 GB, 827 million ticks), August
  2015 to October 2026, with no month missing.
  * The data checks passed. The week opens on Sunday at 21:00 UTC in US summer time and
    22:00 in winter, so the timestamps are true UTC.
  * The gaps longer than 3 days are the year-end holidays plus two archive holes
    (September 2015 and late January 2016), left empty.
  * Median Standard-account spreads, in pips: EURUSD 1.0, GBPUSD 1.2, USDJPY 1.1,
    AUDUSD 1.5, USDCHF 1.5.
  * Files: `results/nqs1_2026-11_data_quality.json` and
    `nqs1_2026-11_data_provenance.json` (SHA-256 of every file).
* **Verdict: NQS-1 FAILED in all three profiles.** No re-tuning will follow.

  | Profile (out of sample) | Trades | Win rate | Per trade | Profit factor | Net | Worst drop | p |
  |---|---|---|---|---|---|---|---|
  | Scalping (M5) | 113 | 38.1% | −0.04R | 0.96 | −0.24% | −1.5% | 0.62 |
  | Intraday (H1) | 519 | 35.8% | −0.01R | 0.95 | −1.7% | −4.8% | 0.56 |
  | Swing (D1) | 48 | 27.1% | −0.15R | 0.71 | −1.5% | −2.4% | 0.84 |

  Every profile failed: net above zero, profit factor 1.2, Sharpe 0.5, 60% positive
  windows, 2× costs and significance. Swing also had fewer than 100 trades.
* **Each setup alone did worse than the combination.** At default parameters, out of
  sample:
  * every single setup lost money;
  * most hit the 5% drawdown halt;
  * the one exception, M5 mean reversion, made 14 trades: too few to mean anything.
* **The ML filter did not help.**
  * Intraday: −0.03R against −0.01R without it.
  * Scalping and swing never had 150 training trades, so the filter was not used.
* **Sensitivity.** No ±20% variant had a positive expectancy in any profile.
* **Risk control worked.** The adaptive combination never hit the 5% drawdown halt, and
  its worst drop was −4.8%. The single setups without regime selection did hit it.
* **Observed after the fact, NOT a result.** Intraday breakouts in VOL_CONTRACTION
  averaged +0.12R (255 trades), and those in VOL_EXPANSION −0.16R (247 trades).
  * Selecting that subset now would be data snooping.
  * It could only become a new hypothesis, tested on data not used here (a forward
    paper test).
* **Decision.** NQS-1 stays in RESEARCH. It gets no demo or live order path.
