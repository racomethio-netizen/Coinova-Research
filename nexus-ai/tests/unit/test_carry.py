from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest
from nexus_backtest.carry import PERP_COST, SPOT_COST, daily_closes_zip, daily_returns, signal


def _idx(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")


def test_signal_uses_only_past_funding() -> None:
    f = pd.Series([0.0006] * 10 + [-0.05] + [0.0006] * 3, index=_idx(14))  # daily sums; crash day 10
    pos = signal(f, lookback=7)
    assert pos.iloc[10] == 1.0        # day 10's own funding is not known yet
    assert pos.iloc[11] == 0.0        # known from day 11 on
    f2 = f.copy()
    f2.iloc[10] = 0.5  # changing day 10 cannot change positions up to day 10
    assert signal(f2, lookback=7).iloc[:11].equals(pos.iloc[:11])


def test_perfect_hedge_earns_funding_minus_costs() -> None:
    prices = [100.0, 110.0, 99.0, 99.0]
    df = pd.DataFrame({"spot": prices, "perp": prices, "funding": [0.0, 0.0003, 0.0003, 0.0003]},
                      index=_idx(4))
    pos = pd.Series([0.0, 1.0, 1.0, 0.0], index=df.index)
    r = daily_returns(df, pos)
    entry_day = (0.1 - 0.1 + 0.0003 - (0.1 * SPOT_COST + 0.1 * PERP_COST) - (SPOT_COST + PERP_COST)) / 2
    assert r.iloc[1] == pytest.approx(entry_day)
    assert r.iloc[3] == pytest.approx(-(SPOT_COST + PERP_COST) / 2)  # exit pays all four legs


def test_microsecond_spot_timestamps_parse() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("BTCUSDT-1d-2025-01.csv",
                   "1735689600000000,1,2,0.5,93000.5,10,1735775999999999,0,0,0,0,0\n")
    s = daily_closes_zip(buf.getvalue())
    assert s.index[0] == pd.Timestamp("2025-01-01", tz="UTC") and s.iloc[0] == 93000.5
