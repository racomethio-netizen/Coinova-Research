# Pre-registration: daily time-series momentum on BTC/ETH USDT-M perpetuals

Written before any result was computed. The test is run once; failing is a valid outcome.

## Hypothesis
Holding each perpetual LONG when its close is above the close L days earlier and SHORT
otherwise, re-evaluated once per day, earns a positive return after costs and funding.

## Data
Binance USDT-M perpetual daily klines and funding rates from data.binance.vision,
BTCUSDT and ETHUSDT, 2020-01 to 2026-10. Missing archive months abort the test.

## Rules (no look-ahead)
- The position for day t+1 is decided from closes up to and including day t, and earns
  the close-to-close return of day t+1. Equal weight per symbol (each 50% of notional).
- Costs: 7 bps per side (5 bps taker fee + 2 bps slippage) on every position change;
  a flip long<->short costs two sides.
- Funding: real archive rates; a long pays a positive rate, a short receives it.
- Leverage: none in the test (returns per unit of notional).

## Parameter choice (walk-forward)
L in {10, 20, 40, 60, 90}. For each window, L is chosen on the previous 365 days by
annualised Sharpe of the portfolio, then judged on the next 90 days only.

## Pass gates (all required, out-of-sample)
1. Mean daily return > 0 and annualised Sharpe >= 0.5.
2. Positive in >= 55% of test windows.
3. t-statistic of daily returns >= 1.0 (one-sided; reported, not a guarantee).
4. Max drawdown of the out-of-sample curve <= 35% (unlevered).
Also reported: buy-and-hold over the same days, for comparison only.

## Decision
Pass -> eligible for paper trading only (never live without owner approval).
Fail -> recorded as failed; no re-tuning of this hypothesis.
