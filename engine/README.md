# NEXUS engine

Python service for NEXUS AI market-data ingestion (Phase 3). Later phases add
signals, outcome resolution, backtests and paper-trading fills. The design is in
`docs/nexus/ARCHITECTURE.md` and `docs/nexus/SIGNAL_ENGINE.md`.

- Talks to PostgreSQL as the least-privilege role `nexus_engine`, **never** with the
  Supabase service-role key.
- Uses **public, keyless** market-data endpoints only. There is no code path that can sign
  exchange requests or place orders. `LIVE_EXECUTION_ENABLED=true` refuses to start.
- In production, a source runs only after its terms review is recorded (gate G-4).

```bash
cd engine
uv sync                        # installs locked deps (exclude-newer enforces the 2-week rule)
uv run pytest                  # unit tests; DB tests need ../scripts/db-test.sh up
uv run ruff check . && uv run mypy
uv run nexus-engine check      # validate configuration
uv run nexus-engine ingest     # one incremental ingestion pass
uv run nexus-engine worker     # long-running scheduler
uv run nexus-engine serve      # FastAPI: /health and token-protected /internal/*
```
