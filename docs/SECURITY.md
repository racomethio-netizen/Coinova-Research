# Security

## Authentication

- Supabase Auth (email + password, email confirmation recommended). Sessions are
  cookies managed by `@supabase/ssr`; `src/proxy.ts` refreshes them.
- Identity is always derived from `supabase.auth.getClaims()`, which verifies the
  JWT. `getSession()` is never trusted for identity.
- Password policy: 10–128 characters with letters and digits (Supabase's own
  policy also applies). Login, registration, and reset requests are rate-limited
  per IP and per email.
- Password reset and confirmation links land on `/auth/callback` (PKCE code or
  token_hash). Redirect targets pass through `safeNextPath` (no open redirects).
- Responses for registration and reset requests don't reveal whether an account exists.

## Authorization

- Server-side guards in `src/lib/auth/session.ts` (`require*Page`, `require*Api`).
  The proxy redirect is only a UX convenience.
- **Database RLS is the final authority** (see DATABASE.md). Admin writes use the
  admin's own session, so `is_staff()` / `is_admin()` policies also apply.
- Roles live in `user_roles`, writable only with the service role via
  `npm run admin:grant` (audited). No API, UI, profile field, or email-domain rule grants roles.
- Tier limits (portfolios, watchlists, items, alerts, AI requests) are enforced by
  database triggers and functions, so clients cannot bypass them with the public anon key.
- Never trust client-supplied user IDs, tiers, roles, prices, ownership, or payment status.

## Web protections

- CSRF: Server Actions verify origin (Next.js built in). Cookie-authenticated JSON
  mutations go through `authed()` → `assertSameOrigin`. Cookies are `SameSite=Lax`.
- Security headers (`next.config.ts`): CSP (`default-src 'self'`, no third-party
  scripts, `frame-ancestors 'none'`), `X-Frame-Options: DENY`, `nosniff`, strict
  Referrer-Policy, Permissions-Policy, COOP, and HSTS plus upgrade-insecure-requests
  when the site URL is HTTPS. `X-Powered-By` is removed.
- Output escaping: React escapes by default. Article Markdown is rendered to React
  elements by a minimal renderer (no raw HTML), and links are limited to
  https/mailto/relative. CSV exports neutralize formula injection.
- Input validation: Zod on every route, server action, env variable, and provider response.
- Rate limiting: per-IP/per-user in-memory windows (`src/lib/http/rate-limit.ts`).
  **Limitation:** limits are per instance. Exact cross-instance quotas (AI usage,
  hourly notifications) use the database. Before scaling horizontally, move the
  general limiter to a shared store (e.g. Redis or a Postgres function).
- Errors: clients get generic messages and codes; details go to structured logs.

## Secrets

- All secrets are server-side env vars validated in `src/lib/env.ts`. The browser
  gets only `NEXT_PUBLIC_SITE_URL`. The service-role key is used only in `src/server/**` and jobs.
- The logger redacts keys matching password/secret/token/api key/authorization/cookie/prompt.
- `.env*` is git-ignored except `.env.example` (placeholders only).
- Set production secrets in the host's dashboard (Render → Environment), never in `render.yaml`.
- Rotate immediately on suspected exposure: Supabase service-role key, CRON_SECRET,
  TELEGRAM_*, RESEND_API_KEY, ANTHROPIC_API_KEY, billing webhook secrets.

## Billing security

- Entitlements change only in `processBillingWebhook` after the provider adapter
  verifies the signature (HMAC with timestamp tolerance for the simulator).
  Redirect pages never grant access.
- Events are stored with unique (provider, event id), which makes processing
  idempotent; duplicate deliveries are no-ops. Out-of-order events older than
  `last_event_at` are ignored. Concurrent updates use optimistic concurrency.
- One open subscription per user (unique partial index). Checkout accepts only a
  plan ID; prices come from server configuration.
- The simulator is refused in production by env validation, emits only
  `livemode=false` events, and those grant nothing while
  `require_livemode_subscriptions=true`.

## Webhooks and cron

- Telegram: `X-Telegram-Bot-Api-Secret-Token` is compared in constant time.
  Link tokens are 256-bit, single-use, expire in 15 minutes, and are stored as
  SHA-256 hashes. Only private chats are accepted.
- Cron: `Authorization: Bearer CRON_SECRET` (≥ 32 characters), compared in constant time.

## Dependency and secret scanning

- `npm audit --omit=dev` reported 0 production vulnerabilities at the time of writing.
  The `npm audit` dev-only finding (`braces` via `eslint-config-next`) is a
  build-time linter dependency and is not shipped.
- Recommended: enable GitHub secret scanning and Dependabot on the repository.

## Incident response basics

1. **Contain:** rotate the affected credential; disable features via `feature_flags`
   (e.g. `ai_assistant`, `public_signups`); pause cron jobs on Render if needed.
2. **Assess:** review structured logs (Render), `admin_audit_logs`, `billing_events`,
   and the Supabase auth logs. Preserve evidence; don't delete rows.
3. **Remediate:** patch, add a regression test, and deploy through the normal checklist.
4. **Notify:** inform affected users and authorities as legally required. Document a timeline.
5. **Review:** add the learnings to this document.
