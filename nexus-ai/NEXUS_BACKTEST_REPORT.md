# NEXUS backtest report

Every strategy NEXUS has evaluated, with the out-of-sample result. A backtest result is
never a promise of future returns; a pass only qualifies a strategy for a forward paper
test on new data. Details and full tables are in each pre-registration under
`docs/research/`.

## Method (all studies)

* Pre-registered rules, grids, data and gates, committed before the run; results appended
  once, unedited.
* Closed bars only, entries at the next bar's open, stop before target within a bar,
  fees 10 bps + slippage 5 bps per side (spot), funding and liquidation modelled for
  futures studies.
* Walk-forward: train 180 days / test 60 days, parameters chosen on train windows only,
  stitched out-of-sample results judged by fixed gates (>= 30 trades, expectancy > 0,
  profit factor >= 1.15, >= 55% positive windows, t-stat >= 1.0).
* Look-ahead protection is tested (indicator prefix-invariance, higher-timeframe alignment
  on closed bars, train trades must exit before the test window).

## Results

| Study | Strategies | Data | Out-of-sample result |
|---|---|---|---|
| Base library (2026-11-21) | trend_following, momentum, breakout, mean_reversion, pullback_support, liquidity_sweep_reversal | BTC/ETH spot 2024-11..2026-11 | all FAIL (expectancy -0.30% to -0.53% per trade; profit factor 0.31-0.57) |
| Daily time-series momentum (2026-11-20) | tsmom long/short perpetuals | BTC/ETH perpetuals | FAIL (Sharpe, positive windows, t-stat, drawdown) |
| Funding carry (2026-11-21) | spot long + perp short | BTC/ETH | FAIL (3.91%/yr < 4%; 43% positive quarters < 60%) |
| Phase 6 (2026-11-22) | vwap_reclaim, volatility_expansion, mtf_confluence, funding_squeeze, taker_flow | BTC/ETH 2024-11..2026-11 | all FAIL (-0.24% to -0.45% per trade) |
| Strategy modules A-G (2026-11-23) | trend_supertrend, breakout_retest, momentum_rv, mean_reversion_vwap, vwap_trend, sweep_reclaim, market_structure | **unseen** BTC/ETH 2022-11..2024-11 (primary); 2024-11..2026-11 (secondary) | all FAIL on the primary period (-0.08% to -0.30% per trade; D made no trades) |

| **Crypto trends ensemble (2026-11-23)** (Zarattini, Pagani & Barbon 2025, replicated) | Donchian breakout ensemble (9 lookbacks), 25% volatility target, long-only spot, BTC+ETH | BTC/ETH daily 2018-08..2026-11 (Binance archives, checksums verified) | **PASS** all 6 gates: Sharpe 1.11 (buy-and-hold 0.74), CAGR 14.1%, max drawdown 15.6% (buy-and-hold 76%), both halves positive, 5 of 7 years positive, primary unseen period +44% |

Forward paper tests on new data (running, decided once at their stopping rule):

* **Crypto trends ensemble, daily** (starts 2026-11-24, review at day 180). Paper only.

* Funding carry, always-on (started 2026-11-22, decision at day 90).
* Taker flow in range regimes (started 2026-11-22, decision at 30 trades or 18 months).

## What this means

**Update 23 Nov 2026: the first strategy to pass.** A published trend-following method
passed every pre-registered gate on 8 years of Binance data. It is slow and daily,
long-only and spot, and is in cash most of the time (average exposure about 16%). It earns
less than holding the coins in bull markets (14% a year against 31%) and loses far less in
crashes (worst drop 16% against 76%).
* **Placebo checks:** shuffled prices with crypto's drift kept beat it in only 3 of 100
  runs, and shuffled prices without drift earn nothing.
* It now runs as a forward PAPER test. Live trading would still need a forward-test pass,
  a dedicated IP and the owner's activation.
* Details: docs/research/2026-11-23-crypto-trends-preregistration.md.

The earlier conclusions below still hold for the short-horizon rules:

* 20 strategies have been tested out of sample; none has a
  positive expectancy after costs that passes the gates. NEXUS therefore places no trades,
  in paper or live. That is the intended behaviour, not a malfunction.
* The round-trip cost (about 0.30% for a spot trade with taker fees and slippage) is larger
  than the gross edge of most short-horizon technical rules on BTC/ETH. Ideas that trade
  less often, use maker execution, or have a structural source of return (for example
  funding) are more promising than more indicator combinations.
* The next step that can change this is the machine-learning phase, judged by the same
  out-of-sample standard and by net trading performance, not by accuracy.

## Phase G1 (2026-11-23): realistic costs, breakdowns, parameter stability

### Slippage that depends on order size

The event backtester can now price slippage with `SlippageModel`.
* The fixed part is half the spread: 5 bps per side.
* Market impact is added on top: daily volatility x sqrt(order value / average daily volume),
  the standard square-root law with a coefficient of 1.
* Volatility and volume come from the 30 days before the fill only.
* Without that history the order pays the 2% cap, never zero.
* Historical order books are not available, so traded volume stands in for depth.

Cost per side on real Binance data (median day):

| Period | Coin | Daily vol | Avg daily volume | $1k | $10k | $100k | $1M | Impact = fee at |
|---|---|---|---|---|---|---|---|---|
| 2022-11..2024-11 | BTC | 2.3% | $1.68 bn | 5.2 bp | 5.6 bp | 6.8 bp | 10.7 bp | $3.1 M |
| 2022-11..2024-11 | ETH | 2.7% | $0.79 bn | 5.3 bp | 6.0 bp | 8.0 bp | 14.5 bp | $1.1 M |
| 2024-11..2026-11 | BTC | 2.1% | $1.65 bn | 5.2 bp | 5.5 bp | 6.6 bp | 10.1 bp | $3.8 M |
| 2024-11..2026-11 | ETH | 3.4% | $1.30 bn | 5.3 bp | 5.9 bp | 8.0 bp | 14.4 bp | $1.1 M |

At NEXUS's order sizes (well under $10k), impact adds less than 1 bp. The 5 bp slippage
assumed in every study was therefore realistic, and costs are dominated by the 10 bp taker
fee. Size only starts to matter above about $1M per order.

**Funding and borrowing:** spot NEXUS never borrows and pays no funding, so these are 0 in
spot reports. Futures studies already model funding and liquidation
(`futures_engine.py`).

### New report fields

* Event backtests now report:
  * recovery factor (net profit / largest drawdown in money);
  * average R (P&L divided by the risk at the stop);
  * total slippage, and funding;
  * results per coin, per side, per entry hour (UTC) and per weekday.
* Walk-forward out-of-sample trades are broken down by regime, coin, 4-hour block,
  weekday and exit type (`reports.breakdown`). Groups under 20 trades are flagged.

Across all 15 strategies that traded, out-of-sample:
* **Time of day:** every 4-hour block lost money, in both periods (2022-24: -0.16% to
  -0.40% per trade).
* **Coins:** both coins lost money in both periods.
* **Weekdays:** all seven lost money in both periods.

There is no hidden profitable hour, coin or weekday. Even if a group had looked
positive, trading only that group would need its own pre-registered test.

### Parameter stability (`nexus_backtest.stability`)

For each of the 9 walk-forward windows, every parameter set in the grid is scored on the
training data and on the following unseen data. Full output:
`docs/research/2026-11-23-parameter-stability-{primary,secondary}.json`.

| | Unseen 2022-11..2024-11 | 2024-11..2026-11 |
|---|---|---|
| Probability of backtest overfitting (PBO), per strategy | 0.17-0.78, median 0.50 | 0.33-0.83, median 0.53 |
| Rank correlation, training vs unseen, per strategy | -0.23 to +0.29, median +0.07 | -0.28 to +0.31, median +0.05 |
| Share of windows choosing the most common set | 11-44% | 11-56% |
| Expectancy of the chosen set: training -> unseen | lower on unseen for 15 of 15 | lower for 15 of 15 |

(mean_reversion_vwap never had enough training trades to choose a parameter set.)

In plain terms:
* The parameter set that looked best on the past lands in the bottom half on unseen data
  about half the time (PBO around 0.5). That is no better than picking at random.
* How the sets rank on past data does not predict how they rank later (correlation near 0).
* The "best" set keeps changing from window to window (4-9 different sets out of 9 choices).
* The chosen set also usually beats its neighbouring settings on training data. That
  points to a narrow, noisy peak, not a broad stable region.

**Conclusion:** the out-of-sample losses are not a tuning problem. These rule families have
no edge to tune into, and searching larger grids would only fit more noise. This supports
the earlier conclusion: further research should change the hypothesis (longer horizons,
maker execution, structural returns such as funding), not the parameters.
