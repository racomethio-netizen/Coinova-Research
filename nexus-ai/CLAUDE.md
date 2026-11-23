# CLAUDE.md: NEXUS AI

Self-contained Python project inside this repo (the repo root holds an unrelated
Next.js app with its own CLAUDE.md). Read `docs/ROADMAP.md` first, then `docs/ARCHITECTURE.md`.

Non-negotiables: Binance spot and USD-M futures (futures disabled by default, isolated
margin, leverage 1-5x (default 1x since 2026-11-23), never raised automatically); no martingale or averaging down;
never fabricate prices, fills, balances or performance; paper results always labeled paper;
live trading never enabled by code (see docs/SECURITY.md); never bypass exchange regional
or eligibility restrictions; never resubmit an order after a timeout (reconcile by client
order ID); Decimal + NUMERIC for money; new migration per schema change, models must match
(`alembic check`); risk ceilings in `nexus_config/settings.py` change only by explicit owner
mandate (ceilings: 1% risk per trade, 2% daily, 4% weekly, 10% drawdown, 3 open
positions; defaults since 2026-11-23 (owner, multi-exchange brief): 0.25% per trade, 1% daily,
2% weekly, 5% drawdown, 3 positions, 30% spot exposure, futures 0.25% at 1x; the bot keeps
BOT_RISK, the spot-engine forward test its pre-registered profile, the global library
RESEARCH_PROFILE).
The kill switch is durable (audit log) and must never be auto-cleared.

Before finishing any change: `scripts/test.sh` with NEXUS_TEST_DATABASE_URL and
NEXUS_TEST_REDIS_URL set (integration tests are skipped otherwise; say so if skipped).
Update docs/ROADMAP.md at the end of each session.
