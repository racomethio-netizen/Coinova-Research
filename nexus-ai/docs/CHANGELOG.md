# NEXUS changelog

Newest first. Architectural decisions: docs/PLATFORM_ARCHITECTURE.md (ADR log).

## 2026-11-27

- **Night scalper (the public idea behind popular MT5 robots): FAILED on both Exness
  accounts.** It was pre-registered before its data was downloaded.
  * Rules: 12 pairs, real bid/ask fills from Exness ticks, NEXUS limits.
  * Results:
    * Standard 2015–2026: −0.34R a trade, halted at −5% in 2016;
    * Raw Spread 2020–2026: −0.29R, halted in 2021.
  * With the halts switched off, every year lost money.
  * The Exness converter now keeps ask prices and supports any pairs and account types.
  * Report: `docs/research/2026-11-27-night-scalper-report.md`.

- **NQS-1 tested: FAILED in all three profiles.**
  * Data: Exness's public tick archive, 11 years, Standard-account bid and ask, built
    by the new `nexus_backtest.exness_ticks`.
  * Out of sample:
    * scalping −0.04R a trade (113 trades);
    * intraday −0.01R (519);
    * swing −0.15R (48).
  * Each setup alone was worse; the ML filter did not help.
  * Results: `results/nqs1_2026-11.json` and `docs/research/2026-11-27-nqs1-report.md`.

- **NQS-1 adaptive forex strategy (owner brief).** Research only.
  * Strategy package `nexus_strategies/nqs1`:
    * seven regimes select among time-series momentum, multi-timeframe trend, breakout
      and mean reversion;
    * entries need two or more confirmations, plus spread, cost and expected-value
      checks;
    * NEXUS risk on one shared book, with pending orders counting toward exposure and
      open risk;
    * ATR stop, take profit, break-even, trailing stop, opposite-signal, time and
      emergency exits;
    * carry at 21:00 UTC rollovers (Wednesday counts triple);
    * an optional ML veto that can only refuse a trade.
  * Research harness `nexus_backtest.nqs1_bt`:
    * walk-forward (24 months of training, 6 of testing) traded as one continuous book;
    * Monte Carlo, ±20% sensitivity, each setup compared with the adaptive combination;
    * the ML filter and a 2× cost stress;
    * a generated comparison report.
  * Pre-registered before any data: `docs/research/2026-11-27-nqs1-preregistration.md`.
    No result yet.
  * Bugs found by the new tests and fixed before any real run:
    * costs were never deducted from equity;
    * the spread was partly counted twice;
    * exposure limits ignored orders waiting to fill;
    * the mean-reversion target used its own bar's close.
- **Exness bridge history export** (`--export-history`): M5 for 3 years, H1 for 12
  years and all D1 history for the 5 pairs.
  * NEXUS stores them gzip-compressed in `history_chunks` (migration 0023), with a
    60 MB cap.
  * Owner endpoints serve a manifest and CSV downloads.
  * All markets shows the received history.
  * Deployed 2026-11-27 02:11 UTC together with the Exness bridge (0022) and the FX
    research.

## 2026-11-26

- **Forex research (owner: "institutions and banks").**
  * Researched bank and institutional FX practice: BIS on internalisation and execution
    algorithms, academic carry, momentum and value, Deutsche Bank's Currency Returns
    index.
  * Pre-registered test of 5 styles on the G10 currencies, 2004–2026: none passed.
    * Carry: +1.1% a year, Sharpe 0.30.
    * Momentum: −1.2% a year.
  * Results are on the Strategy test page.

- **Exness (MetaTrader 5) read-only bridge.** The owner's forex broker.
  * The Windows bridge program (`apps/mt5_bridge`) has no order code.
  * The bridge API uses hashed, revocable tokens (migration 0022).
  * NEXUS stores contract details and closed daily bars in UTC, and keeps a masked
    account summary.
  * All markets has an Exness card and token management. The research view includes
    Exness prices.
  * There is no order path.

- **Multi-asset Stage 2:** trading sessions (24/7, FX 24/5, NYSE, CME via
  `exchange_calendars` 4.13.2), the instrument registry, base-currency conversion that
  never guesses, and migration 0021 (asset class, instrument type, contract multiplier on
  `trading_pairs`). The research view shows open or closed per instrument.

- **Multi-asset Stage 3: research data, two pre-registered tests, research worker.**
  * Forex majors and NASDAQ-100 trend following (2004–2026): FAILED.
  * Volatility-managed NASDAQ-100 (1990–2026): PASSED. Calmer than holding, but lower
    return.
  * New worker `multiasset-research`:
    * stocks research plus the NASDAQ-100 paper forward test from 2026-11-27;
    * forex and commodities research (FRED and Binance stock and commodity perpetuals).
  * Defaults: stocks PAPER (paper only), forex and commodities RESEARCH. No broker
    connector exists, so LIVE is impossible for them.

- **Multi-asset platform, Stage 1** (owner brief "Universal multi-asset autonomous trading
  platform").
  * Audit with 5 reports: docs/MULTI_ASSET_AUDIT_2026-11-26.md.
  * Shared market registry for crypto, stocks, commodities and forex
    (`nexus_common.markets`), with honest connector status: only crypto connectors exist.
  * A per-market switch OFF / RESEARCH / PAPER / LIVE:
    * durable in the audit log;
    * narrows, never widens, the operating mode;
    * LIVE needs the market's own phrase, 2FA and its live checks.
  * Wired into the crypto trader, the spot engine and the bot. The default (PAPER) leaves
    behaviour unchanged.
  * API `/api/v1/markets` and dashboard **All markets**. Docs: docs/MULTI_ASSET.md.

- **Medallion-style market-neutral model (pre-registered, one run): FAILED.**
  * Tested on all 918 Binance USDT perpetuals, delisted included: 16 signals, Ridge and
    XGBoost walk-forward, long 10 / short 10 daily, 2022-01 to 2026-10.
  * Net: Ridge −85.5%, XGBoost −65.8%.
  * The models earned on price, but the funding paid on their shorts and the daily
    trading costs cancelled it.
  * Results are on the Strategy test page (`/api/v1/research/statarb-ml`).

- **Market making (pre-registered, one run): FAILED, 0 of 8 variants.**
  * Tested Hummingbot-style two-sided quoting on 6 coins, perpetuals and spot, 2024-01 to
    2026-10.
  * Every variant lost money even before fees. A real 10,000 USDT book would have been
    empty in 111 to 623 days.
  * Results are on the Strategy test page (`/api/v1/research/market-making`).

- **Batch 3 (pre-registered, one run): grid v2, volatility targeting, BTC/ETH pairs.**
  * Grid v2 (sell held coins on a regime change), on unseen 2020–2023 data: FAILED, worse
    than v1 (−51.1% vs −43.5%).
  * Volatility-targeted holding:
    * BTC/ETH 50/50 PASSED: Sharpe 0.94 vs 0.86 held, worst drop −59% vs −77%, lower
      return than holding;
    * BTC alone and ETH alone failed.
    * Paper forward test from 2026-11-27 (worker `voltarget-paper`).
  * BTC/ETH pairs on perpetuals: FAILED (−13.2%).
  * Results are on the Strategy test page (`/api/v1/research/batch3`).

- **Rebalancing bot and cross-sectional momentum (pre-registered).**
  * Rebalancing R3 (BTC 40 / ETH 40 / USDT 20, 5-point band) PASSED: +38.4% vs +28.4%
    held, worst drop −51.0% vs −53.0%.
    * Paper forward test from 2026-11-27 (worker `rebalance-paper`).
  * Cross-sectional momentum: 0 of 6 passed.
  * Results are on the Strategy test page.

- **Adaptive grid system (PAPER ONLY), owner master command.** See docs/ADAPTIVE_GRID.md.
  * Built:
    * dynamic universe (spot and futures);
    * 8-class grid regime;
    * adaptive grid engine (spot long; futures long, short and neutral at 1x isolated,
      with funding and liquidation);
    * central grid risk manager and allocator;
    * autonomous orchestrator worker with atomic persistence and exact restart recovery;
    * migration 0020;
    * API with owner controls and limits;
    * Adaptive grid dashboard;
    * backtester.
  * Backtest (pre-registered, walk-forward, 12 markets): **FAILED**, −24.6% out of
    sample.
    * Profitable in sideways and weak-bull markets.
    * Losses when ranges break down while holding inventory.
    * Not re-tuned. The system runs as a paper forward test.

- **Freqtrade community strategy test (pre-registered, one run).**
  * 44 public strategies ran unchanged in Freqtrade 2026.9 on 2024-01 to 2026-10 Binance
    data with 0.1% fees.
  * 0 passed, 6 made money and 38 lost; BTC hold made +83.5% over the same window.
  * Results are on the dashboard page **Strategy test**.
- **Grid bot (paper).**
  * Worker, append-only `grid_paper_events` (migration 0019), `/api/v1/grid-bot`, and an
    owner-only restart after a stop-loss.
  * Dashboard page **Grid bot**.

## 2026-11-23

- **Live switch fix (owner: "real money switch doesn't work"):**
  * In live mode the single-server runner started no workers, so even a fully approved
    live setup could never trade. It now starts the live workers (market data, the gated
    live trader, monitoring).
  * The live trader releases its connections when a gate refuses.
  * The Security page's approval button can no longer double-send a single-use 2FA code
    (the owner's two attempts at 16:31 were both refused by the 2FA check). It needs a
    6-digit code and clears it after each attempt.
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
