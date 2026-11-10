"""Baselines reported with every backtest (docs/nexus/SIGNAL_ENGINE.md §6):
no-trade (0 by definition), buy-and-hold, and random entries with the same
count, directions and stop/target distances as the strategy's trades."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

import numpy as np

from ..marketdata.types import Candle
from ..signals.execution import Bar, Plan, resolve, trade_result
from ..signals.risk import CostModel, q_price
from .engine import Trade


def buy_and_hold(candles: Sequence[Candle], start: datetime, end: datetime, costs: CostModel) -> str | None:
    inside = [c for c in candles if c.is_final and start <= c.close_time <= end]
    if len(inside) < 2:
        return None
    net, _ = trade_result("BUY", inside[0].close, inside[-1].close, Decimal(0), costs)
    return str(net)


def random_entries(
    candles: Sequence[Candle],
    tf: str,
    trades: Sequence[Trade],
    start: datetime,
    end: datetime,
    costs: CostModel,
    entry_bars: int,
    horizon_bars: int,
    runs: int = 200,
    seed: int = 0,
) -> dict[str, object] | None:
    """Mean net return per trade over `runs` random-entry simulations, and where the strategy ranks."""
    entered = [t for t in trades if t.entry_price is not None and t.net_return is not None]
    cs = sorted((c for c in candles if c.is_final), key=lambda c: c.open_time)
    idx = [i for i, c in enumerate(cs) if start <= c.close_time <= end and i + 2 + entry_bars + horizon_bars < len(cs)]
    if not entered or not idx:
        return None
    bars = [Bar(c.open_time, c.open, c.high, c.low, c.close) for c in cs]
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(runs):
        rets = []
        for t in entered:
            assert t.entry_price is not None
            i = int(rng.choice(idx))
            ref = cs[i].close
            # Same relative stop/target distances as the original trade.
            stop_pct = abs(t.entry_price - t.stop_loss) / t.entry_price
            target_pct = abs(t.target - t.entry_price) / t.entry_price
            sign = Decimal(1) if t.direction == "BUY" else Decimal(-1)
            plan = Plan(
                t.direction,
                q_price(ref),
                q_price(ref),
                q_price(ref * (1 - sign * stop_pct)),
                q_price(ref * (1 + sign * target_pct)),
                entry_bars,
                horizon_bars,
                costs,
            )
            res = resolve(plan, bars[i + 1 : i + 2 + entry_bars + horizon_bars], tf, cs[i].open_time + (cs[1].open_time - cs[0].open_time))
            if res.net_return is not None:
                rets.append(float(res.net_return))
        if rets:
            means.append(float(np.mean(rets)))
    strat_mean = float(np.mean([float(t.net_return) for t in entered if t.net_return is not None]))
    arr = np.array(means)
    return {
        "runs": runs, "seed": seed,
        "mean_net_return_per_trade": {"p5": f"{np.percentile(arr, 5):.6f}", "p50": f"{np.percentile(arr, 50):.6f}", "p95": f"{np.percentile(arr, 95):.6f}"},
        "strategy_mean_net_return_per_trade": f"{strat_mean:.6f}",
        "strategy_percentile": f"{float((arr <= strat_mean).mean()):.4f}",
    }  # fmt: skip
