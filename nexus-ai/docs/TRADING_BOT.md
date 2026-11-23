# NEXUS trading bot: how it runs and how to operate it

## What it is

A worker named `bot` inside the existing NEXUS service (no second engine). Every 5
minutes, on each pair in `NEXUS_TRADING_PAIRS` (default BTC/USDT, ETH/USDT), it does the
following.
1. Loads the closed 5m candles that the market-data worker stores. These come from the
   Binance WebSocket and REST APIs, with gap repair.
2. Evaluates the strategy (`nexus_strategies/bot_strategy.py`):
   * entry needs all of: 5m close > EMA20 > EMA50; 15m EMA20 > EMA50 with a positive MACD
     histogram; 1h close > EMA50; RSI 52–70 and rising; MACD above its signal and rising;
     volume above 1.2× its 20-bar mean; and a target that covers 3× the trading costs;
   * stop: 1.5 ATR; take-profit: 2R; exit if the 5m close falls below EMA50 or MACD crosses
     down; time stop after 4 hours.
3. Applies the risk rules:
   * 0.25% risk per trade, 0.5% maximum, sized from equity and the stop distance with fees
     and slippage;
   * 1% daily loss limit; 3% drawdown halt (stays until the owner reviews);
   * at most 2 positions, one per pair, no averaging down;
   * cooldown and stop-loss guards;
   * the kill switch; stale market data blocks entries.
4. Places orders through the NEXUS order manager. Its last-gate checks are REDUCING state,
   a 50% fat-finger cap, a 5% price band and an entry rate limit. Duplicate orders are
   blocked by the database. The state of every order is reconciled before anything is
   assumed.
5. Persists every order, fill, position and qualifying signal, and recovers from the
   database after a restart.

## Modes

| Mode | How | What happens |
|---|---|---|
| Market monitoring | `NEXUS_BOT_MODE=monitor` | The bot worker evaluates and records signals; no orders. |
| Paper | `NEXUS_BOT_MODE=paper` (default) | The bot worker fills against the **live Binance order book** with a **simulated** 10,000 USDT balance (portfolio `nexus-bot`). |
| Binance spot live | Owner steps below | The **main trader** runs the same strategy (`bot_ema_rsi_macd`, base 5m and confirm 15m, identical rules) with real orders, behind every live gate. |

## Going live (owner steps, in this order; none can be done by code)

1. **Key test.** Dashboard → Security → Binance credential "Spot" → **Test**. The live gate
   now requires a test from the last 7 days that shows spot trading permission, with
   withdrawals disabled.
2. **Approve the strategy.**
   * Dashboard → Trading bot → "Owner approval": type the acknowledgement and your 2FA
     code. This is audited, and you can withdraw it at any time without 2FA.
   * The main trader then runs it. In paper mode it trades the simulated `main` portfolio,
     which rehearses the live path.
3. **Seed the live ledger.** Dashboard → Settings / Operating mode → fund the live ledger
   with the USDT amount the bot may use (at most what is on Binance).
4. **Set live configuration.** In Render → Environment, set:
   * `NEXUS_TRADING_MODE=live`
   * `NEXUS_LIVE_TRADING_ENABLED=true`
   * `NEXUS_LIVE_TRADING_ACK` = the exact phrase shown on the Settings page
   Then redeploy. In live mode the server runs market data, the LIVE trader and
   monitoring. The paper bot and the paper forward tests pause. Until every gate is met,
   the live trader refuses to start and retries every few minutes. Its log says which
   gate is missing.
   * Fixed 2026-11-23: before this, live mode on the single server started no worker at
     all, so the switch could never trade.
5. **Approve live trading** with 2FA on Settings → Live trading. The card lists any
   blocker left.
6. **Watch the first trade.** NEXUS reconciles every order with Binance by client order ID.
   The kill switch (Risk page) stops everything immediately.

**Known limit:** stop-losses are enforced by NEXUS each cycle. They are market exits when
the bid reaches the stop; there is no resting stop order on Binance. If the NEXUS service is
down, the stop waits until it is back. Placing exchange-side stop-limit orders for live
positions is the next improvement.

## Where to see it

The dashboard page **Trading bot** (`/trading-bot/`) shows:
* the live checks per pair, and why there is NO TRADE;
* open positions with stop-loss and take-profit;
* trade history, orders, P&L and the limits.

API: `GET /api/v1/trading-bot` (authenticated).

## Honest expectation

Indicator strategies of this kind lost money after costs in every NEXUS backtest (5m and
15m costs are large relative to moves). The bot runs so the pipeline can be watched end to
end in paper mode. No profit is implied.
