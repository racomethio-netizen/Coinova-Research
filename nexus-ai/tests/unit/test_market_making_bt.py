"""Market-making backtester on synthetic minute bars: fills need a trade-through, no
look-ahead, inventory limits, spot never sells coins it does not hold, the daily stop,
funding, the loaders and the report gates."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from nexus_backtest import market_making_bt as mm

T0 = int(pd.Timestamp("2024-01-01", tz="UTC").timestamp() * 1000)


def bars(close: np.ndarray, wick: float = 0.001) -> pd.DataFrame:
    o = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"t": T0 + np.arange(len(close)) * 60_000, "open": o,
                         "high": np.maximum(o, close) * (1 + wick),
                         "low": np.minimum(o, close) * (1 - wick), "close": close})


def run(k: pd.DataFrame, cfg: mm.Cfg, funding=None) -> dict:
    fund = funding or (np.array([], "int64"), np.array([]))
    return mm.simulate(k, mm.rolling_vol(k["close"].to_numpy()), fund, 0.0001, cfg)


def sideways(n: int = 3 * 1440) -> np.ndarray:
    return 100 * (1 + 0.003 * np.sin(2 * np.pi * np.arange(n) / 20))


def test_sideways_market_fills_both_sides_and_earns_the_spread_without_fees():
    r = run(bars(sideways()), mm.Cfg("perp", 0.0005, fee_mult=0.0))
    assert r["buys"] > 100 and r["sells"] > 100
    assert r["final"] > 0 and r["fees"] == 0


def test_fees_are_charged_and_strict_fills_fill_less():
    k = bars(sideways())
    base = run(k, mm.Cfg("perp", 0.0005))
    free = run(k, mm.Cfg("perp", 0.0005, fee_mult=0.0))
    strict = run(k, mm.Cfg("perp", 0.0005, penetration=mm.PENETRATION))
    assert base["fees"] > 0 and base["final"] < free["final"]
    assert strict["buys"] + strict["sells"] <= base["buys"] + base["sells"]


def test_no_fill_without_a_trade_through():
    k = bars(np.full(500, 100.0), wick=0.0)                  # flat: nothing trades through
    r = run(k, mm.Cfg("perp", 0.0005))
    assert r["buys"] == 0 and r["sells"] == 0 and r["final"] == 0


def test_no_lookahead():
    close = sideways()
    a = run(bars(close), mm.Cfg("perp", 0.001))
    changed = close.copy()
    changed[2 * 1440 + 10:] *= 1.5                            # day 3 changes only
    b = run(bars(changed), mm.Cfg("perp", 0.001))
    days = sorted(a["daily_measure"])
    for d in days[:2]:
        assert a["daily_measure"][d] == b["daily_measure"][d]


def test_inventory_limit_and_daily_stop_in_a_crash():
    close = 100 * np.exp(-0.001 * np.arange(2 * 1440))        # -0.1%/min, no bounce
    r = run(bars(close, wick=0.0), mm.Cfg("perp", 0.0005))
    assert r["buys"] >= 5 and r["sells"] == 0
    assert r["max_inventory"] <= mm.LIMIT + mm.ORDER + 1
    assert r["stops"] >= 1                                   # 1% daily loss stop fired


def test_spot_never_sells_coins_it_does_not_hold():
    close = 100 * np.exp(0.002 * np.arange(1440))            # +0.2%/min: asks keep filling
    r = run(bars(close, wick=0.0), mm.Cfg("spot", 0.0015))
    assert r["sells"] > 0
    # Target 2,500 USDT of coin at the start; it can sell at most what it holds.
    assert r["sells"] - r["buys"] <= 2_500 / mm.ORDER + 1


def test_funding_is_paid_on_a_long_inventory():
    close = 100 * np.exp(-0.0008 * np.arange(1440))          # bids fill, no bounce
    k = bars(close, wick=0.0)
    ft = np.array([T0 + 90 * 60_000], dtype="int64")         # after warm-up; long by then
    r = run(k, mm.Cfg("perp", 0.0005), funding=(ft, np.array([0.001])))
    assert r["funding"] < 0                                  # longs pay positive funding


def _zip(path: Path, df: pd.DataFrame, header: bool) -> None:
    buf = io.StringIO()
    df.to_csv(buf, header=header, index=False)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(path.stem + ".csv", buf.getvalue())


def test_loaders_handle_headers_microseconds_and_ticks(tmp_path: Path):
    k = bars(100 + 0.0001 * np.arange(200), wick=0.0)        # adjacent ticks present
    raw = pd.DataFrame({"open_time": k["t"] * 1000, "open": k["open"].round(4),
                        "high": k["high"].round(4), "low": k["low"].round(4),
                        "close": k["close"].round(4), "volume": 1.0})
    _zip(tmp_path / "BTCUSDT-1m-2024-01.zip", raw, header=False)       # microseconds
    loaded = mm.load_klines(tmp_path, "BTCUSDT")
    assert loaded["t"].iloc[0] == T0 and len(loaded) == 200
    assert mm.tick_size(loaded) == pytest.approx(0.0001)
    f = pd.DataFrame({"calc_time": [T0 + 8 * 3_600_000 + 3], "funding_interval_hours": [8],
                      "last_funding_rate": [0.0001]})
    _zip(tmp_path / "BTCUSDT-fundingRate-2024-01.zip", f, header=True)
    ft, fr = mm.load_funding(tmp_path, "BTCUSDT")
    assert ft.tolist() == [T0 + 8 * 3_600_000] and fr.tolist() == [0.0001]


def test_report_judges_every_variant_with_the_gates(monkeypatch):
    monkeypatch.setattr(mm, "SYMBOLS", ("AAA", "BBB"))
    results = []
    for sym, seed in (("AAA", 1), ("BBB", 2)):
        rng = np.random.default_rng(seed)
        close = sideways(2 * 1440) * np.exp(np.cumsum(rng.normal(0, 0.0003, 2 * 1440)))
        k = bars(close)
        vol = mm.rolling_vol(k["close"].to_numpy())
        for name, cfg in mm.VARIANTS.items():
            for setting, (fm, pen) in mm.SETTINGS.items():
                r = mm.simulate(k, vol, (np.array([], "int64"), np.array([])), 0.0001,
                                mm.replace(cfg, fee_mult=fm, penetration=pen))
                r.update(variant=name, setting=setting, symbol=sym, tick=0.0001,
                         bars=len(k))
                results.append(r)
    rep = mm.report(results)
    assert set(rep["variants"]) == set(mm.VARIANTS)
    for v in rep["variants"].values():
        assert set(v["gates"]) == {"net_positive", "significant_holm", "sharpe_ge_1",
                                   "drawdown_le_10pct", "positive_2_of_3_years",
                                   "positive_at_2x_fees", "positive_without_best",
                                   "positive_strict_fill"}
        assert v["passed"] == all(v["gates"].values())
        assert v["activity"]["fills_per_day"] >= 0
