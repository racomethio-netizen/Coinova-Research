"""Per-market autonomy switch: what each market supports, fail-closed parsing, and how the
switch narrows (never widens) the global operating mode."""

from __future__ import annotations

from nexus_common.markets import (
    MARKETS,
    Autonomy,
    Market,
    allows_entries,
    combine,
    describe,
    effective,
    live_phrase,
    parse,
)
from nexus_common.opmode import OperatingMode as Op


def test_only_crypto_supports_anything_beyond_off_today():
    assert MARKETS[Market.CRYPTO].allowed() == (Autonomy.OFF, Autonomy.RESEARCH,
                                                Autonomy.PAPER, Autonomy.LIVE)
    for m in (Market.STOCKS, Market.COMMODITIES, Market.FOREX):
        assert MARKETS[m].allowed() == (Autonomy.OFF,)
        assert MARKETS[m].missing_for(Autonomy.PAPER) == [
            "no historical data source is built yet", "no paper trading engine is built yet"]
        assert MARKETS[m].missing_for(Autonomy.OFF) == []


def test_defaults_preserve_crypto_paper_and_fail_closed():
    assert parse(Market.CRYPTO, None) is Autonomy.PAPER          # today's behaviour
    assert parse(Market.FOREX, None) is Autonomy.OFF
    assert parse(Market.CRYPTO, "bogus") is Autonomy.OFF
    assert effective(Market.CRYPTO, {}) is Autonomy.PAPER
    assert effective(Market.CRYPTO, {"crypto": {"autonomy": "RESEARCH"}}) is Autonomy.RESEARCH
    # A stored mode the market cannot support is treated as OFF.
    assert effective(Market.STOCKS, {"stocks": {"autonomy": "LIVE"}}) is Autonomy.OFF


def test_switch_only_narrows_the_operating_mode():
    assert combine(Op.PAPER_TRADING, Autonomy.PAPER) is Op.PAPER_TRADING
    assert combine(Op.PAPER_TRADING, Autonomy.RESEARCH) is Op.SIGNALS_ONLY
    assert combine(Op.PAPER_TRADING, Autonomy.OFF) is Op.ANALYSIS_ONLY
    assert combine(Op.PAPER_TRADING, Autonomy.LIVE) is Op.PAPER_TRADING     # never widens
    assert combine(Op.ANALYSIS_ONLY, Autonomy.LIVE) is Op.ANALYSIS_ONLY
    assert combine(Op.LIVE_AUTONOMOUS, Autonomy.PAPER) is Op.PAPER_TRADING  # per-market key


def test_entries_need_paper_or_live_and_live_needs_live():
    assert allows_entries(Autonomy.PAPER, "paper") and allows_entries(Autonomy.LIVE, "paper")
    assert not allows_entries(Autonomy.RESEARCH, "paper")
    assert not allows_entries(Autonomy.OFF, "paper")
    assert allows_entries(Autonomy.LIVE, "live")
    assert not allows_entries(Autonomy.PAPER, "live")


def test_nothing_is_described_as_connected_without_a_connector():
    for d in describe():
        if d["market"] != "crypto":
            assert all(v["connector"] == "not_built" for v in d["venues"])
            assert d["capabilities"] == {"research_data": False, "paper_engine": False,
                                         "live_connector": False}
    assert live_phrase(Market.FOREX) == "ENABLE LIVE FOREX"
