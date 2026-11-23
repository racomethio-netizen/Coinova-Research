"""Exchange capability registry: what each exchange ACTUALLY offers, detected live from its
public API through CCXT (no API keys, no orders), next to what NEXUS has built for it.

For every exchange it reports:
* the active spot USDT markets and USDT-margined perpetual swaps;
* a sample contract specification (BTC): precision, minimum size and cost, fees, contract
  size, and the maximum leverage when the public API publishes it;
* CCXT's capability flags: order types, stop / take-profit / reduce-only orders, leverage
  and margin-mode setters, funding and open interest, WebSocket feeds;
* sandbox availability and the documented rate limit.

Nothing is assumed: a flag CCXT reports as unknown stays ``None``. Leverage brackets that
only exist behind authenticated endpoints are reported as such. A failure (geo-block, IP
ban, timeout) is recorded as the exchange's error, never filled in.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

EXCHANGES = ("binance", "okx", "bybit", "kraken")

# What NEXUS itself has implemented and tested (independent of the exchange's offer).
NEXUS_SUPPORT: dict[str, dict[str, Any]] = {
    "binance": {"spot_adapter": True, "futures_adapter": True, "live_spot_gate": True,
                "note": "spot: paper and gated live; USD-M futures: paper only (disabled)"},
    "kraken": {"spot_adapter": True, "futures_adapter": False, "live_spot_gate": True,
               "note": "spot adapter only; no NEXUS futures adapter"},
    "okx": {"spot_adapter": False, "futures_adapter": False, "live_spot_gate": False,
            "note": "detected only; no NEXUS trading adapter yet"},
    "bybit": {"spot_adapter": False, "futures_adapter": False, "live_spot_gate": False,
              "note": "detected only; no NEXUS trading adapter yet"},
}
# Spot "max_leverage" values come from margin markets; NEXUS never uses leverage on spot.

FLAGS = {
    "market_order": "createMarketOrder", "limit_order": "createLimitOrder",
    "post_only": "createPostOnlyOrder", "stop_loss_order": "createStopLossOrder",
    "take_profit_order": "createTakeProfitOrder", "stop_order": "createStopOrder",
    "trigger_order": "createTriggerOrder", "reduce_only": "createReduceOnlyOrder",
    "set_leverage": "setLeverage", "set_margin_mode": "setMarginMode",
    "fetch_positions": "fetchPositions", "funding_rate": "fetchFundingRate",
    "open_interest": "fetchOpenInterest", "fetch_order_by_client_id": "fetchOrder",
    "cancel_all": "cancelAllOrders", "fetch_time": "fetchTime",
}
WS_FLAGS = {"ws_order_book": "watchOrderBook", "ws_trades": "watchTrades",
            "ws_ohlcv": "watchOHLCV", "ws_orders": "watchOrders", "ws_balance": "watchBalance"}


def _flag(has: dict[str, Any], name: str) -> bool | str | None:
    v = has.get(name)
    return v if v in (True, False, "emulated") else None


def _spec(m: dict[str, Any] | None) -> dict[str, Any] | None:
    if not m:
        return None
    lim = m.get("limits") or {}
    return {"symbol": m.get("symbol"), "contract_size": m.get("contractSize"),
            "price_precision": (m.get("precision") or {}).get("price"),
            "amount_precision": (m.get("precision") or {}).get("amount"),
            "min_amount": (lim.get("amount") or {}).get("min"),
            "min_cost": (lim.get("cost") or {}).get("min"),
            "max_leverage": (lim.get("leverage") or {}).get("max"),
            "maker": m.get("maker"), "taker": m.get("taker")}


def summarise(exchange_id: str, markets: dict[str, dict[str, Any]], has: dict[str, Any],
              ws_has: dict[str, Any], urls: dict[str, Any], rate_limit_ms: float | None
              ) -> dict[str, Any]:
    """Pure summary of a loaded CCXT exchange (unit-testable without the network)."""
    active = [m for m in markets.values() if m.get("active") is not False]
    spot = [m for m in active if m.get("spot") and m.get("quote") == "USDT"]
    perps = [m for m in active if m.get("swap") and m.get("linear")
             and m.get("settle") == "USDT"]
    spot_btc = next((m for m in spot if m.get("base") == "BTC"), None)
    perp_btc = next((m for m in perps if m.get("base") == "BTC"), None)
    max_lev = _spec(perp_btc)["max_leverage"] if perp_btc else None   # type: ignore[index]
    return {
        "exchange": exchange_id,
        "spot": {"usdt_markets": len(spot), "sample": _spec(spot_btc)},
        "usdt_perpetual": {
            "markets": len(perps), "sample": _spec(perp_btc),
            "leverage_limits": ("published" if max_lev else
                                "not public: needs an authenticated bracket query")
            if perps else None},
        "capabilities": {k: _flag(has, v) for k, v in FLAGS.items()},
        "websocket": {k: _flag(ws_has, v) for k, v in WS_FLAGS.items()},
        "margin_modes": ["isolated", "cross"] if _flag(has, "setMarginMode") else None,
        "sandbox": bool(urls.get("test")),
        "rate_limit_ms": rate_limit_ms,
        "nexus": NEXUS_SUPPORT.get(exchange_id, {}),
    }


def detect(exchange_id: str, *, proxy_url: str | None = None, ca_bundle: str | None = None,
           timeout_ms: int = 20_000, futures: bool = True) -> dict[str, Any]:
    """Load one exchange's public markets and summarise them. Never raises: errors are
    returned in the result. Blocking (run it in a thread)."""
    import ccxt
    import ccxt.pro

    now = datetime.now(UTC).isoformat()
    opts: dict[str, Any] = {"enableRateLimit": True, "timeout": timeout_ms}
    if exchange_id == "binance":
        # Spot and USD-M only: coin-margined (dapi) markets are not used by NEXUS.
        opts["options"] = {"fetchMarkets": ["spot", "linear"] if futures else ["spot"]}
    if proxy_url:
        opts["httpsProxy"] = proxy_url
    if ca_bundle:                          # TLS-inspecting proxy (settings.exchange_ca_bundle)
        import requests

        session = requests.Session()
        session.verify = ca_bundle
        opts["session"] = session
    ex = getattr(ccxt, exchange_id)(opts)
    try:
        markets = ex.load_markets()
        ws_has = dict(getattr(ccxt.pro, exchange_id)().has) if hasattr(ccxt.pro, exchange_id) \
            else {}
        out = summarise(exchange_id, markets, dict(ex.has), ws_has, dict(ex.urls),
                        float(ex.rateLimit) if ex.rateLimit else None)
        out.update(ok=True, checked_at=now, ccxt_version=ccxt.__version__)
        return out
    except Exception as exc:                     # geo-block, ban, timeout: report, never guess
        return {"exchange": exchange_id, "ok": False, "checked_at": now,
                "error": f"{type(exc).__name__}: {str(exc)[:240]}",
                "nexus": NEXUS_SUPPORT.get(exchange_id, {})}
