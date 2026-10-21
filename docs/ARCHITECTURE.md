# Architecture

## Overview

```
Browser ──HTTPS──▶ Next.js (Render web service)
                     ├─ proxy.ts: session refresh, optimistic route guard
                     ├─ Server Components / Server Actions (pages, auth forms)
                     ├─ Route handlers /api/** (JSON API, webhooks, cron)
                     ├─ src/lib/** pure domain modules
                     └─ src/server/** DB-touching services
                            │                         │
                            ▼                         ▼
             Supabase (Postgres + Auth + RLS)   External providers (server-side only)
                                                 CoinGecko · Resend · Telegram · Anthropic
Render Cron Jobs ──POST /api/cron/<job> (Bearer CRON_SECRET)──▶ jobs in the web service
```

The browser never calls Supabase or any third-party API directly. That keeps
every credential on the server and lets CSP restrict `connect-src` to `'self'`.

## Rendering and caching

- `cacheComponents` is disabled (the pre-v16 caching model). The root layout is
  `force-dynamic` because the header shows auth state and the environment banner
  depends on runtime configuration.
- Market data is cached **in process** by `MarketDataService`
  (`src/lib/market/service.ts`) with request coalescing and stale-if-error:

| Data | TTL | Max stale age (served only if refresh fails, labeled "stale") |
|---|---|---|
| Universe markets (one bulk call) | 60 s | 30 min |
| Global market data | 120 s | 60 min |
| Coin details | 5 min | 6 h |
| Price history | 30 min | 24 h |

  With N web instances, each holds its own cache (≤ N provider calls per window).
- Persistent market data (snapshots, history, supply, scores) is written by jobs
  and used for scoring, alerts, and score history.

## Provider interfaces

| Interface | File | Implementations |
|---|---|---|
| `MarketDataProvider` | `src/lib/market/types.ts` | `CoinGeckoProvider`, `MockMarketDataProvider` (dev) |
| `TokenUnlockProvider` | `src/lib/research/unlocks.ts` | none yet; staff-entered cited events |
| `BillingProvider` | `src/lib/billing/types.ts` | `DevBillingSimulator` (dev) |
| `EmailProvider` | `src/lib/notify/email.ts` | `console` (dev), `resend` |
| `AIProvider` | `src/lib/ai/provider.ts` | `AnthropicProvider` (official SDK) |

## Data flow

1. **refresh-markets** (5 min): bulk provider call → upsert `cryptocurrencies`
   (provider-owned columns only) → insert `market_snapshots` keyed by 5-min bucket
   (idempotent) → one `tokenomics_snapshots` row per coin per day.
2. **refresh-history / refresh-fundamentals** (hourly, rotating batches): daily
   closes into `historical_prices`; developer metrics/genesis date onto `cryptocurrencies`.
3. **compute-scores** (hourly): deterministic scoring from persisted inputs only →
   `research_scores` (latest) and `research_score_snapshots` (deduplicated by
   `inputs_hash`).
4. **evaluate-alerts** (5 min): edge-triggered evaluation → `alert_evaluations`,
   `notifications` (unique `dedupe_key`), `notification_deliveries`.
5. **deliver-notifications** (1 min): `claim_due_deliveries` (FOR UPDATE SKIP
   LOCKED) → send → bounded exponential retry (max 5 attempts, ≤ 1 h backoff).
6. **cleanup** (daily): `run_retention_cleanup()`.

## Background jobs

`src/server/jobs/*`. Each run calls `acquire_job(name, key, lock)`. The key is
`job:<time-bucket>`, so a window runs successfully at most once. A failed window is
retried by the next invocation (attempts counted), and a live lock prevents
concurrent workers. Jobs never depend on a browser. Trigger them with
`POST /api/cron/<job>` (Bearer `CRON_SECRET`) or `npm run job -- <job>`.

## Research score (methodology v1.0.0)

`src/lib/research/scoring.ts`: pure and deterministic, with `asOf` passed in. It
computes six category subscores (0–100) with provisional weights from
`src/config/scoring.ts` or the active `scoring_configs` row. Missing categories are
excluded and the remaining weights renormalized. An overall score requires ≥ 60%
weight coverage plus the tokenomics and liquidity categories; otherwise the result
is `insufficient_data`. Stale (> 6 h) or invalid market inputs are excluded and
flagged. Each result records the methodology version, timestamps, flags, and a
SHA-256 `inputsHash`.

## Portfolio methodology

`src/lib/portfolio/calc.ts`. USD quote currency, weighted **average cost**:

- **buy:** quantity += q; cost basis += q × price + fee
- **sell:** requires q ≤ holdings at that time. Realized P&L += (q × price − fee) − avgCost × q
- **transfer_in:** like a buy at the user-declared cost basis per unit (0 allowed for airdrops)
- **transfer_out:** removes avgCost × q of basis without realizing P&L; the fee is a realized loss
- Transactions apply in (occurredAt, createdAt, id) order. Unrealized P&L = q × price − cost basis.
- Edits and deletes are rejected if they would make any later point in the ledger negative.

Transactions are user-entered and not verified on-chain. Figures are not tax advice.

## AI assistant

`src/server/ai.ts`:

1. Gather real retrieved data (market, supply, score, entitled article text).
2. Reserve quota atomically in SQL (`reserve_ai_request`, per UTC day by tier).
3. Build the prompt (`src/lib/ai/prompt.ts`). Data goes in `<data>` blocks and
   third-party text in `<untrusted_document>` blocks, with tags neutralized. The
   system rules forbid invented data and personalized advice.
4. Call the provider: `claude-opus-5-5` recommended, adaptive thinking at medium
   effort, server-side refusal fallback enabled.
5. Record usage metadata only (no prompt or answer text).

Responses show the exact sources and timestamps given to the model and are
labeled AI-generated.
