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

_(appended after the single run)_
