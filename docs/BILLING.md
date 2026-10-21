# Billing and memberships

## Plans and entitlements

Defined in `src/config/plans.ts` (prices are **illustrative**; review before launch):

| Plan | Tier | Price |
|---|---|---|
| Free | free | $0 |
| Premium — Monthly | premium | $19 / month |
| Premium — Yearly | premium | $160 / year |
| Founding Member — Yearly | founding | $300 / year |

Tier limits (`TIER_LIMITS`) are mirrored to `membership_plans.limits` and enforced
in SQL. After editing them, run the `sync-plans` job.

| Capability | Free | Premium | Founding |
|---|---|---|---|
| Public markets, free articles, sample scores | ✓ | ✓ | ✓ |
| Full scores, score history, premium articles, unlock details | — | ✓ | ✓ |
| Portfolios / alerts / CSV export | — | 5 / 50 / ✓ | 20 / 200 / ✓ |
| Watchlists (lists / coins each) | 1 / 10 | 10 / 100 | 25 / 250 |
| AI requests per day | 3 | 50 | 150 |

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

## Testing

- Unit: `tests/unit/billing.test.ts` (signatures, replay window, state machine, entitlements).
- Database: `tests/db/rls.test.ts` (users can't write subscriptions; tier function; grace; livemode).
- Integration: `tests/integration/billing.test.ts` (end-to-end webhook processing on a real database).
