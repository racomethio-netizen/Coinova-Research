"""Live market intelligence for the dashboard (analysis only; places no orders).

Data: Binance's public spot market data (klines and order book) from the configured public
API (data-api.binance.vision by default). Only CLOSED candles are used, so every figure is
reproducible. Futures funding rate and open interest come from Binance's public USD-M
futures API (fapi.binance.com); when it cannot be reached (e.g. HTTP 451 from some server
locations) they are reported as unavailable with the reason, never estimated.

The decision uses the same scoring and NO TRADE rules as the trader (nexus_decision) for a
hypothetical long and short at the last price, with the stop at 2 x ATR(1h) and the target
at 2R. Execution additionally requires a strategy that passed out-of-sample validation.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pandas as pd
from nexus_analysis import (
    liquidity_sweeps,
    market_structure,
    rolling_correlation,
    supply_demand_zones,
    support_resistance,
)
from nexus_decision import MarketSnapshot, NoTradeInputs, decide
from nexus_exchange.models import OrderBook
from nexus_market.analytics import book_metrics
from nexus_strategies import indicators as ind
from nexus_strategies.regime import classify

TIMEFRAMES = ("15m", "1h", "4h", "1d")
CACHE_SECONDS = 30
MIN_SCORE = 60
FUTURES_UNAVAILABLE = "futures data not fetched; not estimated"
FAPI = "https://fapi.binance.com/fapi/v1"


async def _get(client: httpx.AsyncClient, redis: Any, url: str,
               params: dict[str, Any]) -> httpx.Response:
    """Binance public GET through the shared back-off (no calls while paused)."""
    from nexus_common.binance_guard import BinanceBackoff, guarded_get

    try:
        r: httpx.Response = await guarded_get(client, redis, url, params, cache_ttl=30)
    except BinanceBackoff as exc:
        raise httpx.HTTPError(str(exc)) from exc
    return r


async def fetch_derivatives(client: httpx.AsyncClient, symbol: str,
                            redis: Any = None) -> dict[str, Any]:
    """Last funding rate and open interest (contracts) from Binance's public futures API."""
    native = symbol.replace("/", "")
    try:
        p = await _get(client, redis, f"{FAPI}/premiumIndex", {"symbol": native})
        p.raise_for_status()
        oi = await _get(client, redis, f"{FAPI}/openInterest", {"symbol": native})
        oi.raise_for_status()
        return {"funding_rate": _f(p.json().get("lastFundingRate"), 8),
                "open_interest": _f(oi.json().get("openInterest"), 3),
                "futures_data_note": "Binance USD-M perpetual (fapi.binance.com)"}
    except httpx.HTTPError as exc:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        why = f"HTTP {code}" if code else (str(exc)[:120] or type(exc).__name__)
        if code == 451:
            why += ": Binance refuses this server's location"
        return {"funding_rate": None, "open_interest": None,
                "futures_data_note": f"futures data unavailable ({why}); not estimated"}


async def stored_or_fetched_derivatives(redis: Any, client: httpx.AsyncClient,
                                        symbol: str) -> dict[str, Any]:
    """Prefer the derivatives worker's snapshot (OI history, basis, ratios,
    liquidations); fall back to a direct funding/OI fetch."""
    raw = await redis.get(f"nexus:derivatives:{symbol.replace('/', '')}")
    snap = json.loads(raw) if raw else None
    if snap and not snap.get("error"):
        keep = ("funding_rate", "open_interest", "open_interest_value", "oi_change_1h",
                "oi_change_24h", "basis", "mark_price", "index_price",
                "accounts_long_short_ratio", "accounts_long_short_ratio_24h_ago",
                "top_traders_position_ratio", "taker_buy_sell_ratio", "liquidations")
        return {**{k: snap.get(k) for k in keep},
                "futures_data_note": f"Binance USD-M public data, {snap.get('as_of', '')[:16]} UTC"}
    out = await fetch_derivatives(client, symbol, redis)
    if snap and snap.get("error"):
        out["futures_data_note"] += f"; derivatives worker: {snap['error'][:120]}"
    if snap and snap.get("liquidations"):
        out["liquidations"] = snap["liquidations"]
    return out


def _f(x: Any, digits: int = 6) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else round(v, digits)


def event_or_none(value: Any) -> str | None:
    """Structure events are NaN/None when nothing happened; never report them as 'nan'."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return str(value)


def klines_frame(rows: list[list[Any]], now: datetime) -> pd.DataFrame:
    """Binance kline rows -> float frame of CLOSED bars indexed by open time (UTC)."""
    now_ms = now.timestamp() * 1000
    closed = [r for r in rows if int(r[6]) < now_ms]
    idx = pd.DatetimeIndex([datetime.fromtimestamp(int(r[0]) / 1000, tz=UTC) for r in closed],
                           name="open_time")
    return pd.DataFrame({k: [float(r[i]) for r in closed] for k, i in
                         (("open", 1), ("high", 2), ("low", 3), ("close", 4), ("volume", 5))},
                        index=idx)


async def fetch(client: httpx.AsyncClient, base: str, symbol: str,
                now: datetime, redis: Any = None) -> tuple[dict[str, pd.DataFrame], OrderBook]:
    native = symbol.replace("/", "")
    frames: dict[str, pd.DataFrame] = {}
    for tf in ("5m", *TIMEFRAMES):
        r = await _get(client, redis, f"{base}/klines",
                       {"symbol": native, "interval": tf, "limit": 1000})
        r.raise_for_status()
        frames[tf] = klines_frame(r.json(), now)
    r = await _get(client, redis, f"{base}/depth", {"symbol": native, "limit": 100})
    r.raise_for_status()
    d = r.json()
    book = OrderBook(symbol=symbol,
                     bids=tuple((Decimal(p), Decimal(q)) for p, q in d["bids"]),
                     asks=tuple((Decimal(p), Decimal(q)) for p, q in d["asks"]),
                     timestamp=now)
    return frames, book


def timeframe_view(df: pd.DataFrame) -> dict[str, Any]:
    if len(df) < 210:
        return {"bars": len(df), "error": "not enough closed bars"}
    close = df["close"]
    st = market_structure(df).iloc[-1]
    macd = ind.macd(close).iloc[-1]
    bb = ind.bollinger(close).iloc[-1]
    last = float(close.iloc[-1])
    ema = {n: float(ind.ema(close, n).iloc[-1]) for n in (20, 50, 200)}
    width = float(bb.iloc[2] - bb.iloc[1]) if len(bb) >= 3 else float("nan")
    pct_b = (last - float(bb.iloc[1])) / width if width and not math.isnan(width) else None
    return {
        "bars": len(df), "as_of": str(df.index[-1]), "close": _f(last, 8),
        "trend": str(st["trend"]), "structure_event": event_or_none(st["event"]),
        "rsi": _f(ind.rsi(close).iloc[-1], 2),
        "macd": _f(macd.iloc[0], 6), "macd_signal": _f(macd.iloc[1], 6),
        "macd_hist": _f(macd.iloc[2], 6),
        "ema20": _f(ema[20], 8), "ema50": _f(ema[50], 8), "ema200": _f(ema[200], 8),
        "adx": _f(ind.adx(df).iloc[-1], 2), "atr": _f(ind.atr(df).iloc[-1], 8),
        "atr_pct": _f(ind.atr(df).iloc[-1] / last, 6),
        "bollinger_pct_b": _f(pct_b, 4), "volume_z": _f(ind.volume_z(df["volume"]).iloc[-1], 2),
    }


def _hypothesis(side: str, last: float, atr: float, h_trend: str, base_event: str | None,
                at_level: bool, sweep: bool, vol_z: float | None, imbalance: float | None,
                btc_corr: float | None, btc_trend: str | None, inputs: NoTradeInputs) -> dict[str, Any]:
    long = side == "long"
    entry, unit = Decimal(repr(last)), Decimal(repr(atr)) * 2  # stop 2 x ATR, target 2R
    stop = entry - unit if long else entry + unit
    target = entry + 2 * unit if long else entry - 2 * unit
    snap = MarketSnapshot(side=side, entry=entry, stop=stop,
                          target=target, higher_trend=h_trend,
                          structure_event=base_event, at_level=at_level, sweep=sweep,
                          volume_z=vol_z, book_imbalance=imbalance, btc_corr=btc_corr,
                          btc_trend=btc_trend)
    d = decide(snap, inputs, min_score=MIN_SCORE)
    tier = ("strong" if d.score >= 75 else "moderate" if d.score >= MIN_SCORE
            else "weak" if d.score >= 40 else "none")
    return {"side": side, "spot_action": "BUY" if long else "SELL (exit only; spot cannot short)",
            "futures_action": "LONG" if long else "SHORT", "entry": _f(last, 8),
            "stop": _f(stop, 8), "target": _f(target, 8), "reward_risk": _f(d.reward_risk, 2),
            "score": d.score, "strength": tier, "decision": d.action,
            "reasons_for": list(d.earned), "reasons_against": list(d.missed),
            "no_trade_reasons": list(d.reasons)}


def analyze(symbol: str, frames: dict[str, pd.DataFrame], book: OrderBook,
            btc_frames: dict[str, pd.DataFrame] | None, *, now: datetime,
            news_risk: float | None, kill_switch: bool, order_quote: Decimal,
            derivatives: dict[str, Any] | None = None) -> dict[str, Any]:
    h1, h4, m5 = frames["1h"], frames["4h"], frames["5m"]
    if len(h1) < 210 or len(m5) < 300:
        return {"symbol": symbol, "error": "not enough closed candles for analysis"}
    last = float(m5["close"].iloc[-1])
    atr_1h = float(ind.atr(h1).iloc[-1])
    sr = support_resistance(h1).iloc[-1]
    sup, res = _f(sr["support"], 8), _f(sr["resistance"], 8)
    zones = supply_demand_zones(h1)
    recent_zones = [{k: (None if pd.isna(v) else _f(v, 8) if isinstance(v, float) else str(v))
                     for k, v in z.items()} for z in zones.tail(4).to_dict("records")]
    sw = liquidity_sweeps(h1).tail(12)
    sweep_low, sweep_high = bool(sw["sweep_low"].any()), bool(sw["sweep_high"].any())
    tail = m5.tail(288)
    vwap = float((((tail.high + tail.low + tail.close) / 3) * tail.volume).sum() / tail.volume.sum())
    metrics = book_metrics(book)
    regime = classify(h1).iloc[-1]
    atr_rank = ind.rolling_percentile(ind.atr(h1), 2000, min_periods=200).iloc[-1]
    btc_corr = btc_trend = None
    if btc_frames is not None:
        corr = rolling_correlation(h1["close"], btc_frames["1h"]["close"], 96).iloc[-1]
        btc_corr = _f(corr, 3)
        btc_trend = str(market_structure(btc_frames["1h"]).iloc[-1]["trend"])
    h_trend = str(market_structure(h4).iloc[-1]["trend"])
    base_event = event_or_none(market_structure(h1).iloc[-1]["event"])
    vol_z = _f(ind.volume_z(h1["volume"]).iloc[-1], 2)
    inputs = NoTradeInputs(
        now=now, data_time=book.timestamp, exchange_healthy=True,
        spread_bps=metrics.spread_bps if metrics else None,
        depth_quote=metrics.ask_depth_quote if metrics else None, order_quote=order_quote,
        atr_percentile=_f(atr_rank, 3), news_risk=news_risk,
        daily_loss_limit_reached=False, weekly_loss_limit_reached=False,
        kill_switch=kill_switch, reward_risk=None, confirmations_conflict=False,
        regime=None if regime is None else str(regime), setup_present=False)
    imbalance = metrics.imbalance if metrics else None
    hyps = [_hypothesis(side, last, atr_1h, h_trend, base_event,
                        at_level=(sup is not None and abs(last - sup) <= 0.5 * atr_1h) if side == "long"
                        else (res is not None and abs(res - last) <= 0.5 * atr_1h),
                        sweep=sweep_low if side == "long" else sweep_high, vol_z=vol_z,
                        imbalance=imbalance, btc_corr=btc_corr, btc_trend=btc_trend, inputs=inputs)
            for side in ("long", "short")]
    best = max(hyps, key=lambda x: x["score"])
    return {
        "symbol": symbol, "as_of": now.isoformat(), "price": _f(last, 8),
        "change_24h": _f(last / float(h1["close"].iloc[-25]) - 1, 5),
        "regime": None if regime is None else str(regime),
        "atr_percentile": _f(atr_rank, 3), "vwap_24h": _f(vwap, 8),
        "support": sup, "resistance": res, "zones": recent_zones,
        "liquidity_sweep_low_12h": sweep_low, "liquidity_sweep_high_12h": sweep_high,
        "btc_correlation_1h": btc_corr, "btc_trend_1h": btc_trend,
        "order_book": None if metrics is None else {
            "spread_bps": _f(metrics.spread_bps, 3), "imbalance": _f(metrics.imbalance, 3),
            "bid_depth_quote": _f(metrics.bid_depth_quote, 2),
            "ask_depth_quote": _f(metrics.ask_depth_quote, 2)},
        **(derivatives or {"funding_rate": None, "open_interest": None,
                           "futures_data_note": FUTURES_UNAVAILABLE}),
        "timeframes": {tf: timeframe_view(frames[tf]) for tf in TIMEFRAMES},
        "hypotheses": hyps, "direction": best["side"] if best["score"] >= 40 else None,
    }


async def market_intelligence(pairs: list[str], base: str, *, redis: Any, news_risk: float | None,
                              kill_switch: bool, order_quote: Decimal) -> dict[str, Any]:
    cached = await redis.get("nexus:intelligence")
    if cached:
        return {**json.loads(cached), "cached": True}
    now = datetime.now(UTC)
    results: list[dict[str, Any]] = []
    from nexus_common.proxy import httpx_kwargs

    async with httpx.AsyncClient(timeout=15, trust_env=True, **httpx_kwargs()) as client:
        btc: dict[str, pd.DataFrame] | None = None
        for pair in sorted(pairs, key=lambda p: p != "BTC/USDT"):
            try:
                frames, book = await fetch(client, base, pair, now, redis)
            except httpx.HTTPError as exc:
                code = getattr(getattr(exc, "response", None), "status_code", None)
                results.append({"symbol": pair, "error": f"market data unavailable "
                                f"({type(exc).__name__}{f' HTTP {code}' if code else ''})"})
                continue
            if pair == "BTC/USDT":
                btc = frames
            results.append(analyze(pair, frames, book, None if pair == "BTC/USDT" else btc,
                                   now=now, news_risk=news_risk, kill_switch=kill_switch,
                                   order_quote=order_quote,
                                   derivatives=await stored_or_fetched_derivatives(
                                       redis, client, pair)))
    payload = {"generated_at": now.isoformat(), "source": base, "min_score": MIN_SCORE,
               "pairs": results}
    await redis.set("nexus:intelligence", json.dumps(payload, default=str), ex=CACHE_SECONDS)
    return {**payload, "cached": False}
