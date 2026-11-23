# Pre-registration: global strategy library (8 strategies × 3 timeframes)

_Committed before any of these strategies is run on market data. Results are appended
once, below the line, and the rules above it are not changed afterwards._

## Hypothesis

Standard public indicator strategies earn a positive expectancy on Binance spot **after
fees, spread and slippage**. They are taken from TradingView built-in templates,
Freqtrade's sample strategy and the Turtle channel breakout, each with its own entry,
exit, ATR stop, target and time barrier. The tests run on 4h and daily bars, where costs are
small relative to moves, as well as on 1h bars.

Prior evidence is against it: 18 intraday rules failed earlier NEXUS tests. Only daily trend
following (Crypto Trends) passed.

## Strategies (code: `nexus_strategies/library_global.py`, parameters fixed)

| Code | Family | Entry | Exit | Stop / target / time |
|---|---|---|---|---|
| ema_adx_macd_trend | trend | EMA20 > 50 > 200, close > EMA20, ADX ≥ 25, MACD crosses above signal, HTF close > HTF EMA50 | close < EMA50 or MACD crosses down | 2.5 ATR / 5 ATR / 60 bars |
| rsi_macd_roc_momentum | momentum | RSI crosses above 55, MACD hist > 0 and rising, ROC(12) > 0, volume > 1.5× 20-bar mean | RSI < 45 | 2 ATR / 4 ATR / 30 |
| donchian_breakout | breakout | close > previous 20-bar high, volume > 1.5×, HTF up | close < previous 10-bar low | 2 ATR / 6 ATR / 60 |
| bollinger_squeeze_breakout | breakout | BB width in lowest 20% of 120 bars within the previous 5 bars, close > upper band, volume > 1.5× | close < middle band | 2 ATR / 4 ATR / 40 |
| atr_expansion_breakout | breakout | body > 1.5× previous ATR, close in top 25% of range, close > EMA50, volume > 2× | close < EMA20 | low − 0.5 ATR / 3 ATR / 20 |
| bb_rsi_reversion | mean reversion | ADX < 20; previous close < lower BB with RSI < 30; this bar closes up | close ≥ middle band | 1.5 ATR / middle band / 20 |
| vwap_deviation_reversion | mean reversion | z(close − 50-bar VWAP) < −2, RSI < 35, ADX < 25, close > previous close | z ≥ 0 | 1.5 ATR / VWAP / 20 |
| mtf_structure_trend | structure | HTF up, higher confirmed swing low, close crosses above last confirmed swing high (3-bar pivots, known 3 bars later) | close < last swing low | swing low − 0.25 ATR (≤ 3 ATR) / 2R / 40 |

The higher timeframe (HTF) is 1h → 4h, 4h → 1d and 1d → 1w. It uses closed HTF bars
only.

## Data

* **1h and 4h.** 10 symbols: BTC, ETH, BNB, SOL, XRP, ADA, DOGE, LINK, AVAX, LTC (all
  against USDT).
  * Source: Binance spot 15m archives (data.binance.vision), checksums verified,
    2022-09-01 to 2026-11-22. They are resampled to complete 1h, 4h, 1d and 1w bars.
  * Evaluation runs from 2023-01-01; the first four months are indicator warm-up only.
* **1d.** BTC and ETH daily archives, 2017-08 to 2026-11-22 (checksums verified). The HTF is
  1w. Evaluation runs from 2019-01-01.
* Exchange filters (lot step, minimum notional) come from Binance `exchangeInfo`. Missing
  bars are never filled.

## Splits (no parameter is fitted; the latest period is the held-out test)

| Timeframes | Train | Validation | Test |
|---|---|---|---|
| 1h, 4h | 2023-01-01 – 2024-07-01 | 2024-07-01 – 2025-07-01 | 2025-07-01 – 2026-11-22 |
| 1d | 2019-01-01 – 2022-07-01 | 2022-07-01 – 2024-07-01 | 2024-07-01 – 2026-11-22 |

## Execution, costs and risk

`nexus_backtest/library_engine.py`:
* **Fills.** At the next bar's open. Taker fee 0.10%, maker fee 0.10% (no BNB discount).
  * Half-spread is 0.25 bp for BTC/ETH and 1 bp for the others; slippage is 5 bp per
    market order.
  * Each fill is capped at 5% of the bar's volume.
* **Exits inside a bar.** The stop is checked before the target; a gap through the stop
  fills at the open. Targets are limit (maker) orders.
* **Risk.** The owner's research profile: 0.5% risk per trade from the stop distance
  including costs, at most 3 positions, one per symbol, 1.5% open risk, and the 2% daily /
  4% weekly loss limits.
  * The 5% drawdown halt is reported, not applied, so that the edge is measured on the full
    period.
* **Capital.** 10,000 USDT of paper capital per strategy and timeframe.

## Gates (all must hold, per strategy and timeframe)

1. Trades: at least 100 for 1h and 4h, at least 30 for 1d.
2. Full period: profit factor ≥ 1.20 and expectancy > 0 after costs.
3. Out of sample:
   * test profit factor ≥ 1.10 and test expectancy > 0;
   * validation profit factor ≥ 1.00.
4. Drawdown:
   * maximum drawdown ≤ 15%;
   * the Monte Carlo trade-shuffle 95th percentile drawdown ≤ 20%.
5. Significance: the stationary-bootstrap p-value of the mean trade return (block 10,
   5,000 resamples) must be significant after a **Holm correction across all 24 tests** at
   α = 0.05.
6. Breadth (1h and 4h): profit factor > 1 on at least half of the symbols with ≥ 5 trades.

Reported but not gates:
* Sharpe and Sortino ratios, SQN, recovery factor, win rate, average win and loss;
* the equal-weight buy-and-hold benchmark;
* when the 5% drawdown halt would have triggered.

## Consequences

* **Pass.** The strategy becomes a paper forward-test candidate (stage S5). It is never
  traded live without 180 calendar days of paper results and the owner's separate explicit
  approval.
* **Fail.** The result is recorded and the strategy stays disabled.
  * Nothing is re-tuned on this data.
  * A failed strategy may still be observed in paper, clearly labelled as failed.

---

## Results

_Run once on 2026-11-23, after the pre-registration commit (081c550).
* Archive checksums re-verified: 720 + 266 files, 0 bad.
* Full output: `2026-11-23-global-library-result.json`. Runner: `python -m
  nexus_backtest.library_run`._

**One of 24 tests passes: `atr_expansion_breakout` on daily BTC/ETH.** The other 23 fail.

| Strategy | 1h PF (return) | 4h PF (return) | 1d PF (return) |
|---|---|---|---|
| ema_adx_macd_trend | 0.83 (−24%) | 1.00 (+0.1%) | 0.59 (−2%) |
| rsi_macd_roc_momentum | 0.77 (−78%) | 0.96 (−9%) | 2.24 (+7.5%), 30 trades, test PF 0.72 |
| donchian_breakout | 0.90 (−39%) | 1.16 (+31%), drawdown 25% | 2.46 (+21%), p = 0.005, **fails Holm only** |
| bollinger_squeeze_breakout | 0.84 (−57%) | 0.92 (−13%) | 1.72 (+8%), p = 0.16 |
| atr_expansion_breakout | 0.80 (−59%) | 1.01 (+1%) | **3.44 (+7.6%) PASS** |
| bb_rsi_reversion | 0.61 (−10%) | 0.58 (−5%) | 0 trades |
| vwap_deviation_reversion | 0.61 (−27%) | 0.66 (−10%) | 2 trades |
| mtf_structure_trend | 0.87 (−48%) | 1.04 (+5%) | 1.46 (+7%), test PF 0.75 |

Benchmarks (equal-weight buy and hold of the same coins and window):
* 1h / 4h, 10 coins, 2023-01 to 2026-11: **+108% to +110%**.
* 1d, BTC + ETH, 2019-01 to 2026-11: **+1,599%**.

### The passing test: atr_expansion_breakout @ 1d (BTC, ETH)

| | Trades | Profit factor | Return | Max drawdown | Win rate | Expectancy |
|---|---|---|---|---|---|---|
| Full 2019-01 – 2026-11 | 36 | 3.44 | +7.6% | 0.9% | 75% | +0.41 R |
| Train 2019 – 2022-06 | 14 | 4.22 | +3.5% | 0.9% | 79% | +0.49 R |
| Validation 2022-07 – 2024-06 | 11 | 2.12 | +1.5% | 0.8% | 73% | +0.27 R |
| Test 2024-07 – 2026-11 | 11 | 5.00 | +2.4% | 0.8% | 73% | +0.43 R |

* **Significance.** Stationary-bootstrap p = 0.0002, significant after Holm across 24 tests.
  SQN 3.57.
* **Robustness.** Monte Carlo trade-shuffle drawdown: median 0.8%, 95th percentile 1.4%.
  Sharpe 0.98 (daily, the account including idle cash).
* **Breadth.** Profit factor 7.9 on BTC and 2.1 on ETH, 18 trades each.
* **Exits.** 25 targets, 6 strategy exits, 3 time limits, 2 stops. The 5% drawdown halt
  would never have triggered.

**Limitations of this pass (read before relying on it):**
1. **Small sample.** 36 trades over 8 years (about 4–5 a year on two coins), so the
   confidence interval is wide. A 180-day paper period will see only about 2 trades and
   cannot confirm it statistically. It can only catch a gross break.
2. **Economically small at this risk level.** About +0.9% a year at 0.5% risk per trade
   with mostly idle cash, against +1,599% for buy-and-hold. It is a low-exposure filter,
   not a growth strategy.
3. **Many tests were run.** Holm controls the family of 24, but NEXUS has now tested about
   45 rule sets in total. Some selection effect remains.

### Near misses, not passed (no re-tuning)
* **donchian_breakout @ 1d.** Every gate except Holm: p = 0.005 against a Holm threshold of
  0.0022. It matches the Crypto Trends finding that slow daily breakouts work on BTC/ETH.
* **donchian_breakout @ 4h.** +31% but PF 1.16, drawdown 25% and p = 0.23.

### What this confirms
On 1h, every strategy loses after costs: 7 of 8 have PF ≤ 0.90, and all 8 lose money. On
4h nothing reaches significance. Only daily rules show an edge. This matches every earlier
NEXUS test: costs dominate intraday indicator signals on Binance spot.

### Consequence (as pre-registered)
* `atr_expansion_breakout @ 1d` becomes a **paper forward-test candidate**. It is recorded by
  the library forward test from 2026-11-24, with no orders and simulated only.
* It stays disabled for live trading. Live use needs 180 calendar days of paper results and
  the owner's separate explicit approval.
* The other 23 are failed. They are still observed in the same forward ledger, labelled
  "failed".
