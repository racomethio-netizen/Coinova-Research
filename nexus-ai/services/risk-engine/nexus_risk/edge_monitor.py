"""Edge-decay monitor: detects when a strategy's edge disappears and cuts risk or stops it.

Input: the strategy's closed trades in R (net P&L / money at risk to the initial stop), in
the order they closed. Three independent detectors:

1. **CUSUM** (Page's one-sided test) for a downward shift of the mean R. With the reference
   mean ``ref_mean`` (expected R per trade, 0 for a strategy with no validated edge) and the
   scale ``sigma``: S = max(0, S + (ref_mean - k*sigma - R) / sigma). An alarm (S >= h)
   means the recent mean is consistently below the reference: **STOP**.
2. **Rolling window.** Mean R of the last ``window`` trades. If its upper bound (mean + 1
   standard error) is below 0, the recent edge is negative with reasonable confidence:
   **REDUCE** (half size).
3. **Drawdown in R** (peak to trough of cumulative R): **REDUCE** beyond
   ``reduce_drawdown_r`` (10R), **STOP** beyond ``max_drawdown_r`` (15R).

Calibration (simulated healthy edge +0.3R, sd 1.2R, 120 trades, vs one decaying to -0.5R
after 60 trades):
* CUSUM h=4: 1.3% false alarms, 82% of decays caught;
* 15R drawdown stop: 1.5% false alarms, 88% caught;
* 10R reduce: 13% false (it only halves size), 98% caught.
At 0.25% risk per trade, 15R is about 3.75% of equity, inside the 5% portfolio limit.

States only TIGHTEN automatically (NORMAL → REDUCE → STOP). Loosening needs the owner's
audited reset: live or paper performance never relaxes a risk control by itself.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class EdgeState(IntEnum):
    NORMAL = 0
    REDUCE = 1
    STOP = 2


MULTIPLIER = {EdgeState.NORMAL: 1.0, EdgeState.REDUCE: 0.5, EdgeState.STOP: 0.0}


@dataclass(frozen=True)
class EdgeConfig:
    min_trades: int = 10           # below this only the drawdown rule acts
    window: int = 20
    cusum_k: float = 0.5           # allowance, in sigma (detects a shift of about 1 sigma)
    cusum_h: float = 4.0           # alarm threshold, in sigma
    reduce_drawdown_r: float = 10.0
    max_drawdown_r: float = 15.0
    default_sigma: float = 1.0     # R units, used until enough trades exist


@dataclass(frozen=True)
class EdgeStatus:
    state: EdgeState
    multiplier: float
    reasons: tuple[str, ...]
    trades: int
    cusum: float
    rolling_mean: float | None
    rolling_upper: float | None
    drawdown_r: float
    details: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"state": self.state.name, "multiplier": self.multiplier,
                "reasons": list(self.reasons), "trades": self.trades,
                "cusum": round(self.cusum, 4),
                "rolling_mean": None if self.rolling_mean is None else round(self.rolling_mean, 4),
                "rolling_upper": None if self.rolling_upper is None
                else round(self.rolling_upper, 4),
                "drawdown_r": round(self.drawdown_r, 4), **self.details}


def cusum_down(r: list[float], ref_mean: float, sigma: float, k: float) -> float:
    s = 0.0
    for x in r:
        s = max(0.0, s + (ref_mean - k * sigma - x) / sigma)
    return s


def drawdown_r(r: list[float]) -> float:
    peak = cum = worst = 0.0
    for x in r:
        cum += x
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    return worst


def evaluate(r: list[float], *, ref_mean: float = 0.0, ref_sigma: float | None = None,
             previous: EdgeState = EdgeState.NORMAL, cfg: EdgeConfig | None = None
             ) -> EdgeStatus:
    cfg = cfg or EdgeConfig()
    n = len(r)
    sigma = ref_sigma or (statistics_std(r) if n >= cfg.min_trades else cfg.default_sigma)
    sigma = sigma if sigma and sigma > 0 else cfg.default_sigma
    reasons: list[str] = []
    state = EdgeState.NORMAL
    dd = drawdown_r(r)
    if dd >= cfg.max_drawdown_r:
        state = EdgeState.STOP
        reasons.append(f"drawdown {dd:.1f}R >= {cfg.max_drawdown_r:.1f}R")
    elif dd >= cfg.reduce_drawdown_r:
        state = EdgeState.REDUCE
        reasons.append(f"drawdown {dd:.1f}R >= {cfg.reduce_drawdown_r:.1f}R")
    s = cusum_down(r, ref_mean, sigma, cfg.cusum_k) if n >= cfg.min_trades else 0.0
    if s >= cfg.cusum_h:
        state = EdgeState.STOP
        reasons.append(f"CUSUM {s:.1f} >= {cfg.cusum_h:.1f}: mean R fell below the "
                       f"reference {ref_mean:+.2f}R")
    mean = upper = None
    if n >= cfg.min_trades:
        w = r[-cfg.window:]
        mean = sum(w) / len(w)
        se = statistics_std(w) / math.sqrt(len(w)) if len(w) > 1 else 0.0
        upper = mean + se
        if upper < 0 and state < EdgeState.REDUCE:
            state = EdgeState.REDUCE
            reasons.append(f"last {len(w)} trades: mean {mean:+.2f}R, upper bound "
                           f"{upper:+.2f}R < 0")
    if n < cfg.min_trades and state is EdgeState.NORMAL:
        reasons.append(f"collecting evidence ({n} of {cfg.min_trades} trades)")
    if previous > state:                      # never loosen automatically
        reasons.append(f"latched at {previous.name} until the owner resets it")
        state = previous
    return EdgeStatus(state, MULTIPLIER[state], tuple(reasons), n, s, mean, upper, dd,
                      {"ref_mean": ref_mean, "sigma": round(sigma, 4)})


def statistics_std(x: list[float]) -> float:
    if len(x) < 2:
        return 0.0
    m = sum(x) / len(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1))
