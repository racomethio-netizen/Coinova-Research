"""Strategy selector: which strategies may act in the current market regime.

A strategy is eligible in a regime only when ALL of these hold, using out-of-sample
(walk-forward) evidence recorded by validation:

* the strategy passed its validation gates as a whole;
* the regime is in its ``allowed_regimes`` (its own regime slice passed the same gates);
* that regime slice has at least ``min_trades`` trades and a profit factor >= ``min_pf``;
* a ROBUST estimate is positive: the lower bound mean - z x standard error of the per-trade
  net return (not the raw mean, and never recent wins alone);
* if a forward paper record exists with >= ``forward_min_trades`` trades, its own lower
  bound is not below ``forward_floor`` (recent results can only REMOVE a strategy, never
  qualify one).

Ranking among eligible strategies uses the robust lower bound. No eligible strategy means
NO TRADE for that regime.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SelectorRules:
    min_trades: int = 30
    min_pf: float = 1.15
    z: float = 1.0
    forward_min_trades: int = 20
    forward_floor: float = 0.0


DEFAULT_RULES = SelectorRules()


@dataclass(frozen=True)
class Assessment:
    strategy: str
    eligible: bool
    lower_bound: float | None
    expectancy: float | None
    trades: int
    reasons: tuple[str, ...]
    edge: dict[str, Any] = field(default_factory=dict)


def lower_bound(mean: float | None, std: float | None, n: int, z: float) -> float | None:
    if mean is None or std is None or n < 2:
        return None
    return mean - z * std / math.sqrt(n)


def assess(code: str, validation: dict[str, Any], regime: str | None, *,
           forward: dict[str, Any] | None = None,
           rules: SelectorRules = DEFAULT_RULES) -> Assessment:
    reasons: list[str] = []
    edges = validation.get("regime_edges") or {}
    edge = dict(edges.get(regime) or {}) if regime else {}
    n = int(edge.get("n") or 0)
    exp = edge.get("expectancy")
    if not validation:
        reasons.append("never validated")
    elif not validation.get("passed"):
        reasons.append("failed out-of-sample validation")
    if regime is None:
        reasons.append("market regime unknown")
    elif regime not in (validation.get("allowed_regimes") or []):
        reasons.append(f"no validated edge in the {regime} regime")
    if regime is not None and n < rules.min_trades:
        reasons.append(f"{n} out-of-sample trades in {regime} (< {rules.min_trades})")
    pf = edge.get("profit_factor")
    if edge and (pf is None or pf < rules.min_pf):
        reasons.append(f"profit factor {pf} < {rules.min_pf} in {regime}")
    lb = lower_bound(exp, edge.get("std"), n, rules.z)
    if edge and lb is None:
        reasons.append("statistics incomplete (re-run validation to record dispersion)")
    elif lb is not None and lb <= 0:
        reasons.append(f"robust expectancy {lb:.4%} <= 0 (mean {exp:.4%}, n={n})")
    if forward and int(forward.get("n") or 0) >= rules.forward_min_trades:
        flb = lower_bound(forward.get("expectancy"), forward.get("std"),
                          int(forward["n"]), rules.z)
        if flb is not None and flb < rules.forward_floor:
            reasons.append(f"forward paper results below floor ({flb:.4%})")
    return Assessment(code, not reasons, lb, exp, n, tuple(reasons), edge)


def select(evidence: dict[str, dict[str, Any]], regime: str | None, *,
           forward: dict[str, dict[str, Any]] | None = None,
           rules: SelectorRules = DEFAULT_RULES) -> list[Assessment]:
    """All strategies assessed for ``regime``; eligible ones first, best lower bound first."""
    out = [assess(code, v, regime, forward=(forward or {}).get(code), rules=rules)
           for code, v in evidence.items()]
    return sorted(out, key=lambda a: (not a.eligible, -(a.lower_bound or -1e9), a.strategy))
