# Phase 15: continuous paper operation and evaluation

_Run: 2026-11-14 08:21:55 → 08:47:05 UTC (25 min), full production overlay (11
containers, Nginx/TLS, least-privilege DB role), live public market data from Binance
(official mirrors), Kraken and Coinbase. Mode: **paper**._

## What happened

| Area | Result |
|---|---|
| Service restarts | 0 (all services) |
| Error/critical log lines | 0 (market-data, trader, notifier, monitor, api) |
| WebSocket order-book streams | 6/6 connected, 0 disconnects; top of book fresh in Redis |
| Candles | ingested continuously; latest closed 5m bar on all 6 markets = 08:40 at 08:47 (correct: the 08:45 bar was still forming) |
| Order-book snapshots | 30 stored in the window |
| Performance snapshots | every 5 min (equity 9,999.99 virtual USDT; 3,333.33 per venue) |
| Signals / orders / trades | **0 / 0 / 0**: no strategy has passed validation, so none is enabled |
| Memory (whole stack) | ≈ 1.0 GB of 8 GB; CPU idle near 0% between cycles |

## Defects found only by continuous operation (fixed)

1. **Notifier**: blocking stream reads exceeded the Redis client's default socket
   timeout → a TimeoutError every idle cycle. Fixed with one Redis client factory with
   explicit timeouts (`nexus_common.redisutil`).
2. **Nginx**: resolved the `api` container once at startup; after the API container was
   recreated (new IP) every request returned **502**, which would have hit every deploy.
   Fixed with Docker DNS re-resolution (`resolver 127.0.0.11` + variable upstream);
   verified by forcing an IP change (172.18.0.9 → 172.18.0.12) with traffic still served.

## Performance evaluation

There is no paper P&L to evaluate: correctly so. The independent validation on two years
of real data (docs/ROADMAP.md) rejected all four strategies and the AI model with
negative out-of-sample expectancy after costs. Enabling any of them to "show activity"
would contradict the project's rules; forward paper results for an unvalidated strategy
would be expected to lose money and could be mistaken for evidence.

What *was* evaluated and is ready for a real paper period once a candidate passes:

- Paper fills against **real** order books (live test: Binance and Kraken, prices within
  the book and the limit; non-marketable orders never fill).
- End-to-end paper cycle (signal → risk → allocator → sizing → fill → position → stop
  exit → realized P&L, exactly within the 0.25% risk budget plus costs), tested.
- Daily performance reports, risk-limit events, kill switch, drawdown shutdown, tested.

## Recommended evaluation protocol (when a candidate passes validation)

1. Paper-trade it on the VPS for **at least 8 weeks** and **≥ 50 closed trades**.
2. Compare paper expectancy, win rate, slippage and drawdown with its out-of-sample
   validation; reject if paper expectancy ≤ 0 or slippage exceeds the modeled 5 bps.
3. Only then consider live, at minimum size, through every live gate.
