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

* **2026-11-23 late: live switch.**
  * Live mode now runs the live workers (it started none before).
  * The 2FA approval button now sends a single request per tap.
  * Tests: backend suite green; e2e 15/15.
  * The owner's go-live steps are in docs/TRADING_BOT.md: key test, strategy approval,
    seed the live ledger, Render env vars, 2FA approval.

* **2026-11-23 night: quant engine brief.**
  * Audit and map: `docs/INTEGRATION_MAP_2026-11-23.md`.
  * New modules: stat_arb (research only; matches statsmodels to 1e-5), liquidity
    planner (in the testnet round trip), market_conditions, rf family, KuCoin / Gate.io
    detection.
  * Tests: 931 passed, including integration.
  * Next, in order:
    1. Deploy (owner says "Deploy").
    2. Testnet round trip with the owner's testnet key.
    3. Wire `liquidity.plan` into the paper trader.
    4. Pre-registered stat-arb test on futures data.
    5. OKX adapter.

* **2026-11-23 evening: dashboard sluggishness.**
  * Measured on Render: CPU 1%, memory 320 MB of 2 GB; `/system` 1.5–7.7 s;
    `/overview/context` up to 8.8 s.
  * Cause: blocking database calls from the inline workers on the API event loop. The
    database is in us-west-2 and the server in Frankfurt, about 150 ms per round trip.
  * Fix: workers run on their own thread (`InlineWorkers(threaded=True)`), with
    per-loop guard Redis.
  * Tests: 913 passed, including integration.
  * Moving the server next to the database is not an option: a US server IP is blocked
    by Binance (HTTP 451).

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
* First deploy was OOM-killed 4x on Render (512 MiB) ~10 s after the workers started: both
  workers parsed Binance's full exchangeInfo concurrently on top of a higher baseline than
  measured locally. Fix: Binance exchangeInfo is now requested for the traded symbols only
  (`symbols=[...]`), also for request-time clients (spot account view, spot probe, spot
  order test) via `load_binance_spot_markets`. Local peak with an empty DB and an active
  trader: 435 MB -> 244 MB.

## 2026-11-21: Binance IP ban (-1003) on Render Frankfurt

* The Spot page showed Binance -1003 "IP(74.220.51.139) banned until 2026-11-23 06:38 UTC".
  That is Render's shared Frankfurt egress IP; it was reported at 10:15 UTC, before the
  paper workers were switched on, and NEXUS logged no rate-limit errors of its own.
* `nexus_api.binance_ban`: a reported ban is stored in Redis per environment; the account
  views, the connection test and the order test make no Binance calls until it ends
  (calls during a ban extend it). Account view cache 15 s -> 60 s; open orders fetched per
  symbol (weight 6 each) instead of all symbols (weight 80).
* Shared egress IPs mean other Render customers' traffic counts toward the same Binance
  limits; a paid instance does not change the egress IP. A dedicated IP (e.g. a VPS) is the
  durable fix if bans recur. The production key's IP whitelist must match the egress IP.
* Production `strategies.parameters` had no validation, so the Strategies page said "Not
  validated yet". Re-ran `validate-strategies` locally on 2 years of Binance candles
  (2024-11-14 to 2026-11-14, 9 walk-forward windows): all six FAIL (OOS expectancy -0.30%
  to -0.53% per trade, profit factor 0.31-0.57). Copied those results into production
  (is_enabled stays false). A 120-day run gives 0 windows and was not used. The page now
  says why Enable is locked.
* Telegram configured on nexus-ai-fra by the owner (token and chat ID set in Render, never
  in the repo or chat). Notifier runs as the third inline worker; owner confirmed the test
  message arrived (2026-11-21).

## 2026-11-21: X (Twitter) integration, read-only (docs/X_INTEGRATION.md)

* Claude Code: `.mcp.json` -> X's hosted MCP `https://api.x.com/mcp?read_only=true`,
  token from `$X_BEARER_TOKEN` (parsed by `claude mcp get x-readonly`).
* NEXUS: `services/social/nexus_social` (read-only GET-allowlist client with daily
  budget, rate-limit pause and caching; lexicon sentiment -100..100 with confidence;
  credibility, bot, spam and duplicate filtering; news events with confirmed/unconfirmed
  and fake-news risk; trends; combined market view), `/x/status`, `/x/sentiment`,
  dashboard page "X sentiment", Telegram `x_alert`. Trader: block-only `_x_block`.
* Also: the news monitor now runs as a supervised worker (it was not running on Render,
  so the trader rejected every entry as "news risk unknown"); intelligence shows real
  funding rate and open interest from fapi.binance.com (reason shown if unreachable).
* Tested: 510 tests (mocked X API). NOT yet verified live: no X token exists yet.
  Next: owner creates an X app + credits, sets NEXUS_X_BEARER_TOKEN on Render and saves
  the token as a session credential for api.x.com; then verify (1) GET
  /2/tweets/search/recent returns posts, (2) MCP initialize + tools/list on
  ?read_only=true lists no write tools, (3) first refresh writes nexus:x:snapshot.

## 2026-11-21: audit and Phase 1 (safety foundations)

* Audit: docs/AUDIT-2026-11-21.md (findings C1-C2, H1-H4, M1-M5, plan in 8 phases).
* C1 fixed: kill switch is durable. Every engage (owner and the drawdown shutdown) writes
  `kill_switch_engaged` to the append-only audit log; the trader restores the Redis flag
  from it every cycle and blocks entries if the record cannot be read; the API shows the
  durable state if Redis lost it. Clearing still needs the owner + 2FA.
* C2 fixed: while engaged the trader cancels resting entry orders each cycle;
  GET /risk/kill-switch/state lists positions and active orders per mode;
  POST /risk/kill-switch/close-positions (engaged only; typed phrase; live also 2FA) makes
  the trader sell every position with bounded-slippage IOC orders. Risk page UI added.
* M5 applied (owner mandate): defaults 0.25% risk, 1% daily, 3% weekly, 3 positions;
  ceiling for open positions 2 -> 3; other ceilings unchanged; spot is 1x; futures stay
  disabled (their 2-5x options are unchanged until futures are reconsidered).
* Tests: 514 pass (new: restore after Redis loss, unreadable record blocks entries,
  close-all closes a paper position, API state/close confirmation).
* Next: Phase 2 (regime gate per strategy, correlated exposure, cooldown, fill slippage
  limit).

## 2026-11-21: Phase 2 (risk wiring)

* Regime gate: validate-strategies now records out-of-sample edge per regime
  (`regime_edges`) and the regimes that pass the same gates (`allowed_regimes`); the
  trader rejects a signal whose regime is not in that list, or when no record exists.
  Result on 2 years of Binance data: no strategy passes in ANY regime (all negative);
  copied to production (all strategies stay disabled). Strategies page shows the table.
* Correlated exposure (H3): wired; 1h return correlation over 30 days, assumed 1 when it
  cannot be measured; group cap `max_correlated_exposure` 35% (ceiling 50%), threshold
  0.7 (bounded 0.5-0.9).
* Cooldown: no re-entry into a pair for `cooldown_after_stop_minutes` (120, floor 30)
  after any LOSING exit.
* Slippage: already bounded at order level (entry IOC limit = ask x (1 + slippage);
  stop exits floor at MAX_EXIT_SLIPPAGE); measured per trade in reports.
* Tests: 519 pass. Next: Phase 3 (agents, trade proposals, independent Risk Manager
  verdict, trade review).

## 2026-11-21: Phase 3 (agents, proposals, Risk Manager, trade review)

* `services/agents/nexus_agents` (deterministic, no LLM in the decision path):
  market intelligence, technical (15m/1h/4h/1d: structure, EMA stack, MACD, RSI, VWAP,
  S/R, volume), quant (regime, volatility, out-of-sample EV of strategies in the CURRENT
  regime), derivatives (funding, OI; basis/liquidations reported as not collected),
  sentiment (X + news risk), portfolio (capacity, duplicates).
* Proposal: direction, entry, stop, TP1 (1R partial) / TP2 (2R), R:R, size and risk % via
  the real sizing function, regime, validated strategy, technical and sentiment evidence,
  confidence, contradicting evidence, reasons to reject.
* Risk Manager: rule-only, independent of agent opinions; any failed rule = REJECT =
  final "NO TRADE" (kill switch, news unknown/high, X block, regime, no validated
  strategy, R:R < 2, sizing, stale data, portfolio limits, NO TRADE rules, short on spot).
* Trade review: good/bad trade by PROCESS, good/poor execution (slippage vs 2x assumed),
  strategy failure, risk failure (loss > 1.5x planned), rule violation.
* API: GET /agents/proposals, GET /agents/reviews. Page: "AI proposals". Verified locally
  with live Binance data: both pairs NO TRADE with explicit reasons (regime high_vol,
  no validated strategy, news unknown locally).
* Does not change trading behaviour. Tests: 531 pass.
* Next: Phase 4 (paper realism: latency, missed fills, outages; execution reports).

## 2026-11-21: Phase 4 (paper realism, execution quality)

* PaperBroker: `paper_latency_ms` (250) delay, then fill against a FRESH real book (price
  can move: worse fill or a missed IOC); `paper_book_participation` (0.5) of each visible
  level (partial fills); books older than `paper_max_book_age_seconds` (10) refused as an
  outage; `paper_outage_rate` (0 by default, for drills) injects outages: half "refused"
  (order rejected), half "sent but no reply" (order marked unknown, then reconciled by
  client order ID; tested end to end: one order, never resent).
* Execution quality report (recorded orders only): fill rate, partial, missed, rejected,
  unresolved, slippage avg/p95/worst by entry/exit; GET /execution/quality; card on the
  Performance page with the simulation settings.
* Not simulated: queue position for resting orders beyond the visible-size rule; funding
  (spot has none; futures paper path remains future work).
* Tests: 538 pass. Next: Phase 5 (derivatives data: OI history, basis, liquidations).

## 2026-11-22: Phase 5 (derivatives data)

* `nexus_market.derivatives` worker (inline, every 5 min, public endpoints, no key):
  funding, mark/index basis, open interest + 25 h hourly history (1h/24h change),
  accounts and top-trader long/short ratios, taker buy/sell ratio -> Redis
  `nexus:derivatives:{SYMBOL}`. Liquidations from the public `!forceOrder@arr` stream
  (reconnect with capped backoff), per-minute buckets -> 1h/24h longs/shorts liquidated
  (USDT, lower bound). Restricted locations are reported, never worked around (this dev
  container is refused; the Frankfurt server is the live check).
* Intelligence reads the stored snapshot (falls back to a direct funding/OI fetch);
  Derivatives agent now uses basis, OI vs price, ratios and liquidations; the
  Intelligence page shows them (the stale "HTTP 451" label is gone).
* Tests: 542 pass (documented formats, liquidation windows, a real local websocket with
  a dropped connection, agent rules).

## 2026-11-22: Phase 6 (strategy library, pre-registered)

* Pre-registered (commit 05def09, before any run): A vwap_reclaim, B volatility_expansion,
  C mtf_confluence (candle strategies, live-capable, migration 0007 seeds them disabled),
  D funding_squeeze, E taker_flow (research-only: need funding / perp taker-buy columns).
* Single run on 2 years of Binance data: ALL FIVE FAIL (expectancy -0.24% to -0.45% per
  trade, profit factor 0.34-0.72). E's range-regime slice (+0.22%, 45 trades) is treated
  as likely chance (15 slices tested); not used. Full table in the pre-registration doc.
* Not testable now: order-book imbalance (no history; E is the flow proxy) and
  liquidation events (Binance archive 404; live collection keeps 24 h only).
* 11 rule-based spot strategies have now failed out-of-sample. No strategy is enabled.
* Tests: 552 pass; alembic check clean.

## 2026-11-22: Phase 7 (dashboard)

* Navigation: 15 sections (Overview, Live Markets, AI Analysis, Strategy Center, Paper
  Trading, Live Trading, Portfolio, Risk Center, X Intelligence, News, Trade History,
  Performance Analytics, Backtesting, Settings, System Health); existing pages grouped
  under them, none removed.
* New pages: Paper trading, Live trading (gate status), News (items + impact), Settings
  (effective config, no secrets, live blockers).
* Overview: exposure, current drawdown, expectancy, average win/loss, Sharpe (daily,
  annualised; shown only with 30+ days and always with a caveat), market regime, X
  sentiment, risk status. GET /overview/context, /news, /settings/summary.
* WebSocket /api/v1/ws/stream: owner session + same-origin check (cross-site WebSocket
  hijacking), read-only push every 2 s (books, kill switch, bot state); Live Markets uses
  it and falls back to polling.
* Tests: 555 pass. Browser e2e (desktop + 360 px): 9 pass incl. every page (new ones
  too) with no errors or horizontal overflow; "validation results are shown honestly"
  fails only because the local e2e database has no AI model rows (pre-existing data
  dependency of that test, not a regression).

## 2026-11-22: Phase 8 (failure recovery) and programme summary

* New trader-level tests: daily loss limit, restart with an open position, crash through
  the stop (exits at market, flagged as a risk failure), high-volatility regime, database
  outage mid-run (loop survives, no orders), Redis outage (fails closed). All passed
  without code changes. docs/FAILURE_MODES.md maps all Step 14 scenarios to behaviour and
  tests (every referenced test verified to exist).
* Tests: 561 pass.

### State after the 8 phases (audit docs/AUDIT-2026-11-21.md)

* Done: durable kill switch + actions; paper risk profile; regime/correlation/cooldown
  gates; six agents + independent Risk Manager + trade review; realistic paper fills +
  execution quality; derivatives data; 5 more pre-registered strategies (all fail);
  15-section dashboard + WebSocket; failure-mode tests.
* Not done / owner decisions: no validated strategy (11 failed) -> nothing trades; X needs
  a Bearer token + credits; liquidation history needs months of stored data (only 24 h
  kept today); Render free plan + shared IP (Binance -1003 risk) -> a dedicated-IP host is
  the durable fix; futures live execution stays disabled.
* Next research ideas (each must be pre-registered and judged on NEW data, e.g. by paper
  trading from now on): always-on funding carry; taker-flow in range regimes; persisting
  hourly liquidation summaries to enable a liquidation study later.

## 2026-11-22: forward paper test of always-on funding carry (started)

* Pre-registered BEFORE start: docs/research/2026-11-22-carry-forward-paper-preregistration.md
  (commit 135d2ee). 10,000 USDT virtual, BTC+ETH, long spot + short perp of equal notional,
  real Binance quotes and real settled funding, 5% rebalance band, exit-price marks net of
  exit costs, gaps recorded not estimated. One decision at day 90: annualised >= 4%, max
  DD <= 2%, positive weeks >= 60%, gaps < 5%.
* `nexus_market.carry_paper` (inline worker, hourly at :02), append-only ledger table
  `carry_paper_events` (migration 0008: RLS on, anon/authenticated revoked, UPDATE/DELETE
  blocked by trigger), GET /research/carry-paper, page Strategy Center -> Funding carry test.
* Tests: 568 pass (delta neutrality, funding sign, rebalance, idempotent funding, restart,
  gap on 451).

## 2026-11-22: forward paper test of taker flow in range regimes (started)

* Pre-registered BEFORE start (commit b2de25d, 12:05 UTC):
  docs/research/2026-11-22-taker-range-forward-preregistration.md. Frozen params
  (ratio_min 0.55, stop 2.5 ATR, target 3 ATR, no strategy exit, 48 h max hold), unchanged
  research code (TakerFlow + study_signals), range-regime signals only, live Binance spot
  klines + perpetual taker-buy share. Decision once at 30 completed trades (~10-12 months)
  or 18 months (then inconclusive if < 30): mean > 0, PF >= 1.15, t >= 1.
* `nexus_backtest.forward_taker` inline worker (hourly at :03); append-only
  `forward_test_events` table (migration 0009, RLS, revoked API roles, UPDATE/DELETE
  trigger); GET /research/taker-forward; page Strategy Center -> Taker flow test.
* Tests: 572 pass.

## 2026-11-22: Binance public-API bans caused partly by NEXUS; shared back-off added

* Logs: spot data API 429 "6000 weight/min" at 11:33 UTC and 418 IP bans (spot and
  futures) from 11:33 to 12:12, while market-data, derivatives, trader books, intelligence
  and the new forward tests kept calling (each call during a ban can extend it), plus a
  burst on every deploy restart (many deploys today). The shared Render egress IP adds
  other customers' weight. NEXUS was a significant cause, not only the shared IP.
* Fix: `nexus_common.binance_guard`: one Redis back-off per API (spot, futures) honouring
  Retry-After and "banned until"; every public-data caller checks it first and records
  refusals (derivatives, carry test, taker test, intelligence, market-data polls, trader
  book fetches, API fallbacks). Unreadable state = do not call. Tests: 576 pass.
* Impact on the forward tests: banned hours are recorded as gaps (never estimated) and
  count toward their pre-registered gap limits. A dedicated-IP host remains the durable fix.

## 2026-11-22: deep upgrade, Phase 0 (audit and plan)

* Completed: `NEXUS_AUDIT.md` (14 audit questions answered from code, production DB and
  Render logs) and `NEXUS_IMPLEMENTATION_PLAN.md` (Phases A to H, safety first).
* Main findings: no live balance reconciliation with Binance (critical before any live
  trading); no persisted risk-state machine; no four-mode switch; production connects as
  the DB owner role; shared-IP Binance bans; no strategy with an edge.
* Tested: `scripts/test.sh` with test Postgres + Redis: 576 passed (integration included).
* Not deployed (docs only; Binance futures back-off until ~08:54 UTC 23 Nov).
* Next: on owner approval ("Phase A"), start A1 live balance reconciliation.

## 2026-11-22: deep upgrade Phase A (A1 + A2)

* Completed:
  * A1 live balance reconciliation (`nexus_execution/reconcile.py`), wired into the trader
    (blocks entries on mismatch or unavailable).
  * A2 persisted risk-state machine (`nexus_risk/state.py`): NORMAL / REDUCED_RISK /
    NO_NEW_TRADES / CLOSE_ONLY / EMERGENCY_STOP, durable owner floor, transitions in the
    audit log, API `/risk/state` and `/risk/state/floor`, Risk page card.
  * NEXUS_RISK_POLICY.md, NEXUS_TEST_REPORT.md.
* Behaviour change in paper: entries now also stop when market data is stale, and run at
  half risk when a loss limit is 80% used. No strategy is enabled, so nothing trades today.
* Tested: 606 passed (integration included); e2e 11 passed, 2 pre-existing failures (no
  AI-model rows in the e2e database).
* Not deployed yet: wait until the Binance futures back-off ends (~08:54 UTC 23 Nov), then
  run the nexus-ai sync command and watch the Render deploy and the Risk page.
* Still open in Phase A: A3 four modes, A4 least-privilege DB role (owner approval), A5
  request-weight budget, A6 restart-during-execution tests. Live ledger seeding (the live
  ledger starts empty, so live could not buy anything) is a new item, added to A3.

## 2026-11-22: deep upgrade Phase A (A3 to A6)

* Completed: operating modes (Settings page), live ledger funding (Live page), request
  weight budget, interrupted-order recovery, migration 0010 (RLS policies for nexus_app),
  scripts/grant_app_role.sql, NEXUS_DEPLOYMENT_GUIDE.md, migrations can use
  NEXUS_MIGRATION_DATABASE_URL.
* Tested: 624 passed; e2e 13 passed + 2 pre-existing failures.
* Not deployed (Binance back-off until ~08:54 UTC 23 Nov). Migration 0010 is a no-op in
  production until the nexus_app role exists.
* Owner step: A4 (NEXUS_DEPLOYMENT_GUIDE.md).
* Next: Phase B (market data: 30m/2h/6h/12h resampled, 1m/3m for BTC/ETH, data-quality
  report).

## 2026-11-22: deep upgrade Phase B (market data)

* Completed: timeframes 1m, 3m, 30m, 2h, 6h, 12h (migration 0011); 1m polled for traded
  pairs with 30-day retention, filled gradually; 3m/30m/2h/6h/12h derived from stored
  closed bars (`nexus_market/derived.py`); Data quality page and
  `/api/v1/markets/data-quality`; chart timeframe selector extended; NEXUS_ARCHITECTURE.md.
* Settings: `NEXUS_MINUTE_CANDLES` (default true), `NEXUS_MINUTE_RETENTION_DAYS` (30).
  Storage estimate on Supabase: about 20-25 MB for 30 days of 1m + 3m for two pairs.
* Tested: 631 passed; e2e 13 + 2 pre-existing failures.
* Not deployed (Binance back-off until ~08:54 UTC 23 Nov). After deploy: watch the Data
  quality page fill (30 days of 1m history fill in about 8 minutes: 3,000 bars per pair every 30 s).
* Next: B3 live trade stream (optional), then Phase C (technical analysis indicators).

## 2026-11-22: Binance rate-limit / reconnect-loop fix

* Root cause, audit, fix and tests: docs/BINANCE_RATE_LIMIT_REPORT.md.
* Central gate for every Binance REST call (circuit breaker, single probe, jittered
  exponential back-off, budget, cache, stats); one combined WebSocket for books and closed
  candles; passive health; debounced Telegram alerts; System page card; `/system/binance`.
* Tested: 654 passed; storm simulation; real Binance stream verified from this environment.
* Deployed together with Phases A and B for the 24-hour paper stability test (live off).
* Next: collect the 24 h results (+1 h, +6 h, +12 h, +24 h) into the report.

## 2026-11-22: deep upgrade Phase C (technical analysis)

* Completed: `nexus_strategies/ta.py` (extended indicators), `nexus_analysis/technical.py`,
  `nexus_analysis/mtf.py`, API `/analysis/technical` and `/analysis/mtf`, dashboard
  Technical analysis page. Timeframe table extended to 1m-1d.
* Tested: 693 passed; e2e 13 + 2 pre-existing failures; real BTC candles checked.
* Not deployed yet: the 24 h Binance stability test runs on production until 16:45 UTC
  23 Nov; a deploy now would restart the service in the middle of it. Deploy after the
  final checkpoint.
* Next: Phase D (machine learning: purged k-fold with embargo, multi-target labels, MTF
  features, LightGBM/HMM/Isolation Forest, model registry).

## 2026-11-23: strategy modules A-G (brief Phase 4)

* Built `nexus_strategies/library_v3.py` (A trend_supertrend, B breakout_retest,
  C momentum_rv, D mean_reversion_vwap, E vwap_trend, F sweep_reclaim, G market_structure),
  seeded disabled (migration 0012), tested (causality, grids, scenarios).
* Pre-registered (880e6e3) and validated once on UNSEEN data (2022-11..2024-11, Binance
  archives, checksums verified) plus the old period: all 7 FAIL. Results appended to
  docs/research/2026-11-23-strategies-a-g-preregistration.md; summary in
  NEXUS_BACKTEST_REPORT.md. Nothing enabled.
* Research data: local database `nexus_holdout` (2022-11-14..2024-11-13). Runner:
  `python -m nexus_backtest.strategies_ag --db URL --label L --out FILE`.
* After the next deploy, copy the A-G validation results to the production strategy rows
  so the Strategies page shows them as failed.
* Next: Phase D (machine learning), judged by net out-of-sample trading performance.

## 2026-11-23: Phase E (signal fusion, strategy selector)

* `nexus_decision/selector.py` and `fusion.py`; the trader's regime gate now also requires
  the selector's robust evidence; API `/decision/fusion`; dashboard Trade candidates page.
* With every strategy failed and no validated model, fusion returns NO_TRADE for BTC and
  ETH with the reason; this is the intended outcome until something passes.
* Tests: 14 unit (selector rules, fusion decisions, blockers, context-not-votes, model
  veto) + 1 trader integration (thin regime evidence rejected).

## 2026-11-23: Phase D (machine learning)

* Built (nexus_ai): multi-target labels, f2 features, purged k-fold + embargo, model
  families (logit, XGBoost, LightGBM, CatBoost; the last two are the `research` extra),
  time-ordered calibration, Isolation Forest OOD, causal HMM, trading-first evaluation,
  abstaining walk-forward (`research.py`, runner `phase_d.py`), registry rollback.
* Pre-registered (d05b90e) and run once: all configurations FAIL (0 trades; AUC ~0.51-0.52;
  negative Brier skill). Volatility expansion is predictable (AUC 0.76-0.81). Results in the
  pre-registration and NEXUS_MODEL_REPORT.md. Nothing validated or activated.
* Runner: `python -m nexus_ai.phase_d --db URL --label L --features f2 --out FILE`
  (needs `pip install .[research]`).
* Next candidates (each needs its own pre-registration): volatility forecasts for risk
  sizing; maker-execution study; Phase F execution (post-only, stop-limit) and the testnet
  run.

## 2026-11-23: Phase F1 (execution: post-only and stop-limit orders)

* **Completed: order types.** Post-only (Binance `LIMIT_MAKER`) and stop-limit (Binance
  `STOP_LOSS_LIMIT`) orders now work across the whole stack:
  * the order model, and rounding (precision);
  * the paper simulator, which mirrors Binance's refusals;
  * the Binance adapter (ccxt `limit` + `stopLossPrice`);
  * the order manager, which stores the trigger state in the database so it survives restarts;
  * migration 0013 (`orders.stop_price`, `order_type` 'stop_limit');
  * the orders API, and the dashboard Orders page (type and stop columns).
* **Unchanged on purpose:** the trader still uses its own software stops and IOC exits.
  Paper and live trading behaviour is the same as before.
* **Tested:** see NEXUS_TEST_REPORT.md.
* **Next: F2. Needs the owner's Binance demo (testnet) key.**
  * Order-list (OCO) support: two sells on one balance need a database design.
  * Exchange-side protective stops placed by the trader for live spot.
  * An end-to-end demo run of entry, protective stop, take-profit and cancel/replace.

## 2026-11-23: Phase G1 (backtesting realism and robustness)

* **Completed: size-dependent slippage** (`SlippageModel`).
  * Real data shows impact under 1 bp at NEXUS sizes, so the 5 bp assumption was realistic.
  * Fees dominate costs.
* **Completed: fuller backtest reports.** Recovery factor, average R, slippage, and results
  by coin, hour and weekday.
* **Completed: out-of-sample breakdowns** and a parameter-stability report.
  * Rerun with
    `python -m nexus_backtest.stability --db URL --out FILE`.
  * Result: PBO is about 0.5 and the training-vs-unseen rank correlation is about 0, so the
    losses are not a tuning problem.
* **Not changed:** any strategy's validation result, the trader, and live trading (off).
* **Next:** Phase H (dashboard and documents), unless the owner provides the demo key for F2.

## 2026-11-23: Phase H (dashboard: decision log; documents)

* **Completed: the decision log, end to end.**
  * Worker `decision-log`, migration 0014 (`decision_logs`, append-only, 180-day
    retention), API `/decision/log`, and a Decision log page in the dashboard (AI Analysis
    section).
  * The other Phase H1 items already existed: risk-state badge, mode switch,
    reconciliation panel, data-quality page, AI models page, Trade candidates page.
* **Documents updated:** NEXUS_ARCHITECTURE.md (decision log, order types, backtesting),
  NEXUS_DEPLOYMENT_GUIDE.md (migrations 0013/0014), the implementation plan and the test
  report.
* **Tested:** 789 passed. e2e: 13 passed, plus the same 2 failures as earlier phases.
* **Not deployed.** The F1, G1 and H work ship together in the next deploy, which needs the
  owner's go-ahead. Migrations 0013 and 0014 run automatically on deploy.
* **Next:** F2 (needs the owner's Binance demo key) or deploy.

## 2026-11-23: Binance Demo Trading removed (owner decision)

* **Removed from the code:** demo keys, the demo/testnet order tests (API, `ordertest.py`,
  dashboard buttons), and demo hosts. Testnet and production keys remain.
* **Production database:** it holds one old demo key row, already revoked. It is kept,
  shown as "demo (not supported)", and the probes refuse it. The schema is unchanged:
  migrations stay as history and the constraint still accepts the old row.
* **F2 on hold:** exchange-side stops and OCO can only be verified with one supervised,
  minimum-size real order. That needs the owner's explicit approval and live permission.
  The trader keeps its software stops.
* **Tested:** 785 passed; e2e 13 passed, plus the same 2 known failures.

## 2026-11-23: deploy of F1 + G1 + H + demo removal

* **First deploy:** `d196557` went live at 06:45 UTC. Migrations 0013 and 0014 applied.
* **Incident:** market-data and trader could not connect (EMAXCONNSESSION). Supabase's
  session pooler allows 15 client connections, and the new decision-log worker's pool
  pushed the app over that.
* **Impact:** paper only and 0 orders. Candles were not stored from about 06:45 to 06:55;
  the gap-repair job refills them.
* **Fix:** `3e41486`, live at 06:55.
  * Infrequent workers (decision log, carry paper, forward taker) open a connection only
    while working.
  * The API, market-data and trader pools are 3 + 2 overflow; the notifier's is 1 + 1.
* **Verified at 07:05 UTC:**
  * 5 pooler connections (was 15);
  * WebSocket connected; candles current (1m 07:04, 5m 07:00);
  * decision log rows written (NO_TRADE for both pairs);
  * no Binance pauses, 0 orders.

## 2026-11-23: autonomous follow-ups (owner: "do everything by yourself")

* **Deployed `bfacd6f`** (live 07:15 UTC). Clean start: no worker crashes, no database
  errors, WebSocket connected at 07:15:26.
  * The trader waits for a Binance pause at start-up instead of crash-restarting, and
    releases its connections if start-up stops.
  * Clearer message when the rate-limit state cannot be read (it is not a ban).
* **e2e:** 15/15 passed, using labelled fixtures. `e2e.sh` refuses any database that is
  not an e2e database.
* **Final 24 h stability report** scheduled for 24 Nov 07:20 UTC, covering this release.
* **Still needs the owner, so not done:**
  * F2: a supervised real-money test order;
  * any live trading;
  * A4: database role password;
  * paid hosting with its own IP.

## 2026-11-23: strategy search (owner: "find the best profitable strategy and implement it")

* **Searched** the literature on trend following, time-series and cross-sectional momentum,
  and calendar effects.
* **Picked** the best-documented spot-friendly method: Zarattini, Pagani & Barbon (2025),
  "Catching Crypto Trends".
* **Pre-registered** the rules and gates first (cc41f5f), then ran the test once on
  Binance daily archives, 2017-2026, checksums verified.
* **Result: PASS on all 6 gates.**
  * Sharpe 1.11 against 0.74 for buy-and-hold.
  * CAGR 14.1%; max drawdown 15.6% against 76%.
  * It holds per coin, at double costs, and for every lookback.
  * Placebo p is about 0.03.
* **Implemented** as a daily forward PAPER test: worker `trend-paper`, migration 0015, API
  `/research/trend-forward`, and the dashboard page "Trend strategy test". It starts
  2026-11-24 with 10,000 virtual USDT and is reviewed at day 180 (2027-05-23).
  * Current signal (22 Nov close): 0% invested in both coins (downtrend), so it starts in
    cash.
* **Live:** not enabled. It would need all of the following:
  * a forward-test pass;
  * a dedicated IP;
  * the owner's two-factor activation and a funded live ledger.

## 2026-11-23: spot strategy engine (brief "professional Binance SPOT strategy engine")

* **Built** (plan: docs/SPOT_ENGINE_PLAN.md):
  * the regime engine (6 labels);
  * Trend Pullback and Breakout + Retest;
  * shared risk and exit rules (TP1/TP2, break even after fees, no stop widening,
    3-loss cooldown, correlated clusters, a 4% weekly ceiling);
  * the portfolio backtester (spread, slippage, partial fills, rejections, Binance filters,
    emergency exit);
  * a full report (Monte Carlo, bootstrap CIs, benchmark, monthly, per symbol, strategy and
    regime);
  * the paper runner (its own `spot-engine` portfolio, real OrderManager and PaperBroker,
    every signal, rejection, order, fill and exit persisted);
  * the API `/spot-engine`, the dashboard page "Spot strategy engine" with LIVE TRADING
    DISABLED, and migration 0016 (strategy rows, disabled).
* **Backtest** (pre-registered, 10 coins, 2022-11..2026-11): **both FAIL**.
  * Trend Pullback: PF 0.59, -14.5%. Breakout + Retest: PF 0.71, -10.8%.
  * Both lose in every split and on 8-9 of 10 coins, with 95% CIs below break-even.
  * BTC buy-and-hold returned +194% over the same period.
* **Paper:** runs from the next deploy to observe them on new data. Not eligible for live.
* **Tests:** 820 passed. **Live trading stays disabled.**
* **Deployed** cffd743 at 10:27 UTC on 23 Nov: migrations 0015 and 0016 applied, and the
  spot-engine and trend-paper workers running.
* **Futures pause (finding).** Binance banned the shared Render IP on fapi.binance.com
  ("banned until <epoch>" in the 418 body). The gate's first version stored that end time
  at 12:20 on 22 Nov.
  * The logs keep saying "paused until 08:54:50" because the message showed only the time
    of day. The real end is 08:54:50 UTC on a later day (24-26 Nov, the 4-day cap).
  * This is Binance's own stated ban, so NEXUS correctly waits.
  * The message now shows the full date; this ships with the next deploy.
  * The derivatives, carry and taker forward tests have gaps until the ban ends; a dedicated
    IP would avoid this.

## 2026-11-23: global strategy integration (owner master command)

Plan, audit and research: docs/GLOBAL_STRATEGY_INTEGRATION.md. The source code of
Freqtrade, FreqAI, Hummingbot, Jesse, NautilusTrader and Backtrader was read, plus
TradingView's documentation.

* **S1 start-up analysis (Freqtrade recursive-analysis): fixed a real bug.**
  * The live spot engine loaded 4,000 15m bars, which left the 4h EMA200 up to 3.5% away
    from the backtest's value.
  * It now loads 12,000 bars, and opens new positions only after 9,600 bars.
  * A unit test guards it.
* **S2 protections (Freqtrade) and the owner's risk profile.**
  * `nexus_risk/protections.py` (stop-loss guard, low-profit pair lock, lookback drawdown,
    cooldown).
  * Spot rules: a 5% portfolio-drawdown halt, ceilings enforced, `RESEARCH_PROFILE`.
  * Settings defaults are now 0.5% per trade, 2% daily, 5% drawdown (weekly 3%). Ceilings
    are unchanged.
  * The production paper account had no drawdown (10,000 → 10,000) when the 5% limit was
    added.
* **S3 pre-trade checks (NautilusTrader)** in `OrderManager.place`, for entries only:
  * REDUCING state (kill switch, or an owner floor of NO_NEW_TRADES or stricter);
  * a fat-finger cap of 50% of the quote balance;
  * a 5% price band;
  * 10 entries per minute.
  * Exits always pass.
* **S4 library.** 8 strategies (`library_global.py`), the library backtester, and the
  significance tools (stationary bootstrap, SQN, Holm, trade-shuffle Monte Carlo).
  * Pre-registered (081c550), then run once.
  * **1 of 24 passes: atr_expansion_breakout @ 1d (BTC/ETH)**: 36 trades, PF 3.44, +7.6%
    over 2019-2026, drawdown 0.9%, p = 0.0002.
  * The pass is small in money and the sample is small (see the pre-registration).
  * All 1h strategies lose after costs.
* **S5 forward paper test.**
  * Worker `library-paper` (`nexus_backtest/library_forward.py`): hourly, simulated only, no
    orders. It writes an append-only ledger in forward_test_events (migration 0017 adds the
    'signal' kind).
  * API `/research/library`; dashboard page "Strategy lab".
  * Run `global-lib-2026-11-24`, review at 180 days. The daily data warm-up depends on the
    stored 1d history.
* **Tests:** 868 passed (unit + integration); e2e 15/15; alembic check clean.
* **Next:**
  1. Check after deploy that the `library-paper` worker reports `ready`, and how many stored
     1d candles exist for the daily strategies.
  2. Optional S6 (FreqAI recency weights, volatility-forecast input for sizing), only with
     its own pre-registration.
  3. Paper-trade the passing strategy through OrderManager / PaperBroker to measure
     execution differences, once it gives a signal.

## 2026-11-23: functional trading bot (owner's urgent request)

* **Why NEXUS placed 0 orders.**
  * All 18 strategies are disabled (each failed validation), so the main trader always
    decided NO_TRADE.
  * The production API key has spot trading OFF and the account holds 0 USDT.
* **Native NEXUS bot, not Freqtrade.** Every pipeline stage already existed. Freqtrade would
  duplicate data, risk and orders, need paid persistent storage, and hit the same blockers.
* **Built:**
  * the strategy `nexus_strategies/bot_strategy.py`;
  * a profile-driven `SpotPaperRunner` (`EngineProfile`; the spot engine is unchanged);
  * the worker `nexus_execution/bot.py` (modes off / monitor / paper; live only via the
    main trader's live gate);
  * migration 0018 (strategy row);
  * API `/trading-bot`, the dashboard page "Trading bot", and `docs/TRADING_BOT.md`.
* **Risk:** the owner's bot profile is 0.25% per trade (0.5% maximum), 1% daily, 3%
  drawdown halt, and 2 positions, plus protections, the kill switch and pre-trade checks.
* **R0 verified:** production holds 1,499 daily bars per pair (2022-10-16 to 2026-11-22).
* **Tests:** 877 passed (unit + integration); e2e 15/15; alembic check clean.
* **Next (owner):** enable spot trading on the key, fund USDT, and decide on a dedicated
  IP. Then the live gate and the supervised F2 order.

## 2026-11-23: bot live path (owner: "Build live mode")

* The bot strategy is also a main-trader `Strategy`: `BotEmaRsiMacd`, 5m with 15m, history
  3,000 bars (`Strategy.history_bars`).
* The owner can approve an unvalidated strategy:
  * `POST /strategies/bot_ema_rsi_macd/owner-approval` with the exact acknowledgement and
    2FA; audited; withdrawing it needs no 2FA;
  * the trader honours it only when the actor is `user:...`.
* The live gate now also requires a key test under 7 days old showing spot trading
  permission.
* Defaults are now the owner's conservative profile: 0.25% per trade, 1% daily, 3% drawdown,
  2 positions.
* Tests: 881 passed; e2e 15/15.
* Next:
  * exchange-side stop-limit orders for live positions;
  * the owner's go-live steps (docs/TRADING_BOT.md).

## 2026-11-23: multi-exchange brief (audit, capability registry, Part-9 risk)

* Audit: docs/AUDIT-2026-11-23-multi-exchange.md.
  * The real stack is Python / FastAPI, not Node.
  * NEXUS has no Claude API and no Sentry.
  * The only production errors were already-fixed bans and pool exhaustion.
* New: `nexus_exchange/registry.py`, live public CCXT detection for Binance, OKX, Bybit and
  Kraken.
  * API `GET /exchanges/capabilities` and `POST /exchanges/capabilities/refresh` (owner;
    Binance is skipped while banned).
  * Exchanges page card.
  * Detected from the container: OKX full (498 spot / 531 perps); Kraken spot only; Bybit
    403; Binance 451.
* Risk defaults (owner Part 9): 0.25% per trade, 1% daily, 2% weekly, 5% drawdown,
  3 positions, 30% exposure; futures 0.25% at 1x (options 1–5x).
* Tests: 884 passed; e2e 15/15.
* Next: owner key test; exchange-side stops; OKX adapter (demo first); a cross-exchange
  budget; Sentry (needs a DSN).

## 2026-11-23: master directive, Phase 1 (audit, architecture, health, risk core)

* docs/PLATFORM_ARCHITECTURE.md: architecture, critical risks, target architecture
  (evolve, not rewrite), 10-phase roadmap, schema, dependency map, test strategy, ADR log.
  docs/CHANGELOG.md started.
* Fixed: the main risk engine lacked an aggregate open-risk limit.
  * Now 1% of equity (`RiskSettings.max_open_risk`, ceiling 3%).
  * The risk to every stop is counted; a position without a stop counts in full.
  * Enforced by the trader after sizing.
* Weekly loss default 3%.
* `GET /metrics` for Prometheus (token-gated; no balances).
* Tests: 889 passed.
* Next (Phase 2 / 5): exchange-side protective stops; an OKX adapter on OKX demo trading.
* Redis connection fix: Render's free Key Value allows 50 connections. Deploy overlap
  reached 49 (16:30 UTC), causing "Timeout connecting to server" and two dashboard 500s at
  17:03 UTC. Every Redis client now uses a bounded blocking pool (workers 3, API 8,
  5 s wait).

## 2026-11-23: research-backed strategy (TSMOM grid, Han / Kang / Ryu design)

* Pre-registered (97b40d4); run once on 10 Binance perps, 2020–2026, actual funding,
  1,586 archives checksum-verified.
* **0 of 96 pass.**
  * Long-only: OOS Sharpe 0.43–0.99, every configuration positive, PBO 0.08. But it is not
    significant after Holm (min p 0.05), drawdowns exceed 25%, and the median year is
    negative in 2025 and 2026: the edge decayed.
  * Short-only loses everywhere; a +390% single-day move would liquidate a 1× short.
  * Volatility targeting cuts drawdown by about a quarter to 40% at a similar Sharpe.
* New tools: `nexus_ai/sampling.py` (CUSUM, volume / dollar / range bars, tested);
  `nexus_backtest/tsmom_grid.py`; a CSCV PBO.
* Next:
  * an edge-decay monitor for forward tests (a rolling Sharpe or CUSUM on strategy returns
    that cuts risk or stops);
  * a CUSUM + triple-barrier ML study needs its own pre-registration (Phase D models
    already failed at AUC 0.51).

## 2026-11-23: edge-decay monitor (owner: "Build the monitor")

* `nexus_risk/edge_monitor.py` (pure). It works on closed trades in R (net P&L / risk to the
  initial stop), with three detectors:
  * one-sided CUSUM for a downward mean shift (k 0.5σ, h 4) → STOP;
  * rolling 20-trade upper bound below 0 → REDUCE (half size);
  * drawdown in R: 10R → REDUCE, 15R → STOP.
* Calibrated by simulation:
  * CUSUM: 1.3% false alarms on a +0.3R edge, 82% of decays to −0.5R caught;
  * 15R stop: 1.5% false alarms, 88% caught.
* It only tightens automatically. The owner's 2FA reset (audited) restarts the count from
  later trades.
* `nexus_execution/edge.py`: state per (strategy, portfolio) in `strategies.parameters`,
  with escalations audited.
  * Enforced in the bot runner (STOP blocks entries, REDUCE halves risk) and in the main
    trader (risk per trade × multiplier).
* API: `GET /strategies/edge-monitor`, `POST /strategies/{code}/edge-reset` (owner + 2FA).
  Trading bot page card.
* Tests: 904 passed (6 unit, 1 trader integration, 1 API); e2e 15/15; no migration needed.
* Not yet: the simulated forward tests (Strategy lab, Crypto Trends) show no edge-monitor
  state. They place no orders, so there is nothing to gate.

## 2026-11-23: "Execute Phase 1": Binance Spot testnet round trip

* testnet.binance.vision gives HTTP 451 from the dev container (not bypassed), so the run
  executes on the Render server with the owner's stored TESTNET key (owner + 2FA).
* `nexus_execution/testnet_run.py`:
  * clock, rules, balances, book and 1h candles;
  * the trend signal (EMA50 / 200, RSI, ADX, ATR, momentum);
  * risk sizing at 0.25%;
  * an IOC buy and sell with deterministic UUIDv4 client IDs, checked on Binance before
    every submit;
  * reconciliation by client ID; a restart drill.
* API `POST/GET /system/testnet/round-trip`; Connection page card; evidence in the audit
  log. docs/TESTNET.md.
* Tests: 910 passed (round trip on a stateful fake Binance: fills, no duplicate on re-run,
  risk rejection, a lost reply reconciled not resubmitted); e2e 15/15.
* **Real testnet evidence requires the owner:** testnet keys and one press of the button
  after deploy.
