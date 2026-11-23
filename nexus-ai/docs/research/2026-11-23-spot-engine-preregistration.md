# Pre-registration: spot strategy engine (Trend Pullback, Breakout + Retest)

Committed BEFORE any run on market data. The code is tested on synthetic data only:
`tests/unit/test_spot_engine.py`. Results are appended once, unedited.

## Strategies, regime and risk (fixed parameters = the defaults in code)
* **Trend Pullback:** `nexus_strategies.spot_strategies.trend_pullback`, with
  `TrendPullbackParams()`.
  * 15m entry; 1h and 4h EMA200 filters; ADX >= 20; 1h structure not down; BTC filter.
  * Pullback into the EMA20-50 zone or to the last 1h swing low.
  * RSI recovering above 50; a bullish candle; volume >= 1.2x.
  * Not more than 1 ATR above EMA20; at least 2R of room to the 1h resistance.
* **Breakout + Retest:** `breakout_retest`, with `BreakoutParams()`.
  * 1h bars; a 24-bar consolidation narrower than 4 ATR.
  * A breakout close with volume >= 1.5x the 20-bar average and a body >= 50% of the bar.
  * An extension of at most 1.5 ATR; a retest within 6 bars; at least 2R of room.
* **Regime:** `nexus_strategies.spot_regime.classify_spot`, with `RegimeConfig()`.
* **Risk and exits:** `nexus_risk.spot_rules`, with `SpotRiskConfig()`.
  * 0.25% risk per trade; 1% aggregate open risk; 2% daily and 4% weekly drawdown halts
    (the brief asked 5% weekly; the 4% ceiling applies).
  * 3 positions; 20% allocation; a 24 h cooldown after 3 losses; at most 2 positions per
    correlated cluster (correlation >= 0.8).
  * TP1 at 1.5R closes 50%, then the stop moves to break even including fees; TP2 at 3R;
    no trailing.
* **Execution:** `nexus_backtest.spot_engine` with `EngineConfig()`.
  * 0.1% taker and maker fees; spread 0.5 bp for BTC and ETH, 2 bp for the others.
  * Slippage: 3 bp plus square-root impact.
  * At most 5% of a bar's volume; a 1% random rejection rate (seed 7); Binance's real step
    and minimum-notional filters; 10,000 USDT starting capital.

## Data
* Binance spot 15m archives (data.binance.vision), every file checked against its
  SHA-256. 1h and 4h bars are built from complete groups of 15m bars.
* **Universe (fixed now):** BTC, ETH, BNB, SOL, XRP, ADA, DOGE, LINK, AVAX and LTC
  against USDT. These are liquid pairs listed on Binance before 2022. Choosing today's
  survivors adds a small survivorship bias, which is stated, not corrected.
* Warm-up from 2022-09-01. **Evaluation 2022-11-01 to 2026-11-22.**
* Chronological split (parameters are fixed, so nothing is fitted):
  * **train** 2022-11-01 to 2024-05-01: parameter-sensitivity report only;
  * **validation** 2024-05-01 to 2025-07-01;
  * **test (out of sample)** 2025-07-01 to 2026-11-22.

## Gates, per strategy (the brief's suggested targets). All required.
1. **Full period:** profit factor >= 1.30, net expectancy > 0, max drawdown <= 10% (the
   engine run with that strategy alone), at least 200 trades.
2. **Test period:** profit factor >= 1.30 and expectancy > 0.
3. **No big out-of-sample drop:** test profit factor >= 0.75 x train profit factor.
4. **Not one coin:** among symbols with >= 10 trades, at least 60% have PF > 1; without
   the best symbol, PF >= 1.10.
5. **Not one regime:** net profit > 0 in at least 2 regimes with >= 10 trades each.

Reported, not deciding:
* the combined engine;
* the BTC buy-and-hold benchmark;
* Monte Carlo and bootstrap 95% confidence intervals;
* monthly, per-symbol and per-regime tables;
* sensitivity on the train period (each parameter changed one step).

## What a result means
* **Pass:** the strategy is eligible for the 180-day paper validation, with its own
  pre-registered review.
* **Fail:** the strategy still runs in paper so its signals and rejections can be observed,
  but it is marked "failed backtest" and is never eligible for live.
* Live trading stays disabled either way.

## Results (single run, 23 Nov 2026, after commits 9c97bb1, 73cb532 and 5d05f0b)

Data: 720 archive files, all SHA-256 verified. 148,219 15m bars per symbol. 5 bars per
symbol are missing in Binance's own archive; they stay missing and are never filled.
Full output: `2026-11-23-spot-engine-result.json`.

### Decision: both strategies FAIL

| | Trend Pullback | Breakout + Retest |
|---|---|---|
| Trades (full period) | 322 | 270 |
| Profit factor (needs >= 1.30) | **0.59** (95% CI 0.45-0.76) | **0.71** (95% CI 0.54-0.92) |
| Expectancy per trade | -4.50 USDT, -0.42 R (CI entirely < 0) | -4.01 USDT, -0.22 R (CI entirely < 0) |
| Net return, 10,000 USDT start | -14.5% | -10.8% |
| Max drawdown (needs <= 10%) | 15.3% | 13.1% |
| Win rate | 36% | 38% |
| Profit factor: train / validation / test | 0.60 / 0.71 / 0.46 | 0.74 / 0.59 / 0.79 |
| Symbols with PF > 1 | 1 of 10 (DOGE) | 2 of 10 (DOGE, ETH) |
| Fees / slippage paid | 1,062 / 463 USDT | 694 / 284 USDT |
| Exposure (time with a position) | 3.4% | 9.4% |
| Longest losing streak | 16 | 13 |

**Failed gates (both strategies):**
* full-period PF, expectancy and drawdown;
* test-period PF and expectancy;
* not one coin;
* not one regime.

**Passed gates:** at least 200 trades, and no out-of-sample drop. They are equally poor
in every period.

**Benchmark:** BTC buy-and-hold over the same period returned +194% (max drawdown 56%,
Sharpe 0.80). The combined engine returned -21.9% (PF 0.66, max drawdown 23.8%).

**Train-period sensitivity (one parameter changed one step at a time):** every variant
also had PF < 1:
* Trend Pullback: 0.47 to 0.67;
* Breakout + Retest: 0.69 to 0.92.

No nearby setting rescues either strategy, so the losses are not a tuning accident.

**Why (from the decision log):**
* Most Trend Pullback candidates are rejected by the 4h trend filter (25,873 of 48,883).
  Of those that pass, 84% end at the stop, and only 15% reach TP2.
* With about 0.25% round-trip costs and stops of 1 to 3 ATR on 15m bars, the payoff after
  TP1 and the break-even move does not cover the losers.

### Consequence
* Both strategies are marked "failed backtest" and are not eligible for live use.
* The paper runner still records their signals and paper trades, so their behaviour on new
  data can be observed. That is the brief's 180-day paper validation. It cannot change this
  verdict without a new, separately pre-registered hypothesis.
* Live trading stays disabled.
