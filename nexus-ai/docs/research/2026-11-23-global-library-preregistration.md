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

_(appended after the single run)_
