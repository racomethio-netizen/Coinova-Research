"""Central risk manager and capital allocator for the adaptive grid system (paper).

Every simulated ENTRY order of every grid, spot and futures, goes through
``GridRiskManager.authorize``. Exits and closes are never blocked: refusing a
risk-reducing order can only increase risk.

Portfolio state, evaluated over BOTH paper accounts together:
* ``SHUTDOWN``: drawdown from the peak reaches ``max_drawdown``. Everything is closed and
  the state stays latched until the owner restarts the system.
* ``NO_NEW_ORDERS``: any of the following.
  * The daily loss limit (since 00:00 UTC) or the weekly limit (since Monday 00:00 UTC) is
    reached.
  * The NEXUS kill switch is engaged, or the owner's risk floor forbids new trades.
  * Fail closed: the kill-switch state, account state or market data is unavailable.
* ``NORMAL``: otherwise.

Per-order checks (all must pass, each failure named):
* order risk: the loss if this order fills and its grid is then stopped, including fees
  and slippage, must be at most ``max_order_risk`` x total paper equity;
* portfolio exposure after the order (positions plus armed entries, both accounts);
* symbol exposure: per base asset, across spot and futures;
* correlated exposure: assets whose hourly returns correlate at ``corr_threshold`` or
  more with BTC count as one cluster;
* futures margin utilisation of the futures account;
* the number of open simulated orders;
* leverage: exactly 1x in this build.

Owner changes to the limits are accepted only within the hard ceilings of
``nexus_config`` (1% per order, 2% daily, 4% weekly, 10% drawdown), and are audited by
the API.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

D = Decimal

CEILINGS = {"max_order_risk": D("0.01"), "daily_loss": D("0.02"), "weekly_loss": D("0.04"),
            "max_drawdown": D("0.10")}


@dataclass(frozen=True)
class GridRiskLimits:
    max_order_risk: Decimal = Decimal("0.0025")
    daily_loss: Decimal = Decimal("0.01")
    weekly_loss: Decimal = Decimal("0.03")
    max_drawdown: Decimal = Decimal("0.05")
    max_portfolio_exposure: Decimal = Decimal("0.60")
    max_symbol_exposure: Decimal = Decimal("0.25")
    max_correlated_exposure: Decimal = Decimal("0.45")
    corr_threshold: float = 0.8
    max_futures_margin_util: Decimal = Decimal("0.50")
    max_open_orders: int = 120
    leverage: Decimal = Decimal("1")

    def problems(self) -> list[str]:
        out = []
        for k, ceiling in CEILINGS.items():
            v = getattr(self, k)
            if not D(0) < v <= ceiling:
                out.append(f"{k} {v} outside (0, {ceiling}]")
        if self.leverage != 1:
            out.append("leverage is fixed at 1x in this build")
        for k in ("max_portfolio_exposure", "max_symbol_exposure", "max_correlated_exposure",
                  "max_futures_margin_util"):
            if not D(0) < getattr(self, k) <= 1:
                out.append(f"{k} must be in (0, 1]")
        if not 1 <= self.max_open_orders <= 500:
            out.append("max_open_orders must be 1..500")
        return out

    def to_json(self) -> dict[str, Any]:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(self).items()}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> GridRiskLimits:
        base = cls()
        kw: dict[str, Any] = {}
        for k, v in d.items():
            if k in cls.__dataclass_fields__:
                cur = getattr(base, k)
                kw[k] = D(str(v)) if isinstance(cur, Decimal) else type(cur)(v)
        return replace(base, **kw)


@dataclass
class PortfolioView:
    """Both paper accounts as the risk manager sees them (built by the orchestrator)."""
    equity: Decimal
    peak_equity: Decimal
    day_start_equity: Decimal
    week_start_equity: Decimal
    exposure_by_asset: dict[str, Decimal] = field(default_factory=dict)
    futures_margin: Decimal = Decimal(0)
    futures_equity: Decimal = Decimal(0)
    open_orders: int = 0
    btc_corr: dict[str, float] = field(default_factory=dict)
    data_ok: bool = True
    kill_switch: bool | None = False      # None = unreadable
    owner_floor: str | None = None
    latched_shutdown: bool = False


@dataclass(frozen=True)
class OrderIntent:
    market: str
    symbol: str
    asset: str
    side: str            # "buy" / "sell"
    price: Decimal
    qty: Decimal
    loss_if_stopped: Decimal
    margin: Decimal = Decimal(0)


NO_NEW_FLOORS = frozenset({"NO_NEW_TRADES", "CLOSE_ONLY", "EMERGENCY_STOP"})


class GridRiskManager:
    def __init__(self, limits: GridRiskLimits | None = None) -> None:
        self.limits = limits or GridRiskLimits()

    def portfolio_state(self, v: PortfolioView) -> tuple[str, list[str]]:
        lim, reasons = self.limits, []
        if v.latched_shutdown:
            return "SHUTDOWN", ["shutdown latched: owner restart required"]
        if v.peak_equity > 0:
            dd = 1 - v.equity / v.peak_equity
            if dd >= lim.max_drawdown:
                return "SHUTDOWN", [f"drawdown {dd:.2%} >= {lim.max_drawdown:.0%} limit"]
        if not v.data_ok:
            reasons.append("market data or account state unavailable (fail closed)")
        if v.kill_switch is None:
            reasons.append("kill switch state unreadable (fail closed)")
        elif v.kill_switch:
            reasons.append("NEXUS kill switch engaged")
        if v.owner_floor in NO_NEW_FLOORS:
            reasons.append(f"owner risk floor {v.owner_floor}")
        if v.day_start_equity > 0 and \
                v.equity <= v.day_start_equity * (1 - lim.daily_loss):
            reasons.append(f"daily loss {1 - v.equity / v.day_start_equity:.2%} reached the "
                           f"{lim.daily_loss:.0%} limit")
        if v.week_start_equity > 0 and \
                v.equity <= v.week_start_equity * (1 - lim.weekly_loss):
            reasons.append(f"weekly loss {1 - v.equity / v.week_start_equity:.2%} reached "
                           f"the {lim.weekly_loss:.0%} limit")
        return ("NO_NEW_ORDERS", reasons) if reasons else ("NORMAL", [])

    def authorize(self, o: OrderIntent, v: PortfolioView) -> tuple[bool, list[str]]:
        lim = self.limits
        state, why = self.portfolio_state(v)
        if state != "NORMAL":
            return False, [f"portfolio {state}: " + "; ".join(why)]
        out = []
        if v.equity <= 0:
            return False, ["no paper equity"]
        if o.loss_if_stopped > lim.max_order_risk * v.equity:
            out.append(f"order risk {o.loss_if_stopped:.2f} > {lim.max_order_risk:.2%} of "
                       f"equity ({lim.max_order_risk * v.equity:.2f})")
        notional = o.price * o.qty
        total = sum(v.exposure_by_asset.values(), D(0)) + notional
        if total > lim.max_portfolio_exposure * v.equity:
            out.append(f"portfolio exposure {total / v.equity:.1%} > "
                       f"{lim.max_portfolio_exposure:.0%}")
        asset = v.exposure_by_asset.get(o.asset, D(0)) + notional
        if asset > lim.max_symbol_exposure * v.equity:
            out.append(f"{o.asset} exposure {asset / v.equity:.1%} > "
                       f"{lim.max_symbol_exposure:.0%}")
        cluster = {a for a, c in v.btc_corr.items() if c >= lim.corr_threshold} | {"BTC"}
        if o.asset in cluster:
            corr = sum((x for a, x in v.exposure_by_asset.items() if a in cluster), D(0)) \
                + notional
            if corr > lim.max_correlated_exposure * v.equity:
                out.append(f"BTC-correlated exposure {corr / v.equity:.1%} > "
                           f"{lim.max_correlated_exposure:.0%}")
        if o.market == "futures":
            fe = v.futures_equity
            if fe <= 0 or (v.futures_margin + o.margin) > lim.max_futures_margin_util * fe:
                out.append("futures margin utilisation would exceed "
                           f"{lim.max_futures_margin_util:.0%}")
        if v.open_orders + 1 > lim.max_open_orders:
            out.append(f"open simulated orders at the {lim.max_open_orders} limit")
        return (not out), out


@dataclass(frozen=True)
class AllocationPlan:
    per_grid: dict[str, Decimal]
    reasons: dict[str, str]


class PortfolioAllocator:
    """Splits each paper account's equity between its active grids. The two accounts are
    independent (spot capital is never used for futures margin and vice versa). Each grid
    is capped by the symbol-exposure limit of TOTAL equity."""

    def __init__(self, limits: GridRiskLimits) -> None:
        self.limits = limits

    def plan(self, account_equity: dict[str, Decimal], total_equity: Decimal,
             wanted: dict[str, list[str]]) -> AllocationPlan:
        per, why = {}, {}
        cap = self.limits.max_symbol_exposure * total_equity
        for market, symbols in wanted.items():
            eq = account_equity.get(market, D(0))
            if not symbols or eq <= 0:
                continue
            share = eq / len(symbols)
            for s in symbols:
                per[f"{market}:{s}"] = min(share, cap)
                why[f"{market}:{s}"] = (f"{market} account {eq:.2f} / {len(symbols)} grids"
                                        + (f", capped at {cap:.2f}" if share > cap else ""))
        return AllocationPlan(per, why)


def period_starts(now: datetime) -> tuple[str, str]:
    """Keys of the current UTC day and ISO week (for day / week start equity)."""
    iso = now.isocalendar()
    return now.strftime("%Y-%m-%d"), f"{iso.year}-W{iso.week:02d}"
