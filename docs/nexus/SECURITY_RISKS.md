# Security risks and threat model

Scope: the NEXUS additions plus how they interact with existing controls
(`docs/SECURITY.md`). Likelihood (L) and impact (I) use the scale H/M/L. "Phase" is
where the mitigation is implemented and tested.

## Assets worth protecting
1. User accounts, sessions and personal data (email, preferences, push tokens).
2. Entitlements and billing state.
3. **Integrity of the signal track record** (credibility, legal exposure).
4. Service-role key, `nexus_engine` DB credentials, `ENGINE_INTERNAL_TOKEN`,
   `CRON_SECRET`, provider keys.
5. Model artifacts and validation reports.
6. Availability of ingestion and outcome resolution (missed resolutions skew stats).

## Trust boundaries
Browser/mobile ↔ Next.js API · Next.js ↔ Supabase · engine ↔ Supabase ·
engine ↔ exchange public endpoints · admin UI ↔ privileged operations ·
CI ↔ secrets.

## Risk register

| ID | Risk | L | I | Mitigation | Phase |
|---|---|---|---|---|---|
| R-01 | Admin account takeover leads to approving a bad model, changing pairs or plans | M | H | Supabase TOTP MFA; RLS requires `aal = 'aal2'` for admin writes; admin actions audited; roles granted only by CLI | 2 |
| R-02 | Silent rewrite or deletion of losing signals (insider or compromised key) | M | H | Insert-only triggers (apply to service role), hash chain with externally published heads, annotations instead of edits, nightly export of the chain | 4 |
| R-03 | Look-ahead or leakage produces inflated performance shown to users | H | H | Truncation-invariance test, purged/embargoed walk-forward, lockbox, forward and backtest stats shown separately, minimum-sample rule | 4–5 |
| R-04 | Entitlement bypass (free user reads Pro signals via the API or directly via the Supabase anon key) | M | H | Visibility and tier checks in **RLS**, not only in route handlers; RLS tests per tier | 2, 4, 9 |
| R-05 | Mobile bearer-token path weakens CSRF/auth | M | M | Bearer path accepts only `Authorization` header JWTs verified server-side; cookie path keeps CSRF; no CORS for browsers on bearer routes | 8 |
| R-06 | Mobile token theft from device storage | L | M | `expo-secure-store`; short-lived access tokens; refresh rotation; sign-out revokes push token | 8 |
| R-07 | Engine DB credential leak exposes all user data | L | H | `nexus_engine` role with table-scoped grants; no service-role key in engine; rotate on exposure | 2–3 |
| R-08 | `/internal/*` engine endpoints abused | L | M | Long random bearer token, constant-time compare, rate limit, no user data in responses; preferably not publicly routed | 3 |
| R-09 | Untrusted or poisoned market data (bad candles, exchange glitch) creates false signals | M | M | Validation rules, revision tracking, gap → HOLD, optional cross-source check, alert on anomalies | 3 |
| R-10 | Rate-limit violation or ban by the data source | M | M | Token bucket under limits, `Retry-After`, stop-and-alert on 418/451; no IP rotation | 3 |
| R-11 | User-defined strategies lead to code execution or DoS | M | H | Parameter sets of built-in strategies only, JSON-schema validated; per-tier backtest quotas and runtime caps; queue isolation | 5, 9 |
| R-12 | Live trading becomes reachable by accident | L | H | `LIVE_EXECUTION_ENABLED` env kill switch (startup refuses `true`); no credential schema; CI static check fails on order/withdraw endpoints, HMAC request signing, or exchange SDK imports | 4 (check), 10 |
| R-13 | Secrets committed to the repository or exposed to the client | M | H | `.env*` ignored, no `NEXT_PUBLIC_` secrets, gitleaks in CI, logger redaction, mobile builds hold only public Supabase URL and anon key | 2 |
| R-14 | Vulnerable dependencies (npm, PyPI, Docker base) | M | M | Dependabot, `npm audit`, `pip-audit`, pinned lockfiles, slim pinned base images | 2, 10 |
| R-15 | Paper-trading abuse (spam orders) degrades the engine | M | L | Per-user order rate limit (SQL), max open orders, account caps | 6 |
| R-16 | Account deletion leaves personal data, or deletes records that must be retained | M | M | Documented deletion flow: user data removed; billing records retained pseudonymized as legally required; audit entry; data export before deletion | 2 |
| R-17 | Data loss (DB corruption, accidental migration) | L | H | Supabase Pro daily backups, nightly logical export of critical ledgers, quarterly restore drill, never `db reset` in real projects | 10 |
| R-18 | Model artifact tampering (swap artifact after approval) | L | H | SHA-256 recorded at approval and verified at load; Storage bucket writes by service role only | 4 |
| R-19 | Misleading marketing claims (accuracy, returns) | M | H | Claims policy (COMPLIANCE.md §4); performance UI generated only from DB stats with n, period and CI; copy review checklist | 7 |
| R-20 | Abuse of notifications (spam, Telegram/email cost) | L | M | Existing hourly caps and dedupe; per-tier limits; abuse reports | 7 |
| R-21 | Single engine instance down means missed outcome resolution | M | M | Resolver is idempotent and catches up from candles; heartbeat alert; outcomes keyed by signal | 4, 10 |
| R-22 | Clock or timezone errors cause misaligned candles and leakage | M | M | UTC everywhere; alignment validation; DB `now()` for `published_at` | 3 |

## Existing controls to keep (do not regress)
CSP and security headers, RLS on every table, roles granted only by CLI,
entitlements only via verified webhooks, Zod validation everywhere, `mapDbError`,
logger redaction, and the append-only audit log.
