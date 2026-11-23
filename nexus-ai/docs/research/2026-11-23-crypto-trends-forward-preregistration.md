# Pre-registration: forward PAPER test of the crypto trends ensemble

Follows the PASS in `2026-11-23-crypto-trends-preregistration.md`. Committed before the
test's first day. Paper only: no real orders, no real money.

## What runs
* The unchanged research code (`nexus_backtest.crypto_trends`, commits 26a6133 and later):
  the same rules, BTC+ETH with equal halves, 25% target volatility, a 100% cap per coin, a
  20% rebalance threshold, and 10 bps fee + 5 bps slippage.
* Every day after the 00:00 UTC daily close, worker `trend-paper` does the following:
  * downloads the last 1,000 closed daily candles per coin from Binance;
  * replays the strategy from **START = 2026-11-24 00:00 UTC** with 10,000 virtual USDT;
  * appends one ledger row per new day: target and executed weights, trades, costs,
    paper equity, and the closes used.
* Days already recorded are never rewritten. If a replay disagrees with a recorded day by
  more than 0.1% of equity, a divergence is recorded and shown.
* Fills happen at the next daily open with the stated costs (as in the backtest). Real
  order-book execution is not part of this test.

## Review at day 180 (2027-05-23). All required:
1. **Integrity:** zero divergences between the recorded days and the replay.
2. **Return:** the 180-day paper return is >= -6.6% (the backtest's 5th percentile of all
   180-day windows, 2018-2026).
3. **Max drawdown:** <= 15% (the backtest's worst 180-day drawdown was 12.4%).
4. **Data:** no data gap longer than 3 consecutive days left unresolved.

## Meaning
* 180 days of a slow trend strategy cannot prove profitability: it averages about 16%
  exposure and has few trades. This test checks that live behaviour matches the backtest
  and that nothing breaks.
* A pass makes the strategy *eligible* for live spot trading. That would still need:
  * the owner's explicit activation (2FA and the typed confirmation);
  * a dedicated outbound IP;
  * a funded live ledger.
* A fail is recorded, and the strategy stays paper-only.
* Nothing here enables live trading.
