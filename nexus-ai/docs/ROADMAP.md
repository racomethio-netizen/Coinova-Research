# NEXUS AI roadmap and status

_Last updated: 2026-11-14 (session 1: Phase 1 + schema part of Phase 2)._

Legend: ✅ done and covered by passing tests · 🟡 done but needs external
verification · ⛔ blocked on owner decision/credentials · ⬜ not started.

## Verified this session

| Check | Result |
|---|---|
| `ruff check .` | ✅ clean |
| `mypy` (strict, 6 packages, 22 files) | ✅ no issues |
| Unit tests (`tests/unit`) | ✅ 46 / 46 |
| Security tests (`tests/security`) | ✅ 18 / 18 |
| Exchange adapter tests (`tests/exchange`, fake ccxt client) | ✅ 58 / 58 |
| Integration tests (`tests/integration`, PostgreSQL 16 + Redis 7) | ✅ 26 / 26 |
| `alembic upgrade head` → `alembic check` → `downgrade base` → `upgrade head` | ✅ |
| `docker compose up` (postgres, redis, migrate, api) | ✅ all healthy, `/health/ready` 200 |
| Container auto-restart after crash (SIGKILL of API process) | ✅ restarted |
| Secrets absent from container logs | ✅ (Postgres/Redis passwords, session secret, encryption key) |

## Phases

### Phase 1: environment, architecture, foundation ✅
- ✅ Environment inspected (Python 3.13, Postgres 16, Redis 7, Docker 29)
- ✅ Architecture (docs/ARCHITECTURE.md), repository structure
- ✅ Configuration management with hard risk ceilings and live-trading gates
- ✅ Credential encryption (AES-256-GCM, context-bound, rotatable)
- ✅ Structured JSON logging with secret redaction
- ✅ Exchange adapter interface + CCXT spot adapter + precision validation
- ✅ FastAPI app with liveness/readiness, Docker image, Compose stack

### Phase 2: architecture and database (partially done)
- ✅ 22 tables, NUMERIC money, UTC timestamps, FKs, indexes, unique and CHECK
  constraints; paper/live isolation via composite FKs + immutable mode; append-only
  audit log; one open position per base asset; one active model per name
- ⬜ Repository layer (typed CRUD for orders/positions/balances in transactions)
- ⬜ Users/auth bootstrap (owner account creation CLI, Argon2id)

### Phase 3: exchange adapters 🟡 (interface done)
- ⬜ Read-only live verification on all three exchanges (markets, tickers, books, OHLCV)
  — needs network access to exchange APIs from the runtime host
- ⬜ Binance spot testnet order round-trip (needs testnet keys) ⛔
- ⬜ Native Coinbase `key_permissions` and Binance `apiRestrictions` checks
- ⬜ Account fee tiers; Kraken native `cl_ord_id` lookup verification
- ⬜ Exchange health monitor job + `system_events`

### Phases 4–15 ⬜
Market data → strategies → AI → risk → execution → backtesting → paper trading →
dashboard → Telegram → security testing → VPS deployment → continuous paper trading.
See the master plan in README.md.

## Open decisions for the owner ⛔
- Initial capital (needed only for paper portfolio sizing; any number works for paper).
- Exchange API keys: create **read + spot-trade only, withdrawals disabled, IP-restricted**
  keys when Phase 3 live verification starts. Binance testnet keys for order tests.
- VPS provider and domain name (Phase 14).

## Next session: exact commands

```bash
cd nexus-ai
uv venv .venv -p 3.13 && . .venv/bin/activate && uv pip install -e ".[dev]"
# local services (or: docker compose up -d postgres redis)
export NEXUS_TEST_DATABASE_URL=postgresql+psycopg://nexus:<pw>@localhost:5432/nexus_test
export NEXUS_TEST_REDIS_URL=redis://localhost:6379/15
scripts/test.sh                       # lint + mypy + all tests: must be green first
```
Then: Phase 2 repository layer → Phase 3 read-only live adapter verification
(`pytest -m live_exchange`, new tests in `tests/exchange/live/`).
