"""Performance metrics. All computed from simulated fills including costs."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from nexus_backtest.engine import BacktestResult


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    return float(((peak - equity) / peak).max())


def sharpe_sortino(equity: pd.Series) -> tuple[float | None, float | None]:
    """Annualized from DAILY returns (crypto trades 365 days)."""
    daily = equity.resample("1D").last().dropna()
    rets = daily.pct_change().dropna()
    if len(rets) < 10 or rets.std() == 0:
        return None, None
    sharpe = float(rets.mean() / rets.std() * math.sqrt(365))
    downside = rets[rets < 0]
    sortino = (float(rets.mean() / downside.std() * math.sqrt(365))
               if len(downside) > 1 and downside.std() > 0 else None)
    return sharpe, sortino


def trade_stats(trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty:
        return {"trades": 0, "win_rate": None, "profit_factor": None, "expectancy": None,
                "avg_win": None, "avg_loss": None, "net_pnl": 0.0, "fees": 0.0}
    wins, losses = trades[trades.pnl > 0], trades[trades.pnl <= 0]
    gross_win, gross_loss = wins.pnl.sum(), -losses.pnl.sum()
    return {
        "trades": len(trades),
        "win_rate": float(len(wins) / len(trades)),
        "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else None,
        "expectancy": float(trades.pnl.mean()),
        "expectancy_pct": float(trades["return"].mean()),
        "avg_win": float(wins.pnl.mean()) if len(wins) else None,
        "avg_loss": float(losses.pnl.mean()) if len(losses) else None,
        "net_pnl": float(trades.pnl.sum()),
        "fees": float(trades.fees.sum()),
    }


def compute_metrics(result: BacktestResult) -> dict[str, Any]:
    eq = result.equity
    initial = float(result.config.initial_capital)
    trades = result.trades_frame()
    final = float(eq.iloc[-1]) if len(eq) else initial
    days = max((eq.index[-1] - eq.index[0]).total_seconds() / 86400, 1) if len(eq) > 1 else 1
    sharpe, sortino = sharpe_sortino(eq)
    exposure = 0.0
    if not trades.empty and len(eq) > 1:
        held = (trades.exit_time - trades.entry_time).dt.total_seconds().sum()
        exposure = float(held / ((eq.index[-1] - eq.index[0]).total_seconds() or 1))
    out: dict[str, Any] = {
        "start": eq.index[0].isoformat() if len(eq) else None,
        "end": eq.index[-1].isoformat() if len(eq) else None,
        "initial_capital": initial,
        "final_equity": final,
        "net_return": final / initial - 1,
        "cagr": (final / initial) ** (365 / days) - 1 if final > 0 else None,
        "max_drawdown": max_drawdown(eq),
        "sharpe": sharpe,
        "sortino": sortino,
        "time_in_market": exposure,
        **trade_stats(trades),
        "events": [(t.isoformat(), k, v) for t, k, v in result.events],
        "rejected_signals": dict(result.rejected_signals),
    }
    if not trades.empty:
        out["by_strategy"] = {k: trade_stats(g) for k, g in trades.groupby("strategy")}
        out["by_regime"] = {str(k): trade_stats(g) for k, g in trades.groupby("regime")}
        out["by_exit"] = trades.exit_reason.value_counts().to_dict()
    return out


def json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [json_safe(v) for v in obj]
    if isinstance(obj, float | np.floating):
        f = float(obj)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(obj, np.integer):
        return int(obj)
    return obj
