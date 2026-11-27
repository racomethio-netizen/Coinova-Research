# Binance Spot Grid bot: pre-registration (2026-11-27)

**Status: registered before any run of this test.** The owner asked for a test of the
Binance Spot Grid bot itself, as ordinary users run it.

It differs from NEXUS's *adaptive* grid (failed on 2026-11-26, −24.6%):
* there is no regime filter, no ATR-based design and no NEXUS-specific logic;
* it is the plain product: a fixed price range, a number of grids, a fixed investment,
  and an optional stop-loss.

## How the bot works (Binance's Spot Grid, as documented by Binance)

The mechanics are rebuilt from Binance's public description of the product. The code is
`nexus_backtest/binance_grid_bt.py`.

* **Grid lines.** N + 1 prices between the lower and upper limit, with geometric spacing
  (equal percentage steps; Binance offers arithmetic or geometric).
* **Orders.** Each of the N grids trades the same base-asset quantity:
  * it buys at its lower line and sells at its upper line;
  * after a fill, the opposite order is placed on the same grid.
* **Start:**
  * grids entirely above the current price are bought at market (taker fee), so they
    hold coins with a sell order waiting;
  * the grid containing the price, and those below it, wait with a buy order;
  * the quantity is set so the whole investment is used.
* **Fees.** 0.1% per fill (Binance's standard spot fee, without the BNB discount).
  Market orders also pay 0.05% slippage.
* **Fills (on Binance 5-minute bars, no look-ahead):**
  * the price path inside each bar is open → low → high → close when the bar closed up,
    and open → high → low → close otherwise;
  * a limit order fills only when price trades strictly through its line, at the line
    price;
  * the opposite order is active from the next bar (a conservative delay).
* **How it is run.** A bot lives 30 days.
  * At the end, its coins are sold at market.
  * A new bot starts at the current price with everything the previous one ended with.
  * This is how people use it: they restart the grid around the new price.

## Configurations (6, all fixed now)

| Range around the start price | Grids | Step (approx.) | Stop-loss |
|---|---|---|---|
| ±10% | 40 | 0.5% | none / 5% below the lower line |
| ±20% | 40 | 1.0% | none / 5% below the lower line |
| ±30% | 30 | 2.0% | none / 5% below the lower line |

* **Without a stop** (Binance's default), a fall below the range keeps all the bought
  coins until the 30 days end.
* **With the stop**, all coins are sold at market when price trades 5% below the lower
  line. The bot then waits for the next 30-day start.

## Data and portfolio

* **Coins.** The 10 coins of NEXUS's earlier tests: BTC, ETH, BNB, SOL, XRP, ADA, DOGE,
  LINK, AVAX and LTC, against USDT.
* **Data.** Binance spot 5-minute klines (data.binance.vision), SHA-256 checked, complete
  months January 2021 to October 2026.
  * The period covers the 2021 bull market, the 2022 crash and the recovery.
  * A missing month aborts the run, and nothing is filled in.
* **Allocation.** 1,000 USDT per coin, each coin's bot compounding on its own, pooled
  into one 10,000 USDT portfolio.
* **Benchmark.** The same 10 coins bought once at the start (taker fee) and held.

## Pass conditions (pooled portfolio, per configuration; all must hold)

1. Net return above zero after all fees.
2. Sharpe ratio (daily, ×√365) at least 0.5.
3. Sharpe higher than holding the coins.
4. Calmar ratio (CAGR divided by the maximum drawdown) at least holding's.
5. Positive in at least 60% of calendar half-years.
6. Net return above zero at **2× fees**.
7. Still above zero without the best coin.
8. Stationary bootstrap p-value for a positive mean daily return (5,000 simulations,
   mean block 10 days) passes Holm's correction at 5% across the 6 configurations.

**Reported, never gated:**
* results per coin and per year;
* fees paid and completed grid trades;
* stop-outs;
* the share of time spent below the range (holding coins) and above it (all cash);
* **NEXUS fit:** the share of the account that could be allocated so that the test's
  maximum drawdown stays within NEXUS's 5% limit (5% ÷ maximum drawdown), and the
  return at that allocation.

## NEXUS rules and decision

* A spot grid buys more as price falls, within a fixed budget.
  * NEXUS allows a grid only with a fixed budget, a stop and a known worst case (see
    `nexus_strategies/grid.py`), and never with martingale sizing.
  * The no-stop configurations are tested because that is how Binance runs by default.
    They would not be allowed live.
* **A stop configuration passes:** a paper forward test (no orders) of at least 3
  months, then the owner decides. Live trading needs the owner's explicit approval and
  stays within NEXUS's spot exposure cap.
* **Nothing passes:** the result is recorded, and there is no re-tuning on this data.
* **A harness bug found after the run:** it is fixed and the test re-run. Both results
  are reported.

## Results (2026-11-27)

* **Data.** 700 monthly archives, all SHA-256 verified. There are 7 exchange-outage gaps
  over 5 minutes, which are not filled.
* **Before any result was seen,** the timing check found a timestamp-unit bug (pandas 3
  stores milliseconds), which was fixed and tested.

**Verdict: all 6 configurations FAILED, in both runs.** No re-tuning will follow.

### The two runs

* **Pre-registered run** (commit 29dfdfc).
* **Corrected re-run** (commit 24b15ba). After the first run, a fair-game sanity check
  led to one realism fix: an order that is already through the market when it becomes
  active fills at the bar's open, as a real limit order does, not at its worse line
  price.
  * The check's remaining bias was traced to the synthetic generator's price jumps; it
    shrinks to non-significance with a near-continuous price.
  * Both runs are reported, as the pre-registration requires.

| Configuration (corrected run) | Net | Sharpe | Max drawdown | Calmar | Original run |
|---|---|---|---|---|---|
| ±10% / 40 grids | −63.9% | −0.22 | −78.6% | −0.20 | −78.3% |
| ±10% / 40 grids + stop | −72.5% | −0.99 | −78.7% | −0.25 | −80.6% |
| ±20% / 40 grids | +12.1% | 0.24 | −62.1% | 0.03 | −10.9% |
| ±20% / 40 grids + stop | −26.1% | −0.07 | −59.2% | −0.09 | −36.7% |
| ±30% / 30 grids | **+101.6%** | 0.50 | −57.4% | 0.22 | +80.4% |
| ±30% / 30 grids + stop | +8.1% | 0.19 | −55.2% | 0.02 | +1.1% |
| *Holding the same coins* | *+1,049%* | *0.90* | *−87.6%* | *0.59* | |

### The best configuration (±30%, no stop)

* **Return.** +101.6% in almost 6 years (12.8% a year).
* **Failed checks:**
  * a lower Sharpe than holding;
  * a lower Calmar than holding;
  * not significant (p 0.076).
* **When the profit was made.** Nearly all of it came in 2021 (+136%). It then lost 45%
  in 2022, 4% in 2025 and 10% so far in 2026.
* **Not allowed live.** It has no stop, so NEXUS rules forbid it for real money.

### What the test shows

* **Narrow grids lose heavily.** At ±10%, the bot made about 350,000 trades and paid
  about 15,900 USDT in fees on 10,000 USDT. It also capped every rally: in 2021 it made
  +6% (original run) while holding made +2,073%.
* **The stop-loss hurts.** It sells near the lows and the bot then waits.
* **NEXUS fit.** To keep the 5% drawdown limit, at most about 9% of the account could go
  into the best grid, which would have earned about +9% in total over 6 years.

**Decision.** The Binance Spot Grid is recorded as failed. No live or paper order path
is added.
