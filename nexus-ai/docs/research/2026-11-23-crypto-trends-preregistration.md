# Pre-registration: "Catching Crypto Trends" ensemble trend-following (spot, long-only)

Committed BEFORE any run on market data. The code is tested only on synthetic data before
that. Results are appended once, unedited. Nothing below changes after the run.

## Why this strategy

Owner request: find the best-evidenced profitable strategy published anywhere, then test
it honestly. The literature search (23 Nov) found:

* trend following has the strongest published evidence for spot, long-only crypto;
* cross-sectional momentum is weak, needs shorting and crashes;
* intraday and calendar effects do not persist;
* daily time-series momentum on perpetuals was already tested by NEXUS and failed.

The most complete, reproducible description is Zarattini, Pagani and Barbon, "Catching
Crypto Trends: A Tactical Approach for Bitcoin and Altcoins", Swiss Finance Institute
Research Paper 25-80 (revised 9 Apr 2025). For Bitcoin, 2015 to Mar 2025, before costs,
the paper reports CAGR 30%, Sharpe 1.58 and max drawdown 19%.

Caveat: that sample overlaps ours. This is an independent replication on Binance prices
with stricter costs, not a fully unseen test of the authors' idea.

## Rules (from the paper, section 4; deviations marked)

* **Data:** Binance spot daily klines (UTC days, close at 00:00 UTC) from the public
  archive (data.binance.vision), BTCUSDT and ETHUSDT from Aug 2017.
* **Donchian channel** over the last n daily CLOSES, including today:
  * Up = max of the closes; Down = min of the closes; Mid = (Up + Down) / 2.
* **Signal per lookback n**, where n is 5, 10, 20, 30, 60, 90, 150, 250 or 360:
  * **Enter** when today's close equals Up(n).
  * **Exit** when today's close is at or below the trailing stop.
  * The trailing stop starts at Mid(n) on the entry day. It then becomes
    max(previous stop, Mid(n)) each day and never moves down.
  * Otherwise the previous position is kept.
* **Size per lookback:** w(n) = min(0.25 / sigma, CAP) x Pos(n).
  * sigma is the annualised standard deviation of the last 90 daily returns (x sqrt(365)).
  * **Deviation:** CAP = 100%, not 200%. Spot NEXUS never uses leverage.
* **Combo weight:** the mean of the 9 lookback weights (0 to 100%) for each coin.
* **Portfolio:** BTC and ETH (the coins NEXUS trades), each with half the capital.
* **Rebalancing (paper, section 5.1):**
  * A change in the combo weight caused by a signal (any lookback entering or exiting)
    is executed in full.
  * A change caused only by volatility is executed only if
    |target - current| > 20% of the target.
* **Timing:** signals use day t's close; the trade fills at day t+1's open, which equals
  the close in a 24/7 market. No look-ahead.
* **Costs:** 10 bps fee + 5 bps slippage on every traded notional. The paper uses 10 bps
  in total, so ours is stricter. Idle cash earns 0%.
* **Warm-up:** the first 360 days of each coin are used only to compute indicators.
  * The evaluation starts when both coins have 360 days of history, about Aug 2018.
  * It ends with the last archive day.

## Gates (all required; net of costs; BTC+ETH portfolio)

1. Sharpe ratio (daily, x sqrt(365)) >= 0.8 over the whole evaluation period.
2. Maximum drawdown <= 35%.
3. Sharpe >= 0.5 in BOTH halves (split at the midpoint date).
4. At least 60% of full calendar years are positive.
5. Sharpe above the Sharpe of a 50/50 BTC+ETH buy-and-hold over the same days.
6. On NEXUS's primary unseen period (2022-11-14 to 2024-11-13): net return > 0.

Reported but not deciding:
* per-coin results;
* each lookback alone;
* the paper's 200% cap;
* zero costs;
* 2x costs.

## What a pass allows

A pass qualifies the strategy only for a **forward paper test** on new data from the next
day onward. It never directly allows trading with real money.

Live trading would still need all of the following:
* a successful forward test (pre-registered separately);
* a dedicated IP;
* the owner's explicit activation with two-factor authentication.

A fail is recorded and nothing is enabled.

## Results (single run, 23 Nov 2026, after commits cc41f5f, 26a6133, 862af69, 3bc7f96)

Data: Binance spot 1d archives, BTCUSDT and ETHUSDT, 2017-08-17 to 2026-11-22
(3,385 days each). All 266 files verified against Binance's SHA-256 checksums.
Evaluation: 2018-08-12 to 2026-11-22. Full output: `2026-11-23-crypto-trends-result.json`.

### Decision: PASS (all 6 gates)

| Gate | Result | Required |
|---|---|---|
| Sharpe, net | **1.11** | >= 0.8 |
| Max drawdown | **15.6%** | <= 35% |
| Sharpe, each half | **1.50** (2018-08 to 2022-08) / **0.70** (2022-08 to 2026-11) | >= 0.5 each |
| Positive full years | **5 of 7** (2019 +21%, 2020 +52%, 2021 +26%, 2022 -7%, 2023 +18%, 2024 +25%, 2025 -2%) | >= 60% |
| Sharpe vs 50/50 BTC+ETH buy-and-hold | **1.11 vs 0.74** | higher |
| Primary unseen period (2022-11-14 to 2024-11-13) | **+44%** (Sharpe 1.36, max drawdown 11%) | > 0 |

**Strategy vs buy-and-hold:**
* Strategy: net CAGR 14.1%, x2.97 over 8.3 years.
* Buy-and-hold: CAGR 30.5%, x9.1, but with a 76% max drawdown (-65% in 2022).
* The strategy is invested only part of the time (average exposure: BTC 20%, ETH 13%), so
  it earns less than holding in strong bull markets and loses far less in crashes.
* Trading: 1,817 trades in total, turnover 6.7x a year, costs paid 18% of starting capital
  over the whole period.

**Robustness (reported, not deciding):**
* Per coin: BTC Sharpe 1.06, ETH 0.93.
* Costs: zero costs, Sharpe 1.18; double costs, Sharpe 1.03.
* The paper's 200% cap: identical (the 100% cap never binds at 25% target volatility).
* Each lookback alone: Sharpe 0.62 (5 days) to 1.20 (30 days); every one is positive.
* **Different code, same answer:** an independent vectorised computation of the paper's
  formula (no costs) gives Sharpe 1.119; the simulator gives 1.118.

**Placebo (added after the decision, to check for bugs and drift):**
* Shuffled daily returns, which destroy trends but keep crypto's upward drift: mean
  Sharpe 0.64 over 100 runs, and only 3% reached the real 1.11.
* Shuffled with the drift removed: mean -0.15 (no bug).
* So part of the Sharpe is the long-only exposure to crypto's rise. The trend timing adds a
  significant amount on top (p of about 0.03), and the main benefit is the much smaller
  drawdowns.

**Limits, stated plainly:**
* The authors' paper (2015-2025) overlaps this period. This was an independent replication
  with stricter costs, not a test on data nobody has seen.
* The second half was weaker (Sharpe 0.70, 8% a year).
* A backtest is not a promise of future returns.

### Next (per this pre-registration)
A pass allows only a **forward paper test** on new data. See
`2026-11-23-crypto-trends-forward-preregistration.md`.
