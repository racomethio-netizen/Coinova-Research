# NEXUS changelog

Newest first. Architectural decisions: docs/PLATFORM_ARCHITECTURE.md (ADR log).

## 2026-11-28

- **Unified brief Phase 5: the strategy orchestrator** (owner: "Phase 5").
  * `nexus_decision.orchestrator` gives one BUY / SELL / NO_TRADE per instrument,
    forex and crypto, with every reason.
    * **Only a strategy module that passed its pre-registered test can propose a
      trade.** The highest tested result after costs wins.
    * **Blockers (any one gives NO_TRADE):**
      * the market switch is OFF;
      * the kill switch is engaged;
      * the risk state, which fails closed when unknown;
      * stale data or a closed forex market;
      * a scheduled economic event;
      * the spread is above 0.25 of the hourly ATR, or crypto volume is below
        20 million USDT a day.
    * **Signal quality:** at least 2:1, a fresh signal bar, direction matching the
      regime, and no high-volatility regime for trend or breakout modules.
  * **Worker** (`nexus_api.orchestrator_worker`, paper mode):
    * runs every 5 minutes;
    * higher-timeframe bars are cached until a new bar can exist;
    * the crypto swing module's stance comes from its forward paper test;
    * other modules' signals come from the forward observer when switched to OBSERVE.
  * **Log:** migration 0025 adds `orchestrator_decisions`, append-only. A row is
    written on change and at least hourly; rows are pruned after 180 days.
  * **API and dashboard:** `GET /api/v1/orchestrator` and the *Strategy orchestrator*
    page.
  * Proposals only: the independent risk engine and execution still decide (Phases 6
    and 7). Nothing places an order.

## 2026-11-27

- **Unified brief Phase 4: intelligence** (owner: "Phase 4").
  * **Economic calendar and event blackout** (`nexus_news.calendar`,
    `data/economic_calendar.json`).
    * 95 scheduled events, Dec 2026 to Dec 2027: Fed, ECB, BoE, BoJ, RBA and SNB
      decisions, US CPI and the US jobs report.
    * Every date was copied from the publisher's official page and verified against the
      page text, with the source and the date it was read.
    * New entries pause from 30 minutes before to 30 minutes after an event: 90 after
      the Fed, to cover its press conference.
    * When a publisher prints no time (BoE, BoJ, RBA), the whole local day is blocked.
      Nothing is inferred.
    * Exits are never blocked. A coverage warning appears 45 days before a source's
      published schedule runs out.
  * **Forex + crypto market intelligence:** `GET /api/v1/intelligence/markets` and a
    *Market intelligence* page.
    * One snapshot per instrument: trend by timeframe, RSI, ADX, ATR, daily VWAP
      (tick volume for forex), support and resistance, liquidity, the FX session, a
      regime label and event risk.
    * Stored closed bars only.
  * **Machine-learning trade filter (meta-labelling) tested once, FAILED**
    (`nexus_ai.meta_filter`, `nexus_backtest.ml_filter_bt`; pre-registered in a676605
    before the run).
    * LightGBM + XGBoost + CatBoost with isotonic calibration and purged walk-forward
      (2020 to 2026), used only to block signals of the forex scalping and intraday
      breakout modules.
    * Out-of-sample AUC 0.485 and 0.514: no predictive power.
    * Filtered results: −0.140R and −0.090R per trade (unfiltered −0.098R and −0.084R).
    * The drift check found a strong volatility shift: PSI up to 2.3 in 2021.
    * No filter is attached and no model is registered.

- **Unified brief Phase 3: the six strategy modules** (owner: "Phase 3").
  * **One framework** (`nexus_strategies.modules`):
    * each module has its own settings, signal rule, historical test, forward record
      and on/off switch;
    * every plan is at least 2:1 reward/risk, risks 0.25% and holds at most 3
      positions;
    * the historical test and the forward record use one simulator
      (`modules.sim`), so they cannot disagree;
    * forex fills use the bid/ask with each bar's real Exness spread; crypto fills pay
      Binance fees and slippage.
  * **Tested once, rules written first.** Pre-registration (`docs/research/
    2026-11-27-strategy-modules-preregistration.md`), committed before the run as
    f7d0c6f; results in e9d7831.
    * All four new modules **FAILED** after costs:
      * forex scalping: −0.103R a trade over 6,825 trades;
      * forex intraday breakout: −0.093R over 4,222;
      * forex swing: −0.130R over 281;
      * crypto scalping: −0.117R over 240.
    * The breakout-retest re-run agrees with its 23 Nov failure.
    * "Catching Crypto Trends" passes all six gates again (Sharpe 1.11); it remains the
      one module with evidence.
    * A diagnostic added after the run (not a gate): before costs the forex rules have
      no edge, and the crypto rules have a small one that fees remove.
  * **Registry and switch.** Migration 0024 adds `strategy_modules` and
    `strategy_module_signals`.
    * `GET /api/v1/strategy-modules`.
    * Owner-only `POST /api/v1/strategy-modules/{code}/activation`, audited:
      * OFF and OBSERVE are allowed;
      * PAPER is refused in this phase, and also refused by a database constraint
        unless the module passed its test.
  * **Forward observation** (`nexus_backtest.module_observer`, a new paper-mode worker):
    * runs only for modules switched to OBSERVE;
    * records each new signal once, from NEXUS's stored candles, and resolves it with the
      same simulator;
    * places no orders.
  * **Dashboard:** a new *Strategy modules* page.

- **Unified brief Phase 2: market-data repair** (owner: "Phase 2").
  * **Forex and gold bars stored by NEXUS.** The Exness read-only bridge (version 1.1)
    now also sends closed M5 and H1 bars of EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF,
    USDCAD, NZDUSD and XAUUSD. Before this, intraday forex data existed only on the
    owner's PC.
    * NEXUS checks every bar before storing it (`nexus_market.fx_bars.check_bars`):
      well-formed, finite, positive, consistent OHLC, on the timeframe grid, closed, no
      duplicates. Rejected rows are counted by reason and never repaired. Unusually
      large bars are kept and flagged.
    * Stored in `market_candles` (`5m`/`1h`, source `mt5`). Retention is 45 days for M5
      and 400 days for H1, pruned at most hourly.
    * Backfill on request. NEXUS asks for a whole series when it is new or short, or
      when the recent bars do not reach the stored ones (the bridge was offline). The
      bridge sends two series per cycle, every 30 s, until done.
    * Data health: `GET /api/v1/markets/forex-data` and a table on All markets. It shows
      status, coverage and gaps over FX-session hours only (weekends are not gaps), the
      last batch's validation counts, and the spread against the typical M5 range.
  * **Redis errors at every restart fixed** (`nexus_common.redisutil`, `workers.py`).
    * Cause: during a deploy the old and the new instance run side by side. With 17
      inline workers each they exceeded Render Key Value's 50-connection limit, so
      every deploy logged "Timeout connecting to server" / "No connection available"
      and restarted workers.
    * Fix, part 1: opening a Redis connection is retried with jittered back-off (6
      attempts, about 20 s). Only the connection set-up is retried, never a command,
      so nothing is ever sent twice. A wrong password fails at once.
    * Fix, part 2: workers wait up to 30 s (was 5 s) for a free pool slot.
    * Fix, part 3: the inline workers start one by one, 1 s apart, market data and the
      trader first.

- **Exness DEMO trading for gold (XAUUSD) and EURUSD** (owner: "make the forex bot
  available for trading").
  * Orders go only on an Exness demo account, through a new Windows program
    (`apps/mt5_bridge/nexus_mt5_demo_trader.py`).
    * It refuses non-demo accounts before every order.
    * It never resends a client ID that is already in the order history.
  * The server endpoint `POST /api/v1/bridge/mt5/demo/sync`:
    * refuses non-demo accounts (403, audited);
    * opens new trades only when the market switch is PAPER, the kill switch is off and
      the demo account is within 1% daily, 3% weekly and the 5% drawdown halt;
    * exits are always allowed.
  * Strategies (`nexus_strategies.exness_demo`), both failed narrowly and shown as
    observation:
    * gold: the volatility rule in a 25% sleeve;
    * EURUSD: NQS-1 intraday.
  * Forex and commodities can now be switched to PAPER (Exness demo). LIVE remains
    impossible.
  * The owner can reset the demo halt (audited). There is an All markets card.

- **Exness gold and NASDAQ-100 with volatility control: FAILED narrowly** (all three
  portfolios). It uses the unchanged NASDAQ-100 rule on Exness's own bid/ask ticks.
  * Every portfolio had a higher Sharpe and smaller drops than holding, held at 2× costs
    and stayed positive with swaps.
  * Each missed one strict gate:
    * gold: drawdown only 8.8 points better, and the first-half Sharpe;
    * NASDAQ-100: the second-half Sharpe;
    * 50/50: drawdown only 8.7 points better.
  * 50/50: 15.2% a year, Sharpe 1.24, worst drop −18%.
  * There is a card on the Strategy test page.

- **Bot league: the NEXUS volatility-control bot ranks first** against 5 popular Binance
  bots: the Spot Grid at 3 settings, the Rebalancing bot, and buy-and-hold.
  * Setup: BTC + ETH, 10,000 USDT, 2021-02 to 2026-10, the same costs and data for all.
  * NEXUS: +126.9% (15.3% a year), Sharpe 0.55, worst drop −59%.
  * Rebalancing bot: +121.4%; hold: +97.5%; best grid: +34.2%.
  * Pre-registered as a comparison (the NEXUS bot's parameters come from an earlier
    test over overlapping years).
  * There is a card on the Strategy test page.

- **Binance Spot Grid bot (as people run it): all 6 settings FAILED.** It was
  pre-registered before any run.
  * Setup: 10 coins, Binance 5-minute data 2021–2026, new bot every 30 days, 0.1% fees.
  * Results:
    * best (±30%, no stop): +102% over 6 years, against +1,049% for holding the coins,
      with a lower Sharpe and Calmar and no significance;
    * narrow grids: −64% to −73%.
  * Both the pre-registered run and a corrected re-run are reported. The correction: an
    order already through the market fills at the bar's open.
  * A timestamp-unit bug (pandas 3) was caught before any result was seen.
  * Report: `docs/research/2026-11-27-binance-spot-grid-report.md`.

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
