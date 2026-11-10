# Go-live checklist

Every item needs a name and date. Unchecked items block the step they belong to.
**Nothing here is done by Claude without the owner's explicit approval:** no deployment,
no purchase, no live billing, no gate change.

Launch in stages. Each stage keeps the gates of the stages after it closed.

## Stage A: staging (no real users)

- [ ] Owner approves creating the Supabase **staging** project and the Render services (cost)
- [ ] `npx supabase link --project-ref <ref> && npx supabase db push`; `supabase/seed.sql` (staging only)
- [ ] `alter role nexus_engine with login password '<generated>'` (out of band); engine `DATABASE_URL` via the pooler
- [ ] Render blueprint reviewed and applied: `cryptoscope-web`, `nexus-engine` (worker), `nexus-watchdog` (cron); all `sync: false` secrets entered in the dashboard
- [ ] `CRON_SECRET` identical on web, engine and watchdog; `WEB_CRON_URL` = the web URL (https)
- [ ] `npm run job -- sync-plans` (or `POST /api/cron/sync-plans`)
- [ ] Admin granted with `npm run admin:grant`; TOTP enrolled; `/admin/health` shows **All checks pass** (or only explained warnings)
- [ ] An alert reaches an admin: stop the engine for 5 minutes, confirm the `engine.heartbeat` email, restart
- [ ] Ledger export appears after 02:00 UTC; `ledger-verify` passes
- [ ] `scripts/restore-drill.sh <staging URL>` passes; logged in RUNBOOKS.md
- [ ] Load test against staging on the target instance size, thresholds met; logged in RUNBOOKS.md
- [ ] External uptime check on `https://<web>/api/health`
- [ ] Every CI job green on the commit being deployed

## Stage B: production, research features only (G-1 `staff`, G-2 `false`)

- [ ] D2: trademark search done; brand set in `src/config/site.ts`; domain bought (owner)
- [ ] Supabase **production** project with daily backups / PITR (owner purchase); SMTP, auth redirect URLs
- [ ] Off-site copy of ledger exports decided and running (owner decision)
- [ ] Legal: terms, privacy policy (retention periods, R-16), risk disclosures, operator entity
- [ ] Everything in docs/DEPLOYMENT.md "Production checklist"
- [ ] SECURITY_REVIEW.md signed by the owner; every 🟡 item accepted or closed
- [ ] Claims-policy copy review (COMPLIANCE.md §4) of the site, emails and store listings
- [ ] `autoDeploy: false` kept; rollback steps (DEPLOYMENT.md) understood

## Stage C: market data shown to users (gate G-4) and live signal engine

- [ ] D5: data source and hosting region chosen; terms review recorded in `market_sources.terms_reviewed_at`
- [ ] Network access from the engine to the source hosts; the region does not return 451
- [ ] ≥3-year backfill per pair/timeframe; gaps reported honestly (Phase 3 exit)
- [ ] 24 h live ingestion without errors (Phase 3 exit)
- [ ] Validation report for `trend_pullback` published internally, whatever it shows (Phase 5 item 5)

## Stage D: signals visible to users (gate G-1)

- [ ] Legal review of signal publication in the target markets
- [ ] An approved model or rules version with a reproducible validation report (or signals stay "not calibrated" with that label)
- [ ] `SIGNALS_VISIBILITY=beta` (invited users) first, then `public`, each by an approved deploy of web **and** engine
- [ ] Decision on publishing the daily chain head externally (SECURITY_REVIEW.md R-02)

## Stage E: paid plans (G-5) and paid signals (G-2)

- [ ] D9: payment provider chosen after the eligibility checklist (docs/BILLING.md); adapter passes `tests/support/billing-conformance.ts`
- [ ] Prices in `src/config/plans.ts` reviewed; provider price IDs set; `sync-plans` run
- [ ] Webhook configured; end-to-end test in the provider's test mode on staging
- [ ] G-6: country allow-list for paid features decided
- [ ] For paid signals only: legal approval for paid signals, an independent security review, `PAID_SIGNALS_ENABLED=true`

## Stage F: mobile store release (gate G-7)

- [ ] Apple/Google developer accounts and EAS (owner approval, cost)
- [ ] Store-policy review in docs/nexus/MOBILE.md completed (crypto apps, in-app purchase, D7)
- [ ] Production `EXPO_PUBLIC_*` values point at production; push credentials configured
- [ ] Store listing copy passes the claims policy

## Never (COMPLIANCE.md §2–3)

`LIVE_EXECUTION_ENABLED` stays `false`. No exchange keys, custody, deposits or withdrawals.
Execution would need the full §3 checklist **and** new reviewed code.
