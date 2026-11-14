"""Telegram message templates (HTML parse mode). Every trading message states its mode."""

from __future__ import annotations

from html import escape
from typing import Any

ICONS = {
    "signal": "📡", "order_submitted": "📝", "order_filled": "✅", "stop_loss": "🛑",
    "take_profit": "🎯", "daily_report": "📊", "risk_warning": "⚠️",
    "exchange_disconnected": "🔌", "exchange_connected": "🔗", "emergency_shutdown": "🚨",
    "order_state_unknown": "❓",
}


def _mode(p: dict[str, Any]) -> str:
    mode = str(p.get("mode", "")).upper()
    return f"[{mode}] " if mode in ("PAPER", "LIVE") else ""


def _e(v: Any) -> str:
    return escape("—" if v is None else str(v))


def format_event(p: dict[str, Any]) -> str | None:
    kind = p.get("type")
    icon = ICONS.get(str(kind), "•")
    m = _mode(p)
    if kind == "signal":
        return (f"{icon} {m}<b>Signal</b> {_e(p.get('strategy'))} {_e(p.get('symbol'))}\n"
                f"entry {_e(p.get('entry'))} · stop {_e(p.get('stop'))} · "
                f"target {_e(p.get('target'))}\ndecision: <b>{_e(p.get('status'))}</b>"
                + (f"\nreason: {_e(p.get('reason'))}" if p.get("reason") else ""))
    if kind == "order_submitted":
        return (f"{icon} {m}<b>Order approved and sent</b> #{_e(p.get('order_id'))}\n"
                f"BUY {_e(p.get('quantity'))} {_e(p.get('symbol'))} on {_e(p.get('exchange'))} "
                f"limit {_e(p.get('price'))}\nstop {_e(p.get('stop'))} · target {_e(p.get('target'))}"
                f"\nstatus: <b>{_e(p.get('status'))}</b>")
    if kind in ("stop_loss", "take_profit"):
        label = "Stop-loss triggered" if kind == "stop_loss" else "Take-profit triggered"
        return (f"{icon} {m}<b>{label}</b> {_e(p.get('symbol'))} on {_e(p.get('exchange'))}\n"
                f"selling {_e(p.get('quantity'))} at bid {_e(p.get('bid'))}")
    if kind == "risk_warning":
        return (f"{icon} {m}<b>Risk limit</b> {_e(p.get('event'))} ({_e(p.get('severity'))})\n"
                f"equity {_e(p.get('equity'))}")
    if kind == "exchange_disconnected":
        return f"{icon} <b>{_e(p.get('exchange'))} unreachable</b>\n{_e(p.get('detail'))}"
    if kind == "exchange_connected":
        return f"{icon} <b>{_e(p.get('exchange'))} reconnected</b>"
    if kind == "emergency_shutdown":
        return (f"{icon} <b>EMERGENCY SHUTDOWN: kill switch engaged</b>\n"
                f"reason: {_e(p.get('reason'))}\nby: {_e(p.get('actor'))}\n"
                "No new orders until the owner clears it (2FA required).")
    if kind == "order_state_unknown":
        return (f"{icon} {m}<b>Order state unknown</b> #{_e(p.get('order_id'))}\n"
                "Exchange did not confirm the order. It will be reconciled by client order ID; "
                "it is never resubmitted automatically. New orders are blocked meanwhile.")
    if kind == "system_alert":
        state = "recovered" if p.get("ok") else "PROBLEM"
        return f"🖥️ <b>System {state}</b>: {_e(p.get('check'))} ({_e(p.get('detail'))})"
    if kind == "daily_report":
        r = p.get("report") or {}
        return (f"{icon} {m}<b>Daily performance</b> {_e(p.get('date'))}\n"
                f"{_e(r.get('title'))}\n"
                f"equity {_e(r.get('equity'))} · realized P&L {_e(r.get('realized_pnl'))}\n"
                f"unrealized {_e(r.get('unrealized_pnl'))} · drawdown {_e(r.get('current_drawdown'))}\n"
                f"closed trades {_e(r.get('closed_trades'))} · win rate {_e(r.get('win_rate'))}")
    return None  # unknown types are not sent (logged by the caller)
