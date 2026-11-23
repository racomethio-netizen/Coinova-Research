"""Performance by market condition: does a strategy only work in one kind of market?

Each day of a reference market (BTC daily closes by default) gets labels computed only
from data up to that day (causal; the label never uses the future):

* **trend** (exactly one of):
  * ``bull``: 90-day return > +10% and close above its 200-day mean;
  * ``bear``: 90-day return < -10% and close below its 200-day mean;
  * ``sideways``: otherwise.
* **high_vol**: 30-day realised volatility above the 80th percentile of its own history
  so far (expanding, at least 365 days).
* **low_liquidity**: 30-day mean dollar volume below the 20th percentile of its history
  so far.
* **crash**: a day with a -10% close-to-close move, or a close 25% below the 30-day high,
  plus the 10 days after it.

``by_condition`` splits a strategy's DAILY returns (net of costs) by those labels and
reports the same metrics for each slice. It is a diagnostic, not a gate: one slice can
have few days, and the report gives the count.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

TREND_LOOKBACK, TREND_THRESHOLD, SMA = 90, 0.10, 200
VOL_WINDOW, VOL_PCT, LIQ_PCT, MIN_HISTORY = 30, 0.80, 0.20, 365
CRASH_DAY, CRASH_DD, CRASH_TAIL = -0.10, 0.25, 10


def _expanding_pct_rank(x: pd.Series, min_periods: int) -> pd.Series:
    """Rank of today's value within its own past (today included), 0..1."""
    vals = x.to_numpy(float)
    out = np.full(len(vals), np.nan)
    for i in range(len(vals)):
        if i + 1 < min_periods or not np.isfinite(vals[i]):
            continue
        hist = vals[: i + 1]
        hist = hist[np.isfinite(hist)]
        out[i] = float((hist <= vals[i]).mean())
    return pd.Series(out, index=x.index)


def _flag(cond: pd.Series, rank: pd.Series) -> pd.Series:
    """Nullable boolean: unknown (NA) while there is not enough history."""
    out = cond.astype("boolean")
    out[rank.isna()] = pd.NA
    return out


def label_days(close: pd.Series, volume: pd.Series | None = None) -> pd.DataFrame:
    """Causal market-condition labels per day (index = the reference market's days)."""
    c = close.astype(float)
    ret90 = c / c.shift(TREND_LOOKBACK) - 1
    sma = c.rolling(SMA, min_periods=SMA).mean()
    trend = pd.Series("sideways", index=c.index, dtype=object)
    trend[(ret90 > TREND_THRESHOLD) & (c > sma)] = "bull"
    trend[(ret90 < -TREND_THRESHOLD) & (c < sma)] = "bear"
    trend[sma.isna() | ret90.isna()] = None
    r = np.log(c).diff()
    vol = r.rolling(VOL_WINDOW).std() * math.sqrt(365)
    vol_rank = _expanding_pct_rank(vol, MIN_HISTORY)
    out = pd.DataFrame({"trend": trend, "realised_vol": vol,
                        "high_vol": _flag(vol_rank > VOL_PCT, vol_rank)})
    if volume is not None:
        dv = (c * volume.astype(float).reindex(c.index)).rolling(VOL_WINDOW).mean()
        liq_rank = _expanding_pct_rank(dv, MIN_HISTORY)
        out["low_liquidity"] = _flag(liq_rank < LIQ_PCT, liq_rank)
    day = c.pct_change()
    dd = c / c.rolling(30, min_periods=1).max() - 1
    shock = (day <= CRASH_DAY) | (dd <= -CRASH_DD)
    crash = shock.astype(float).rolling(CRASH_TAIL + 1, min_periods=1).max() > 0
    out["crash"] = crash
    return out


def _metrics(r: pd.Series) -> dict[str, Any]:
    r = r.dropna()
    n = len(r)
    if n == 0:
        return {"days": 0}
    eq = (1 + r).cumprod()
    sd = float(r.std(ddof=1)) if n > 1 else 0.0
    down = r[r < 0]
    dsd = float(np.sqrt((down ** 2).mean())) if len(down) else 0.0
    mdd = float((eq / eq.cummax() - 1).min())
    total = float(eq.iloc[-1] - 1)
    ann = (1 + total) ** (365 / n) - 1 if total > -1 else -1.0
    return {"days": n, "net_return": total,
            "annualised_return": ann if n >= 30 else None,
            "sharpe": float(r.mean() / sd * math.sqrt(365)) if sd > 0 else None,
            "sortino": float(r.mean() / dsd * math.sqrt(365)) if dsd > 0 else None,
            "max_drawdown": mdd,
            "calmar": ann / abs(mdd) if mdd < 0 and n >= 30 else None,
            "positive_days": float((r > 0).mean()),
            "exposure": float((r != 0).mean())}


def by_condition(daily_returns: pd.Series, labels: pd.DataFrame) -> dict[str, Any]:
    """Metrics of a strategy's daily returns overall and in every market condition."""
    r = daily_returns.astype(float)
    lab = labels.reindex(r.index)
    out: dict[str, Any] = {"all": _metrics(r)}
    for t in ("bull", "bear", "sideways"):
        out[t] = _metrics(r[lab["trend"] == t])
    out["unlabelled_trend"] = int(lab["trend"].isna().sum())
    for col in ("high_vol", "low_liquidity", "crash"):
        if col in lab:
            mask = lab[col].astype("boolean")
            out[col] = _metrics(r[mask.fillna(False).astype(bool)])
            out[f"not_{col}"] = _metrics(r[(~mask).fillna(False).astype(bool)])
    return out


def fragile_conditions(report: dict[str, Any], *, min_days: int = 60) -> list[str]:
    """Conditions with enough days where the strategy lost money or its Sharpe was
    negative, so the dashboard can show where it breaks."""
    out = []
    for k, m in report.items():
        if not isinstance(m, dict) or k == "all" or m.get("days", 0) < min_days:
            continue
        if m.get("net_return", 0) < 0 or (m.get("sharpe") or 0) < 0:
            out.append(k)
    return out
