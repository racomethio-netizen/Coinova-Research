"""Deterministic explanations stored with each signal (docs/nexus/SIGNAL_ENGINE.md §5).

Built only from the exact values the strategy used; all values are strings so
the stored JSON (and its hash) is stable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .strategy import Params, Proposal


def explain(direction: str, tf: str, f: dict[str, str], levels: dict[str, str], prop: Proposal, p: Params) -> dict[str, object]:
    up = direction == "BUY"
    trend = "up" if up else "down"
    reasons = [
        {"code": f"htf_trend_{trend}", "detail": f"{f['htf']} EMA50 {f['htf_ema50']} is {'above' if up else 'below'} EMA200 {f['htf_ema200']}"},
        {"code": "price_vs_ema200", "detail": f"close {f['close']} is {'above' if up else 'below'} {tf} EMA200 {f['ema200']}"},
        {"code": "pullback_to_ema20", "detail": f"price returned to the {tf} EMA20 ({f['ema20']}) within the last {p.pullback_bars} bars"},
        {"code": "rsi", "detail": f"RSI(14) is {f['rsi14']} after {'dipping below' if up else 'rising above'} {p.rsi_low if up else p.rsi_high}"},
        {"code": "momentum", "detail": f"MACD histogram {f['macd_hist']} is {'rising' if up else 'falling'}"},
        {"code": "volume", "detail": f"volume is {f['volume_ratio']}x its 20-bar average"},
    ]
    return {
        "summary": f"{'Long' if up else 'Short'} setup on {tf}: {trend}trend pullback with momentum and volume confirmation.",
        "reasons": reasons,
        "levels": {
            "entry": f"{prop.entry_low} - {prop.entry_high}",
            "stop_loss": str(prop.stop_loss),
            "take_profits": [str(x) for x in prop.take_profits],
            "net_risk_reward": str(prop.risk_reward),
            **levels,
        },
        "invalidation": f"a candle {'trading at or below' if up else 'trading at or above'} the stop loss {prop.stop_loss}",
        "risks": [
            "Rules-only strategy: confidence is not calibrated and no model has been validated yet.",
            "Crypto assets are volatile; gaps can fill stops at worse prices than shown.",
            f"Assumes {p.costs.fee_rate} fee and {p.costs.slippage_rate} slippage per side.",
        ],
        "confidence": "not calibrated",
    }
