from __future__ import annotations

import pandas as pd
from nexus_backtest.futures_validation import funding_adjuster, resample


def _five(start: str, n: int) -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="5min", tz="UTC", name="open_time")
    vals = [float(i) for i in range(n)]
    return pd.DataFrame({"open": vals, "high": [v + 0.5 for v in vals],
                         "low": [v - 0.5 for v in vals], "close": [v + 0.25 for v in vals],
                         "volume": [1.0] * n}, index=idx)


def test_resampled_bar_uses_only_its_own_5m_bars() -> None:
    frames = resample(_five("2026-01-01 00:00", 24))  # 00:00 .. 01:55
    h = frames["1h"]
    first = h.loc[pd.Timestamp("2026-01-01 00:00", tz="UTC")]
    assert first.open == 0.0 and first.close == 11.25 and first.high == 11.5 and first.low == -0.5
    assert first.volume == 12.0
    assert list(h.index) == [pd.Timestamp("2026-01-01 00:00", tz="UTC"),
                             pd.Timestamp("2026-01-01 01:00", tz="UTC")]
    assert len(frames["15m"]) == 8 and frames["15m"].iloc[0].close == 2.25


def test_funding_counts_only_settlements_while_open() -> None:
    times = pd.to_datetime(["2026-01-01 00:00", "2026-01-01 08:00", "2026-01-01 16:00",
                            "2026-01-02 00:00"], utc=True)
    rates = pd.Series([0.001, 0.0001, 0.0002, 0.005], index=times)
    study = pd.DataFrame({
        "entry_time": pd.to_datetime(["2026-01-01 07:00", "2026-01-01 09:00"], utc=True),
        "exit_time": pd.to_datetime(["2026-01-01 17:00", "2026-01-01 10:00"], utc=True),
        "ret": [0.01, -0.002], "win": [True, False]})
    out = funding_adjuster({"BTCUSDT": rates})(study, "BTCUSDT")
    assert abs(out.funding[0] - 0.0003) < 1e-12 and out.funding[1] == 0.0
    assert abs(out.ret[0] - 0.0097) < 1e-12 and out.ret[1] == -0.002
    assert bool(out.win[0]) and not bool(out.win[1])
