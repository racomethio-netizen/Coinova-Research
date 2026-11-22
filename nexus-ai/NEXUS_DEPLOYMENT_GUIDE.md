# NEXUS deployment guide

Production today: Render web service `nexus-ai-fra` (Frankfurt, free plan, 512 MB) running
the API with the workers inside it; Postgres on Supabase; Redis on Render Key Value. The
service auto-deploys from GitHub `racomethio-netizen/nexus-ai` branch `main`. Start command
(DEPLOY_RENDER.md): `alembic upgrade head && uvicorn ...`, so migrations run on every
deploy. More detail: docs/DEPLOYMENT.md.

## Before every deploy

1. `scripts/test.sh` with the test Postgres and Redis set: everything passes.
2. Not during a Binance ban or back-off: a restart sends a burst of requests from the
   shared IP. Check the System page or the logs for `BinanceBackoff`.
3. Live trading stays off (`NEXUS_TRADING_MODE=paper`, `NEXUS_LIVE_TRADING_ENABLED=false`).

## After every deploy

1. Render shows the deploy as live; `/health/ready` returns ok.
2. Risk page: "Risk state (paper)" is reported within a minute. NORMAL is expected when
   candles are current; NO_NEW_TRADES with "market data stale" means the market-data
   worker is not ingesting.
3. Settings page: the operating mode is what you chose (default Paper trading).
4. Logs: no `cycle_failed`, no `binance_weight_pause` loops.

## A4: switch the app to the restricted database account (owner action)

Why: the app connects as `postgres`, the owner of every table. If its password leaked,
whoever held it could delete the audit log or change the schema. `nexus_app` can read and
write trading data only: no schema changes, no deleting the audit log or research
ledgers. Only you can do this step because it involves choosing a password.

1. Supabase dashboard -> SQL Editor. Run, with a long random password of your own:

   ```sql
   CREATE ROLE nexus_app LOGIN PASSWORD 'choose-a-long-random-password';
   GRANT CONNECT ON DATABASE postgres TO nexus_app;
   ```

2. In the same editor, paste and run the whole of `scripts/grant_app_role.sql` (grants
   plus row-level-security policies for `nexus_app` only). It is safe to run twice.
3. Render -> `nexus-ai-fra` -> Environment:
   * Add `NEXUS_MIGRATION_DATABASE_URL` = the CURRENT value of `NEXUS_DATABASE_URL`
     (the owner connection, used only to run migrations).
   * Change `NEXUS_DATABASE_URL`: the same address, with the user and password replaced.
     With the Supabase pooler the user part looks like `postgres.<project-ref>`; it
     becomes `nexus_app.<project-ref>`. With a direct connection it becomes `nexus_app`.
4. Save; Render redeploys. Check `/health/ready`, the Risk page and the logs. If anything
   fails, put the old `NEXUS_DATABASE_URL` back (the owner URL): nothing else changes.

Never paste the password into a chat; it goes only into Supabase and Render.

## Not done by code (owner decisions)

* Paid hosting with its own outbound IP before any live trading (the free plan shares its
  IP with other users, which caused the Binance bans).
* A Binance demo key for the testnet run (plan item F2).
* Live trading: off. The Live autonomous mode refuses to switch on until every gate on
  the Settings page is cleared.
