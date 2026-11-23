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
