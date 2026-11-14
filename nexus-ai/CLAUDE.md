# CLAUDE.md: NEXUS AI

Self-contained Python project inside this repo (the repo root holds an unrelated
Next.js app with its own CLAUDE.md). Read `docs/ROADMAP.md` first, then `docs/ARCHITECTURE.md`.

Non-negotiables: spot only (no leverage/margin/futures/short/martingale code paths);
never fabricate prices, fills or performance; paper results always labeled paper; live
trading never enabled by code (see docs/SECURITY.md); never resubmit an order after a
timeout (reconcile by client order ID); Decimal + NUMERIC for money; new migration per
schema change, models must match (`alembic check`); risk limits may only be tightened.

Before finishing any change: `scripts/test.sh` with NEXUS_TEST_DATABASE_URL and
NEXUS_TEST_REDIS_URL set (integration tests are skipped otherwise; say so if skipped).
Update docs/ROADMAP.md at the end of each session.
