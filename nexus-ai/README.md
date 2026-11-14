# NEXUS AI

Personal, multi-exchange (Binance, Kraken, Coinbase Advanced Trade) cryptocurrency
**spot** trading system. Paper trading by default. Live trading is disabled and can
only be enabled by the owner through several independent gates (docs/SECURITY.md).

> **Status: all 15 phases built and tested; deployment verified locally.** No strategy or
> AI model has passed out-of-sample validation on 2 years of real data after costs, so
> the system currently places **no trades** (by design). No profitability is claimed.
> See docs/ROADMAP.md for results and the decisions needed from the owner.

## Fixed trading rules

Spot only · no leverage, futures, shorting or martingale · BTC/USDT, ETH/USDT ·
5m/15m/1h/4h · max 2 open positions · risk/trade 0.25% · daily loss 1% · weekly loss
2% · drawdown shutdown 5% · max exposure 50%. Risk limits can be tightened via config,
never loosened (startup fails).

## Quick start (local)

```bash
cp .env.example .env
scripts/generate-secrets.sh            # paste the output into .env
docker compose up -d --build           # postgres, redis, migrate, api, market-data,
                                       # trader (paper), notifier, monitor
docker compose exec api python -m nexus_api.cli create-owner --email you@example.com
curl localhost:8000/health/ready       # {"status":"ok",...}
curl localhost:8000/api/v1/system/config
```

Production (HTTPS, backups): docs/DEPLOYMENT.md. Dashboard dev: `cd apps/dashboard &&
npm ci && npx next build`, then serve with `NEXUS_DASHBOARD_DIR=apps/dashboard/out`.

Behind a TLS-inspecting proxy, build with `EXTRA_CA_FILE=/path/ca.pem` (a BuildKit
secret; it is not stored in the image).

## Development

```bash
uv venv .venv -p 3.13 && . .venv/bin/activate
uv pip install -e ".[dev]"
export NEXUS_TEST_DATABASE_URL=postgresql+psycopg://nexus:<pw>@localhost:5432/nexus_test
export NEXUS_TEST_REDIS_URL=redis://localhost:6379/15
scripts/test.sh            # ruff + mypy --strict + pytest (integration skipped if URLs unset)
alembic upgrade head       # uses NEXUS_DATABASE_URL
uvicorn --factory nexus_api.main:create_app --reload
```

The integration suite drops and recreates the `public` schema of the test database and
refuses to run unless the database name contains `test`.

## Layout

```
apps/api                     FastAPI control plane (nexus_api)
apps/dashboard               Next.js dashboard (Phase 11)
services/exchange-gateway    Common spot adapter interface + CCXT adapter (nexus_exchange)
services/market-data|strategies|ai-engine|risk-engine|execution-engine|notification-engine
packages/common|configuration|database|security   shared libraries
research/backtesting|training                      offline research (Phases 6, 9)
infrastructure/docker|nginx|monitoring             deployment
migrations/                  Alembic (schema source of truth)
tests/unit|integration|exchange|security
docs/                        ARCHITECTURE, ROADMAP, SECURITY, EXCHANGES, DATABASE
```

## Development plan

1 environment ✅ · 2 database ✅ · 3 exchange adapters 🟡 (orders need keys) · 4 market
data ✅ · 5 strategies ✅ (all rejected) · 6 AI ✅ (model rejected) · 7 risk ✅ ·
8 execution ✅ · 9 backtesting ✅ · 10 paper trading ✅ · 11 dashboard ✅ · 12 Telegram ✅ ·
13 security ✅ · 14 deployment 🟡 (needs VPS) · 15 continuous paper evaluation 🟡.

A phase is marked complete only when its acceptance tests pass. Models are promoted
only after passing out-of-sample and walk-forward validation; a high historical win
rate alone is never a promotion criterion.
