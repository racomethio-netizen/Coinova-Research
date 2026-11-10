"""Gap detection over a stored candle sequence. Gaps are reported, never interpolated."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from ..timeframes import tf_delta


def find_gaps(stored_open_times: Iterable[datetime], tf: str, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Missing intervals in [start, end] (both aligned open times, inclusive).

    Returns (gap_start, gap_end) pairs where gap_start is the first missing open
    time and gap_end the first open time after the gap (exclusive).
    """
    step = tf_delta(tf)
    if end < start:
        return []
    present = sorted({t for t in stored_open_times if start <= t <= end})
    gaps: list[tuple[datetime, datetime]] = []
    expected = start
    for t in present:
        if t > expected:
            gaps.append((expected, t))
        expected = t + step
    if expected <= end:
        gaps.append((expected, end + step))
    return gaps
