# Pre-registration: funding-rate carry (spot long + perpetual short) on BTC and ETH

Written before any result was computed. The test is run once; failing is a valid outcome.

## Hypothesis
A delta-neutral position (long spot, short the same quantity of the USDT-M perpetual),
held only while recent funding is clearly positive, earns a positive risk-adjusted return
after all costs, because shorts receive funding from longs.

## Data
Binance spot daily klines and USDT-M perpetual daily klines and funding rates from
data.binance.vision, BTCUSDT and ETHUSDT, 2020-01 to 2026-10. Missing months abort.

## Rule (fixed in advance, no parameter search)
- Signal for day t+1: mean of the funding rates settled during days t-6..t (21 settlements)
  is above 0.01% per 8 hours (Binance's base interest component). Decided from past
  settlements only; applied from the next day.
- In position: long spot quantity q and short perp quantity q, equal notional N at entry.
- Capital per symbol = 2N (spot fully paid; perp collateral equal to its notional, 1x),
  so the hedge has no liquidation risk below a ~100% price rise between daily rebalances.
- Daily P&L = q*(spot change) - q*(perp change) + funding received on q*perp close
  (a short receives a positive rate).
- Daily rebalance back to equal notional N on both legs.

## Costs
- Spot: 10 bps fee + 2 bps slippage per side. Perp: 5 bps fee + 2 bps slippage per side.
- Entry and exit trade both legs. Daily rebalancing trades the notional change of each
  leg (|price change| x N) at the same rates.

## Portfolio
Equal capital in BTC and ETH. Return on total capital (cash when out of position earns 0).

## Pass gates (all required, full period 2020-01 to 2026-10)
1. Annualised return on capital >= 4% after costs.
2. Annualised Sharpe >= 1.0.
3. Max drawdown <= 10%.
4. Positive in >= 60% of calendar quarters.
5. t-statistic of daily returns >= 2.0.
Also reported: always-in-position variant and per-year results, for comparison only.

## Decision
Pass -> candidate for paper trading only (never live without owner approval).
Fail -> recorded as failed; this rule is not re-tuned.
