# Market data

## Providers

| `MARKET_DATA_PROVIDER` | Use | Notes |
|---|---|---|
| `mock` | Development and tests only | 30 fictional "(Mock)" assets with deterministic values. A global banner and "Mock data" badges are always shown. **Refused when `APP_ENV=production`.** |
| `coingecko` | Production | CoinGecko API v3 via `src/lib/market/coingecko.ts` |

### CoinGecko configuration

| Plan (`COINGECKO_API_PLAN`) | Base URL | Auth header |
|---|---|---|
| `demo` (free key) | `https://api.coingecko.com/api/v3` | `x-cg-demo-api-key` |
| `pro` (paid) | `https://pro-api.coingecko.com/api/v3` | `x-cg-pro-api-key` |
| `public` (keyless) | `https://api.coingecko.com/api/v3` | none; heavily rate limited |

Keys are sent as headers, never as query strings. Endpoints used:
`/coins/markets` (bulk, ≤ 250 IDs per call), `/coins/{id}`,
`/coins/{id}/market_chart`, and `/global`.

**Verification status:** the adapter is unit-tested against representative
responses, including validation failures, 429, 5xx, timeouts, and malformed JSON.
It has **not been exercised against the live API**, because the build environment
could not reach api.coingecko.com. Before launch, run with a demo key
(`MARKET_DATA_PROVIDER=coingecko`), load `/markets` and a coin page, and trigger
`refresh-markets` once. The job logs `universe_ids_not_returned_by_provider` for
any configured ID CoinGecko doesn't recognize; fix those in `src/config/coins.ts`.

## Rate limits and call budget

Exact limits depend on your CoinGecko plan; check the dashboard. Demo plans are
commonly documented at about 30 calls/min with a monthly cap. Our usage:

| Source | Calls |
|---|---|
| refresh-markets (every 5 min) | 1 per 250 coins → ~8,640/month for 100 coins |
| Page views | cached: ≤ 1 bulk call/min/instance, details ≤ 1 per coin per 5 min, history ≤ 1 per coin/period per 30 min |
| refresh-history + refresh-fundamentals (hourly batches of 25 + 20) | with `HISTORY_REFRESH_DELAY_MS=2500` → ≤ ~24 calls/min while running |

Retries: up to 3, with exponential backoff (500 ms → 8 s) honoring `Retry-After`,
only for 429/5xx/timeouts/network errors. On 429 the batch jobs stop early, and
the next run continues where they left off.

## Freshness and attribution

- Every page shows the data source and the time it was **retrieved**. Coin pages
  also show the provider's own `last_updated`.
- If a refresh fails, cached data younger than the max stale age is served with a
  **"Stale — provider unavailable"** badge. Older data isn't shown, and the page
  says the data is unavailable. Prices are never substituted.
- Alerts ignore observations older than 30 minutes. Scores treat market inputs
  older than 6 hours as unreliable.
- Attribution: data is credited to CoinGecko in the UI. Review CoinGecko's
  attribution and licensing terms for your plan before launch. Charts use
  TradingView Lightweight Charts™ (Apache-2.0), with the attribution logo kept as required.

## Coverage

About 100 coins are configured in `src/config/coins.ts`. Stablecoins are listed
but not scored. Expand the list only after measuring API usage and job duration.
Coverage depends on what the provider reports; missing fields display as "—".

## Token unlocks

No unlock data provider is integrated (`TOKEN_UNLOCK_PROVIDER=none`). The UI says
so and never estimates schedules. Staff can record events under `/admin/coins`
**only with an https source URL**, provenance (project-reported or provider), and
a confidence level. To add a provider, implement `TokenUnlockProvider` in
`src/lib/research/unlocks.ts`, add its env option, validate its responses with Zod,
and add tests before enabling it.
