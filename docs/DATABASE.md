# Database

PostgreSQL on Supabase. Migrations in `supabase/migrations/` (apply in filename order):

| File | Contents |
|---|---|
| `…000100_core.sql` | profiles (+ signup trigger), user_roles, `has_role/is_admin/is_staff`, app_config, feature_flags, append-only admin_audit_logs |
| `…000200_membership.sql` | membership_plans (seeded from `src/config/plans.ts`), subscriptions, billing_events, billing_payments, checkout_sessions, entitlement functions |
| `…000300_market_research.sql` | cryptocurrencies, market_snapshots (+ `latest_market_snapshots` view), historical_prices, tokenomics_snapshots, token_unlock_events, scoring_configs, research_scores, research_score_snapshots, research_articles, research_article_bodies |
| `…000400_user_features.sql` | portfolios, portfolio_transactions, watchlists, watchlist_items, alerts, alert_evaluations, notifications, notification_deliveries, telegram_accounts, telegram_link_tokens, tier-limit triggers |
| `…000500_operations.sql` | ai_usage_records + `reserve_ai_request`, background_jobs + `acquire_job`, `run_retention_cleanup` |
| `…000600_fundamentals.sql` | cached developer metrics and refresh timestamps on cryptocurrencies |

`supabase/seed.sql` is **development-only** (it lets simulator subscriptions grant
access and adds labeled sample articles). Never run it in production.

## Key relationships

- `auth.users` 1–1 `profiles`; 1–n `user_roles`, `subscriptions`, `portfolios`, `watchlists`, `alerts`, `notifications`.
- `portfolios` 1–n `portfolio_transactions` (both carry `user_id`; policies require the parent to belong to the same user).
- `watchlists` 1–n `watchlist_items`; `alerts` → coin **or** portfolio (check constraint).
- `notifications` 1–n `notification_deliveries` (unique per channel); `notifications.dedupe_key` is unique.
- `research_articles` 1–1 `research_article_bodies` (split so RLS can gate premium text).
- `billing_events` unique (`provider`, `provider_event_id`); `subscriptions` unique (`provider`, `provider_subscription_id`), at most one open subscription per user.

## Row Level Security summary

| Data | anon | authenticated user | staff/admin | service role |
|---|---|---|---|---|
| Market tables, coins, scoring configs, feature flags, plans | read | read | + staff metadata edits | full |
| Research article metadata (published) | read | read | full (drafts too) | full |
| Article bodies | free only | tier ≥ article tier | all | full |
| research_scores | sample coins | sample, or all if Premium | all | full |
| research_score_snapshots | — | Premium | all | full |
| profiles | — | own (limited columns updatable) | admins read | full |
| portfolios, transactions, watchlists, alerts | — | **own only**; tier limits via triggers | — | full |
| subscriptions, billing_payments | — | read own, **no writes** | admins read | full |
| billing_events, background_jobs | — | — | admins read | full |
| user_roles | — | read own, **no writes** | admins read | full |
| notifications | — | read own; update `read_at` only | — | full |
| telegram_link_tokens | — | — | — | full |
| admin_audit_logs | — | — | staff read; append own | append only (immutable trigger) |

The entitlement source of truth is `current_membership_tier()`. A subscription
grants access if `active`/`trialing` before period end, or `past_due` within the
grace period (`app_config.billing_grace_period_days`). Test-mode
(`livemode=false`) subscriptions grant nothing while
`app_config.require_livemode_subscriptions` is true, which is the default.

These guarantees are verified by `tests/db/rls.test.ts` (52 tests) and
`tests/integration/*.test.ts`.

## Applying migrations to a new Supabase project

1. Create a project at supabase.com. Note the project ref and database password.
2. With the [Supabase CLI](https://supabase.com/docs/guides/cli), link and push:
   ```bash
   npx supabase login
   npx supabase link --project-ref <ref>
   npx supabase db push          # applies supabase/migrations in order
   ```
   Alternatively paste each migration file, in order, into the SQL editor.
3. **Development projects only:** run `supabase/seed.sql` in the SQL editor.
4. Auth settings: set the Site URL to `NEXT_PUBLIC_SITE_URL`, add
   `<site>/auth/callback` to the redirect allow-list, and enable email confirmation.
5. Trigger `sync-plans` and `refresh-markets` once (see DEPLOYMENT.md).
6. Grant the first admin: `npm run admin:grant -- you@example.com admin`.

Never run `supabase db reset` or drop objects in an existing project without
explicit owner approval.

## Retention and cleanup (`run_retention_cleanup`, daily)

| Table | Retention |
|---|---|
| market_snapshots | 90 days (daily history is kept in historical_prices) |
| alert_evaluations | 30 days |
| notification_deliveries (terminal) | 90 days |
| notifications | 180 days |
| telegram_link_tokens | 1 day after expiry |
| checkout_sessions (not completed) | 30 days |
| background_jobs | 90 days |
| ai_usage_records | 400 days |

Billing events, payments, subscriptions, and audit logs are kept indefinitely
(financial and audit records). Review against legal requirements before launch.
