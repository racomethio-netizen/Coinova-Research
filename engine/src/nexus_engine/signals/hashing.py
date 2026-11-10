"""Canonical signal payloads and the hash chain (ADR-005).

content_hash = sha256(canonical JSON of the published fields, including prev_hash).
The same function rebuilds the payload from stored rows, so `verify_chain`
detects any change made outside the database's insert-only rules.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

HASHED_FIELDS = (
    "id", "evaluation_id", "pair_id", "timeframe", "strategy_id", "model_version_id", "direction", "as_of", "published_at",
    "entry_low", "entry_high", "stop_loss", "take_profits", "risk_reward", "confidence", "confidence_band",
    "entry_expires_at", "horizon_bars", "assumptions", "explanation", "visibility", "prev_hash",
)  # fmt: skip


def normalize(v: Any) -> Any:
    """Canonical JSON form of a stored value (also used by the ledger export)."""
    if isinstance(v, datetime):
        return v.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, list | tuple):
        return [normalize(x) for x in v]
    if isinstance(v, dict):
        return {str(k): normalize(x) for k, x in v.items()}
    if v is None or isinstance(v, str | int | bool):
        return v
    return str(v)  # UUIDs


def canonical(payload: Mapping[str, Any]) -> bytes:
    data = {k: normalize(payload[k]) for k in HASHED_FIELDS}
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def content_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()


def verify_chain(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """Rows in `seq` order. Returns a list of problems (empty means intact)."""
    problems: list[str] = []
    prev: str | None = None
    for row in rows:
        if row["prev_hash"] != prev:
            problems.append(f"signal {row['id']}: prev_hash does not link to the previous signal")
        if content_hash(row) != row["content_hash"]:
            problems.append(f"signal {row['id']}: content does not match its hash")
        prev = row["content_hash"]
    return problems
