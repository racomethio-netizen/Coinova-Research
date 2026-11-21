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

## Result (run once, 2026-11-21, after this file was committed as f187883)

Data: 2,496 daily bars per symbol (2020-01-01 to 2026-10-31), funding every day; largest
daily spot/perp close gap 0.7% (BTC), 1.0% (ETH). The rule was in position 30% of days.

| Metric | Pre-registered rule | Always-on (comparison only) |
|---|---|---|
| Annual return on capital | 3.91% | 5.51% |
| Sharpe | 5.21 | 7.29 |
| Max drawdown | 0.89% | 0.93% |
| Positive quarters | 43% (12/28) | 89% (25/28) |
| t-statistic | 13.6 | 19.1 |

Per year, rule: 2020 8.3%, 2021 15.7%, 2022 -0.1%, 2023 0.8%, 2024 3.2%, 2025 -0.3%, 2026 0.0%.
Per year, always-on: 2020 10.0%, 2021 17.0%, 2022 0.3%, 2023 3.5%, 2024 5.7%, 2025 1.8%, 2026 0.6%.

**FAIL** on gate 1 (3.91% < 4%) and gate 4 (43% < 60%; quarters out of position count as
non-positive). The edge itself is real and low-risk in this sample, but it has shrunk to
about 1-2% a year in 2025-2026, below cash rates. Not re-tuned. The always-on variant
passed every gate here, but it was a comparison, not the hypothesis: it may only be
judged on data it has not seen, i.e. a forward paper test from now on.

Limits of this test: daily closes (intraday basis spikes not modelled); exchange and
counterparty risk not modelled; capital assumed 1x-collateralised with daily rebalancing.
