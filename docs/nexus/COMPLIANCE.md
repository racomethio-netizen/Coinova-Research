# Compliance gates, kill switches, and claims policy

> This is an engineering control document, **not legal advice**. Signals,
> subscriptions for signals, and automated trading are regulated activities in many
> jurisdictions (for example: investment-advice, financial-promotion and
> crypto-asset rules). Qualified counsel must review before each gate opens.

## 1. Gates

| Gate | Default | Blocks | Opened by |
|---|---|---|---|
| G-1 `SIGNALS_VISIBILITY` | `staff` | Signals visible only to staff (internal research/shadow) | Owner deploy after legal review → `beta` (invited users) → `public` |
| G-2 `PAID_SIGNALS_ENABLED` | `false` | Signal features excluded from paid plans and from the pricing page | Owner deploy after legal approval for paid signals in the target markets |
| G-3 `LIVE_EXECUTION_ENABLED` | `false` | Any order placement (none exists in code) | Only after §3 is complete **and** new reviewed code exists |
| G-4 Market-data terms | not reviewed | Showing exchange-sourced data to paying users | Owner records the ToS review (source, date, permitted use, regions) in `market_sources.terms_reviewed_at` |
| G-5 Live billing | `BILLING_PROVIDER=none` | Taking money | Owner selects a provider, completes eligibility (docs/BILLING.md) and approves |
| G-6 Geography | all blocked for paid features until reviewed | Paid features where not authorized | Allow-list of countries maintained by the owner (enforced server-side) |
| G-7 Mobile store policies | not reviewed | Store submission | Review the current App Store / Play policies on crypto apps and in-app purchase of subscriptions before Phase 8 release |

Gates G-1 to G-3 are environment variables (ADR-008), so they cannot be flipped
from the database or the admin UI. Every change is a deploy, recorded in the release notes.

## 2. Hard prohibitions (initial scope)
- No exchange account connections, API keys or credential storage.
- No custody, deposits, withdrawals or transfers of any asset.
- No live order endpoints.
- Paper trading uses virtual balances only, labeled as such everywhere.

## 3. Activation checklist for future automated execution
All required, documented and signed off in the repository before any code is written:
1. Applicable regulatory authorization in each target jurisdiction.
2. Legal review of terms, disclosures and the activation flow.
3. Written exchange permission / API agreement compatible with the use.
4. Independent security audit of the execution design and implementation.
5. Explicit, revocable per-user consent with risk disclosures.
6. Strict risk controls: per-user limits, global kill switch, order-size caps,
   anomaly halts, and a withdrawal-disabled key policy enforced and verified.

## 4. Claims policy (applies to UI, marketing, app store listings, emails)
- **Never** state or imply guaranteed accuracy or returns ("90% accurate", "sure
  win", "risk-free").
- **Never** fabricate, round up, cherry-pick or backfill statistics. Every displayed
  figure is computed from stored records, with **sample size, period, data source,
  and assumptions** (fees, slippage) next to it.
- Backtest results and live (forward) results are always labeled and shown separately.
  Hypothetical results carry a hypothetical-performance disclaimer.
- Aggregates under the minimum sample (SIGNAL_ENGINE.md §6) are shown as
  "insufficient sample", without a headline percentage.
- Confidence is a "calibrated model estimate", never a "probability of winning".
- An accuracy figure of 80–90% may appear only if it comes from independently reproducible,
  sufficiently large out-of-sample results, and only with the context above. Even then, it is not used as a headline promise.
- Demo or mock data is always labeled (existing mock banner rule).
- Standard risk disclosure on signal, performance, pricing and paper-trading pages:
  not financial advice, past performance does not indicate future results,
  crypto assets are volatile and you can lose money.

## 5. Records kept for compliance
All published signals and outcomes (indefinitely), annotations/retractions,
model approvals with validation reports, gate changes (release notes + audit log),
billing records (per docs/BILLING.md), and data deletion/export requests.
