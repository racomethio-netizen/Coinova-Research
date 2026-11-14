"""Per-exchange capability declarations, verified against ccxt 4.4.80 source.

Anything marked "to verify" must be confirmed against the live API in Phase 3
before live trading on that exchange is allowed.
"""

from __future__ import annotations

from nexus_config import ExchangeCode

from nexus_exchange.models import Capabilities

CAPABILITIES: dict[ExchangeCode, Capabilities] = {
    ExchangeCode.BINANCE: Capabilities(
        exchange="binance",
        client_order_lookup="native",  # GET /api/v3/order?origClientOrderId=
        sandbox_available=True,        # Spot testnet (testnet.binance.vision)
        websocket_market_data=True,
        post_only=True,                # LIMIT_MAKER
        withdrawal_permission_check="api",  # GET /sapi/v1/account/apiRestrictions
        notes=(
            "Testnet balances and liquidity are synthetic; use only for adapter tests.",
        ),
    ),
    ExchangeCode.KRAKEN: Capabilities(
        exchange="kraken",
        # ccxt's kraken.fetch_order maps a client id to the legacy integer `userref`,
        # not the string `cl_ord_id` we submit, so we scan open + closed orders.
        client_order_lookup="scan",
        sandbox_available=False,       # Kraken has no public spot sandbox
        websocket_market_data=True,
        post_only=True,
        withdrawal_permission_check="manual",  # key permissions not readable via API
        notes=(
            "No spot sandbox: adapter is tested with recorded/fake clients and read-only "
            "live calls; first live orders must be minimum size under owner supervision.",
            "Native cl_ord_id query support to verify in Phase 3.",
        ),
    ),
    ExchangeCode.COINBASE: Capabilities(
        exchange="coinbase",
        client_order_lookup="scan",    # no get-by-client_order_id endpoint in ccxt
        sandbox_available=False,       # Advanced Trade sandbox returns static mocks only
        websocket_market_data=True,
        post_only=True,
        # /api/v3/brokerage/key_permissions exists but is not wrapped by ccxt 4.4.80;
        # native call to implement in Phase 3. Until then: manual confirmation.
        withdrawal_permission_check="manual",
        notes=("Coinbase lists BTC-USDT/ETH-USDT; liquidity may be lower than USD pairs.",),
    ),
}
