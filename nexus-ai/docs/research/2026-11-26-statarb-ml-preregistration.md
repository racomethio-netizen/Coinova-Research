# Pre-registration: "Medallion-style" market-neutral statistical model on Binance perpetuals

_Committed before any run. Results are appended once below the line; nothing above it
changes afterwards._

## Why

The owner asked for a bot like Renaissance Technologies' Medallion Fund.

Its code is secret, but its principles are public (G. Zuckerman, *The Man Who Solved the
Market*, 2019):
* many small, weak statistical signals combined in one model;
* traded across many assets at once, with longs and shorts balanced so that the overall
  market direction does not matter;
* short holding periods of about a day;
* strict testing on data the model never saw.

This test applies those principles to crypto. It cannot copy Medallion's real advantages:
* decades of proprietary data;
* hundreds of researchers;
* near-zero trading costs;
* leverage.

## Data

* **Source:** Binance official USD-M perpetual daily klines and funding rates from
  data.binance.vision, every archive checked against its `.CHECKSUM`.
* **Coverage:** **every USDT perpetual that ever listed, including delisted ones**, so the
  test is not flattered by survivorship.
* **Excluded:**
  * stablecoins: base USDC, BUSD, TUSD, FDUSD, USDP, DAI, USDE, USD1;
  * gold and silver: XAU, XAG, XAUT, PAXG;
  * index perpetuals: BTCDOM, DEFI, FOOTBALL, BLUEBIRD;
  * any coin whose daily returns over the previous 60 days have a standard deviation
    below 0.2% (stablecoin-like).

## Universe (point in time)

On each day `d`, a coin is eligible if all of these hold:
* it has at least 60 daily bars up to `d − 1`;
* it has a bar on `d − 1` (still trading);
* it is among the **60 largest by average quote volume over the 30 days to `d − 1`**.

## Features

There are 16. Each uses data up to the close of `d − 1` and is ranked across the
eligible coins to [−0.5, 0.5].

* **Returns:**
  1–5. returns over 1, 3, 7, 14 and 30 days.
* **Volume and volatility:**
  6. volume shock: log(yesterday's quote volume / 30-day average);
  7. realised volatility over 14 days;
  8. yesterday's high-low range / close;
  9. where yesterday closed within its range: (close − low) / (high − low).
* **Position in the recent range:**
  10. distance from the 30-day high;
  11. distance from the 30-day low.
* **Funding:**
  12. average funding over the last 3 days;
  13. that average minus the average of the 7 days before.
* **Relation to BTC and size:**
  14. 60-day beta to BTC;
  15. 7-day return minus beta × BTC's 7-day return (residual);
  16. log of 30-day average quote volume (size).

**Target:** the coin's return on day `d`, ranked across the eligible coins to [−0.5, 0.5].

## Models

Each model is trained walk-forward:
* It is retrained on the first day of every month.
* It trains on all eligible coin-days of the previous 365 days whose target day is
  before that month.
* It predicts every day of that month.

The two models:
* **A, Ridge regression:** alpha = 10.
* **B, gradient-boosted trees (XGBoost):**
  * max_depth 3, 300 trees, learning rate 0.05;
  * subsample 0.8, colsample_bytree 0.8;
  * min_child_weight 50, seed 0.

**Baseline** (reported, and used in gate 7): 1-day reversal, with score = −(1-day return
rank).

## Portfolio

* **Each day at 00:00 UTC:**
  * long the 10 coins with the highest prediction and short the 10 lowest, 5% of equity
    each;
  * 100% gross and 0% net, with no leverage beyond 1x.
* **Rebalancing:** to target weights every day. Costs are paid on the traded change in
  weight.
* **Costs:** 0.05% taker fee + 0.05% slippage per side, with a 2× stress.
* **Funding:** actual funding rates. Longs pay positive funding; shorts receive it.
* **Delisting:** a coin that stops trading is closed at its last close.

## Test window

* Out-of-sample predictions: **2022-01-01 to 2026-10-31**.
* The first training year is 2021. Features warm up from 2020.

## Gates

Each model is judged separately; a model passes only if all hold.

1. Net return > 0 after costs and funding.
2. Annualised Sharpe ≥ 1.0.
3. Mean daily return > 0 with a stationary-bootstrap p-value (block 10, 5,000
   resamples) < 0.05 after a **Holm correction across the two models**.
4. Maximum drawdown ≤ 25%.
5. Positive in at least 4 of the 5 years (2022, 2023, 2024, 2025, 2026 to October).
6. Still > 0 at 2× costs.
7. Sharpe higher than the 1-day reversal baseline under the same rules and costs, so the
   model must add something over the simplest signal.
8. Still > 0 after removing the P&L of the 3 coins that contributed most.

**Reported:**
* turnover and the number of eligible coins per day;
* P&L of the long side and the short side;
* funding and costs;
* per-year results;
* what each model learned (ridge coefficients and tree feature gains).

## Consequence

* **Pass:** a paper forward test of the passing model, market-neutral on perpetuals.
  * Paper only.
  * Live needs the owner's approval and futures enabled (1x, isolated).
* **Fail:** recorded and shown in the app. Not re-tuned.

## Known limitations

* Trades are assumed at the 00:00 UTC price that the features used. The 0.05% slippage
  partly covers the delay.
* Delisted coins are assumed closed at their last close. Binance announces delistings
  days ahead, but a real exit can be worse.
* Shorting small coins can be restricted in practice: position limits and high funding.

---

## Results

_(appended after the single run)_
