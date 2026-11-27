"""Scheduled high-impact economic events and the trading blackout around them
(unified brief, section D: news and economic event risk).

The calendar is a committed data file (``data/economic_calendar.json``). Every date was
copied from the publisher's own official page, which is recorded per source with the
date it was read. Nothing is inferred: a source whose page did not list a period simply
has no events there, and its ``covers_until`` says how far it is complete.

Times are stored as the publisher states them (local time and time zone) and converted
to UTC here, so daylight-saving changes are handled. An event whose time is not
published blocks its whole local day.

Blackout: no NEW entries in instruments exposed to the event's currency from ``BEFORE``
before to ``AFTER`` after the event (a source may set wider windows, for example 90
minutes after the Fed's statement to cover its press conference). Exits are never
blocked. Crypto (USDT pairs) counts as exposed to USD events.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DATA = Path(__file__).resolve().parent / "data" / "economic_calendar.json"
BEFORE = timedelta(minutes=30)
AFTER = timedelta(minutes=30)
STALE_WARNING = timedelta(days=45)   # warn when a source's coverage ends this soon


@dataclass(frozen=True)
class Event:
    start: datetime                  # UTC: the announcement, or local midnight if no time
    end: datetime                    # UTC: same as start, or the end of the local day
    currency: str
    title: str
    source: str
    time_known: bool
    before: timedelta = BEFORE
    after: timedelta = AFTER

    def window(self) -> tuple[datetime, datetime]:
        return self.start - self.before, self.end + self.after

    def as_dict(self) -> dict[str, Any]:
        lo, hi = self.window()
        return {"start": self.start.isoformat(), "end": self.end.isoformat(),
                "blackout_from": lo.isoformat(), "blackout_to": hi.isoformat(),
                "currency": self.currency, "title": self.title, "source": self.source,
                "time_known": self.time_known}


@dataclass(frozen=True)
class Calendar:
    events: tuple[Event, ...]
    sources: dict[str, dict[str, Any]]

    def covers_until(self, currency: str) -> date | None:
        """The earliest coverage end among the sources for this currency."""
        ends = [date.fromisoformat(s["covers_until"]) for s in self.sources.values()
                if s.get("currency") == currency and s.get("covers_until")]
        return min(ends) if ends else None


def _event(raw: dict[str, Any], sources: dict[str, dict[str, Any]]) -> Event:
    src = sources[raw["source"]]
    tz = ZoneInfo(raw.get("tz") or src["tz"])
    day = date.fromisoformat(raw["date"])
    hhmm = raw.get("time", src.get("time"))
    if hhmm:
        local = datetime.combine(day, time.fromisoformat(hhmm), tzinfo=tz)
        start = end = local.astimezone(UTC)
    else:
        start = datetime.combine(day, time(0), tzinfo=tz).astimezone(UTC)
        end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz).astimezone(UTC)
    return Event(start, end, raw.get("currency") or src["currency"],
                 raw.get("title") or src["title"], raw["source"], bool(hhmm),
                 timedelta(minutes=src.get("before_min", BEFORE.total_seconds() / 60)),
                 timedelta(minutes=src.get("after_min", AFTER.total_seconds() / 60)))


def parse(data: dict[str, Any]) -> Calendar:
    sources = data["sources"]
    events = sorted((_event(e, sources) for e in data["events"]), key=lambda e: e.start)
    return Calendar(tuple(events), sources)


@lru_cache(maxsize=1)
def load(path: str = str(DATA)) -> Calendar:
    return parse(json.loads(Path(path).read_text()))


def currencies(symbol: str, market: str) -> tuple[str, ...]:
    """The currencies an instrument is exposed to: both legs of a forex pair; USD for
    USDT crypto pairs and USD-quoted metals."""
    s = symbol.upper().replace("/", "")
    if market == "crypto":
        return ("USD",) if s.endswith(("USDT", "USDC", "USD")) else ()
    if len(s) == 6:
        return (s[:3], s[3:]) if s[:3] not in ("XAU", "XAG") else ("USD",)
    return ()


def event_risk(symbol: str, market: str, at: datetime, cal: Calendar | None = None,
               *, horizon: timedelta = timedelta(days=7)) -> dict[str, Any]:
    """Whether new entries are blocked at ``at``, the events behind it, the next event,
    and whether the calendar is still complete for these currencies."""
    cal = cal or load()
    ccys = currencies(symbol, market)
    active = [e for e in cal.events if e.currency in ccys
              and e.window()[0] <= at <= e.window()[1]]
    upcoming = [e for e in cal.events if e.currency in ccys and at < e.start <= at + horizon]
    cover = {c: cal.covers_until(c) for c in ccys}
    stale = {c: (d is None or datetime.combine(d, time(0), tzinfo=UTC) - at < STALE_WARNING)
             for c, d in cover.items()}
    return {"symbol": symbol, "market": market, "currencies": list(ccys),
            "blocked": bool(active), "active": [e.as_dict() for e in active],
            "upcoming": [e.as_dict() for e in upcoming[:5]],
            "covers_until": {c: (d.isoformat() if d else None) for c, d in cover.items()},
            "coverage_warning": [c for c, s in stale.items() if s],
            "rule": "no new entries around a scheduled event (normally 30 min before to 30 "
                    "min after; the whole local day when the publisher prints no time); "
                    "exits are never blocked"}
