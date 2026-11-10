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
- **Admin MFA (migration 0009):** `is_admin()` / `is_staff()` are true only in an
  MFA-verified session (`aal = 'aal2'` in the Supabase JWT). Every admin/staff
  policy and function therefore requires TOTP verification. Privileged users without
  aal2 are sent to `/dashboard/settings/security` (page guards) or get
  "Two-factor verification is required" (API guards).
- **Feature entitlements:** `plan_features` + `has_feature(key)` in SQL, mirrored by
  `hasFeature()` / `requireFeatureApi()` / `authed({ feature })` in TypeScript.
- **Compliance kill switches** (`SIGNALS_VISIBILITY`, `PAID_SIGNALS_ENABLED`,
  `LIVE_EXECUTION_ENABLED`) are environment-only and validated when the server starts
  (`src/instrumentation.ts`). `LIVE_EXECUTION_ENABLED=true` prevents startup.
- **Bearer tokens (mobile):** `Authorization: Bearer <access token>` is verified with
  `getClaims(token)`. On that path cookies are ignored and the CSRF origin check is
  skipped, because a cross-site page cannot attach that header without CORS, and
  none is offered. Malformed Bearer headers get 401. Other schemes, such as Basic
  auth in front of staging, are ignored.
- **Engine role:** `nexus_engine` is NOLOGIN with no table privileges until later
  phases grant specific tables. Its password is set out of band, never in the repository.

## Account data rights

- Export: `GET /api/account/export` returns `export_my_data()`, a SECURITY INVOKER
  function, so RLS bounds it to rows the user can already read.
- Deletion (Settings): user content cascades. Payment records are kept with
  `user_id` set to NULL as financial records. Audit entries the user wrote keep
  `actor_id` NULL. A trigger records `account.deleted` with no identifying data.

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

- CI (`.github/workflows/ci.yml`) runs `npm audit --omit=dev --audit-level=high`
  and a gitleaks scan (`.gitleaks.toml`) on every push. Dependabot opens weekly
  update PRs for npm and GitHub Actions (`.github/dependabot.yml`).
- 2026-11-10: the audit found critical advisories in next 16.3.8 (including RCE)
  and a high one in zod 4.6.5. Both were upgraded (next 16.4.3, zod 4.6.7), giving
  0 production vulnerabilities.
- Also recommended: enable GitHub secret scanning push protection.

## Incident response basics

1. **Contain:** rotate the affected credential; disable features via `feature_flags`
   (e.g. `ai_assistant`, `public_signups`); pause cron jobs on Render if needed.
2. **Assess:** review structured logs (Render), `admin_audit_logs`, `billing_events`,
   and the Supabase auth logs. Preserve evidence; don't delete rows.
3. **Remediate:** patch, add a regression test, and deploy through the normal checklist.
4. **Notify:** inform affected users and authorities as legally required. Document a timeline.
5. **Review:** add the learnings to this document.
