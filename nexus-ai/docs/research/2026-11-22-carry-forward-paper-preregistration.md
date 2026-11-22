# Pre-registration: forward paper test of always-on funding carry

Committed BEFORE the test starts. The rules, costs and pass thresholds below do not change
during the test. Interim numbers are shown on the dashboard, but the only decision is
made once, at day 90.

## Why this test

The 2026-11-21 historical test (docs/research/2026-11-21-carry-preregistration.md) failed
its pre-registered rule. The always-on variant looked better (5.5% a year) but it was
chosen after seeing the data, so it can only be judged on data nobody has seen: from
the start of this test onward.

## Position (paper only, real prices)

* Capital: 10,000 USDT (virtual), split equally between BTCUSDT and ETHUSDT.
* Per symbol: long spot worth N = 2,500 USDT and short the USD-M perpetual worth N
  (1x, isolated margin N). Capital per symbol 2N = 5,000. Delta neutral.
* Entry at the first run after deployment: spot bought at the spot ASK, perpetual sold at
  the perpetual BID. Entry costs: spot 0.12% and perpetual 0.07% of each leg's notional
  (fee + slippage, as in the historical test).
* Funding: Binance's actual settled funding rates (public `fundingRate` history),
  applied to the short's notional at each settlement's mark price. Positive funding is
  received; negative funding is paid.
* Rebalance: checked hourly. If the spot leg's value and the short's notional differ by
  more than 5% of N, both legs are reset to equal notional at current prices, paying the
  same cost rates on the turnover.
* Marks: hourly, at exit prices (spot BID, perpetual ASK) and net of the exit costs, so the
  equity shown is what closing everything would return. Conservative by construction.
* Data gaps: if Binance data is unavailable (for example a location refusal or a rate-limit
  ban), the hour is recorded as a gap. Nothing is estimated or interpolated.

## Decision (once, at day 90)

PASS only if all hold over the 90 days:

1. Annualised net return on the 10,000 capital >= 4%.
2. Maximum drawdown of hourly equity <= 2%.
3. Positive calendar weeks >= 60% (complete weeks only).
4. Data gaps < 5% of hours; otherwise the result is INCONCLUSIVE, not a pass.

A PASS means "worth considering with real money after a futures account exists and the
owner decides"; it is not a promise of future returns. A FAIL ends this idea.

## Limits stated up front

* Binance futures are not available on the owner's account today, so this cannot be run
  with real money now regardless of the result.
* Not modelled: exchange or counterparty failure, liquidation of the short in an extreme
  spike (1x isolated margin needs a ~100% price rise to liquidate), funding-interval
  changes, borrowing or transfer delays between spot and futures wallets.
