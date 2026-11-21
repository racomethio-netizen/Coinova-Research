"""Post-trade review: judges the PROCESS, not just the outcome.

A loss that followed every rule is a good trade; a win that broke a rule is a bad trade.
Labels: good/bad trade, good/poor execution, strategy failure, risk failure, rule
violation. Every label comes with the measured numbers behind it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

RULE_EXITS = {"stop_loss", "take_profit", "trailing_stop", "partial_take_profit",
              "kill_switch_close", "time_exit"}


def _d(x: Any) -> Decimal | None:
    try:
        return None if x is None else Decimal(str(x))
    except Exception:
        return None


def review_trade(position: dict[str, Any], entry: dict[str, Any] | None,
                 exits: list[dict[str, Any]], *, slippage_bps_assumed: Decimal) -> dict[str, Any]:
    pnl = _d(position.get("realized_pnl")) or Decimal(0)
    outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "flat"
    labels: list[str] = []
    notes: list[str] = []
    details = (entry or {}).get("details") or {}
    stop = _d(details.get("stop"))
    fill = _d((entry or {}).get("average_fill_price"))
    expected = _d((entry or {}).get("expected_price"))
    qty = _d((entry or {}).get("filled_amount"))
    violations: list[str] = []
    if entry is None:
        violations.append("no recorded entry order")
    if stop is None:
        violations.append("entered without a stop-loss")
    mult = _d(details.get("risk_multiplier"))
    if mult is not None and mult > 1:
        violations.append(f"risk multiplier {mult} above 1")
    reason = position.get("exit_reason")
    if reason not in RULE_EXITS:
        violations.append(f"exit not by a defined rule ({reason})")
    # Execution quality: entry slippage vs the price expected when the order was sent.
    slip_bps = None
    if fill is not None and expected and expected > 0:
        slip_bps = (fill - expected) / expected * Decimal(10_000)
        notes.append(f"entry slippage {slip_bps:.1f} bps (assumed {slippage_bps_assumed} bps)")
    exit_slips = []
    for x in exits:
        xf, xe = _d(x.get("average_fill_price")), _d(x.get("expected_price"))
        if xf is not None and xe:
            exit_slips.append((xe - xf) / xe * Decimal(10_000))
    if exit_slips:
        notes.append(f"exit slippage {max(exit_slips):.1f} bps (worst)")
    worst = max([s for s in [slip_bps, *exit_slips] if s is not None], default=None)
    poor_exec = worst is not None and worst > 2 * slippage_bps_assumed
    labels.append("poor execution" if poor_exec else "good execution")
    # Risk: realized loss vs the loss planned at entry (quantity x stop distance).
    planned = None
    if fill is not None and stop is not None and qty:
        planned = qty * (fill - stop)
        notes.append(f"planned risk {planned:.2f}, realized {pnl:.2f}")
    risk_failure = planned is not None and planned > 0 and pnl < 0 and -pnl > planned * Decimal(
        "1.5")
    if risk_failure:
        labels.append("risk failure")
        notes.append("loss exceeded 1.5x the planned risk (gap, slippage or a late exit)")
    if violations:
        labels.append("rule violation")
        notes += violations
    if outcome == "loss" and reason == "stop_loss" and not violations and not risk_failure:
        labels.append("strategy failure")
        notes.append("thesis invalidated at the planned stop: the risk control worked")
    good = not violations and not risk_failure
    labels.insert(0, "good trade" if good else "bad trade")
    return {"position_id": position.get("id"), "symbol": position.get("symbol"),
            "strategy": details.get("strategy"), "regime": details.get("regime"),
            "outcome": outcome, "realized_pnl": str(pnl), "exit_reason": reason,
            "labels": labels, "notes": notes,
            "entry_slippage_bps": None if slip_bps is None else str(round(slip_bps, 2)),
            "closed_at": position.get("closed_at")}
