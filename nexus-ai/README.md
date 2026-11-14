# NEXUS AI

Personal, multi-exchange (Binance, Kraken, Coinbase Advanced Trade) cryptocurrency
**spot** trading system. Paper trading by default. Live trading is disabled and can
only be enabled by the owner through several independent gates (docs/SECURITY.md).

> **Status: Phase 1 complete (foundation).** No strategy, model or order execution
> exists yet. Nothing here has produced trading results, and no profitability is
> claimed or implied. See docs/ROADMAP.md.

## Fixed trading rules

Spot only · no leverage, futures, shorting or martingale · BTC/USDT, ETH/USDT ·
5m/15m/1h/4h · max 2 open positions · risk/trade 0.25% · daily loss 1% · weekly loss
2% · drawdown shutdown 5% · max exposure 50%. Risk limits can be tightened via config,
never loosened (startup fails).

## Quick start (local)

```bash
cp .env.example .env
scripts/generate-secrets.sh            # paste the output into .env
docker compose up -d --build           # postgres, redis, migrate, api
curl localhost:8000/health/ready       # {"status":"ok",...}
curl localhost:8000/api/v1/system/config
```

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

1 environment ✅ · 2 architecture & database 🟡 · 3 exchange adapters 🟡 · 4 market
data · 5 strategies · 6 AI · 7 risk · 8 execution · 9 backtesting · 10 paper trading ·
11 dashboard · 12 Telegram · 13 security testing · 14 VPS deployment · 15 continuous
paper trading and evaluation.

A phase is marked complete only when its acceptance tests pass. Models are promoted
only after passing out-of-sample and walk-forward validation; a high historical win
rate alone is never a promotion criterion.
