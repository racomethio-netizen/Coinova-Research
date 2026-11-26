# Pre-registration: Freqtrade community strategies, out-of-sample test

_Committed before any run. Results are appended once below the line; nothing above it
changes afterwards._

## Why

The owner asked why NEXUS does not simply use the strategies other bots use. This test
takes the public Freqtrade strategy collection and runs every eligible strategy
unchanged, on data it was not built on, with real fees. The question it answers: which
of them actually made money?

## Source

* **Repository:** `github.com/freqtrade/freqtrade-strategies`, commit
  `f3340ce11f5bdf62f598522e64d1f5638eaa13f5` (2026-09-08), 68 strategy files.
* **License:** GPL-3.0. The strategy files are downloaded into a scratch directory at run
  time and are **not** copied into this repository. Only the harness and the results are
  committed.
* **Engine:** Freqtrade 2026.9 itself, its own backtester. Each strategy runs as its
  author wrote it: its ROI table, stop-loss, trailing stop and timeframe. Nothing is
  re-implemented and no parameter is changed.

## Eligible strategies

All files under `user_data/strategies`, except:
* `lookahead_bias/`: published as deliberate examples of look-ahead bias;
* `futures/` and any strategy with `can_short = True`: futures are disabled in NEXUS;
* `TWAPStrategy`, `AlmgrenChrissStrategy`: execution algorithms, not trading signals;
* `DoesNothingStrategy`: a template;
* 1-minute strategies: no 1m history in this test (listed as not tested);
* any strategy that fails to load or run on Freqtrade 2026.9 (listed with the error).

## Data

* **Pairs (10 spot, against USDT):** BTC, ETH, BNB, SOL, XRP, ADA, DOGE, LINK, AVAX, LTC.
* **Source:** official 5-minute klines from `data.binance.vision`, monthly archives,
  SHA-256 verified where a checksum is published, 2023-06 to 2026-10. 15m, 30m, 1h, 4h,
  12h and 1d bars are resampled from 5m (left-closed, labelled by open time, as Binance
  does).
* A missing month aborts the run. Nothing is filled.

## Out-of-sample window

* Strategies first published before 2024: **2024-01-01 to 2026-10-31**.
* Strategies first published later: from the first day of the month after publication to
  2026-10-31, flagged as a short window.
* Strategies whose logic or parameters changed inside their window are flagged.
  * Changed: Supertrend (indicator replaced by a library version, 2026-01 and 2026-08),
    TrendRiderStrategy, FAdxSmaStrategy (futures; excluded anyway).
  * Formatting, typo and library-compatibility commits are not counted as changes.

## Backtest settings (identical for all)

* Starting wallet: 10,000 USDT; stake: an equal split of the wallet; at most 3 open
  trades.
* Fee: **0.1% per side** (Binance spot taker without BNB). Rerun at **0.2%** (2×) for the
  cost-stress gate.
* No hyperopt and no parameter search: the published defaults only.

## Gates (a strategy passes only if all hold, at 0.1% fees)

1. Net profit > 0.
2. Profit factor ≥ 1.2.
3. Maximum drawdown of the account ≤ 25%.
4. At least 50 trades.
5. Net profit positive in at least 2 of 3 periods: 2024, 2025 and 2026 year to date.
6. Net profit still > 0 at 0.2% fees per side.
7. **Multiple testing.** The mean per-trade return must be significantly > 0: bootstrap
   p-value (10,000 resamples of the trades), Holm-corrected across ALL strategies tested,
   at 5%.

## Reported, not gated

Also reported for every strategy:
* trades, win rate, average duration, total fees, exposure;
* Sharpe and Sortino (daily);
* the best and worst pair.

Benchmarks over the same window: BTC buy-and-hold, and an equal-weight hold of the 10
pairs.

## Consequence

* **Pass.** The strategy is added to the NEXUS paper forward test (spot, long-only, behind
  the risk engine). It goes live only with the owner's approval.
* **Fail.** Recorded, with the failed gates, and shown in the app. No re-tuning.

---

## Results

_Single run on 2026-11-26 after the pre-registration commit faf1829.
* Full output: `research/backtesting/nexus_backtest/results/freqtrade_community_2026-11.json`
  (also served at `GET /api/v1/research/freqtrade-community` and shown on the dashboard
  page **Strategy test**).
* Runner: `nexus_backtest.freqtrade_community`._

**No strategy passes (0 of 44 tested). Six made money before the checks; 38 lost money.**
As pre-registered, nothing is added to the paper forward test.

### Run notes (method, not results)

* **Data.** 410 monthly archives, all SHA-256 checksums matched. 359,712 five-minute bars
  per coin, 0 gaps.
* **Market rules.** Freqtrade needs Binance's `exchangeInfo` at start-up.
  * `api.binance.com` is region-blocked from the test machine (HTTP 451) and was not
    bypassed.
  * The identical document was fetched once from Binance's public market-data host
    `data-api.binance.vision` and served from a file (`nexus_backtest.freqtrade_offline`),
    so the backtests made no exchange calls.
* **Not run.**
  * 20 not eligible, as pre-registered: 7 futures / short, 6 one-minute, 4 look-ahead
    examples, 3 execution algorithms or templates.
  * 4 failed to load and are listed with the error:
    * GodStra, Heracles and multi_tf import the `ta` library, which Freqtrade 2026.9 does
      not install;
    * FixedRiskRewardLoss sets no timeframe.

### Benchmarks over the same window (2024-01 to 2026-10)

* BTC buy-and-hold: **+83.5%**.
* Equal-weight hold of the 10 coins: +9.9%. BNB +126% and XRP +103%; ADA −68% and AVAX
  −78%.

### Top of the ranking (all failed)

| Strategy | Bars | Net | Worst drop | Profit factor | Trades | At 2× fees | Failed checks |
|---|---|---|---|---|---|---|---|
| ADXMomentum | 1h | +177.9% | −36.5% | 1.10 | 3,356 | −57.1% | PF < 1.2, drawdown, 2× fees, Holm |
| MultiMa | 4h | +130.7% | −26.4% | 1.53 | 249 | +72.2% | drawdown, only 2024 positive (2025 −3,348 USDT), Holm |
| UniversalMACD | 5m | +89.7% | −32.9% | 1.77 | 366 | +68.9% | drawdown −32.9%, Holm (p 0.00120 vs 0.00119 needed: missed by a hair) |
| ReinforcedAverageStrategy | 4h | +64.8% | −30.3% | 1.21 | 290 | +37.1% | drawdown, Holm |
| CustomStoplossWithPSAR | 1h | +55.6% | −16.3% | 4.42 | 6 | +55.2% | only 6 trades, Holm |
| AverageStrategy | 4h | +47.8% | −57.0% | 1.08 | 585 | +1.9% | PF, drawdown, years, Holm |

### What the run shows

1. **Most public strategies lose money on new data.**
   * The median net result is **−64%**.
   * 32 of 44 lost more than half the starting capital and 11 lost more than 90%.
   * The fast, high-turnover ones (10,000+ trades) went to almost zero, mostly in fees.
2. **Even the winners would have been hard to hold.**
   * Only 3 beat simply holding BTC (+83.5%): ADXMomentum, MultiMa and UniversalMACD.
   * Each of those fell more than 25% along the way.
   * ADXMomentum's profit disappears at double fees: it wins 86% of trades for tiny gains
     and loses big on the rest.
   * MultiMa earned everything in 2024 and lost in 2025 and 2026.
3. **UniversalMACD is the closest to passing.**
   * It was positive in all three years, profit factor 1.77, still +69% at double fees.
   * It failed on drawdown (−32.9% vs the 25% limit).
   * It missed multiple-testing significance by a hair: bootstrap p 0.00120 vs the Holm
     threshold of 0.05 / 42 = 0.00119.
   * It may be re-examined only under a **new** pre-registration with fresh data (for
     example forward paper trading). The rules here are not loosened after the fact.
4. **Why commercial bots look better than this.** Advertised results are usually in-sample
   or survivor examples. On data they were not built on, public strategies behave like
   this table.
