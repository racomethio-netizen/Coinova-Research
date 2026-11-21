# NEXUS AI roadmap and status

_Last updated: 2026-11-14 (session 1: phases 1–15)._

Legend: ✅ done and covered by passing tests · 🟡 done, needs external verification ·
⛔ blocked on owner decision/credentials/infrastructure · ⬜ not started.

## Headline result (read this first)

The software platform is complete and tested. **No trading strategy and no AI model has
passed independent out-of-sample validation**, so the system correctly places **no
trades**. On 2 years of real Binance data (Nov 2024–Nov 2026), after realistic costs
(0.10% fee + 0.05% slippage per side):

| Candidate | OOS trades | Win rate | Expectancy / trade | Profit factor | Positive windows | Verdict |
|---|---|---|---|---|---|---|
| Trend following (1h/4h) | 763 | 32.5% | −0.53% | 0.54 | 0 / 9 | rejected |
| Momentum (15m/1h) | 196 | 26.0% | −0.35% | 0.46 | 0 / 9 | rejected |
| Breakout (15m/1h) | 113 | 27.4% | −0.43% | 0.40 | 1 / 9 | rejected |
| Mean reversion (15m/1h) | 114 | 42.1% | −0.31% | 0.52 | 3 / 9 | rejected |
| AI model (XGBoost + logit, triple barrier, 15m) | 90 | 11% | −0.32% | 0.12 | 0 / 7 | rejected (AUC 0.513, Brier skill < 0) |

Walk-forward: parameters chosen on 180 days, judged on the next 60 unseen days; AI used
270-day purged training windows. Tests prove the same gates **pass** on synthetic data
with a real signal, so the rejections reflect the market, not rigged gates.

An 80–90% win rate is easy to manufacture (tiny targets, wide stops) and is worthless
when expectancy after costs is negative; the system optimizes expectancy and refuses to
promote anything on in-sample metrics. Further research should change the *hypothesis*
(e.g. longer horizons, different instruments, maker-only execution), not re-tune until
something passes, which would be data snooping.

## Verified this session

| Check | Result |
|---|---|
| `scripts/test.sh` (ruff, mypy --strict on 13 packages, pytest) | ✅ 348 passed |
| Live read-only exchange tests (`pytest -m live_exchange`) | ✅ 23 passed (Binance via public mirror, Kraken, Coinbase; paper fills on real books) |
| Dashboard e2e (Playwright, desktop + 360 px) | ✅ 11 passed (+1 skipped by design) |
| `pip-audit` / `npm audit` | ✅ 0 known vulnerabilities |
| Production stack locally (Nginx/TLS, 11 services) | ✅ all healthy; restore drill matched 23/23 tables; ≈0.9 GB RAM |
| Continuous paper run on live data (25 min, prod stack) | ✅ 0 restarts, 0 errors; see docs/PAPER_EVALUATION.md |

## Phases

1. **Environment & foundation** ✅: config with hard risk ceilings, encryption, logging, adapter interface, Docker.
2. **Database** ✅: 22 tables + 3 migrations; paper/live isolation via composite FKs; append-only audit; one-active-order and one-position-per-asset indexes; least-privilege app role.
3. **Exchange adapters** 🟡: CCXT spot adapter; verified read-only live on all three. ⛔ Order placement untested on real exchanges (needs your API keys; Binance testnet keys recommended first). Coinbase has no 4h candles (aggregated from 1h). Kraken/Coinbase lack client-ID lookup (scan fallback).
4. **Market data** ✅: backfill, gap reporting, 6 live WebSocket books, health monitor. Finding: Coinbase BTC/USDT omits ~30% of 5m bars (illiquid USDT books).
5. **Strategies** ✅: 4 long-only MTF strategies, causal indicators (truncation tests), cost-aware filter. All fail validation (above).
6. **AI** ✅: features, triple-barrier labels, XGBoost+logit ensemble, isotonic calibration, purged walk-forward, drift (PSI), sha256-verified registry. Model rejected (above).
7. **Risk** ✅: Decimal sizing incl. costs, daily/weekly limits, 5% drawdown shutdown, fail-closed kill switch, allocator, reconciliation.
8. **Execution** ✅: intent-before-submit, exactly-once submission, unknown→reconcile by client ID, idempotent fills, DB duplicate guard, live gate.
9. **Backtesting** ✅: event-driven engine (next-open fills, stop-first, gap fills, costs), walk-forward, metrics, persisted runs.
10. **Paper trading** ✅: real-book fill simulation, virtual capital, reports titled PAPER.
11. **Dashboard** ✅: 14 pages + login, static export, auth, charts.
12. **Telegram** ✅: all event types, daily report, token never logged. ⛔ Needs your bot token/chat ID to deliver.
13. **Security** ✅: auth/2FA/CSRF/rate limits, DB roles, static checks, dependency audit (found and fixed real advisories).
14. **Deployment** 🟡: complete and verified locally. ⛔ Needs your VPS, domain and approval to deploy.
15. **Continuous paper trading** 🟡: 25-min continuous production-stack run on live data: 0 restarts, 0 errors, 6/6 streams, fresh candles; two defects found and fixed (Redis timeout, Nginx stale DNS). Zero trades because nothing passed validation (docs/PAPER_EVALUATION.md). Multi-week paper evaluation requires the VPS.

## Owner decisions / inputs needed ⛔

1. **VPS + domain** (and permission to deploy). Choose a region where your exchanges allow trading (Binance returned HTTP 451 here).
2. **Research direction**: accept that no current candidate trades, or fund new hypotheses (see headline). Do not lower the gates.
3. **API keys** (read + spot trade, no withdrawals, IP-restricted) only when a strategy passes validation and has a successful paper period.
4. **Telegram** bot token + chat ID (optional).
5. **Initial real capital**: still undecided; irrelevant until live approval.

## Next session: exact commands

```bash
cd nexus-ai && uv venv .venv -p 3.13 && . .venv/bin/activate && uv pip install -e ".[dev]"
export NEXUS_TEST_DATABASE_URL=postgresql+psycopg://nexus:<pw>@localhost:5432/nexus_test
export NEXUS_TEST_REDIS_URL=redis://localhost:6379/15
scripts/test.sh                                    # must be green
pytest -m live_exchange tests/exchange/live        # read-only, needs exchange access
scripts/security-audit.sh                          # re-audit dependencies
```
Then: deploy (docs/DEPLOYMENT.md) once a VPS exists; Binance testnet order round-trip;
new research hypotheses through `nexus_backtest.cli validate-strategies` / `nexus_ai.cli train`.

---

## Status against the 12-phase mandate v2 (updated 2026-11-17)

Legend: ✅ built, tested and wired into the live pipeline · 🟡 built and tested, not yet
wired into the trader · ⛔ blocked (external access or owner decision) · ⬜ not started.

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Architecture and structure | ✅ | Mandate v2 and plan in `docs/ARCHITECTURE.md`. |
| 2 | Market data | ✅ | 5m, 15m, 1h, 4h and 1d (1d verified on all three exchanges). Live books and WebSocket streams. |
| 3 | Technical and quantitative analysis | ✅ | Swings, BOS and CHoCH, support/resistance, supply/demand zones, liquidity sweeps, BTC correlation, ATR, volume. Used by the trader gate. Funding rate and open interest need futures (⬜). |
| 4 | AI decision engine | ✅ | Deterministic 0–100 score with explained earned and missed parts, wired into every entry. The XGBoost model remains rejected and is not used in decisions. |
| 5 | No-trade intelligence | ✅ | All gates in `nexus_decision.no_trade`, wired. 1:2 minimum enforced. Tests: each gate blocks alone. |
| 6 | Strategy engine | 🟡 | Six strategies (trend, momentum, breakout, mean reversion, pullback, liquidity-sweep reversal). The regime gate is built, but the trader does not yet use it. None of the six passes validation. |
| 7 | Risk management | 🟡 | Daily, weekly and drawdown limits, the 1% risk setting, correlated exposure (`nexus_risk.correlation`), trailing stops and partial take-profit (`nexus_risk.exits`), and consecutive-loss protection (`nexus_risk.streak`). The exit and streak modules are not yet called from live exit management or trade recording. |
| 8 | Backtesting | ✅ | Fees, slippage, walk-forward, out-of-sample, Monte Carlo (live sizing), Sharpe, Sortino, profit factor. Futures funding costs need futures (⬜). |
| 9 | Paper trading and news | ✅ | Paper fills against real books. News monitor (Federal Reserve, SEC, Kraken and Coinbase status), blocking entries on high-impact or unknown news. BLS and BEA feeds unavailable. |
| 10 | Exchange execution | 🟡 | Spot: the existing lifecycle with duplicate protection and reconciliation. Binance futures (1x, long and short): ⬜ not built. Live futures API blocked from this location (HTTP 451). The historical futures archive is reachable. |
| 11 | Dashboard and Telegram | ✅ | START, PAUSE and STOP with confirmations and audit. Kill switch. Telegram alerts. Daily and monthly P&L and regime view: ⬜. |
| 12 | Security, testing and deployment | 🟡 | Security and dependency audit clean (Next.js 16.4.5, npm and pip 0 vulnerabilities). Production stack verified locally. ⛔ Deployment needs a VPS in an eligible region and your approval. |

**Not done yet, in order of importance:**
1. Wire trailing stops, partial take-profit, consecutive-loss protection and the regime gate into the live trader.
2. Build the Binance futures adapter (1x, long and short). Verify live once the server is in an eligible region.
3. Daily and monthly P&L and the regime view on the dashboard.
4. Deploy (⛔ VPS and approval).

---

## Futures progress (2026-11-18)

**Built and tested (offline):**
- Futures settings: leverage tiers 2x, 5x, 10x, 20x (default 2x); futures risk per trade 1%; isolated margin.
- Futures sizing engine: size from risk, stop distance, fees and slippage; liquidation-distance refusal.
- Binance USD-M adapter (native endpoints): orders, stops, reduce-only closes, positions, funding, margin settings. Live read-only check returns HTTP 451 from this location.
- Futures backtester: isolated margin, long and short, funding, liquidation, mark-price stops, drawdown halt. Loader for public archives (no account needed).

**Baseline run, 2024-11 to 2026-10, real BTC and ETH USDT-M data (in-sample, not validated):**
trend-following, long only, costs 0.05% taker and 0.02% slippage per side.

| Symbol | Tier | Trades | Net return | Max drawdown | Halted |
|---|---|---|---|---|---|
| BTCUSDT | 2x | 52 | −5.5% | 10.3% | yes, 2025-07-03 |
| BTCUSDT | 5x and 10x | 32 | −3.1% | 10.5% | not recorded |
| BTCUSDT | 20x | 31 | −4.5% | not recorded | not recorded |
| ETHUSDT | 2x | 36 | −4.4% | 10.2% | not recorded |
| ETHUSDT | 5x and 10x | not recorded | −3.9% | not recorded | not recorded |
| ETHUSDT | 20x | not recorded | −3.8% | not recorded | not recorded (1 liquidation, 12 refusals) |

"Not recorded" means the value was not captured from the run output and has not been re-derived.
Re-running the baseline will fill these cells. Nothing in this table is a trading result.
This baseline is not a result to trade on. The strategy already fails spot validation.
The drawdown halt stops new entries at 10%. A live system would need an owner reset after a breach.

**Not yet done:** walk-forward validation of futures strategies, a short-side strategy,
trader integration of the futures path, futures paper trading, the futures dashboard, and
live verification (blocked by eligibility and the server).

---

## Session 2026-11-20: owner mandate v3, Binance connection, Render deployment

**Done and verified**
- Risk mandate: 0.5% default / 1% max risk per trade, 2% daily, 4% weekly (chosen so
  daily <= weekly <= drawdown), 10% drawdown, futures 2x default / 5x max, isolated.
- Binance connection page and API: testnet or production key, spot and/or futures,
  2FA to add, encrypted at rest, never returned; real signed read-only test; disconnect.
- Bug found against the real futures testnet: Binance retired GET /fapi/v1/account;
  the adapter now uses /fapi/v2/account and /fapi/v2/positionRisk.
- Deployed: https://nexus-ai-qioq.onrender.com (Render, Oregon, free plan; Supabase
  Postgres; Render Key Value). Supabase public API locked (RLS on, no anon grants).
- Tests: 452 passed (scripts/test.sh); e2e: every page renders on desktop and 360px.

**Verified limits of this environment**
- api.binance.com, fapi.binance.com, spot testnet and demo trading: HTTP 451 here.
- testnet.binancefuture.com: reachable; a signed call with an invalid key returns
  Binance -2014, so signing and routing work. A real testnet key is still needed.
- Render's Oregon region is in the US, which Binance.com does not serve.

**Not done yet (next)**
1. Owner: enable 2FA, add a Binance Futures testnet key on the connection page, test it.
2. With owner authorization: testnet order round-trip (entry, exchange stop, take-profit,
   reduce-only close) through the adapter.
3. DONE: Spot and Futures pages (live Binance account data via the stored key), Telegram
   settings page (status, alert list, real test message), trailing stop + partial
   take-profit + loss-streak protection in the trader (restart-safe; tests on real DB).
4. Futures path in the trader (paper first). BLOCKED for real data: Binance's futures
   API (fapi/dapi) returns HTTP 451 here and Render's region is in the US; only the
   testnet (not real prices) and the historical archive are reachable.
   DONE instead: futures walk-forward validation on real archive data (5m perpetual
   klines + real funding, 2024-11..2026-10, BTC+ETH, long-only): all six strategies
   FAIL out of sample (expectancy -0.04% to -0.31%/trade, PF 0.52-0.89). Recorded in
   backtest_runs and shown on the Backtesting page. Migration 0005 seeds the two newer
   strategies (they were missing from the strategies table).
5. Workers on Render (paid background workers) for market data and paper trading.
6. Production Binance: needs a server region and an account Binance serves. Not bypassed.

**2026-11-20 (later):** AI Intelligence page live: per-pair analysis from Binance public
spot data (works from Render: data-api.binance.vision reachable there), scored with the
trader's decide() rules for long and short, reasons for/against, NO TRADE reasons.
Funding/open interest unavailable (fapi HTTP 451). This also confirms spot paper
trading on Render can use live data once background workers are approved.

## 2026-11-21: single-server paper trading switch

* `NEXUS_RUN_WORKERS=true` makes the API process supervise the market-data and trader
  workers (child processes, restart with back-off, clean shutdown). Paper mode only:
  refused when `NEXUS_TRADING_MODE` is not paper. Tested (tests/unit/test_workers.py) and
  verified end to end locally (both workers start with the API and stop with it).
* Measured memory with all three running: about 870 MB. Render Starter (512 MB) is too
  small; Standard (2 GB) is needed. The free plan also sleeps when idle, which stops workers.
* Funding carry research: FAIL (see docs/research/2026-11-21-carry-preregistration.md).
* Owner chose a 5-day trial on the FREE instance (512 MB). Added
  NEXUS_WORKERS_MODE=inline (workers as tasks in the API event loop) and trimmed ccxt market
  metadata to the traded symbols (Binance lists ~3,500 markets, ~100 MB per client) plus
  malloc_trim. Measured API + both workers: 870 MB (process) -> 575 -> 469 -> 423 MB.
* NEXUS_KEEP_AWAKE_URL: optional self-request every 10 min while workers run, because the
  free plan sleeps idle web services (750 free hours/month; 5 days = 120 h).
* Render env for the trial (nexus-ai-fra): NEXUS_RUN_WORKERS=true, NEXUS_WORKERS_MODE=inline,
  MALLOC_ARENA_MAX=2, NEXUS_ENABLED_EXCHANGES=binance, NEXUS_KEEP_AWAKE_URL=<service URL>.
  To stop: set NEXUS_RUN_WORKERS=false.
