"""Operating mode: what NEXUS is allowed to do with what it sees.

* ANALYSIS_ONLY    market analysis only: no trading signals are recorded, no orders.
* SIGNALS_ONLY     signals are generated and risk-checked, and recorded with the verdict;
                   nothing is executed.
* PAPER_TRADING    signals that pass every check become PAPER orders (simulated on real
                   order books). The default when the owner has never chosen.
* LIVE_AUTONOMOUS  signals that pass every check become REAL orders. Only selectable when
                   every live-trading gate passes (see nexus_api.routes.set_operating_mode),
                   and only a trader started in live mode (server configuration) acts on it.
                   A paper trader treats it as SIGNALS_ONLY (a shadow record to compare).

In every mode, positions that are already open keep being protected (stops, targets,
trailing, kill switch). The choice is durable: the latest ``operating_mode_set`` row written
by the owner in the append-only audit log. Unreadable = ANALYSIS_ONLY (fail closed).
"""

from __future__ import annotations

from enum import StrEnum


class OperatingMode(StrEnum):
    ANALYSIS_ONLY = "ANALYSIS_ONLY"
    SIGNALS_ONLY = "SIGNALS_ONLY"
    PAPER_TRADING = "PAPER_TRADING"
    LIVE_AUTONOMOUS = "LIVE_AUTONOMOUS"


DEFAULT = OperatingMode.PAPER_TRADING
AUDIT_ACTION = "operating_mode_set"


def records_signals(op: OperatingMode) -> bool:
    return op is not OperatingMode.ANALYSIS_ONLY


def places_orders(op: OperatingMode, broker_mode: str) -> bool:
    """Whether a trader whose broker is ``broker_mode`` ('paper' | 'live') may place
    ENTRY orders under ``op``. Exits of open positions are never gated by this."""
    if broker_mode == "live":
        return op is OperatingMode.LIVE_AUTONOMOUS
    return op is OperatingMode.PAPER_TRADING


def parse(raw: str | None) -> OperatingMode:
    """Stored value -> mode. Missing = the default; anything unrecognised = ANALYSIS_ONLY."""
    if raw is None:
        return DEFAULT
    try:
        return OperatingMode(raw)
    except ValueError:
        return OperatingMode.ANALYSIS_ONLY
