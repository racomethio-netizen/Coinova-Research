# NEXUS audit for the multi-exchange brief (2026-11-23)

Everything below was checked in the code, the production database, the Render logs, or by
live public API calls. Nothing is assumed.

## 0. The real stack (it differs from the brief's description)

| Brief says | Actual NEXUS |
|---|---|
| Node.js / TypeScript backend | **Python 3.13 FastAPI** backend (`apps/api`), 13 service packages, about 171 typed modules. TypeScript is used only for the Next.js dashboard. |
| CCXT | Yes: CCXT 4.5.91 (Python), plus a native Binance futures transport |
| Supabase PostgreSQL, Redis, WebSockets, Render workers | Yes: one Render service (Frankfurt, 2 GB) runs 14 inline workers; Supabase session pooler (15 connections) |
| Telegram | Yes (notifier worker) |
| Claude API | **Not used by NEXUS.** Its "AI" is classical ML (`nexus_ai`). The Claude API belongs to the unrelated app at the repository root. |
| Sentry | **Not installed.** Errors go to structured JSON logs (secrets redacted) and Telegram alerts. |

## 1–9. Findings

1. **Broken components.**
   * None found failing now.
   * The last 24 h of production errors come from two already-fixed causes:
     * Binance rate-limit bans on the shared IP (fixed by the central weight gate);
     * exhaustion of the 15-connection database pool (fixed with small pools).
   * One "cached plan" error happened once during a migration deploy.
   * No error logs since the 10:27 UTC deploy on 23 Nov.
2. **Missing functionality.**
   * OKX and Bybit have no trading adapters.
   * NEXUS has no Kraken futures adapter.
   * Multi-exchange routing is not built.
   * Futures run in paper only, Binance only.
   * There is no user-data WebSocket: order state comes from REST polling with
     reconciliation.
   * Stops are not resting orders on the exchange.
   * Sentry is not installed.
3. **Exchange integration.**
   * The Binance spot adapter works.
   * The production Binance key's last test (22 Nov) showed **spot trading permission OFF
     and 0 USDT**. The owner says both are now changed; NEXUS has not re-tested the key yet.
   * Futures data are paused by a Binance IP ban until 24–26 Nov.
4. **Execution.**
   * Never exercised live: 0 orders ever. All 18 validated-or-failed strategies are
     disabled.
   * The bot now runs in paper mode (since 14:25 UTC on 23 Nov).
5. **Strategy calculations.**
   * Prefix-invariance (no look-ahead) tests cover every strategy.
   * One live-versus-backtest warm-up bug was found and fixed earlier today (spot-engine
     4h EMA200).
6. **Database sync.**
   * Daily history was 121 bars and is now 1,499.
   * Gap repair works.
   * Balance reconciliation exists for live mode.
7. **Performance.** The Supabase session pool (15) and the shared egress IP are the real
   limits, not CPU.
8. **Security.**
   * Encrypted keys, 2FA for sensitive actions, audit log, no withdrawal permission, CSRF
     and rate limits.
   * Weak point: the shared IP.
   * Sentry would add error tracking without exposing secrets, if it is configured with
     scrubbing.
9. **Deployment.** One service runs everything. The brief's split into 8 services is not
   justified by measured load and would multiply Supabase connections past the 15 limit.

## Status of the brief's 24 parts

| Part | Status | Notes |
|---|---|---|
| 1 Preservation / audit | ✅ this document | nothing removed |
| 2 Multi-exchange infrastructure | 🟡 | **New today: live capability registry** for Binance, OKX, Bybit and Kraken. Trading adapters: Binance (spot live-gated, futures paper) and Kraken spot. OKX and Bybit are not yet built. |
| 3 Market intelligence | ✅ mostly | 1m (30 days) to 1d candles, books, spread, taker flow, funding, OI, BTC correlation; volume profile and liquidations are limited by data availability |
| 4 Strategy library | ✅ built, tested | about 45 rule sets; only 2 passed out-of-sample (daily Crypto Trends, daily ATR expansion) |
| 5 ML | ✅ built | XGBoost / LightGBM / CatBoost / logistic, purged CV, calibration, registry, rollback; no model passed |
| 6 Regime engine | ✅ | NO TRADE is the default when evidence is missing |
| 7 Signal fusion | ✅ | the weights must be validated; nothing validated passes yet |
| 8 Portfolio | 🟡 | correlation, exposure and concentration exist; there is no cross-exchange shared budget yet because only one exchange trades |
| 9 Capital protection | ✅ updated today | the owner's Part-9 defaults (below) |
| 10 Spot engine | ✅ | paper running; live behind gates |
| 11 Futures engine | 🟡 | Binance USD-M paper only; disabled for live |
| 12 Execution optimizer | 🟡 | IOC, post-only, stop-limit and reconciliation exist; no automatic order-type selector |
| 13 Routing | ⬜ | needs a second trading adapter first |
| 14 Backtesting | ✅ | |
| 15 Paper trading | ✅ running | bot, spot engine, library, crypto trends |
| 16 Autonomy | ✅ in paper | live requires the owner's activation |
| 17 Learning | 🟡 | degradation detection by forward results; no automatic recommendations |
| 18 Dashboard | ✅ 40 pages | START / PAUSE / STOP exist as operating modes and the kill switch |
| 19 Claude role | n/a | Claude is not in the execution path; nothing to restrict |
| 20 Security | ✅ | Sentry missing |
| 21 Deployment split | ⬜ deliberately | see finding 9 |
| 22–24 Roadmap, gates, delivery | this document and ROADMAP.md | |

## Live detection results (from this audit container, 2026-11-23)

| Exchange | Result |
|---|---|
| OKX | reachable: 498 spot USDT markets, 531 USDT perpetuals, sandbox; stop / take-profit / reduce-only / leverage / isolated margin / WebSocket orders all supported; BTC perp contract 0.01, maker 0.02% / taker 0.05% |
| Kraken | reachable: 44 spot USDT markets, **0 USDT perpetuals** (Kraken derivatives are a separate venue); no sandbox; spot fees 0.16% / 0.26% |
| Bybit | **HTTP 403 (CloudFront geo-block)** from this location |
| Binance | **HTTP 451 "restricted location"** from this location (production in Frankfurt uses Binance normally) |

Production runs from Frankfurt, so its results can differ. Use "Detect now" on the
Exchanges page. Blocks are reported, never bypassed.

## Risk configuration applied (owner, Part 9)

* **Global defaults:** 0.25% risk per trade, 1% daily, 2% weekly, 5% drawdown, 3 positions,
  30% maximum spot exposure.
* **Futures:** disabled, 0.25% per trade, default leverage **1x** (1–5x selectable, never
  raised automatically), isolated margin only.
* The bot keeps its own stricter profile (0.25% / 1% daily / 3% drawdown / 2 positions).

## Next phases, in order (each needs the owner's go-ahead)

1. **Owner:** run the Binance key test. The live gate now needs a fresh test showing spot
   permission.
2. **Exchange-side protective stops** for live spot positions: a resting STOP_LOSS_LIMIT on
   Binance, reconciled, and cancelled before other exits.
3. **OKX adapter (spot first, then USDT perps at 1x isolated)**, tested on OKX's demo
   trading first. OKX is the most complete exchange in the registry.
4. A cross-exchange risk budget, then routing (fees, spread, depth, balance per venue).
5. Sentry with secret scrubbing (needs a DSN from the owner).
