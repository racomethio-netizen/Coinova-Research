"""Server-side forex and metals bars from the Exness MT5 read-only bridge.

Before this, intraday forex data existed only on the owner's PC (the demo trader read it
from MetaTrader at each sync). Now the read-only bridge also sends closed M5 and H1 bars of
the forex majors and gold, and NEXUS keeps them in ``market_candles`` (timeframes ``5m``
and ``1h``, source ``mt5``), so research and future forex strategies run on stored data.

* Every bar is checked before it is stored (``check_bars``): well-formed and finite,
  positive prices, consistent OHLC, aligned to the timeframe, already closed (the forming
  bar is never stored), no duplicate times. Rejected rows are counted by reason, never
  repaired or guessed.
* The bridge sends a few recent bars each cycle. NEXUS asks it for a backfill when a
  series is new or short, or when the recent bars do not reach the newest stored bar (the
  bridge was offline): ``need_backfill``.
* Retention: M5 45 days, H1 400 days (``prune``), to stay well inside the database plan.
* ``series_health``: staleness, gaps and coverage measured only over FX-session hours
  (Sunday 17:00 to Friday 17:00 New York), so weekends are not gaps.

No price is ever invented: a missing bar stays missing and is reported as such.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Any

from nexus_common import sessions
from nexus_db import models, repo
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

# What the bridge sends intraday bars for by default (NEXUS_INTRADAY_SYMBOLS on the PC).
INTRADAY_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD", "NZDUSD",
                    "XAUUSD")
MT5_TF: dict[str, tuple[str, int]] = {"M5": ("5m", 300), "H1": ("1h", 3600)}
RETENTION_DAYS = {"5m": 45, "1h": 400}
MIN_BARS = {"5m": 1000, "1h": 500}       # fewer stored: ask the bridge for a backfill
SOURCE = "mt5"
SESSION = "fx_24x5"
# A bar that should have closed this long ago and is not stored makes the series stale.
# Gold has a daily trading break at the broker that the FX session does not model.
STALE_GRACE = {"forex": timedelta(minutes=10), "commodities": timedelta(minutes=75)}
HEALTH_WINDOW = {"5m": timedelta(days=7), "1h": timedelta(days=60)}
JUMP_FACTOR = 20                         # bar range above 20x the batch median: flagged


@dataclass
class Checked:
    bars: list[repo.CandleRow]
    rejected: dict[str, int] = field(default_factory=dict)
    forming: int = 0                     # not closed yet: skipped, not an error
    jumps: int = 0                       # stored, but flagged as unusually large

    def reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1


def check_bars(rows: list[list[float]], offset_h: int, now: datetime, seconds: int, *,
               align: bool = True) -> Checked:
    """Broker server-time rows ``[t, o, h, l, c, volume]`` -> closed, valid UTC bars,
    oldest first. ``align``: the UTC open must be a multiple of the timeframe (M5/H1;
    daily bars open at the broker's midnight, so they are not aligned)."""
    out = Checked([])
    seen: dict[datetime, repo.CandleRow] = {}
    for r in rows:
        if len(r) < 6 or not all(isinstance(x, int | float) and math.isfinite(x)
                                 for x in r[:6]):
            out.reject("malformed")
            continue
        if min(r[1:5]) <= 0:
            out.reject("non_positive")
            continue
        open_utc = datetime.fromtimestamp(int(r[0]), tz=UTC) - timedelta(hours=offset_h)
        if open_utc > now:
            out.reject("future")
            continue
        if open_utc + timedelta(seconds=seconds) > now:
            out.forming += 1
            continue
        if align and int(open_utc.timestamp()) % seconds:
            out.reject("misaligned")
            continue
        o, h, lo, c = (Decimal(str(x)) for x in r[1:5])
        if h < max(o, c) or lo > min(o, c):
            out.reject("ohlc_inconsistent")
            continue
        if open_utc in seen:
            out.reject("duplicate")
            continue
        seen[open_utc] = repo.CandleRow(open_utc, o, h, lo, c, Decimal(str(max(r[5], 0))))
    out.bars = [seen[t] for t in sorted(seen)]
    ranges = [b.high - b.low for b in out.bars if b.high > b.low]
    if len(ranges) >= 20:
        typical = median(ranges)
        out.jumps = sum(1 for b in out.bars if b.high - b.low > typical * JUMP_FACTOR)
    return out


def stored_state(s: Session, pair_ids: list[int], now: datetime
                 ) -> dict[tuple[int, str], tuple[int, datetime | None]]:
    """(pair, timeframe) -> (bars within retention, newest open) for the intraday frames."""
    if not pair_ids:
        return {}
    c = models.MarketCandle
    window = or_(*(and_(c.timeframe == tf, c.open_time >= now - timedelta(days=days))
                   for tf, days in RETENTION_DAYS.items()))
    rows = s.execute(select(c.pair_id, c.timeframe, func.count(), func.max(c.open_time))
                     .where(c.pair_id.in_(pair_ids), window)
                     .group_by(c.pair_id, c.timeframe)).all()
    return {(int(p), str(tf)): (int(n), newest) for p, tf, n, newest in rows}


def need_backfill(stored: int, newest_before: datetime | None, first_received: datetime | None,
                  timeframe: str, *, recently_full: bool) -> bool:
    """Whether to ask the bridge for the full series. ``recently_full``: a full series
    arrived in the last hours, so a short series is all the broker has."""
    if recently_full:
        return False
    if newest_before is None or stored < MIN_BARS[timeframe]:
        return True
    step = timedelta(seconds=dict(MT5_TF.values())[timeframe])
    return first_received is not None and first_received > newest_before + step


def prune(s: Session, pair_ids: list[int], now: datetime) -> int:
    """Delete MT5 intraday bars older than the retention of their timeframe."""
    if not pair_ids:
        return 0
    c = models.MarketCandle
    removed = 0
    for tf, days in RETENTION_DAYS.items():
        res = s.execute(delete(c).where(c.pair_id.in_(pair_ids), c.timeframe == tf,
                                        c.source == SOURCE,
                                        c.open_time < now - timedelta(days=days)))
        removed += int(getattr(res, "rowcount", 0) or 0)
    return removed


def _floor(ts: datetime, seconds: int) -> datetime:
    return datetime.fromtimestamp(int(ts.timestamp()) // seconds * seconds, tz=UTC)


def expected_opens(start: datetime, end: datetime, seconds: int,
                   kind: str = SESSION) -> list[datetime]:
    """Bar opens in [start, end] (aligned) at which the session is open."""
    step = timedelta(seconds=seconds)
    t = _floor(start, seconds)
    if t < start:
        t += step
    out = []
    while t <= end:
        if sessions.is_open(kind, t):
            out.append(t)
        t += step
    return out


def series_health(times: list[datetime], now: datetime, seconds: int, *,
                  window: timedelta, grace: timedelta, kind: str = SESSION,
                  expected: list[datetime] | None = None) -> dict[str, Any]:
    """Freshness and completeness of one stored series over session hours only.
    ``expected``: the session's bar opens in the window, if already computed."""
    step = timedelta(seconds=seconds)
    have = set(times)
    newest = max(times) if times else None
    if expected is None:
        expected = expected_opens(now - window, _floor(now, seconds) - step, seconds, kind)
    missing_runs, run, largest = 0, 0, 0
    for t in expected:
        if t in have:
            run = 0
            continue
        run += 1
        missing_runs += run == 1
        largest = max(largest, run)
    missing = sum(1 for t in expected if t not in have)
    overdue = [t for t in expected
               if (newest is None or t > newest) and t + step <= now - grace]
    market_open = sessions.is_open(kind, now)
    if newest is None:
        status = "no data"
    elif overdue:
        status = "stale"
    elif not market_open:
        status = "closed"
    else:
        status = "ok"
    return {"newest": newest.isoformat() if newest else None,
            "age_seconds": int((now - (newest + step)).total_seconds()) if newest else None,
            "status": status, "market_open": market_open, "overdue_bars": len(overdue),
            "expected": len(expected), "stored": len(have & set(expected)),
            "missing": missing, "gaps": missing_runs, "largest_gap_bars": largest,
            "coverage": round(1 - missing / len(expected), 4) if expected else None}


def health_report(s: Session, pairs: list[tuple[int, str, str]], now: datetime,
                  quotes: dict[str, dict[str, Any]] | None = None,
                  quality: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One row per (instrument, timeframe). ``pairs``: (pair id, base name, asset class).
    ``quotes``: the bridge's latest bid/ask by base name. ``quality``: the last batch's
    validation counts by "<base>/<timeframe>". One query per timeframe for all pairs (the
    database is far from the server)."""
    if not pairs:
        return []
    c = models.MarketCandle
    ids = [p for p, _, _ in pairs]
    times: dict[tuple[int, str], list[datetime]] = {}
    ranges: dict[int, list[Decimal]] = {}
    expected: dict[str, list[datetime]] = {}
    for tf, secs in MT5_TF.values():
        window = HEALTH_WINDOW[tf]
        expected[tf] = expected_opens(now - window, _floor(now, secs) - timedelta(seconds=secs),
                                      secs)
        rows = s.execute(select(c.pair_id, c.open_time, c.high - c.low).where(
            c.pair_id.in_(ids), c.timeframe == tf, c.open_time >= now - window)
            .order_by(c.open_time.desc())).all()
        for pid, t, rng in rows:
            times.setdefault((int(pid), tf), []).append(t)
            if tf == "5m" and len(ranges.setdefault(int(pid), [])) < 288 and rng is not None:
                ranges[int(pid)].append(Decimal(rng))
    out = []
    for pid, base, asset_class in pairs:
        grace = STALE_GRACE.get(asset_class, STALE_GRACE["forex"])
        positive = [r for r in ranges.get(pid, []) if r > 0]
        typical = median(positive) if len(positive) >= 20 else None
        quote = (quotes or {}).get(base)
        spread = None
        if quote and quote.get("bid") and quote.get("ask"):
            spread = Decimal(str(quote["ask"])) - Decimal(str(quote["bid"]))
        for mt5_tf, (tf, secs) in MT5_TF.items():
            out.append({
                "symbol": base, "asset_class": asset_class, "timeframe": tf,
                **series_health(times.get((pid, tf), []), now, secs, window=HEALTH_WINDOW[tf],
                                grace=grace, expected=expected[tf]),
                "last_batch": (quality or {}).get(f"{base}/{mt5_tf}"),
                "spread": str(spread) if spread is not None else None,
                "spread_points": quote.get("spread_points") if quote else None,
                "spread_vs_m5_range": (round(float(spread / typical), 3)
                                       if spread is not None and typical else None)})
    return out
