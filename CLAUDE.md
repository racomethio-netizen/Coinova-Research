# CLAUDE.md — working on CryptoScope Research

Read this first in every session. Then read `docs/ROADMAP.md` for current status
and the next tasks.

## Architecture in one screen

- `src/app/**`: Next.js 16 App Router pages, route handlers (`api/**`), server actions.
  `cacheComponents` is **off** (previous caching model); the root layout is
  `force-dynamic` because the header reads the session.
- `src/proxy.ts`: Next 16 "proxy" (formerly middleware): refreshes the Supabase
  session and optimistically redirects signed-out users from `/dashboard` and `/admin`.
- `src/config/*`: **the single place** for branding, plans/prices/limits, scoring
  weights, and the coin universe.
- `src/lib/**`: framework-light, mostly pure modules (market providers, scoring,
  tokenomics, portfolio math, alert evaluation, billing state machine, prompt
  building). Unit-test these.
- `src/server/**`: server-only modules that touch the database (repositories,
  billing webhook processing, jobs, Telegram, AI service, admin).
- `supabase/migrations/*.sql`: the schema, RLS, and SQL functions. The source of truth for authorization.
- Next.js docs for the installed version live in `node_modules/next/dist/docs/`.
  This Next.js has breaking changes vs. older versions: read the relevant guide before using an unfamiliar API.

## Non-negotiable rules

1. **Never fabricate data.** No invented prices, unlock schedules, payments,
   research results, or citations. Missing → show "—"/"Insufficient data".
   Provider failure → stale-labeled cache or an explicit error, never a guess.
2. **Mock data must stay obviously mock.** Fictional "(Mock)" assets, global
   banner, refused when `APP_ENV=production`.
3. **Identity and entitlements come from the server.** Use `getViewer()` /
   `require*Api()` / `require*Page()` (`src/lib/auth/session.ts`). Never trust
   client-supplied user IDs, tiers, roles, prices, or payment status.
4. **RLS is the last line of defense.** User-facing queries use the user's
   session client. The service-role client (`getServiceClient`) is only for jobs,
   verified webhooks, and admin reads after a server-side admin check.
5. **Entitlements change only in `processBillingWebhook`** after signature
   verification. Never grant access from a redirect or client callback.
6. **Secrets are server-only.** No `NEXT_PUBLIC_` secrets. Never log secrets
   (the logger redacts common keys; don't defeat it). Never commit `.env*`.
7. **Money and quantities use decimal strings + `decimal.js`.** No floats for
   authoritative portfolio math.
8. Do not deploy, purchase services, or enable live billing without the owner's
   explicit approval.

## Coding standards

- TypeScript strict; validate every external input with Zod (`parseJsonBody`,
  `parseQuery`, schemas next to the feature).
- API responses use the envelope in `src/lib/http/api.ts` (`ok` / `fail` / `ApiError`).
  Cookie-authenticated mutations go through `authed()` (CSRF origin check, rate limit).
- Map DB errors with `mapDbError`; never return raw database messages.
- Keep UI accessible: labels for inputs, `aria-invalid` + error text, focus states,
  no information conveyed by color alone, works at 360px wide.
- Match existing style: small components, Tailwind utility classes using the
  design tokens in `globals.css` (`bg-elevated`, `text-muted`, `border-line`…).

## Database schema changes

- Add a **new** timestamped file in `supabase/migrations/`; never edit a migration
  that may have been applied anywhere. Never reset/drop a real database.
- Every new table: RLS enabled, explicit policies, explicit `revoke`s for roles
  that must not write, indexes for the queries you add.
- SECURITY DEFINER functions: `set search_path = ''`, fully qualified names,
  `revoke execute ... from public` unless intentionally callable.
- Add RLS tests to `tests/db/rls.test.ts` for any new user-owned or gated data.
- If plan limits change, update `src/config/plans.ts` **and** run the `sync-plans` job.

## Commands to run before finishing any change

```bash
npm run typecheck && npm run lint && npm test
npm run db:test:up && npm run test:db && npm run test:integration   # if SQL or server modules changed
npm run build && npm run test:e2e                                    # if pages/routes changed
```

Dependencies: `.npmrc` sets `legacy-peer-deps=true` because npm 10's resolver
crashes on Vite's optional devtools peer chain. Pin new packages to versions at
least two weeks old.

## Continuing work across sessions

1. Read `docs/ROADMAP.md` → "Next steps".
2. Run the checks above to confirm a green baseline.
3. Work in small verified increments; update `docs/ROADMAP.md` (Completed /
   Tested / Incomplete / Blocked) at the end of each session with exact next commands.
