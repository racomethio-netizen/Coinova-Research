# NEXUS multi-asset platform

Goal: one trading system for crypto, stocks, commodities and forex, with **one switch per
market**. It is not four separate bots. The audit and the full stage plan are in
docs/MULTI_ASSET_AUDIT_2026-11-26.md.

## Stage status

| Stage | Status |
|---|---|
| 1. Market registry and per-market autonomy switches | **done** (2026-11-26) |
| 2. Instrument and session model, base-currency accounting | **done** (2026-11-26): see "Stage 2" below |
| 3. Research data and pre-registered backtests for stocks, commodities, forex | **done for what free data allows** (2026-11-26): see "Stage 3" below |
| 4. Interactive Brokers connector (stocks, ETFs, commodity futures), paper account first | not started; needs an IBKR account and a host for IB Gateway |
| 5. Forex connector: **Exness via MetaTrader 5** (owner's broker) | **read-only bridge built** (2026-11-26). Needs the owner to run it on Windows; the order path (demo only first) comes next |
| 6. Cross-market risk budget and consolidated dashboard | not started |
| 7. LIVE, one market at a time | owner authorization per market |

## The per-market switch (Stage 1)

Code: `packages/common/nexus_common/markets.py`.

| Setting | Meaning |
|---|---|
| OFF | No new signals or orders. Open positions keep their stops, targets and kill switch. |
| RESEARCH | Data and analysis. Signals are recorded with their risk verdict; no orders. |
| PAPER | Signals that pass every check become paper orders. |
| LIVE | Real orders. Needs that market's own phrase (`ENABLE LIVE CRYPTO`, `ENABLE LIVE STOCKS`, …), the owner's 2FA code and the market's live checks. A trader started in live mode is also required. |

**Rules.**
* **A market switch only narrows.** The effective mode is the more restrictive of the
  global operating mode and the market's switch. For example:
  * global PAPER_TRADING + crypto RESEARCH = signals only;
  * global PAPER_TRADING + crypto LIVE = still paper.
* **Durable and audited.** Each change is a `market_autonomy_set` row in the append-only
  audit log (actor, reason, previous value). Only the owner's rows count.
* **Defaults:**
  * Crypto: PAPER, today's behaviour.
  * Stocks: PAPER. That is the paper forward test of the one stock rule that passed; no
    broker orders exist.
  * Forex and commodities: RESEARCH (analysis only).
* **Fail closed.** A stored value that can't be read, or one the market can't support,
  means OFF.
* **Only supported modes can be chosen.**
  * Stocks: OFF, RESEARCH or PAPER.
  * Forex and commodities: OFF or RESEARCH.
  * None of the three can be LIVE until a broker connector exists. The API explains what
    is missing.
* **Where it is enforced for crypto:**
  * the main trader (paper and live), which combines the switch with the operating mode
    each cycle;
  * the spot engine and the NEXUS bot (same runner), which record the signal with the
    reason "crypto market switch is …" and place no order.

  Paper research ledgers (forward tests, grids, rebalancing, volatility target) place no
  broker orders and are not affected.

### API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/markets` | Every market: switch, allowed settings, effective mode, venues with connector status, what the owner must provide, LIVE blockers, crypto paper positions |
| POST | `/api/v1/markets/{market}/autonomy` | Body `{"autonomy": "RESEARCH", "reason": "…"}`. LIVE also needs `confirm` (the phrase) and `totp`. Owner only, with the CSRF header. |

Dashboard: **Overview → All markets** (`/multi-asset/`).

### Configuration

Stage 1 adds no environment variables. The switches live in the database, so they survive
restarts and deploys and need no redeploy to change. Example:

```bash
# Watch crypto signals without paper orders for a while (owner session cookie + CSRF header)
curl -X POST https://<host>/api/v1/markets/crypto/autonomy \
  -H 'Content-Type: application/json' -H 'x-nexus-csrf: 1' -b cookies.txt \
  -d '{"autonomy": "RESEARCH", "reason": "observe before resuming paper"}'
```

## Stage 2: instruments, sessions, base currency

* **Sessions** (`nexus_common.sessions`):
  * `24x7`: crypto and Binance perpetuals.
  * `fx_24x5`: Sunday 17:00 to Friday 17:00 New York time, with daylight saving.
  * `XNYS`: NYSE hours, holidays and early closes.
  * `CMES`: CME Globex.

  Exchange calendars come from `exchange_calendars==4.13.2`. With pandas 3.1 it emits
  pandas deprecation warnings; recheck when pandas is upgraded.
  `is_open(kind, ts)` and `next_open(kind, ts)` refuse times without a time zone.
* **Instruments** (`nexus_common.instruments`):
  * Each has its market, type (spot, perpetual, stock, ETF, future, FX spot, CFD,
    index), venue, quote currency, session and contract multiplier.
  * Broker contract details are read at connection time, never typed in.
  * The research instruments are registered here; `SPXUSDT` (a memecoin) is excluded.
* **Base currency.** `to_base(amount, currency, rates)` converts with an explicit rate. A
  missing rate raises `MissingRate`, and USDT is never silently treated as USD.
* **Migration 0021.** `trading_pairs` gains:
  * `asset_class` (crypto, stocks, commodities, forex);
  * `instrument_type`;
  * `contract_multiplier` (> 0).

  These are CHECK-constrained, and existing rows default to crypto, spot, ×1. `alembic
  check` is clean, and the migration was downgraded and re-applied in testing.
* **Research view.** Each instrument now shows whether its market is open, or when it
  reopens.

## Stage 3: research data, tests and the research worker

* **Data sources.** Free, official, no account needed:
  * FRED: daily NASDAQ-100, the 7 major currencies against USD, and 3-month interest
    rates;
  * Binance USD-M stock, ETF and commodity perpetuals: QQQ, SPY, NVDA, AAPL, MSFT, TSLA,
    gold, silver, platinum, palladium, crude oil, natural gas, copper.
  * Excluded: `SPXUSDT`, a memecoin, not the S&P 500.
  * Stooq, Yahoo and Dukascopy refused this environment, and Alpha Vantage needs a key.
* **Pre-registered tests (one run each):**
  * Trend following on the forex majors and the NASDAQ-100, 2004–2026: **failed**
    (Sharpe 0.04 and 0.06). See
    docs/research/2026-11-26-multi-asset-trend-preregistration.md.
  * Volatility-managed NASDAQ-100, 1990–2026: **passed**. Sharpe 0.79 vs 0.63 held, worst
    drop −44% vs −83%, but 11.5% vs 14.1% a year. See
    docs/research/2026-11-26-ndx-voltarget-preregistration.md.
  * Commodities: not testable yet. No free tradable history is long enough (Binance's
    commodity perpetuals start late 2025 / 2026).
* **Worker `multiasset-research`** (every 6 hours), each part obeying its market's
  switch:
  * stocks: a NASDAQ-100 and stock-perpetual research view; under PAPER, the paper
    forward test of the volatility-managed NASDAQ-100 from 2026-11-27 (10,000 virtual
    USD, 180-day review);
  * forex: majors with their carry;
  * commodities: Binance commodity perpetuals.

  It shows descriptive numbers only (returns, 200-day trend, volatility). A failed
  download is shown as "not available", never estimated.

## Exness (MetaTrader 5): read-only bridge

The owner trades forex at Exness. Exness is reached through MetaTrader 5, whose Python
interface runs only on Windows next to the terminal.

* **Bridge** (`apps/mt5_bridge`, setup in its README.md). A small program on the owner's
  Windows PC or VPS.
  * It attaches to the logged-in MT5 terminal and, every 5 minutes, sends to NEXUS over
    HTTPS: contract details, closed daily bars, quotes, the account summary and open
    positions.
  * Its source contains no order or position-closing calls, and a unit test checks this.
  * It never asks for the MT5 password.
  * It only ever sends: NEXUS never connects to the owner's machine.
* **Server time.**
  * The bridge measures the broker's server-time offset from the freshest tick.
  * Bars are sent only when that offset is known (or set by
    `NEXUS_SERVER_UTC_OFFSET`).
  * NEXUS stores them in UTC and drops the forming bar.
* **NEXUS side** (`apps/api/nexus_api/bridge.py`):
  * `POST /api/v1/bridge/mt5/snapshot` with `Authorization: Bearer <token>`.
  * Rate-limited, with a 4 MB body limit.
  * Every field is validated; symbol names are checked with a strict pattern.
  * Contract details go to `trading_pairs` (exchange `exness`, classified as forex,
    commodities or stock/index CFDs, with the contract size as the multiplier).
  * Closed daily bars go to `market_candles` (source `mt5`). The account summary goes to
    Redis with the account number masked (`***321`).
  * It asks the bridge for full history until 300 daily bars are stored.
  * Snapshots that are not read-only are refused.
* **Tokens.**
  * Created by the owner on All markets ("Create bridge token"), shown once, stored as a
    SHA-256 hash (table `broker_bridges`, migration 0022), and revocable.
  * Creating and revoking are recorded in the audit log.
* **Dashboard.** All markets shows:
  * the bridge's status (connected, account type, last seen);
  * the masked account summary;
  * the owner's own open positions;
  * symbols with bid, ask, spread, contract size, minimum lot and swaps.

  The research view includes Exness's daily prices per market.
* **Safety.** No order path to Exness exists. Exness's high leverage is never used: NEXUS
  keeps notional at or below 1× equity. A real-money account is shown as such and only
  read.
* **Next** (after the owner runs the bridge on a demo account):
  * pre-registered tests on Exness's own prices, spreads and swaps (gold, oil, indices,
    forex);
  * then a demo-only order path, with the same rules as Binance (intent first, one
    submission, reconcile by a client ID in the order comment).

## Deployment notes for the coming connectors

* **Interactive Brokers (Stage 4).**
  * IB Gateway (Java, about 1 GB RAM) must run where NEXUS can reach it. It needs the
    owner's login and periodic two-factor confirmation (to verify at connection time).
  * Plan: a small separate host or private service. **Not Railway.**
  * Start with an IBKR paper account.
* **MetaTrader 5 (Stage 5).**
  * The official `MetaTrader5` Python package works only on Windows with the MT5 terminal
    running, so it needs a Windows host acting as a bridge.
  * A Linux-native broker API (for example OANDA v20) is the alternative.
* **Credentials.**
  * Broker keys are stored encrypted like exchange keys.
  * No withdrawal or transfer rights.
  * Read-only first.
* **Region.** Brokers accept clients by country. NEXUS never bypasses a regional
  restriction.
