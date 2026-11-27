# Bot league: NEXUS against popular Binance bots (2026-11-27)

**Status: registered before the league is run.** The owner asked for a bot "competitive
with the other best bots".

The league puts every candidate on the same footing:
* coins: BTC and ETH;
* period: 2021-02-01 to 2026-10-31;
* capital: 10,000 USDT;
* costs: 0.1% per trade plus 0.05% slippage;
* data: Binance 5-minute klines (SHA-256 verified), from the Spot Grid test.

**What this is and is not.** Every rule below was fixed earlier. Nothing is tuned here.
* The NEXUS bot's parameters (40% volatility target, 30-day window, 10-point band) come
  from its own pre-registered test (`2026-11-26-batch3-preregistration.md`, 2020-07 to
  2026-10). That test overlaps this period.
* So the league is a **fair side-by-side comparison, not new proof** of an edge. New
  proof can only come from the paper forward test, which started on 2026-11-27.

## Contenders (all rules fixed)

1. **NEXUS volatility-control bot** (`batch3.vol_target`).
   * It holds a 50/50 BTC/ETH basket bought at the start, with weight
     min(1, 40% ÷ 30-day realised volatility), using data up to the previous close.
   * The rest is held in USDT.
   * It trades only when the weight moves by more than 10 points.
2. **Binance Spot Grid ±30% / 30 grids, no stop:** the best setting in the Spot Grid
   test (`binance_grid_bt`, corrected fill rule), with 5,000 USDT per coin and 30-day
   bots.
3. **Binance Spot Grid ±30% / 30 grids with stop:** the variant NEXUS rules would
   allow.
4. **Binance Spot Grid ±10% / 40 grids, no stop:** a typical narrow setting.
5. **Binance Rebalancing bot:** 50/50 BTC/ETH, fully invested, rebalanced at the daily
   close when either coin drifts more than 5 points from 50%.
6. **Buy and hold:** 50/50 bought once.

## Measures and ranking

* **Measures:**
  * net return and yearly return (CAGR);
  * Sharpe ratio (daily, ×√365);
  * maximum drawdown;
  * Calmar ratio;
  * worst calendar year and the number of positive years.
* **NEXUS fit.** The share of the account that keeps the 5% maximum drawdown rule
  (5% ÷ maximum drawdown), and the return at that share.
* **Ranking.** Contenders are ranked by Sharpe (return per unit of risk). Calmar and
  return are shown alongside. The table is reported as it comes out.

## Results (single run, 2026-11-27; commit 00e565e)

| Rank | Bot | Net | Per year | Sharpe | Max drawdown | Calmar | Worst year |
|---|---|---|---|---|---|---|---|
| 1 | **NEXUS volatility-control bot** | **+126.9%** | +15.3% | **0.55** | **−58.8%** | **0.26** | **−48.8%** |
| 2 | Binance Rebalancing bot | +121.4% | +14.8% | 0.53 | −76.2% | 0.19 | −65.1% |
| 3 | Buy and hold 50/50 | +97.5% | +12.6% | 0.50 | −76.8% | 0.16 | −66.4% |
| 4 | Binance Spot Grid ±30% / 30 | +34.2% | +5.3% | 0.31 | −58.7% | 0.09 | −42.5% |
| 5 | Binance Spot Grid ±30% / 30 + stop | −18.9% | −3.6% | 0.02 | −61.3% | −0.06 | −44.4% |
| 6 | Binance Spot Grid ±10% / 40 | −67.1% | −17.6% | −0.32 | −74.9% | −0.24 | −61.4% |

* **Ranking.** The NEXUS bot ranks first by Sharpe, and also has the highest return and
  Calmar, and the shallowest worst year.
* **Limits:**
  * its parameters came from an earlier test over overlapping years;
  * its own drawdown (−59%) means that under the 5% NEXUS rule only about 9% of an
    account could run it, which would have earned about +10.8% over the period;
  * its paper forward test (started 2026-11-27) is the independent evidence that is
    still to come.
