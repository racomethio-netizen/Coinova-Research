# NEXUS audit (deep upgrade, Phase 0)

Date: 2026-11-22, 12:25 UTC. Method: code inspection of the whole repository, the
production database (Supabase), the production service (Render: deploys, events, logs,
metrics) and the test suite. Earlier audit: docs/AUDIT-2026-11-21.md (its critical items
are fixed). Every statement below was checked today unless marked *not verified*.

## 1. Architecture and stack

| Layer | What exists |
|---|---|
| Language | Python 3.13 backend (no TypeScript backend); Next.js 16 static dashboard |
| API | FastAPI: session cookies (HttpOnly, SameSite=Strict, Secure in production), Argon2 + TOTP, CSRF header + Origin check, owner-only mutations, login throttling (HTTP 429), owner-only WebSocket with Origin check |
| Database | Postgres on Supabase, 27 tables, Alembic 0009, RLS on every table with no policies (deny-all to the public API roles), append-only audit log and ledgers |
| Cache / bus | Redis (Render Key Value, free, not persistent): books, heartbeats, streams, back-off state, kill-switch flag (durable copy in the audit log) |
| Exchange | ccxt: Binance spot adapter (filters, precision, min qty/notional, error mapping, rate limiter); Binance USD-M adapter (isolated margin, leverage, reduce-only, protective orders); demo/testnet support |
| Workers | One Render web service (free, 512 MB, Frankfurt) running the API plus 8 supervised in-process workers: market data, trader, notifier, news, derivatives, X sentiment (off: no token), carry paper test, taker-flow paper test |
| ML | `nexus_ai`: XGBoost + logistic regression ensemble, isotonic calibration, purged time-ordered split, walk-forward validation with promotion gates, sha256-verified artifacts, drift module, owner-gated activation |
| Research | Event backtester, walk-forward (180/60), Monte Carlo, futures backtester (mark-price stops, funding, liquidation), pre-registered studies |
| Tests | 576 automated (unit, integration on Postgres + Redis, security, exchange fakes), Playwright e2e for every page at desktop and 360 px |

## 2. Existing trading functionality

* Paper trader on real Binance books: signals -> NO TRADE rules -> risk engine -> allocator
  -> sizing (fees and slippage included) -> marketable limit IOC; stop-loss, take-profit,
  partial take-profit, ATR trailing stop, loss-streak protection, cooldown after losses,
  regime and correlated-exposure gates, durable kill switch with cancel and close-all.
* Live path: `LiveBroker` exists behind six independent gates; never enabled.
* Analysis: decision score, six deterministic agents, independent Risk Manager, trade
  review; X sentiment module; news risk; derivatives context.
* Two forward paper tests running (funding carry, taker flow in range regimes).

## 3. Production state right now (database, 12:23 UTC)

| Item | Value |
|---|---|
| Latest closed candles | 5m 12:15, 15m 12:00, 1h 11:00, 4h 08:00, 1d 21 Nov: current |
| Order-book snapshots | latest 12:20: current |
| Strategies enabled | 0 of 9 (all failed out-of-sample validation) |
| Trading signals / orders / positions | 0 / 0 / 0, ever |
| AI models | 0 rows (none trained on production data) |
| Exchange keys | 1 production key (spot only, withdrawals disabled, verified 21 Nov); demo key revoked |
| Binance access | spot API banned until 23 Nov 06:38 UTC (account calls); futures public API backed off until ~08:54 UTC 23 Nov |

## 4. Answers to the audit questions

1. **Architecture and stack**: section 1.
2. **Existing trading functionality**: section 2.
3. **Broken functionality**
   * Binance access: repeated IP bans on the shared Render egress IP. NEXUS contributed
     (workers kept calling during bans; restart bursts). Shared back-off added today;
     the shared IP remains.
   * Free-plan hosting: sleeps without traffic (kept awake by a self-request), 512 MB,
     one process for everything.
   * X sentiment: built, never verified live (no token).
   * Demo/testnet order tests: built, never run (demo key revoked).
4. **Why real-market trading is "failing"**: it is not failing; it is not happening, by
   design. In order of importance: (a) no strategy has a positive out-of-sample edge
   (11 tested); (b) live trading is gated off; (c) the real account holds about 0.005
   USDT, below Binance's ~5 USDT minimum order; (d) the key has no futures permission;
   (e) Binance bans on the shared IP. Fixing (b) to (e) without (a) would only lose money.
5. **Is Binance data real and current?** Yes (section 3). Candles are closed bars only;
   gaps are detected at ingest.
6. **Do API connections work?** Public spot data: yes between bans. Futures public
   data: banned until ~08:54 UTC tomorrow. Signed account calls: key valid on 21 Nov,
   account calls banned until 06:38 UTC tomorrow.
7. **Are signals generated correctly?** No strategy is enabled, so the trader generates
   none, which is correct. Strategy signal code is unit-tested and causal (higher
   timeframes aligned on bar close).
8. **Can orders execute?** Paper: yes (tested end to end, including partial fills,
   timeouts, outages). Live: the code path is tested only against exchange fakes; it has
   never placed a real or demo order. *Not verified on a real exchange.*
9. **Is position state reconciled with Binance?** Orders: yes (unknown orders resolved by
   client order ID, never resent). **Balances: no.** In live mode the internal balance
   ledger is never compared with Binance balances; "balances reconciled" only means every
   position can be marked. **Production-critical before any live trading.**
10. **Duplicate order risk**: low. One active order per pair and side (database unique
    index), idempotent signals, client order IDs, no resubmission after timeouts.
11. **Risk management**: yes, deterministic and independent of AI (limits, sizing,
    streak, cooldown, correlation, regime, kill switch). Missing: an explicit, persisted
    risk-state machine (NORMAL / REDUCED_RISK / NO_NEW_TRADES / CLOSE_ONLY /
    EMERGENCY_STOP); today the equivalent behaviour is spread across the kill switch,
    bot state, loss-streak state and per-trade checks.
12. **Security**: no secrets in git or logs (tested); keys AES-GCM encrypted; Supabase
    security advisor: only the informational "RLS enabled, no policy" (intended
    deny-all). Findings: (a) the app connects to the database as the owner role
    (`postgres`), so the least-privilege `nexus_app` role and append-only grants are not
    in force in production (triggers still block UPDATE/DELETE on ledgers and the audit
    log); (b) CSP allows `'unsafe-inline'` scripts (static export); (c) Redis has no
    persistence (state that must survive is mirrored in Postgres); (d) backups depend on
    the Supabase plan (*not verified*).
13. **Database inconsistencies**: none found (`alembic check` clean; migrations and
    models match). Strategies table carries validation JSON per strategy; two
    append-only ledgers for forward tests.
14. **Missing features versus the upgrade brief**: timeframes 1m, 3m, 30m, 2h, 6h, 12h;
    live trade stream; indicators Supertrend, Ichimoku, Stochastic RSI, CCI, ROC, OBV,
    MFI, Keltner, RSI divergence, pivots, equal highs/lows; multi-timeframe agreement
    scores as features; LightGBM, CatBoost, HMM regimes, Isolation Forest; purged k-fold
    with embargo across all models; multi-target labels; scheduled retraining with
    registry rollback; calibrated signal fusion with LONG/SHORT/WAIT/NO TRADE; strategy
    selector by regime with sample-size rules; stop-limit and post-only orders; balance
    reconciliation; persisted risk states; four operating modes; testnet runs; the
    NEXUS_*.md document set.

## 5. Severity summary

| Severity | Finding |
|---|---|
| Critical (before any live trading) | No balance reconciliation with Binance in live mode |
| Critical (operations) | Shared-IP Binance bans; free single-process hosting |
| High | No persisted risk-state machine; live order path never exercised on testnet/demo; app uses the database owner role |
| High (research) | No strategy or model with an edge after costs |
| Medium | Missing timeframes, indicators, models and fusion listed in 4.14; X unverified |
| Low | CSP `unsafe-inline`; Redis non-persistent (mitigated) |
