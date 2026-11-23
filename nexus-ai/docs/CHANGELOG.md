# NEXUS changelog

Newest first. Architectural decisions: docs/PLATFORM_ARCHITECTURE.md (ADR log).

## 2026-11-23

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
