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

## Modes (`NEXUS_BOT_MODE`)

| Mode | What happens |
|---|---|
| `off` | The worker does not start. |
| `monitor` | Market monitoring: evaluations and signals only, no orders. |
| `paper` (default) | Orders fill against the **live Binance order book** (latency, partial fills, Binance's order rules) with a **simulated** 10,000 USDT balance in the `nexus-bot` paper portfolio. |
| live | **Not available from the bot.** Real orders go only through the main trader's live gate, after the owner's explicit activation. |

Live trading stays **disabled** until the owner enables it.

## Before real orders could ever be enabled (owner steps)

The last connection check (22 Nov 2026) showed the following.
1. **The production API key has spot trading switched OFF** (`spot_trading_enabled: false`).
   In Binance API management, enable "Spot & Margin Trading" on that key and keep
   withdrawals disabled. Never share your password or seed phrase.
2. **The account holds 0 USDT.** Real orders need funds.
3. **The Render egress IP is shared.** Binance can rate-limit or ban it because of other
   tenants. A dedicated IP (Oracle Always Free, or a paid static IP) avoids this.
4. Then the existing live gate applies:
   * owner 2FA approval with the acknowledgement phrase;
   * the key re-verified;
   * the kill switch clear;
   * a supervised small testnet order and then a live order (F2);
   * the full test suite green.

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
