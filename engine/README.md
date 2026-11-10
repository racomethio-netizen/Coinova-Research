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
uv run nexus-engine ledger-export --out DIR          # JSONL + manifest (SHA-256, chain head)
uv run nexus-engine ledger-verify DIR [--previous M]  # files, hashes, chain, continuity
uv run nexus-engine ledger-restore-drill DIR          # round trip through temp tables
```

The worker also runs the nightly ledger export (`LEDGER_EXPORT_DIR`, `LEDGER_EXPORT_HOUR_UTC`,
`LEDGER_EXPORT_KEEP`) and calls the web app's cron endpoints (`WEB_CRON_URL` + `CRON_SECRET`),
each schedule entry in its own thread. Operations: `docs/nexus/RUNBOOKS.md`.
