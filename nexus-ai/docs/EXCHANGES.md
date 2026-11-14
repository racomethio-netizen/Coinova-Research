# Exchange support (honest status)

Verified against the ccxt source installed in this repo (4.4.80, re-checked on 4.5.91). Items marked
**to verify** must be confirmed against the live API in Phase 3 before live trading on
that exchange is permitted.

| Capability | Binance | Kraken | Coinbase Advanced Trade |
|---|---|---|---|
| Spot BTC/USDT, ETH/USDT | Yes | Yes | Yes (BTC-USDT/ETH-USDT; thinner than USD books) |
| Submit with our client order ID | `newClientOrderId` | `cl_ord_id` | `client_order_id` |
| Look up order by client ID | **Native** (`origClientOrderId`) | **Scan** fallback¹ | **Scan** fallback² |
| Sandbox / testnet | Spot testnet | **None** | Static mock sandbox only (not useful) |
| Verify withdrawals disabled via API | `GET /sapi/v1/account/apiRestrictions` | **Not possible** → manual owner confirmation | `GET /api/v3/brokerage/key_permissions` exists but not wrapped by ccxt → native call in Phase 3; manual until then |
| Post-only limit orders | Yes (LIMIT_MAKER) | Yes | Yes |
| Public WebSocket market data | Yes | Yes | Yes |
| Account fee tier | Via account endpoints (Phase 3) | Via `TradeVolume` (Phase 3) | Via `transaction_summary` (Phase 3) |

¹ ccxt's `kraken.fetch_order` maps a client ID to the legacy integer `userref`, not the
string `cl_ord_id` we submit. The adapter therefore scans open orders, then closed
orders from the last 48 h, matching `clientOrderId` (which ccxt parses from
`cl_ord_id`). A native `cl_ord_id` query is **to verify**.

² No get-by-client-order-id endpoint is wrapped; same scan strategy.

Fallback guarantees: if the scan itself fails, the adapter **raises** (never returns
"not found"), so the execution engine keeps the order `unknown` and blocks new orders
on that exchange until it is resolved.

## Fees

ccxt's market `maker`/`taker` values are public base-tier fees. Phase 3 replaces them
with the account's actual tier where the exchange exposes it; until then, paper trading
uses the configured `NEXUS_PAPER_FEE_BPS` (default 10 bps), which is at or above the
base tier on all three exchanges.

## Precision

Markets without explicit price/amount precision are **excluded** (listed in
`adapter.excluded_markets`) rather than guessed. Amounts round down; buy prices round
down; sell prices round up. `submit_order` re-validates and refuses unrounded values.
