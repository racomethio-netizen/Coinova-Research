"""Phase 6 strategies: causal features, no signals without required data, loaders."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from nexus_backtest.phase6 import funding_24h, taker_ratio_1h
from nexus_strategies import ALL_STRATEGIES
from nexus_strategies.library_v2 import (
    FundingSqueeze,
    MtfConfluence,
    TakerFlow,
    VolatilityExpansion,
    VwapReclaim,
    session_vwap,
)


def ohlcv(n: int, freq: str, start: str = "2026-01-01", seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.004, n)))
    return pd.DataFrame({"open": close * (1 - 0.001), "high": close * 1.003,
                         "low": close * 0.997, "close": close,
                         "volume": rng.uniform(50, 150, n)}, index=idx)


def test_session_vwap_resets_each_utc_day() -> None:
    df = ohlcv(200, "15min")
    v = session_vwap(df)
    first_bar_each_day = df.index[df.index.floor("D").to_series().diff().ne(pd.Timedelta(0))]
    tp = (df["high"] + df["low"] + df["close"]) / 3
    assert np.allclose(v.loc[first_bar_each_day], tp.loc[first_bar_each_day])


def test_candle_strategies_registered_and_compute() -> None:
    for code in ("vwap_reclaim", "volatility_expansion", "mtf_confluence"):
        assert code in ALL_STRATEGIES
    frames = {"15m": ohlcv(3000, "15min"), "1h": ohlcv(1200, "1h"), "4h": ohlcv(400, "4h")}
    for cls in (VwapReclaim, VolatilityExpansion, MtfConfluence):
        out = cls().compute(frames)
        assert set(out.columns) >= {"entry", "stop", "target", "regime"}
        assert (out.loc[out["entry"], "stop"] < out.loc[out["entry"], "target"]).all()


def test_data_dependent_strategies_never_signal_without_their_data() -> None:
    frames = {"1h": ohlcv(1200, "1h"), "4h": ohlcv(400, "4h")}
    assert not FundingSqueeze().compute(frames)["entry"].any()
    assert not TakerFlow().compute(frames)["entry"].any()
    assert "funding_squeeze" not in ALL_STRATEGIES and "taker_flow" not in ALL_STRATEGIES


def _zip(path: Path, name: str, text: str) -> Path:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    path.write_bytes(buf.getvalue())
    return path


def test_taker_ratio_and_funding_loaders(tmp_path: Path) -> None:
    head = ("open_time,open,high,low,close,volume,close_time,quote_volume,count,"
            "taker_buy_volume,taker_buy_quote_volume,ignore\n")
    t0 = 1_735_689_600_000          # 2025-01-01 00:00 UTC
    rows = "".join(f"{t0 + i * 300_000},1,1,1,1,10,0,0,0,{6 if i < 12 else 2},0,0\n"
                   for i in range(24))
    z = _zip(tmp_path / "BTCUSDT-5m-2025-01.zip", "BTCUSDT-5m-2025-01.csv", head + rows)
    r = taker_ratio_1h([z])
    assert list(r.round(3)) == [0.6, 0.2]
    fh = "calc_time,funding_interval_hours,last_funding_rate\n"
    frows = "".join(f"{t0 + i * 8 * 3_600_000},8,{v}\n"
                    for i, v in enumerate([-0.0003, 0.0, 0.0003, 0.0006]))
    fz = _zip(tmp_path / "BTCUSDT-fundingRate-2025-01.zip", "BTCUSDT-fundingRate-2025-01.csv",
              fh + frows)
    f = funding_24h([fz])
    assert np.isnan(f.iloc[1]) and abs(f.iloc[2]) < 1e-12 and abs(f.iloc[3] - 0.0003) < 1e-12
