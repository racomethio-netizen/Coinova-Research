"""Operating modes: which trader may place entry orders, and fail-closed parsing."""

from __future__ import annotations

from nexus_common.opmode import OperatingMode as M
from nexus_common.opmode import parse, places_orders, records_signals


def test_only_the_matching_trader_places_orders() -> None:
    table = {m: (places_orders(m, "paper"), places_orders(m, "live")) for m in M}
    assert table == {M.ANALYSIS_ONLY: (False, False), M.SIGNALS_ONLY: (False, False),
                     M.PAPER_TRADING: (True, False), M.LIVE_AUTONOMOUS: (False, True)}


def test_analysis_only_records_no_signals() -> None:
    assert [m for m in M if not records_signals(m)] == [M.ANALYSIS_ONLY]


def test_parse_defaults_to_paper_and_fails_closed() -> None:
    assert parse(None) is M.PAPER_TRADING
    assert parse("SIGNALS_ONLY") is M.SIGNALS_ONLY
    assert parse("live please") is M.ANALYSIS_ONLY
