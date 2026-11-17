"""News-risk score in [0, 1] from recent items. Rules are explicit keyword weights.

Score = highest matched weight among items newer than the window, decayed linearly to
zero over the window. Unknown coverage (any feed failing) raises the score to at least
``unknown_floor`` so the decision layer sees uncertainty, not silence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from nexus_news.feeds import Item

RULES: tuple[tuple[str, float], ...] = (
    ("fomc", 1.0), ("federal funds rate", 1.0), ("interest rate decision", 1.0),
    ("consumer price", 0.9), ("cpi", 0.9), ("employment situation", 0.9),
    ("nonfarm", 0.9), ("payroll", 0.8), ("inflation", 0.7),
    ("exchange halt", 0.9), ("withdrawals paused", 0.9), ("security incident", 0.9),
    ("hack", 0.8), ("outage", 0.6), ("degraded", 0.4), ("maintenance", 0.3),
    ("sec charges", 0.8), ("enforcement", 0.6), ("lawsuit", 0.6), ("ban", 0.6),
    ("crypto", 0.4), ("bitcoin", 0.4), ("stablecoin", 0.5),
)


@dataclass(frozen=True)
class NewsRisk:
    score: float | None          # None = unknown (no reliable coverage)
    matched: tuple[str, ...]
    unknown_sources: tuple[str, ...]
    as_of: datetime


_PATTERNS = tuple((re.compile(rf"\b{re.escape(k)}\b"), k, w) for k, w in RULES)


def _weight(title: str) -> tuple[float, str | None]:
    """Whole-word matches only ("ban" must not match "Bank")."""
    lower = title.lower()
    best, label = 0.0, None
    for pattern, keyword, w in _PATTERNS:
        if w > best and pattern.search(lower):
            best, label = w, keyword
    return best, label


def score_items(items: list[Item], *, now: datetime, errors: dict[str, str],
                window: timedelta = timedelta(hours=6), unknown_floor: float = 0.7) -> NewsRisk:
    best = 0.0
    matched: list[str] = []
    for it in items:
        if it.published is None:
            continue
        age = now - it.published
        if age < timedelta(0) or age > window:
            continue
        w, label = _weight(it.title)
        decayed = w * (1 - age / window)
        if label and decayed > 0:
            matched.append(f"{it.source}: {label}")
        best = max(best, decayed)
    if errors:
        best = max(best, unknown_floor)
    # Unreadable coverage is "unknown", not zero: report it as None when nothing is readable.
    all_failed = bool(errors) and not items
    return NewsRisk(None if all_failed else round(best, 3), tuple(matched),
                    tuple(sorted(errors)), now)
