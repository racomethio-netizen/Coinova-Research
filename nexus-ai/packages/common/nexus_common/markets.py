"""The markets NEXUS covers and the owner's per-market autonomy switch.

Four markets: crypto, stocks, commodities, forex. Each has its own switch:

* OFF       no new signals and no new orders. Positions that are already open keep being
            protected (stops, targets, kill switch).
* RESEARCH  data and analysis; signals are recorded with their risk verdict; no orders.
* PAPER     signals that pass every check become PAPER orders.
* LIVE      signals that pass every check become REAL orders. It needs that market's own
            manual authorization (exact phrase, 2FA, the market's live gates) AND a trader
            started in live mode. Code never selects it.

The switch can only narrow the global operating mode (``nexus_common.opmode``): the
effective mode is the more restrictive of the two.

The choice is durable. It is the latest ``market_autonomy_set`` row written by the owner
in the append-only audit log, one per market.
* Never set: the market's default. Crypto defaults to PAPER, which is today's behaviour;
  the other markets default to OFF.
* Unreadable: OFF (fail closed).

A market can only be switched to modes it actually supports (``allowed``). A market
without a data source, paper engine or broker connector stays OFF, and the reason is
shown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from nexus_common.opmode import OperatingMode

AUDIT_ACTION = "market_autonomy_set"


class Market(StrEnum):
    CRYPTO = "crypto"
    STOCKS = "stocks"
    COMMODITIES = "commodities"
    FOREX = "forex"


class Autonomy(StrEnum):
    OFF = "OFF"
    RESEARCH = "RESEARCH"
    PAPER = "PAPER"
    LIVE = "LIVE"


_RANK = {Autonomy.OFF: 0, Autonomy.RESEARCH: 1, Autonomy.PAPER: 2, Autonomy.LIVE: 3}
_OP_RANK = {OperatingMode.ANALYSIS_ONLY: 0, OperatingMode.SIGNALS_ONLY: 1,
            OperatingMode.PAPER_TRADING: 2, OperatingMode.LIVE_AUTONOMOUS: 3}
_CAP = {Autonomy.OFF: OperatingMode.ANALYSIS_ONLY, Autonomy.RESEARCH: OperatingMode.SIGNALS_ONLY,
        Autonomy.PAPER: OperatingMode.PAPER_TRADING, Autonomy.LIVE: OperatingMode.LIVE_AUTONOMOUS}


@dataclass(frozen=True)
class Venue:
    code: str
    name: str
    instruments: tuple[str, ...]
    connector: str                    # "built" | "not_built"
    access: str                       # how NEXUS talks to it
    owner_needs: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class MarketSpec:
    market: Market
    name: str
    venues: tuple[Venue, ...]
    research_data: bool               # historical data + analysis available
    paper_engine: bool                # paper orders possible
    live_connector: bool              # a broker connector that can place real orders
    default: Autonomy
    notes: tuple[str, ...] = field(default_factory=tuple)

    def allowed(self) -> tuple[Autonomy, ...]:
        out = [Autonomy.OFF]
        if self.research_data:
            out.append(Autonomy.RESEARCH)
        if self.paper_engine:
            out.append(Autonomy.PAPER)
        if self.live_connector:
            out.append(Autonomy.LIVE)
        return tuple(out)

    def missing_for(self, mode: Autonomy) -> list[str]:
        """Why ``mode`` cannot be selected for this market (empty = it can)."""
        need = {Autonomy.RESEARCH: ("research_data", "no historical data source is built yet"),
                Autonomy.PAPER: ("paper_engine", "no paper trading engine is built yet"),
                Autonomy.LIVE: ("live_connector", "no broker connector is built yet")}
        out: list[str] = []
        for m in (Autonomy.RESEARCH, Autonomy.PAPER, Autonomy.LIVE):
            if _RANK[m] <= _RANK[mode] and not getattr(self, need[m][0]):
                out.append(need[m][1])
        return out


IBKR = Venue(
    "ibkr", "Interactive Brokers",
    ("US and international stocks", "ETFs", "index futures and options where permitted",
     "commodity futures (CME, COMEX, NYMEX, CBOT), micro contracts preferred"),
    "not_built", "TWS API through IB Gateway (Stage 4)",
    ("an IBKR account (a paper account is enough to start)",
     "a host for IB Gateway with periodic two-factor login",
     "market-data subscriptions for real-time quotes (paid by IBKR's schedule)"),
    "Contract specifications (multiplier, tick, currency, trading hours) will be read "
    "from IBKR at connection time, never typed in by hand.")

MARKETS: dict[Market, MarketSpec] = {
    Market.CRYPTO: MarketSpec(
        Market.CRYPTO, "Cryptocurrency",
        (Venue("binance_spot", "Binance Spot", ("USDT spot pairs, dynamic discovery",),
               "built", "CCXT REST + WebSocket market data",
               notes="Live orders only through the live gate (phrase, 2FA, verified key)."),
         Venue("binance_usdm", "Binance USD-M Futures", ("USDT perpetuals",), "built",
               "native REST transport, 1x isolated",
               notes="Paper only; futures stay disabled for live unless the owner enables "
                     "them."),
         Venue("kraken_spot", "Kraken Spot", ("USDT/USD spot pairs",), "built", "CCXT")),
        research_data=True, paper_engine=True, live_connector=True, default=Autonomy.PAPER,
        notes=("Existing NEXUS crypto stack; behaviour unchanged at the default (PAPER).",)),
    Market.STOCKS: MarketSpec(
        Market.STOCKS, "Stocks and ETFs", (IBKR,),
        research_data=False, paper_engine=False, live_connector=False, default=Autonomy.OFF,
        notes=("US pattern-day-trader rule applies to US margin accounts under $25,000 "
               "(check the current FINRA rule).", "T+1 settlement; short sales need borrow.")),
    Market.COMMODITIES: MarketSpec(
        Market.COMMODITIES, "Commodities",
        (IBKR, Venue("mt5_cfd", "MetaTrader 5 broker (CFDs)",
                      ("gold, silver, oil, gas CFDs (broker-dependent)",), "not_built",
                      "MetaTrader5 Python package (Windows only, Stage 5)",
                      ("an MT5 broker account", "a Windows host running the MT5 terminal"))),
        research_data=False, paper_engine=False, live_connector=False, default=Autonomy.OFF,
        notes=("Futures expire and must be rolled; even micro contracts can exceed a small "
               "account's risk budget, in which case NEXUS refuses the trade.",)),
    Market.FOREX: MarketSpec(
        Market.FOREX, "Forex",
        (Venue("mt5", "MetaTrader 5 broker", ("major, minor and supported exotic pairs",),
               "not_built", "MetaTrader5 Python package (Windows only, Stage 5)",
               ("an MT5 broker account (demo first)", "a Windows host running the MT5 "
                "terminal")),
         Venue("oanda", "OANDA v20 (alternative)", ("major and minor pairs",), "not_built",
               "REST API, runs on Linux (Stage 5 option)",
               ("an OANDA account available in the owner's country",))),
        research_data=False, paper_engine=False, live_connector=False, default=Autonomy.OFF,
        notes=("Forex is leveraged by default; NEXUS keeps notional at or below equity "
               "(1x) unless the owner changes the mandate.",)),
}


def parse(market: Market, raw: str | None) -> Autonomy:
    """Stored value -> autonomy. Never set = the market's default; unknown = OFF."""
    if raw is None:
        return MARKETS[market].default
    try:
        return Autonomy(raw)
    except ValueError:
        return Autonomy.OFF


def effective(market: Market, rows: dict[str, dict[str, Any]]) -> Autonomy:
    """The market's autonomy from ``market_autonomy_db_state`` rows. A stored mode the
    market cannot support (for example after a connector is removed) becomes OFF."""
    row = rows.get(market.value)
    mode = parse(market, row.get("autonomy") if row else None)
    return mode if mode in MARKETS[market].allowed() else Autonomy.OFF


def combine(op: OperatingMode, autonomy: Autonomy) -> OperatingMode:
    """The more restrictive of the global operating mode and the market's switch."""
    cap = _CAP[autonomy]
    return op if _OP_RANK[op] <= _OP_RANK[cap] else cap


def allows_entries(autonomy: Autonomy, broker_mode: str) -> bool:
    """Whether a paper or live broker may place NEW orders under ``autonomy``."""
    if broker_mode == "live":
        return autonomy is Autonomy.LIVE
    return _RANK[autonomy] >= _RANK[Autonomy.PAPER]


def live_phrase(market: Market) -> str:
    return f"ENABLE LIVE {market.value.upper()}"


def describe() -> list[dict[str, Any]]:
    """Static description of every market for the API and dashboard."""
    return [{"market": s.market.value, "name": s.name, "default": s.default.value,
             "allowed": [a.value for a in s.allowed()],
             "capabilities": {"research_data": s.research_data, "paper_engine": s.paper_engine,
                              "live_connector": s.live_connector},
             "venues": [{"code": v.code, "name": v.name, "instruments": list(v.instruments),
                         "connector": v.connector, "access": v.access,
                         "owner_needs": list(v.owner_needs), "notes": v.notes}
                        for v in s.venues],
             "notes": list(s.notes)} for s in MARKETS.values()]
