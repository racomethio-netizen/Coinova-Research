# Pre-registration: grid v2, volatility-targeted holding, BTC/ETH pairs

_Committed before any run. Results are appended once below the line; nothing above it
changes afterwards._

Three tests the owner asked for ("Test next three"). Costs and gates are fixed here.
Nothing is tuned after the run. A strategy goes to paper only if it passes; live only
with the owner's approval.

## 1. Grid v2: close the inventory when the regime breaks

**Hypothesis.** It comes from the adaptive-grid backtest
(docs/research/2026-11-26-adaptive-grid-preregistration.md).
* There the grid earned in SIDEWAYS and WEAK_BULL regimes and lost when the regime turned
  while it held coins.
* Rule change: when the regime leaves the grid's allowed set, close all held lots at
  once (taker fee plus slippage), instead of only suspending new entries.
* A new grid may start as soon as the regime allows again.
* Everything else is unchanged: the engine, the regime and the default parameters
  (spacing 0.6 × ATR, range 8 × ATR), with no tuning.

**Why this data.** The hypothesis was formed on 2024–2026 data, so testing it there would
be in-sample. It is tested on **2020-07-01 to 2023-12-31**, which no grid test has used:
* spot 15m klines from data.binance.vision, checksums verified;
* 2020-01 to 2020-06 is warm-up;
* SOL and AVAX start after their listing (August and September 2020).

**Setup.** Spot, 10 coins, 1,000 USDT each, as in the adaptive-grid backtest:
* 0.1% fees, 0.05% stop slippage;
* trade-through fills on the 15m OHLC path;
* a 7-day pause after a stop-loss;
* v1 (the original rule) is run on the same data for comparison.

**Gates** (v2, all 10 coins pooled):
1. Net return after costs > 0.
2. Profit factor ≥ 1.1.
3. Maximum drawdown ≤ 15%.
4. Positive in at least 2 of the 4 periods (2020 H2, 2021, 2022, 2023).
5. Still > 0 at 2× fees.
6. Still > 0 without the best coin.
7. Better than v1 on the same data.

## 2. Volatility-targeted holding (Moreira & Muir 2017, "Volatility-Managed Portfolios")

**Rule.**
* Hold BTC, ETH, or a 50/50 mix, with weight = min(1, 40% / realised volatility). The
  volatility is annualised from the last 30 daily returns, using closes up to the
  previous day.
* The rest is cash (USDT).
* The weight is changed only when it moves by more than 10 points, at the daily close.

**Data.** 2020-07-01 to 2026-10-31, daily closes from verified Binance klines.

**Costs.** 0.15% per side on traded notional; stress at 2×.

**Comparison.** The same asset held at 100%.

**Gates** (per asset: BTC, ETH, 50/50):
1. Sharpe higher than holding over the full window.
2. Maximum drawdown at least 10 points smaller than holding's.
3. Net return > 0.
4. Sharpe higher than holding in BOTH halves (2020-07 to 2023-06 and 2023-07 to
   2026-10).
5. Gates 1 and 3 still hold at 2× costs.

**Reported, not gated:** 30% and 50% volatility targets.

## 3. BTC/ETH pairs trade (statistical arbitrage) on perpetual futures

**Rule** (`nexus_strategies.stat_arb`, unchanged defaults):
* Hourly closes. Every 168 hours, refit Engle-Granger on the previous 720 hours.
* Trade only when cointegrated at 5%, with a half-life of 1–30 bars and the expected
  reversion above 2× round-trip costs.
* Enter at |z| ≥ 2, exit at |z| ≤ 0.5, stop at |z| ≥ 4 (60-bar z-score window).

**Positions and costs.**
* Long the cheap leg, short the rich leg; beta-weighted; 1x gross on 10,000 USDT.
* 0.05% taker fee + 0.05% slippage per leg per side.
* Actual funding on both legs at every funding time.

**Data.** Binance USD-M perpetuals, 15m klines and funding (verified), 2024-01-01 to
2026-10-31. 2023-06 to 2023-12 is formation and warm-up.

**Gates:**
1. Net return > 0 after costs and funding.
2. Annualised Sharpe ≥ 0.5.
3. Maximum drawdown ≤ 15%.
4. Mean daily return > 0 with a stationary-bootstrap p-value < 0.05 (block 10, 5,000
   resamples).
5. Positive in at least 2 of 3 years.
6. Still > 0 at 2× costs.

**Note.** Even on passing, live trading would need futures, which NEXUS keeps disabled.
A pass means a paper test first.

---

## Results

### Implementation notes (committed with the harness, before the run)

These details were not specified above. Each was fixed before any result was seen.

* **Data checks.**
  * Spot 15m archives for 2020–2023: 465, every one matching Binance's `.CHECKSUM`.
  * Futures archives: 164, all matching.
  * Each coin is missing the same 93 fifteen-minute bars, which are exchange-wide
    downtime.
* **Tick sizes (grid).** Today's exchange filters are used, except where today's tick is
  coarser than 0.01% of a coin's lowest 2020–2023 price. There the tick is divided by 10
  until it is not.
  * Why: Binance quoted low-priced coins with more decimals then. DOGE at $0.0023 has
    today's tick of 0.00001, which is 0.4% of its price and would distort the grid
    spacing.
  * Affected: BNB, SOL, XRP, ADA, DOGE, LINK, AVAX, LTC.
  * Unchanged: BTC, ETH.
  * Code: `batch3.refine_tick`.
* **Volatility targeting.**
  * Positions are units of the coin plus cash, so the held weight drifts with price
    between trades.
  * At each daily close the target weight is compared with the held weight, and the
    position trades to target only if they differ by more than 10 points. Cost is paid on
    the traded notional.
  * The 50/50 mix is bought at the window start and held.
  * Daily closes come from two sources, both official Binance klines with checksums
    verified: 2020-01 to 2023-12 from the spot 15m archives, and 2024-01 to 2026-10 from
    the hourly series used in the rebalancing test.
* **Pairs.**
  * The 2× cost stress keeps the same trades and pays double fees and slippage, as in the
    adaptive-grid stress. Funding is not multiplied.
  * Funding is charged at the hourly close price for any settlement inside a bar the
    position was held through.
  * A position's model (beta, intercept) is kept from entry to exit.
  * Size: 1x gross on current equity.

_(Results appended below after the single run.)_

### Results of the single run (2026-11-26, harness commit d46c6e1)

Output: `research/backtesting/nexus_backtest/results/batch3_2026-11.json`, also shown on
the dashboard page **Strategy test**. Historical backtests only; no order was placed.

**Grid v2: FAILED (0 of 7 gates). Volatility targeting: the BTC/ETH 50/50 mix PASSED;
BTC alone and ETH alone FAILED. BTC/ETH pairs: FAILED (1 of 6 gates).**

#### 1. Grid v2 vs v1, spot, 2020-07 to 2023-12 (10 coins pooled, 10,000 USDT)

| Version | Net | Worst drop | Profit factor | Round trips | Forced sales | Stop-losses | Fees |
|---|---|---|---|---|---|---|---|
| v1 (pause on a regime change) | −43.5% | −47.3% | 0.70 | 41,445 | 0 | 464 | 2,538 |
| **v2 (sell held coins on a regime change)** | **−51.1%** | **−51.2%** | **0.70** | **61,962** | **5,782** | **101** | **4,247** |
| v1 at 2× fees | −59.9% | −61.1% | 0.54 | 41,445 | 0 | 464 | 4,416 |
| v2 at 2× fees | −73.4% | −73.4% | 0.49 | 61,976 | 5,785 | 101 | 6,605 |

* **By period (v2 / v1):**
  * 2020 H2: −4.1% / −0.1%
  * 2021: −18.8% / −16.7%
  * 2022: −24.7% / −28.7%
  * 2023: −16.7% / −4.8%
* **By coin:** every coin lost under both versions. Under v2 the losses ranged from DOGE
  −44% to XRP −62%. Without the best coin, v2 was −51.8%.
* **By regime when the trip closed** (v2, USDT):
  * SIDEWAYS +6,722 and WEAK_BULL +3,012;
  * WEAK_BEAR −11,437 and HIGH_VOLATILITY −2,167.
  * The pattern found on 2024–2026 holds on unseen data: grids earn in calm and gently
    rising markets and lose more when markets fall.
* **Why v2 was worse.**
  * The regime changed often, about once every two days per coin.
  * Each change forced a sale at market (taker fee plus slippage), and a new grid then
    bought back in.
  * Fewer stop-losses (101 vs 464) did not make up for 5,782 forced sales and 67% more
    fees.
* **Gates (v2):** all 7 failed: net, profit factor, drawdown, periods, 2× fees, without
  the best coin, and better than v1.

#### 2. Volatility-targeted holding, 2020-07 to 2026-10 (target 40%)

| Asset | Strategy | Held | Sharpe (held) | Worst drop (held) | Trades | Gates |
|---|---|---|---|---|---|---|
| BTC | +468% | +749% | 0.875 (0.878) | −64.7% (−76.6%) | 102 | fail: Sharpe, both halves, 2× costs |
| ETH | +475% | +900% | 0.846 (0.856) | −58.0% (−79.3%) | 129 | fail: Sharpe, 2× costs |
| **BTC/ETH 50/50** | **+612%** | **+824%** | **0.938 (0.861)** | **−58.7% (−77.4%)** | **113** | **PASS** |

* **50/50 by half** (Sharpe, strategy vs held):
  * 2020-07 to 2023-06: 1.28 vs 1.15.
  * 2023-07 to 2026-10: 0.62 vs 0.51.
* **50/50 at 2× costs:** Sharpe 0.929, net +595%.
* **50/50 by year** (strategy vs held):
  * 2022: −49.0% vs −66.7%.
  * 2025: −2.7% vs −9.1%.
  * 2026 to October: −16.1% vs −18.7%.
* **Average coin weight:** BTC 0.78, ETH 0.62, 50/50 0.70.
* **Reported, not gated** (50/50):
  * 30% target: +388%, Sharpe 0.917, worst drop −49.2%.
  * 50% target: +664%, Sharpe 0.892, worst drop −66.9%.

**What the pass means, and what it does not.**
* The 50/50 rule earned *less* than holding (+612% vs +824%), with a smaller worst drop
  (−59% vs −77%) and slightly more return per unit of risk (Sharpe 0.94 vs 0.86).
* It is a way to hold BTC/ETH with somewhat less pain, not a money-maker on its own. Its
  worst drop was still more than half the money.
* One of three variants passed, and BTC alone was almost exactly a tie with holding. The
  edge is small, and no significance test was pre-registered for it.
* **Consequence:** it starts a 180-day paper forward test on 2026-11-27 (worker
  `voltarget-paper`, 10,000 virtual USDT). It goes live only with the owner's approval.

#### 3. BTC/ETH pairs on perpetuals, 2024-01 to 2026-10

* **Result:**
  * Net −13.2% after costs and funding; −19.4% at 2× costs.
  * Worst drop −13.2%; Sharpe −1.14; bootstrap p = 0.97.
  * By year: 2024 0.0% (no trade), 2025 −6.6%, 2026 −7.1%.
* **Trades:** 33 (16 long the spread, 17 short), 6 stopped out at |z| ≥ 4, held 27 hours on
  average.
* **Money (USDT):**
  * Trades lost 700 before costs.
  * Fees and slippage: 625.
  * Funding: +4.7 received.
* **Why:**
  * BTC and ETH were cointegrated in only 14 of the 148 weekly refits, so there were
    few trades.
  * Those trades lost money even before costs. The spread's z-score often "reverted"
    because its 60-hour average moved, not because prices converged.
* **Gates:** only the drawdown gate (≤ 15%) passed. Failed: net, Sharpe, significance,
  2 of 3 years, 2× costs.

#### Overall

* **Passed:** one of the three ideas, volatility-targeted BTC/ETH 50/50. It is a way to
  hold coins with smaller drops, not an income strategy.
* **Not re-tuned:** grid v2 and the pairs trade are recorded as failed.
