# Billing and memberships

## Plans and entitlements

Defined in `src/config/plans.ts` (prices are **illustrative**; review before launch):

| Plan | Tier | Price |
|---|---|---|
| Free | free | $0 |
| Pro — Monthly / Yearly | pro | $9 / month, $79 / year |
| Premium — Monthly / Yearly | premium | $19 / month, $160 / year |

The legacy `founding` tier is retired; any legacy row is treated as premium.

Tier limits (`TIER_LIMITS`) are mirrored to `membership_plans.limits` and enforced
in SQL. Feature entitlements (`PLAN_FEATURES`) are mirrored to `plan_features` and
checked with `has_feature()` / `hasFeature()`. After editing either, run the `sync-plans` job.

| Limit | Free | Pro | Premium |
|---|---|---|---|
| Watchlists (lists / coins each) | 1 / 10 | 5 / 50 | 10 / 100 |
| Portfolios / alerts | 0 / 0 | 2 / 25 | 5 / 50 |
| AI requests per day | 3 | 20 | 50 |
| Backtests per day | 0 | 0 | 10 |

### Feature entitlements and where they are enforced

Every feature a plan lists is enforced on the server, and `tests/unit/entitlements.test.ts`
plus the "entitlement matrix" block in `tests/db/rls.test.ts` check each feature × tier.

| Feature | Min tier | Enforced in |
|---|---|---|
| `analytics.advanced` | Pro | `market_scanner` view returns NULL for MACD, ATR, volume ratio and higher-timeframe trend below Pro |
| `signals.history` | Pro | `can_view_signal` (live signals + full history; also needs gates G-1/G-2) |
| `signals.alerts` | Pro | `notify_signal_subscribers` trigger: opt-in (`profiles.signal_notifications`), public signals only |
| `performance.reports` | Pro | `GET /api/performance/export` (CSV of the signal ledger) |
| `models.analytics` | Premium | RLS on `validation_reports` (approved/retired models only) |
| `backtesting` | Premium | `request_backtest` + `/api/backtests`, quota `backtestsPerDay` |
| `strategies.custom` | Premium | `request_backtest` rejects non-default parameters without it |

Paper trading stays available to every signed-in user (virtual funds, no tier gate).
The pricing page never markets signals while `PAID_SIGNALS_ENABLED=false` (gate G-2).

### Mobile (decision D7)

The mobile app shows the current plan and the "included in Pro" notes only. It has no
purchase UI and no external purchase links. Upgrades happen on the web. Store billing
(IAP) would need a provider that supports it and a G-7 review; it is not built.

## Subscription lifecycle

The state machine lives in `src/lib/billing/state.ts`. SQL access rules live in
`subscription_grants_access`.

- `subscription.activated` / `renewed` / `reactivated` → `active` with a new period
- `subscription.payment_failed` → `past_due` (access continues until period end + grace days)
- `subscription.canceled` with `cancelAtPeriodEnd` → stays `active` until period end; otherwise `canceled` immediately
- `subscription.expired` → `expired`; `charge.refunded` → `refunded` (access ends immediately)
- Events older than the subscription's `last_event_at` are recorded and ignored.

## Webhook configuration

Endpoint: `POST /api/billing/webhook`. The configured provider adapter verifies
the raw body's signature. Processing steps:

1. Verify the signature, check livemode matches the provider, and validate the schema.
2. Insert into `billing_events` (unique provider + event ID). Duplicates return `{status: "duplicate"}`.
3. Apply the event to the subscription (optimistic concurrency) and record user-visible `billing_payments`.
4. Mark the event `processed`, `ignored` (with a reason), or `failed`. Failed or
   conflicting events return non-2xx so the provider retries.

## Development simulator

Set `BILLING_PROVIDER=dev_simulator` and `DEV_BILLING_WEBHOOK_SECRET` (≥ 32
characters), and run `supabase/seed.sql` on the **development** database so
test-mode subscriptions grant access. Then:

1. On `/pricing` choose a plan → `/dashboard/membership` → **Continue to checkout**.
2. The simulated checkout page sends a signed `subscription.activated` event through the real webhook path.
3. `/dashboard/membership` offers renewal, payment failure, immediate cancel,
   expiration, and refund buttons (simulator only).

The simulator can't run in production: env validation rejects it, its events are
`livemode=false`, and test-mode subscriptions grant nothing while
`require_livemode_subscriptions=true` (the migration default).

## Adding a real provider

1. Confirm eligibility (checklist below), then pick a provider.
2. Implement `BillingProvider` (`createCheckout`, `parseWebhook` with signature
   verification, `cancelSubscription`), mapping provider events to `NormalizedBillingEvent`.
   Include the user ID and plan ID as checkout metadata.
3. Put provider price IDs in `PAID_PLANS[].providerPriceIds`, add env variables,
   and register the adapter in `getBillingProvider`.
4. Write tests for signature verification, event mapping, duplicates, and out-of-order delivery.
5. Configure the provider's webhook to `https://<site>/api/billing/webhook`.
6. Test end to end in the provider's test mode on a staging environment
   (`require_livemode_subscriptions=false` on staging only).
7. Get owner approval before enabling live mode in production.

### Provider conformance suite

`tests/support/billing-conformance.ts` exports `describeBillingProviderConformance(name, makeHarness)`.
Every adapter must pass it. It checks that signed events are accepted and normalized,
that missing, wrong and tampered signatures and stale replays are rejected, that the
state machine applies the normalized events, and that checkout works only for active plans.
`tests/unit/billing-conformance.test.ts` runs it for `DevBillingSimulator`; add one
line there for the real adapter.

## Provider eligibility checklist (Ethiopia-based owner)

Do **not** assume any provider supports your business. Before choosing one, confirm:

- [ ] The provider supports merchants registered in, or operated from, your country of registration
- [ ] Payouts to your bank account and currency, and their fees and payout schedule
- [ ] Whether a merchant-of-record provider (which handles sales tax/VAT) is needed for international customers
- [ ] Recurring subscriptions, proration, cancellations, and refunds are supported
- [ ] Signed webhooks with event IDs and test mode
- [ ] Required business documents (registration, tax ID, beneficial ownership)
- [ ] Restrictions on financial-information or crypto-related businesses in their acceptable-use policy
- [ ] Consumer-protection requirements for auto-renewal disclosures in your customers' jurisdictions
- [ ] Refund, chargeback, and dispute policies
- [ ] Whether subscription research **signals** are allowed at all (decision D9), and in which customer countries
- [ ] Mobile: whether the provider supports App Store / Google Play billing, if in-app purchase is ever wanted (D7)

## Testing

- Unit: `tests/unit/billing.test.ts` (signatures, replay window, state machine, entitlements).
- Database: `tests/db/rls.test.ts` (users can't write subscriptions; tier function; grace; livemode).
- Integration: `tests/integration/billing.test.ts` (end-to-end webhook processing on a real database).
