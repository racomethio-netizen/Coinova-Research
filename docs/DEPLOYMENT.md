# Deployment (Render + Supabase)

> Deploying, buying plans, or enabling live billing requires the owner's
> explicit approval. Nothing in this repository deploys automatically.

## 1. Supabase (database and auth)

1. Create a **production** project, and separately a **staging** project.
2. Apply migrations: `npx supabase link --project-ref <ref> && npx supabase db push`
   (see DATABASE.md). **Do not run `seed.sql` in production.**
3. Auth → URL configuration: Site URL `https://<your-domain>`; redirect URLs
   `https://<your-domain>/auth/callback`. Enable email confirmations, and configure
   SMTP so auth emails come from your domain.
4. Confirm `app_config.require_livemode_subscriptions = true` in production.

## 2. Render web service

Use `render.yaml` as a starting blueprint, or create the service manually:

- Runtime Node 22 · Build `npm ci && npm run build` · Start `npm run start -- -p $PORT`
- Health check path: `/api/health` (returns 503 if a configured database is unreachable)
- Environment: set every required variable from `.env.example` in
  **Render → Service → Environment** (secrets are never committed). Minimum for production:
  `APP_ENV=production`, `NEXT_PUBLIC_SITE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`,
  `SUPABASE_SERVICE_ROLE_KEY`, `MARKET_DATA_PROVIDER=coingecko`, `COINGECKO_API_PLAN`,
  `COINGECKO_API_KEY`, `EMAIL_PROVIDER=resend`, `RESEND_API_KEY`, `EMAIL_FROM`, `CRON_SECRET`.
- `NEXT_PUBLIC_SITE_URL` must be set **at build time** too (CSP/HSTS are built
  from it), so keep it in the service environment.
- Startup fails fast if the environment is invalid (for example the mock provider
  in production). Read the error in the deploy logs.

## 3. Scheduled jobs

Jobs run inside the web service and are triggered by authenticated HTTP calls,
so cron services need only `APP_URL` and `CRON_SECRET`.

| Job | Schedule | Purpose |
|---|---|---|
| `refresh-markets` then `evaluate-alerts` | every 5 min | snapshots and alert evaluation |
| `deliver-notifications` | every minute | email/Telegram delivery with retries |
| `refresh-history`, `refresh-fundamentals`, `compute-scores` | hourly | rotating history/fundamentals batches, then scores |
| `cleanup` | daily | retention policy |
| `sync-plans` | after changing `src/config/plans.ts` | mirror limits into the DB |

Each job is idempotent per time window (`background_jobs.idempotency_key`), so
overlapping or retried triggers are safe. Monitor them in `/admin`.

## 4. Optional integrations

- **Telegram:** create a bot with @BotFather; set `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_BOT_USERNAME`, and `TELEGRAM_WEBHOOK_SECRET`; then register the webhook:
  `curl "https://api.telegram.org/bot$TOKEN/setWebhook" -d url=https://<domain>/api/telegram/webhook -d secret_token=$TELEGRAM_WEBHOOK_SECRET`
- **AI:** `AI_PROVIDER=anthropic`, `ANTHROPIC_API_KEY`, `AI_MODEL=claude-opus-5-5`.
  Disable at any time with the `ai_assistant` feature flag.
- **Billing:** see BILLING.md. Keep `BILLING_PROVIDER=none` until a provider is approved.

## 5. First-run steps

1. Trigger `sync-plans`, `refresh-markets`, `refresh-history`, `refresh-fundamentals`,
   then `compute-scores` (`APP_URL=… CRON_SECRET=… npm run job -- <name>`).
2. Create your account on the site, then grant admin:
   `SUPABASE_URL=… SUPABASE_SERVICE_ROLE_KEY=… npm run admin:grant -- you@example.com`.
3. Publish initial research from `/admin/research`.

## Production checklist

- [ ] `APP_ENV=production`; the health check reports `database: ok` and `marketData: coingecko`
- [ ] No mock banner visible; `/markets` shows CoinGecko attribution and fresh timestamps
- [ ] Every secret is in the host's environment settings; nothing secret is in the repo or `render.yaml`
- [ ] `require_livemode_subscriptions = true`; `BILLING_PROVIDER` is not `dev_simulator`
- [ ] Supabase Auth redirect URLs, email confirmation, and custom SMTP are configured
- [ ] Admin granted through the CLI only; a second test account cannot reach `/admin`
- [ ] Cron jobs running; recent `succeeded` runs visible in `/admin`
- [ ] Legal: terms, privacy policy, risk disclosures, refund policy, and the operator entity in `src/config/site.ts`
- [ ] Prices in `src/config/plans.ts` reviewed; provider price IDs configured
- [ ] Backups/PITR enabled on Supabase; restore procedure tested on staging
- [ ] `npm run typecheck && npm run lint && npm test && npm run test:db && npm run test:integration && npm run build && npm run test:e2e` all green

## Rollback

- **App:** Render → Service → Deploys → select the previous successful deploy →
  *Rollback*. Keep `autoDeploy` off for production until CI gating exists.
- **Database:** migrations are forward-only. To undo, write a new migration that
  reverts the change, test it on staging, then apply. For data loss, use Supabase
  point-in-time recovery (plan-dependent). Never reset production.
- **Feature-level:** toggle `feature_flags` in `/admin` (AI, Telegram, signups,
  maintenance banner), or pause cron services.
