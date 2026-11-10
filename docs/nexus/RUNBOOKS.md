# Runbooks

What to do when something goes wrong, and the routine operations around backups.
Alerts reach every admin as an in-app notification and an email, at most once per check
and hour. They come from the watchdog (`check-health`, its own Render cron, every 5 min),
which reads `public.ops_health()`. The same checks are on `/admin/health`.

**First moves for any incident:** open `/admin/health`, then the Render logs for
`nexus-engine` and `cryptoscope-web`. Note the time and what you change, in the incident log below.

## Alerts and what to do

| Check | Severity | Threshold | Do this |
|---|---|---|---|
| `engine.heartbeat` | critical | no worker heartbeat for 3 min | Render → `nexus-engine` → Logs. Restart the service. If it crashes on start, read the first error: `Invalid engine environment` lists variable **names** to fix. Outcomes catch up automatically after restart (resolver is idempotent). |
| `marketdata.blocked` | critical | the source returned 418/451 | **Do not** retry from other IPs or regions to get around it (prohibited). Read the source's notice, check the terms review (G-4) and region (D5). Signals stop on their own (gap → HOLD). Restart the worker only once the cause is resolved. |
| `webcron.heartbeat` | critical/warning | no web-cron status for 3 min, or never | The engine runs the web jobs. If the engine is down, see above. If it never reported: set `WEB_CRON_URL` and `CRON_SECRET` on the engine. Fallback: run a job by hand (below). |
| `webcron.failing` | warning | an entry failed 3× in a row | The status shows `failed:<job>:<HTTP status>`. 401 → `CRON_SECRET` differs between web and engine. 5xx → web logs for that job and `/admin` job history. |
| `marketdata.stale` | warning | last candle older than 2 candles + 5 min | Usually follows an engine or source problem. Otherwise run `nexus-engine ingest --symbol <SYM>` in a Render shell and read its log. |
| `marketdata.gaps` | warning | a gap open > 1 h | The worker retries every 10 min. Persistent: `nexus-engine backfill --symbol S --tf T --start … --end …`. If the source has no data for the range, the gap is marked unrecoverable (that is honest, not a failure). |
| `jobs.failing` | warning | ≥3 failed web jobs in 1 h, or >20% | `/admin` → job history → error text. Provider outages (CoinGecko, email) recover on their own; config errors need a fix and redeploy. |
| `jobs.stuck` | warning | a job is still `running` after its lock expired | The process died mid-job. The next window runs it again; nothing to do unless it repeats. |
| `notifications.failing` | warning | ≥10 failed deliveries in 1 h | Check email provider status/keys (`RESEND_API_KEY`) and Telegram token. Failed deliveries are retried with backoff. |
| `engine.jobs_failed` | warning | an engine job failed in 24 h | `select kind, payload, last_error from engine_jobs where status = 'failed' order by finished_at desc;` |
| `ledger.export` | warning | no export for 26 h, or never | `LEDGER_EXPORT_DIR` unset, disk full, or worker down. Run `nexus-engine ledger-export --out /var/data/ledger` in a Render shell. |
| `ledger.integrity` | **critical** | the latest export found a broken chain or a continuity problem | Treat as a possible tampering incident. See "Ledger integrity incident" below. |

## Kill switches (gates G-1 to G-3)

Environment variables only (ADR-008), changed by an owner-approved deploy of **both**
`cryptoscope-web` and `nexus-engine`:

- Hide all signals immediately: `SIGNALS_VISIBILITY=staff` (and `PAID_SIGNALS_ENABLED=false`).
- `LIVE_EXECUTION_ENABLED` must stay `false`; both services refuse to start otherwise.
- Feature-level switches (AI, Telegram, signups, maintenance banner): `/admin` feature flags.

## Ledger integrity incident

1. Set `SIGNALS_VISIBILITY=staff` on web and engine (stops new public signals).
2. Keep every export directory. Do not prune, and copy them off-site now.
3. `nexus-engine verify-chain` on production lists which signals fail.
4. Compare with the last good export: `nexus-engine ledger-verify <new export> --previous <last good>/manifest.json`.
5. Check `admin_audit_logs` and the Supabase logs for direct table access in the window.
   Rotate the `nexus_engine` password and the service-role key.
6. Do not "fix" rows. Corrections are made with annotations (`correction`, `retraction`).
   Disclose publicly if published signals were affected (COMPLIANCE.md §4).

## Backups and the restore drill (R-17)

| What | How | Where |
|---|---|---|
| Full database | Supabase daily backups / point-in-time recovery | **Needs a Supabase plan that includes them (owner purchase)** |
| Signal ledger | Engine, nightly at `LEDGER_EXPORT_HOUR_UTC` (default 02:00): JSONL + `manifest.json` (SHA-256 per file, chain head), verified against the previous export, last `LEDGER_EXPORT_KEEP` (30) kept | `LEDGER_EXPORT_DIR` (Render disk `/var/data/ledger`) |
| Off-site copy | Copy each export directory to storage outside Render and Supabase, e.g. weekly | **Owner decision (storage provider)**. Not automated yet |

Commands (engine shell or locally with `DATABASE_URL` set):

```bash
nexus-engine ledger-export --out /var/data/ledger          # exit 1 if the chain is broken
nexus-engine ledger-verify /var/data/ledger/ledger-<ts> --previous /var/data/ledger/ledger-<prev>/manifest.json
nexus-engine ledger-restore-drill /var/data/ledger/ledger-<ts>   # temp tables only; re-verifies after a round trip
```

**Quarterly full restore drill** (also runs in CI against the test database):

```bash
npm run db:test:up                              # local scratch cluster (never a real database)
scripts/restore-drill.sh '<staging direct connection string>'
```

It dumps the source (read-only), restores into a new local database, compares row counts
of the critical tables, verifies the hash chain on the copy, then drops the copy. Log each run:

| Date | Source | Result | By |
|---|---|---|---|
| 2026-11-10 | local engine test DB (synthetic) | ✅ counts match, chain intact | Claude (Phase 10) |
| | staging | | |

## Running a web job by hand

```bash
curl -fsS -X POST -H "Authorization: Bearer $CRON_SECRET" "$APP_URL/api/cron/<job>"
```

Jobs: `refresh-markets`, `evaluate-alerts`, `deliver-notifications`, `refresh-history`,
`refresh-fundamentals`, `compute-scores`, `cleanup`, `sync-plans`, `check-health`.
Each is idempotent per time window, so a repeat is safe.

## Secret rotation

| Secret | Where | Steps |
|---|---|---|
| `CRON_SECRET` | web, engine, watchdog | Generate 32+ random chars; set on all three; redeploy all three together (a mismatch shows as `webcron.failing` with 401). |
| `nexus_engine` password | Supabase SQL + engine `DATABASE_URL` | `alter role nexus_engine with password '<new>';` then update `DATABASE_URL` and restart the engine. |
| Service-role key | Supabase dashboard + web | Roll it in Supabase, update `SUPABASE_SERVICE_ROLE_KEY`, redeploy web. |
| `ENGINE_INTERNAL_TOKEN` | engine | Only if `/internal/*` is in use. |
| Provider keys | web | Per provider; never paste into chat, tickets or the repo. |

## Load test

```bash
npm run build && APP_ENV=test MARKET_DATA_PROVIDER=mock npx next start -p 3100 &
npx tsx scripts/load-test.ts --duration 30 --concurrency 20 --p95-ms 800 --max-error-rate 0.01
```

Baseline 2026-11-10 (container, 4 vCPU, mock data, no DB): 20 clients → ~200 req/s,
p95 192 ms, 0 errors; 50 clients (pages only) → ~194 req/s, p95 400 ms, 0 errors.
`429` responses are counted separately (per-IP API rate limit working). **Before go-live**,
run it against staging on the target instance (`--allow-remote`, owner approval, off-peak),
with the thresholds above, and record the result here.

## Incident log

| Date (UTC) | Alert / symptom | Impact | Actions | Follow-up |
|---|---|---|---|---|
| | | | | |
