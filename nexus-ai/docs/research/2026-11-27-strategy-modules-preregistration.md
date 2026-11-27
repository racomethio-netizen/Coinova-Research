# Pre-registration: the six strategy modules (unified brief, Phase 3)

Committed BEFORE any run on market data. The code was tested only on synthetic data
before this file was committed: no look-ahead, simulator fills, and the harness end to
end. The results are appended once, unedited. Nothing below changes after the run.

Owner request: the "PROJECT NEXUS" unified brief, section C (six strategy modules, each
with its own configuration, backtest, performance tracking and activation), then "Phase 3".

## What is tested

| # | Module | Market | Status in this test |
|---|---|---|---|
| 1 | Forex scalping momentum pullback | EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF | **new rule**: can pass |
| 2 | Forex intraday trend breakout | same 5 pairs | **new rule**: can pass |
| 3 | Forex swing trend following | same 5 pairs | **new rule**: can pass |
| 4 | Crypto scalping volume momentum | BTCUSDT, ETHUSDT (spot, long only) | **new rule**: can pass |
| 5 | Crypto intraday breakout retest | BTCUSDT, ETHUSDT (spot, long only) | existing rule (spot engine), **FAILED on 23 Nov 2026** (profit factor 0.71). Re-run in the module framework for the record only; it cannot pass here and the earlier verdict stands. |
| 6 | Crypto swing trend momentum | BTCUSDT, ETHUSDT (spot, long only) | existing rule "Catching Crypto Trends", **PASSED on 23 Nov 2026** and runs as the trend-paper forward test. Re-run with its own unchanged harness as a replication check only. |

Prior evidence: similar ideas have mostly failed in NEXUS's earlier tests:

* crypto trend pullback and breakout/retest (23 Nov);
* G10 FX styles (26 Nov);
* NQS-1 (27 Nov);
* the night scalper (27 Nov).

A failure here is the expected outcome and will be reported as such.

## Rules (code: `services/strategies/nexus_strategies/modules/`)

Common to modules 1 to 5:

* **Decision timing.** A decision is made at a bar's close, using only that bar and
  earlier ones. Higher timeframes contribute closed bars only (`frames.align_higher`).
* **Entry.** A market order at the OPEN of the next execution bar. If that bar opens more
  than the module's maximum delay after the signal (5 minutes; module 3: 3 days), the
  signal is dropped.
* **Stop and target.** The stop is fixed at the signal. The target is
  `entry ± 2.0 × |entry − stop|` from the actual fill, so the planned reward/risk is
  exactly 2:1, the brief's minimum.
* **Position limits.** One position per symbol, at most 3 open per module, 0.25% of
  equity risked per trade.
* **No extras.** No partial exits, no trailing and no averaging.

**1. Forex scalping momentum pullback** (`FxScalpConfig`). Execution on M5.

* **When:** the signal bar closes between 07:00 and 15:00 UTC, Monday to Friday.
* **Long:** all of these must hold:
  * H1 trend: H1 close > EMA50 and EMA20 > EMA50 (last closed H1 bar);
  * M5 trend: EMA20 > EMA50;
  * in the previous 6 M5 bars, a low touched EMA20 and RSI14 was ≤ 45;
  * the bar closes above EMA20 and above the previous bar's high, with RSI14 > 50.
* **Short:** the mirror image (RSI ≥ 55 on the pullback, < 50 on the trigger).
* **Stop:** beyond the extreme of the last 7 bars ± 0.2 ATR14(M5).
  * It is at least 1.0 ATR away; signals needing more than 3.0 ATR are skipped.
  * The bar's mean spread must be ≤ 15% of the stop distance.
* **Time stop:** 36 bars (3 hours).

**2. Forex intraday trend breakout** (`FxBreakoutConfig`). Execution on M5.

* **Asian range:** the high and low of the M5 bars opening 00:00 to 07:00 UTC (at least
  60 of 84 bars).
* **Trend:** from the last closed FX day (22:00 to 22:00 UTC). Long only if its close >
  EMA50(D1); short only if its close < EMA50.
* **Range filter:** the range must be between 0.25 and 1.0 × ATR14(D1).
* **Trigger:** the first M5 bar opening 07:00 to 11:00 UTC that closes beyond the range in
  the trend direction. One attempt per pair per day.
* **Stop:** the middle of the range. The spread must be ≤ 15% of the risk.
* **Exit:** a clock exit at 20:00 UTC.

**3. Forex swing trend following** (`FxSwingConfig`). Signals on the FX day; execution on
H1.

* **Long:** EMA50 > EMA200 (D1) and the close is above the highest high of the previous
  20 days.
* **Short:** the mirror image.
* **Stop:** 2.0 × ATR20(D1) from the signal close.
* **Time stop:** 40 days (960 H1 bars).

**4. Crypto scalping volume momentum** (`CryptoScalpConfig`). Execution on 5m, long only.

* **Trend:** the 1h close > EMA50 and EMA20 > EMA50.
* **Trigger:** all of these on the 5m bar:
  * volume ≥ 3 × the median of the previous 48 bars;
  * the close is above the highest high of the previous 12 bars;
  * the close is in the top 25% of the bar's range;
  * the bar closes up.
* **Stop:** the bar's low − 0.25 ATR14.
  * It is at least 1.0 ATR away; signals needing more than 4.0 ATR are skipped.
  * The round-trip cost of 0.30% of the price must be ≤ 25% of the risk.
* **Time stop:** 48 bars (4 hours).

**5. Crypto breakout retest** (`CryptoBreakoutRetestConfig`): the unchanged rule
`spot_strategies.breakout_retest` with `BreakoutParams` defaults.

* Applied on 1h bars, with `classify_spot` on 1h and the BTC 4h risk-off filter for ETH.
* Entries come from its stop; the target is 2R and the time stop 48 hours. Execution on 5m.

**6. Crypto swing trend momentum:** `nexus_backtest.crypto_trends` with `SimConfig()`
defaults, exactly as pre-registered on 23 Nov 2026.

## Data

* **Forex:** Exness Standard-account ticks from Exness's public archive
  (`ticks.ex2archive.com`, provenance file with SHA-256 per month).
  * The ticks are turned into M5 BID bars with each bar's mean spread
    (`exness_ticks`, built 27 Nov 2026).
  * Coverage: Aug 2015 to Oct 2026. Evaluation: signals from 1 Jan 2016; Aug to Dec 2015
    is indicator warm-up only.
  * Bars with impossible OHLC values or a negative spread are dropped and counted.
* **Crypto:** Binance spot 5m klines, Jan 2021 to Oct 2026, from data.binance.vision.
  * Every monthly file must match Binance's SHA-256, or the run stops.
  * Evaluation: signals from 1 Mar 2021 (Jan to Feb 2021 is warm-up). Missing bars stay
    missing and are counted.
* **Module 6:** Binance daily archives, BTCUSDT and ETHUSDT, as on 23 Nov.

## Costs (`nexus_backtest.modules_bt`, `nexus_strategies.modules.sim`)

* **Forex:**
  * a buy pays the ask (bid + the bar's mean spread); a long's stop is checked on bid
    prices, a short's on ask prices;
  * slippage of 0.1 pip on every market fill (entries, stops, time and clock exits);
  * a target fills at its price, never better;
  * a stop that the market gaps through fills at the open;
  * a bar touching both stop and target counts as a stop;
  * swap at the 21:00 UTC rollover (Wednesday ×3): the FRED 3-month rate differential
    minus a 0.5% a year markup. Where the rate is unknown, nothing is charged, and the
    report says how many trades that affects.
* **Crypto:**
  * a 0.10% fee per side;
  * 0.05% slippage on market fills (entries, stops, time exits);
  * the target is a resting limit order (fee only).

## Gates (modules 1 to 4; all required)

1. Enough trades: at least 200 (module 3: at least 60).
2. Mean net R per trade > 0 (positive expectancy after costs).
3. Significant after Holm's correction across the 4 new modules at 5%:
   * the test is one-sided;
   * the p-value is a stationary bootstrap of the trade R sequence
     (`significance.stationary_bootstrap_pvalue`, 5,000 simulations, block 10, seed 0).
4. Profit factor ≥ 1.15.
5. Mean net R > 0 in both halves of the evaluation period, split at its midpoint by entry
   time.
6. Mean net R > 0 at 2× costs (spread, slippage, fees and swap doubled; same trades).
7. Maximum drawdown ≤ 10% when each trade risks 0.25% of equity, compounded in exit order.

Reported but not deciding: per pair or coin, per year, exit reasons, win rate, swap share.

## What a pass allows

A pass allows only:
* a forward **observation** record (signals and their hypothetical results on new data);
* later, after Phases 5 to 7 (orchestrator, cross-market risk, execution adapters), a
  separately pre-registered forward **paper** test.

It never allows real-money trading. Live trading stays disabled until the owner approves
it manually (brief acceptance rules).

A fail is recorded. The module stays in the registry with its activation OFF by default.
The owner may switch it to OBSERVE (record signals, no orders) but not to paper trading.

## Results (single run, 27 Nov 2026 20:52 UTC, after commit f7d0c6f)

Output: `research/backtesting/nexus_backtest/results/strategy_modules_2026-11.json` (and
`.md`).

Data checks:
* Forex: 5 pairs, about 833,000 M5 bars each; 0 bars dropped; swap rates known for every
  pair.
* Crypto: 70 monthly files per coin, all matching Binance's SHA-256; 213 bars missing in
  Binance's own archive, left missing.

Evaluation: forex 1 Jan 2016 to 30 Oct 2026; crypto 1 Mar 2021 to 31 Oct 2026.
Historical simulation only; no order was placed.

### Decision: all four new modules FAIL

| Module | Trades | Win rate | Mean R (net) | Profit factor | Mean R at 2x costs | Max drawdown at 0.25% | p-value | Result |
|---|---|---|---|---|---|---|---|---|
| 1 Forex scalping momentum pullback | 6,825 | 35.3% | −0.103 | 0.83 | −0.206 | 83.5% | 1.00 | **FAIL** |
| 2 Forex intraday trend breakout | 4,222 | 35.1% | −0.093 | 0.85 | −0.168 | 64.5% | 1.00 | **FAIL** |
| 3 Forex swing trend following | 281 | 32.0% | −0.130 | 0.80 | −0.166 | 10.0% | 0.98 | **FAIL** |
| 4 Crypto scalping volume momentum | 240 | 38.3% | −0.117 | 0.81 | −0.288 | 7.5% | 0.92 | **FAIL** |
| 5 Crypto breakout retest (re-run) | 91 | 40.7% | −0.046 | 0.94 | −0.240 | 3.1% | 0.68 | not eligible; the 23 Nov FAIL stands and this agrees |

Failed gates:
* Modules 1 and 2: positive expectancy, significance, profit factor, both halves, 2x
  costs and drawdown.
* Modules 3 and 4: every gate except trade count and drawdown.
* No module had a positive mean in both halves. Module 1 had one positive year out of
  eleven (2020); module 2 had none.

**Module 6, crypto swing trend momentum (replication):** PASS on all six gates again.
Sharpe 1.11, max drawdown 15.6%, CAGR 14.1% (Aug 2018 to Nov 2026), unseen period
2022-11 to 2024-11 +44%. This is identical to the 23 Nov result; it stays the one module
with evidence, and its forward paper test (trend-paper) continues.

### Diagnostic added after the run (not a gate; cannot change a verdict)

The same trades with every cost at zero
(`results/strategy_modules_before_costs_2026-11.json`):

| Module | Mean R before costs | Mean R after costs |
|---|---|---|
| 1 Forex scalping | +0.012 | −0.103 |
| 2 Forex intraday breakout | −0.012 | −0.093 |
| 3 Forex swing | −0.109 | −0.130 |
| 4 Crypto scalping | +0.074 | −0.117 |
| 5 Crypto breakout retest | +0.289 | −0.046 |

What this shows:
* The forex rules have no edge even before costs. The Standard account's spread then
  turns them clearly negative: about 0.1R per trade for scalping and breakout.
* The two crypto rules show a small gross edge that Binance's fees and slippage more
  than remove.
* A future hypothesis would have to start from lower costs (maker-only entries, a raw-
  spread account) or longer holding periods. It must be pre-registered separately; these
  rules are not re-tuned.

### Consequences

* No module is enabled. All six stay in the registry with activation OFF.
* The owner may switch any module to OBSERVE (signals recorded, no orders). PAPER is
  refused, because no new module passed.
* Module 6's forward paper test is unchanged.
* Live trading stays disabled.
