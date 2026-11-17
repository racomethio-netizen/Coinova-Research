from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from nexus_decision import NoTradeInputs, no_trade_reasons
from nexus_news.feeds import Item, fetch_items, parse_feed
from nexus_news.scoring import score_items

NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)
RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>FOMC issues statement on the federal funds rate</title>
<link>https://www.federalreserve.gov/x</link><pubDate>Tue, 17 Nov 2026 11:30:00 GMT</pubDate></item>
<item><title>Routine speech</title><link>https://www.federalreserve.gov/y</link>
<pubDate>Mon, 16 Nov 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""
ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Degraded performance on withdrawals</title><link href="https://status/x"/>
<updated>2026-11-17T11:55:00Z</updated></entry></feed>"""


def test_parse_rss_and_atom() -> None:
    rss = parse_feed("federal_reserve", RSS)
    assert rss[0].title.startswith("FOMC")
    assert rss[0].link == "https://www.federalreserve.gov/x"
    atom = parse_feed("kraken_status", ATOM)
    assert atom[0].link == "https://status/x"
    assert atom[0].published == datetime(2026, 11, 17, 11, 55, tzinfo=UTC)


def test_malicious_xml_is_refused() -> None:
    bomb = b"""<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "x"><!ENTITY b "&a;&a;&a;">]><rss>&b;</rss>"""
    with pytest.raises(ValueError):
        parse_feed("x", bomb)
    with pytest.raises(ValueError):
        parse_feed("x", b"<rss><item>")


def test_fomc_within_window_scores_high_and_old_items_ignored() -> None:
    items = parse_feed("federal_reserve", RSS)
    risk = score_items(items, now=NOW, errors={})
    assert risk.score is not None and risk.score > 0.5
    assert any("fomc" in m for m in risk.matched)
    old = score_items(items[1:], now=NOW, errors={})
    assert old.score == 0.0


def test_unreadable_source_is_unknown_not_quiet() -> None:
    risk = score_items([], now=NOW, errors={"sec": "ConnectError"})
    assert risk.score is None and risk.unknown_sources == ("sec",)
    partial = score_items([], now=NOW, errors={"sec": "x"})
    assert partial.score is None
    mixed = score_items(parse_feed("federal_reserve", RSS), now=NOW, errors={"sec": "x"})
    assert mixed.score is not None and mixed.score >= 0.7


def test_unknown_news_blocks_trading_in_decision_layer() -> None:
    x = NoTradeInputs(now=NOW, data_time=NOW, exchange_healthy=True, spread_bps=None,
                      depth_quote=None, order_quote=None, atr_percentile=None,
                      news_risk=None, daily_loss_limit_reached=False,
                      weekly_loss_limit_reached=False, kill_switch=False, reward_risk=3.0,
                      confirmations_conflict=False, regime="trend_up", setup_present=True)
    assert any("news risk unknown" in r for r in no_trade_reasons(x))


async def test_fetch_collects_errors_per_feed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "sec" in str(request.url):
            return httpx.Response(503)
        return httpx.Response(200, content=RSS)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items, errors = await fetch_items(client, {"federal_reserve": "https://fed/x",
                                                   "sec": "https://sec/x"})
    assert errors == {"sec": "HTTPStatusError"}
    assert all(isinstance(i, Item) for i in items) and items


def test_keywords_match_whole_words_only() -> None:
    from nexus_news.scoring import _weight

    assert _weight("Bank Payments Unavailable")[0] == 0.0
    assert _weight("Ban on crypto advertising")[1] == "ban"
