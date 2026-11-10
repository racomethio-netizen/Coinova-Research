# Security review (Phase 10, 2026-11-10)

An internal review of every risk in [SECURITY_RISKS.md](SECURITY_RISKS.md) against the code
as it is on branch `claude/cloud-environment-setup-9kee8o`. It is **not** an independent
audit. An independent review is still required before paid signals (G-2), and before any
execution work (COMPLIANCE.md §3).

Status: ✅ mitigated and covered by automated tests · 🟡 mitigated in part, or by
configuration that only exists at deploy time · ⛔ open.

| Risk | Status | Evidence (tests or code) | Remaining gap / owner action |
|---|---|---|---|
| R-01 Admin takeover | ✅ | `is_admin()` requires `aal2`; DB tests "admin MFA (aal2) enforcement", "role escalation"; roles granted only by `npm run admin:grant` | Enroll TOTP for every admin before go-live |
| R-02 Rewrite of losing signals | 🟡 | Insert-only triggers + hash chain (DB tests "signal ledger"; engine `test_tampering_is_detected`); nightly export with continuity check (`test_ledger_export_db.py`); `ledger.integrity` alert | Chain heads are not yet published outside our infrastructure. Decide whether to publish the daily head (e.g. a public page or a signed post) |
| R-03 Look-ahead / leakage | ✅ | Truncation-invariance and causality tests, purged/embargoed walk-forward, lockbox (engine `test_strategy.py`, `test_validation.py`); minimum-sample rule in the performance UI | Re-run when a model is added |
| R-04 Entitlement bypass | ✅ | Visibility/tier checks in RLS; entitlement matrix tested per feature × tier (unit `entitlements.test.ts`, DB "entitlement matrix enforced in SQL") | none |
| R-05 Bearer path weakens auth | ✅ | `parseBearer` accepts only `Authorization: Bearer` JWTs verified by Supabase (`security.test.ts`); cookie path keeps CSRF origin checks; no CORS headers are emitted anywhere in `src/` | none |
| R-06 Token theft from device | ✅ | SecureStore (chunked adapter); sign-out revokes the push token first (`mobile/app/(tabs)/settings.tsx`); Supabase refresh rotation | Confirm refresh-token rotation and reuse detection are on in the Supabase project |
| R-07 Engine credential leak | ✅ | `nexus_engine` role with table-scoped grants (DB "nexus_engine role"; engine DB tests run as that role); no service-role key in the engine | Generate its password out of band; rotate on exposure (RUNBOOKS.md) |
| R-08 `/internal/*` abuse | 🟡 | 404 unless `ENGINE_INTERNAL_TOKEN` (≥32 chars) is set; constant-time compare (`api.py`, `test_api.py`). In `render.yaml` the engine is a **worker** with no public port | No rate limit on `/internal/*`. Acceptable while it is not routed; add one before exposing it |
| R-09 Poisoned market data | 🟡 | Validation rules, revision tracking, gap → HOLD (`test_ingest_db.py`, `test_gaps.py`); `marketdata.stale` / `marketdata.gaps` alerts | Optional cross-source check not built (needs a second source, D5) |
| R-10 Source ban | ✅ | Token bucket, `Retry-After`, stop on 418/451 (`test_ratelimit_retry.py`, `test_worker.py`); `marketdata.blocked` critical alert | none |
| R-11 Custom strategies → code exec / DoS | ✅ | Only bounded parameters of built-in strategies (`BacktestParamsSchema`, unit `backtests.test.ts`); `strategies.custom` + per-tier `backtestsPerDay` + one active run in SQL | none |
| R-12 Live trading reachable | ✅ | `LIVE_EXECUTION_ENABLED=true` refused at startup (web `env.ts`, engine `config.py`); static check `test_no_execution.py`; `DisabledExecutionAdapter` | none |
| R-13 Secrets in repo/client | ✅ | gitleaks in CI; `.env*` ignored; logger redaction; engine `ConfigError` never prints values; web-cron logs status only (`test_webcron.py`) | none |
| R-14 Vulnerable dependencies | 🟡 | `npm audit --omit=dev`: 0. `pip-audit`: 0. Dependabot now covers npm (web, mobile), GitHub Actions, **uv (engine) and the engine Docker image**; CI also runs weekly | Mobile: 3 high advisories, all inside **Expo CLI build tooling** (`@expo/cli` → `node-forge@1.4.0`, `zod@3.25.76`). They are not in the app bundle; the app's own `zod` is 4.6.7 (fixed). Clear with the next Expo SDK patch. Dev-only: 8 moderate (`minimatch` via `typescript-eslint`) |
| R-15 Paper-trading abuse | ✅ | Per-user order rate limits and caps in SQL (DB "paper trading") | none |
| R-16 Deletion leaves data / deletes required records | ✅ | DB "account lifecycle: export and deletion"; billing records pseudonymized; ledger exports omit annotation authors | Legal to confirm the retention periods in the privacy policy |
| R-17 Data loss | 🟡 | Nightly ledger export + verification; restore drill script (`scripts/restore-drill.sh`) run locally and in CI on every push; `ledger.export` alert | Requires owner purchases/decisions: Supabase plan with daily backups/PITR, and off-site storage for exports. Run the drill against staging and log it in RUNBOOKS.md |
| R-18 Model artifact tampering | 🟡 | `model_versions.artifact_sha256` is immutable after insert and required for approval (migration 0013) | No model artifact exists yet. Load-time SHA-256 verification must ship with the first model |
| R-19 Misleading claims | ✅ | Performance UI only from DB stats with n/period/assumptions and a minimum sample; pricing hides signals while G-2 is closed (e2e); `FEATURE_MATRIX` has no signal rows (unit) | Copy review of store listings and marketing before launch (GO_LIVE.md) |
| R-20 Notification abuse | ✅ | Hourly caps and dedupe; per-tier limits; abuse reports (DB "notifications", "abuse reports"); ops alerts deduplicated per check and hour | none |
| R-21 Engine down | ✅ | Idempotent resolver that catches up; `engine.heartbeat` critical alert from an **independent** watchdog cron; web cron jobs no longer depend on Render cron services but report their own heartbeat | Configure an external uptime check on `/api/health` (watches the watchdog's host) |
| R-22 Clock/timezone errors | ✅ | UTC everywhere, alignment validation, DB `now()` for `published_at` (`test_timeframes.py`, publication-lag HOLD) | none |

## Other findings in this review

- **Load test** (local, 4 vCPU, mock data, no database): 0 errors at 20 and 50 concurrent
  clients; the per-IP API rate limit returned 429 as designed. This does not measure the
  production instance size. See RUNBOOKS.md "Load test" before go-live.
- **Restore drill** caught that the web DB test fixtures use placeholder signal hashes. The
  drill correctly fails on that database. This is test data only; real chains verify.
- **Watchdog independence**: the check that detects a dead engine must not run inside the
  engine. `check-health` runs on its own Render cron, and delivers its alerts itself.

## Sign-off

| Role | Name | Date |
|---|---|---|
| Owner | | |
| Independent reviewer (before G-2) | | |
