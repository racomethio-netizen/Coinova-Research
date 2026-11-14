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
