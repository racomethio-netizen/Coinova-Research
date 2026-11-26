"""Read the owner's per-market autonomy switch (``nexus_common.markets``) for a worker.

Unreadable = OFF (fail closed): no new orders, open positions stay protected."""

from __future__ import annotations

from typing import Any

from nexus_common.logging import get_logger
from nexus_common.markets import Autonomy, Market, effective
from nexus_db import repo, transaction

log = get_logger("nexus.execution.markets_gate")


def read_autonomy(factory: Any, market: Market) -> Autonomy:
    try:
        with transaction(factory) as s:
            rows = repo.market_autonomy_db_state(s)
    except Exception as exc:
        log.error("market_autonomy_unreadable", market=market.value,
                  error=type(exc).__name__)
        return Autonomy.OFF
    return effective(market, rows)
