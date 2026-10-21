# Testing

All suites run against local, disposable resources. **Never point tests at
production.** The DB harness refuses non-localhost hosts.

| Suite | Command | What it covers |
|---|---|---|
| Unit | `npm test` | Scoring (missing/invalid/boundary/extreme data, determinism, versioning), tokenomics, CoinGecko adapter (success, 429, 5xx, timeouts, invalid/malformed responses, batching), cache staleness, portfolio math (fees, transfers, overselling, P&L), CSV injection, alerts (thresholds, edge-triggering, dedupe keys, disabled, retries), billing (signatures, replay, duplicates, out-of-order, failure, cancel, expiry, refund, livemode), env validation, CSRF, open redirects, prompt-injection handling, Telegram tokens, Markdown link safety, log redaction |
| Database / RLS | `npm run db:test:up && npm run test:db` | Real migrations on PostgreSQL 16 with a Supabase shim (roles, `auth.uid()` from JWT claims, default grants). Covers cross-user isolation, role escalation, self-granted membership, premium content gating, tier-limit triggers, column privileges, audit immutability, AI quota, job idempotency, service-only functions |
| Integration | `npm run test:integration` | Server modules over **real supabase-js → PostgREST → Postgres**: billing webhooks end to end, jobs (refresh, history, fundamentals, scores, alerts, delivery retries), portfolio and watchlist repositories under RLS, Telegram linking, AI quota and failure accounting |
| End-to-end | `npm run build && npm run test:e2e` | Playwright on desktop and mobile (Pixel 7) against `next start` with mock data: pages, search and sort, chart history API, score gating, 404s, auth redirects, form validation, client error-free rendering, no horizontal overflow, API validation, CSRF rejection, security headers |

## Database test setup

`scripts/db-test.sh up` initializes a throwaway cluster in `.tmp/pgdata` on port
54329 (it runs as the `postgres` system user when invoked as root).
`scripts/db-test.sh down` deletes it. Each test run drops and recreates the
`cryptoscope_test` database and applies `tests/db/supabase-shim.sql` followed by
every migration.

## Integration tests

These need a PostgREST binary at `.tmp/postgrest`, or set `POSTGREST_BIN`. For example:

```bash
curl -sSL -o /tmp/pgrst.tar.xz https://github.com/PostgREST/postgrest/releases/download/v12.2.12/postgrest-v12.2.12-linux-static-x86-64.tar.xz
tar xJf /tmp/pgrst.tar.xz -C .tmp
```

The harness (`tests/integration/global-setup.ts`) starts PostgREST with a test
JWT secret and a proxy that maps `/rest/v1` and emulates
`GET /auth/v1/admin/users/:id`. JWTs for anon, service, and user roles are minted
in `tests/integration/jwt.ts`. **GoTrue (Supabase Auth) is not emulated**, so
sign-up and sign-in are verified manually (below).

## Fixtures and test accounts

- Tests create their own users directly in `auth.users` and grant tiers by
  inserting subscriptions. No shared accounts exist.
- The mock market provider is deterministic for a given clock (`MockMarketDataProvider(now)`).
- `supabase/seed.sql` holds development-only sample articles for manual testing.

## Manual checks (need a real Supabase project)

1. Register → receive the confirmation email → click the link → land on the dashboard.
2. Sign out, sign in, and request a password reset → reset via the email link.
3. With the simulator enabled on a dev database: upgrade → portfolio, alerts, and premium articles unlock; cancel, refund, and expiry behave as documented.
4. `npm run admin:grant -- you@example.com` → `/admin` accessible; a second normal account cannot reach it.
5. Run each cron job once (`npm run job -- refresh-markets`, etc.) and check `/admin` job history.

## Performance checks

`MarketDataService` makes at most one bulk provider call per cache window per
instance. The `markets` and `history` APIs serve from cache, and integration
tests confirm jobs are idempotent. Before launch, load-test `/api/markets` and
`/markets` (for example `npx autocannon -c 50 -d 30 http://localhost:3000/api/markets`)
on the target instance size, and check slow queries in Supabase's query performance view.
