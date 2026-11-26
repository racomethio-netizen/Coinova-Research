"""Dynamic market universe: discovery, exclusions, ranking (volatility is not rewarded),
hysteresis, separate spot and futures rules, and request weights."""

from __future__ import annotations

from nexus_common.binance_guard import endpoint_weight
from nexus_market import universe as u


def spot_info(sym: str, base: str, status: str = "TRADING", spot: bool = True) -> dict:
    return {"symbol": sym, "baseAsset": base, "quoteAsset": "USDT", "status": status,
            "isSpotTradingAllowed": spot,
            "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                        {"filterType": "NOTIONAL", "minNotional": "5"}]}


def test_discovery_spot_and_futures_rules_are_separate():
    spot = u.SymbolDiscoveryService.parse_spot({"symbols": [
        spot_info("BTCUSDT", "BTC"), spot_info("ETHBTC", "ETH") | {"quoteAsset": "BTC"},
        spot_info("OLDUSDT", "OLD", status="BREAK"), spot_info("XUSDT", "X", spot=False)]})
    assert [s.symbol for s in spot] == ["BTCUSDT", "OLDUSDT", "XUSDT"]
    assert spot[0].min_notional == 5 and spot[2].status == "SPOT_TRADING_DISABLED"
    fut = u.SymbolDiscoveryService.parse_futures({"symbols": [
        {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "marginAsset": "USDT",
         "status": "TRADING", "contractType": "PERPETUAL", "underlyingType": "COIN",
         "onboardDate": 1569398400000,
         "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                     {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                     {"filterType": "MIN_NOTIONAL", "notional": "100"}]},
        {"symbol": "BTCUSDT_261225", "baseAsset": "BTC", "quoteAsset": "USDT",
         "status": "TRADING", "contractType": "CURRENT_QUARTER", "filters": []}]})
    assert fut[0].ccxt == "BTC/USDT:USDT" and fut[0].min_notional == 100
    assert fut[1].status.startswith("NOT_PERPETUAL")


def test_structural_exclusions():
    bases = {"BTC", "BTCUP", "JUP", "USDC"}
    lev = u.SymbolInfo("spot", "BTCUPUSDT", "BTCUP", "USDT", "TRADING", .01, .01, .01, 5)
    jup = u.SymbolInfo("spot", "JUPUSDT", "JUP", "USDT", "TRADING", .01, .01, .01, 5)
    usdc = u.SymbolInfo("spot", "USDCUSDT", "USDC", "USDT", "TRADING", .01, .01, .01, 5)
    assert "leveraged token" in u.UniverseRiskFilter.structural(lev, bases)
    assert u.UniverseRiskFilter.structural(jup, bases) == []          # JUP is not leveraged
    assert u.UniverseRiskFilter.structural(usdc, bases)[0].startswith("stablecoin")


def stats(vol: float, spread_bps: float = 1, atr: float = 0.008, adx: float = 18,
          chg: float = 0.01, move: float = 0.02) -> u.MarketStats:
    bid = 100.0
    ask = bid * (1 + spread_bps / 10_000)
    return u.MarketStats(quote_volume=vol, change_24h=chg, bid=bid, ask=ask, bid_qty=500,
                         ask_qty=500, atr_pct=atr, adx=adx, max_hourly_move=move,
                         candles=249, gaps=0)


def info(sym: str, market: str = "spot") -> u.SymbolInfo:
    return u.SymbolInfo(market, sym, sym[:-4], "USDT", "TRADING", .01, .001, .001, 5)


def test_ranking_penalises_excess_volatility_and_rewards_liquidity():
    rk = u.SymbolRankingEngine(u.SPOT_RULES)
    assert rk.volatility_fit(0.008) == 1.0
    assert rk.volatility_fit(0.0015) < 1.0 and rk.volatility_fit(0.025) < 0.5
    calm = rk.score(stats(1e9), [1e9, 1e8])
    wild = rk.score(stats(1e9, atr=0.028, adx=38, chg=0.12), [1e9, 1e8])
    assert calm["score"] > wild["score"]


def test_engine_filters_ranks_and_keeps_incumbents():
    eng = u.DynamicUniverseEngine(u.SPOT_RULES)
    syms = ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT", "EEEUSDT", "FFFUSDT", "GGGUSDT"]
    st = {s: stats(1e9 - i * 1e8, adx=10 + i * 3) for i, s in enumerate(syms)}
    st["GGGUSDT"] = stats(1e6)                                         # illiquid
    st["FFFUSDT"] = stats(5e8, spread_bps=20)                          # wide spread
    cands, rej = eng.stage_one([info(s) for s in syms], st, now_ms=2_000_000_000_000)
    assert {r["symbol"] for r in rej} == {"GGGUSDT", "FFFUSDT"}
    assert all(r["reasons"] for r in rej)
    st["EEEUSDT"] = stats(6e8, move=0.2)                                # abnormal hourly move
    keep, rows = eng.stage_two(cands, st, incumbents=[])
    assert keep == ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"]
    bad = next(r for r in rows if r["symbol"] == "EEEUSDT")
    assert not bad["eligible"] and "abnormal hourly move" in bad["reasons"][0]
    # Hysteresis: an incumbent ranked 5th..8th keeps its slot over a newcomer.
    st["DDDUSDT"] = stats(6.5e8, adx=35)
    st["EEEUSDT"] = stats(6e8, adx=5)
    keep2, _ = eng.stage_two(cands, st, incumbents=["DDDUSDT"])
    assert "DDDUSDT" in keep2 and len(keep2) == 4


def test_whole_market_request_weights():
    assert endpoint_weight("spot", "https://api.binance.com/api/v3/ticker/24hr", {}) == 80
    assert endpoint_weight("spot", "/api/v3/ticker/24hr", {"symbol": "BTCUSDT"}) == 2
    assert endpoint_weight("futures", "/fapi/v1/ticker/24hr", {}) == 40
    assert endpoint_weight("spot", "/api/v3/ticker/bookTicker", {}) == 4
    assert endpoint_weight("futures", "/fapi/v1/premiumIndex", {}) == 10
    assert endpoint_weight("futures", "/fapi/v1/premiumIndex", {"symbol": "BTCUSDT"}) == 1
