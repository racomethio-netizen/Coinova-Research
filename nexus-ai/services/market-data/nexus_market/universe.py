"""Dynamic market universe: discover, filter and rank Binance Spot and USD-M Futures
symbols for grid trading. Every selection, rejection and score is recorded.

Components (each is a small class so it can be tested alone):
* ``SymbolDiscoveryService``: tradable USDT symbols and their exchange filters, from
  ``exchangeInfo`` (spot: status TRADING and spot trading allowed; futures: PERPETUAL,
  USDT-margined, status TRADING).
* ``LiquidityAnalyzer``: 24h quote volume and trade count, top-of-book depth.
* ``SpreadAnalyzer``: bid-ask spread in basis points from the book ticker.
* ``VolatilityAnalyzer``: hourly ATR% (14), realised volatility, largest hourly move, from
  CLOSED 1h candles.
* ``UniverseRiskFilter``: hard exclusions (each with a reason):
  * inactive markets, leveraged tokens and stablecoin or wrapped-duplicate bases;
  * thin volume, wide spreads, recent listings and failing exchange filters;
  * abnormal moves and poor data quality.
* ``SymbolRankingEngine``: a weighted score. High volatility is NOT rewarded: volatility
  scores 1 inside a target band and is penalised above it, because a grid in a fast market
  only buys a falling knife or sells a rally too early.
* ``DynamicUniverseEngine``: two-stage scan. The cheap stage uses whole-market tickers for
  every symbol; the expensive stage fetches klines only for the most liquid candidates.
  Spot and futures follow separate rules. Selection uses hysteresis: a selected symbol
  keeps its slot while it stays eligible and ranks within 2x the slot count, so the
  universe does not churn and grids are not rebuilt for noise.

Requests go through ``binance_guard.guarded_get`` (central weight budget, circuit breaker,
shared cache). A failed request aborts the scan and the previous universe stays: nothing
is guessed.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from typing import Any

SPOT_API = "https://api.binance.com/api/v3"
FUTURES_API = "https://fapi.binance.com/fapi/v1"

STABLE_OR_WRAPPED = frozenset({
    "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "PYUSD", "USDE", "USD1", "RLUSD", "BFUSD",
    "EUR", "EURI", "AEUR", "XUSD", "UST", "USTC", "PAXG", "WBTC", "WBETH", "BETH", "BNSOL"})
LEVERAGED_SUFFIX = re.compile(r"^(?P<root>[A-Z0-9]{2,})(UP|DOWN|BULL|BEAR)$")


@dataclass(frozen=True)
class SymbolInfo:
    market: str                     # "spot" or "futures"
    symbol: str                     # exchange id, e.g. BTCUSDT
    base: str
    quote: str
    status: str
    price_tick: float
    amount_step: float
    min_qty: float
    min_notional: float
    onboard_ms: int | None = None   # futures listing time

    @property
    def ccxt(self) -> str:
        return f"{self.base}/{self.quote}" + (":USDT" if self.market == "futures" else "")

    def to_json(self) -> dict[str, Any]:
        return {"market": self.market, "symbol": self.symbol, "pair": self.ccxt,
                "base": self.base, "price_tick": self.price_tick,
                "amount_step": self.amount_step, "min_qty": self.min_qty,
                "min_notional": self.min_notional}


def _filters(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f.get("filterType", ""): f for f in raw.get("filters", [])}


class SymbolDiscoveryService:
    @staticmethod
    def parse_spot(info: dict[str, Any], quote: str = "USDT") -> list[SymbolInfo]:
        out = []
        for s in info.get("symbols", []):
            if s.get("quoteAsset") != quote:
                continue
            f = _filters(s)
            notional = f.get("NOTIONAL", f.get("MIN_NOTIONAL", {}))
            status = s.get("status", "") if s.get("isSpotTradingAllowed", True) \
                else "SPOT_TRADING_DISABLED"
            out.append(SymbolInfo(
                "spot", s["symbol"], s.get("baseAsset", ""), quote, status,
                float(f.get("PRICE_FILTER", {}).get("tickSize", 0) or 0),
                float(f.get("LOT_SIZE", {}).get("stepSize", 0) or 0),
                float(f.get("LOT_SIZE", {}).get("minQty", 0) or 0),
                float(notional.get("minNotional", 0) or 0)))
        return out

    @staticmethod
    def parse_futures(info: dict[str, Any], quote: str = "USDT") -> list[SymbolInfo]:
        out = []
        for s in info.get("symbols", []):
            if s.get("quoteAsset") != quote or s.get("marginAsset", quote) != quote:
                continue
            f = _filters(s)
            status = s.get("status", "")
            if s.get("contractType") != "PERPETUAL":
                status = f"NOT_PERPETUAL:{s.get('contractType')}"
            elif s.get("underlyingType", "COIN") != "COIN":
                status = f"UNDERLYING:{s.get('underlyingType')}"
            out.append(SymbolInfo(
                "futures", s["symbol"], s.get("baseAsset", ""), quote, status,
                float(f.get("PRICE_FILTER", {}).get("tickSize", 0) or 0),
                float(f.get("LOT_SIZE", {}).get("stepSize", 0) or 0),
                float(f.get("LOT_SIZE", {}).get("minQty", 0) or 0),
                float(f.get("MIN_NOTIONAL", {}).get("notional", 0) or 0),
                s.get("onboardDate")))
        return out


@dataclass(frozen=True)
class MarketStats:
    quote_volume: float | None = None       # 24h, quote currency
    trades: int | None = None
    change_24h: float | None = None          # fraction
    bid: float | None = None
    ask: float | None = None
    bid_qty: float | None = None
    ask_qty: float | None = None
    atr_pct: float | None = None             # hourly ATR(14) / close
    realized_vol: float | None = None        # annualised, hourly closes
    max_hourly_move: float | None = None     # largest |close-to-close| hourly move
    adx: float | None = None
    candles: int = 0
    gaps: int = 0


class LiquidityAnalyzer:
    @staticmethod
    def top_depth_quote(st: MarketStats) -> float | None:
        if None in (st.bid, st.ask, st.bid_qty, st.ask_qty):
            return None
        assert st.bid is not None and st.ask is not None
        assert st.bid_qty is not None and st.ask_qty is not None
        return min(st.bid * st.bid_qty, st.ask * st.ask_qty)


class SpreadAnalyzer:
    @staticmethod
    def spread_bps(st: MarketStats) -> float | None:
        if st.bid is None or st.ask is None or st.bid <= 0 or st.ask < st.bid:
            return None
        return (st.ask - st.bid) / ((st.ask + st.bid) / 2) * 10_000


class VolatilityAnalyzer:
    @staticmethod
    def from_klines(rows: list[list[Any]]) -> dict[str, Any]:
        """Closed 1h klines (Binance array rows, oldest first; the last row is dropped as
        it may be open)."""
        import numpy as np
        import pandas as pd
        from nexus_strategies import indicators as ind

        rows = rows[:-1] if rows else rows
        if len(rows) < 30:
            return {"candles": len(rows), "gaps": 0}
        df = pd.DataFrame([[float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in rows],
                          columns=["open", "high", "low", "close"])
        t = np.array([int(r[0]) for r in rows])
        gaps = int(np.sum(np.diff(t) != 3_600_000))
        c = df["close"]
        r = np.log(c).diff().dropna()
        atr = ind.atr(df, 14).iloc[-1]
        return {"candles": len(rows), "gaps": gaps,
                "atr_pct": float(atr / c.iloc[-1]),
                "realized_vol": float(r.tail(168).std() * math.sqrt(24 * 365)),
                "max_hourly_move": float(r.tail(168).abs().max()),
                "adx": float(ind.adx(df, 14).iloc[-1])}


@dataclass(frozen=True)
class UniverseRules:
    market: str
    max_symbols: int
    min_quote_volume: float
    max_spread_bps: float
    min_top_depth_quote: float
    max_abs_change_24h: float = 0.25
    max_hourly_move: float = 0.08
    max_atr_pct: float = 0.03
    min_candles: int = 150
    max_gaps: int = 2
    min_listing_days: int = 60
    stage_two_candidates: int = 20
    vol_band: tuple[float, float] = (0.003, 0.015)   # hourly ATR% where grids work best
    weights: dict[str, float] = field(default_factory=lambda: {
        "liquidity": 0.30, "spread": 0.20, "volatility_fit": 0.20, "range": 0.20,
        "stability": 0.10})


SPOT_RULES = UniverseRules("spot", max_symbols=4, min_quote_volume=50e6, max_spread_bps=5,
                           min_top_depth_quote=10_000)
FUTURES_RULES = UniverseRules("futures", max_symbols=4, min_quote_volume=200e6,
                              max_spread_bps=3, min_top_depth_quote=20_000,
                              min_listing_days=180)


class UniverseRiskFilter:
    def __init__(self, rules: UniverseRules) -> None:
        self.rules = rules

    @staticmethod
    def structural(info: SymbolInfo, bases: set[str]) -> list[str]:
        reasons = []
        if info.status != "TRADING":
            reasons.append(f"not trading ({info.status})")
        if info.base in STABLE_OR_WRAPPED:
            reasons.append("stablecoin, fiat or wrapped duplicate")
        m = LEVERAGED_SUFFIX.match(info.base)
        if m and m.group("root") in bases:
            reasons.append("leveraged token")
        if info.status == "TRADING" and (info.price_tick <= 0 or info.amount_step <= 0):
            reasons.append("missing exchange filters (tick / step)")
        return reasons

    def liquidity(self, info: SymbolInfo, st: MarketStats, now_ms: int) -> list[str]:
        r, out = self.rules, []
        if st.quote_volume is None or st.quote_volume < r.min_quote_volume:
            out.append(f"24h volume {st.quote_volume or 0:,.0f} < {r.min_quote_volume:,.0f}")
        spread = SpreadAnalyzer.spread_bps(st)
        if spread is None:
            out.append("no valid book ticker")
        elif spread > r.max_spread_bps:
            out.append(f"spread {spread:.2f} bps > {r.max_spread_bps} bps")
        depth = LiquidityAnalyzer.top_depth_quote(st)
        if depth is not None and depth < r.min_top_depth_quote:
            out.append(f"top-of-book depth {depth:,.0f} < {r.min_top_depth_quote:,.0f}")
        if st.change_24h is not None and abs(st.change_24h) > r.max_abs_change_24h:
            out.append(f"abnormal 24h move {st.change_24h:+.1%}")
        if info.onboard_ms and now_ms - info.onboard_ms < r.min_listing_days * 86_400_000:
            out.append(f"listed less than {r.min_listing_days} days ago")
        return out

    def volatility(self, st: MarketStats) -> list[str]:
        r, out = self.rules, []
        if st.candles < r.min_candles:
            out.append(f"only {st.candles} hourly candles (need {r.min_candles})")
        if st.gaps > r.max_gaps:
            out.append(f"{st.gaps} gaps in hourly candles")
        if st.max_hourly_move is not None and st.max_hourly_move > r.max_hourly_move:
            out.append(f"abnormal hourly move {st.max_hourly_move:.1%} in the last 7 days")
        if st.atr_pct is not None and st.atr_pct > r.max_atr_pct:
            out.append(f"hourly ATR {st.atr_pct:.2%} > {r.max_atr_pct:.0%} (too volatile)")
        return out


class SymbolRankingEngine:
    def __init__(self, rules: UniverseRules) -> None:
        self.rules = rules

    def volatility_fit(self, atr_pct: float | None) -> float:
        if atr_pct is None:
            return 0.0
        lo, hi = self.rules.vol_band
        if lo <= atr_pct <= hi:
            return 1.0
        if atr_pct < lo:
            return max(0.0, atr_pct / lo)              # too quiet: steps would not pay fees
        return max(0.0, 1 - (atr_pct - hi) / hi)        # too volatile: penalised, not rewarded

    def score(self, st: MarketStats, volumes: list[float]) -> dict[str, float]:
        r = self.rules
        lv = math.log10(max(st.quote_volume or 1, 1))
        logs = sorted(math.log10(max(v, 1)) for v in volumes) or [lv]
        liq = sum(1 for x in logs if x <= lv) / len(logs)
        spread = SpreadAnalyzer.spread_bps(st)
        parts = {
            "liquidity": liq,
            "spread": 0.0 if spread is None else max(0.0, 1 - spread / r.max_spread_bps),
            "volatility_fit": self.volatility_fit(st.atr_pct),
            "range": 0.0 if st.adx is None else max(0.0, 1 - st.adx / 40),
            "stability": 0.0 if st.change_24h is None
            else max(0.0, 1 - abs(st.change_24h) / 0.15)}
        parts["score"] = sum(r.weights[k] * parts[k] for k in r.weights)
        return {k: round(v, 4) for k, v in parts.items()}


class DynamicUniverseEngine:
    def __init__(self, rules: UniverseRules) -> None:
        self.rules = rules
        self.filter = UniverseRiskFilter(rules)
        self.ranker = SymbolRankingEngine(rules)

    def stage_one(self, infos: list[SymbolInfo], stats: dict[str, MarketStats],
                  now_ms: int) -> tuple[list[SymbolInfo], list[dict[str, Any]]]:
        """Cheap filters on every symbol. Returns the stage-two candidates (most liquid
        first) and the rejection records."""
        bases = {i.base for i in infos}
        records, passed = [], []
        for info in infos:
            st = stats.get(info.symbol, MarketStats())
            reasons = UniverseRiskFilter.structural(info, bases)
            if not reasons:
                reasons = self.filter.liquidity(info, st, now_ms)
            if reasons:
                records.append({"symbol": info.symbol, "stage": 1, "eligible": False,
                                "reasons": reasons})
            else:
                passed.append(info)
        passed.sort(key=lambda i: -(stats[i.symbol].quote_volume or 0))
        keep = passed[: self.rules.stage_two_candidates]
        for info in passed[self.rules.stage_two_candidates:]:
            records.append({"symbol": info.symbol, "stage": 1, "eligible": False,
                            "reasons": [f"outside the {self.rules.stage_two_candidates} most "
                                        "liquid candidates"]})
        return keep, records

    def stage_two(self, candidates: list[SymbolInfo], stats: dict[str, MarketStats],
                  incumbents: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
        """Volatility filters, scores and the hysteresis selection."""
        vols = [stats[c.symbol].quote_volume or 0 for c in candidates]
        rows: list[dict[str, Any]] = []
        for c in candidates:
            st = stats[c.symbol]
            reasons = self.filter.volatility(st)
            sc = self.ranker.score(st, vols)
            rows.append({"symbol": c.symbol, "pair": c.ccxt, "stage": 2,
                         "eligible": not reasons, "reasons": reasons, "scores": sc,
                         "metrics": {"quote_volume": st.quote_volume,
                                     "spread_bps": SpreadAnalyzer.spread_bps(st),
                                     "top_depth_quote": LiquidityAnalyzer.top_depth_quote(st),
                                     "atr_pct": st.atr_pct, "adx": st.adx,
                                     "change_24h": st.change_24h,
                                     "max_hourly_move": st.max_hourly_move},
                         "filters": c.to_json()})
        ranked = sorted([r for r in rows if r["eligible"]], key=lambda r: -r["scores"]["score"])
        for i, r in enumerate(ranked):
            r["rank"] = i + 1
        n = self.rules.max_symbols
        keep = [r["symbol"] for r in ranked if r["symbol"] in incumbents and r["rank"] <= 2 * n]
        for r in ranked:
            if len(keep) >= n:
                break
            if r["symbol"] not in keep:
                keep.append(r["symbol"])
        keep = keep[:n]
        for r in rows:
            r["selected"] = r["symbol"] in keep
            if r["eligible"] and not r["selected"]:
                r["reasons"] = [f"rank {r['rank']} below the {n} selected"]
            elif r["selected"] and r["symbol"] in incumbents:
                r["kept_by_hysteresis"] = r.get("rank", 0) > n
        return keep, rows


# ------------------------------------------------------------------- data acquisition
async def _get(client: Any, redis: Any, url: str, params: dict[str, Any],
               ttl: int) -> Any:
    from nexus_common.binance_guard import guarded_get

    r = await guarded_get(client, redis, url, params, cache_ttl=ttl)
    if r.status_code != 200:
        raise RuntimeError(f"{url.rsplit('/', 1)[-1]}: HTTP {r.status_code}")
    return r.json()


async def scan_market(client: Any, redis: Any, rules: UniverseRules, incumbents: list[str],
                      now_ms: int, *, api: str | None = None) -> dict[str, Any]:
    """One full scan of one market. Raises on any data failure (the caller keeps the
    previous universe). ``api`` overrides the base URL (tests, public data host)."""
    api = api or (SPOT_API if rules.market == "spot" else FUTURES_API)
    info = await _get(client, redis, f"{api}/exchangeInfo", {}, 3600)
    infos = (SymbolDiscoveryService.parse_spot(info) if rules.market == "spot"
             else SymbolDiscoveryService.parse_futures(info))
    tickers = await _get(client, redis, f"{api}/ticker/24hr", {}, 300)
    books = await _get(client, redis, f"{api}/ticker/bookTicker", {}, 30)
    tk = {t["symbol"]: t for t in tickers}
    bk = {b["symbol"]: b for b in books}
    stats: dict[str, MarketStats] = {}
    for i in infos:
        t, b = tk.get(i.symbol, {}), bk.get(i.symbol, {})

        def f(d: dict[str, Any], k: str) -> float | None:
            v = d.get(k)
            return float(v) if v not in (None, "") else None
        pc = f(t, "priceChangePercent")
        stats[i.symbol] = MarketStats(
            quote_volume=f(t, "quoteVolume"), trades=int(t["count"]) if "count" in t else None,
            change_24h=None if pc is None else pc / 100, bid=f(b, "bidPrice"),
            ask=f(b, "askPrice"), bid_qty=f(b, "bidQty"), ask_qty=f(b, "askQty"))
    candidates, records = DynamicUniverseEngine(rules).stage_one(infos, stats, now_ms)
    for c in candidates:
        rows = await _get(client, redis, f"{api}/klines",
                          {"symbol": c.symbol, "interval": "1h", "limit": 250}, 900)
        stats[c.symbol] = replace(stats[c.symbol], **VolatilityAnalyzer.from_klines(rows))
    selected, ranked = DynamicUniverseEngine(rules).stage_two(candidates, stats, incumbents)
    return {"market": rules.market, "selected": selected, "ranked": ranked,
            "rejected_stage_one": records, "symbols_seen": len(infos),
            "filters": {s: next(i.to_json() for i in infos if i.symbol == s) for s in selected}}
