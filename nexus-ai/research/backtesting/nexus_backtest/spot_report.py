"""Report for a spot-engine backtest: every metric the brief lists, net of costs, with
a BTC/USDT buy-and-hold benchmark, Monte Carlo of the trade sequence and bootstrap
confidence intervals. Descriptive: the decision uses the pre-registered gates only."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from nexus_backtest.montecarlo import bootstrap
from nexus_backtest.spot_engine import Result


def _sharpe_sortino(equity: pd.Series) -> tuple[float | None, float | None]:
    daily = equity.resample("1D").last().dropna().pct_change().dropna()
    if len(daily) < 20 or daily.std() == 0:
        return None, None
    sh = float(daily.mean() / daily.std() * math.sqrt(365))
    down = daily[daily < 0]
    so = float(daily.mean() / down.std() * math.sqrt(365)) if len(down) > 1 else None
    return sh, so


def _mdd(equity: pd.Series) -> float:
    return float((1 - equity / equity.cummax()).max()) if len(equity) else 0.0


def trade_stats(tr: pd.DataFrame) -> dict[str, Any]:
    if tr.empty:
        return {"trades": 0}
    wins, losses = tr[tr.pnl > 0], tr[tr.pnl <= 0]
    gl = -losses.pnl.sum()
    streak = longest = 0
    for x in tr.pnl:
        streak = streak + 1 if x <= 0 else 0
        longest = max(longest, streak)
    return {"trades": len(tr), "net_pnl": float(tr.pnl.sum()),
            "win_rate": float(len(wins) / len(tr)),
            "profit_factor": float(wins.pnl.sum() / gl) if gl > 0 else None,
            "avg_winner": float(wins.pnl.mean()) if len(wins) else None,
            "avg_loser": float(losses.pnl.mean()) if len(losses) else None,
            "expectancy": float(tr.pnl.mean()), "expectancy_r": float(tr.r.mean()),
            "longest_losing_streak": longest, "fees": float(tr.fees.sum())}


def _ci(values: np.ndarray, fn: Any, n: int = 2000, seed: int = 0) -> list[float] | None:
    if len(values) < 10:
        return None
    rng = np.random.default_rng(seed)
    stats = [fn(rng.choice(values, len(values), replace=True)) for _ in range(n)]
    stats = [x for x in stats if x is not None and np.isfinite(x)]
    return [float(np.percentile(stats, 2.5)), float(np.percentile(stats, 97.5))] if stats else None


def _pf(v: np.ndarray) -> float | None:
    gl = -v[v <= 0].sum()
    return float(v[v > 0].sum() / gl) if gl > 0 else None


def report(res: Result, btc_close: pd.Series) -> dict[str, Any]:
    eq = res.equity
    tr = pd.DataFrame(res.trades)
    initial = res.config.initial
    sh, so = _sharpe_sortino(eq)
    fills = pd.DataFrame([o for o in res.orders if o.get("status") in ("filled", "partial")])
    out: dict[str, Any] = {
        "period": [str(eq.index[0]), str(eq.index[-1])] if len(eq) else None,
        "initial": initial, "final_equity": float(eq.iloc[-1]) if len(eq) else initial,
        "net_profit": float(eq.iloc[-1] - initial) if len(eq) else 0.0,
        "return_pct": float(eq.iloc[-1] / initial - 1) if len(eq) else 0.0,
        "max_drawdown": _mdd(eq), "sharpe": sh, "sortino": so,
        "exposure_time": res.exposure,
        "slippage_total": float(fills["slippage"].sum()) if "slippage" in fills else 0.0,
        **trade_stats(tr)}
    if not tr.empty:
        tr["month"] = pd.to_datetime(tr.exit_time).dt.strftime("%Y-%m")
        out["by_symbol"] = {k: trade_stats(g) for k, g in tr.groupby("symbol")}
        out["by_strategy"] = {k: trade_stats(g) for k, g in tr.groupby("strategy")}
        out["by_regime"] = {str(k): trade_stats(g) for k, g in tr.groupby("regime")}
        out["by_exit"] = tr.exit_kind.value_counts().to_dict()
        pnl = tr.pnl.to_numpy(float)
        out["ci95_expectancy"] = _ci(pnl, lambda v: float(v.mean()))
        out["ci95_profit_factor"] = _ci(pnl, _pf)
        mc = bootstrap(pnl / initial, simulations=2000, seed=1)
        out["monte_carlo"] = mc.__dict__
    monthly = eq.resample("ME").last()
    out["monthly_returns"] = {k.strftime("%Y-%m"): float(v) for k, v in
                              monthly.pct_change().fillna(monthly.iloc[0] / initial - 1
                                                          if len(monthly) else 0).items()}
    b = btc_close.loc[eq.index[0]:eq.index[-1]] if len(eq) else btc_close.iloc[:0]
    if len(b) > 1:
        bh = b / b.iloc[0] * initial
        bsh, bso = _sharpe_sortino(bh)
        out["benchmark_btc_buy_and_hold"] = {"return_pct": float(b.iloc[-1] / b.iloc[0] - 1),
                                             "max_drawdown": _mdd(bh), "sharpe": bsh,
                                             "sortino": bso}
    signals = pd.DataFrame(res.signals)
    if not signals.empty:
        out["signals"] = {"candidates": len(signals),
                          "accepted": int((signals.status == "accepted").sum()),
                          "rejection_reasons": pd.Series(
                              [r for rs in signals.reasons for r in rs]).value_counts()
                          .head(15).to_dict()}
    rej = [o for o in res.orders if o.get("status") == "rejected"]
    out["orders"] = {"filled": int((fills.status == "filled").sum()) if len(fills) else 0,
                     "partial": int((fills.status == "partial").sum()) if len(fills) else 0,
                     "rejected": len(rej),
                     "rejected_reasons": pd.Series([o["why"] for o in rej]).value_counts()
                     .to_dict() if rej else {}}
    return out
