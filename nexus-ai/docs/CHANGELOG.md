# NEXUS changelog

Newest first. Architectural decisions: docs/PLATFORM_ARCHITECTURE.md (ADR log).

## 2026-11-23

- **Quant engine brief (Part 1 audit and first gaps):**
  * Integration map: `docs/INTEGRATION_MAP_2026-11-23.md`.
  * Statistical arbitrage module (`stat_arb.py`): Engle-Granger statistics verified
    against statsmodels; research only.
  * Liquidity and execution planner (`liquidity.py`): book walk, spread, slippage,
    depth, slicing, and the post-only vs IOC choice. Wired into the testnet round trip
    (REJECTED_BY_LIQUIDITY).
  * Market-condition breakdown for backtests (`market_conditions.py`).
  * Opt-in random-forest model family.
  * KuCoin and Gate.io in the capability registry (detected only).
- **Dashboard speed (owner: "very sluggish"):**
  * Cause: Supabase is in us-west-2, the server in Frankfurt (about 150 ms per query
    round trip). The 12 inline workers made blocking queries on the API's event loop
    (88,000 candle lookups so far), so every dashboard request waited behind them.
    Requests took 1.5–7.7 s while CPU sat at 1%.
  * Fix: inline workers run on their own event loop in a dedicated thread. The Binance
    guard's default Redis client is now kept per event loop. The sign-in lookup and
    `/system` queries also run off the loop.
- **Edge-decay monitor:**
  * CUSUM, a rolling window and R-drawdown detectors;
  * NORMAL / REDUCE / STOP, enforced in the bot and the main trader;
  * it only tightens; the owner's 2FA reset.
- **Research:** TSMOM grid (Han / Kang / Ryu design), 0 of 96 pass; CUSUM and volume /
  dollar / range bars.
- **Reliability:** bounded blocking Redis pools (free Key Value 50-connection limit).
- **Risk:**
  - aggregate open-risk limit (1% of equity) in the main risk engine;
  - weekly loss limit 3% (master directive Part 8);
  - earlier the same day: 30% spot exposure, 3 positions, 5% drawdown, futures 1x.
- **Monitoring:** `GET /metrics` (Prometheus, bearer `NEXUS_METRICS_TOKEN`) with process,
  database / Redis, worker restarts, Binance circuit, kill switch, market-data age and bot
  scan age.
- **Exchanges:** live capability registry for Binance, OKX, Bybit and Kraken (public CCXT
  detection; API and Exchanges page).
- **Trading bot:**
  * an EMA / RSI / MACD 5m strategy in monitor / paper modes;
  * a main-trader path after the owner's 2FA approval;
  * the live gate requires a key test under 7 days old showing spot permission.
- **Data:** 1,500 days of daily candles kept for daily-strategy warm-up.
- **Research:**
  - global strategy library (8 strategies × 3 timeframes, 1 pass);
  - spot-engine warm-up fix;
  - protections;
  - pre-trade checks.
