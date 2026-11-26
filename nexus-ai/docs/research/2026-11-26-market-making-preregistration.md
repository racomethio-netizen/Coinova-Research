# Pre-registration: market making (two-sided quoting) on Binance perpetuals and spot

_Committed before any run. Results are appended once below the line; nothing above it
changes afterwards._

## Why

The owner asked for the method of the world's most successful trading bots.
* Those are market makers: they keep a buy order just below the price and a sell order
  just above it, and earn the gap many times a day.
* Hummingbot's "pure market making" is the open-source version that retail traders run.
* Professional firms add speed, fee rebates and size that a small account does not have.
* This is the one major bot method NEXUS has not yet tested. Grids, DCA-style, momentum,
  trend, AI, pairs, carry and rebalancing have all been tested.

## Data

* **Source:** Binance official 1-minute klines from data.binance.vision, every archive
  checked against its `.CHECKSUM`.
  * USD-M perpetuals and spot.
  * Actual perpetual funding rates.
* **Symbols (USDT):** BTC, ETH, SOL, BNB, XRP, DOGE.
* **Window:** 2024-01-01 to 2026-10-31.
* **Known bias:** the six are today's largest coins (survivorship).

## Strategy (per symbol, an independent paper book of 10,000 USDT, 1x)

**Quotes, every minute at the bar open.** Only data up to the previous minute's close is
used. `ref` is the previous close; `σ` is the standard deviation of the last 60
one-minute log returns.
* **Buy:** one post-only limit at `ref × (1 − h + k)`.
* **Sell:** one post-only limit at `ref × (1 + h + k)`.
* **Rounding:** prices are rounded away from `ref` to the tick.
* **Inventory skew:** `k = −0.8 × h × clip(D / L, −1, 1)`.
  * `D` is the inventory's deviation from target, in USDT at `ref`.
  * `L` = 2,500 USDT is the inventory limit.
  * The quotes lean toward reducing inventory.
* **Size:** 500 USDT per side.
  * No buy if `D + 500 > L`.
  * No sell if `D − 500 < −L`.
  * Spot cannot sell coins it does not hold.
* **Lifetime:** each order lives for that one minute and is then replaced.

**Fills (conservative).**
* A buy fills only if the minute's low is **strictly below** its price.
* A sell fills only if the minute's high is **strictly above** its price.
* The fill is at the order price, full size, with the maker fee.

**Daily loss stop.** This is NEXUS's 1% default.
* It triggers when the book's P&L for the UTC day falls 100 USDT (1%) below the day's
  start.
* The inventory deviation is then closed at the next minute's open, with the taker fee
  plus 0.02% slippage.
* Quoting stops until the next UTC day.

**Perpetuals.**
* Target inventory 0: long or short up to 2,500 USDT.
* Actual funding is paid or received on the inventory at every funding time.

**Spot.**
* Long only.
* At the start, 2,500 USDT of the coin is bought at the taker cost. That coin quantity
  is the target.
* `D = (held quantity − target quantity) × ref`.
* P&L is measured as the **excess over holding that starting coin quantity without
  trading**, so the target inventory's price moves do not count as skill. The daily stop
  uses the same excess.

**Fees.** VIP 0, no BNB discount.
* Perpetuals: maker 0.02%, taker 0.05%.
* Spot: maker 0.10%, taker 0.10%.

## Variants (8, all reported, all judged)

* Perpetuals:
  * `h` = 0.05%, 0.10%, 0.20%;
  * volatility-scaled `h = max(0.03%, 2σ)`.
* Spot (wider, because the maker fee is 5× higher):
  * `h` = 0.15%, 0.20%, 0.30%;
  * `h = max(0.12%, 2σ)`.

## Gates

A variant passes only if all hold. P&L is pooled across the 6 symbols: 60,000 USDT, with
daily P&L divided by 60,000.

1. Net P&L > 0 after fees, funding and stops (spot: the excess is > 0).
2. Mean daily P&L > 0 with a stationary-bootstrap p-value (block 10, 5,000 resamples)
   significant after a **Holm correction across the 8 variants** (family α = 0.05).
3. Annualised Sharpe of daily P&L ≥ 1.0.
4. Maximum drawdown of the pooled book ≤ 10%.
5. Positive in at least 2 of 3 years (2024, 2025, 2026).
6. Still > 0 at 2× fees.
7. Still > 0 without the best symbol.
8. Still > 0 with a stricter fill rule, where the price must trade 0.02% beyond the order
   (a robustness check for queue position and latency).

**Reported:**
* fills per day;
* average and maximum inventory;
* days stopped;
* fees and funding;
* P&L per symbol and per year.

## Consequence

* **Pass:** a fast paper market-making bot (paper only).
  * Live only with the owner's approval.
  * Perpetuals would also need the owner to enable futures (1x, isolated).
* **Fail:** recorded and shown in the app. Not re-tuned.

## Known limitations

* Quotes refresh once a minute. Hummingbot's default is 30 seconds, and professional
  firms refresh in milliseconds.
* There is no historical order book. Queue position is unknown, so fills require trading
  through the order price (gate 8 tightens this).
* An order of 500 USDT is assumed fully filled when traded through. It is small next to
  these markets' volume.

---

## Results

_(appended after the single run)_

### Results of the single run (2026-11-26, harness commit ab2b88d)

Output: `research/backtesting/nexus_backtest/results/market_making_2026-11.json`, also on
the dashboard page **Strategy test**.
* **Data checked:** 408 perpetual archives (klines and funding) and 204 spot archives,
  all matching their checksums.
* Historical backtest only; no order was placed.

**FAILED: 0 of 8 variants pass. Every variant failed all 8 gates.**

| Variant | Fills a day per coin | P&L before fees (6 books) | Fees | P&L per day per 10,000 book | A real book empty after |
|---|---|---|---|---|---|
| perp h 0.05% | 617 | −171,699 | 386,693 | −89.9 | 111 days |
| perp h 0.10% | 380 | −96,643 | 237,585 | −53.8 | 186 days |
| perp h 0.20% | 127 | −20,701 | 78,992 | −16.1 | 623 days |
| perp vol-scaled | 162 | −25,520 | 100,862 | −20.4 | 491 days |
| spot h 0.15% | 114 | −53,017 | 357,911 | −66.2 | 151 days |
| spot h 0.20% | 80 | −34,931 | 250,284 | −45.9 | 218 days |
| spot h 0.30% | 38 | −15,090 | 117,728 | −21.4 | 468 days |
| spot vol-scaled | 105 | −38,815 | 328,235 | −59.1 | 169 days |

All figures are in USDT. The backtest kept trading past zero, so its percentage returns
(−166% to −931% of 60,000) mean that real books would have been emptied, as shown in the
last column.

* **Every year and every coin lost, in every variant.** Bootstrap p-values were 1.0.
* **The daily 1% loss stop fired on 95 to 4,310 of the 6,210 coin-days.**
* **The stricter-fill and 2× fee checks also lost.** The strict-fill rule lost slightly
  less on some variants, because it took fewer fills.
* **Funding was negligible:** −17 to −62 USDT in total. Inventory stayed near zero on
  average (about 300–420 USDT).

**Why.**
* **It lost even before fees.** A resting order is filled only when the price runs
  *through* it, so at the moment of the fill the price has already moved against the
  order. That loss is larger than the spread it earns.
* **A random walk with no fees reproduces it.** On a pure random walk with 60 price steps
  a minute, the same code lost about 19 USDT a day per book at h = 0.05%. The predicted
  overshoot effect for that walk was about 24 USDT a day. So this is the known
  adverse-selection effect, not a code error.
* **Why professional firms still profit.** They:
  * sit first in the queue, so they are filled on touches, not only on run-throughs;
  * pay zero or negative maker fees;
  * cancel within microseconds when the price moves.

  A small account refreshing once a minute behind the queue has none of these.

**Consequence.** No market-making bot is built. Not re-tuned.
