"""Night scalper simulator on synthetic bars: the New York window (with daylight saving),
bid/ask fills, stop first, gaps, time exits, USD conversion of crosses, NEXUS sizing and
limits, commission and the cost stress, the durable halt."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest
from nexus_backtest import night_scalper as ns

T0 = pd.Timestamp("2024-07-08 21:30", tz="UTC")     # Monday 17:30 New York (EDT)


def bare(rows: dict[str, list[dict[str, Any]]], rates: dict[str, float] | None = None,
         r: ns.Rules | None = None, risk: ns.Risk | None = None,
         start: pd.Timestamp = T0) -> ns.Night:
    """A simulator with hand-made rows (5-minute steps from ``start``)."""
    n = ns.Night.__new__(ns.Night)
    n.r, n.risk = r or ns.Rules(), risk or ns.Risk()
    n.pairs = tuple(rows)
    times = [start + pd.Timedelta(minutes=5 * i) for i in range(max(len(v) for v in rows.values()))]
    n.times = times
    n.rows = {p: dict(zip(times, v, strict=False)) for p, v in rows.items()}
    n.skipped_room = 0
    rates = {"EURUSD": 1.1, "GBPUSD": 1.27, "AUDUSD": 0.66, "NZDUSD": 0.6, "USDCHF": 0.9,
             "USDCAD": 1.35} | (rates or {})
    n.rate = {k: pd.Series(v, index=pd.DatetimeIndex(times)) for k, v in rates.items()}
    n.book = ns.Book()
    return n


def bar(bid: float, *, hi: float | None = None, lo: float | None = None,
        close: float | None = None, spread: float = 0.0002, d: int = 0,
        mid: float = 1.1050, atr_: float = 0.0010, flat: bool = False) -> dict[str, Any]:
    hi = bid if hi is None else hi
    lo = bid if lo is None else lo
    c = bid if close is None else close
    return {"open": bid, "high": hi, "low": lo, "close": c, "ask_open": bid + spread,
            "ask_high": hi + spread, "ask_low": lo + spread, "ask_close": c + spread,
            "spread_mean": spread, "dir": d, "mid": mid, "atr": atr_, "flat_now": flat}


def test_window_follows_new_york_time_through_daylight_saving():
    def frames(start: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        idx = pd.date_range(start, periods=24 * 12 * 3, freq="5min", tz="UTC")
        px = 1.1 + 0.0001 * np.sin(np.arange(len(idx)))
        m5 = pd.DataFrame({"open": px, "high": px + 0.0001, "low": px - 0.0001, "close": px,
                           "spread_mean": 0.0001}, index=idx)
        h1 = m5.resample("1h").agg({"open": "first", "high": "max", "low": "min",
                                    "close": "last"})
        return m5, h1

    summer = ns.signals(*frames("2024-07-08"), ns.Rules())      # EDT = UTC-4
    winter = ns.signals(*frames("2024-01-08"), ns.Rules())      # EST = UTC-5
    s_open = pd.Timestamp("2024-07-08 21:25", tz="UTC")         # closes 17:30 New York
    w_open = pd.Timestamp("2024-01-08 22:25", tz="UTC")
    assert summer.at[s_open, "night"] and not summer.at[s_open - pd.Timedelta("5min"), "night"]
    assert winter.at[w_open, "night"] and not winter.at[w_open - pd.Timedelta("5min"), "night"]
    assert not winter.at[pd.Timestamp("2024-01-08 21:25", tz="UTC"), "night"]  # 16:30 EST
    flat = pd.Timestamp("2024-07-09 02:55", tz="UTC")            # closes 23:00 New York
    assert summer.at[flat, "flat_now"] and summer.at[flat, "night"]


def test_long_fills_at_ask_and_takes_profit_on_bid():
    rows = {"EURUSD": [bar(1.1000, d=1, mid=1.1030, atr_=0.005), bar(1.1000),
                       bar(1.1010, hi=1.1035, close=1.1030)]}
    book = bare(rows).run()
    (tr,) = book.trades
    assert tr["entry"] == pytest.approx(1.1002)                  # bid 1.1000 + spread
    assert tr["reason"] == "target" and tr["exit"] == pytest.approx(1.1030)
    assert tr["units"] == 2000                    # 25 USD / (2 x 0.005), in 1,000 lots
    assert tr["risk_usd"] == pytest.approx(0.01 * 2000)
    assert tr["pnl_usd"] == pytest.approx((1.1030 - 1.1002) * 2000)


def test_size_is_cut_by_the_currency_exposure_cap():
    rows = {"EURUSD": [bar(1.1000, d=1, mid=1.1030), bar(1.1000),
                       bar(1.1010, hi=1.1035, close=1.1030)]}
    (tr,) = bare(rows).run().trades
    # 0.25% risk would be 12,500 units; 0.6 x 10,000 USD of EUR caps it at 5,454 -> 5,000.
    assert tr["units"] == 5000


def test_short_stops_on_the_ask_first_when_both_are_reachable():
    rows = {"EURUSD": [bar(1.1000, d=-1, mid=1.0970), bar(1.1000),
                       bar(1.1000, hi=1.1020, lo=1.0960)]}
    (tr,) = bare(rows).run().trades
    assert tr["entry"] == pytest.approx(1.1000)                  # a sell gets the bid
    assert tr["reason"] == "stop"
    assert tr["exit"] == pytest.approx(1.1020 + 0.00002)         # stop + 0.2 pip slippage
    assert tr["r"] < -1


def test_gap_through_the_stop_fills_at_the_open_and_time_exit():
    rows = {"EURUSD": [bar(1.1000, d=1), bar(1.1000), bar(1.0950)]}
    (tr,) = bare(rows).run().trades
    assert tr["reason"] == "stop_gap" and tr["exit"] == pytest.approx(1.0950 - 0.00002)
    rows = {"EURUSD": [bar(1.1000, d=1), *[bar(1.1001) for _ in range(40)]]}
    (tr,) = bare(rows).run().trades
    assert tr["reason"] == "time" and tr["bars"] == 36
    rows = {"EURUSD": [bar(1.1000, d=1), bar(1.1001), bar(1.1001, flat=True), bar(1.1)]}
    (tr,) = bare(rows).run().trades
    assert tr["reason"] == "time" and tr["bars"] == 2            # 23:00 New York


def test_cross_profit_is_converted_to_usd_and_sized_in_usd():
    rows = {"EURAUD": [bar(1.6500, d=1, mid=1.6530, spread=0.0003, atr_=0.006),
                       bar(1.6500, spread=0.0003),
                       bar(1.6510, hi=1.6535, spread=0.0003)]}
    (tr,) = bare(rows, rates={"AUDUSD": 0.65}).run().trades
    stop_dist = 2 * 0.006
    assert tr["units"] == np.floor(25 / (stop_dist * 0.65) / 1000) * 1000 == 3000
    assert tr["pnl_usd"] == pytest.approx((1.6530 - 1.6503) * tr["units"] * 0.65)


def test_commission_and_the_cost_stress():
    rows = {"EURUSD": [bar(1.1000, d=1, mid=1.1030), bar(1.1000),
                       bar(1.1010, hi=1.1035, close=1.1030)]}
    raw = bare(rows, r=ns.Rules(commission_per_100k=7.0)).run().trades[0]
    assert raw["costs_usd"] == pytest.approx(7.0 * raw["units"] / 100_000)
    stress = bare(rows, r=ns.Rules(commission_per_100k=7.0, cost_mult=2.0)).run().trades[0]
    assert stress["entry"] == pytest.approx(1.1004)              # one more spread
    assert stress["exit"] == pytest.approx(1.1028)
    assert stress["costs_usd"] == pytest.approx(2 * raw["costs_usd"])


def test_target_already_passed_at_the_fill_is_not_traded():
    rows = {"EURUSD": [bar(1.1000, d=1, mid=1.1005), bar(1.1010), bar(1.1010)]}
    book = bare(rows).run()
    assert not book.trades and book.rejections == {"target passed before the fill": 1}


def test_shared_limits_positions_open_risk_and_currency_exposure():
    pairs = ["EURUSD", "GBPUSD", "AUDUSD", "EURGBP", "EURAUD"]
    rows = {p: [bar(1.1000, d=1, mid=1.1100), *[bar(1.1000) for _ in range(5)]]
            for p in pairs}
    n = bare(rows)
    seen: list[tuple[int, float]] = []
    orig = n._consider

    def spy(p: str, t: pd.Timestamp, row: dict[str, Any], eq: float) -> None:
        orig(p, t, row, eq)
        legs = list(n.book.open.values())
        usd = sum(x.units * n.base_usd(x.pair, t) for x in legs if x.pair.endswith("USD")) + \
            sum(v["units"] * n.base_usd(q, t) for q, v in n.book.pending.items()
                if q.endswith("USD"))
        seen.append((len(n.book.open) + len(n.book.pending), usd / eq))

    n._consider = spy  # type: ignore[method-assign]
    book = n.run()
    assert max(s[0] for s in seen) <= 3
    assert max(s[1] for s in seen) <= 0.6 + 1e-9                 # short USD at most 0.6x
    assert sum(v for k, v in book.rejections.items() if k in (
        "max open positions", "exposure limit", "aggregate open risk limit",
        "below the minimum lot")) >= 2


def test_drawdown_halt_closes_everything_and_stays():
    rows = {"EURUSD": [bar(1.1000, d=1, mid=1.1100, atr_=0.05), bar(1.1000),
                       bar(1.0000), bar(1.1000, d=1, mid=1.1100), bar(1.1), bar(1.1)]}
    book = bare(rows, risk=ns.Risk(per_trade=0.06, open_risk=0.1)).run()
    assert book.halted is not None
    assert book.rejections.get("halted (drawdown limit)") == 1
    assert len(book.trades) == 1


def test_half_years_chain_from_starting_equity():
    t = pd.Timestamp
    curve = [(t("2024-02-01", tz="UTC"), 10_100.0), (t("2024-08-01", tz="UTC"), 9_999.0)]
    hy = ns.half_years(curve)
    assert hy["2024-H1"] == pytest.approx(0.01)
    assert hy["2024-H2"] == pytest.approx(9_999 / 10_100 - 1)
