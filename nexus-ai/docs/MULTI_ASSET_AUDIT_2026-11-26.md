# NEXUS multi-asset audit (stocks, commodities, forex, crypto), 2026-11-26

This audit answers the owner's "Universal multi-asset autonomous trading platform" brief.
Everything below was checked in the repository, the production database, or the Render
logs. Facts about brokers that NEXUS has not connected to yet are marked **to verify at
connection time**.

## 1. Existing architecture

**Stack.**
* Python 3.13 with FastAPI (`apps/api`, 83 routes) and a Next.js dashboard
  (`apps/dashboard`, 39 pages).
* PostgreSQL on Supabase: 28 tables, 20 Alembic migrations.
* Redis: Render Key Value, free tier, 25 MB.
* One Render service in Frankfurt runs the API and 17 background workers on its own
  thread.
* About 33,500 lines of Python in 13 service packages, 4 shared packages and research
  code. 1,021 automated tests plus 16 browser tests.

**Scope today: crypto only.**

| Area | What exists | Live status |
|---|---|---|
| Brokers / exchanges | Binance spot (CCXT), Binance USD-M futures (native transport, 1x isolated), Kraken spot; live capability registry for OKX, Bybit, Kraken | No live order has ever been placed. The production key's last test (22 Nov) showed spot trading permission off and 0 USDT. Futures are paper-only. |
| Market data | REST backfill and WebSocket streams, 1m–1d candles, order books, spread, taker flow, funding, open interest, gap repair, data-quality report, dynamic universe scan | running |
| Intelligence | Technical analysis, market structure, levels, liquidity sweeps, multi-timeframe scores, regime engines, correlation, free news feeds, X sentiment (needs a token), classical ML (XGBoost, LightGBM, CatBoost, logistic) with purged CV and calibration, signal fusion, strategy selector, agents with a Risk Manager verdict | running in paper |
| Strategies | About 45 rule sets (trend, mean reversion, breakout, momentum, grid, adaptive grid, pairs, carry, rebalancing, volatility targeting, market making, market-neutral ML) | Only 3 passed their pre-registered tests: Crypto Trends (daily), rebalancing R3, and volatility-targeted BTC/ETH 50/50. All the others failed and are recorded. |
| Risk | Sizing, per-trade, daily, weekly and drawdown limits (0.25% / 1% / 3% / 5% defaults, hard ceilings in code), persisted risk-state machine, durable kill switch, correlation and exposure limits, protections, streak protection, grid risk caps, no martingale and no averaging down | enforced |
| Execution | Order state machine with intent persisted first, exactly one submit, reconcile by client order ID on timeout, duplicate-order prevention, pre-trade checks, paper broker filling on real order books, live gate (phrase + 2FA + gates) | paper |
| Backtesting | Event-driven and vectorised backtesters, walk-forward, stationary bootstrap, Holm correction, pre-registration discipline, cost and slippage models | used for every result |
| Paper trading | Main bot, spot engine, grid bot, adaptive grid, rebalancing, volatility target, funding carry, taker flow, strategy library, trends | running |
| Monitoring | Health and readiness endpoints, structured JSON logs with secret redaction, Telegram alerts, heartbeats, Binance weight gate | running |
| Modes | Global operating mode: ANALYSIS_ONLY, SIGNALS_ONLY, PAPER_TRADING, LIVE_AUTONOMOUS (durable in the append-only audit log) | PAPER_TRADING |

**How the brief's folder layout maps onto the existing packages.** The brief asks not to
rewrite the project unnecessarily, so nothing is moved.

| Brief | Existing package |
|---|---|
| `/core` | `packages/common`, `packages/configuration`, `packages/database`, `packages/security` |
| `/intelligence` | `services/analysis`, `services/ai-engine`, `services/news`, `services/social`, `services/decision`, `services/agents` |
| `/market_data` | `services/market-data` |
| `/markets/crypto` | `services/exchange-gateway` |
| `/markets/stocks`, `/markets/commodities`, `/markets/forex` | **missing**. Stage 1 adds the shared market registry (`nexus_common.markets`); the connectors come in later stages. |
| `/strategies` | `services/strategies` |
| `/machine_learning` | `services/ai-engine`, `research/training` |
| `/portfolio` | `services/risk-engine` (allocator, correlation), `services/execution-engine` (accounting) |
| `/risk` | `services/risk-engine` |
| `/execution` | `services/execution-engine` |
| `/backtesting` | `research/backtesting` |
| `/paper_trading` | `services/execution-engine` (paper broker), the paper workers |
| `/monitoring` | `apps/api` (health, logs), `services/notification-engine` |
| `/dashboard` | `apps/dashboard` |
| `/tests` | `tests/` (unit, integration, exchange, security) |

## 2. Missing components

* **Broker connectors.** None exist for stocks, commodities or forex.
  * There is no Interactive Brokers (IBKR) or MetaTrader 5 code.
  * No broker account is connected, so none can be claimed.
* **Instrument model.** `trading_pairs` holds crypto pairs only (base/quote, tick, step).
  Missing:
  * asset class;
  * instrument type (stock, ETF, future, FX spot, CFD);
  * contract multiplier and quote currency;
  * futures expiry and roll;
  * stock corporate actions;
  * FX pip and lot conventions.
* **Trading sessions and calendars.** Crypto trades 24/7, and NEXUS's stale-data checks
  assume continuous bars. Stocks, futures and forex close at night, at weekends and on
  holidays, and they gap. An exchange calendar per market is needed.
* **Multi-currency accounting.** Every NEXUS balance is in USDT. Stocks, futures and FX
  settle in USD, EUR and others; P&L must be converted to one base currency.
* **Margin and leverage.** NEXUS caps futures at 1x. FX and futures are normally traded on
  margin, so a margin model and the 1x ceiling must apply per market.
* **Market-specific rules:**
  * the US pattern-day-trader rule (margin accounts under $25,000; check the current FINRA
    rule);
  * T+1 settlement;
  * short-sale borrow availability;
  * trading halts;
  * futures daily price limits;
  * FX rollover and swap.
* **Data for research.** NEXUS has no historical or real-time data for stocks,
  commodities or forex.
  * Real-time stock data from IBKR needs paid market-data subscriptions.
  * Free daily history exists from public sources and needs a licence check.
* **Fundamental data** (earnings, balance sheets) for stocks: none.
* **Per-market autonomy switches:** none. There is one global operating mode.
  **Stage 1 adds them.**
* **Cross-market risk budget, and consolidated equity in a base currency:** none.

## 3. Integration plan (small stages, each tested, each deployable)

| Stage | Delivers | Needs from the owner |
|---|---|---|
| **1 (this session)** | Shared market registry for crypto, stocks, commodities and forex. Per-market autonomy switch OFF / RESEARCH / PAPER / LIVE: durable, audited, with LIVE needing that market's own manual authorization. Wired into the crypto trader; with the default (PAPER) its behaviour is unchanged. Honest connector status per market. API and a "Markets" dashboard page. Tests. | nothing |
| 2 | Instrument and session model: migration for asset class, instrument type, multiplier, currency, expiry; exchange calendars; base-currency accounting. Research-only. | nothing |
| 3 | Research data and pre-registered backtests for non-crypto markets: daily data for liquid ETFs, continuous micro-futures and FX majors, with costs per market. RESEARCH mode for those markets. | approve the data source |
| 4 | IBKR connector (stocks, ETFs, futures for commodities): read-only first, then paper orders on an IBKR **paper** account. Runs through IB Gateway. | an IBKR account (a paper account is enough to start); a host for IB Gateway |
| 5 | Forex connector. The MT5 Python package works only on Windows with the MT5 terminal running, so either a Windows VPS bridge or a Linux-native broker API (for example OANDA v20). | choose broker and host |
| 6 | Cross-market risk budget, correlation across asset classes, consolidated equity, allocation and per-market performance | nothing |
| 7 | LIVE for one market at a time, only after paper validation | explicit authorization per market (phrase + 2FA), a broker key without withdrawal rights |

**Rules for every stage:**
* nothing working is removed;
* the crypto paper trading keeps running;
* no broker is shown as connected until NEXUS has actually connected to it.

## 4. Dependencies and infrastructure

| Item | Today | For multi-asset |
|---|---|---|
| Hosting | One Render web service, Frankfurt, 2 GB | IB Gateway is a Java process (about 1 GB). It needs its own small host or a Render private service, plus periodic login with IBKR two-factor (to verify at connection time). MT5 needs Windows. **No Railway** (as instructed). |
| Database | Supabase, 15-connection session pooler | Enough for Stages 1–3. Watch connections when a broker worker is added. |
| Redis | 25 MB free | Tight already (an out-of-memory incident on 26 Nov was fixed by not caching large bodies). Broker data must not be cached there in bulk. |
| Python libraries | FastAPI, SQLAlchemy, CCXT, pandas, scikit-learn, XGBoost | New, each pinned and at least 2 weeks old: `ib_async` (IBKR, Stage 4), `exchange_calendars` (Stage 2), `MetaTrader5` (Windows only, Stage 5, if chosen) |
| Costs | Free tiers | Possible costs: IBKR real-time data subscriptions, a Windows VPS for MT5. **None are needed for Stages 1–3.** |
| Region | Binance refuses this build container (HTTP 451); production in Frankfurt works | Broker availability depends on the owner's country of residence. NEXUS never bypasses a regional restriction. |

## 5. Risk assessment

**Financial.**
* **No evidence yet of a trading edge in any market.**
  * Of about 45 crypto rule sets, only 3 slow "holding" strategies passed.
  * Every fast-trading method failed after costs: grid, market making, momentum, ML
    stat-arb.
  * Nothing suggests stocks, commodities or forex are easier. Most retail short-term
    traders in those markets lose money, and the brokers' own disclosures say so for
    leveraged CFDs and FX.
* **What multi-asset can honestly offer:** diversification for **investing** strategies
  (volatility-targeted allocation across stocks, bonds, gold and crypto), and more
  candidate hypotheses to test. It does not offer a promise of profit.
* **Small-account limits.**
  * One micro gold futures contract (10 troy ounces) is worth tens of thousands of
    dollars.
  * With 0.25% risk per trade, a small account cannot size most futures positions.
    NEXUS must refuse them, not round up.
  * Fractional shares (to verify at connection time) and small FX lot sizes help.

**Leverage.**
* FX and futures are leveraged by default.
* The 1x ceiling stays in code for every market. Notional exposure must never exceed
  equity unless the owner changes the mandate.

**Technical.**
* Broker session drops and the gateway's re-login requirement.
* Market closures and gaps: stops can fill far past their price at the open.
* Futures expiry and roll.
* FX quote conventions (pips, JPY pairs).
* Time zones and holidays.
* Multi-currency P&L mistakes.
* Illiquid stocks and partial fills.
* Short-sale restrictions.

  Each needs tests before it carries money.

**Operational and security.**
* More credentials to protect. Every broker key must be read-only or trading-only, never
  with withdrawal or transfer rights.
* The kill switch must cover every market at once.

**Regulatory.**
* The pattern-day-trader rule, leverage caps for retail clients (for example ESMA's
  limits in the EU), and tax reporting per country.
* NEXUS does not give tax advice.

**Controls carried over unchanged:**
* paper first;
* live never enabled by code;
* LIVE requires the owner's explicit per-market authorization;
* NO TRADE is always a valid decision;
* no martingale, no averaging down, no unlimited grid exposure;
* every number shown is measured, never invented.
