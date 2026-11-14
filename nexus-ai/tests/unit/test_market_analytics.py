from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from nexus_exchange.models import Candle, OrderBook
from nexus_market.analytics import (
    average_true_range,
    book_metrics,
    detect_gaps,
    estimate_fill,
    realized_volatility,
    volume_zscore,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
STEP = 300


def times(idx: list[int]) -> list[datetime]:
    return [T0 + timedelta(seconds=STEP * i) for i in idx]


class TestGaps:
    def test_no_gaps(self) -> None:
        assert detect_gaps(times(list(range(10))), STEP) == []

    def test_interior_runs(self) -> None:
        gaps = detect_gaps(times([0, 1, 4, 5, 7]), STEP)
        assert [(g.start, g.end, g.missing) for g in gaps] == [
            (times([2])[0], times([3])[0], 2), (times([6])[0], times([6])[0], 1)]

    def test_explicit_window_edges(self) -> None:
        gaps = detect_gaps(times([2, 3]), STEP, start=times([0])[0], end=times([5])[0])
        assert [(g.missing) for g in gaps] == [2, 2]

    def test_empty(self) -> None:
        assert detect_gaps([], STEP) == []
        assert detect_gaps([], STEP, start=T0, end=T0)[0].missing == 1


def book(bids: list[tuple[str, str]], asks: list[tuple[str, str]]) -> OrderBook:
    return OrderBook("BTC/USDT", tuple((D(p), D(a)) for p, a in bids),
                     tuple((D(p), D(a)) for p, a in asks), T0)


class TestBook:
    def test_metrics(self) -> None:
        b = book([("99.9", "2"), ("99", "100")], [("100.1", "1"), ("101", "100")])
        m = book_metrics(b, band_bps=D(50))
        assert m is not None
        assert m.mid == D("100.0") and m.spread_bps == D("20")
        assert m.bid_depth_quote == D("199.8")       # 99 is outside the 0.5% band
        assert m.ask_depth_quote == D("100.1")
        assert 0 < m.imbalance < 1

    def test_empty_book(self) -> None:
        assert book_metrics(book([], [("1", "1")])) is None

    def test_estimate_fill_walks_levels(self) -> None:
        b = book([("99", "1")], [("100", "1"), ("102", "1")])
        vwap, filled = estimate_fill(b, "buy", D("1.5"))  # type: ignore[misc]
        assert filled == D("1.5") and vwap == D("100.6666666666666666666666667")
        vwap, filled = estimate_fill(b, "buy", D("5"))  # type: ignore[misc]
        assert filled == D("2")  # book too thin: reported, not invented
        assert estimate_fill(book([], []), "sell", D(1)) is None


def candles(closes: list[float], vol: list[float] | None = None) -> list[Candle]:
    out = []
    for i, c in enumerate(closes):
        prev = closes[i - 1] if i else c
        o = D(str(prev))
        cl = D(str(c))
        out.append(Candle(T0 + timedelta(seconds=STEP * i), o, max(o, cl) + 1, min(o, cl) - 1, cl,
                          D(str((vol or [1.0] * len(closes))[i]))))
    return out


class TestVolatility:
    def test_constant_prices_zero_vol(self) -> None:
        assert realized_volatility(candles([100] * 10), step_seconds=STEP) == 0

    def test_known_value(self) -> None:
        c = candles([100, 110, 100, 110])
        r = math.log(1.1)
        expected = math.sqrt(sum((x - r / 3) ** 2 for x in [r, -r, r]) / 2)
        assert realized_volatility(c, step_seconds=STEP, annualize=False) == pytest.approx(expected)

    def test_atr(self) -> None:
        c = candles([100] * 20)
        assert average_true_range(c, 14) == D(2)
        assert average_true_range(c[:5], 14) is None

    def test_volume_z(self) -> None:
        vols = [1.0, 2.0] * 30 + [10.0]
        z = volume_zscore(candles([100] * len(vols), vols), 50)
        assert z is not None and z > 10
        assert volume_zscore(candles([100] * 60, [1.0] * 60), 50) is None
