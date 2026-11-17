"""Fetch and parse RSS/Atom feeds safely (defusedxml, timeouts, size limits)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import defusedxml.ElementTree as ET
import httpx

USER_AGENT = "NEXUS-AI-news-monitor/1.0 (research; contact the owner)"
MAX_BYTES = 2_000_000
FEEDS: dict[str, str] = {
    "federal_reserve": "https://www.federalreserve.gov/feeds/press_all.xml",
    "sec": "https://www.sec.gov/news/pressreleases.rss",
    "kraken_status": "https://status.kraken.com/history.atom",
    "coinbase_status": "https://status.coinbase.com/history.atom",
}


@dataclass(frozen=True)
class Item:
    source: str
    title: str
    link: str
    published: datetime | None


def _text(el: ET.Element | None) -> str:
    return "" if el is None or el.text is None else el.text.strip()


def _parse_date(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_feed(source: str, xml_bytes: bytes) -> list[Item]:
    """RSS 2.0 (<item>) or Atom (<entry>). Raises ValueError on malformed XML."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"malformed feed from {source}") from exc
    items: list[Item] = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        title = _text(next((c for c in el if c.tag.rsplit("}", 1)[-1] == "title"), None))
        link_el = next((c for c in el if c.tag.rsplit("}", 1)[-1] == "link"), None)
        link = (link_el.get("href") if link_el is not None and link_el.get("href")
                else _text(link_el))
        date_el = next((c for c in el if c.tag.rsplit("}", 1)[-1] in
                        ("pubDate", "published", "updated")), None)
        items.append(Item(source, title, link, _parse_date(_text(date_el))))
    return items


async def fetch_items(client: httpx.AsyncClient, feeds: dict[str, str] | None = None
                      ) -> tuple[list[Item], dict[str, str]]:
    """Returns (items, errors). ``errors`` maps feed name to a short reason; a feed in
    errors contributed NO items, so callers must treat its coverage as unknown."""
    items: list[Item] = []
    errors: dict[str, str] = {}

    async def one(name: str, url: str) -> None:
        try:
            r = await client.get(url, headers={"User-Agent": USER_AGENT}, timeout=10.0)
            r.raise_for_status()
            if len(r.content) > MAX_BYTES:
                raise ValueError("feed too large")
            items.extend(parse_feed(name, r.content))
        except (httpx.HTTPError, ValueError) as exc:
            errors[name] = f"{type(exc).__name__}"

    await asyncio.gather(*(one(n, u) for n, u in (feeds or FEEDS).items()))
    return items, errors
